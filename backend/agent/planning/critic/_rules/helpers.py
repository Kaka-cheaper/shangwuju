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

import re
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
# 营业时间解析（移植自 blueprint.py，ADR-0008 B-2b G3：逐字节照搬语义）
# ============================================================
#
# 死代码 blueprint._opening_hours_critic 只用 preferred_start_time + 累加 duration
# 粗略推算节点时段（不含 hop 通勤耗时）。本模块把其营业时间解析部分（正则 + 判定函数）
# 原样搬进 critic 层，供 check_opening_hours 用**真实**已 assemble 的 node.start_time
# 判定——因此是精确版，而非重复实现。

_BUSINESS_HOURS_RE = re.compile(
    r"^([01]\d|2[0-3]):([0-5]\d)\s*[-–]\s*([01]\d|2[0-3]):([0-5]\d)$"
)


def _is_in_business_hours(start_min: int, end_min: int, opening_hours: str) -> bool:
    """判断 [start_min, end_min]（分钟）是否完全落在 opening_hours 内。

    支持 "10:30-21:30" / "00:00-23:59" / "08:00 - 22:00" 等单区间格式。
    - 空 opening_hours → True（无营业时间约束默认通过）
    - 不识别格式 → True（不误伤，让其它 check 兜底）
    - 跨日营业（close_t <= open_t，如 "22:00-04:00"）→ True（hackathon 范围简化通过）
    """
    if not opening_hours:
        return True
    m = _BUSINESS_HOURS_RE.match(opening_hours.strip())
    if not m:
        return True
    open_h, open_m, close_h, close_m = map(int, m.groups())
    open_t = open_h * 60 + open_m
    close_t = close_h * 60 + close_m
    if close_t <= open_t:
        return True
    return open_t <= start_min and end_min <= close_t


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
