"""agent.blueprint_llm —— LLM 蓝图生成器（edge_v1）。

负责：
1. 把 POI / 餐厅候选打包为预览（不超过 top_k 条，仅展示 LLM 决策需要的字段；
   含 review_excerpts UGC 摘要供 LLM 在 rationale 里引用）
2. 调 LLM（response_format=json_object）让它出 PlanBlueprint
3. 围栏剥离 + JSON 解析 + **显式拒绝旧 stages / start_time / end_time 字段**
   + Pydantic 校验

【edge_v1 关键变化】
- 删除 commute_matrix：assemble 自己用 lookup_hop 算 hop，不再喂给 LLM
  （旧设计让 LLM 既看 commute_matrix 又算 start_time 容易漂移；现在 LLM 完全
  不算时间，commute_matrix 失去用途）
- 输出契约：`{nodes: [...], preferred_start_time: "14:00", rationale: "..."}`
- 解析层显式拒绝旧字段（stages / node 内 start_time / end_time / commute_minutes），
  让 BlueprintGenError 携带的 detail 比 Pydantic ValidationError 更友好

不负责：
- Critic 验证（蓝图级 critic 已随 ADR-0009 决策 8 删除——无生产调用者；
  Itinerary 级校验见 agent/planning/critic/critics_v2.py）
- 真实调用 search_pois / search_restaurants（在 planner_llm_first.py）
- 最终拼装 Itinerary（在 agent/planning/blueprint/assemble_blueprint.py）
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from schemas.domain import Poi, Restaurant
from schemas.intent import IntentExtraction

from .blueprint import PlanBlueprint
from ...core.llm_client import (
    MIMO_THINKING_DISABLED_EXTRA_BODY,
    LLMClient,
    LLMMessage,
    strip_json_fence,
)
from .prompts.blueprint_prompt import (
    BLUEPRINT_SYSTEM_PROMPT,
    build_user_message,
)


# ============================================================
# 异常
# ============================================================


@dataclass
class BlueprintGenError(Exception):
    """LLM 蓝图生成失败（JSON 非法 / 字段缺失 / 旧字段污染 / Pydantic 校验失败）。

    上层 planner_llm_first / LangGraph plan 节点应捕获，触发 critic backprompt
    或 fallback 链；不让旧字段透传到 assemble。
    """

    reason: str
    detail: str | None = None
    raw_content: str | None = None

    def __str__(self) -> str:  # pragma: no cover
        return f"BlueprintGenError({self.reason}): {self.detail}"


# ============================================================
# 候选预览（review_excerpts UGC 引用逻辑保留）
# ============================================================


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
    out: list[dict] = []
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


def _poi_preview(p: Poi, *, companions: list | None = None) -> dict:
    """投影 POI 给 LLM 看（spec planning-quality-deep-review R2）。

    Args:
        p: 候选 POI
        companions: IntentExtraction.companions 列表（含 .age 属性）；
            为 None 时降级到 default 桶。

    新增字段（R2）：
        suggested_duration_minutes: 按 companions 投影为单值（int 或 None）；
            **不**暴露 dict 结构给 LLM（design.md "不暴露字段名"原则）。
    """
    from utils.duration_helpers import get_duration_for_companions  # 局部导入避免循环

    suggested_int = get_duration_for_companions(
        p.suggested_duration_minutes, companions or []
    )
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
        "suggested_duration_minutes": suggested_int,
        "review_excerpts": _format_review_excerpts(p.reviews),
    }


def _restaurant_preview(r: Restaurant) -> dict:
    """投影餐厅给 LLM 看（spec planning-quality-deep-review R2）。

    新增字段（R2）：
        typical_dining_min: 按 cuisine 业界基线（健康轻食 40 / 粤菜 90 / 火锅 120 等）。
    """
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
        "typical_dining_min": r.typical_dining_min,
        "review_excerpts": _format_review_excerpts(r.reviews),
    }


def build_candidate_preview(
    pois: list[Poi],
    restaurants: list[Restaurant],
    top_k: int = 5,
    *,
    transport_preference: str = "taxi",
    companions: list | None = None,
    must_include_ids: frozenset[str] | set[str] | None = None,
) -> dict:
    """打包候选预览给 LLM（edge_v1：不再含 commute_matrix）。

    Args:
        pois / restaurants: 已搜索到的候选
        top_k: 每类候选取前几条（按 rating 排序），避免 token 爆炸
        transport_preference: walking / taxi / bus；仅作为元信息透传给 LLM
            告知用户偏好的交通方式（assemble 自己按这个调 lookup_hop 算 hop，
            **不再**给 LLM 喂段间通勤矩阵——这是 edge_v1 与旧版的关键区别）
        companions: IntentExtraction.companions 列表，spec R2 用于
            `_poi_preview` 投影 SuggestedDuration dict 为单值；缺省时
            降级到 default 桶（向后兼容旧调用方）。
        must_include_ids: 必须出现在预览里的实体 id（赞锁定根治批：锁定实体
            可能评分不进 top_k，被裁掉后 LLM 无从选它——系统提示硬性约束 3
            要求 target_id 必须在预览里存在，「必须保留」段引用一个预览里
            不存在的 id 是自相矛盾的指令）。命中的候选若被 top_k 裁掉则追加
            回来；不在召回池里的 id 无法凭空造出（那是 NO_MATCHING_CANDIDATES
            advisory 的领域，见 ils_planner._resolve_pinned）。None = 现状行为。

    Returns:
        {
          "pois": [...],            # 含 suggested_duration_minutes（int 投影）
          "restaurants": [...],     # 含 typical_dining_min
          "transport_preference": "taxi"
        }

    设计动机（edge_v1）：
        旧版给 LLM 喂 commute_matrix 让它「查表代入」算 start_time——
        但 edge_v1 LLM 完全不输出 start_time（只输出 nodes + duration_min），
        commute_matrix 在 prompt 里失去作用，反而占 token。assemble_from_blueprint
        在拼装 Itinerary 时调 lookup_hop 自己算 hop。
    """
    pois_sorted = sorted(pois, key=lambda p: p.rating, reverse=True)[:top_k]
    rests_sorted = sorted(
        restaurants, key=lambda r: r.rating, reverse=True
    )[:top_k]

    if must_include_ids:
        picked_poi_ids = {p.id for p in pois_sorted}
        pois_sorted += [
            p for p in pois if p.id in must_include_ids and p.id not in picked_poi_ids
        ]
        picked_rest_ids = {r.id for r in rests_sorted}
        rests_sorted += [
            r for r in restaurants if r.id in must_include_ids and r.id not in picked_rest_ids
        ]

    return {
        "pois": [_poi_preview(p, companions=companions) for p in pois_sorted],
        "restaurants": [_restaurant_preview(r) for r in rests_sorted],
        "transport_preference": transport_preference,
    }


# ============================================================
# 主入口
# ============================================================


# 旧字段名集合（解析层显式挡住，给 BlueprintGenError 更友好的诊断）
_LEGACY_NODE_FIELDS: frozenset[str] = frozenset(
    {"start_time", "end_time", "commute_minutes"}
)


def generate_blueprint(
    intent: IntentExtraction,
    pois: list[Poi],
    restaurants: list[Restaurant],
    *,
    client: LLMClient,
    critic_feedback: list[str] | None = None,
    top_k_preview: int = 5,
    user_id: str = "demo_user",
    pinned: list[dict] | None = None,
    single_consumption: bool = False,
) -> PlanBlueprint:
    """让 LLM 看候选数据后出蓝图（edge_v1：仅 nodes + preferred_start_time + rationale）。

    Args:
        intent: 已抽取的意图
        pois / restaurants: 已搜索到的候选实体
        client: LLM 客户端（必须可调 .chat()，OpenAI 兼容协议）
        critic_feedback: 上一轮 critic 的硬违规消息列表（重生成时传，注入 user message）
        top_k_preview: 候选预览取前几条
        user_id: 解析交通偏好用（默认 demo_user）
        pinned: 锁定清单 list[{"kind","target_id","name"}]（赞锁定根治批，形状同
            AgentState.pinned_targets）——注入 user message「必须保留」段（见
            build_user_message docstring；系统提示不动）。None/空 = 现状行为。

    Returns:
        PlanBlueprint（含 nodes + preferred_start_time + rationale）

    Raises:
        BlueprintGenError: 任意以下情况：
            - LLM 调用失败 / 返回空内容
            - JSON 非法或不是对象
            - payload 含已废弃的 `stages` 字段（旧 schema 污染）
            - 任意 node 含已废弃字段（start_time / end_time / commute_minutes）
            - PlanBlueprint Pydantic 校验失败（字段缺失 / 类型错误 / extra="forbid"）
    """
    # 1. 解析用户交通偏好（与 critics_v2 / assemble 保持同源）
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
        # user_profile 加载失败不阻塞蓝图生成；assemble 自己有兜底
        pass

    # 2. 构造候选预览（不含 commute_matrix；锁定实体强制进预览，见
    # build_candidate_preview.must_include_ids docstring）
    pinned_ids = frozenset(
        p.get("target_id")
        for p in (pinned or [])
        if isinstance(p, dict) and p.get("target_id")
    )
    preview = build_candidate_preview(
        pois,
        restaurants,
        top_k=top_k_preview,
        transport_preference=transport_pref,
        companions=list(intent.companions),  # spec R2: 投影 SuggestedDuration → 单值
        must_include_ids=pinned_ids or None,
    )
    intent_json = intent.model_dump_json()
    candidates_json = json.dumps(preview, ensure_ascii=False, indent=2)

    user_msg = build_user_message(  # single_consumption 见 Bug B·B4 firm 块
        intent_json=intent_json,
        candidates_json=candidates_json,
        critic_feedback=critic_feedback,
        pinned=list(pinned) if pinned else None,
        single_consumption=single_consumption,
    )
    messages = [
        LLMMessage(role="system", content=BLUEPRINT_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_msg),
    ]

    # 3. 调 LLM
    try:
        resp = client.chat(
            messages,
            temperature=0.2,
            response_format={"type": "json_object"},
            # 真因修复批 item 5：低成本保险，非本批诊断出的主根因（真 LLM
            # 8/8 轮蓝图都正常生成了 JSON，问题在 assemble 侧的槽对齐，见
            # assemble_blueprint.py 的槽吸附修复）。但蓝图生成同样是"只要
            # 结构化 JSON、不要思考过程"的场景——思考模型（如 MiMo）的
            # reasoning token 计入 max_tokens 预算，narrator.py 已经在生产
            # 踩过一次"思考吃光预算、正文截空"的静默失败（见该文件模块
            # docstring），蓝图生成没有理由不带同一份保险，不必等它也在
            # 生产上出事才补。对不识别该字段的 provider 无害（见常量定义）。
            extra_body=MIMO_THINKING_DISABLED_EXTRA_BODY,
        )
    except Exception as e:  # noqa: BLE001
        raise BlueprintGenError(
            reason="llm_chat_failed",
            detail=f"{type(e).__name__}: {e}",
        ) from e

    content = strip_json_fence(resp.content) or ""
    if not content:
        raise BlueprintGenError(reason="empty_response")

    # 4. JSON 解析
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
            reason="not_a_json_object",
            detail=f"top-level type={type(payload).__name__}",
            raw_content=content[:500],
        )

    # 5. 显式拒绝旧 schema 字段（解析层就挡住，比 Pydantic ValidationError 友好）
    if "stages" in payload:
        raise BlueprintGenError(
            reason="legacy_stages_field",
            detail=(
                "LLM 输出包含已废弃的 stages 字段，请只输出 nodes 数组（edge_v1）；"
                "edge_v1 模型把通勤过程从 stage 拆为独立的 hop 边，LLM 只决定节点 "
                "kind/target/duration，不输出 start_time / end_time / hop"
            ),
            raw_content=content[:500],
        )

    nodes_raw = payload.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise BlueprintGenError(
            reason="nodes_missing_or_empty",
            detail=f"nodes={nodes_raw!r}（edge_v1 要求 nodes 为非空列表）",
            raw_content=content[:500],
        )

    # 6. 逐 node 显式查旧字段（让诊断指明是哪个 index）
    for i, n in enumerate(nodes_raw):
        if not isinstance(n, dict):
            raise BlueprintGenError(
                reason="node_not_dict",
                detail=f"nodes[{i}] 不是 dict: {n!r}",
                raw_content=content[:500],
            )
        forbidden = _LEGACY_NODE_FIELDS.intersection(n.keys())
        if forbidden:
            raise BlueprintGenError(
                reason="legacy_node_field",
                detail=(
                    f"nodes[{i}] 含已废弃字段 {sorted(forbidden)}；"
                    f"edge_v1 LLM 只输出 kind/target_kind/target_id/duration_min/note，"
                    f"start_time / hop 时间由 assemble_from_blueprint 自动算"
                ),
                raw_content=content[:500],
            )

    # 7. Pydantic 校验：BlueprintNode/PlanBlueprint 的 extra="forbid" 兜剩下的脏字段
    try:
        return PlanBlueprint.model_validate(payload)
    except (ValueError, TypeError) as e:
        raise BlueprintGenError(
            reason="blueprint_validation_failed",
            detail=str(e),
            raw_content=content[:500],
        ) from e
