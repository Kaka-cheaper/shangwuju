"""agent.blueprint_llm —— LLM 蓝图生成器。

负责：
1. 把 POI / 餐厅候选打包为预览（不超过 top_k 条，仅展示 LLM 决策需要的字段）
2. 调 LLM（response_format=json_object）让它出 PlanBlueprint
3. 围栏剥离 + JSON 解析 + Pydantic-style 校验

不负责：
- Critic 验证（在 agent/blueprint.py）
- 真实调用 search_pois / search_restaurants（在 planner_llm_first.py）
- 最终拼装 Itinerary（在 planner.py）
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from schemas.domain import Poi, Restaurant
from schemas.intent import IntentExtraction

from .blueprint import (
    BlueprintStage,
    BlueprintTargetKind,
    PlanBlueprint,
)
from .llm_client import LLMClient, LLMMessage, strip_json_fence
from .prompts.blueprint_prompt import (
    BLUEPRINT_SYSTEM_PROMPT,
    build_user_message,
)


# ============================================================
# 异常
# ============================================================

@dataclass
class BlueprintGenError(Exception):
    """LLM 蓝图生成失败（JSON 非法 / 字段缺失 / 校验失败）。

    上层 planner_llm_first 应捕获并 fallback 到下一层（critic backprompt 或 rule）。
    """

    reason: str
    detail: str | None = None
    raw_content: str | None = None

    def __str__(self) -> str:  # pragma: no cover
        return f"BlueprintGenError({self.reason}): {self.detail}"


# ============================================================
# 候选预览
# ============================================================

def _poi_preview(p: Poi) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "type": p.type,
        "tags": list(p.tags),
        "suitable_for": list(p.suitable_for),
        "distance_km": p.distance_km,
        "opening_hours": p.opening_hours,
        "rating": p.rating,
        "age_range": p.age_range,
        "price_range": p.price_range,
        "review_excerpts": _format_review_excerpts(p.reviews),
    }


def _restaurant_preview(r: Restaurant) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "cuisine": r.cuisine,
        "tags": list(r.tags),
        "suitable_for": list(r.suitable_for),
        "distance_km": r.distance_km,
        "opening_hours": r.opening_hours,
        "avg_price": r.avg_price,
        "rating": r.rating,
        "review_excerpts": _format_review_excerpts(r.reviews),
    }


def _format_review_excerpts(reviews) -> list[dict]:
    """把 UGC 评论压缩成 LLM 易消费的摘要：top-2 helpful 的评论。

    每条仅给：text 前 60 字 + age_bucket + tag_evidence。
    用于 LLM 在 rationale 中引用「真实用户怎么说」让评委看到可信度。
    """
    if not reviews:
        return []
    sorted_revs = sorted(
        reviews,
        key=lambda r: getattr(r, "helpful_count", 0),
        reverse=True,
    )[:2]
    out = []
    for r in sorted_revs:
        text = getattr(r, "text", "") or ""
        out.append(
            {
                "excerpt": text[:60],
                "age_bucket": getattr(r, "user_age_bucket", ""),
                "tag_evidence": list(getattr(r, "tag_evidence", []) or []),
            }
        )
    return out


def build_candidate_preview(
    pois: list[Poi],
    restaurants: list[Restaurant],
    top_k: int = 5,
    *,
    transport_preference: str = "taxi",
) -> dict:
    """打包候选预览给 LLM。

    Args:
        pois / restaurants: 已搜索到的候选
        top_k: 每类候选取前几条（按 rating 排序），避免 token 爆炸
        transport_preference: walking / taxi / bus，决定通勤矩阵走哪一列

    Returns:
        {
          "pois": [...],
          "restaurants": [...],
          "commute_matrix": [
            { "from": "home", "to": "P001", "minutes": 13, "mode": "taxi" },
            ...
          ],
          "transport_preference": "taxi"
        }

    设计动机（pitfall P1-2026-05-23 ILS 死循环根因）：
      旧版 LLM 只看 distance_km（候选→家直线），靠经验法则猜段间通勤，
      但 critic 用的是 routes.json 段间真实矩阵——两边数据源不同，LLM 永远算不准。
      把矩阵直接喂给 LLM，让它从「猜距离」变「查表代入」。
    """
    pois_sorted = sorted(pois, key=lambda p: p.rating, reverse=True)[:top_k]
    rests_sorted = sorted(
        restaurants, key=lambda r: r.rating, reverse=True
    )[:top_k]

    return {
        "pois": [_poi_preview(p) for p in pois_sorted],
        "restaurants": [_restaurant_preview(r) for r in rests_sorted],
        "commute_matrix": _build_commute_matrix(
            pois_sorted, rests_sorted, transport_preference
        ),
        "transport_preference": transport_preference,
    }


def _build_commute_matrix(
    pois: list[Poi],
    restaurants: list[Restaurant],
    transport_pref: str,
) -> list[dict]:
    """构造段间通勤矩阵：home ↔ POI / POI ↔ Restaurant / Restaurant → home。

    只列 LLM 真正可能用到的边（top_k 候选两两 + home 双向），避免 token 爆炸。
    若 routes.json 没记录某条边，用 haversine 兜底（与 critic 保持一致）。

    返回：list[{from, to, minutes, mode}]，按 from+to 字典序排列方便 LLM 查表。
    """
    from tools._helpers import find_route

    poi_ids = [p.id for p in pois]
    rest_ids = [r.id for r in restaurants]

    # 需要的边：home↔POI / home↔Restaurant / POI↔Restaurant 双向
    edges: list[tuple[str, str]] = []
    for pid in poi_ids:
        edges.append(("home", pid))
        edges.append((pid, "home"))
    for rid in rest_ids:
        edges.append(("home", rid))
        edges.append((rid, "home"))
    for pid in poi_ids:
        for rid in rest_ids:
            edges.append((pid, rid))
            edges.append((rid, pid))

    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for fr, to in edges:
        if (fr, to) in seen or fr == to:
            continue
        seen.add((fr, to))
        route = find_route(fr, to)
        minutes: int | None = None
        if route is not None:
            if transport_pref == "walking" and route.walking_minutes is not None:
                minutes = route.walking_minutes
            elif transport_pref == "bus" and route.bus_minutes is not None:
                minutes = route.bus_minutes
            elif route.taxi_minutes is not None:
                minutes = route.taxi_minutes
        if minutes is None:
            # routes.json 没有这条边——LLM 看不到不算违规（critic 会兜底）
            # 这里跳过避免误导 LLM
            continue
        out.append(
            {
                "from": fr,
                "to": to,
                "minutes": minutes,
                "mode": transport_pref,
            }
        )
    out.sort(key=lambda x: (x["from"], x["to"]))
    return out


# ============================================================
# 主入口
# ============================================================

def generate_blueprint(
    intent: IntentExtraction,
    pois: list[Poi],
    restaurants: list[Restaurant],
    *,
    client: LLMClient,
    critic_feedback: list[str] | None = None,
    top_k_preview: int = 5,
    user_id: str = "demo_user",
) -> PlanBlueprint:
    """让 LLM 看候选数据后出蓝图。

    Args:
        intent: 已抽取的意图
        pois / restaurants: 已搜索到的候选实体
        client: LLM 客户端（必须可调 .chat()）
        critic_feedback: 上一轮 critic 的硬违规消息列表（重生成时传）
        top_k_preview: 候选预览取前几条
        user_id: 解析交通偏好用（默认 demo_user）

    Returns:
        PlanBlueprint

    Raises:
        BlueprintGenError: JSON 非法 / 字段缺失 / 蓝图自身字段约束失败
    """
    # 解析用户交通偏好（与 critics_v2 保持一致的判定方式）
    transport_pref = "taxi"
    try:
        from data.loader import load_user_profiles

        profiles = load_user_profiles()
        profile = profiles.get(user_id)
        if profile is not None:
            pref = getattr(profile, "transport_preference", "taxi") or "taxi"
            if pref in ("walking", "taxi", "bus"):
                transport_pref = pref
    except Exception:  # noqa: BLE001
        pass

    preview = build_candidate_preview(
        pois, restaurants,
        top_k=top_k_preview,
        transport_preference=transport_pref,
    )
    intent_json = intent.model_dump_json()
    candidates_json = json.dumps(preview, ensure_ascii=False, indent=2)

    user_msg = build_user_message(
        intent_json=intent_json,
        candidates_json=candidates_json,
        critic_feedback=critic_feedback,
    )
    messages = [
        LLMMessage(role="system", content=BLUEPRINT_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_msg),
    ]

    try:
        resp = client.chat(
            messages,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except Exception as e:  # noqa: BLE001
        raise BlueprintGenError(
            reason="llm_chat_failed",
            detail=f"{type(e).__name__}: {e}",
        ) from e

    content = strip_json_fence(resp.content) or ""
    if not content:
        raise BlueprintGenError(reason="empty_response")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as e:
        raise BlueprintGenError(
            reason="json_decode_failed",
            detail=str(e),
            raw_content=content[:500],
        ) from e

    if not isinstance(payload, dict):
        raise BlueprintGenError(
            reason="not_a_json_object", raw_content=content[:500]
        )

    stages_raw = payload.get("stages")
    if not isinstance(stages_raw, list) or not stages_raw:
        raise BlueprintGenError(
            reason="stages_missing_or_empty",
            detail=f"stages={stages_raw!r}",
            raw_content=content[:500],
        )

    try:
        stages = [_parse_stage(s) for s in stages_raw]
    except (ValueError, TypeError, KeyError) as e:
        raise BlueprintGenError(
            reason="stage_field_invalid",
            detail=str(e),
            raw_content=content[:500],
        ) from e

    rationale = str(payload.get("rationale", "")).strip()[:500]

    try:
        return PlanBlueprint(stages=stages, rationale=rationale)
    except ValueError as e:
        raise BlueprintGenError(
            reason="blueprint_validation_failed",
            detail=str(e),
            raw_content=content[:500],
        ) from e


def _parse_stage(raw: dict) -> BlueprintStage:
    """解析单条 stage；字段缺失 / 类型错误抛 ValueError 让上层包装。"""
    if not isinstance(raw, dict):
        raise ValueError(f"stage 不是 dict: {raw!r}")

    kind = str(raw.get("kind", "")).strip()
    if not kind:
        raise ValueError("stage.kind 为空")

    start_time = str(raw.get("start_time", "")).strip()
    if not start_time:
        raise ValueError("stage.start_time 为空")

    try:
        duration_min = int(raw.get("duration_min", 0))
    except (TypeError, ValueError) as e:
        raise ValueError(f"stage.duration_min 不是整数: {e}") from e

    tk_raw = str(raw.get("target_kind", "none")).strip().lower()
    try:
        target_kind = BlueprintTargetKind(tk_raw)
    except ValueError as e:
        raise ValueError(f"stage.target_kind 非法: {tk_raw!r}") from e

    target_id_raw = raw.get("target_id")
    target_id = (
        str(target_id_raw).strip() if target_id_raw not in (None, "") else None
    )

    note_raw = raw.get("note")
    note = str(note_raw).strip()[:200] if note_raw else None

    return BlueprintStage(
        kind=kind,
        start_time=start_time,
        duration_min=duration_min,
        target_kind=target_kind,
        target_id=target_id,
        note=note,
    )
