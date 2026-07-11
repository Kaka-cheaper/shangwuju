"""tests.test_preferences_endpoint —— 用户偏好面板全环方案 §1/§2.3/§14.4：
读端点补 session_id + recent_trips 加进 UserPreferenceView。

驱动手法：大部分用例直调 `api.preferences` 里的端点函数（同模块风格：纯
sync 函数，不依赖 FastAPI DI/Request，直调即真实覆盖端点逻辑）；`TestClient`
类额外覆盖 FastAPI 参数绑定契约本身（query string 解析成 `Optional[str]`
形参、POST 完全不带 body 时 `ResetRequest | None` 是否真的绑定成 `None` 而
不是校验报错）——这两点是直调函数测不出来的，因为直调时参数已经是 Python
对象，跳过了 FastAPI 的请求解析层。

覆盖点：
1. GET 不传 session_id → 纯模板视图（memory 空、recent_trips 空）——W1 既有
   契约不破。
2. GET 传 session_id → 合并视图（accepted_tags 有计数、recent_trips 非空）。
3. recent_trips 字段存在且值正确透传（LIFO、summary 原样）。
4. POST /reset 传 body.session_id → 清的是该会话键，不是 user_id。
5. POST /reset 不传 body → 退回按 user_id 清扫的旧 no-op 兼容路径（不炸）。
6. TestClient 级：GET query 绑定 + POST 无 body 时的 FastAPI 请求解析契约。
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.preferences import ResetRequest, get_user_preferences, reset_user_preferences, router
from data.memory_store import record_accepted, record_recent_trip, reset_all_memory
from schemas.domain import RecentTrip


@pytest.fixture(autouse=True)
def _isolate_memory():
    reset_all_memory()
    os.environ.pop("SHANGWUJU_MEMORY_DIR", None)
    yield
    reset_all_memory()


def test_get_without_session_id_returns_template_only():
    view = get_user_preferences("u_dad")
    assert view["memory"]["accepted_tags"]["counts"] == {}
    assert view["recent_trips"] == []


def test_get_with_session_id_returns_merged_view():
    record_accepted("sess_pref_ep", tags=["低脂"], distance_km=3.0)
    view = get_user_preferences("u_dad", session_id="sess_pref_ep")
    assert view["memory"]["accepted_tags"]["counts"].get("低脂") == 1
    assert view["suggested_distance_max_km"] == 3.0


def test_get_with_session_id_includes_recent_trips():
    trip = RecentTrip(
        timestamp="2026-07-11T10:00:00Z",
        social_context="家庭日常",
        summary="家庭日常场景，轻松节奏，室内活动为主。",
        success=True,
    )
    record_recent_trip("sess_pref_ep_trips", trip)
    view = get_user_preferences("u_dad", session_id="sess_pref_ep_trips")
    assert len(view["recent_trips"]) == 1
    assert view["recent_trips"][0]["summary"] == trip.summary
    assert view["recent_trips"][0]["social_context"] == "家庭日常"


def test_session_scoped_no_leak_across_sessions():
    """闭环唯一必要修法的核心不变式：A 会话的累积不得混进 B 会话视图。"""
    record_accepted("sess_pref_a", tags=["商务体面"])
    view_b = get_user_preferences("u_dad", session_id="sess_pref_b")
    assert "商务体面" not in view_b["top_priors"]
    view_none = get_user_preferences("u_dad")
    assert "商务体面" not in view_none["top_priors"]


def test_reset_with_session_id_in_body_clears_that_session():
    record_accepted("sess_pref_reset", tags=["低脂"])
    result = reset_user_preferences("u_dad", ResetRequest(session_id="sess_pref_reset"))
    assert result["status"] == "ok"
    # 清的是 session 键，重新读该 session 应为空
    view = get_user_preferences("u_dad", session_id="sess_pref_reset")
    assert view["memory"]["accepted_tags"]["counts"] == {}


def test_reset_without_body_falls_back_to_user_id_key_noop():
    """兼容路径：不传 body（或 session_id 为空）时按 user_id 清扫，不炸。"""
    result = reset_user_preferences("u_dad", None)
    assert result["status"] == "ok"


def test_reset_does_not_clear_other_session():
    """清空只打被清的那个会话键，不误伤同用户其它会话（§2.4 边界确认）。"""
    record_accepted("sess_pref_keep", tags=["亲子友好"])
    record_accepted("sess_pref_wipe", tags=["低脂"])
    reset_user_preferences("u_dad", ResetRequest(session_id="sess_pref_wipe"))
    kept = get_user_preferences("u_dad", session_id="sess_pref_keep")
    assert kept["memory"]["accepted_tags"]["counts"].get("亲子友好") == 1


# ============================================================
# TestClient 级：FastAPI 参数绑定契约本身（直调函数测不出来的那一层）
# ============================================================


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_wire_get_binds_session_id_from_query_string(client: TestClient):
    record_accepted("sess_wire_a", tags=["低脂"])
    resp = client.get("/preferences/u_dad", params={"session_id": "sess_wire_a"})
    assert resp.status_code == 200
    assert resp.json()["memory"]["accepted_tags"]["counts"].get("低脂") == 1


def test_wire_get_without_query_param_is_template_view(client: TestClient):
    record_accepted("sess_wire_b", tags=["低脂"])
    resp = client.get("/preferences/u_dad")
    assert resp.status_code == 200
    assert resp.json()["memory"]["accepted_tags"]["counts"] == {}


def test_wire_post_reset_with_no_body_at_all_does_not_error(client: TestClient):
    """真正的裸 POST（连 `{}` 都不发）——验证 `ResetRequest | None = None` 在
    FastAPI 请求解析层真的能绑定成 None，而不是因为"缺 body"报 422。"""
    resp = client.post("/preferences/u_dad/reset")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_wire_post_reset_with_json_body_clears_that_session(client: TestClient):
    record_accepted("sess_wire_c", tags=["亲子友好"])
    resp = client.post("/preferences/u_dad/reset", json={"session_id": "sess_wire_c"})
    assert resp.status_code == 200
    check = client.get("/preferences/u_dad", params={"session_id": "sess_wire_c"})
    assert check.json()["memory"]["accepted_tags"]["counts"] == {}
