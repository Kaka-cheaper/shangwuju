"""persona / preferences 端点（Phase 0.7 跨用户个性化）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["用户与偏好"])


@router.get("/personas", summary="拉所有 mock persona")
def list_personas() -> dict[str, list[dict[str, Any]]]:
    """返回所有 mock persona（前端 user 切换器拉这个）。

    payload 形态：
    {
      "personas": [
        { "user_id": "u_dad", "label": "新手爸爸", "icon": "👨‍👩‍👧",
          "notes": "...", "default_distance_max_km": 5.0,
          "default_tags": {...} },
        ...
      ]
    }
    """
    from data.memory_store import load_personas

    return {"personas": [p.model_dump() for p in load_personas()]}


@router.get("/preferences/{user_id}", summary="读取某用户的合并偏好")
def get_user_preferences(user_id: str) -> dict[str, Any]:
    """合并 persona + memory 给前端偏好面板用。"""
    from data.memory_store import compute_priors

    view = compute_priors(user_id)
    return view.model_dump()


@router.post("/preferences/{user_id}/reset", summary="清除某用户的累积偏好")
def reset_user_preferences(user_id: str) -> dict[str, Any]:
    """清掉某 user 的累积 memory（演示完清场用）。"""
    from data.memory_store import reset_memory

    fresh = reset_memory(user_id)
    return {"status": "ok", "memory": fresh.model_dump()}
