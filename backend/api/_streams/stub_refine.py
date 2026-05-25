"""_stub_refine + _refine_stream + _extract_distance_km —— refine 路径 stub 实现。

A 同学未实现 backend.agent.refiner 时的兜底；A commit 后 _refine_stream 自动切真版。
"""

from __future__ import annotations

import re
from typing import Any, AsyncIterator, Optional

from schemas import (
    IntentExtraction,
    RefinementInput,
    RefinementOutput,
    SseEvent,
    SseEventType,
)

from .._sse_helpers import delay as _delay
from .._sse_helpers import now_ms as _now_ms
from .models import ChatStreamRequest
from .stub_stream import _stub_stream


# ============================================================
# 距离关键词识别（中文 + 数字）→ km 数
# ============================================================

_DISTANCE_KEYWORDS = ("公里以内", "km以内", "公里内", "km内", "公里以下", "公里")


def _extract_distance_km(text: str) -> Optional[float]:
    """从反馈文本里提 distance 上限（km）。

    支持「3 公里」「3公里以内」「3km 以内」「不超过 3 公里」。
    返回 None 表示文本无距离指示。
    """
    if not text:
        return None
    # 匹配 "数字 + 可选空白 + 单位"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(公里|km|千米)", text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:  # pragma: no cover
            return None
    return None


def _stub_refine(
    original: IntentExtraction, feedback_text: str
) -> RefinementOutput:
    """启发式 refiner（A 未实现 backend.agent.refiner 时的兜底）。

    规则：
    - "太远了" / "近一点" / "X 公里以内" → 缩小 distance_max_km
        - 显式数字优先；否则 distance × 0.6（向下取整到 0.5）
    - "不辣" / "清淡" → 加 dietary tag「不辣」
    - "便宜一点" / "贵一点" → 改 raw_input 提示，不改 schema 字段（避免 D9 越界）
    - 反馈空 → distance × 0.8 兜底（让用户感到 Agent 有响应）

    输出 RefinementOutput.refined_intent 必须仍合法（§5.7 D-SoT）；
    changed_fields 是中文摘要。
    """
    refined_data = original.model_dump()
    changes: list[str] = []

    txt = (feedback_text or "").strip()

    # ===== 距离调整 =====
    new_distance: Optional[float] = None
    if txt:
        explicit = _extract_distance_km(txt)
        if explicit is not None:
            new_distance = max(0.5, min(explicit, original.distance_max_km))
            if new_distance != original.distance_max_km:
                changes.append(
                    f"距离上限：{original.distance_max_km:g}km → {new_distance:g}km"
                )
        elif any(kw in txt for kw in ("太远", "近一点", "近点", "别走太远", "别太远")):
            scaled = round(original.distance_max_km * 0.6 * 2) / 2  # 取整到 0.5
            new_distance = max(0.5, scaled)
            if new_distance != original.distance_max_km:
                changes.append(
                    f"距离上限：{original.distance_max_km:g}km → {new_distance:g}km"
                )
    if new_distance is None and not txt:
        # 空反馈兜底：缩 0.8
        scaled = round(original.distance_max_km * 0.8 * 2) / 2
        if scaled != original.distance_max_km and scaled >= 0.5:
            new_distance = scaled
            changes.append(
                f"距离上限：{original.distance_max_km:g}km → {new_distance:g}km（兜底）"
            )
    if new_distance is not None:
        refined_data["distance_max_km"] = new_distance

    # ===== 饮食偏好叠加（仅命中词典内值）=====
    existing_dietary = set(refined_data.get("dietary_constraints") or [])
    if txt:
        if ("不辣" in txt or "清淡" in txt) and "不辣" not in existing_dietary:
            existing_dietary.add("不辣")
            changes.append("加忌口：不辣")
        if ("低脂" in txt or "减肥" in txt) and "低脂" not in existing_dietary:
            existing_dietary.add("低脂")
            changes.append("加忌口：低脂")
    refined_data["dietary_constraints"] = sorted(existing_dietary)

    # ===== 同行人语义增强（不改 schema 字段，仅写 raw_input 帮助下游 LLM）=====
    if txt:
        refined_data["raw_input"] = f"{original.raw_input}（用户反馈：{txt}）"

    # 重新校验（保证仍合法）
    refined = IntentExtraction.model_validate(refined_data)

    note: Optional[str] = None
    if changes:
        note = "已根据您的反馈调整：" + "；".join(changes)
    elif txt:
        note = "已记录您的反馈，本次维持原约束并重排候选。"
    else:
        note = "未收到具体反馈，本次自动收紧距离重排。"

    return RefinementOutput(
        refined_intent=refined,
        changed_fields=changes,
        refiner_note=note,
    )


async def _refine_stream(
    req: RefinementInput,
    cached: dict[str, Any],
) -> AsyncIterator[SseEvent]:
    """/chat/refine 完整 SSE 序列：refinement_start → refinement_done → 主路径事件。

    参考 api_contract.md §7。
    """
    seq = 0

    def emit(type_: SseEventType, payload: dict[str, Any]) -> SseEvent:
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload, timestamp_ms=_now_ms())
        seq += 1
        return ev

    # ---- 0: refinement_start ----
    yield emit(
        SseEventType.REFINEMENT_START,
        {"feedback_text": req.feedback_text or ""},
    )
    await _delay(180)

    # ---- 调 refiner（优先 A 实现，否则 _stub_refine）----
    original = IntentExtraction.model_validate(cached["intent"])
    refinement: RefinementOutput
    try:  # 预留：A 同学 commit refiner 后此分支生效
        from agent.intent.refiner import refine_intent  # type: ignore[import-not-found]

        refinement = refine_intent(original, req.feedback_text or "")
    except Exception:  # noqa: BLE001 — 兜底覆盖 ImportError + 实现异常
        refinement = _stub_refine(original, req.feedback_text or "")

    # ---- 1: refinement_done ----
    yield emit(SseEventType.REFINEMENT_DONE, refinement.model_dump())
    await _delay(220)

    # ---- 2..N: 复用主路径事件序列（用 refined intent 驱动）----
    placeholder_req = ChatStreamRequest(
        message=refinement.refined_intent.raw_input,
        session_id=req.session_id,
    )
    async for ev in _stub_stream(
        placeholder_req,
        intent_override=refinement.refined_intent,
        starting_seq=seq,
    ):
        # 同步本地 seq 计数器到 stream 内部，保证后续 seq 单调（虽然 _stub_stream 自管，
        # 这里只需透传事件即可——它的 emit 会基于 starting_seq 累加）
        yield ev
        seq = ev.seq + 1
