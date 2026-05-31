"""agent.planning.preference_scorer —— LLM 语义打分（ItiNera EMNLP'24 范式）。

【为什么需要这一层】

ILS 算法的 4 维 utility（comfort / time / cost / smoothness）只能算出
「客观可量化指标」（标签命中数 / 距离 / 价格），但抓不到：

- 「带 5 岁娃想看可爱小动物，避开恐怖元素」这类**语义偏好**
- 「想要安静的氛围，但又要能拍照」这类**多约束权衡**
- POI 的 `description` / `reviews` 字段里的**自然语言信号**

ItiNera (EMNLP'24) 的关键洞察：把 LLM 当成「语义 ranker」放进算法主循环，
让它给每个候选打 0-1 语义契合分，作为 utility 的额外加项。

【设计纪律（spec algorithm-redesign R4）】

- **失败兜底全 0.5**：LLM 调用失败 / JSON 解析失败 / 字段缺失 → 全部默认 0.5
  分（不阻断 ILS 主路径；hackathon demo 永不翻车）
- **stub 模式短路**：检测 `client.provider == "stub"` 时直接返全 0.5（不调 LLM）
- **批量调一次**：30 个 POI 一起喂 prompt，避免 N 次 API call 把 latency 拉爆
  （spec A 锁 30s 预算下加 ~3s 单次调用是可接受的）
- **prompt 严格 JSON 输出**：用 `response_format={"type": "json_object"}` 让 LLM 输出
  围栏剥离后能直接 json.loads
- **clip [0, 1] + 类型校验**：LLM 偶尔返 "0.85" 字符串 / 越界值 / 缺字段 → 一律
  fallback 0.5

【与 _utility 的关系】

`ils_planner._utility` 末尾追加 `score += 0.3 * semantic_scores.get(poi.id, 0.5)`，
保留 spec A R5 的 `_overload_penalty` 不变；这是「先验客观 utility + LLM 主观加项」
的双层叠加（不是替换）。

不负责：
- 餐厅打分（餐厅由 dietary_constraints 硬约束 + spec A R7 social_compat 处理）
- 行程总分（在 _utility 主体）
- 多日规划（半日范式不需要）
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from agent.core.llm_client import LLMMessage
from agent.core.prompt_guard import ROLE_LOCK_NOTICE
from schemas.domain import Poi
from schemas.intent import IntentExtraction


logger = logging.getLogger(__name__)


# ============================================================
# Prompt
# ============================================================

_SCORER_PROMPT = ROLE_LOCK_NOTICE + """

你是一个本地生活语义打分助手。给定用户的出行意图与一组候选 POI，
为每个 POI 输出 0.0-1.0 之间的契合度分数（保留两位小数）。

【打分维度】
1. social_context 适配度（场景调性是否匹配）
2. 同行人画像适配度（年龄 / 体力 / 偏好）
3. 体验标签语义匹配（不只看字面 tag，看含义）
4. 用户隐含期待（从 raw_input 自然语言推）

【输出格式】严格 JSON，无任何解释文字：
```json
{"scores": {"P001": 0.85, "P002": 0.42, ...}}
```

【打分校准】
- 0.8-1.0：高度契合（首选推荐）
- 0.5-0.8：中等契合（可选）
- 0.0-0.5：不契合（应避免）
- 不确定时给 0.5（保守中性）

只输出 JSON，禁止任何解释 / markdown 围栏 / 多余字符。
"""


# ============================================================
# 主接口
# ============================================================


def score_pois_with_llm(
    intent: IntentExtraction,
    pois: list[Poi],
    *,
    client: Any | None = None,
) -> dict[str, float]:
    """批量调一次 LLM 给候选 POI 打 0-1 语义契合分。

    Args:
        intent: 意图（自然语言 raw_input + 结构化 social_context / companions）
        pois: 候选 POI 列表（已经过 grounding-first / search_pois 过滤）
        client: LLMClient；None 时用默认 client（懒加载）

    Returns:
        dict[poi_id, score]：每个 POI 的 0-1 语义分。
        失败 / stub mode → 所有 POI 返 0.5。

    设计纪律：
    - **永不抛异常**：任何错误都 fallback 全 0.5（ILS 主路径不阻断）
    - **stub 短路**：client.provider == "stub" 时直接返全 0.5
    - **空 POI 列表**：返空 dict
    """
    if not pois:
        return {}

    fallback = {p.id: 0.5 for p in pois}

    # 1. stub 模式短路
    if client is not None and getattr(client, "provider", None) == "stub":
        logger.info("preference_scorer: stub mode, returning all 0.5")
        return fallback

    # 2. 拿 client（client 为 None 时懒加载默认 client）
    if client is None:
        try:
            from agent.core.llm_client import get_llm_client
            client = get_llm_client()
        except Exception as exc:
            logger.warning(
                "preference_scorer: failed to get default LLM client (%s), fallback all 0.5",
                exc,
            )
            return fallback

    # 二次检查 stub（懒加载后）
    if getattr(client, "provider", None) == "stub":
        return fallback

    # 3. 构造 prompt
    user_msg = _build_user_message(intent, pois)
    messages = [
        LLMMessage(role="system", content=_SCORER_PROMPT),
        LLMMessage(role="user", content=user_msg),
    ]

    # 4. 调 LLM；任何异常 fallback
    try:
        resp = client.chat(
            messages,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning(
            "preference_scorer: LLM chat failed (%s), fallback all 0.5",
            exc,
        )
        return fallback

    raw = (resp.content or "").strip()
    if not raw:
        return fallback

    # 5. JSON 解析（已经过 strip_json_fence；client.chat 内部已处理围栏）
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "preference_scorer: JSON decode failed (%s); raw=%r; fallback all 0.5",
            exc,
            raw[:200],
        )
        return fallback

    scores_raw = payload.get("scores")
    if not isinstance(scores_raw, dict):
        logger.warning(
            "preference_scorer: payload.scores is not dict (got %r); fallback all 0.5",
            type(scores_raw).__name__,
        )
        return fallback

    # 6. 类型校验 + clip [0, 1] + 缺失项填 0.5
    out: dict[str, float] = {}
    for poi in pois:
        raw_score = scores_raw.get(poi.id)
        clipped = _coerce_and_clip(raw_score)
        out[poi.id] = clipped if clipped is not None else 0.5
    return out


# ============================================================
# Helpers
# ============================================================


def _build_user_message(intent: IntentExtraction, pois: list[Poi]) -> str:
    """构造 LLM 用户消息（intent 自然语言 + POI 列表压缩描述）。"""
    # intent 摘要
    companion_text = "无（独处）"
    if intent.companions:
        parts = [
            f"{c.role}{f'({c.age}岁)' if c.age is not None else ''}×{c.count}"
            for c in intent.companions
        ]
        companion_text = "、".join(parts)

    intent_block = (
        f"【用户原话】{intent.raw_input}\n"
        f"【场景】{intent.social_context}\n"
        f"【同行人】{companion_text}\n"
        f"【体验偏好】{', '.join(intent.experience_tags) or '无'}\n"
        f"【时长】{intent.duration_hours[0]}-{intent.duration_hours[1]}h\n"
        f"【距离上限】{intent.distance_max_km}km"
    )

    # POI 列表压缩（id / name / type / tags / rating / desc 前 80 字符）
    poi_lines: list[str] = []
    for poi in pois:
        # description 字段在 schema 中可能不存在；用 reviews[0].text 当替代
        desc = ""
        if poi.reviews:
            desc = (poi.reviews[0].text or "")[:80]
        tags_str = ", ".join(poi.tags[:6])
        poi_lines.append(
            f"- {poi.id}: {poi.name}（{poi.type}）"
            f"\n  tags: [{tags_str}] | rating: {poi.rating}"
            + (f"\n  评价摘要: {desc}" if desc else "")
        )
    pois_block = "\n".join(poi_lines)

    return (
        f"{intent_block}\n\n"
        f"【候选 POI 列表】\n{pois_block}\n\n"
        f"请输出每个 POI 的语义契合度 0.0-1.0 分（JSON 格式）。"
    )


def _coerce_and_clip(value: Any) -> Optional[float]:
    """LLM 返的分数可能是 float / int / "0.85" 字符串 / None；统一转 float 并 clip [0,1]。

    Returns:
        float in [0, 1]；不能转 → None（调用方 fallback 0.5）
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return max(0.0, min(1.0, f))


__all__ = ["score_pois_with_llm"]
