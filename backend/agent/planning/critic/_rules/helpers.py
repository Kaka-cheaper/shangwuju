"""critic 共享 helpers（spec code-modularization-refactor H6）。

8 个 module-level 私有函数从 critics_v2.py 抽出：
- 数据加载：_safe_load_pois / _safe_load_restaurants / _safe_load_user_profile
- 时间解析：_parse_hhmm / _fmt_hhmm
- 节点工具：_resolve_node_location / _humanize_node
- 用户偏好：_resolve_transport_preference

为什么独立成 helpers.py 而非内嵌 checks.py：
- 多个 _check_xxx 共用同一组 helper（DRY）
- 保留 helper 私有性（不进 critics_v2 公开 API）
- 单测可独立覆盖（不必 import 整套 critic 框架）

行为契约：与拆分前的 critics_v2.py 内嵌 helper 完全一致。
"""

from __future__ import annotations

from typing import Optional

from schemas.itinerary import ActivityNode


# ============================================================
# 时间解析
# ============================================================


def parse_hhmm(value: str) -> Optional[int]:
    """把 "14:30" 转成总分钟数（870）。格式不合法返 None。"""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        h = int(parts[0])
        mn = int(parts[1])
    except ValueError:
        return None
    # 允许 24-29h 跨日表示（夜宵 23:00-01:00 已在 schema 兼容）
    if not (0 <= h <= 29 and 0 <= mn <= 59):
        return None
    return h * 60 + mn


def fmt_hhmm(total: int) -> str:
    """工具函数：分钟数 → HH:MM。"""
    total = max(0, min(total, 24 * 60 - 1))
    return f"{total // 60:02d}:{total % 60:02d}"


# ============================================================
# 数据加载（容错）
# ============================================================


def safe_load_pois():
    """容错加载 POI；mock 数据缺失时返空列表，跳过相关检查。"""
    try:
        from data.loader import load_pois  # 延迟 import，避免无 mock 数据时炸

        return load_pois()
    except Exception:
        return []


def safe_load_restaurants():
    try:
        from data.loader import load_restaurants

        return load_restaurants()
    except Exception:
        return []


def safe_load_user_profile(user_id: str = "demo_user"):
    """容错加载 UserProfile（含 transport_preference / home_location 坐标）。"""
    try:
        from data.loader import load_user_profile, load_user_profiles

        # 优先按 user_id 查多用户字典，找不到回退默认
        try:
            profiles = load_user_profiles()
            if user_id in profiles:
                return profiles[user_id]
        except Exception:
            pass
        return load_user_profile()
    except Exception:
        return None


# ============================================================
# 节点工具
# ============================================================


def resolve_node_location(
    node: ActivityNode, *, user_profile=None
) -> tuple[Optional[float], Optional[float]]:
    """从 ActivityNode 提取 (lat, lng)。

    - target_kind="home"：优先 user_profile.home_location，回退 node 自带坐标
    - 其它：直接读 node.lat/lng（assemble 已写入）

    edge_v1 简化：node 永远有明确 target，不存在「过程段」需要兜底坐标。
    """
    if node.target_kind == "home" and user_profile is not None:
        loc = getattr(user_profile, "home_location", None)
        if loc is not None and loc.lat is not None and loc.lng is not None:
            return loc.lat, loc.lng
    return node.lat, node.lng


def humanize_node(idx: int, node: ActivityNode) -> str:
    """把 nodes[idx] 翻译成人话「第 N 段「kind · title」」。"""
    label = node.title or node.target_id or "未命名"
    return f"第 {idx + 1} 段「{node.kind} · {label}」"


def resolve_transport_preference(profile) -> str:
    """从 user_profile 取 transport_preference，越界值回退 taxi。"""
    pref = getattr(profile, "transport_preference", "taxi") or "taxi"
    if pref in ("walking", "taxi", "bus"):
        return pref
    return "taxi"
