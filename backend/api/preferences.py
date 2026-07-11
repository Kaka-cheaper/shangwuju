"""persona / preferences 端点（Phase 0.7 跨用户个性化）。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter(tags=["用户与偏好"])


class ResetRequest(BaseModel):
    """POST /reset 的请求体——session_id 走 body（HTTP 方法语义：POST 的参数
    是操作载荷，见方案 §14.4）。缺省时退回按 user_id 清扫的旧 no-op 行为
    （前端契约不破）。"""

    model_config = ConfigDict(extra="forbid")

    session_id: Optional[str] = None


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
def get_user_preferences(user_id: str, session_id: Optional[str] = None) -> dict[str, Any]:
    """persona 模板 + 会话累积的合并视图给前端偏好面板用。

    键语义（记忆身份读写分离批，ADR-0015 身份边界补充决策，2026-07-05；读端点
    断链修复，用户偏好面板全环方案 §2.3/§14.4）：
    demo 无账号体系，会话即身份——`user_id` 是画像模板 id（共享只读），
    `session_id` 是累积键（会话私有）。`session_id` 走 **query 参数**（GET 无
    请求体，且 session_id 语义上是"想看哪个会话视角的偏好视图"，是资源过滤
    条件——比挂 header 更贴合 HTTP 惯例，见方案 §2.3 候选 α）。

    缺省 `session_id`（不传）时退回**纯模板视图**（memory 区恒为空计数）——
    诚实展示"这个画像模板长什么样"，绝不把某个访客的会话累积当成"该用户"的
    偏好展示给别人；这是既有 W1 契约（无 session 上下文的调用方，如后台批量
    读模板）的兼容路径，不是本端点的主用法。前端 `refreshPreferences` 会传当前
    `sessionId`（含房间模式下的房间会话键），见 `frontend/lib/store.ts`。
    """
    from data.memory_store import compute_priors

    view = compute_priors(user_id, session_id)
    return view.model_dump()


@router.post("/preferences/{user_id}/reset", summary="清除某键的累积偏好")
def reset_user_preferences(user_id: str, body: ResetRequest | None = None) -> dict[str, Any]:
    """清掉某会话键的累积 memory（演示完清场用；按钮文案"清空学到的记忆"）。

    `session_id` 走 **请求体**（POST 已带 body，参数即操作载荷，见方案
    §14.4）——真正被清空的是 `reset_memory(session_id)`，不是 `user_id`
    （画像模板永不被运行时改写）。`body` 缺省或其 `session_id` 为空时退回
    按 `user_id` 清扫的旧 no-op 行为（该键从未被累积写入，近似 no-op），
    保留是为前端契约不破 + 生产迁移（键换账号 ID）后恢复原语义。
    """
    from data.memory_store import reset_memory

    session_key = (body.session_id if body else None) or user_id
    fresh = reset_memory(session_key)
    return {"status": "ok", "memory": fresh.model_dump()}
