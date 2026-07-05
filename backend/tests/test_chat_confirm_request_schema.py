"""tests.test_chat_confirm_request_schema —— 分界修缮批 任务 6：删死协议。

病灶（全后端 LLM/规则分界普查实锤）：`ChatConfirmRequest.allowed_restaurant_ids
/ allowed_poi_ids`（spec execution-quality-review R2 立的"执行类工具白名单"）
全仓零消费——字段收下后从未有任何代码比对它们，`api/chat.py` docstring 却宣称
校验存在，是虚假安全声明。前端也从未发送（grep frontend/ 零命中）。

防编造目标的真实防线是 pending_actions 忠实回放（规划期 finalize_plan 锁清单、
confirm 期 replay_confirm_actions 照单执行，见 execute_finalize.py），不是这对
死字段。删除后 `extra="forbid"` 会让仍按旧文档发送这两个字段的请求 422——
这里钉死"协议里不再收这两个字段"。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api._streams.models import ChatConfirmRequest


def test_confirm_request_no_longer_accepts_dead_whitelist_fields():
    with pytest.raises(ValidationError):
        ChatConfirmRequest(
            session_id="s1",
            decision="confirm",
            allowed_restaurant_ids=["R001"],
        )
    with pytest.raises(ValidationError):
        ChatConfirmRequest(
            session_id="s1",
            decision="confirm",
            allowed_poi_ids=["P001"],
        )


def test_confirm_request_normal_shape_still_valid():
    req = ChatConfirmRequest(session_id="s1", decision="confirm")
    assert req.decision == "confirm"
    assert not hasattr(req, "allowed_restaurant_ids")
    assert not hasattr(req, "allowed_poi_ids")
