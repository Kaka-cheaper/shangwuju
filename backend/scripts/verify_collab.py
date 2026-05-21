"""验证多人协作 WebSocket 端点的基本功能。

运行：python -m scripts.verify_collab
"""

import asyncio
import json

from httpx import AsyncClient, ASGITransport
from main import app


async def main():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. 创建房间
        resp = await client.post("/room/create", json={
            "user_id": "u_dad",
            "nickname": "老公",
        })
        assert resp.status_code == 200, f"创建房间失败: {resp.text}"
        data = resp.json()
        room_id = data["room_id"]
        print(f"✓ 房间创建成功: {room_id}")
        print(f"  分享链接: {data['share_url']}")

        # 2. 获取房间状态
        resp = await client.get(f"/room/{room_id}/state")
        assert resp.status_code == 200
        state = resp.json()
        assert state["type"] == "room_state"
        assert state["owner_id"] == "u_dad"
        assert len(state["members"]) == 1
        print(f"✓ 房间状态获取成功: {len(state['members'])} 成员")

        # 3. 不存在的房间返回 404
        resp = await client.get("/room/nonexist/state")
        assert resp.status_code == 404
        print("✓ 不存在的房间正确返回 404")

        # 4. 带 session 行程创建房间
        # 先模拟一个 session 有行程
        from main import _SESSION_STORE
        _SESSION_STORE["test_sess"] = {
            "intent": {"raw_input": "测试", "distance_max_km": 5},
            "itinerary": {"summary": "测试行程", "stages": []},
        }
        resp = await client.post("/room/create", json={
            "user_id": "u_dad",
            "nickname": "老公",
            "session_id": "test_sess",
        })
        assert resp.status_code == 200
        data2 = resp.json()
        room_id2 = data2["room_id"]

        resp = await client.get(f"/room/{room_id2}/state")
        state2 = resp.json()
        assert state2["itinerary"] is not None
        assert state2["intent"] is not None
        print(f"✓ 带行程创建房间成功: 行程已带入")

    print("\n✓ 所有 HTTP 端点验证通过")
    print("\n注：WebSocket 端点需要真实 WS 连接测试（uvicorn 启动后用浏览器或 websockets 库）")


if __name__ == "__main__":
    asyncio.run(main())
