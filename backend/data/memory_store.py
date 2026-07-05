"""data.memory_store —— Persona 模板加载 + 会话私有累积记忆读写。

【身份边界（记忆身份读写分离批，ADR-0015 身份边界补充决策，2026-07-05）】
demo 无账号体系，**会话即身份**；两类数据的键语义严格分开：

- **模板（读，共享只读）**：persona（mock_data/personas.json 里策划好的画像，
  label / 默认 tag / 预算距离先验）按 user_id / persona id 键控——前端
  onboarding 选画像 → X-User-Id 的链路价值保留，多访客共用同一模板无副作用
  （模板永不被运行时改写）。
- **累积（写，会话私有）**：确认行程产生的一切积累——偏好标签
  （accepted/rejected）、距离史、访问史、路径偏好（UserMemory），以及行程
  档案（recent_trips）——一律按 **session_id** 键控，进程内存储（与
  SESSION_STORE 同生命周期语义）。A 访客确认的行程绝不进入 B 访客的叙事。

生产迁移 = 把键从会话 ID 换成账号 ID，机制不动。

存储策略（demo 级）：
- Persona：从 mock_data/personas.json 加载，进程内缓存
- Memory ：默认进程内字典；可通过 SHANGWUJU_MEMORY_DIR 环境变量启用磁盘持久化
            每键一个 JSON 文件（<session_key>.json），写入是 best-effort（异常不传播）
- recent_trips：纯进程内（demo 姿态；无文件写 → 无并发写文件竞态）

线程安全：用 threading.Lock 保护 _MEMORY_CACHE / _TRIPS_CACHE 写入（main.py 把
        plan 跑在线程，confirm/refine 也可能在 worker 线程触发更新）。

D9 边界：本模块只负责持久化，**不**做 LLM 提示词拼装；compute_priors 输出
        UserPreferenceView 给 intent_parser 消费，prompt 注入文本由 prompts/ 写。

不负责：
- prompt 注入文案（在 backend/agent/prompts/）
- 业务侧累积时机（在 confirm 路径：api/_streams/graph_confirm.py 两轨后台任务）
- 行程摘要生成 / 脱敏（在 agent/planning/memory_writer.py）
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from schemas.domain import RecentTrip
from schemas.persona import (
    Persona,
    PersonaDefaultTags,
    TagCounter,
    UserMemory,
    UserPreferenceView,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PERSONAS_FILE = _REPO_ROOT / "mock_data" / "personas.json"


# ============================================================
# Persona 加载
# ============================================================

@lru_cache(maxsize=1)
def load_personas() -> list[Persona]:
    """加载所有 persona。失败兜底为空列表（不阻塞主流程）。"""
    path = Path(os.getenv("SHANGWUJU_PERSONAS_FILE", str(_DEFAULT_PERSONAS_FILE)))
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [Persona.model_validate(item) for item in raw]
    except Exception:  # noqa: BLE001
        return []


def get_persona(user_id: str) -> Optional[Persona]:
    """按 user_id 找 persona；找不到返回 None（前端切到默认 demo_user）。

    向后兼容：user_id="demo_user" 视为 alias 指向默认 persona（u_dad）。
    """
    # 兼容旧 demo_user：保持 W1 既有测试与 SSE 默认 user_id 不破
    if user_id == "demo_user":
        for p in load_personas():
            if p.user_id == "u_dad":
                return p
    for p in load_personas():
        if p.user_id == user_id:
            return p
    return None


def get_default_persona() -> Persona:
    """前端没指定 user 时的兜底（u_dad 家庭主线，演示主路径）。"""
    p = get_persona("u_dad")
    if p is not None:
        return p
    # 极端兜底：personas.json 也读不到时用硬编码
    return Persona(
        user_id="demo_user",
        label="默认用户",
        icon="👤",
        notes="无显式画像，全部按用户输入抽取",
        home_location="（未设置）",
        default_distance_max_km=5.0,
        default_budget=300.0,
        default_tags=PersonaDefaultTags(),
    )


# ============================================================
# Memory 存储（累积侧：键 = session_id，会话私有）
# ============================================================
#
# 键语义说明：下面所有 `session_key` 形参就是会话 ID（demo「会话即身份」）。
# UserMemory.user_id 字段承载的也是这个键（schema 字段名保持向后兼容，
# 语义见 schemas/persona.py::UserMemory docstring）。

_MEMORY_CACHE: dict[str, UserMemory] = {}
_LOCK = threading.Lock()


def _memory_dir() -> Optional[Path]:
    raw = os.getenv("SHANGWUJU_MEMORY_DIR")
    if not raw:
        return None
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _disk_path(session_key: str) -> Optional[Path]:
    d = _memory_dir()
    if d is None:
        return None
    safe = session_key.replace("/", "_").replace("\\", "_")
    return d / f"{safe}.json"


def _load_from_disk(session_key: str) -> Optional[UserMemory]:
    p = _disk_path(session_key)
    if p is None or not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return UserMemory.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def _save_to_disk(memory: UserMemory) -> None:
    p = _disk_path(memory.user_id)
    if p is None:
        return
    try:
        p.write_text(
            json.dumps(memory.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def get_memory(session_key: str) -> UserMemory:
    """读会话私有 memory；缓存未命中时尝试磁盘加载，失败兜底空 memory。"""
    with _LOCK:
        cached = _MEMORY_CACHE.get(session_key)
        if cached is not None:
            return cached
        loaded = _load_from_disk(session_key)
        if loaded is None:
            loaded = UserMemory(user_id=session_key)
        _MEMORY_CACHE[session_key] = loaded
        return loaded


def reset_memory(session_key: str) -> UserMemory:
    """重置某个键的 memory + 行程档案（演示完清场用）。"""
    fresh = UserMemory(user_id=session_key)
    with _LOCK:
        _MEMORY_CACHE[session_key] = fresh
        _TRIPS_CACHE.pop(session_key, None)
    _save_to_disk(fresh)
    return fresh


def reset_all_memory() -> None:
    """清掉所有键的内存 / 行程档案与磁盘缓存（测试 fixture 用）。"""
    with _LOCK:
        _MEMORY_CACHE.clear()
        _TRIPS_CACHE.clear()
    d = _memory_dir()
    if d is not None and d.exists():
        for f in d.glob("*.json"):
            try:
                f.unlink()
            except OSError:
                pass


# ============================================================
# Memory 更新（业务侧调用；键 = session_id）
# ============================================================

def record_accepted(session_key: str, *, tags: list[str], distance_km: float | None = None) -> UserMemory:
    """confirm 后调用：把 itinerary 命中的 tag 累计到本会话的 accepted。"""
    with _LOCK:
        memory = _MEMORY_CACHE.get(session_key) or _load_from_disk(session_key) or UserMemory(user_id=session_key)
        for tag in tags:
            if not tag:
                continue
            memory.accepted_tags.bump(tag, +1)
            # 同时把曾被拒过的 tag 计数 -1（用户改主意）
            if memory.rejected_tags.counts.get(tag, 0) > 0:
                memory.rejected_tags.bump(tag, -1)
        if distance_km is not None and distance_km >= 0:
            memory.distance_history.append(float(distance_km))
            # 最多保留最近 20 次
            memory.distance_history = memory.distance_history[-20:]
        memory.last_updated_ms = int(time.time() * 1000)
        _MEMORY_CACHE[session_key] = memory
    _save_to_disk(memory)
    return memory


def record_rejected(session_key: str, *, tags: list[str]) -> UserMemory:
    """refine 中如果反馈含「去掉 X」的 changed_fields，调用此函数扣分。"""
    with _LOCK:
        memory = _MEMORY_CACHE.get(session_key) or _load_from_disk(session_key) or UserMemory(user_id=session_key)
        for tag in tags:
            if not tag:
                continue
            memory.rejected_tags.bump(tag, +1)
            if memory.accepted_tags.counts.get(tag, 0) > 0:
                memory.accepted_tags.bump(tag, -1)
        memory.last_updated_ms = int(time.time() * 1000)
        _MEMORY_CACHE[session_key] = memory
    _save_to_disk(memory)
    return memory


# ============================================================
# Step 7：visited / preferred_routes 累积（键 = session_id）
# ============================================================

def record_visited(
    session_key: str,
    *,
    visits: list[tuple[str, str]],  # [(target_id, target_kind), ...]
    cooldown_days: int = 30,
) -> UserMemory:
    """confirm 后调：把本次 itinerary 中的 POI / 餐厅 id 累计到本会话 visited_targets。

    Args:
        session_key: 会话 id（会话即身份）
        visits: [(target_id, target_kind), ...]，target_kind ∈ {poi, restaurant}
        cooldown_days: 冷却期（天）；这段时间内 search 工具会排除该 target

    Returns:
        更新后的 UserMemory
    """
    from schemas.persona import VisitedRecord

    now_ms = int(time.time() * 1000)
    with _LOCK:
        memory = _MEMORY_CACHE.get(session_key) or _load_from_disk(session_key) or UserMemory(user_id=session_key)
        for tid, tkind in visits:
            if not tid or not tkind:
                continue
            memory.visited_targets.append(
                VisitedRecord(
                    target_id=tid,
                    target_kind=tkind,
                    visited_at_ms=now_ms,
                    cooldown_days=cooldown_days,
                )
            )
        # 限制单键历史最多 200 条（避免无限增长）
        if len(memory.visited_targets) > 200:
            memory.visited_targets = memory.visited_targets[-200:]
        memory.last_updated_ms = now_ms
        _MEMORY_CACHE[session_key] = memory
    _save_to_disk(memory)
    return memory


def record_preferred_route(
    session_key: str, *, segments: list[tuple[str, str]]
) -> UserMemory:
    """confirm 后调：相邻段成对录入「(from_id, to_id) → +1」。

    Args:
        segments: [(from_id, to_id), ...]，按 itinerary 段顺序排
    """
    with _LOCK:
        memory = _MEMORY_CACHE.get(session_key) or _load_from_disk(session_key) or UserMemory(user_id=session_key)
        for from_id, to_id in segments:
            if not from_id or not to_id or from_id == to_id:
                continue
            key = f"{from_id}|{to_id}"
            memory.preferred_routes[key] = int(
                memory.preferred_routes.get(key, 0)
            ) + 1
        memory.last_updated_ms = int(time.time() * 1000)
        _MEMORY_CACHE[session_key] = memory
    _save_to_disk(memory)
    return memory


# ============================================================
# 行程档案（recent_trips）—— 会话私有累积
# ============================================================
#
# 原状：memory_writer 把 recent_trips 写全局单文件 user_profile.json（读-改-写
# 一个共享文件）；读写分离批把它收编进本模块的会话私有存储——
# user_profile.json 从此是只读模板（dietary_preference / home_location），
# 运行时零文件写入，并发写文件竞态随之消失。
# 策略（5 条上限 / social_context + 5 分钟幂等窗）原样保留，在写入点原子执行。
# social_context_history（旧档案的伴生字段）无任何读者，随文件写路径一并退役。

_TRIPS_CACHE: dict[str, list[RecentTrip]] = {}
_TRIPS_MAX = 5
_TRIPS_DEDUP_WINDOW_SECONDS = 5 * 60


def _parse_trip_ts(ts_str: str) -> Optional[datetime]:
    """兼容 "...Z" / "...+00:00" / naive（按 UTC 补齐）。"""
    try:
        if ts_str.endswith("Z"):
            return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        ts = datetime.fromisoformat(ts_str)
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def get_recent_trips(session_key: str) -> list[RecentTrip]:
    """读本会话的行程档案（LIFO，最新在头；最多 5 条）。"""
    with _LOCK:
        return list(_TRIPS_CACHE.get(session_key) or [])


def record_recent_trip(session_key: str, trip: RecentTrip) -> bool:
    """confirm 后调：把一条（已脱敏的）行程档案写进本会话。

    原子执行三条策略（与旧 memory_writer 文件写路径语义一致，键变机制不动）：
    - 幂等：同 social_context 且时间差 < 5 分钟 → 跳过返 False
    - LIFO：最新在头
    - 上限：只保留最近 5 条

    Returns:
        True=写入；False=幂等窗内重复被跳过。
    """
    now = _parse_trip_ts(trip.timestamp)
    with _LOCK:
        trips = _TRIPS_CACHE.setdefault(session_key, [])
        if now is not None:
            for existing in trips:
                if existing.social_context != trip.social_context:
                    continue
                ts = _parse_trip_ts(existing.timestamp)
                if ts is None:
                    continue
                if abs((now - ts).total_seconds()) < _TRIPS_DEDUP_WINDOW_SECONDS:
                    return False
        trips.insert(0, trip)
        del trips[_TRIPS_MAX:]
    return True


# ============================================================
# 合并视图（intent_parser + persona_qa + 前端面板都用）
# ============================================================

# 评分计算公式：final = persona_weight * PERSONA_WEIGHT + memory_count * MEMORY_WEIGHT
PERSONA_WEIGHT = 0.3
MEMORY_WEIGHT = 0.7
PERSONA_DEFAULT_TAG_BASE = 3  # persona 自带 tag 的基础权重（相当于已被点过 3 次）
TOP_PRIORS_N = 5


def compute_priors(
    user_id: str, session_id: Optional[str] = None
) -> UserPreferenceView:
    """双键合并 persona 模板 + 会话累积，输出 top tag 与建议距离。

    键语义（读写分离批）：
    - `user_id`：模板键——persona label / 默认 tag / 默认距离（共享只读）。
    - `session_id`：累积键——本会话确认攒下的偏好 / 距离史。缺省（None）时
      返回**纯模板视图**（零累积）——供无会话上下文的调用方（如
      GET /preferences/{user_id}）诚实展示"模板长什么样"，绝不混入任何
      别的会话的累积。
    """
    persona = get_persona(user_id) or get_default_persona()
    memory = get_memory(session_id) if session_id else UserMemory(user_id="")

    # 合并打分
    score: dict[str, float] = {}
    # persona 自带 tag → base 权重
    persona_tags: list[str] = []
    persona_tags.extend(persona.default_tags.physical)
    persona_tags.extend(persona.default_tags.dietary)
    persona_tags.extend(persona.default_tags.experience)
    for t in persona_tags:
        score[t] = score.get(t, 0.0) + PERSONA_DEFAULT_TAG_BASE * PERSONA_WEIGHT
    # memory accepted +
    for t, n in memory.accepted_tags.counts.items():
        score[t] = score.get(t, 0.0) + n * MEMORY_WEIGHT
    # memory rejected -（强惩罚，让用户拒过的 tag 即使 persona 默认有也不进 top）
    for t, n in memory.rejected_tags.counts.items():
        if n > 0:
            score[t] = score.get(t, 0.0) - n * MEMORY_WEIGHT * 1.5

    sorted_tags = sorted(score.items(), key=lambda kv: kv[1], reverse=True)
    top_priors = [t for t, s in sorted_tags if s > 0][:TOP_PRIORS_N]

    suggested = memory.median_distance()
    if suggested is None:
        suggested = persona.default_distance_max_km

    return UserPreferenceView(
        persona=persona,
        memory=memory,
        top_priors=top_priors,
        suggested_distance_max_km=suggested,
    )
