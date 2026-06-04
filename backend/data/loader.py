"""data.loader —— mock_data/*.json 的统一加载入口。

职责：
- 暴露 `load_pois() / load_restaurants() / load_routes() / load_user_profile()`
  四个函数，给所有 Tool 共用——避免每个 Tool 各自打开 JSON。
- 模块级单次缓存：同一进程内只加载一次 JSON，提升测试与多 Tool 调用性能。
- 数据源路径优先级：环境变量 `SHANGWUJU_MOCK_DIR` > 默认 `<repo>/mock_data/`

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
