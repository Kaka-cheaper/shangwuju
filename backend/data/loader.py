"""data.loader —— mock_data/*.json 的统一加载入口。

职责：
- 暴露 `load_pois() / load_restaurants() / load_routes() / load_user_profile()`
  四个函数，给所有 Tool 共用——避免每个 Tool 各自打开 JSON。
- 模块级单次缓存：同一进程内只加载一次 JSON，提升测试与多 Tool 调用性能。
- 数据源路径优先级：环境变量 `SHANGWUJU_MOCK_DIR` > 默认 `<repo>/mock_data/`
- 声明当前数据集的「距离真相源」（`dataset_distance_mode()`，见下）——
  `data.nearby_provider.venue_distance_km` 据此单一声明分派。

不负责：
- 业务过滤（过滤算法在 Tool 实现）。
- 字段校验（由 schemas/ 的 Pydantic 自动完成）。
- 数据写入（Mock 是只读快照，AGENTS.md §3.3 4 层架构边界）。

P1 实现细节（C 同学 owner）：
- 函数签名锁定，**不要改名 / 不要改返回类型**——P2/P3 都依赖。
- 对应文件命名约定（C 在 P1 时创建）：
    mock_data/pois.json          -> List[Poi]
    mock_data/restaurants.json   -> List[Restaurant]
    mock_data/routes.json        -> List[Route]
    mock_data/user_profile.json  -> UserProfile  （单对象，非列表）
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from schemas.domain import ExtraService, Poi, Restaurant, Route, UserProfile


_DEFAULT_MOCK_DIR = Path(__file__).resolve().parents[2] / "mock_data"


def _mock_dir() -> Path:
    env = os.getenv("SHANGWUJU_MOCK_DIR")
    return Path(env) if env else _DEFAULT_MOCK_DIR


# ============================================================
# 数据集「距离真相源」声明（c′批 任务一：距离解析收口）
# ============================================================
#
# 背景（诊断已实证，见 data/nearby_provider.py::venue_distance_km 消费点）：
# 当前杭州 mock 集是**虚构密集小城**——`pois.json`/`restaurants.json` 里手写的
# `distance_km` 字段（home→该点的直线距离）才是产品叙事的距离真相：数值经过
# 挑选，保证"半天逛遍全城"在一个 5km 出行半径内成立。`location.lat/lng` 用的
# 是真实杭州西湖景区坐标（只为地图渲染时 pin 位置看着真实），两者从未被要求
# 一致——实测对同一批 home→POI/餐厅算 haversine 真实距离，与 authored 字段
# 系统性偏差 0.2x～5x（如 P020 authored 1.8km 而 haversine 9.6km），真坐标半径
# 常超 15km，远超 authored 字段刻意收窄的 ~5km 叙事半径。
#
# 这不是"哪个字段算错了"的 bug，是两个字段服务两个不同目的、从设计起就没有
# 被要求自洽——`venue_distance_km` 把"该用哪个当真相"收口成一个显式声明，
# 而不是任由调用方各自决定传不传 lat/lng（那正是 S7 商务场景 execute 阶段
# 走 haversine、ILS 阶段回退 authored 字段、同一 intent 两次召回结果各异的
# 根因：不是距离算错，是两条调用路径读的不是同一份"真相"）。
#
# 值：
#   "authored" （默认）—— 数据集的 distance_km 字段是权威真相；坐标只服务
#                          地图渲染，不参与召回距离判定。
#   "coords"            —— 数据集坐标本身可信（如未来望京真实数据集：每条
#                          POI/餐厅的经纬度就是真实点位，distance_km 字段
#                          要么按坐标算好、要么干脆不再权威），召回距离改用
#                          haversine(home, venue.location) 实时算。
#
# 望京真实数据集接入时：把这里的默认值改成 "coords"（或设
# SHANGWUJU_DISTANCE_MODE=coords env），`venue_distance_km` 自动切换，
# 调用方（execute 阶段 NearbySearchProvider / ILS `_query_pois`/
# `_query_restaurants`）不需要跟着改一行代码——两侧本来就已经统一经过这一个
# 接缝（见该函数消费点 docstring）。
_VALID_DISTANCE_MODES = frozenset({"authored", "coords"})


def dataset_distance_mode() -> str:
    """返回当前 mock 数据集声明的距离真相源：`"authored"` | `"coords"`。

    可用 `SHANGWUJU_DISTANCE_MODE` env 覆盖（供测试构造 "coords" 分支，
    不需要真的换一套望京数据集）；非法值 / 缺省一律回退 "authored"
    （安全默认——现有杭州集就是 authored 语义，误设不该悄悄切换真相源）。
    """
    raw = (os.getenv("SHANGWUJU_DISTANCE_MODE") or "authored").strip().lower()
    return raw if raw in _VALID_DISTANCE_MODES else "authored"


def _load_json(filename: str):
    path = _mock_dir() / filename
    if not path.exists():
        raise FileNotFoundError(
            f"mock 数据文件不存在: {path}（请检查 SHANGWUJU_MOCK_DIR 或填充 mock_data/）"
        )
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_pois() -> list[Poi]:
    """加载所有 POI。"""
    return [Poi.model_validate(x) for x in _load_json("pois.json")]


@lru_cache(maxsize=1)
def load_restaurants() -> list[Restaurant]:
    """加载所有餐厅。"""
    return [Restaurant.model_validate(x) for x in _load_json("restaurants.json")]


@lru_cache(maxsize=1)
def load_routes() -> list[Route]:
    """加载所有路线。"""
    return [Route.model_validate(x) for x in _load_json("routes.json")]


@lru_cache(maxsize=1)
def load_extra_services() -> list[ExtraService]:
    """加载可加购的附加服务。"""
    return [ExtraService.model_validate(x) for x in _load_json("extra_services.json")]


@lru_cache(maxsize=1)
def load_user_profile() -> UserProfile:
    """加载默认用户画像（demo_user）。兼容旧单对象格式。"""
    return UserProfile.model_validate(_load_json("user_profile.json"))


@lru_cache(maxsize=1)
def load_user_profiles() -> dict[str, UserProfile]:
    """加载所有用户画像（多用户字典）。

    返回 {user_id: UserProfile} 映射。
    若 user_profiles.json 不存在，退化为单用户兼容。
    """
    path = _mock_dir() / "user_profiles.json"
    if not path.exists():
        # 兼容：只有旧 user_profile.json
        profile = load_user_profile()
        return {profile.user_id: profile}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return {uid: UserProfile.model_validate(v) for uid, v in data.items()}


def reset_cache() -> None:
    """测试用：清空 lru_cache。"""
    load_pois.cache_clear()
    load_restaurants.cache_clear()
    load_routes.cache_clear()
    load_extra_services.cache_clear()
    load_user_profile.cache_clear()
    load_user_profiles.cache_clear()
