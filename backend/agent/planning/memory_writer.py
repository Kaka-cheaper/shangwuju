"""agent.planning.memory_writer —— 用户画像记忆回写（TravelAgent / TriFlow 范式）。

【为什么需要这一层】

TravelAgent (NeurIPS'24) / TriFlow 的关键发现：把每次行程结果写回用户画像的
`recent_trips` 字段，下次意图解析阶段把匹配 social_context 的最新 1 条注入 prompt
（"用户上次「家庭」场景的行程：{summary}"），LLM 输出明显贴合用户偏好。

【设计纪律（spec algorithm-redesign R5）】

- **永不阻断主流程**：失败 / cancel / 文件锁竞争 → log warning，不抛异常
- **隐私脱敏**：summary 由 LLM 生成时 prompt 显式约束「不出现具体年龄数字
  （5 岁 → 学龄前儿童）/ 不出现具体地址 / 经纬度」
- **幂等键**：social_context + 5 分钟 timestamp 窗口（同 session 重复不追加）
- **跨平台兼容**：用 `threading.Lock`（不依赖 Unix 专属的 fcntl）
- **5 条上限**：写入时丢弃 5 条之外的旧记录（保 LIFO 顺序）

【触发点：confirm 路径，绑定下单动作而非方案就绪】

触发者是 `graph/nodes/execute_finalize.py:_persist_memory_side_effect`（用户确认后的
下单/购票/加购执行节点），在执行成功后作为副作用调 `persist_memory(state)`。
失败时 try/except 包裹不阻断 execute_finalize 主输出。

语义关键：记忆绑定的是用户「确认下单」这一动作，**不是 plan-ready（方案就绪）**——
方案没确认就持久化偏好是错误的产品语义。早期版本曾挂在 narrate_node（方案就绪即写），
后迁到 confirm 路径；narrate 节点现仅算 pending_actions、不再触发记忆回写
（交叉引用 `graph/nodes/narrate.py:226`）。

不负责：
- 跨 session 持久化数据库（mock_data 是 demo 用，未来 Postgres 替换）
- 多用户冲突（demo 仅 demo_user 单用户，加 lock 是防同 session 并发副作用）
- 复杂 RAG 检索（单 session 召回最近 1-2 条已足够；不引入 vector 索引）
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agent.core.llm_client import LLMMessage
from data.loader import load_user_profile
from schemas.domain import RecentTrip, UserProfile
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

_MAX_RECENT_TRIPS = 5
_DEDUP_WINDOW_SECONDS = 5 * 60  # 5 分钟去重窗口

# 跨平台进程内文件锁（同一 session 多个 narrate 调用不冲突）
_FILE_LOCK = threading.Lock()

# 摘要 prompt（脱敏由 prompt 显式约束 + LLM 自觉）
_SUMMARIZE_PROMPT = """你是一个本地生活行程摘要助手。用 1-2 句中文描述这次行程，
让用户下次回顾时能快速理解。

【脱敏要求（必须严格遵守）】
- ❌ 不出现具体年龄数字（如「5 岁」），用「学龄前儿童」/「学童」/「老人」/「成年朋友」
- ❌ 不出现具体地址 / 经纬度
- ❌ 不出现具体景点 / 餐厅名（只描述类型，如「亲子博物馆」/「健康轻食餐厅」）
- ✓ 描述场景（家庭日常 / 情侣约会等）+ 整体节奏（轻松 / 紧凑）+ 主活动类型

【输出长度】50-150 字
【格式】纯中文文本，无 JSON / Markdown
"""


# ============================================================
# 主接口
# ============================================================


def persist_memory(
    state: dict[str, Any],
    *,
    profile_path: Path | None = None,
    client: Any | None = None,
) -> bool:
    """把当前行程结果回写到 user_profile.json 的 recent_trips 字段。

    Args:
        state: AgentState dict（含 intent / itinerary / user_decision 等）
        profile_path: user_profile.json 路径；None 时从 mock_data 默认位置加载
        client: LLMClient 用于生成 summary；None 时懒加载 + 摘要兜底

    Returns:
        True 表示成功写入；False 表示跳过 / 失败（永不抛异常）

    设计纪律：
    - 永不抛异常：任何错误都 catch 后 return False
    - cancel / 失败方案不写入：state.get("user_decision") in {None, "cancel"} 时跳过
    - 5 分钟幂等：同 session social_context + timestamp 窗口内重复不追加
    """
    try:
        return _persist_memory_impl(state, profile_path=profile_path, client=client)
    except Exception as exc:
        logger.warning("memory_writer: persist_memory failed: %s", exc)
        return False


def _persist_memory_impl(
    state: dict[str, Any],
    *,
    profile_path: Path | None,
    client: Any | None,
) -> bool:
    intent = state.get("intent")
    itinerary = state.get("itinerary")

    if intent is None or itinerary is None:
        return False

    # cancel / 未确认 / 失败方案不写入
    user_decision = state.get("user_decision")
    success = bool(user_decision == "confirm")

    # 不强制要求 user_decision == "confirm" —— narrate 阶段可能在 confirm 之前调用；
    # 我们用 success=False 标记尚未下单的草案，让 recent_trips 也能记录草稿
    # （但 cancel 跳过 ）
    if user_decision == "cancel":
        return False

    social_context = getattr(intent, "social_context", "") or ""
    if not social_context:
        # social_context 是召回 key，缺失则没有意义
        return False

    # 1. 加载 profile
    profile_path = _resolve_profile_path(profile_path)
    profile = _load_profile_safe(profile_path)
    if profile is None:
        return False

    # 2. 检查幂等（5 分钟窗口）
    now_iso = _now_iso()
    if _is_duplicate(profile.recent_trips or [], social_context, now_iso):
        logger.info("memory_writer: skip duplicate within 5min window")
        return False

    # 3. 生成脱敏 summary
    summary = _summarize_trip(itinerary, intent, client=client)
    if not summary:
        return False

    # 4. 构造 RecentTrip + 限 5 条上限
    new_trip = RecentTrip(
        timestamp=now_iso,
        social_context=social_context,
        summary=summary,
        success=success,
    )
    existing = list(profile.recent_trips or [])
    existing.insert(0, new_trip)  # LIFO：最新在头
    truncated = existing[:_MAX_RECENT_TRIPS]

    # 5. 更新 social_context_history（去重 + 上限 20）
    sc_history = list(profile.social_context_history or [])
    if social_context in sc_history:
        sc_history.remove(social_context)
    sc_history.insert(0, social_context)
    sc_history = sc_history[:20]

    # 6. 写文件（threading.Lock 跨平台）
    with _FILE_LOCK:
        try:
            updated = profile.model_copy(
                update={
                    "recent_trips": truncated,
                    "social_context_history": sc_history,
                },
            )
            _save_profile(updated, profile_path)
            return True
        except Exception as exc:
            logger.warning("memory_writer: file write failed: %s", exc)
            return False


# ============================================================
# Helpers
# ============================================================


def _resolve_profile_path(override: Path | None) -> Path:
    """解析 user_profile.json 路径。"""
    if override is not None:
        return override
    # 与 data/loader 同源逻辑
    mock_dir = os.getenv("SHANGWUJU_MOCK_DIR")
    if mock_dir:
        return Path(mock_dir) / "user_profile.json"
    # 默认 mock_data/user_profile.json（相对项目根目录）
    project_root = Path(__file__).resolve().parents[3]
    return project_root / "mock_data" / "user_profile.json"


def _load_profile_safe(path: Path) -> Optional[UserProfile]:
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return UserProfile.model_validate(raw)
    except Exception as exc:
        logger.warning("memory_writer: load profile failed: %s", exc)
        return None


def _save_profile(profile: UserProfile, path: Path) -> None:
    """写 JSON（原子化：先写 .tmp 再 rename）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = profile.model_dump(mode="json", exclude_none=False)
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def _now_iso() -> str:
    """ISO 8601 时间戳（UTC，含 Z 后缀）"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_duplicate(
    recent_trips: list[RecentTrip],
    social_context: str,
    now_iso: str,
) -> bool:
    """5 分钟窗口去重检查"""
    if not recent_trips:
        return False
    try:
        now = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False

    for trip in recent_trips:
        if trip.social_context != social_context:
            continue
        try:
            ts_str = trip.timestamp
            # 兼容 "...Z" / "...+00:00"
            if ts_str.endswith("Z"):
                ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
            else:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue
        delta = abs((now - ts).total_seconds())
        if delta < _DEDUP_WINDOW_SECONDS:
            return True
    return False


def _summarize_trip(
    itinerary: Itinerary,
    intent: IntentExtraction,
    *,
    client: Any | None = None,
) -> Optional[str]:
    """LLM 生成脱敏摘要；失败时返简单兜底文本（永不返 None 让流程过不去）"""
    # 构造给 LLM 的轻量描述
    nodes_desc: list[str] = []
    for node in itinerary.nodes:
        if node.target_kind == "home":
            continue
        kind_map = {"poi": "活动", "restaurant": "用餐"}
        type_label = kind_map.get(node.target_kind, node.target_kind)
        nodes_desc.append(f"{type_label}({node.duration_min}min)")
    nodes_str = " → ".join(nodes_desc) if nodes_desc else "未生成行程"

    fallback_summary = (
        f"{intent.social_context}场景行程：{nodes_str}；"
        f"总时长约 {itinerary.total_minutes // 60}小时。"
    )

    # stub 模式短路
    if client is not None and getattr(client, "provider", None) == "stub":
        return fallback_summary

    if client is None:
        try:
            from agent.core.llm_client import get_llm_client

            client = get_llm_client()
        except Exception:
            return fallback_summary

    if getattr(client, "provider", None) == "stub":
        return fallback_summary

    user_msg = (
        f"【场景】{intent.social_context}\n"
        f"【行程节点序列】{nodes_str}\n"
        f"【总时长】{itinerary.total_minutes}min\n\n"
        f"请输出 50-150 字脱敏摘要。"
    )

    try:
        resp = client.chat(
            [
                LLMMessage(role="system", content=_SUMMARIZE_PROMPT),
                LLMMessage(role="user", content=user_msg),
            ],
            temperature=0.4,
        )
        text = (resp.content or "").strip()
        if not text:
            return fallback_summary
        # 长度上限保护
        return text[:500]
    except Exception as exc:
        logger.warning("memory_writer: LLM summarize failed: %s", exc)
        return fallback_summary


__all__ = ["persist_memory"]
