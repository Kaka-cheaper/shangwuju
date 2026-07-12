"""nodes.intent —— 意图抽取节点。

复用 backend/agent/intent_parser.py 的 parse_intent —— 它已含 persona prior 注入。

输入：state["user_input"]
输出：
- state["intent"] = IntentExtraction
- state["quality_issues"]：spec execution-quality-review R1 词典外社交意图检测
  当 raw_input 含明确社交关键词（老师 / 客户 / 宠物 / 同事 / 网友 等不在 9 词典内）
  但 social_context 已被 LLM 强行映射成 9 选 1 时，写入降级文案让 narrator 主动质疑。
  设计纪律：仅在「关键词命中且与 social_context 语义偏差大」时触发，避免误伤。

【ADR-0012 决策 4：字段生命周期表】新需求 = 新规划事件的一种触发方式（另一种是
refiner_node 的反馈路径），两者共用 agent.graph.state.reset_for_new_episode() 生成
的同一份 EPISODE_SCOPED 重置 diff——防止会话中期新需求（intent 路径）把上一次规划
事件残留的 itinerary/critic_feedback_text/advisories 等漏进这一次全新规划（ADR-0012
背景 5：今天靠 route_turn.py 的兜底归并把这条路径掩护成"会话中期不可达"，归并删除后
必须由这里的重置接住）。合并顺序：reset diff 先铺底，本节点自己的业务输出
（intent / quality_issues）后覆盖，绝不能让 reset 把本轮刚解析的 intent 冲掉。
quality_issues 因此必须是"本轮从零开始算"，不能从 incoming state 的旧值累加
（旧值可能是上一次规划事件留下的，若从它累加会让 reset 铺的干净底失去意义）。
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState, reset_for_new_episode
from agent.intent.parser import IntentParseError, parse_intent
from agent.intent.prompts.router_prompt import FLOOR_REPLAN_SEND
from agent.core.llm_client import get_llm_client
from schemas.intent import IntentExtraction


# spec execution-quality-review R1：词典外社交关键词 → 推断的最接近 social_context 9 选 1
# 当 raw_input 含 key 但 social_context 不在 fits 集合时，触发降级文案
_OUT_OF_VOCAB_SOCIAL_KEYWORDS: dict[str, set[str]] = {
    "老师": {"商务接待", "同学重聚", "朋友热闹"},
    "客户": {"商务接待"},
    "宠物": {"独处放空", "家庭日常"},  # 宠物伴随通常是独处或全家
    "狗子": {"独处放空", "家庭日常"},
    "同事": {"商务接待", "朋友热闹"},
    "网友": {"朋友热闹", "闺蜜聊天"},
    "导师": {"商务接待", "同学重聚"},
    "前辈": {"商务接待", "同学重聚"},
    "邻居": {"朋友热闹", "家庭日常"},
}


def _detect_out_of_vocab_social(raw_input: str, social_context: str) -> str | None:
    """检测词典外社交关键词与抽取的 social_context 是否语义偏差大。

    返回降级文案（中文）；None 表示无偏差不触发。
    """
    if not raw_input or not social_context:
        return None

    text = raw_input.lower()
    for keyword, fits in _OUT_OF_VOCAB_SOCIAL_KEYWORDS.items():
        if keyword in text or keyword in raw_input:
            # 命中关键词
            if social_context in fits:
                # social_context 已经在合理映射集合里，不质疑
                continue
            # 关键词命中但 social_context 偏离 → 触发降级文案
            return (
                f"我把您说的「{keyword}」理解为「{social_context}」场景，"
                f"如果不太合适，您可以说「换成 X 场景」让我重新规划"
            )
    return None


def _build_fallback_intent(user_input: str) -> IntentExtraction:
    """意图解析彻底失败时的兜底意图（保 demo 不崩）。

    设计：用最保守的默认值——空同行人 / 空词典字段 / 默认家庭日常场景 /
    宽松距离时长——让下游 search/blueprint 仍能出一个通用方案，
    raw_input 保留用户原话供 narrator 引用。低 parse_confidence 标记不确定。
    """
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input=user_input,
        parse_confidence=0.3,
        ambiguous_fields=["all"],
    )


def intent_node(state: AgentState) -> dict[str, Any]:
    client = get_llm_client()
    user_input = state.get("user_input") or ""
    user_id = state.get("user_id") or "demo_user"
    # 读写分离批：user_id=画像模板键（共享只读），session_id=累积键（会话私有
    # 偏好先验 + 行程档案召回）——两把钥匙都从图 state 取，房间路径的持久线程
    # （collab_{room_id}）与单人会话同构，无需分叉。
    session_id = state.get("session_id")

    # E-1 缺口修复(ADR-0011 落地状态节有案):canonical「重新规划一个」这五个字
    # 不含任何需求要素,语义=「重做我的需求」——复用上一事件 intent 的 raw_input
    # 重解。读旧 intent 发生在本函数尾部 reset_for_new_episode() 铺底之前,此刻
    # 仍可读;无上一事件时原样解析(该 chip 只在有方案时由地板发出,防御分支)。
    if user_input == FLOOR_REPLAN_SEND:
        prev_intent = state.get("intent")
        prev_raw = getattr(prev_intent, "raw_input", "") if prev_intent else ""
        if prev_raw:
            user_input = prev_raw

    # 韧性修复：LLM 偶发返回非法 JSON → parse_intent 重试耗尽抛 IntentParseError。
    # 旧行为：异常冒泡到 graph 流 → stream_error → demo 崩（评委看到红色错误）。
    # 新行为：捕获后用兜底意图继续跑，并写 quality_issue 让 narrator 诚实告知。
    fallback_used = False
    try:
        # max_retries=2（共 3 次机会）：LLM 偶发 JSON 错是瞬态，多给一次重试
        # 显著降低落到兜底意图的概率（兜底是降级体验，能避则避）
        intent = parse_intent(
            user_input,
            client=client,
            user_id=user_id,
            session_id=session_id,
            max_retries=2,
        )
    except IntentParseError as e:
        import logging as _logging

        _logging.getLogger("agent.graph.intent").warning(
            "intent_parse_failed_fallback: %s（raw_input=%r）", e.reason, user_input[:60]
        )
        intent = _build_fallback_intent(user_input)
        fallback_used = True
    except Exception:  # noqa: BLE001
        # D2：parse_intent 抛【非】IntentParseError（如 LLM 客户端 / 依赖的非预期错）→
        # 也用兜底意图继续这一轮，绝不让裸异常冒泡成 STREAM_ERROR。loudly 落完整 traceback
        # （degrade, don't go silent），再复用与 IntentParseError 同款的兜底意图 + quality_issue。
        import logging as _logging

        _logging.getLogger("agent.graph.intent").exception(
            "intent_parse_unexpected_fallback（raw_input=%r）", user_input[:60]
        )
        intent = _build_fallback_intent(user_input)
        fallback_used = True

    # 房间人数地板（协作房间注入，2026-07-12）：用户没明说人数时，把
    # capacity_requirement 兜到房间在场人数——一处生效全链路（搜餐容量过滤 /
    # execute_finalize 预约头数 / critic 校验同源读它）。max 保证 LLM 已明说的更大
    # 值不被拉低；单人路径 floor=0，max(x,0)=x 零影响。反馈重排走 resume（本节点
    # 不重跑），那条路径的 floor 在 room.py 侧对精炼后 intent 另做。
    _floor = state.get("party_size_floor") or 0
    if _floor > 0:
        intent = intent.model_copy(
            update={"capacity_requirement": max(intent.capacity_requirement or 0, _floor)}
        )

    # spec execution-quality-review R1：词典外社交意图降级文案
    # 本轮从零开始算（ADR-0012 决策 4）：intent_node 是 quality_issues 每个规划事件
    # 唯一的写手，不从 incoming state 的旧值累加——旧值可能是上一次规划事件（甚至
    # 更早、中间隔了几轮 chitchat）留下的，累加会让下面 reset_for_new_episode() 铺
    # 的干净底失去意义。
    out: dict[str, Any] = {"intent": intent}
    issues: list[str] = []

    if fallback_used:
        issues.append(
            "我没完全听懂你的需求，先按通用下午行程帮你安排了，"
            "你可以再说一遍或换种说法，我重新规划。"
        )

    warning = _detect_out_of_vocab_social(
        user_input, getattr(intent, "social_context", "") or ""
    )
    if warning:
        issues.append(warning)

    if issues:
        out["quality_issues"] = issues

    # 重置部分（EPISODE_SCOPED 全集）先铺底，业务输出（intent / quality_issues）
    # 后覆盖——见模块 docstring。首轮（make_initial_state 从没写过这批键）时这一步
    # 等价 no-op，见 test_state_lifecycle.py。
    return {**reset_for_new_episode(), **out}
