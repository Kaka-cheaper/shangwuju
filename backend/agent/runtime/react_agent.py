"""react_agent —— ReAct 单一 Agent 主体。

⚠ DEPRECATED（2026-05-31 spec planning-pipeline-consolidation R5）：
   V2 ReAct 路径仅在 USE_LANGGRAPH=0 且 USE_REACT_AGENT=1 时命中。当前默认
   USE_LANGGRAPH=1，chat.py 第 1 层即 return V3 LangGraph，本模块主体**不执行**。
   保留作 USE_LANGGRAPH=0 的 fallback，**不删**；新功能一律加到 graph/ 下，
   不要再扩展本文件。三路线全貌见 AGENTS.md §3.3.2。

让 LLM 看到全部 8 工具，自主决策何时调用哪个工具、是否输出行程或仅文字回话。

关键设计选择：
1. ``output_type = Union[ChatResponse, ItineraryResponse]``
   LLM 自己选输出形态：调工具完成完整规划 → ItineraryResponse；
   闲聊 / 元能力 / 拒答 / Q&A → ChatResponse。Pydantic AI 在生成最终回复时
   让 LLM 在两个分支里选一个 —— 不需要外部分类器。

2. 所有 8 工具用 ``@unified_agent.tool`` 装饰挂载，参数化展开（不传整个
   Input 模型）—— Pydantic AI 自动生成 OpenAI Function schema 时会让 LLM
   看到每个参数的 description（中文+英文括号双语，给 MiMo / GPT 都稳）。

3. 工具内部通过 ``ToolProvider`` 抽象解耦数据源（mock / gaode / dianping），
   ``observability.trace_span`` 包每个工具调用，链路全记录到 structlog；
   失败不抛异常，返回 ``{"success": False, "reason": ...}`` 让 LLM 在
   ReAct 循环中自己决策应对（empty_candidates → 放宽距离重试等）。

4. ``critics_v2.validate_itinerary`` 通过 ``@output_validator`` 接入：
   critical 违规 → ``ModelRetry`` → LLM 自纠错（LLM-Modulo 范式）。
   warning 不触发重做，避免 LLM 过度修复反而劣化方案。

5. ``retries=3`` 给 critic backprompt 留循环空间 —— 一次规划最多
   被 critic 拒 3 次，超出后框架抛 ``UnexpectedModelBehavior``。

不负责：
- LLM SDK 调用 / retry / 围栏剥离（Pydantic AI 框架层）
- SSE 事件推送（G agent 用 ``unified_agent.iter()`` 拦截工具调用）
- 业务约束的算法实现（在 ``critics_v2.py``）
- ToolProvider 切换工厂（在 ``tool_provider.py``）

import 关系（避免循环）：
- 本模块 → output_types / deps / model_factory / tool_provider / observability
- 本模块 → schemas/tools.py 仅作 Input/Output 字段类型参考
- ``critics_v2`` 用 try/import 延迟加载，让 F agent 还没合流时也能跑
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError, model_validator
from pydantic_ai import Agent, ModelRetry, RunContext

from agent.runtime.deps import AgentDeps
from agent.runtime.model_factory import create_model
from agent.runtime.observability import get_logger, trace_span
from agent.runtime.output_types import AgentOutput, ChatResponse, ItineraryResponse
from agent.runtime.tool_provider import ToolProvider, get_tool_provider

# 8 对工具 Input/Output 模型（仅用作字段类型/默认值参考；
# 工具签名是参数化展开的，不直接喂给 LLM）
from schemas.tags import (
    DIETARY_TAGS,
    EXPERIENCE_TAGS,
    PHYSICAL_TAGS,
    SOCIAL_CONTEXTS,
)
from schemas.tools import (
    BuyTicketInput,
    CheckRestaurantAvailabilityInput,
    EstimateRouteTimeInput,
    GenerateShareMessageInput,
    GetUserProfileInput,
    ReserveRestaurantInput,
    SearchPoisInput,
    SearchRestaurantsInput,
)


logger = get_logger("agent.v2.react")


# ============================================================
# 词典白名单过滤（防 LLM 漂值导致 Tool 输入校验失败）
# ============================================================
#
# 背景：schemas/tools.py 的 SearchPoisInput.social_context 类型是
# ``Optional[SocialContext]``，SocialContext 是 9 选 1 的 Literal。
# 即使 prompt 强约束了词典，LLM 偶尔仍会给 "家庭"/"family" 这类非词典值。
# Pydantic Literal 校验会抛 ValidationError，pydantic_ai 反馈给 LLM 自纠错；
# 但 LLM 可能死磕错的值，触发 ``tool_retries`` 用尽抛 UnexpectedModelBehavior。
#
# 修法：进入业务模型前，先过白名单 —— 非词典值自动 drop（tag）或置 None
# （social_context）。这是 Tool 入口的「容错防御」，不影响 prompt 强约束的
# 教育效果（critic / 上层校验仍按词典进行）。


def _filter_dict(values: list[str] | None, dictionary: frozenset[str]) -> list[str]:
    """白名单过滤：非词典词自动剔除；保持顺序去重。"""
    if not values:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if not isinstance(v, str):
            continue
        if v not in dictionary or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _filter_social_context(value: str | None) -> str | None:
    """social_context 白名单过滤：非 9 选 1 的值自动置 None。"""
    if not value or not isinstance(value, str):
        return None
    return value if value in SOCIAL_CONTEXTS else None


# ============================================================
# MiMo Function Calling 兼容层
# ============================================================
#
# 已知问题（pitfalls.md P2-2026-05-17 拟登记）：MiMo v2.5 Pro 在 OpenAI Function
# Calling 模式下，**会把数组参数序列化成 JSON 字符串**而不是真数组：
#     期望：physical_constraints: ["亲子友好", "适合老人"]
#     MiMo 实际：physical_constraints: "[\"亲子友好\", \"适合老人\"]"
# Pydantic AI 框架的 schema 校验会拒收（"Input should be a valid array"），
# LLM 看到反馈也理解不了根因，反复重试 3 次后被框架抛 UnexpectedModelBehavior。
#
# 修法：tool 入口处对所有 list 参数走 _coerce_list，自动把"JSON 字符串"还原成
# list；正常 list / None / 空字符串 直通。
#
# 由于 Pydantic AI 在框架层对参数 schema 校验，签名必须改成宽容类型（Any 或
# list | str | None），让框架放行后由我们自己解析。这里用 ``Any`` 最稳。


def _coerce_list(value: Any) -> list[Any] | None:
    """把"可能被 MiMo 误序列化的列表"还原成 list。

    - None / "" / [] → None（让上层用默认值）
    - 真 list → 直接返回
    - JSON 字符串 → json.loads；解析失败时回退为 [value]
    - 其他单值（int / str 等非空）→ [value]
    """
    import json

    if value is None:
        return None
    if isinstance(value, list):
        return value if value else None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # JSON 字符串形态："[...]"
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return parsed if parsed else None
            except (ValueError, TypeError):
                pass
        # 兜底：单值字符串当作 1 元素 list
        return [s]
    # 单值（int / float 等）当作 1 元素 list
    return [value]


def _coerce_int(value: Any) -> int | None:
    """把"可能被 MiMo 误序列化的整数"还原成 int。

    MiMo 偶尔把数字也序列化成字符串："3" → 3。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # bool 是 int 的子类，先排除
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            try:
                return int(float(s))
            except ValueError:
                return None
    return None


def _coerce_int_list(value: Any) -> list[int] | None:
    """把 list[int] 形参做 _coerce_list + 逐元素 _coerce_int。"""
    coerced = _coerce_list(value)
    if coerced is None:
        return None
    out: list[int] = []
    for item in coerced:
        i = _coerce_int(item)
        if i is not None:
            out.append(i)
    return out or None


# ============================================================
# MiMo nested object 兼容子类（仅给 Agent 当 output_type 之一用）
# ============================================================
#
# 已知问题：MiMo v2.5 Pro 在 Function Calling 下也会把 nested object 字段
# 序列化成 JSON 字符串，例如：
#     itinerary: "{\"summary\": ..., \"nodes\": [...]}"  ← 字符串！
# 应该是：
#     itinerary: {"summary": ..., "nodes": [...]}        ← object
#
# Pydantic 拒收会反馈给 LLM 自纠错，但 MiMo 顽固反复出错（实测 5 次重试都不改）。
# 修法：在 ItineraryResponse 的子类上加 ``@model_validator(mode="before")``，
# 把字符串型 itinerary 自动 json.loads 成 dict 后再走父类校验。
#
# 这个子类**仅作 Agent output_type**用；output_validator 内部会把它转回
# 标准 ItineraryResponse 供 G agent 消费，对外契约不变。
# 不动 backend/agent/v2/output_types.py（守"v2 已有文件不动"纪律）。


class _FlexibleItineraryResponse(ItineraryResponse):
    """ItineraryResponse 容错子类：MiMo 把 itinerary 字段输出为 JSON 字符串时自动解包。

    Pydantic v2 model_validator(mode="before") 在反序列化前先跑，把所有形态统一成
    dict 后再走父类的 strict 校验。不影响正常 dict 输入。
    """

    @model_validator(mode="before")
    @classmethod
    def _normalize_nested_objects(cls, data: Any) -> Any:
        """把可能被 LLM 误序列化为字符串的 nested 字段（itinerary）还原成 dict。"""
        if not isinstance(data, dict):
            return data

        # itinerary 可能被序列化成字符串
        itin = data.get("itinerary")
        if isinstance(itin, str):
            import json as _json

            stripped = itin.strip()
            if stripped.startswith("{"):
                try:
                    data["itinerary"] = _json.loads(stripped)
                except (ValueError, TypeError):
                    pass

        # itinerary.nodes / hops / orders 也可能被序列化成字符串（虽然 itinerary 自己 dict）
        itin_obj = data.get("itinerary")
        if isinstance(itin_obj, dict):
            for k in ("nodes", "hops", "schedule", "orders"):
                v = itin_obj.get(k)
                if isinstance(v, str):
                    import json as _json

                    stripped = v.strip()
                    if stripped.startswith("["):
                        try:
                            itin_obj[k] = _json.loads(stripped)
                        except (ValueError, TypeError):
                            pass

        return data


# Agent 实际 output_type：用 Flexible 替代 ItineraryResponse；
# output_validator 转回标准类型保证对外契约不变。
_AgentOutputFlexible = ChatResponse | _FlexibleItineraryResponse


# ============================================================
# 静态 instructions（模块级常量，避免每次 run 都拼字符串）
# ============================================================

_BASE_INSTRUCTIONS = """你是「晌午局」——本地半日下午出行管家（以下称「我」/「Agent」）。
你的工作是基于用户一句话需求，调用 8 个工具自主完成「想去哪、吃什么、怎么走、几点订位」整条链路，
最终输出 ChatResponse 或 ItineraryResponse 二选一。

【你的目标】
理解用户输入 → 决策是否调用工具 → 输出 ChatResponse 或 ItineraryResponse。

【决策原则（按顺序判断）】
1. 用户在闲聊 / 问候 / 自我介绍（"你好" / "你是谁"）→ 直接 ChatResponse 暖心回话，**不调任何工具**。
2. 用户问元能力（"你能做什么 / 你支持哪些场景 / 怎么用"）→ ChatResponse + suggestions（2-4 个引导短语）。
3. 用户问范围外（写代码 / 数学题 / 时事 / 八卦 / 翻译）→ ChatResponse 礼貌拒答 + 一句引导回主线。
4. 用户问 POI / 餐厅细节（"P004 适合 5 岁吗" / "R001 几点开门"）→ 调对应查询工具 → ChatResponse 把工具结果用人话回答。
5. 用户给完整规划需求（"今天下午想和老婆孩子出去玩，孩子 5 岁，老婆减肥"）→ 调多工具完成规划 → ItineraryResponse。
6. 用户在已有行程基础上反馈（"太远了 / 换近一点 / 不要那家餐厅"）→ 看 message_history 找上一轮 baseline → 调工具调整 → ItineraryResponse。

【8 工具表（你看到的就是 OpenAI Function 名）】
查询类（规划 / 答疑都会用）：
- get_user_profile         读用户家位置 / 默认预算 / 交通偏好；规划开始时**几乎必调**一次
- search_pois              按距离 + 物理 tag + 体验 tag + 同行年龄查活动地点候选
- search_restaurants       按距离 + 饮食 tag + 容量查餐厅候选
- check_restaurant_availability  查指定餐厅在 17:00 / 17:30 / 18:00 等时段是否可订
- estimate_route_time      估算 home/POI/餐厅之间的通勤时间

执行类（仅在用户明确「确认下单」时调，规划阶段**禁止调用**）：
- reserve_restaurant       下单餐厅
- buy_ticket               买景点门票
- generate_share_message   生成转发文案

【典型调用顺序（参考，非死板）】
1. get_user_profile（取 home_location）
2. search_pois（约束：距离 / physical / experience / social_context / age_in_party）
3. search_restaurants（约束：距离 / dietary / capacity）
4. 对前 1-3 家餐厅 × 17:00 / 17:30 / 18:00 调 check_restaurant_availability，命中即停
5. estimate_route_time × N（home→POI / POI→餐厅 / 餐厅→home，N 由实际节点数决定）
6. 输出 ItineraryResponse（中间节点 ≥ 1，按用户真实需求出节点数；首尾 home 由后端补 + 暖心 narration）

【失败 reason → 应对策略表（违反就是浪费 retry 额度）】
- empty_candidates        → 放宽 distance_max_km +2km 重试 1 次；仍空 → ChatResponse 解释「这附近 X 类候选不足」
- restaurant_full         → 同餐厅换 17:30 / 18:00；同店全满 → 切下一家 search_restaurants 候选
- ticket_sold_out         → 替换同类型 POI（亲子乐园 → 亲子博物馆等）
- distance_exceeded       → 删除附加活动，缩主活动距离
- duration_exceeded       → 删除附加活动，保留主活动 + 用餐
- not_found / upstream_failure  → 重试 1 次；仍失败 → ChatResponse 解释失败原因

【输出纪律（critic 会校验，违规会触发 ModelRetry）】
- 输出 ItineraryResponse 时：
  - itinerary.schema_version 固定为 "edge_v1"（系统会校验）
  - itinerary.nodes 首尾固定 home（target_kind="home" / duration_min=0），中间节点 ≥ 1
    （**不要写死 5 段**——可以是 1 个 mid node「只想吃饭」也可以是 3-4 个「家庭多停留」；
    按用户实际需求出节点数，别套模板）
  - 中间节点 target_kind ∈ {poi, restaurant}，按需要包含主活动 / 用餐 / 自由 等 kind 标签
  - itinerary.hops 长度恒等于 nodes - 1，每条 hop 含 minutes / mode / path_type
    （由系统按 routes.json 自动算，LLM 输出时如不确定可置 mode="taxi" / path_type="estimated"，
    后端 assemble 会重算覆盖）
  - 时间轴单调递增，相邻节点不重叠（容差 ±5 分钟）
  - 总时长在用户 duration_hours 范围内（容差 ±30 分钟）
  - itinerary.orders **必须为空数组 []**（规划阶段不假装下单；下单只能由用户确认后再调 reserve_restaurant / buy_ticket）
  - narration 80-200 字，暖语气，称呼「你」（不用「您」「用户」），含主活动 + 用餐 + 邀请反馈
- 输出 ChatResponse 时：
  - text 必须中文，1-200 字最佳，语气随场景：闲聊 warm、元能力 neutral、拒答 playful、共情 empathetic
  - suggestions 可选，每条 ≤ 24 字中文短语；闲聊 / 元能力 / 拒答时建议给 2-3 个

【中文词典强约束（关键 · 调 search_pois / search_restaurants 时务必遵守）】
调用 search_pois 的 ``physical_constraints`` / ``experience_tags`` 参数、
search_restaurants 的 ``dietary_constraints`` 参数，**只能从中文词典选词**：
- physical：「亲子友好/适合 5-10 岁/适合青少年/适合老人/无台阶/可休息/无障碍/高强度/低强度」
- dietary ：「低脂/健康轻食/高蛋白/日料/粤菜/不辣/无牛肉/有儿童餐/高人均/有包间/软烂/下午茶/甜品」
- experience：「拍照友好/网红打卡/安静聊天/热闹/社交/独处舒缓/商务体面/礼仪感/亲密情侣/学习成长/看展/室内/户外」
- social_context（9 选 1）：「家庭日常/老人伴助/闺蜜聊天/朋友热闹/情侣亲密/商务接待/同学重聚/独处放空/纪念日仪式感」

**绝对禁止**输出英文（"family" / "healthy" / "low-fat" / "business" / "kid-friendly" 等）、拼音、或自创同义词
（如「亲子」必须写成「亲子友好」；「健康饮食」必须写成「健康轻食」；「老人友好」必须写成「适合老人」）。
词典不命中的约束**直接不传**（让该参数为空数组），不要发明词。

【few-shot：5 个典型场景速查】

S1（家庭主线）：
  user="今天下午想和老婆孩子出去玩，孩子 5 岁，老婆减肥"
  你的动作：调 get_user_profile → search_pois(physical=["亲子友好","适合 5-10 岁"], age_in_party=[5])
  → search_restaurants(dietary=["低脂","健康轻食"]) → check_restaurant_availability ×2-3
  → estimate_route_time ×N → 输出 ItineraryResponse（中间节点 2-3 个 poi/restaurant，
    narration 含「老婆」「孩子」）

S7（独处）：
  user="这周加班加得想吐，下午想一个人安安静静待几个小时再回家"
  你的动作：调 get_user_profile → search_pois(experience=["独处舒缓","安静聊天"], social_context="独处放空")
  → search_restaurants(dietary=[]) 或跳过用餐 → estimate_route_time ×N
  → 输出 ItineraryResponse（party_size=1，narration 暖语气共情）

S6（商务）：
  user="下午临时被叫去接外地客户，对方是商务人士"
  你的动作：调 get_user_profile → search_pois(experience=["商务体面","礼仪感"]) → search_restaurants(dietary=["高人均","有包间"], require_private_room=True)
  → check_restaurant_availability ×2-3 → estimate_route_time ×3 → ItineraryResponse

拒答 / 范围外：
  user="5+5 等于几"
  你的动作：**不调任何工具** → ChatResponse(text="这个我帮不上忙呢～不过下午局规划是我的强项，要不让我帮你安排一下？")

元能力问答：
  user="你能做什么"
  你的动作：**不调任何工具** → ChatResponse(text="我是「晌午局」——下午半日出行管家。一句话告诉我想做什么，
  我帮你串好「去哪、吃啥、怎么走、几点订位」整条链路。", suggestions=["带娃放电","一个人放空","商务接待"])

【硬性禁止】
- ❌ 不在 node.note 写"已为你预约"——预约只能由 reserve_restaurant 真返回 success=true 后由后端追加 orders；
  规划阶段 orders=[] 是硬条款。
- ❌ 不发明 Tool 名（不在上面 8 工具列表里的一律不存在）。
- ❌ 不在 search_pois / search_restaurants 参数里写英文 tag。
- ❌ 不要写 ``if scene_type == "family"`` 这种伪代码（D9 硬条款）；用约束参数描述用户。
- ❌ 不要把节点数写死成 5（家庭多停留可以 3-4 个，单段「只想吃饭」可以 1 个；按用户意图出节点数）。
- ❌ 闲聊 / 元能力 / 拒答时**不要调任何工具**（浪费 token + 拉慢首字节）。

【输出格式（OpenAI Function Calling 关键 · 防 list-as-string Bug）】
当你输出 ItineraryResponse 时，OpenAI Function 调用的参数体里：
- ``itinerary.nodes`` / ``itinerary.hops`` / ``itinerary.schedule`` **必须**是真 JSON 数组
  ``[{"node_id":"n0","target_kind":"home",...}, ...]``，
  **绝不能**是 JSON 字符串 ``"[{\"node_id\":\"n0\"...}]"``。
- ``itinerary.orders`` 同上，必须是真数组（规划阶段用 ``[]``）。
- 调 search_pois / search_restaurants 时 ``physical_constraints`` / ``dietary_constraints``
  / ``experience_tags`` / ``age_in_party`` 也**必须**是真数组：
  ✓ ``physical_constraints: ["亲子友好","适合 5-10 岁"]``
  ✗ ``physical_constraints: "[\"亲子友好\",\"适合 5-10 岁\"]"``（这是字符串，不是数组）。
- 同理整数参数 ``capacity_requirement`` / ``party_size`` / ``quantity`` 必须是真整数 `3` 而不是字符串 `"3"`。"""


# ============================================================
# Agent 实例（模块级单例）
# ============================================================

unified_agent: Agent[AgentDeps, AgentOutput] = Agent(
    model=create_model(),
    deps_type=AgentDeps,
    output_type=_AgentOutputFlexible,  # type: ignore[arg-type]
    instructions=_BASE_INSTRUCTIONS,
    retries=3,
    # output 校验失败给 5 次额度（MiMo 偶尔会把 list 字段序列化成 JSON 字符串，
    # 多给它机会自纠正；不影响 tool_retries 仍走 retries=3 的 cascade 默认）
    output_retries=5,
)


@unified_agent.instructions
def _bind_user_context(ctx: RunContext[AgentDeps]) -> str:
    """动态追加 per-turn 上下文（user_id / session_id / planner_mode）。

    Pydantic AI 会把本函数返回的字符串拼到 _BASE_INSTRUCTIONS 之后；这样静态
    部分（5000+ 字的规则）只构造一次，每轮只加 ≤ 100 字的会话上下文。
    """
    return (
        f"\n【当前会话上下文】\n"
        f"- user_id：{ctx.deps.user_id}\n"
        f"- session_id：{ctx.deps.session_id or '(未设置)'}\n"
        f"- planner_mode：{ctx.deps.planner_mode}（仅作日志标记，不影响你的决策）\n"
        f"\n如果 message_history 含上一轮的 ItineraryResponse，本轮属于反馈/调整路径——\n"
        f"先理解用户反馈在指上一轮的哪一段，再调工具调整。"
    )


# ============================================================
# 工具挂载 —— 8 个工具参数化展开
# ============================================================

# 注：所有工具都返回 dict（model_dump），让 LLM 在 ReAct 中看到结构化结果；
# 失败时同样返 dict（success=False + reason），不抛异常。


@unified_agent.tool
async def get_user_profile(
    ctx: RunContext[AgentDeps],
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """读用户画像（家位置 / 默认预算 / 交通偏好）。

    Args:
        user_id: 目标用户 id；缺省时用 deps.user_id（demo_user 兜底）

    Returns:
        ``{"success": bool, "profile": {...} | None, "reason": str | None}``
    """
    provider: ToolProvider = get_tool_provider()
    inp = GetUserProfileInput(user_id=user_id or ctx.deps.user_id or "demo_user")
    with trace_span("tool.get_user_profile", user_id=inp.user_id):
        out = await provider.get_user_profile(inp)
    return out.model_dump()


@unified_agent.tool
async def search_pois(
    ctx: RunContext[AgentDeps],
    distance_max_km: float = 5.0,
    physical_constraints: Any = None,
    experience_tags: Any = None,
    social_context: Optional[str] = None,
    age_in_party: Any = None,
    preferred_types: Any = None,
    limit: int = 10,
) -> dict[str, Any]:
    """按距离 + 物理约束 + 体验标签 + 同行年龄查询活动地点候选。

    Args:
        distance_max_km: 距家最远公里数（默认 5）；放宽距离时 +2km 重试用此参数
        physical_constraints: 物理约束 tag 列表，**只能**从中文词典选词
            （如 ["亲子友好"] / ["适合老人","无台阶"]）
        experience_tags: 体验偏好 tag 列表，**只能**从中文词典选词
            （如 ["独处舒缓"] / ["商务体面"]）
        social_context: 社交上下文（9 选 1 中文，如 "家庭日常" / "独处放空"）
        age_in_party: 同行人年龄列表（如 [5]）；亲子场景过滤 age_range 用
        preferred_types: 用户明示的 POI 类型（如 ["展览","美术馆"]）
        limit: 返回候选数上限（默认 10，最多 50）

    Returns:
        ``{"success": bool, "candidates": [...], "reason": str | None}``
        失败 reason ∈ {empty_candidates, distance_exceeded, upstream_failure, not_found}
    """
    pc_raw = _coerce_list(physical_constraints)
    et_raw = _coerce_list(experience_tags)
    pt_raw = _coerce_list(preferred_types)
    age_raw = _coerce_int_list(age_in_party)
    limit_raw = _coerce_int(limit) or 10
    logger.info(
        "tool.search_pois.invoked",
        distance_max_km=distance_max_km,
        physical=pc_raw,
        experience=et_raw,
        social=social_context,
        age=age_raw,
        types=pt_raw,
    )
    provider = get_tool_provider()
    pc_filtered = _filter_dict(pc_raw, PHYSICAL_TAGS)
    et_filtered = _filter_dict(et_raw, EXPERIENCE_TAGS)
    sc_filtered = _filter_social_context(social_context)
    if (pc_raw and len(pc_filtered) != len(pc_raw)) or \
       (et_raw and len(et_filtered) != len(et_raw)) or \
       (social_context and sc_filtered is None):
        logger.warning(
            "tool.search_pois.dict_filter",
            physical_in=pc_raw,
            physical_out=pc_filtered,
            experience_in=et_raw,
            experience_out=et_filtered,
            social_in=social_context,
            social_out=sc_filtered,
        )
    try:
        inp = SearchPoisInput(
            distance_max_km=distance_max_km,
            physical_constraints=pc_filtered,  # type: ignore[arg-type]
            experience_tags=et_filtered,  # type: ignore[arg-type]
            social_context=sc_filtered,  # type: ignore[arg-type]
            age_in_party=age_raw,
            preferred_types=pt_raw or [],
            limit=limit_raw,
        )
    except ValidationError as e:
        logger.warning("tool.search_pois.validation_error", errors=e.errors())
        return {
            "success": False,
            "reason": "invalid_input",
            "candidates": [],
            "message": (
                "search_pois 参数校验失败：" + "; ".join(
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                )[:300]
            ),
        }
    with trace_span(
        "tool.search_pois",
        distance=distance_max_km,
        physical_n=len(inp.physical_constraints),
        experience_n=len(inp.experience_tags),
    ):
        out = await provider.search_pois(inp)
    return out.model_dump()


@unified_agent.tool
async def search_restaurants(
    ctx: RunContext[AgentDeps],
    distance_max_km: float = 5.0,
    dietary_constraints: Any = None,
    experience_tags: Any = None,
    social_context: Optional[str] = None,
    capacity_requirement: Any = None,
    require_private_room: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    """按距离 + 饮食 tag + 容量查餐厅候选。

    Args:
        distance_max_km: 距家最远公里数（默认 5）
        dietary_constraints: 饮食 tag 列表，**只能**从中文词典选词
            （如 ["低脂","健康轻食"] / ["粤菜","有包间"]）
        experience_tags: 体验偏好 tag（如 ["商务体面"]）
        social_context: 社交上下文（9 选 1 中文）
        capacity_requirement: 同行 ≥4 人时按桌型过滤（如 6 人）
        require_private_room: 是否必须有包间（商务场景常用）
        limit: 返回候选数上限（默认 10）

    Returns:
        ``{"success": bool, "candidates": [...], "reason": str | None}``
    """
    dc_raw = _coerce_list(dietary_constraints)
    et_raw = _coerce_list(experience_tags)
    cap_raw = _coerce_int(capacity_requirement)
    limit_raw = _coerce_int(limit) or 10
    provider = get_tool_provider()
    try:
        inp = SearchRestaurantsInput(
            distance_max_km=distance_max_km,
            dietary_constraints=_filter_dict(dc_raw, DIETARY_TAGS),  # type: ignore[arg-type]
            experience_tags=_filter_dict(et_raw, EXPERIENCE_TAGS),  # type: ignore[arg-type]
            social_context=_filter_social_context(social_context),  # type: ignore[arg-type]
            capacity_requirement=cap_raw,
            require_private_room=bool(require_private_room),
            limit=limit_raw,
        )
    except ValidationError as e:
        logger.warning("tool.search_restaurants.validation_error", errors=e.errors())
        return {
            "success": False,
            "reason": "invalid_input",
            "candidates": [],
            "message": (
                "search_restaurants 参数校验失败：" + "; ".join(
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                )[:300]
            ),
        }
    with trace_span(
        "tool.search_restaurants",
        distance=distance_max_km,
        dietary_n=len(inp.dietary_constraints),
        capacity=cap_raw,
    ):
        out = await provider.search_restaurants(inp)
    return out.model_dump()


@unified_agent.tool
async def check_restaurant_availability(
    ctx: RunContext[AgentDeps],
    restaurant_id: str,
    time: str,
    party_size: Any = 2,
) -> dict[str, Any]:
    """查指定餐厅在指定时段是否可订。

    Args:
        restaurant_id: 餐厅 id（如 "R001"）；从 search_restaurants 返回的 candidates 里取
        time: 检查时段，HH:MM 格式（典型尝试 "17:00" / "17:30" / "18:00"）
        party_size: 用餐人数（默认 2）

    Returns:
        ``{"success": bool, "available": bool, "queue_minutes": int,
            "suggested_alternative_time": str | None, "reason": str | None}``
        reason 常见值：restaurant_full（满座；可换 suggested_alternative_time 重试）
    """
    provider = get_tool_provider()
    ps_raw = _coerce_int(party_size) or 2
    inp = CheckRestaurantAvailabilityInput(
        restaurant_id=restaurant_id, time=time, party_size=ps_raw
    )
    with trace_span(
        "tool.check_restaurant_availability",
        restaurant_id=restaurant_id,
        time=time,
        party_size=ps_raw,
    ):
        out = await provider.check_restaurant_availability(inp)
    return out.model_dump()


@unified_agent.tool
async def estimate_route_time(
    ctx: RunContext[AgentDeps],
    from_location: str,
    to_location: str,
) -> dict[str, Any]:
    """估算两点通勤时间。

    Args:
        from_location: 起点 —— "home" 或 POI/餐厅 id（如 "P004"）
        to_location: 终点 —— "home" 或 POI/餐厅 id

    Returns:
        ``{"success": bool, "route": {"distance_km": ..., "duration_min": ...} | None,
            "reason": str | None}``
    """
    provider = get_tool_provider()
    inp = EstimateRouteTimeInput(from_location=from_location, to_location=to_location)
    with trace_span(
        "tool.estimate_route_time",
        from_loc=from_location,
        to_loc=to_location,
    ):
        out = await provider.estimate_route_time(inp)
    return out.model_dump()


@unified_agent.tool
async def reserve_restaurant(
    ctx: RunContext[AgentDeps],
    restaurant_id: str,
    time: str,
    party_size: Any,
    extra_notes: Optional[str] = None,
) -> dict[str, Any]:
    """**执行类**——下单餐厅。仅在用户明确「确认预约」后调用，规划阶段禁用。

    Args:
        restaurant_id: 餐厅 id
        time: 用餐时段（HH:MM）
        party_size: 用餐人数
        extra_notes: 备注（如 "需要儿童椅" / "靠窗"）

    Returns:
        ``{"success": bool, "order_id": str | None, "restaurant_id": str,
            "confirmed_time": str | None, "confirmed_party_size": int | None,
            "reason": str | None}``
    """
    provider = get_tool_provider()
    ps_raw = _coerce_int(party_size) or 1
    inp = ReserveRestaurantInput(
        restaurant_id=restaurant_id,
        time=time,
        party_size=ps_raw,
        extra_notes=extra_notes,
    )
    with trace_span(
        "tool.reserve_restaurant",
        restaurant_id=restaurant_id,
        time=time,
        party_size=ps_raw,
    ):
        out = await provider.reserve_restaurant(inp)
    return out.model_dump()


@unified_agent.tool
async def buy_ticket(
    ctx: RunContext[AgentDeps],
    poi_id: str,
    quantity: Any = 1,
    visitor_ages: Any = None,
) -> dict[str, Any]:
    """**执行类**——买景点门票。仅在用户明确「确认下单」后调用，规划阶段禁用。

    Args:
        poi_id: POI id
        quantity: 票数（默认 1）
        visitor_ages: 游客年龄列表（用于亲子半价等门票分类）

    Returns:
        ``{"success": bool, "order_id": str | None, "poi_id": str,
            "quantity": int | None, "total_price": float | None,
            "reason": str | None}``
        失败 reason 常见值：ticket_sold_out
    """
    provider = get_tool_provider()
    qty = _coerce_int(quantity) or 1
    ages = _coerce_int_list(visitor_ages)
    inp = BuyTicketInput(poi_id=poi_id, quantity=qty, visitor_ages=ages)
    with trace_span("tool.buy_ticket", poi_id=poi_id, quantity=qty):
        out = await provider.buy_ticket(inp)
    return out.model_dump()


@unified_agent.tool
async def generate_share_message(
    ctx: RunContext[AgentDeps],
    itinerary_summary: str,
    social_context: str,
    audience: Optional[str] = None,
) -> dict[str, Any]:
    """**执行类**——为已确认的行程生成可一键复制的转发文案。

    Args:
        itinerary_summary: 行程摘要（如 "下午 14:00-19:30 · 森林乐园 → 健康轻食"）
        social_context: 社交上下文（9 选 1 中文，决定文案调性）
        audience: 转发对象（如 "妻子" / "朋友群" / "客户"）

    Returns:
        ``{"success": bool, "message": str | None, "reason": str | None}``
    """
    provider = get_tool_provider()
    safe_ctx = _filter_social_context(social_context)
    if safe_ctx is None:
        return {
            "success": False,
            "reason": "invalid_input",
            "message": (
                f"social_context={social_context!r} 不在 9 选 1 词典中："
                f"{sorted(SOCIAL_CONTEXTS)}；请改用合法值"
            ),
        }
    inp = GenerateShareMessageInput(
        itinerary_summary=itinerary_summary,
        social_context=safe_ctx,  # type: ignore[arg-type]
        audience=audience,
    )
    with trace_span(
        "tool.generate_share_message",
        social_context=social_context,
        audience=audience,
    ):
        out = await provider.generate_share_message(inp)
    return out.model_dump()


# ============================================================
# critic backprompt（output_validator）
# ============================================================

@unified_agent.output_validator
def _validate_output(
    ctx: RunContext[AgentDeps], output: Any
) -> AgentOutput:
    """对 ItineraryResponse 跑 critic，critical 违规 → ModelRetry。

    - ChatResponse：无业务约束，直接放行
    - _FlexibleItineraryResponse：先转回标准 ItineraryResponse（保契约），
      然后跑 ``critics_v2.validate_itinerary``。critical 违规拼成中文修复指令
      抛 ``ModelRetry``，触发 LLM 自纠错；warning 不触发重做（避免过度修复）

    F agent 的 ``critics_v2`` 还没合流时，本函数静默放行（不阻塞主链路）。
    """
    if isinstance(output, ChatResponse):
        return output

    # _FlexibleItineraryResponse 转回标准 ItineraryResponse
    if isinstance(output, _FlexibleItineraryResponse):
        # 转换：dump → 重新 validate 成 ItineraryResponse 标准形态
        # （extra="forbid" 配合 ConfigDict，dump 不会带额外字段）
        try:
            output = ItineraryResponse.model_validate(output.model_dump())
        except ValidationError as e:
            logger.warning(
                "output_validator.itinerary_normalize_failed", error=str(e)[:300]
            )
            # 标准化失败仍传 Flexible 给下游 critic 走（model_dump 可用）
    if not isinstance(output, ItineraryResponse):
        # 类型异常，保险起见放行
        return output  # type: ignore[return-value]

    # F agent 的 critics_v2 兜底导入
    try:
        from agent.planning.critic.critics_v2 import (
            Severity,
            format_violations_for_llm,
            validate_itinerary,
        )
    except ImportError:
        logger.info("critic.skipped", reason="critics_v2_not_available")
        return output

    # 拿 intent_snapshot —— G agent 在 deps.extra 里塞，没塞就跳过 critic
    intent_raw = ctx.deps.extra.get("intent_snapshot")
    if intent_raw is None:
        logger.info("critic.skipped", reason="no_intent_snapshot")
        return output

    # 兼容 dict / IntentExtraction 两种形态
    try:
        from schemas import IntentExtraction
    except ImportError:
        return output

    if isinstance(intent_raw, dict):
        try:
            intent = IntentExtraction.model_validate(intent_raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("critic.intent_validate_failed", error=str(e))
            return output
    elif isinstance(intent_raw, IntentExtraction):
        intent = intent_raw
    else:
        logger.warning(
            "critic.intent_unknown_type", type=type(intent_raw).__name__
        )
        return output

    violations = validate_itinerary(output.itinerary, intent)
    critical = [v for v in violations if v.severity == Severity.CRITICAL]

    if critical:
        msg = format_violations_for_llm(critical)
        logger.warning(
            "critic.retry",
            violations_total=len(violations),
            critical=len(critical),
            codes=[v.code.value for v in critical],
        )
        raise ModelRetry(msg)

    logger.info(
        "critic.pass",
        violations_total=len(violations),
        warnings=len([v for v in violations if v.severity == Severity.WARNING]),
    )
    return output


# ============================================================
# 公共入口
# ============================================================

async def run_react_turn_inner(
    message: str,
    *,
    deps: AgentDeps,
    message_history: Optional[list[Any]] = None,
) -> Any:
    """跑一次完整 ReAct 循环，返回 Pydantic AI 原生 ``AgentRunResult``。

    G agent 会用 ``unified_agent.iter()`` 来流式包装本函数同等逻辑（便于
    把工具调用拦截后推 SSE）；本函数仅给单元测试 + 同步路径用。

    Args:
        message: 用户本轮输入
        deps: AgentDeps 容器（user_id / session_id / planner_mode / tracer / extra）
        message_history: 历史消息列表（``ModelMessage`` 序列）；
            首轮可传 None / []，反馈轮传上一轮 ``result.all_messages()``

    Returns:
        ``AgentRunResult[AgentOutput]``——通过 ``.output`` 拿 ChatResponse
        或 ItineraryResponse；通过 ``.all_messages()`` 拿历史用于下一轮。
    """
    return await unified_agent.run(
        message,
        deps=deps,
        message_history=message_history or [],
    )


__all__ = [
    "unified_agent",
    "run_react_turn_inner",
]
