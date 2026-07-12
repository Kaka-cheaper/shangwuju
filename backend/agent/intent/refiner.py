"""agent.refiner —— 用户拒绝方案 + 反馈 → 调整后的 IntentExtraction。

业务故事见 schemas/refine.py 顶部 docstring。

实现策略：
- LLM 调用 1 次，response_format=json_object
- 校验前跑 _inherit_missing_keys（C1 通用键缺失继承守卫，forge-intent-loss
  收敛，2026-07-12）：白名单字段键缺失 → 按出处门控 + 矛盾检测从 original
  继承，收编原 explicit_dining_requested 专属补丁——详见该函数 docstring
- 围栏剥离 + Pydantic 二次校验（防漂移；pitfalls P2-预埋）
- 若校验失败 → 错误回灌 LLM 1 次重试
- 若 2 次都失败 → 走规则化兜底（_rule_fallback：根据反馈关键词调字段）
  评分硬要求：refine 端到端必须有降级路径，**不能**让 Demo 上转圈
- LLM 成功后跑 _enforce_duration_consistency：若反馈含具体小时数，
  强制让 refined_intent.duration_hours 与 changed_fields 对齐
  （防 LLM 在文本里说改了但 JSON 字段没改，参考 problem.md 问题 11）

spec planning-quality-deep-review R8（Task 7）引入，ADR-0014 G-0（2026-07-03）迁移：
- _rule_fallback 保留 _KEYWORDS_SESSION_TOO_LONG（"太久" "太长" "盯不住" "无聊"
  "扛不住" "腻了"）识别，但收缩目标从原 pace_profile.single_session_max_min
  迁移到 duration_hours 上界——原字段 pace_profile 全系统无消费方（规划器
  pace_budget.py 自证不读，见其模块 docstring，自己另走 relaxed/medium/energetic
  三档节奏模型），该收缩在业务上纯属空转；duration_hours 有真实消费（规划器
  拿它定总时长硬预算），迁移后命中该反馈才有"用户可见效果=行程真的变短"。
  收缩比例沿用 30%（× 0.7），带下限保护（不低于 duration_hours 下界，也不
  低于 1 小时地板）；见 _rule_fallback 内 SESSION_TOO_LONG 分支注释。
- _extract_duration_from_feedback 扩支持「半小时」/「30 分钟」/「一个半小时」
  三类正则，让分钟级 / 半小时级 / 1.5 小时级反馈也能被识别为具体时长。

防御要点（与 intent_parser 一致）：
- 词典外 tag 由 Pydantic Literal 拦截 → 校验失败 → 重试 / 兜底
- raw_input 字段不允许被 LLM 改写（兜底覆盖回原值）
- 顶层禁止字段（scene_type 等）由 §5.7 model_config extra="forbid" 拦截

不负责：
- 重新规划（rule 范式在 rule_planner.plan_itinerary；LLM 主路径在 agent/graph/）
- HTTP 端点（在 main.py，B 块）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from schemas.category_vocab import all_canonical_terms
from schemas.intent import IntentExtraction
from schemas.refine import RefinementOutput
from schemas.tags import DIETARY_TAGS

from ..core.llm_client import LLMClient, LLMMessage, strip_json_fence
from ..core.feedback_detector import looks_like_feedback
from .prompts.refiner_prompt import (
    REFINER_FEW_SHOTS,
    REFINER_SYSTEM_PROMPT,
    build_user_message,
)


# ============================================================
# 异常
# ============================================================

@dataclass
class RefinementError(Exception):
    """refiner 全部路径失败（LLM 重试 + 兜底都不行）。

    上层应推 stream_error 事件并终止 SSE 流。
    """

    reason: str
    last_validation_error: str | None = None

    def __str__(self) -> str:  # pragma: no cover
        return f"RefinementError({self.reason})"


# ============================================================
# 上一版行程 → 给 refiner 判反馈用的结构化摘要
# ============================================================

_HOP_LABEL = {
    "walking": "步行",
    "taxi": "打车",
    "bus": "公交",
    "haversine_estimated": "约",
    "virtual": "",
}


def summarize_itinerary(itinerary: object) -> str | None:
    """把上一版行程压成给 refiner 判反馈用的结构化摘要。

    取舍（对 refiner 判反馈是信号还是噪声）：
      留：每站名字 + 停留时长、站间通勤(方式/分钟)、一句方案 summary——"太远 / 太久 / 太赶 /
          不要那家"等反馈正是要对照这些维度。
      删：node_id / hop_id / 经纬度 / address / 订单 / schema_version——对判反馈是噪声。
    形式：半结构化分行（带量纲），不是有损的 "A → B → C" 串，让 LLM 能精确对照反馈。
    防御式：dict / model / None / 任意异常都安全（None 或尽力而为），绝不搞挂 refine 主流程。
    """
    if not itinerary:
        return None
    try:
        data = (
            itinerary.model_dump()
            if hasattr(itinerary, "model_dump")
            else dict(itinerary)
        )
    except Exception:  # noqa: BLE001
        return None

    max_lines = 12  # token 预算：约 6 站 + 站间通勤
    lines: list[str] = []

    schedule = data.get("schedule")
    if isinstance(schedule, list) and schedule:
        # 优先用派生视图 schedule：已展平、带时长 minutes / 通勤 mode / hidden 标记
        for e in schedule:
            if not isinstance(e, dict) or e.get("hidden"):
                continue
            mins = e.get("minutes") or 0
            if e.get("entry_kind") == "hop":
                if mins:  # 跳过 0 分钟同地占位
                    mode = _HOP_LABEL.get(str(e.get("mode") or ""), "通勤") or "通勤"
                    lines.append(f"  ↳ {mode} {mins}min")
            else:
                title = str(e.get("title") or "").strip()
                if not title:
                    continue
                start = str(e.get("start") or "").strip()
                dur = f" {mins}min" if mins else ""
                lines.append(f"- {start} {title}{dur}".strip())
            if len(lines) >= max_lines:
                break
    else:
        # 退回源真值 nodes（schedule 未填充时）：列非 home 站 + 停留时长
        # 注意：home 判断是 target_kind=="home"，不是 kind（kind 是「主活动/用餐」中文标签）
        for n in data.get("nodes") or []:
            if not isinstance(n, dict) or n.get("target_kind") == "home":
                continue
            title = str(n.get("title") or "").strip()
            if not title:
                continue
            start = str(n.get("start_time") or "").strip()
            dur = n.get("duration_min") or 0
            tail = f" {dur}min" if dur else ""
            lines.append(f"- {start} {title}{tail}".strip())
            if len(lines) >= max_lines:
                break

    if not lines:
        # 连站点都取不到 → 退到方案自带的一句摘要 / 转发文案
        for k in ("summary", "share_message"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:200]
        return None

    summ = data.get("summary")
    header = f"上一版:{summ.strip()}\n" if isinstance(summ, str) and summ.strip() else ""
    return (header + "\n".join(lines)).strip()


# ============================================================
# ADR-0014 决策 1（G-1）：反馈轮出处传播——纯规则，不要 LLM 自报
# ============================================================
#
# 与 parser 首轮（LLM 自报 + 规则交叉校正）不同：反馈轮不要求 refiner 的
# LLM 自己判断出处，而是对 (original, refined) 两份 IntentExtraction 做
# 结构化 diff 现算——"changed_fields 对应的字段/新元素" 就是这个 diff 的
# 直接结果，不解析 changed_fields 的中文自由文本（那是给用户看的，不是给
# 程序判断用的信号源）。两条产出路径（LLM 成功 / _rule_fallback 兜底）都
# 在各自返回前调用同一个函数，保证无论走哪条路径出处传播规则一致。

_SCALAR_PROVENANCE_FIELDS: tuple[str, ...] = (
    "start_time",
    "start_weekday",
    "duration_hours",
    "distance_max_km",
    "social_context",
    "capacity_requirement",
    # ADR-0014 决策 3（G-3）：budget_per_person 同款标量 diff 传播——changed→
    # user_stated 已由下方通用循环覆盖，不需要专属分支。
    "budget_per_person",
)

_LIST_PROVENANCE_FIELDS: tuple[str, ...] = (
    "physical_constraints",
    "dietary_constraints",
    "experience_tags",
    "extra_services",
)

# "重申升级"（值没变，但反馈原话又重新提了一遍 → 升级 user_stated）只对
# "值是可读中文短语、可能直接出现在反馈原话里"的字段做字面核对。数字类标量
# （distance_max_km/duration_hours/capacity_requirement）子串匹配噪声太大
# （"5"这种短数字极易在无关文本里假阳性命中），排除，只保留"继承原出处"语义。
_REASSERT_CHECKABLE_SCALAR_FIELDS: frozenset[str] = frozenset({"social_context", "start_weekday"})


# ============================================================
# C1 通用「键缺失继承」守卫（forge-intent-loss 收敛，2026-07-12）
# ============================================================
#
# 【命名问题 + prior art】refiner 反馈轮对 intent 是"整体替换"（STATE 架构：
# LLM 每轮重新吐出完整对象），但语义上这本该是 partial update——RFC 7386
# JSON Merge Patch 的核心纪律是"键缺失=不动、值显式 null=删除"，两者绝不能
# 用同一种"缺省"表达；DST（对话状态追踪）文献同样确认"未变化的槽位应由外部
# 机制 carryover，不该指望模型每轮重新生成"（Improving Long Distance Slot
# Carryover, arXiv:1906.01149）。本函数是这条规范在 C1（渐进式、不改契约）
# 路线下的落地：把 explicit_dining_requested 的专属"键缺失继承"补丁
# （原 refiner.py:453-464，四条不变式批 C5a）推广为覆盖全部 9 个"静默默认
# 或可选"需求字段的通用规则。
#
# 【为什么是这 9 个字段（逐字段 schema 审计，非 prompt 语义猜测）】
# IntentExtraction 18 个字段按 `Field(...)` 签名分三类：
# - A 响亮必传（start_time/companions/physical_constraints/dietary_constraints/
#   experience_tags/parse_confidence/raw_input）：键缺失 → Pydantic
#   ValidationError → 现有错误回灌重试 / _rule_fallback 兜底（从 original
#   model_copy 恢复）——双重安全，本就无需守卫；且三类 tag 的"消失"正是
#   `record_rejected` 的负反馈信号源（graph/nodes/refiner.py::_dropped_tags），
#   无差别继承会让"LLM 忘写"和"用户真的不要"都变成"没消失"，悄悄吃掉负反馈
#   信号——必传字段刻意不纳入本守卫。
# - B 静默默认·语义要紧（duration_hours=[4,6] / distance_max_km=5.0 /
#   social_context="家庭日常"）：默认值是"具体值"而非"空语义"，键缺失会
#   静默把 user_stated 的值顶回这个默认值，且不被任何 record_rejected 逻辑
#   接住——真实的静默丢失面，必须纳入。
# - C 可选空默认（start_weekday/capacity_requirement/extra_services/
#   preferred_poi_types/explicit_dining_requested/budget_per_person）：默认值
#   本身就是"没设"的合法语义表达，但 user_stated 过的仍需守卫继承（否则同样
#   静默丢失）。
# 显式排除（虽也带 default，但继承是 bug，不是漏保护）：understanding（每轮
# 必须重新生成，继承旧值=让用户看到上一轮的①拍文案）、ambiguous_fields
# （描述抽取过程本身非用户诉求）、field_provenance（守卫跑完后由
# `_propagate_field_provenance` 整体重算，不走继承逻辑）。
#
# 白名单必须是显式枚举，不能写成"凡带 default 就继承"——那会把 understanding
# 也继承了。
_INHERITABLE_FIELDS: tuple[str, ...] = (
    # B 类：静默默认·语义要紧
    "duration_hours",
    "distance_max_km",
    "social_context",
    # C 类：可选空默认
    "start_weekday",
    "capacity_requirement",
    "extra_services",
    "preferred_poi_types",
    "explicit_dining_requested",
    "budget_per_person",
)

# 出处门控（T2）：哪些出处值"倾向继承"——user_stated 是用户亲口说的，理应
# 保留；inferred/prior/default 是推断/画像/兜底，不该被"继承守卫"钉死，让
# 本轮 LLM（哪怕键缺失落回 Pydantic 默认）自由覆盖，不强行继承一个连用户
# 自己都没说过的旧推断值。
_INHERIT_PRIORITY_PROVENANCE: frozenset[str] = frozenset({"user_stated"})

# 无先验注入通道字段：门控在"缺 provenance 记录"时的默认值不能一刀切
# ——必须按字段本身"除 user_stated 外还有没有别的产出路径"来定，这条判据
# 复用 parser.py 已经拍板的同一份分类（`agent.intent.parser.compute_injected_priors`
# 的 `InjectedPriors` 只覆盖 social_context/distance_max_km/三类 tag；
# `_apply_provenance_correction` 对没有先验注入通道、也没有"随手给个默认
# 数字"可比对的字段——start_time/start_weekday/capacity_requirement/
# budget_per_person——明文规定"缺自报时一律兜底 user_stated"，理由是"这几个
# 字段只要有值，几乎总是来自用户或明确推断，不存在被随手塞一个先验/默认值
# 的情况"）。本守卫的 9 个白名单字段里同样成立这条判据的有：
# - `explicit_dining_requested` / `preferred_poi_types`：**根本不在**
#   `field_provenance` 覆盖范围内（schemas/intent.py 字段 docstring 明文
#   排除），生产环境里这两个字段**永远不会有** provenance 记录——如果门控
#   要求"必须查到 user_stated 才继承"，这两个字段会被门控**无条件拦死**，
#   直接退化回"守卫形同虚设"，把 explicit_dining_requested 原有专属补丁
#   （无条件继承，不查 provenance）的行为打回去了，这是需要修正的真实缺口，
#   不是可以忽略的边界情况。
# - `start_weekday` / `capacity_requirement` / `budget_per_person`：与 parser
#   同款字段，无先验注入通道，parser 侧已确立"缺记录=按 user_stated 处理"。
# - `extra_services`：parser 侧同样明文"无先验注入通道，只做自报兜底，不做
#   prior 强制纠偏"——同一分类。
# 这 6 个字段缺 provenance 记录时按 `user_stated` 处理（复用 parser 的既有
# 结论，不是我自己另拍一个）；`duration_hours`/`distance_max_km`/
# `social_context` **不**在这个集合里——它们在 `InjectedPriors`/
# `_SCALAR_SCHEMA_DEFAULTS` 里有真实的 prior/inferred/default 产出路径，
# 缺 provenance 记录时按"未知，不强行继承"处理，与 parser 侧
# `_apply_provenance_correction` 的分野完全对称，不是新发明的例外。
_NO_PRIOR_CHANNEL_FIELDS: frozenset[str] = frozenset(
    {
        "explicit_dining_requested",
        "preferred_poi_types",
        "start_weekday",
        "capacity_requirement",
        "budget_per_person",
        "extra_services",
    }
)

# 矛盾检测判据（T2 反例修复）：user_stated 不是无条件安全——若反馈原话对
# 该值显式表达了否定/改口（"不吃烧烤了""不要预算限制了""换成……"），继承
# 反而是错的（该撤回的被继承）。词表覆盖率无语料验证，是启发式兜底，不是
# 精确解——漏判方向是"保守留旧值"，比原始 bug"该保留的被丢"温和一个量级，
# 但不是零残余（R2/R3 已诚实记录）。
_NEGATION_MARKERS: tuple[str, ...] = (
    "不要", "不吃", "不去", "别", "不用", "算了", "取消", "换成", "改成", "不再",
)

# 列表/短语字段：值是可读中文短语，可能直接出现在反馈原话里，能做"邻近否定
# 词"字面检测（同 `_REASSERT_CHECKABLE_SCALAR_FIELDS` 的"数字类标量子串匹配
# 噪声太大，排除"同一条纪律的镜像——这里反过来，短语类才做检测）。
_CONTRADICTION_CHECKABLE_LIST_FIELDS: frozenset[str] = frozenset(
    {"preferred_poi_types", "extra_services"}
)


def _value_contradicted_by_feedback(value: object, feedback: str) -> bool:
    """反馈原话是否显式否定/改口了这个（中文短语）值本身。

    启发式：值本身出现在反馈里 + 反馈里存在否定词 → 判"很可能是在说不要这个
    了"。不做真正的邻近窗口/句法分析（成本与本项目 hackathon 节奏不匹配），
    命中即判——宁可少数误判"没矛盾"（继承没撤回的旧值，方向温和），也不做
    过度复杂的语义分析。
    """
    if not isinstance(value, str) or not value or not feedback:
        return False
    if value not in feedback:
        return False
    return any(marker in feedback for marker in _NEGATION_MARKERS)


def _inherit_missing_keys(
    original: IntentExtraction,
    refined_intent_data: dict,
    feedback_text: str,
) -> None:
    """校验前拦 LLM 原始输出 dict：白名单字段键缺失 → 按出处门控 + 矛盾检测
    从 original 继承（原地修改 `refined_intent_data`）。

    机制（RFC-7386 键存在性语义）：
    - 键**不在** dict 里 → LLM 忘写 → 按下方门控决定是否继承。
    - 键**在** dict 里（哪怕值是 `null`/`[]`）→ LLM 显式表态（撤回或改写）
      → 放行，不干预——这是 null-on-removal 撤回信道的技术基础：撤回时
      LLM 应输出显式空值而非省略键，两者在 JSON 层面可区分（`in` 检查键
      存在性，不检查值是否为 None/空）。

    门控规则（T2，含 R4 修正）：
    - `original` 该字段的出处若为 `user_stated` → 倾向继承，但先过矛盾检测
      （`_value_contradicted_by_feedback` / budget_per_person 的显式撤回
      信道）——反馈原话显式否定/撤回该值时不继承，让它落回 Pydantic 默认
      （或走 `_rule_fallback` 关键词分支重新判断）。
    - 出处明确记录为 `inferred`/`prior`/`default` → 放行，不继承（本轮 LLM
      的判断，哪怕键缺失落回默认值，也不该被一个连用户自己都没说过的旧
      推断值钉死）。
    - **出处无记录**（`field_provenance` 里没有这个键）时的默认值不能一刀切
      按"不继承"处理——`_NO_PRIOR_CHANNEL_FIELDS` 集合里的字段（同
      `agent.intent.parser._apply_provenance_correction` 的既有分类：无
      persona/memory 先验注入通道、也没有"随手给个默认值"可比对）本来就
      只有 user_stated 一条产出路径，无记录时按 user_stated 处理（复用
      parser 侧已拍板的同一条规则，不是新发明）——`explicit_dining_requested`
      /`preferred_poi_types` 尤其关键：它们**根本不在** `field_provenance`
      覆盖范围内（schema 字段 docstring 明文排除），生产环境永远不会有
      provenance 记录，若按"无记录=不继承"处理，会让这两个字段的继承守卫
      形同虚设（explicit_dining_requested 原专属补丁是无条件继承，不查
      provenance——收编后的通用守卫如果因为查不到 provenance 就拒绝继承，
      是行为倒退，不是行为对齐）。其余字段（duration_hours/distance_max_km/
      social_context，真有 prior/inferred 产出路径）无记录时仍按"未知，
      不强行继承"处理，与 parser 侧的分野对称。

    列表字段（preferred_poi_types/extra_services）继承的是"没丢失"（0→不变）
    的下限，不是"正确合并"——用户"再加个看展的"这类追加诉求若 LLM 忘写该键，
    继承拿到的是旧值本身、不含追加的新元素（决策点 D，C1 的既知天花板，
    C2 delta 架构才能原生解决，本次不做）。
    """
    old_prov = original.field_provenance or {}
    fb = feedback_text or ""

    for field in _INHERITABLE_FIELDS:
        if field in refined_intent_data:
            continue  # 键存在（哪怕值是 null/[]）= LLM 显式表态，不干预

        old_value = getattr(original, field)
        provenance = old_prov.get(field)

        if provenance is None and field in _NO_PRIOR_CHANNEL_FIELDS:
            # 无先验注入通道的字段，缺记录按 user_stated 处理（parser 侧
            # 既有分类的直接复用，见上方 docstring）。
            provenance = "user_stated"

        if provenance not in _INHERIT_PRIORITY_PROVENANCE:
            # inferred/prior/default/仍无记录（有先验通道的字段）：不继承，
            # 放行给 Pydantic 默认值
            continue

        # user_stated：矛盾检测——反馈原话是否显式否定/撤回了这个值
        if field in _CONTRADICTION_CHECKABLE_LIST_FIELDS and isinstance(old_value, list):
            contradicted = any(
                _value_contradicted_by_feedback(v, fb) for v in old_value
            )
            if contradicted:
                continue  # 判显式撤回意图，不继承，宁可落空让 drift/兜底重新判
        # 数字/枚举类标量（budget_per_person/duration_hours/distance_max_km/
        # social_context/start_weekday/capacity_requirement）不做子串否定
        # 检测（同 `_REASSERT_CHECKABLE_SCALAR_FIELDS` 排除数字标量的纪律：
        # 假阳性噪声太大）——撤回信道完全依赖 null-on-removal（键存在+值
        # null=撤回），不依赖否定词表。

        refined_intent_data[field] = old_value


def _repair_dictionary_drift(
    refined: IntentExtraction, feedback_text: str
) -> IntentExtraction:
    """反馈轮「词典外品类」漂移根治（用户拍板方案2：共享规则，中立后处理）。

    【命名问题】parser 首轮与 refiner 反馈轮本该共享同一份"品类归属"认知——
    这是「多入口共享同一条业务规则」的经典问题（DRY 原则的落地形态之一：
    同一判定逻辑不能只在一个入口实现、指望另一个入口的 LLM 每次都记得），
    成熟做法是把判定收敛到一个中立函数，被动态检查各入口的输出是否漏判，
    而不是指望每个入口的 prompt 各自记住同一条规则（prompt 记忆是软约束，
    程序化校验才是硬约束——同 ADR-0014 决策 1"反馈轮出处传播不要 LLM 自报，
    纯规则 diff 现算"是同一条纪律：LLM 侧教了只是双保险，真正兜底的是这里）。

    【背景】`intent_parser_prompt.py`「明示餐饮/活动品类必须保留」段教 LLM：
    词典外品类（烧烤/撸串/火锅…）必须原样写进 `preferred_poi_types`（下游
    anchor-escape 靠这个信号触发召回）。但 `refiner_prompt.py`（反馈轮）
    从未提过 `preferred_poi_types`——LLM 反馈轮遇到"吃个烧烤"时，会尝试把
    它塞进 `dietary_constraints`（词典内没有"烧烤"这个 Literal 值）→ 校验
    失败会被 Pydantic 拦截整条丢弃，或 LLM 自己判断"词典没有就不加"→
    `preferred_poi_types` 保持空 → anchor-escape 收不到信号 → 品类丢失。

    【本函数做什么】refiner 产出 `refined_intent` 后，程序化检查：反馈原话
    里提到的词，若命中 `category_vocab.all_canonical_terms()`（词汇表单一
    真相源，同一张表也是 `poi_desire_match`/prompt 例词对齐测试的依据）、
    且不在 `DIETARY_TAGS` 封闭词典内（词典内有对应词的走 dietary_constraints
    正常路径，不需要本函数插手）、且尚未出现在 `preferred_poi_types` 里
    （幂等：parser 首轮已正确填的不重复加，也不覆盖 LLM 这轮自己正确填出的）、
    且**反馈原话没有对这个词显式否定/撤回**（见下方"撤回感知"）
    → 自动补进 `preferred_poi_types`。

    词表来源刻意复用 `category_vocab.all_canonical_terms()`，不新拍一个
    近似判断——避免"哪些词算品类"这件事出现第二个漂移的真相源。

    【为什么是"补齐"而非"替换"】只做追加、不做删除或改写：反馈轮的原有
    产出（LLM 或 _rule_fallback 给出的 preferred_poi_types）视为已经正确，
    本函数只补漏，不覆盖——避免在"共享规则"之外再引入一次额外的字段级
    决策权，保持"最小必要介入"（同 refiner prompt 的"字段最小修改原则"）。

    【撤回感知（forge-intent-loss 收敛批发现的联动缺口，2026-07-12）】
    本函数原始版本对"词出现在反馈原话里"做纯字面匹配，不看这次提及是肯定
    还是否定——这在 null-on-removal 撤回信道（`_inherit_missing_keys` 教
    LLM 显式撤回时输出 `preferred_poi_types: []`）落地后会形成一个真实的
    联动 bug：用户说"不吃烧烤了"，LLM 正确输出显式 `[]`（撤回生效），但
    "烧烤"这个词仍然字面出现在反馈原话里——本函数不看否定词，会把刚撤回的
    "烧烤"重新加回来，直接抵消撤回信道的效果。复用 `_inherit_missing_keys`
    同款的 `_value_contradicted_by_feedback` 否定词检测（同一份判据，不
    另拍一个近似版本——避免"否定检测"出现第二个真相源，同本函数"词表复用
    all_canonical_terms()"的既有纪律）：命中"词本身 + 邻近否定词"就跳过
    该词的补齐，让撤回真正生效。
    """
    fb = feedback_text or ""
    if not fb:
        return refined

    existing = set(refined.preferred_poi_types or [])
    to_add: list[str] = []
    for term in all_canonical_terms():
        if term in DIETARY_TAGS:
            continue  # 词典内有对应词 → 走 dietary_constraints 正常路径，不由本函数插手
        if term in existing:
            continue  # 幂等：已经填过（parser 首轮或本轮 LLM 已正确产出）不重复加
        if term in fb:
            if _value_contradicted_by_feedback(term, fb):
                continue  # 撤回感知：反馈原话否定了这个词，不该被本函数补回来
            to_add.append(term)
            existing.add(term)

    if not to_add:
        return refined
    return refined.model_copy(
        update={"preferred_poi_types": list(refined.preferred_poi_types or []) + to_add}
    )


def _propagate_field_provenance(
    original: IntentExtraction,
    refined: IntentExtraction,
    feedback_text: str,
) -> dict[str, str]:
    """反馈轮出处传播——纯规则，不依赖 LLM 自报（ADR-0014 决策 1）。

    对 original/refined 两份 IntentExtraction 做结构化 diff：
    - 标量字段：值变了 → `user_stated`；值未变 → 继承原出处（若原出处非
      `user_stated` 且反馈原话字面重申了该值 → 升级 `user_stated`，仅对
      `_REASSERT_CHECKABLE_SCALAR_FIELDS` 做重申检测）。
    - 列表字段：新元素（refined 有、original 没有）→ `user_stated`；仍存在
      的元素继承原出处（同上重申升级检测，列表元素都是中文短语，天然适用）；
      撤回的元素（original 有、refined 没有）不写回 key——出处键同步清理。
    - 原本没有 provenance 记录的字段/键（老数据 / 首轮未标）在未变更时也
      不写回（保持 Optional 语义，不无中生有）。
    """
    old_prov = dict(original.field_provenance or {})
    new_prov: dict[str, str] = {}
    fb = feedback_text or ""

    for field in _SCALAR_PROVENANCE_FIELDS:
        old_val = getattr(original, field)
        new_val = getattr(refined, field)
        if new_val != old_val:
            new_prov[field] = "user_stated"
            continue
        old_p = old_prov.get(field)
        if old_p is None:
            continue
        if (
            old_p != "user_stated"
            and field in _REASSERT_CHECKABLE_SCALAR_FIELDS
            and isinstance(new_val, str)
            and new_val
            and new_val in fb
        ):
            new_prov[field] = "user_stated"
        else:
            new_prov[field] = old_p

    for field in _LIST_PROVENANCE_FIELDS:
        old_list = list(getattr(original, field) or [])
        new_list = list(getattr(refined, field) or [])
        old_set = set(old_list)
        for value in new_list:
            key = f"{field}:{value}"
            if value not in old_set:
                new_prov[key] = "user_stated"
                continue
            old_p = old_prov.get(key)
            if old_p is None:
                continue
            if old_p != "user_stated" and value in fb:
                new_prov[key] = "user_stated"
            else:
                new_prov[key] = old_p
        # 撤回元素（old_set 里有、new_list 没有）：对应 key 不写入 new_prov，
        # 即"出处键同步清理"——上面的循环天然只遍历 new_list，撤回的元素
        # 根本不会进入这一轮，键就此消失。

    return new_prov


def _compose_raw_input(original_raw: str, feedback: str) -> str:
    """决定 refined.raw_input 的拼法（下游 preference_scorer / 重规划 message 都读它）。

    - 局部反馈（太远 / 便宜 / 换个氛围）：原句是请求主体，反馈追加在后。
    - 换场景的延续（周末改带爸妈吃饭）：新句才是主体，原句退为括注上下文——
      免得下游同时读到旧场景词（老婆孩子）和新场景词（爸妈）而自相矛盾。
    """
    fb = (feedback or "").strip()
    if not fb:
        return original_raw
    if looks_like_feedback(fb):
        return f"{original_raw}（反馈：{fb}）"
    return f"{fb}（上一版：{original_raw}）"


# ============================================================
# 主入口
# ============================================================

def refine_intent(
    original: IntentExtraction,
    feedback_text: str,
    *,
    client: LLMClient | None = None,
    max_retries: int = 1,
    itinerary_summary: str | None = None,
    ledger_recap: str | None = None,
) -> RefinementOutput:
    """合并反馈进原 intent。

    流程：
    1. 调 LLM（response_format=json_object）
    2. 剥围栏 + json.loads
    3. Pydantic v2 校验（refined_intent 必须合法 IntentExtraction）
    4. 若失败 → 错误回灌 1 次
    5. 若仍失败 → _rule_fallback 兜底（不抛异常）

    `client` 缺省时通过 get_llm_client() 自动按 LLM_PROVIDER 环境变量构造，
    便于 HTTP 层（main.py）直接 `refine_intent(original, feedback)` 调用而不必关心 LLM 接线。

    `ledger_recap`（ADR-0011 决策 3 refiner 切片，2026-07-03 新增）：调用方
    （`agent/graph/nodes/refiner.py::refiner_node`）经会话上下文打包器
    产出的「方案版本志 + 台账生效条目」文本，见 `build_user_message` 同名
    参数 docstring。只影响 LLM 路径的 prompt；`_rule_fallback` 走关键词兜底，
    不消费这个字段（兜底本就不读会话历史）。
    """
    if client is None:
        from ..core.llm_client import get_llm_client

        try:
            client = get_llm_client()
        except (ValueError, RuntimeError):
            # 缺 API key / base_url 等配置问题 → 直接走 _rule_fallback
            return _rule_fallback(original, feedback_text)
    original_json = original.model_dump_json()

    error_feedback: str | None = None
    for attempt in range(max_retries + 1):
        try:
            return _llm_refine(
                original=original,
                original_json=original_json,
                feedback_text=feedback_text,
                client=client,
                error_feedback=error_feedback,
                itinerary_summary=itinerary_summary,
                ledger_recap=ledger_recap,
            )
        except Exception as e:  # noqa: BLE001 —— 见下方说明,兜底承诺必须覆盖全部异常
            # 原为 (RefinementError, ValidationError, json.JSONDecodeError)——
            # 只兜"LLM 回了但内容坏"的三类;传输层异常(APITimeoutError/连接拒绝)
            # 会穿透,炸成 stream_error(graph_execution_failed)。--degraded 降级
            # 演练实锤(2026-07-03):LLM 挂掉时首轮/路由/叙事都能扛,唯独反馈轮
            # 直接报错,违反本函数 docstring"若仍失败→_rule_fallback 不抛异常"
            # 的承诺。改为全兜:内容类异常带 error_feedback 重试仍有意义,传输类
            # 重试一次无害(可能瞬断),最终一律落规则兜底——与 intent_node 的
            # except Exception 哲学对齐。
            error_feedback = str(e)
            if attempt >= max_retries:
                # 走兜底，不抛异常（Demo 不能因为 LLM 出 bug 而转圈）
                return _rule_fallback(original, feedback_text)


def _llm_refine(
    *,
    original: IntentExtraction,
    original_json: str,
    feedback_text: str,
    client: LLMClient,
    error_feedback: str | None,
    itinerary_summary: str | None = None,
    ledger_recap: str | None = None,
) -> RefinementOutput:
    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=REFINER_SYSTEM_PROMPT),
    ]
    for fs_user, fs_assistant in REFINER_FEW_SHOTS:
        messages.append(LLMMessage(role="user", content=fs_user))
        messages.append(LLMMessage(role="assistant", content=fs_assistant))

    user_msg = build_user_message(original_json, feedback_text, itinerary_summary, ledger_recap)
    if error_feedback:
        user_msg = (
            f"上次输出存在错误：\n{error_feedback}\n\n"
            f"请重新按 schema 严格输出。\n\n"
            f"{user_msg}"
        )
    messages.append(LLMMessage(role="user", content=user_msg))

    resp = client.chat(
        messages,
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    cleaned = strip_json_fence(resp.content)
    if not cleaned:
        raise RefinementError(reason="empty_response")

    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise RefinementError(reason="not_a_json_object")

    # raw_input 兜底：保留原句 + 拼接本次反馈，让下游能从 raw_input 提取精确约束
    # （pitfalls P1-2026-05-17 引申：反馈作为最高优先级约束，必须落到下游可读的字段）
    refined_intent_data = payload.get("refined_intent", {})
    if isinstance(refined_intent_data, dict):
        refined_intent_data["raw_input"] = _compose_raw_input(
            original.raw_input, feedback_text
        )
        # C1 通用键缺失继承守卫（forge-intent-loss 收敛，2026-07-12）：收编原
        # explicit_dining_requested 专属补丁（四条不变式批 C5a），推广到 9 个
        # 白名单字段（见 `_inherit_missing_keys` docstring）。必须在
        # `model_validate` 之前跑——它直接操作 LLM 原始输出 dict 的键存在性，
        # 校验后的 IntentExtraction 对象已经看不到"键缺不缺"这个信号（Pydantic
        # 会把缺键字段静默填成默认值，届时无法区分"忘写"与"默认值恰好如此"）。
        _inherit_missing_keys(original, refined_intent_data, feedback_text)

    refined_intent = IntentExtraction.model_validate(refined_intent_data)

    raw_changed = list(payload.get("changed_fields", []) or [])
    # 问题 11 修复：LLM 可能在 changed_fields 里说改了时长，但 refined_intent.duration_hours
    # 字段没真改。强制对齐反馈里的具体小时数。
    refined_intent, fixed_changed = _enforce_duration_consistency(
        refined_intent, raw_changed, feedback_text
    )

    # 烧烤根治批 L1（共享规则，方案2）：LLM 反馈轮遗漏"词典外品类"信号时的
    # 中立后处理补齐——见 _repair_dictionary_drift docstring。放在
    # _propagate_field_provenance 之前跑（preferred_poi_types 不在 provenance
    # 覆盖范围内，顺序对本字段无影响，但保持"业务补齐先于出处记账"的固定顺序
    # 便于未来该字段若纳入 provenance 时无需重排）。
    refined_intent = _repair_dictionary_drift(refined_intent, feedback_text)

    # ADR-0014 决策 1（G-1）：反馈轮纯规则传播出处，覆盖/忽略 LLM 在
    # refined_intent.field_provenance 里可能自报的任何值（"不要 LLM 自报"）。
    refined_intent = refined_intent.model_copy(
        update={
            "field_provenance": _propagate_field_provenance(
                original, refined_intent, feedback_text
            )
        }
    )

    return RefinementOutput(
        refined_intent=refined_intent,
        changed_fields=fixed_changed,
        refiner_note=payload.get("refiner_note") or None,
    )


# ============================================================
# 规则化兜底（LLM 失败时不让 Demo 翻车）
# ============================================================

# 关键词 → 字段调整映射（粗粒度）
_KEYWORDS_DISTANCE_NEAR = ("太远", "近一点", "近些", "别太远", "靠近")
_KEYWORDS_DISTANCE_FAR = ("远一点", "远点", "再远", "不限距离")
_KEYWORDS_CHEAPER = ("太贵", "便宜", "划算", "省点", "预算紧", "贵了")
_KEYWORDS_TIME_TIGHT = ("时间紧", "快一点", "短一点", "时间不多")
_KEYWORDS_TIME_LOOSE = ("时间多", "长一点", "再长")

# ADR-0014 G-0（2026-07-03）迁移说明：
# "这段太长 / 太久 / 盯不住 / 腻了" 类反馈原意是"单段节奏太长"，历史上缩的是
# pace_profile.single_session_max_min（不动 duration_hours / distance_max_km）。
# 但 pace_profile 全系统无消费方（agent/planning/planners/pace_budget.py 自证
# 不读该字段，走自己的 relaxed/medium/energetic 三档模型），该收缩纯属业务空转。
# 迁移后收缩目标改为 duration_hours 上界（规划器拿它定总时长硬预算，真实消费）——
# 用户说"太久了"最终感知到的是总时长上限收紧，效果上仍是"这趟变短了"。
_KEYWORDS_SESSION_TOO_LONG = (
    "太久", "太长", "盯不住", "无聊", "扛不住", "腻了",
)

# 收缩比例（30%）：沿用迁移前 pace_profile 时代的比例设计，避免无依据地另起数字。
_SESSION_SHRINK_RATIO = 0.7

# 下限保护：duration_hours 上界不缩过 duration_hours 下界（避免 [lo,hi] 反转成
# 无效区间），也不缩过 1 小时地板（0 小时的半日出行没有业务意义）。
_MIN_DURATION_HOURS_HI = 1


# ===== 中文数字 → 阿拉伯数字（仅 1-9，常用即可）=====
_CN_DIGITS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _extract_duration_from_feedback(feedback: str) -> tuple[int, int] | None:
    """从反馈文本里抽取具体小时时长。

    支持模式：
    - "我只有 1 小时"  / "一小时"        → (1, 1)
    - "就 2 小时吧"   / "两小时"         → (2, 2)
    - "1 到 2 个小时"                    → (1, 2)
    - "再给我 2-3 小时"                  → (2, 3)
    - "半小时" / "30 分钟"               → (0, 1)   spec planning-quality-deep-review R8
    - "一个半小时" / "1.5 小时" / "1 个半小时" → (1, 2)   spec planning-quality-deep-review R8

    无具体数字（"时间紧" / "时间多"）→ None，让上层走关键词分支或保留原值。

    设计纪律：本函数是问题 11 修复的核心；写完后 _rule_fallback 与 _enforce_duration_consistency
    都依赖它。返回 tuple 而非 list 是因为下游统一用 list(refined.duration_hours) 比较，
    tuple 让函数纯化（不可变）。

    spec planning-quality-deep-review R8 扩展：增加分钟级 / 半小时级 / 1.5 小时级三类正则
    （以前只识别整数小时）。"半小时差不多" 等带尾随修饰词的也命中（命中后下游裁段会按 0-1h 兜底）。
    """
    import re

    if not feedback:
        return None
    s = feedback.strip()

    # ===== spec R8 扩展：先匹配 "一个半小时" / "1 个半小时" / "1.5 小时" / "X 个半小时"
    # 之所以放最前是因为 "一个半小时" 会被下面的 "一" 中文数字先匹配掉（误识别为 1 小时）。
    one_and_half_re = re.compile(
        r"(?:一个半小时|一个半|1\s*个半小时|1\s*个半|1[\.．]5\s*(?:个)?\s*小时)"
    )
    if one_and_half_re.search(s):
        return (1, 2)

    # ===== spec R8 扩展：分钟级 / 半小时级
    # 半小时（不带其他数字）→ (0, 1)
    if re.search(r"半\s*小时", s) and not re.search(r"[一二两三四五六七八九十1-9]\s*个?\s*半\s*小时", s):
        # "半小时"" / "就半小时" / "半小时差不多" → (0, 1)
        # 但 "一个半小时" / "1 个半小时" 已被上面分支吃掉，这里只剩纯 "半小时"
        return (0, 1)
    # 30/45/15/20/40/50 分钟 等典型分钟级
    minutes_re = re.compile(r"(\d+)\s*分钟")
    m = minutes_re.search(s)
    if m:
        n = int(m.group(1))
        if 0 < n < 60:
            # 不足 1 小时统一映射到 (0, 1)
            return (0, 1)
        if 60 <= n <= 12 * 60:
            # ≥ 60 分钟也兜底转小时（如 "90 分钟"）
            hours = n // 60
            extra = 1 if n % 60 else 0
            return (hours, hours + extra)

    # 把中文数字归一为阿拉伯数字（仅在「数字 + 小时」上下文里替换，避免误伤）
    for cn, ar in _CN_DIGITS.items():
        s = s.replace(f"{cn}小时", f"{ar} 小时")
        s = s.replace(f"{cn}个小时", f"{ar} 小时")

    # 范围模式（必须先匹配，避免被单数字模式截断）
    range_re = re.compile(r"(\d+)\s*(?:到|至|-|~)\s*(\d+)\s*(?:个)?\s*小时")
    m = range_re.search(s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if 0 < lo <= hi <= 12:
            return (lo, hi)

    # 单数字模式
    single_re = re.compile(r"(\d+)\s*(?:个)?\s*小时")
    m = single_re.search(s)
    if m:
        n = int(m.group(1))
        if 0 < n <= 12:
            return (n, n)

    return None


def _enforce_duration_consistency(
    refined: IntentExtraction,
    changed_fields: list[str],
    feedback: str,
) -> tuple[IntentExtraction, list[str]]:
    """LLM 输出后校验：refined.duration_hours 必须与 feedback 真实数字一致。

    问题 11 根因：LLM 在 changed_fields 文本里复读了用户的"1 小时"，
    但 refined_intent.duration_hours 字段保留原值 [4,6]。下游 planner 用错时长
    导致行程仍 4 小时多。

    策略：
    - 反馈含具体数字（_extract_duration_from_feedback 命中）
    - refined.duration_hours 与提取值不符
    → 强制覆盖 refined.duration_hours，并修正 changed_fields 文本（如果 LLM 没生成时长条目，则补一条）
    """
    extracted = _extract_duration_from_feedback(feedback)
    if extracted is None:
        return refined, changed_fields

    current = tuple(refined.duration_hours)
    if current == extracted:
        return refined, changed_fields  # 已一致

    # 强制对齐
    fixed = refined.model_copy(update={"duration_hours": list(extracted)})
    fixed_changed = list(changed_fields)

    # 修正或补充 changed_fields 里的时长条目
    new_msg = f"时长：{list(current)} → {list(extracted)} 小时"
    has_duration_entry = any("时长" in c for c in fixed_changed)
    if has_duration_entry:
        fixed_changed = [
            new_msg if "时长" in c else c for c in fixed_changed
        ]
    else:
        fixed_changed.append(new_msg)

    return fixed, fixed_changed


# ============================================================
# ADR-0014 决策 3（G-3）：反馈里明说的预算数字 → budget_per_person
# ============================================================

_BUDGET_NUMBER_RE_PATTERNS: tuple[re.Pattern, ...] = (
    # "人均 150" / "人均150元" / "人均差不多150" —— 最具体，优先匹配；
    # [^\d]{0,6} 容忍"提到/给到/定在/就/是/差不多/大概/控制在"等任意短连接词
    re.compile(r"人均[^\d]{0,6}(\d+(?:\.\d+)?)"),
    # "预算 200" / "预算给到200" / "预算提到200" / "预算定在200" / "预算就200"
    re.compile(r"预算[^\d]{0,6}(\d+(?:\.\d+)?)"),
    # 兜底："200 元/块（以内/左右/上下/一个人/每人）" —— 泛化数字+货币单位
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块钱?)(?:以内|左右|上下|一个人|每人)?"),
)


def _extract_budget_from_feedback(feedback: str) -> float | None:
    """从反馈文本里抽取用户明说的人均预算数字（ADR-0014 决策 3，与
    `_extract_duration_from_feedback` 同款设计：定量表达才提取，不编造）。

    只在原话**明确给出数字**时返回值——"太贵了/便宜点"这类定性反馈不含数字，
    本函数天然返回 None（不硬映射），budget_per_person 保持原值或 None，
    与 parser 首轮"定性不映射数字"同一条纪律的反馈轮镜像。

    模式按具体到泛化排序（"人均" > "预算" > 泛化"元/块"），避免"预算紧张，
    这次五公里以内"这类句子里的"5"被泛化模式误吞——泛化模式要求"元/块"
    货币单位紧跟数字，公里数不会误命中。
    """
    if not feedback:
        return None
    for pattern in _BUDGET_NUMBER_RE_PATTERNS:
        m = pattern.search(feedback)
        if m:
            try:
                return float(m.group(1))
            except ValueError:  # pragma: no cover 防御性
                continue
    return None


# ============================================================
# 信任带修订5：反馈轮 understanding——LLM 路径靠 prompt 现生成（见
# refiner_prompt.py 的【understanding 风格】），但 `LLM_PROVIDER=stub` 下
# refiner 实际走的是本文件的 _rule_fallback（StubLLMClient 返回的扁平
# IntentExtraction JSON 没有 refined_intent 外层包装，校验必炸，见
# test_refiner.py::test_refine_intent_with_stub_falls_back_to_rule 钉住的
# 既有行为）——"stub 兜"必须落在这里，否则 --stub 冒烟下反馈轮①拍永远空白。
# ============================================================

_UNDERSTANDING_MAX_QUOTE_LEN = 12


def _rule_understanding(feedback: str, is_scenario: bool, changed: list[str]) -> str:
    """规则化兜底版 understanding——同 §四①风格红线（句式"用户说……，我理解成……"、
    ≤40 字、同款禁词），但没有 LLM 可用，只能按已经算过的关键词分支归纳一句，
    不是自由生成。反馈为空时改用"用户没再多说，我理解成……"（同 prompt 风格
    红线里的空反馈变体）。
    """
    fb = feedback.strip()
    if not fb:
        return "用户没再多说，我理解成先重新打散候选试试"

    quoted = fb if len(fb) <= _UNDERSTANDING_MAX_QUOTE_LEN else fb[:_UNDERSTANDING_MAX_QUOTE_LEN] + "…"
    prefix = f"用户说{quoted}，我理解成"

    if is_scenario:
        return f"{prefix}这次要换个新场景"
    if any(k in fb for k in _KEYWORDS_DISTANCE_NEAR):
        return f"{prefix}要拉近距离"
    if any(k in fb for k in _KEYWORDS_DISTANCE_FAR):
        return f"{prefix}范围可以再放宽点"
    if any(k in fb for k in _KEYWORDS_CHEAPER) or _extract_budget_from_feedback(fb) is not None:
        return f"{prefix}预算要收紧"
    if any(k in fb for k in _KEYWORDS_TIME_TIGHT):
        return f"{prefix}时间得压缩一下"
    if any(k in fb for k in _KEYWORDS_TIME_LOOSE) or any(k in fb for k in _KEYWORDS_SESSION_TOO_LONG):
        return f"{prefix}时长要调整一下"
    if changed:
        return f"{prefix}要按这个调整一下"
    return f"{prefix}先重新配一版试试"


def _rule_fallback(
    original: IntentExtraction, feedback_text: str
) -> RefinementOutput:
    """LLM 失败时按关键词做轻量调整。

    确保 refined_intent 仍是合法 IntentExtraction（用 model_copy(update=...)）。
    """
    feedback = (feedback_text or "").strip()
    feedback_lower = feedback.lower()
    # 这次输入像"对方案的反馈"还是"换了个新场景"(LLM 不可用时，规则抽不出新场景，避免乱改)
    is_scenario = bool(feedback) and not looks_like_feedback(feedback)

    updates: dict = {}
    changed: list[str] = []

    # 距离
    if any(k in feedback for k in _KEYWORDS_DISTANCE_NEAR):
        old = original.distance_max_km
        new = max(2.0, round(old * 0.6, 1))
        if new < old:
            updates["distance_max_km"] = new
            changed.append(f"距离上限：{old}km → {new}km")
    elif any(k in feedback for k in _KEYWORDS_DISTANCE_FAR):
        old = original.distance_max_km
        new = min(15.0, round(old * 1.5, 1))
        if new > old:
            updates["distance_max_km"] = new
            changed.append(f"距离上限：{old}km → {new}km")

    # 预算（去高人均 / 商务体面）
    if any(k in feedback for k in _KEYWORDS_CHEAPER):
        new_dietary = [t for t in original.dietary_constraints if t != "高人均"]
        if "健康轻食" not in new_dietary:
            new_dietary.append("健康轻食")
        if new_dietary != original.dietary_constraints:
            updates["dietary_constraints"] = new_dietary
            changed.append("去掉：高人均；加：健康轻食")
        new_exp = [t for t in original.experience_tags if t != "商务体面"]
        if new_exp != original.experience_tags:
            updates["experience_tags"] = new_exp
            changed.append("去掉体验：商务体面")

    # 预算——明说具体数字（ADR-0014 决策 3，G-3）：独立于上面的 CHEAPER 关键词
    # 判断（"预算给到 200"本身不含"贵/便宜"字样，需要单独识别，与
    # _extract_duration_from_feedback 独立于 TIME_TIGHT/TIME_LOOSE 关键词同一
    # 设计）。只在原话明说数字时才更新，不编造。
    extracted_budget = _extract_budget_from_feedback(feedback)
    if extracted_budget is not None and extracted_budget != original.budget_per_person:
        old_budget_label = (
            f"{original.budget_per_person:.0f}" if original.budget_per_person else "未设定"
        )
        updates["budget_per_person"] = extracted_budget
        changed.append(f"预算：{old_budget_label} → {extracted_budget:.0f} 元/人")

    # 时间——精确数字优先（"我只有 1 小时" / "两小时" / "2 到 3 小时"）
    extracted_duration = _extract_duration_from_feedback(feedback)
    if extracted_duration is not None:
        if tuple(original.duration_hours) != extracted_duration:
            updates["duration_hours"] = list(extracted_duration)
            changed.append(
                f"时长：{list(original.duration_hours)} → {list(extracted_duration)} 小时"
            )
    elif any(k in feedback for k in _KEYWORDS_TIME_TIGHT):
        if list(original.duration_hours) != [2, 3]:
            updates["duration_hours"] = [2, 3]
            changed.append(f"时长：{list(original.duration_hours)} → [2, 3] 小时")
    elif any(k in feedback for k in _KEYWORDS_TIME_LOOSE):
        if list(original.duration_hours) != [5, 7]:
            updates["duration_hours"] = [5, 7]
            changed.append(f"时长：{list(original.duration_hours)} → [5, 7] 小时")

    # ADR-0014 G-0：SESSION_TOO_LONG 反馈 → 缩 duration_hours 上界 30%
    # （迁移自原 pace_profile.single_session_max_min，见模块 docstring 与常量注释）。
    # 只在本轮尚未被更精确的数字反馈（"我只有 1 小时"类）决定 duration_hours 时才生效——
    # 显式数字永远比关键词猜的收缩比例精确，不应被本分支覆盖（见 test_rule_fallback_
    # explicit_hour_number_wins_over_session_too_long_keyword）。
    if any(k in feedback for k in _KEYWORDS_SESSION_TOO_LONG) and "duration_hours" not in updates:
        old_lo, old_hi = original.duration_hours[0], original.duration_hours[1]
        shrunk_hi = round(old_hi * _SESSION_SHRINK_RATIO)
        new_hi = max(shrunk_hi, old_lo, _MIN_DURATION_HOURS_HI)  # 下限保护
        if new_hi < old_hi:
            updates["duration_hours"] = [old_lo, new_hi]
            changed.append(
                f"时长上界：{old_hi}h → {new_hi}h（命中『太久』反馈，收紧总时长上限）"
            )

    # 反馈为空 / 模糊反馈且没命中关键词 → 轻量缩距离打散候选。
    # 但"换场景"不走这条：LLM 不可用、规则抽不出新同行/活动，做距离裁剪只会误导，
    # 宁可保留原约束，靠 raw_input(新句在前)让重规划看到新意图。
    if not updates and not is_scenario:
        old = original.distance_max_km
        if old > 2:
            new = max(2.0, round(old - 1, 1))
            updates["distance_max_km"] = new
            changed.append(f"距离上限：{old}km → {new}km（轻量调整）")

    # raw_input：局部反馈→原句在前；换场景→新句在前(见 _compose_raw_input)
    if feedback:
        updates["raw_input"] = _compose_raw_input(original.raw_input, feedback)

    # 信任带修订5（stub 兜）：understanding 每轮必须重新生成，不继承 original
    # 的旧值（那是上一轮的叙事，会让评委看到"文不对题"的①拍）。
    updates["understanding"] = _rule_understanding(feedback, is_scenario, changed)

    refined = original.model_copy(update=updates)

    # 烧烤根治批 L1（共享规则，方案2）：LLM 不可用时的降级路径也不能漏——
    # 与 _llm_refine 共用同一个中立后处理函数（见 _repair_dictionary_drift
    # docstring），保证无论走哪条路径，"词典外品类"信号都不会因为 LLM 缺席
    # 而丢失。命中时补一条 changed_fields，让前端 toast 也能看到这次调整。
    _before_poi_types = set(refined.preferred_poi_types or [])
    refined = _repair_dictionary_drift(refined, feedback)
    _added_poi_types = [
        t for t in (refined.preferred_poi_types or []) if t not in _before_poi_types
    ]
    if _added_poi_types:
        changed.append(f"加品类：{'、'.join(_added_poi_types)}")

    # ADR-0014 决策 1（G-1）："_rule_fallback 路径同样维护"——它改
    # distance_max_km / duration_hours / dietary_constraints / experience_tags
    # 时同样要走纯规则出处传播（如"太久了"命中 SESSION_TOO_LONG 缩
    # duration_hours 时标 user_stated）。
    refined = refined.model_copy(
        update={
            "field_provenance": _propagate_field_provenance(original, refined, feedback)
        }
    )
    if changed:
        note = "已基于反馈关键词做轻量调整（LLM 不可用，走规则化兜底）。"
    elif is_scenario:
        note = "（LLM 暂不可用）这像是换了新场景，已保留原约束并把新需求记进原话，建议重试一次。"
    else:
        note = "未识别可执行调整，已重新打散候选排序。"
    return RefinementOutput(
        refined_intent=refined,
        changed_fields=changed,
        refiner_note=note,
    )
