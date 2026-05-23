"""tests.test_reviews_ugc —— Step 9：UGC 评论 + LLM prompt 注入。

覆盖：
1. 所有 POI 至少 2 条评论（向后兼容例外：P_SOLD 售罄无评论是合理的）
2. 所有餐厅至少 2 条评论
3. 评论字段完整（text / rating / age_bucket / tag_evidence / visited_at / helpful_count）
4. _format_review_excerpts 摘要正确（top-2 helpful + 60 字截断）
5. build_candidate_preview 含 review_excerpts 字段（向 LLM 暴露）
"""

from __future__ import annotations

import pytest

from agent.planning.blueprint.blueprint_llm import _format_review_excerpts, build_candidate_preview
from data.loader import load_pois, load_restaurants, reset_cache
from schemas.domain import Review


# ============================================================
# Mock 数据完整性
# ============================================================

def test_pois_have_reviews():
    """除 P_SOLD（售罄无评论合理）外，所有 POI 至少 2 条评论。"""
    reset_cache()
    pois = load_pois()
    for p in pois:
        if p.id == "P_SOLD":
            continue  # 售罄实体允许无评论
        assert len(p.reviews) >= 2, (
            f"POI {p.id}（{p.name}）应有 ≥2 条评论，实际 {len(p.reviews)}"
        )


def test_restaurants_have_reviews():
    """所有餐厅至少 2 条评论。"""
    reset_cache()
    rests = load_restaurants()
    for r in rests:
        assert len(r.reviews) >= 2, (
            f"餐厅 {r.id}（{r.name}）应有 ≥2 条评论，实际 {len(r.reviews)}"
        )


def test_review_fields_complete():
    """随机抽几条评论确认 schema 字段完整（pydantic 已校验，这里二次确认）。"""
    pois = load_pois()
    sample = next(p for p in pois if p.id == "P040")
    assert len(sample.reviews) >= 2
    rev = sample.reviews[0]
    # 所有必填字段
    assert isinstance(rev.text, str) and len(rev.text) >= 10
    assert 1 <= rev.rating <= 5
    assert rev.user_age_bucket
    assert isinstance(rev.tag_evidence, list)
    assert isinstance(rev.helpful_count, int)


def test_p040_p041_p042_have_handcrafted_reviews():
    """P040 / P041 / P042 是手工写的复合场景 POI，评论应特别真实。"""
    pois = load_pois()
    for pid in ("P040", "P041", "P042"):
        p = next((x for x in pois if x.id == pid), None)
        assert p is not None
        assert len(p.reviews) >= 2
        # 至少一条评论的文本 ≥ 30 字（手工写的更详尽）
        long_revs = [r for r in p.reviews if len(r.text) >= 30]
        assert len(long_revs) >= 1, f"{pid} 至少应有 1 条 ≥30 字详尽评论"


# ============================================================
# review excerpt 摘要
# ============================================================

def test_format_review_excerpts_top_2_helpful():
    """选 top-2 helpful_count，按降序。"""
    revs = [
        Review(
            text="第 1 条评论，这是测试文本可以很长，确保超过 10 个字。",
            rating=5,
            user_age_bucket="80后",
            tag_evidence=["亲子友好"],
            helpful_count=10,
        ),
        Review(
            text="第 2 条评论，这是另一段测试文字也超过 10 个字。",
            rating=4,
            user_age_bucket="90后",
            tag_evidence=[],
            helpful_count=50,
        ),
        Review(
            text="第 3 条评论，又一段测试内容超过 10 个字。",
            rating=4.5,
            user_age_bucket="00后",
            tag_evidence=[],
            helpful_count=20,
        ),
    ]
    excerpts = _format_review_excerpts(revs)
    assert len(excerpts) == 2
    # 第一条应该是 helpful=50
    assert "第 2 条" in excerpts[0]["excerpt"]
    # 第二条应该是 helpful=20
    assert "第 3 条" in excerpts[1]["excerpt"]


def test_format_review_excerpts_truncates_to_60_chars():
    """text 长度 > 60 时截断。"""
    long_text = "测" * 200
    rev = Review(
        text=long_text,
        rating=5,
        user_age_bucket="80后",
        tag_evidence=[],
        helpful_count=10,
    )
    excerpts = _format_review_excerpts([rev])
    assert len(excerpts[0]["excerpt"]) <= 60


def test_format_review_excerpts_empty_returns_empty():
    assert _format_review_excerpts([]) == []


# ============================================================
# build_candidate_preview 含评论
# ============================================================

def test_candidate_preview_includes_review_excerpts():
    """LLM 看到的 preview 含 review_excerpts。"""
    pois = load_pois()
    rests = load_restaurants()
    p040 = next(p for p in pois if p.id == "P040")
    r001 = next(r for r in rests if r.id == "R001")

    preview = build_candidate_preview([p040], [r001], top_k=5)
    assert "review_excerpts" in preview["pois"][0]
    assert "review_excerpts" in preview["restaurants"][0]
    # P040 手工写了 2 条评论 → preview 至少 1 条
    assert len(preview["pois"][0]["review_excerpts"]) >= 1
    assert len(preview["restaurants"][0]["review_excerpts"]) >= 1
    # 摘要字段
    excerpt = preview["pois"][0]["review_excerpts"][0]
    assert "excerpt" in excerpt
    assert "age_bucket" in excerpt
    assert "tag_evidence" in excerpt
