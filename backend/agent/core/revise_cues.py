"""agent.core.revise_cues —— 「明说要改方案」祈使词判定（中立模块，无归属方偏向）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
沿革（晌午局对话轮路由规则层重构，2026-07-12）：

  `looks_like_explicit_revise` 原定义在 `agent.core.soft_constraint_sniffer`——
  历史上它是软约束嗅探器用来区分「提约束·没说改」（主动问气泡）vs「提约束·
  明说改」（直接重规划）的判据。但它被另外两个模块跨界消费：
    - `agent.core.dialogue_acts`（booking/confirm 排除"明说改"误判）
    - `agent.core.itinerary_qa`（QA 排除"帮我换成…吗"这类疑问式改请求）
  三个消费方本该平权，却被迫都 import 一个"名义上属于软约束模块"的函数——
  当软约束嗅探器的**路由角色**被删除（本批：过约束"提了但没说改"的场景改由
  路由脑子少样本承接，见 `agent/routing/brain_prompt.py`），若不先把这个判据
  挪出来，删除动作会连带砸掉 dialogue_acts.py / itinerary_qa.py 的两处 import。

  故本模块独立出来，职责单一：只做"这句话是不是明确的祈使替换/删除/重做"
  的字面判定，不归属于任何一个具体的对话行为模块，供三方平权引用。

不负责：
  - 提约束的语义识别（那是已删除的软约束嗅探器路由角色的历史范围，见
    ADR-0011 E-2-c 之后的 route_turn.py 级联收口说明）；
  - 反馈/确认/预约/提问的判定（各自模块的事）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

# 明确的"动手改方案"祈使词。命中 → 用户明说要改，应直接走 feedback/refiner
# 重新规划，不该被"主动问一句"的气泡二次拦截。
_EXPLICIT_REVISE_KEYWORDS: tuple[str, ...] = (
    "换成", "改成", "换个", "换一个", "帮我换", "帮我改", "给我换",
    "去掉", "重新规划", "重新安排", "重排", "重新来", "重做", "调整成",
)


def looks_like_explicit_revise(text: str) -> bool:
    """这句是不是「明确要求改方案」（含祈使替换词）。

    三方消费者共同的排除闸：
    - dialogue_acts.looks_like_booking / looks_like_confirm：明说改的话不该
      被误判成预约/确认；
    - itinerary_qa.answer_itinerary_question：疑问式改请求（"帮我换成…吗"）
      不该被当成提问接地作答。
    """
    if not text:
        return False
    return any(k in text for k in _EXPLICIT_REVISE_KEYWORDS)


__all__ = ["looks_like_explicit_revise"]
