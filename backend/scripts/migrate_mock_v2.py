"""migrate_mock_v2 —— mock_data 升级脚本（spec planning-quality-deep-review R1）。

把：
1) `mock_data/pois.json` 中每个 POI 的 `suggested_duration_minutes`（int）
   升级为 SuggestedDuration dict（含 `default` 必填 + 至少 1 个 age 桶可选）；
2) `mock_data/restaurants.json` 中每个餐厅按 cuisine 回填 `typical_dining_min`；
3) `mock_data/personas.json` 中每个 persona 加 `default_pace_profile`。

设计原则：
- 幂等：脚本可重复跑，已经升级过的 POI 不再改写
- 双兼容期：加 dict 不删 int 兼容（schema 用 Union），下游 helper 同时支持
- 不动 reviews / capacity / tags 等其他字段

参考依据（业界对标，详见 .kiro/specs/.../reports/agent-G/report.md §4）：
- Smithsonian SEEC：3-6 岁博物馆 60-90min
- 美国家长选择金奖博物馆：2-7 岁单次浏览 90min
- TripAdvisor / Foursquare：餐厅按 cuisine 默认 60-90min

运行方式：
    cd backend && .venv/Scripts/python.exe scripts/migrate_mock_v2.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MOCK_DIR = REPO_ROOT / "mock_data"


# ============================================================
# 1. POI 类型 → SuggestedDuration 桶字典
# ============================================================
# 规则：
# - default 给 80% 主流客群的合理时长
# - 至少给一个 age 桶（按 type 主导客群推断）
# - 严格度对齐业界基线（亲子博物馆 60-90min / 主题乐园 120-180min / 茶馆 90min 等）
_AGE_TIER_RULES: dict[str, dict[str, int]] = {
    # 亲子（5-12 岁主导）
    "亲子博物馆": {"default": 90, "kid_3_6": 60, "kid_7_12": 75, "multi_gen": 60},
    "亲子乐园": {"default": 120, "kid_3_6": 75, "kid_7_12": 90, "multi_gen": 75},
    "亲子游乐场": {"default": 90, "kid_3_6": 60, "kid_7_12": 75, "multi_gen": 60},
    "儿童阅读馆": {"default": 60, "kid_3_6": 45, "kid_7_12": 60, "multi_gen": 45},
    "DIY 工坊": {"default": 90, "kid_3_6": 60, "kid_7_12": 75, "multi_gen": 60},
    "烘焙工坊": {"default": 90, "kid_3_6": 60, "kid_7_12": 75, "multi_gen": 60},
    # 主题大型娱乐（成人主导，亲子陪伴）
    "主题乐园": {"default": 180, "kid_3_6": 90, "kid_7_12": 120, "senior": 90, "multi_gen": 90},
    "城市观光": {"default": 90, "senior": 60, "multi_gen": 60},
    "街区漫步": {"default": 75, "senior": 60, "multi_gen": 60},
    # 文化场景（中等强度）
    "展览": {"default": 75, "kid_3_6": 45, "senior": 60, "multi_gen": 60},
    "画廊": {"default": 60, "senior": 45, "multi_gen": 45},
    "图书馆": {"default": 60, "senior": 60, "multi_gen": 45},
    "书店": {"default": 60, "senior": 45, "multi_gen": 45},
    "戏曲园": {"default": 90, "senior": 75, "multi_gen": 60},
    "茶馆": {"default": 90, "senior": 75, "multi_gen": 60},
    "演出": {"default": 90, "senior": 75, "multi_gen": 75},
    # 室外活动 / 公园
    "城市公园": {"default": 75, "kid_3_6": 60, "senior": 60, "multi_gen": 60},
    "运动步道": {"default": 60, "senior": 45, "multi_gen": 45},
    "庆典花园": {"default": 75, "senior": 60, "multi_gen": 60},
    # 室内娱乐 / 游戏
    "桌游馆": {"default": 90, "kid_7_12": 90, "multi_gen": 75},
    "密室": {"default": 90, "kid_7_12": 75},
    "剧本杀": {"default": 180, "kid_7_12": 120},
    "KTV": {"default": 90},
    "电影院": {"default": 120, "kid_3_6": 75, "senior": 90, "multi_gen": 90},
    "livehouse": {"default": 120},
    "酒吧": {"default": 90},
    # 复合 / 主题空间
    "复合体验馆": {"default": 90, "kid_3_6": 60, "senior": 60, "multi_gen": 60},
    "复合空间": {"default": 75, "senior": 60, "multi_gen": 60},
    "私享空间": {"default": 90, "multi_gen": 60},
    "商务茶室": {"default": 90, "senior": 75, "multi_gen": 60},
    # 健身 / 运动 / SPA
    "健身房": {"default": 60},
    "瑜伽馆": {"default": 60, "senior": 45},
    "室内运动馆": {"default": 75, "kid_3_6": 45, "kid_7_12": 60, "multi_gen": 60},
    "SPA": {"default": 90, "senior": 75},
    # 个护 / 美甲
    "美甲": {"default": 60},
    # 萌宠
    "猫咖": {"default": 75, "kid_3_6": 45, "senior": 45, "multi_gen": 45},
    # 餐饮（POI 中也可能出现，但实际是餐厅；此处给保底）
    "咖啡馆": {"default": 60, "senior": 45, "multi_gen": 45},
}


def _upgrade_poi_duration(poi: dict[str, Any]) -> bool:
    """把 poi['suggested_duration_minutes'] 从 int 升级为 dict。

    返回是否真的改写（已是 dict 则返回 False，幂等）。
    """
    cur = poi.get("suggested_duration_minutes")
    if isinstance(cur, dict):
        return False  # 已升级

    poi_type = poi.get("type", "")
    bucket = _AGE_TIER_RULES.get(poi_type)
    if bucket is None:
        # 未在词典中，至少把 int 包成 dict 形态保留 default
        if isinstance(cur, int):
            poi["suggested_duration_minutes"] = {"default": cur}
            return True
        return False

    new_val: dict[str, int] = dict(bucket)
    # 如果原 int 与 default 不同，保留为旧 default 的兼容性（取较小值偏稳）
    if isinstance(cur, int) and cur > 0:
        # 业务取舍：当原值更宽松（如 120）时仍按词典 default 取，避免 5 岁娃 2.5h 复发
        # 当原值更严格时（如某 POI 已有 60），保留较严格的 default
        new_val["default"] = min(bucket["default"], cur) if cur < bucket["default"] else bucket["default"]
    poi["suggested_duration_minutes"] = new_val
    return True


# ============================================================
# 2. Restaurant 按 cuisine → typical_dining_min
# ============================================================
_CUISINE_DINING_MIN: dict[str, int] = {
    "健康轻食": 40,
    "咖啡": 45,
    "下午茶": 75,
    "烘焙甜品": 45,
    "杭帮菜": 75,
    "本帮菜": 75,
    "粤菜": 90,
    "川菜": 90,
    "湘菜": 90,
    "日料": 75,
    "东南亚菜": 75,
    "烧烤": 90,
    "火锅": 120,
    "西餐": 90,
    "法餐": 120,
}

_PREMIUM_TAGS = ("高人均", "私房菜", "包间", "有包间")


def _upgrade_restaurant_dining(rest: dict[str, Any]) -> bool:
    """给 restaurant 加 typical_dining_min（按 cuisine + tags 调整）。

    返回是否真的改写。
    """
    if rest.get("typical_dining_min") is not None:
        return False

    cuisine = rest.get("cuisine", "")
    base = _CUISINE_DINING_MIN.get(cuisine)
    if base is None:
        # 未知 cuisine，按一般正餐 75min 兜底
        base = 75

    # 高人均 / 私房菜 / 含包间 加 15min
    tags = rest.get("tags", [])
    if any(t in tags for t in _PREMIUM_TAGS):
        base += 15

    rest["typical_dining_min"] = base
    return True


# ============================================================
# 3. Persona 加 default_pace_profile
# ============================================================
_PACE_PROFILE_BY_USER: dict[str, dict[str, int]] = {
    # 5 岁男孩家庭 → 单段 ≤ 75 / 总活跃 ≤ 240 / 每 45min 一次休息 / 偏好 60min 停留
    "u_dad": {
        "single_session_max_min": 75,
        "total_active_min": 240,
        "break_every_min": 45,
        "preferred_dwell_min": 60,
    },
    # 商务接待 → 单段 ≥ 90 / 偏好长时段
    "u_biz": {
        "single_session_max_min": 120,
        "total_active_min": 240,
        "break_every_min": 90,
        "preferred_dwell_min": 90,
    },
    # 老人陪伴 → 单段 ≤ 75 / 高频休息
    "u_grandma": {
        "single_session_max_min": 75,
        "total_active_min": 180,
        "break_every_min": 45,
        "preferred_dwell_min": 60,
    },
    # 独处放空 → 单段 60-90 / 中等节奏
    "u_solo": {
        "single_session_max_min": 90,
        "total_active_min": 240,
        "break_every_min": 90,
        "preferred_dwell_min": 75,
    },
    # 情侣 → 单段 60-90
    "u_couple": {
        "single_session_max_min": 90,
        "total_active_min": 240,
        "break_every_min": 60,
        "preferred_dwell_min": 75,
    },
}


def _upgrade_persona_pace(persona: dict[str, Any]) -> bool:
    """给 persona 加 default_pace_profile。"""
    if "default_pace_profile" in persona:
        return False
    pp = _PACE_PROFILE_BY_USER.get(persona.get("user_id", ""))
    if pp is None:
        return False
    persona["default_pace_profile"] = pp
    return True


# ============================================================
# Driver
# ============================================================

def main() -> int:
    pois_path = MOCK_DIR / "pois.json"
    rest_path = MOCK_DIR / "restaurants.json"
    persona_path = MOCK_DIR / "personas.json"

    # 1. POI
    pois = json.loads(pois_path.read_text(encoding="utf-8"))
    poi_changed = sum(1 for p in pois if _upgrade_poi_duration(p))
    pois_path.write_text(
        json.dumps(pois, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✓ pois.json: {poi_changed}/{len(pois)} POI 已升级 SuggestedDuration")

    # 2. Restaurant
    rests = json.loads(rest_path.read_text(encoding="utf-8"))
    rest_changed = sum(1 for r in rests if _upgrade_restaurant_dining(r))
    rest_path.write_text(
        json.dumps(rests, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✓ restaurants.json: {rest_changed}/{len(rests)} 餐厅已加 typical_dining_min")

    # 3. Persona
    personas = json.loads(persona_path.read_text(encoding="utf-8"))
    persona_changed = sum(1 for p in personas if _upgrade_persona_pace(p))
    persona_path.write_text(
        json.dumps(personas, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"✓ personas.json: {persona_changed}/{len(personas)} persona "
        f"已加 default_pace_profile"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
