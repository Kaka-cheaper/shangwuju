"""agent.refiner —— 用户拒绝方案 + 反馈 → 调整后的 IntentExtraction。

业务故事见 schemas/refine.py 顶部 docstring。

实现策略：
- LLM 调用 1 次，response_format=json_object
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

from schemas.intent import IntentExtraction
from schemas.refine import RefinementOutput

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

    refined_intent = IntentExtraction.model_validate(refined_intent_data)

    raw_changed = list(payload.get("changed_fields", []) or [])
    # 问题 11 修复：LLM 可能在 changed_fields 里说改了时长，但 refined_intent.duration_hours
    # 字段没真改。强制对齐反馈里的具体小时数。
    refined_intent, fixed_changed = _enforce_duration_consistency(
        refined_intent, raw_changed, feedback_text
    )

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
