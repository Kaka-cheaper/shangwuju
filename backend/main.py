"""backend.main —— 晌午局 FastAPI 入口（HTTP + SSE 流式网关）。

接口契约：见 `backend/api_contract.md`
- GET  /health        健康检查
- POST /chat/stream   主入口：一句话 → SSE 流式输出
- POST /chat/confirm  MVP-2：用户确认后执行（stub 模式下也走完整事件序列）
- GET  /scenarios     拉取 8 个演示场景的快捷输入

运行模式由环境变量 LLM_PROVIDER 决定：
- stub      ：本文件内置 fixture（家庭主场景 + E1 异常 → 重规划），无需 LLM
- deepseek  ：P2 完成后接入 backend.agent.planner（当前未实现，落到 stub）
- qwen      ：同上

不负责：
- LLM 调用（在 backend/agent/）
- Tool 实现（在 backend/tools/）
- 规划决策（在 backend/agent/planner.py）

参考：
- pitfalls P3-跨项目「dotenv 双重保险加载」：CLI 入口与服务入口都要 load_dotenv()
- AGENTS.md §3.3 4 层架构边界：UI 不直连 LLM；HTTP 层只做转发与 SSE 序列化
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, AsyncIterator, Optional

from dotenv import load_dotenv

# 双重保险加载 .env（uvicorn --reload 子进程会跳过 CLI 入口）
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from schemas import (
    Companion,
    IntentExtraction,
    Itinerary,
    ItineraryStage,
    SseEvent,
    SseEventType,
)
from schemas.errors import FailureReason


# ============================================================
# 配置
# ============================================================

VERSION = "0.1.0"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "stub")
CORS_ORIGINS_RAW = os.getenv("SHANGWUJU_CORS_ORIGINS", "http://localhost:3000")
CORS_ORIGINS = [o.strip() for o in CORS_ORIGINS_RAW.split(",") if o.strip()]


# ============================================================
# 演示场景集（来源：docs/01-requirements/演示场景集.md §二）
# ============================================================

SCENARIOS: list[dict[str, str]] = [
    {
        "id": "S1",
        "title": "家庭主线",
        "input": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
        "icon": "👨‍👩‍👧",
    },
    {
        "id": "S2",
        "title": "朋友 4 人",
        "input": "今天下午想和朋友出去玩几小时，4 个人 2 男 2 女，别离家太远。",
        "icon": "👫",
    },
    {
        "id": "S3",
        "title": "情侣看展",
        "input": "周日下午带着女朋友去看个展，顺便找个安静能聊天的地方吃饭。",
        "icon": "💑",
    },
    {
        "id": "S4",
        "title": "带父母散步",
        "input": "周日下午想带外公外婆出去走走，别走太远他们腿不好。",
        "icon": "👴",
    },
    {
        "id": "S5",
        "title": "闺蜜下午茶",
        "input": "周末下午约了闺蜜想找个网红的地方拍拍照吃个下午茶。",
        "icon": "👯",
    },
    {
        "id": "S6",
        "title": "商务接待",
        "input": "下午临时被叫去接个外地客户，对方是商务人士，帮我安排下。",
        "icon": "💼",
    },
    {
        "id": "S7",
        "title": "独处放空",
        "input": "这周加班加得想吐，下午想一个人安安静静待几个小时再回家。",
        "icon": "🌿",
    },
    {
        "id": "S8",
        "title": "跨代际纪念日",
        "input": "周日是我妈生日，全家 6 个人想一起出去吃顿好的，她想吃粤菜。",
        "icon": "🎂",
    },
]


# ============================================================
# Request / 内存 session 存
# ============================================================

class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(..., min_length=1, max_length=500)
    session_id: str = Field(..., min_length=1, max_length=128)
    scenario_id: Optional[str] = None


class ChatConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1, max_length=128)
    decision: str = Field(..., pattern="^(confirm|reject|modify)$")
    modifications: Optional[dict[str, Any]] = None


# session_id -> {"intent": ..., "itinerary": ...}（demo 级 in-memory）
_SESSION_STORE: dict[str, dict[str, Any]] = {}


# ============================================================
# 应用
# ============================================================

app = FastAPI(
    title="晌午局 Backend",
    version=VERSION,
    description="本地半日出行管家 Agent 后端（FastAPI + SSE）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 端点
# ============================================================

@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": VERSION,
        "llm_provider": LLM_PROVIDER,
    }


@app.get("/scenarios")
def scenarios() -> dict[str, list[dict[str, str]]]:
    return {"scenarios": SCENARIOS}


@app.post("/chat/stream")
async def chat_stream(req: ChatStreamRequest) -> EventSourceResponse:
    """主入口：一句话 → SSE 流式输出。

    当前 W3：固定走 stub fixture。P2 完成后改为：
        from agent.planner import run_planner
        async for ev in run_planner(req): yield _to_sse(ev)
    """
    return EventSourceResponse(
        _safe_stream(_stub_stream(req)),
        media_type="text/event-stream",
    )


@app.post("/chat/confirm")
async def chat_confirm(req: ChatConfirmRequest) -> EventSourceResponse:
    """MVP-2：用户确认后下发执行类 Tool。"""
    return EventSourceResponse(
        _safe_stream(_stub_confirm(req)),
        media_type="text/event-stream",
    )


# ============================================================
# SSE 包装与异常兜底
# ============================================================

def _to_sse(event: SseEvent) -> dict[str, Any]:
    """把 SseEvent 转成 sse-starlette 接受的 dict 形式。

    sse-starlette 约定每条事件含 event / id / data 三键。
    前端按 SseEvent.type 解析 payload。
    """
    return {
        "event": event.type.value,
        "id": str(event.seq),
        "data": event.model_dump_json(),
    }


async def _safe_stream(
    inner: AsyncIterator[SseEvent],
) -> AsyncIterator[dict[str, Any]]:
    """把内部 SseEvent 流转成 sse-starlette dict 流；中途异常 → stream_error + done。"""
    last_seq = -1
    try:
        async for ev in inner:
            last_seq = ev.seq
            yield _to_sse(ev)
    except asyncio.CancelledError:
        # 客户端断开：静默退出，不再推事件
        raise
    except Exception as e:  # noqa: BLE001
        err = SseEvent(
            type=SseEventType.STREAM_ERROR,
            seq=last_seq + 1,
            payload={"reason": "unexpected", "detail": f"{type(e).__name__}: {e}"},
            timestamp_ms=int(time.time() * 1000),
        )
        yield _to_sse(err)
        yield _to_sse(SseEvent(type=SseEventType.DONE, seq=last_seq + 2))


# ============================================================
# Stub fixture：家庭主场景完整 SSE 序列
# ============================================================

async def _delay(ms: int = 350) -> None:
    """让前端可见动画节奏——评委能看清每一步。"""
    await asyncio.sleep(ms / 1000.0)


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _stub_stream(req: ChatStreamRequest) -> AsyncIterator[SseEvent]:
    """对应 api_contract.md §2 示例事件序列（含 E1 异常 → 重规划 → 成功）。

    注意：当前固定家庭主场景输出。P2 接入真实 planner 后，按意图差异化。
    """
    seq = 0

    def emit(type_: SseEventType, payload: dict[str, Any]) -> SseEvent:
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload, timestamp_ms=_now_ms())
        seq += 1
        return ev

    # ---- 0: intent_parsed ----
    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[4, 6],
        distance_max_km=5,
        companions=[
            Companion(role="妻子", count=1),
            Companion(role="孩子", age=5, count=1),
        ],
        physical_constraints=["亲子友好", "适合 5-10 岁"],
        dietary_constraints=["低脂", "健康轻食"],
        experience_tags=[],
        social_context="家庭日常",
        raw_input=req.message,
        parse_confidence=0.88,
        ambiguous_fields=[],
    )
    yield emit(SseEventType.INTENT_PARSED, intent.model_dump())
    await _delay()

    # ---- 1-2: get_user_profile ----
    yield emit(
        SseEventType.TOOL_CALL_START,
        {"tool": "get_user_profile", "input": {"user_id": "demo_user"}},
    )
    await _delay(220)
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "get_user_profile",
            "output": {
                "success": True,
                "profile": {
                    "user_id": "demo_user",
                    "home_location": {"name": "西溪居住区"},
                    "default_budget": 300.0,
                    "transport_preference": "taxi",
                },
            },
            "duration_ms": 80,
        },
    )
    await _delay()

    # ---- 3-4: search_pois ----
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "search_pois",
            "input": {
                "distance_max_km": 5,
                "physical_constraints": ["亲子友好", "适合 5-10 岁"],
                "experience_tags": [],
                "social_context": "家庭日常",
                "age_in_party": [5],
            },
        },
    )
    await _delay(420)
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "search_pois",
            "output": {
                "success": True,
                "candidates": [
                    {"id": "P001", "name": "森林儿童探索乐园", "distance_km": 4.2, "rating": 4.6},
                    {"id": "P004", "name": "西溪亲子动物园", "distance_km": 3.5, "rating": 4.5},
                    {"id": "P007", "name": "童趣沙池公园", "distance_km": 2.8, "rating": 4.3},
                ],
            },
            "duration_ms": 120,
        },
    )
    await _delay()

    # ---- 5: agent_thought（流式打字效果可选）----
    yield emit(
        SseEventType.AGENT_THOUGHT,
        {"text": "命中 3 个亲子 POI，按距离与评分综合，优先「森林儿童探索乐园」。"},
    )
    await _delay(300)

    # ---- 6-7: search_restaurants ----
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "search_restaurants",
            "input": {
                "distance_max_km": 5,
                "dietary_constraints": ["低脂", "健康轻食"],
                "social_context": "家庭日常",
            },
        },
    )
    await _delay(420)
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "search_restaurants",
            "output": {
                "success": True,
                "candidates": [
                    {"id": "R001", "name": "轻语沙拉 · 西溪店", "distance_km": 2.1, "avg_price": 75},
                    {"id": "R005", "name": "绿野食光", "distance_km": 3.0, "avg_price": 88},
                ],
            },
            "duration_ms": 110,
        },
    )
    await _delay()

    # ---- 8-9: check_restaurant_availability 17:00 → 满（埋点 E1）----
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "check_restaurant_availability",
            "input": {"restaurant_id": "R001", "time": "17:00", "party_size": 3},
        },
    )
    await _delay(260)
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "check_restaurant_availability",
            "output": {
                "success": True,
                "restaurant_id": "R001",
                "time": "17:00",
                "available": False,
                "queue_minutes": 0,
                "suggested_alternative_time": "17:30",
            },
            "duration_ms": 60,
        },
    )
    await _delay()

    # ---- 10: replan_triggered（评委要看的异常韧性证据）----
    yield emit(
        SseEventType.REPLAN_TRIGGERED,
        {
            "reason": FailureReason.RESTAURANT_FULL.value,
            "from_tool": "check_restaurant_availability",
        },
    )
    await _delay(300)

    # ---- 11-12: 改约 17:30，成功 ----
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "check_restaurant_availability",
            "input": {"restaurant_id": "R001", "time": "17:30", "party_size": 3},
        },
    )
    await _delay(260)
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "check_restaurant_availability",
            "output": {
                "success": True,
                "restaurant_id": "R001",
                "time": "17:30",
                "available": True,
                "queue_minutes": 0,
            },
            "duration_ms": 55,
        },
    )
    await _delay()

    # ---- 13: itinerary_ready ----
    itinerary = Itinerary(
        summary="家庭半日方案 · 西溪亲子探索 + 健康晚餐",
        stages=[
            ItineraryStage(
                kind="出发",
                start="14:00",
                end="14:25",
                title="从家出发 · 打车前往西溪湿地",
                note="预估打车 25 分钟",
            ),
            ItineraryStage(
                kind="主活动",
                start="14:25",
                end="17:00",
                title="森林儿童探索乐园 · 亲子游玩",
                poi_id="P001",
                note="5 岁年龄段适配，户外低强度",
            ),
            ItineraryStage(
                kind="转场",
                start="17:00",
                end="17:30",
                title="步行 + 短途打车至轻语沙拉",
                note="步行 18 分钟，可慢慢走",
            ),
            ItineraryStage(
                kind="用餐",
                start="17:30",
                end="18:45",
                title="轻语沙拉 · 健康轻食晚餐",
                restaurant_id="R001",
                note="待你确认后为你预约 17:30 三人位",
            ),
            ItineraryStage(
                kind="返回",
                start="18:45",
                end="19:10",
                title="打车回家",
                note="预估 25 分钟",
            ),
        ],
        orders=[],
        share_message=None,
        total_minutes=310,
    )
    _SESSION_STORE[req.session_id] = {
        "intent": intent.model_dump(),
        "itinerary": itinerary.model_dump(),
    }
    yield emit(SseEventType.ITINERARY_READY, itinerary.model_dump())
    await _delay(150)

    # ---- 14: done ----
    yield emit(SseEventType.DONE, {})


async def _stub_confirm(req: ChatConfirmRequest) -> AsyncIterator[SseEvent]:
    """MVP-2 stub：confirm → reserve_restaurant + generate_share_message。"""
    seq = 0

    def emit(type_: SseEventType, payload: dict[str, Any]) -> SseEvent:
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload, timestamp_ms=_now_ms())
        seq += 1
        return ev

    if req.decision != "confirm":
        yield emit(
            SseEventType.AGENT_THOUGHT,
            {"text": f"已收到 {req.decision}，本次不执行预约。"},
        )
        yield emit(SseEventType.DONE, {})
        return

    # reserve_restaurant
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "reserve_restaurant",
            "input": {"restaurant_id": "R001", "time": "17:30", "party_size": 3},
        },
    )
    await _delay(320)
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "reserve_restaurant",
            "output": {
                "success": True,
                "order_id": "R20260516_001",
                "restaurant_id": "R001",
                "confirmed_time": "17:30",
                "confirmed_party_size": 3,
            },
            "duration_ms": 180,
        },
    )
    await _delay()

    # generate_share_message
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "generate_share_message",
            "input": {
                "itinerary_summary": "家庭半日方案 · 西溪亲子探索 + 健康晚餐",
                "social_context": "家庭日常",
                "audience": "妻子",
            },
        },
    )
    await _delay(420)
    share_msg = (
        "下午带宝贝去西溪森林儿童探索乐园玩 2 小时，17:30 已订好轻语沙拉的三人位，"
        "都是低脂健康餐你可以放心吃。打车 25 分钟到，玩完慢慢走过去就行～"
    )
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "generate_share_message",
            "output": {"success": True, "message": share_msg},
            "duration_ms": 220,
        },
    )
    await _delay()

    # 把订单与文案合并写回 itinerary 并再推一次 itinerary_ready
    cached = _SESSION_STORE.get(req.session_id, {})
    itin_dict = dict(cached.get("itinerary") or {})
    if itin_dict:
        itin_dict["orders"] = [
            {
                "order_id": "R20260516_001",
                "kind": "餐厅预约",
                "target_id": "R001",
                "target_name": "轻语沙拉 · 西溪店",
                "detail": "17:30 三人位",
            }
        ]
        itin_dict["share_message"] = share_msg
        _SESSION_STORE[req.session_id] = {**cached, "itinerary": itin_dict}
        yield emit(SseEventType.ITINERARY_READY, itin_dict)
        await _delay(140)

    yield emit(SseEventType.DONE, {})
