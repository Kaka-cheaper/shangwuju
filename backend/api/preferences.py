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
    """persona 模板视图给前端偏好面板用。

    键语义（记忆身份读写分离批，ADR-0015 身份边界补充决策，2026-07-05）：
    demo 无账号体系，会话即身份——累积记忆已按 session_id 键控（会话私有），
    而本端点只有 user_id（画像模板 id，被所有选同一画像的访客共享）、没有会话
    上下文，因此返回的是 `compute_priors(user_id)` 的**纯模板视图**（memory 区
    恒为空计数）——诚实展示"这个画像模板长什么样"，绝不把某个访客的会话累积
    当成"该用户"的偏好展示给别人。生产迁移（键换账号 ID）后本端点自动恢复
    "模板 + 账号累积"的合并视图，机制不动。
    """
    from data.memory_store import compute_priors

    view = compute_priors(user_id)
    return view.model_dump()


@router.post("/preferences/{user_id}/reset", summary="清除某键的累积偏好")
def reset_user_preferences(user_id: str) -> dict[str, Any]:
    """清掉某键的累积 memory（演示完清场用）。

    读写分离批注：累积已按 session_id 键控；按 user_id 清扫在 demo 姿态下
    近似 no-op（该键不再被累积写入），保留端点是为前端契约不破 + 生产迁移后
    （键=账号 ID）恢复原语义。
    """
    from data.memory_store import reset_memory

    fresh = reset_memory(user_id)
    return {"status": "ok", "memory": fresh.model_dump()}
