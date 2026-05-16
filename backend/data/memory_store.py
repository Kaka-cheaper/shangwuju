"""data.memory_store —— Persona 加载 + UserMemory 累积/读写。

存储策略（demo 级）：
- Persona：从 mock_data/personas.json 加载，进程内缓存
- Memory ：默认进程内字典；可通过 SHANGWUJU_MEMORY_DIR 环境变量启用磁盘持久化
            每 user 一个 JSON 文件（user_id.json），写入是 best-effort（异常不传播）

线程安全：用 threading.Lock 保护 _MEMORY_CACHE 写入（main.py 把 plan 跑在线程，
        confirm/refine 也可能在 worker 线程触发更新）。

D9 边界：本模块只负责持久化，**不**做 LLM 提示词拼装；compute_priors 输出
        UserPreferenceView 给 intent_parser 消费，prompt 注入文本由 prompts/ 写。

不负责：
- prompt 注入文案（在 backend/agent/prompts/）
- 业务侧累积时机（在 main.py confirm/refine 路径）
"""

from __future__ import annotations

import json
import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

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
# Memory 存储
# ============================================================

_MEMORY_CACHE: dict[str, UserMemory] = {}
_LOCK = threading.Lock()


def _memory_dir() -> Optional[Path]:
    raw = os.getenv("SHANGWUJU_MEMORY_DIR")
    if not raw:
        return None
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _disk_path(user_id: str) -> Optional[Path]:
    d = _memory_dir()
    if d is None:
        return None
    safe = user_id.replace("/", "_").replace("\\", "_")
    return d / f"{safe}.json"


def _load_from_disk(user_id: str) -> Optional[UserMemory]:
    p = _disk_path(user_id)
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


def get_memory(user_id: str) -> UserMemory:
    """读 memory；缓存未命中时尝试磁盘加载，失败兜底空 memory。"""
    with _LOCK:
        cached = _MEMORY_CACHE.get(user_id)
        if cached is not None:
            return cached
        loaded = _load_from_disk(user_id)
        if loaded is None:
            loaded = UserMemory(user_id=user_id)
        _MEMORY_CACHE[user_id] = loaded
        return loaded


def reset_memory(user_id: str) -> UserMemory:
    """重置 memory（演示完清场用）。"""
    fresh = UserMemory(user_id=user_id)
    with _LOCK:
        _MEMORY_CACHE[user_id] = fresh
    _save_to_disk(fresh)
    return fresh


def reset_all_memory() -> None:
    """清掉所有 user 的内存与磁盘缓存（测试 fixture 用）。"""
    with _LOCK:
        _MEMORY_CACHE.clear()
    d = _memory_dir()
    if d is not None and d.exists():
        for f in d.glob("*.json"):
            try:
                f.unlink()
            except OSError:
                pass


# ============================================================
# Memory 更新（业务侧调用）
# ============================================================

def record_accepted(user_id: str, *, tags: list[str], distance_km: float | None = None) -> UserMemory:
    """confirm 后调用：把 itinerary 命中的 tag 累计到 accepted。"""
    with _LOCK:
        memory = _MEMORY_CACHE.get(user_id) or _load_from_disk(user_id) or UserMemory(user_id=user_id)
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
        _MEMORY_CACHE[user_id] = memory
    _save_to_disk(memory)
    return memory


def record_rejected(user_id: str, *, tags: list[str]) -> UserMemory:
    """refine 中如果反馈含「去掉 X」的 changed_fields，调用此函数扣分。"""
    with _LOCK:
        memory = _MEMORY_CACHE.get(user_id) or _load_from_disk(user_id) or UserMemory(user_id=user_id)
        for tag in tags:
            if not tag:
                continue
            memory.rejected_tags.bump(tag, +1)
            if memory.accepted_tags.counts.get(tag, 0) > 0:
                memory.accepted_tags.bump(tag, -1)
        memory.last_updated_ms = int(time.time() * 1000)
        _MEMORY_CACHE[user_id] = memory
    _save_to_disk(memory)
    return memory


# ============================================================
# 合并视图（intent_parser + 前端面板都用）
# ============================================================

# 评分计算公式：final = persona_weight * PERSONA_WEIGHT + memory_count * MEMORY_WEIGHT
PERSONA_WEIGHT = 0.3
MEMORY_WEIGHT = 0.7
PERSONA_DEFAULT_TAG_BASE = 3  # persona 自带 tag 的基础权重（相当于已被点过 3 次）
TOP_PRIORS_N = 5


def compute_priors(user_id: str) -> UserPreferenceView:
    """合并 persona + memory，输出 top tag 与建议距离。"""
    persona = get_persona(user_id) or get_default_persona()
    memory = get_memory(user_id)

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
