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

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from schemas import (
    Companion,
    IntentExtraction,
    Itinerary,
    ItineraryStage,
    RefinementInput,
    RefinementOutput,
    SseEvent,
    SseEventType,
    current_env_mode,
    resolve_planner_mode,
)
from schemas.errors import FailureReason


# ============================================================
# 配置
# ============================================================

VERSION = "0.1.0"
# 仅作 /health 显示用；解耦后真假 planner 由 _use_real_planner() 单独判断
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "").strip() or "openai-compatible"
CORS_ORIGINS_RAW = os.getenv("SHANGWUJU_CORS_ORIGINS", "http://localhost:3000")
CORS_ORIGINS = [o.strip() for o in CORS_ORIGINS_RAW.split(",") if o.strip()]


def _use_real_planner() -> bool:
    """是否启用真 planner 链路（意图解析 + plan_itinerary_with_mode）。

    解析顺序（优先级递减）：
    1. PLANNER_USE_REAL 显式开关（1/true/yes/on → 真，0/false/no/off → 假）
    2. LLM_PROVIDER=stub  → 假（开发/单测兼容）
    3. 有任意 LLM credential（LLM_API_KEY 或旧名 DEEPSEEK_API_KEY/QWEN_API_KEY）→ 真
    4. 默认 → 假（即纯 stub fixture，不调任何 LLM）
    """
    raw = os.getenv("PLANNER_USE_REAL")
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in ("1", "true", "yes", "on")

    explicit_provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit_provider == "stub":
        return False

    has_credential = bool(
        (os.getenv("LLM_API_KEY") or "").strip()
        or (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        or (os.getenv("QWEN_API_KEY") or "").strip()
    )
    return has_credential


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
    # Phase 0.7：可选；缺省时按 X-User-Id header > "demo_user" 兜底
    user_id: Optional[str] = Field(default=None, max_length=64)


class ChatConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1, max_length=128)
    decision: str = Field(..., pattern="^(confirm|reject|modify)$")
    modifications: Optional[dict[str, Any]] = None
    user_id: Optional[str] = Field(default=None, max_length=64)


def _resolve_user_id(
    body_user_id: Optional[str],
    header_user_id: Optional[str],
) -> str:
    """优先级：body.user_id > X-User-Id header > "demo_user"。"""
    for candidate in (body_user_id, header_user_id):
        if candidate and candidate.strip():
            return candidate.strip()
    return "demo_user"


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
    """健康检查 + 当前生效配置。

    `llm_provider` 与 `planner_real` 反映**当前真实**配置（解耦后由 base_url 自动推断 +
    _use_real_planner() 判断），不再被 .env 中是否显式设 LLM_PROVIDER 干扰。
    """
    # 推断真实 provider 展示名：stub 模式下显示 stub；否则由客户端工厂解析
    if (os.getenv("LLM_PROVIDER") or "").strip().lower() == "stub":
        provider_display = "stub"
    else:
        try:
            from agent.llm_client import _resolve_creds

            _, _, _, provider_display = _resolve_creds(None)
        except Exception:  # noqa: BLE001
            provider_display = "openai-compatible"
    return {
        "status": "ok",
        "version": VERSION,
        "llm_provider": provider_display,
        "planner_mode": current_env_mode(),
        "planner_real": "1" if _use_real_planner() else "0",
    }


@app.get("/scenarios")
def scenarios() -> dict[str, list[dict[str, str]]]:
    return {"scenarios": SCENARIOS}


# ============================================================
# Phase 0.7：persona / preferences 端点
# ============================================================


@app.get("/personas")
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

    return {
        "personas": [p.model_dump() for p in load_personas()],
    }


@app.get("/preferences/{user_id}")
def get_user_preferences(user_id: str) -> dict[str, Any]:
    """合并 persona + memory 给前端偏好面板用。"""
    from data.memory_store import compute_priors

    view = compute_priors(user_id)
    return view.model_dump()


@app.post("/preferences/{user_id}/reset")
def reset_user_preferences(user_id: str) -> dict[str, Any]:
    """清掉某 user 的累积 memory（演示完清场用）。"""
    from data.memory_store import reset_memory

    fresh = reset_memory(user_id)
    return {"status": "ok", "memory": fresh.model_dump()}


# ============================================================
# Memory 累积 helper（confirm/refine 路径调用）
# ============================================================


def _collect_itinerary_tags(itinerary_dict: dict[str, Any]) -> list[str]:
    """从已确认 itinerary 里抽出命中的 tag（用于 memory accept）。

    策略：
    - 主活动 POI 的 tags + suitable_for
    - 用餐餐厅的 tags + suitable_for
    - 去重；tag 词典外的不写入（防漂移）
    """
    from schemas.tags import (
        DIETARY_TAGS,
        EXPERIENCE_TAGS,
        PHYSICAL_TAGS,
        SOCIAL_CONTEXTS,
    )

    valid = PHYSICAL_TAGS | DIETARY_TAGS | EXPERIENCE_TAGS | SOCIAL_CONTEXTS

    out: set[str] = set()

    # 注：这里没有完整的 POI/Restaurant 对象，仅能从 stages 拿到 id；
    # demo 安全做法：从 mock_data 反查
    try:
        from data.loader import load_pois, load_restaurants

        pois_by_id = {p.id: p for p in load_pois()}
        rests_by_id = {r.id: r for r in load_restaurants()}
    except Exception:  # noqa: BLE001
        pois_by_id = {}
        rests_by_id = {}

    for stage in itinerary_dict.get("stages") or []:
        if stage.get("poi_id"):
            poi = pois_by_id.get(stage["poi_id"])
            if poi is not None:
                out.update(poi.tags or [])
                out.update(poi.suitable_for or [])
        if stage.get("restaurant_id"):
            rest = rests_by_id.get(stage["restaurant_id"])
            if rest is not None:
                out.update(rest.tags or [])
                out.update(rest.suitable_for or [])

    return [t for t in out if t in valid]


def _accumulate_memory_after_confirm(
    cached: dict[str, Any],
    itinerary_dict: dict[str, Any],
) -> None:
    """confirm 后：把 itinerary 命中的 tag 写进 user memory。

    cached 里的 user_id 由 _planner_stream 写入；缺失时跳过累积（不阻塞主流程）。
    """
    user_id = cached.get("user_id")
    if not user_id:
        return
    from data.memory_store import record_accepted

    tags = _collect_itinerary_tags(itinerary_dict)
    intent = cached.get("intent") or {}
    distance = intent.get("distance_max_km")
    try:
        record_accepted(
            user_id,
            tags=tags,
            distance_km=float(distance) if distance is not None else None,
        )
    except Exception:  # noqa: BLE001
        # 累积失败不阻塞主流程
        pass


def _accumulate_memory_after_refine(
    cached: dict[str, Any],
    rejected_tags: list[str],
) -> None:
    """refine 中如果反馈含「去掉 X」类的 tag，写进 user memory rejected。"""
    user_id = cached.get("user_id")
    if not user_id or not rejected_tags:
        return
    from data.memory_store import record_rejected

    try:
        record_rejected(user_id, tags=rejected_tags)
    except Exception:  # noqa: BLE001
        pass


@app.post("/chat/stream")
async def chat_stream(req: ChatStreamRequest, request: Request) -> EventSourceResponse:
    """主入口：一句话 → SSE 流式输出。

    解析 PLANNER_MODE：
        header X-Planner-Mode > env PLANNER_MODE > default("rule")
    解析 user_id（Phase 0.7）：
        body.user_id > X-User-Id header > "demo_user"

    分发：
        PLANNER_USE_REAL=1（或 LLM_PROVIDER 非 stub）→ 走真 planner（_planner_stream）
        否则                                          → 走 stub fixture（_stub_stream）
    """
    mode = resolve_planner_mode(
        header_value=request.headers.get("X-Planner-Mode"),
        env_value=os.getenv("PLANNER_MODE"),
    )
    user_id = _resolve_user_id(req.user_id, request.headers.get("X-User-Id"))
    if _use_real_planner():
        inner = _planner_stream(req, mode=mode, user_id=user_id)
    else:
        inner = _stub_stream(req)
    return EventSourceResponse(
        _safe_stream(inner),
        media_type="text/event-stream",
        headers={"X-Planner-Mode": mode, "X-User-Id": user_id},
    )


@app.post("/chat/confirm")
async def chat_confirm(req: ChatConfirmRequest, request: Request) -> EventSourceResponse:
    """MVP-2：用户确认后下发执行类 Tool。"""
    mode = resolve_planner_mode(
        header_value=request.headers.get("X-Planner-Mode"),
        env_value=os.getenv("PLANNER_MODE"),
    )
    return EventSourceResponse(
        _safe_stream(_stub_confirm(req)),
        media_type="text/event-stream",
        headers={"X-Planner-Mode": mode},
    )


@app.post("/chat/refine")
async def chat_refine(req: RefinementInput, request: Request) -> EventSourceResponse:
    """Phase 0.6：用户拒绝方案 + 反馈 → refiner 合并 → 重新规划。

    流程（详见 api_contract.md §7）：
        1. 从内存 session 取原 intent；不存在 → 422
        2. 推 refinement_start（含 feedback_text）
        3. 调 refiner（A 实现的 backend.agent.refiner.refine_intent；
           未实现时走 main.py 内置启发式 _stub_refine 兜底）
        4. 推 refinement_done（含 RefinementOutput）
        5. 复用 stub 主路径事件序列，但用 refined_intent 驱动（distance 等关键字段反映新值）
        6. done
    """
    cached = _SESSION_STORE.get(req.session_id)
    if cached is None:
        raise HTTPException(
            status_code=422,
            detail=f"session not found: {req.session_id}",
        )

    mode = resolve_planner_mode(
        header_value=request.headers.get("X-Planner-Mode"),
        env_value=os.getenv("PLANNER_MODE"),
    )

    if _use_real_planner():
        inner = _refine_stream_real(req, cached, mode=mode)
    else:
        inner = _refine_stream(req, cached)
    return EventSourceResponse(
        _safe_stream(inner),
        media_type="text/event-stream",
        headers={"X-Planner-Mode": mode},
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


async def _delay(ms: int = 350) -> None:
    """让前端可见动画节奏——评委能看清每一步。"""
    await asyncio.sleep(ms / 1000.0)


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _stub_stream(
    req: ChatStreamRequest,
    *,
    intent_override: Optional[IntentExtraction] = None,
    starting_seq: int = 0,
) -> AsyncIterator[SseEvent]:
    """对应 api_contract.md §2 示例事件序列（含 E1 异常 → 重规划 → 成功）。

    参数：
        intent_override: 若提供，跳过 fixture intent 直接用它；search_pois / search_restaurants
                         的 input 也会反映其 distance_max_km / 约束（用于 /chat/refine 复用）。
        starting_seq:    seq 起始值；refine 流复用主路径时 seq 从已经 emit 过的位置继续。

    注意：当前固定家庭主场景输出。P2 接入真实 planner 后，按意图差异化。
    """
    seq = starting_seq

    def emit(type_: SseEventType, payload: dict[str, Any]) -> SseEvent:
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload, timestamp_ms=_now_ms())
        seq += 1
        return ev

    # ---- 0: intent_parsed ----
    if intent_override is not None:
        intent = intent_override
    else:
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
    # 仅当走主路径（/chat/stream）时推 intent_parsed；refine 已经推过 refinement_done，
    # 不再重复推 intent_parsed 避免前端重置 IntentSummary
    if intent_override is None:
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
                "distance_max_km": intent.distance_max_km,
                "physical_constraints": list(intent.physical_constraints),
                "experience_tags": list(intent.experience_tags),
                "social_context": intent.social_context,
                "age_in_party": [c.age for c in intent.companions if c.age is not None] or None,
            },
        },
    )
    await _delay(420)
    # 候选按 distance ≤ intent.distance_max_km 过滤
    _all_pois = [
        {"id": "P001", "name": "森林儿童探索乐园", "distance_km": 4.2, "rating": 4.6},
        {"id": "P004", "name": "西溪亲子动物园", "distance_km": 3.5, "rating": 4.5},
        {"id": "P007", "name": "童趣沙池公园", "distance_km": 2.8, "rating": 4.3},
    ]
    _poi_candidates = [p for p in _all_pois if p["distance_km"] <= intent.distance_max_km] or _all_pois[-1:]
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "search_pois",
            "output": {
                "success": True,
                "candidates": _poi_candidates,
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
                "distance_max_km": intent.distance_max_km,
                "dietary_constraints": list(intent.dietary_constraints),
                "social_context": intent.social_context,
            },
        },
    )
    await _delay(420)
    _all_restaurants = [
        {"id": "R001", "name": "轻语沙拉 · 西溪店", "distance_km": 2.1, "avg_price": 75},
        {"id": "R005", "name": "绿野食光", "distance_km": 3.0, "avg_price": 88},
    ]
    _rest_candidates = [r for r in _all_restaurants if r["distance_km"] <= intent.distance_max_km] or _all_restaurants[:1]
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "search_restaurants",
            "output": {
                "success": True,
                "candidates": _rest_candidates,
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


# ============================================================
# Stub refiner：feedback_text → 修改 IntentExtraction
# ============================================================

# 距离关键词识别（中文 + 数字）→ km 数
_DISTANCE_KEYWORDS = ("公里以内", "km以内", "公里内", "km内", "公里以下", "公里")


def _extract_distance_km(text: str) -> Optional[float]:
    """从反馈文本里提 distance 上限（km）。

    支持「3 公里」「3公里以内」「3km 以内」「不超过 3 公里」。
    返回 None 表示文本无距离指示。
    """
    import re

    if not text:
        return None
    # 匹配 "数字 + 可选空白 + 单位"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(公里|km|千米)", text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:  # pragma: no cover
            return None
    return None


def _stub_refine(
    original: IntentExtraction, feedback_text: str
) -> RefinementOutput:
    """启发式 refiner（A 未实现 backend.agent.refiner 时的兜底）。

    规则：
    - "太远了" / "近一点" / "X 公里以内" → 缩小 distance_max_km
        - 显式数字优先；否则 distance × 0.6（向下取整到 0.5）
    - "不辣" / "清淡" → 加 dietary tag「不辣」
    - "便宜一点" / "贵一点" → 改 raw_input 提示，不改 schema 字段（避免 D9 越界）
    - 反馈空 → distance × 0.8 兜底（让用户感到 Agent 有响应）

    输出 RefinementOutput.refined_intent 必须仍合法（§5.7 D-SoT）；
    changed_fields 是中文摘要。
    """
    refined_data = original.model_dump()
    changes: list[str] = []

    txt = (feedback_text or "").strip()

    # ===== 距离调整 =====
    new_distance: Optional[float] = None
    if txt:
        explicit = _extract_distance_km(txt)
        if explicit is not None:
            new_distance = max(0.5, min(explicit, original.distance_max_km))
            if new_distance != original.distance_max_km:
                changes.append(
                    f"距离上限：{original.distance_max_km:g}km → {new_distance:g}km"
                )
        elif any(kw in txt for kw in ("太远", "近一点", "近点", "别走太远", "别太远")):
            scaled = round(original.distance_max_km * 0.6 * 2) / 2  # 取整到 0.5
            new_distance = max(0.5, scaled)
            if new_distance != original.distance_max_km:
                changes.append(
                    f"距离上限：{original.distance_max_km:g}km → {new_distance:g}km"
                )
    if new_distance is None and not txt:
        # 空反馈兜底：缩 0.8
        scaled = round(original.distance_max_km * 0.8 * 2) / 2
        if scaled != original.distance_max_km and scaled >= 0.5:
            new_distance = scaled
            changes.append(
                f"距离上限：{original.distance_max_km:g}km → {new_distance:g}km（兜底）"
            )
    if new_distance is not None:
        refined_data["distance_max_km"] = new_distance

    # ===== 饮食偏好叠加（仅命中词典内值）=====
    existing_dietary = set(refined_data.get("dietary_constraints") or [])
    if txt:
        if ("不辣" in txt or "清淡" in txt) and "不辣" not in existing_dietary:
            existing_dietary.add("不辣")
            changes.append("加忌口：不辣")
        if ("低脂" in txt or "减肥" in txt) and "低脂" not in existing_dietary:
            existing_dietary.add("低脂")
            changes.append("加忌口：低脂")
    refined_data["dietary_constraints"] = sorted(existing_dietary)

    # ===== 同行人语义增强（不改 schema 字段，仅写 raw_input 帮助下游 LLM）=====
    if txt:
        refined_data["raw_input"] = f"{original.raw_input}（用户反馈：{txt}）"

    # 重新校验（保证仍合法）
    refined = IntentExtraction.model_validate(refined_data)

    note: Optional[str] = None
    if changes:
        note = "已根据您的反馈调整：" + "；".join(changes)
    elif txt:
        note = "已记录您的反馈，本次维持原约束并重排候选。"
    else:
        note = "未收到具体反馈，本次自动收紧距离重排。"

    return RefinementOutput(
        refined_intent=refined,
        changed_fields=changes,
        refiner_note=note,
    )


async def _refine_stream(
    req: RefinementInput,
    cached: dict[str, Any],
) -> AsyncIterator[SseEvent]:
    """/chat/refine 完整 SSE 序列：refinement_start → refinement_done → 主路径事件。

    参考 api_contract.md §7。
    """
    seq = 0

    def emit(type_: SseEventType, payload: dict[str, Any]) -> SseEvent:
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload, timestamp_ms=_now_ms())
        seq += 1
        return ev

    # ---- 0: refinement_start ----
    yield emit(
        SseEventType.REFINEMENT_START,
        {"feedback_text": req.feedback_text or ""},
    )
    await _delay(180)

    # ---- 调 refiner（优先 A 实现，否则 _stub_refine）----
    original = IntentExtraction.model_validate(cached["intent"])
    refinement: RefinementOutput
    try:  # 预留：A 同学 commit refiner 后此分支生效
        from agent.refiner import refine_intent  # type: ignore[import-not-found]

        refinement = refine_intent(original, req.feedback_text or "")
    except Exception:  # noqa: BLE001 — 兜底覆盖 ImportError + 实现异常
        refinement = _stub_refine(original, req.feedback_text or "")

    # ---- 1: refinement_done ----
    yield emit(SseEventType.REFINEMENT_DONE, refinement.model_dump())
    await _delay(220)

    # ---- 2..N: 复用主路径事件序列（用 refined intent 驱动）----
    placeholder_req = ChatStreamRequest(
        message=refinement.refined_intent.raw_input,
        session_id=req.session_id,
    )
    async for ev in _stub_stream(
        placeholder_req,
        intent_override=refinement.refined_intent,
        starting_seq=seq,
    ):
        # 同步本地 seq 计数器到 stream 内部，保证后续 seq 单调（虽然 _stub_stream 自管，
        # 这里只需透传事件即可——它的 emit 会基于 starting_seq 累加）
        yield ev
        seq = ev.seq + 1


# ============================================================
# 真 planner 链路（PLANNER_USE_REAL=1 或 LLM_PROVIDER!=stub 启用）
# ============================================================

# Tracer 事件 type → SseEventType 映射
_TRACER_TO_SSE: dict[str, SseEventType] = {
    "intent_parsed": SseEventType.INTENT_PARSED,
    "tool_call_start": SseEventType.TOOL_CALL_START,
    "tool_call_end": SseEventType.TOOL_CALL_END,
    "replan_triggered": SseEventType.REPLAN_TRIGGERED,
    "agent_thought": SseEventType.AGENT_THOUGHT,
    "itinerary_ready": SseEventType.ITINERARY_READY,
    "stream_error": SseEventType.STREAM_ERROR,
}


def _tracer_to_events(tracer: Any, starting_seq: int = 0) -> list[SseEvent]:
    """把 Tracer 收集的内部事件转成 SseEvent 列表。

    未知 type 会被丢弃（不做兜底事件——避免误推）。
    """
    out: list[SseEvent] = []
    seq = starting_seq
    for record in tracer.records:
        sse_type = _TRACER_TO_SSE.get(record.type)
        if sse_type is None:
            continue
        out.append(
            SseEvent(
                type=sse_type,
                seq=seq,
                payload=dict(record.payload),
                timestamp_ms=record.timestamp_ms,
            )
        )
        seq += 1
    return out


async def _stream_tracer_events(
    events: list[SseEvent],
    *,
    delay_ms: int = 200,
) -> AsyncIterator[SseEvent]:
    """把 tracer 事件按节奏推给前端，让评委能看清每一步。"""
    for ev in events:
        yield ev
        await _delay(delay_ms)


def _intent_via_llm(message: str, *, user_id: str | None = None) -> IntentExtraction:
    """用真 LLM 客户端跑意图解析；任何失败 → 兜底家庭主场景 fixture。

    Phase 0.7：传 user_id 时 prompt 注入 persona/memory prior（"我是谁 + 学过什么"）。
    Demo 安全网：评委网络抖动或 API 限流时也能跑通。
    """
    from agent.intent_parser import parse_intent
    from agent.llm_client import get_llm_client

    try:
        client = get_llm_client()
        return parse_intent(message, client=client, user_id=user_id)
    except Exception:  # noqa: BLE001
        return IntentExtraction(
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
            raw_input=message,
            parse_confidence=0.6,
            ambiguous_fields=["llm_unavailable_fallback"],
        )


async def _planner_stream(
    req: ChatStreamRequest,
    *,
    mode: str,
    intent_override: Optional[IntentExtraction] = None,
    starting_seq: int = 0,
    user_id: str | None = None,
) -> AsyncIterator[SseEvent]:
    """真 planner 链路：意图解析 → plan_itinerary_with_mode → 实时推送 tracer 事件。

    Phase 0.7：传 user_id 时意图解析注入 persona/memory prior；
    最终 session 也把 user_id 一并存下，confirm/refine 路径可读到。

    实时推送策略（重要）：
        plan_itinerary_with_mode 在 LLM mode 下会跑 30-60s（多轮 LLM chat）。
        若同步等它跑完才 yield，前端 SSE 解析器会触发首字节超时。
        本函数把 plan 跑在 asyncio.to_thread 后台线程，主线程消费 Tracer 订阅
        emit 的事件，通过 asyncio.Queue 实时 yield 给客户端。

    与 _stub_stream 接口对齐；refine 链路也复用本流程（intent_override / starting_seq）。
    """
    import asyncio
    import threading

    from agent.planner import plan_itinerary_with_mode
    from agent.trace import TraceRecord, Tracer

    seq = starting_seq

    # ---- 意图解析判断（不立刻同步调 LLM，避免首字节超时）----
    if intent_override is not None:
        emit_intent_event = False
    else:
        emit_intent_event = True
        # 立刻发心跳：8s 首字节超时窗口内必须有字节
        yield SseEvent(
            type=SseEventType.AGENT_THOUGHT,
            seq=seq,
            payload={"text": "正在理解你的需求……"},
            timestamp_ms=_now_ms(),
        )
        seq += 1

    # ---- 准备 Tracer + 订阅队列 ----
    tracer = Tracer()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[TraceRecord | None] = asyncio.Queue()

    def _on_record(record: TraceRecord) -> None:
        # Tracer.emit 在 worker 线程触发；用 loop.call_soon_threadsafe 投入主线程队列
        loop.call_soon_threadsafe(queue.put_nowait, record)

    tracer.subscribe(_on_record)

    # ---- 后台线程：意图解析（如需要） + 跑真 planner ----
    plan_done = threading.Event()
    plan_result_holder: dict[str, Any] = {}

    def _run_plan() -> None:
        try:
            # 意图解析放后台线程，避免阻塞主线程导致首字节超时
            if intent_override is not None:
                intent = intent_override
            else:
                intent = _intent_via_llm(req.message, user_id=user_id)
                # 立刻 emit intent_parsed，让前端尽快看到结果
                tracer.emit("intent_parsed", intent.model_dump())
            plan_result_holder["intent"] = intent
            result = plan_itinerary_with_mode(intent, mode, tracer=tracer)
            plan_result_holder["result"] = result
        except Exception as e:  # noqa: BLE001
            plan_result_holder["error"] = e
        finally:
            plan_done.set()
            # 推一个 None sentinel 唤醒主消费循环（防止 queue.get() 永久阻塞）
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_run_plan, daemon=True).start()

    # ---- 主循环：消费队列 → yield SSE ----
    seen_intent_parsed = False

    async def _drain_until_done() -> AsyncIterator[SseEvent]:
        nonlocal seq, seen_intent_parsed
        while True:
            record = await queue.get()
            if record is None:
                # plan 已结束，把剩余队列内容也清干净
                while not queue.empty():
                    extra = queue.get_nowait()
                    if extra is None:
                        continue
                    ev = _record_to_sse(extra, seq, seen_intent_parsed, emit_intent_event)
                    if ev is not None:
                        seq += 1
                        if ev.type == SseEventType.INTENT_PARSED:
                            seen_intent_parsed = True
                        yield ev
                return
            ev = _record_to_sse(record, seq, seen_intent_parsed, emit_intent_event)
            if ev is None:
                continue
            seq += 1
            if ev.type == SseEventType.INTENT_PARSED:
                seen_intent_parsed = True
            yield ev

    async for ev in _drain_until_done():
        yield ev

    # ---- 等后台线程收尾（轻量，因为 sentinel 已发）----
    plan_done.wait(timeout=2)
    if "error" in plan_result_holder:
        # 意外异常：推 stream_error
        err = plan_result_holder["error"]
        yield SseEvent(
            type=SseEventType.STREAM_ERROR,
            seq=seq,
            payload={
                "reason": "planner_failed",
                "detail": f"{type(err).__name__}: {err}",
            },
        )
        seq += 1

    # ---- 写 session ----
    intent = plan_result_holder.get("intent")
    result = plan_result_holder.get("result")
    if intent is not None and result is not None and result.itinerary is not None:
        _SESSION_STORE[req.session_id] = {
            "intent": intent.model_dump(),
            "itinerary": result.itinerary.model_dump(),
            "user_id": user_id or "demo_user",
        }

    # ---- 推 done ----
    yield SseEvent(type=SseEventType.DONE, seq=seq, payload={})


def _record_to_sse(
    record: Any,
    seq: int,
    seen_intent_parsed: bool,
    emit_intent_event: bool,
) -> Optional[SseEvent]:
    """单条 TraceRecord → SseEvent；refine 链路要跳过 INTENT_PARSED。"""
    sse_type = _TRACER_TO_SSE.get(record.type)
    if sse_type is None:
        return None
    if sse_type == SseEventType.INTENT_PARSED:
        if not emit_intent_event or seen_intent_parsed:
            return None
    return SseEvent(
        type=sse_type,
        seq=seq,
        payload=dict(record.payload),
        timestamp_ms=record.timestamp_ms,
    )


async def _refine_stream_real(
    req: RefinementInput,
    cached: dict[str, Any],
    *,
    mode: str,
) -> AsyncIterator[SseEvent]:
    """/chat/refine 真链路：refiner 合并 → plan_itinerary_with_mode 重算。

    事件序列（同 stub 版）：refinement_start → refinement_done → 主路径 → done
    """
    seq = 0

    def emit(type_: SseEventType, payload: dict[str, Any]) -> SseEvent:
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload, timestamp_ms=_now_ms())
        seq += 1
        return ev

    # ---- 0: refinement_start ----
    yield emit(
        SseEventType.REFINEMENT_START,
        {"feedback_text": req.feedback_text or ""},
    )
    await _delay(180)

    # ---- 调真 refiner（A 实现）----
    original = IntentExtraction.model_validate(cached["intent"])
    try:
        from agent.refiner import refine_intent

        refinement = refine_intent(original, req.feedback_text or "")
    except Exception:  # noqa: BLE001 — 防 LLM 抖动；走 stub refiner 兜底
        refinement = _stub_refine(original, req.feedback_text or "")

    # Phase 0.7：累积 memory rejected（推断 user 拒绝的 tag）
    refined = refinement.refined_intent
    rejected_tags: list[str] = []
    rejected_tags.extend(set(original.dietary_constraints) - set(refined.dietary_constraints))
    rejected_tags.extend(set(original.experience_tags) - set(refined.experience_tags))
    rejected_tags.extend(set(original.physical_constraints) - set(refined.physical_constraints))
    if rejected_tags:
        _accumulate_memory_after_refine(cached, rejected_tags)

    # ---- 1: refinement_done ----
    yield emit(SseEventType.REFINEMENT_DONE, refinement.model_dump())
    await _delay(220)

    # ---- 2..N: 真 planner 重跑 ----
    user_id = cached.get("user_id")
    placeholder_req = ChatStreamRequest(
        message=refinement.refined_intent.raw_input,
        session_id=req.session_id,
        user_id=user_id,
    )
    async for ev in _planner_stream(
        placeholder_req,
        mode=mode,
        intent_override=refinement.refined_intent,
        starting_seq=seq,
        user_id=user_id,
    ):
        yield ev
        seq = ev.seq + 1


# ============================================================
# Stub fixture：confirm 流
# ============================================================


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
        # Phase 0.7：confirm 累积 memory（记录 itinerary 命中的所有 tag）
        _accumulate_memory_after_confirm(cached, itin_dict)
        yield emit(SseEventType.ITINERARY_READY, itin_dict)
        await _delay(140)

    yield emit(SseEventType.DONE, {})
