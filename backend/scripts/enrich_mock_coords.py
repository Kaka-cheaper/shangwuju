"""scripts.enrich_mock_coords —— 批量地理编码补全 mock_data 坐标。

设计动机（spec frontend-experience-innovation R2）：
- 当前 pois.json / restaurants.json 的 location 大多只有 `name` 字符串（如「西溪天街」）
- 高德地图 MapOverlay 需要 lat/lng 才能标注
- 跑本脚本一次：把 31 个独立地名通过高德 Web 服务 GeoCode API 转成坐标，
  写回 mock_data/*.json 的 location.lat / location.lng 字段

为什么不前端动态地理编码：
- 评委演示时网络抖动 → 标注延迟出现，观感差
- demo 时希望「一切都已就绪」

为什么不前端硬编码坐标表：
- 31 个地名靠人查容易出错
- 切换真实数据源时（mock → 美团/高德 POI）映射要重做

运行方式：
    cd backend
    .venv\\Scripts\\python.exe -m scripts.enrich_mock_coords
    或：uv run scripts/enrich_mock_coords.py

约束：
- AMAP_REST_KEY 需在 backend/.env 设置（Web 服务 Key，不是 JS API Key）
- 城市固定 city=杭州（demo 范围）
- 已有 lat/lng 的 location 跳过（增量更新）
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# 允许直接 python 跑
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
import httpx

load_dotenv()


# ============================================================
# 配置
# ============================================================

AMAP_REST_KEY = os.getenv("AMAP_REST_KEY", "").strip()
AMAP_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
DEFAULT_CITY = "杭州"
REQUEST_TIMEOUT_S = 10.0
REQUEST_INTERVAL_S = 0.5  # 高德个人 key QPS 较紧（实际可能 ≤2/s），保守 500ms 间隔
MAX_RETRIES_PER_NAME = 3  # 单个地名最多重试 3 次（指数退避）

# 杭州主城区合理范围（西湖区 + 拱墅 + 上城 + 下城 + 江干 + 滨江 + 余杭一部分）
# 用于过滤地理编码结果——超出范围的视为错误命中
HANGZHOU_LAT_MIN, HANGZHOU_LAT_MAX = 30.10, 30.40
HANGZHOU_LNG_MIN, HANGZHOU_LNG_MAX = 119.95, 120.40

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MOCK_DIR = PROJECT_ROOT / "mock_data"
POIS_PATH = MOCK_DIR / "pois.json"
RESTAURANTS_PATH = MOCK_DIR / "restaurants.json"


def _is_in_hangzhou(lat: float, lng: float) -> bool:
    return (
        HANGZHOU_LAT_MIN <= lat <= HANGZHOU_LAT_MAX
        and HANGZHOU_LNG_MIN <= lng <= HANGZHOU_LNG_MAX
    )


# ============================================================
# 高德 GeoCode 调用
# ============================================================

def _geocode_once(
    address: str, *, client: httpx.Client
) -> tuple[float, float] | str | None:
    """单次调用。返回:
        (lat, lng)：成功
        "RETRY"  ：限流类错误（CUQPS / DAILY_QUERY），调用方应退避重试
        None     ：永久失败（地名不存在 / key 无效 / 解析失败 / 不在杭州范围）
    """
    try:
        r = client.get(
            AMAP_GEOCODE_URL,
            params={
                "address": address,
                "city": DEFAULT_CITY,
                "key": AMAP_REST_KEY,
            },
            timeout=REQUEST_TIMEOUT_S,
        )
        data = r.json()
    except Exception:
        return "RETRY"  # 网络异常视为可重试

    status = data.get("status")
    infocode = str(data.get("infocode") or "")

    if status != "1":
        # 限流类（CUQPS_HAS_EXCEEDED_THE_LIMIT=10021, DAILY_QUERY_OVER_LIMIT=10044, 等）
        if infocode in ("10021", "10003", "10004", "10044"):
            return "RETRY"
        return None

    geocodes = data.get("geocodes") or []
    if not geocodes:
        return None

    location_str = geocodes[0].get("location") or ""
    if not location_str or "," not in location_str:
        return None
    lng_s, lat_s = location_str.split(",", 1)
    try:
        lng = float(lng_s)
        lat = float(lat_s)
    except ValueError:
        return None

    # 范围合理性校验：高德对模糊地名（如"南山路"）可能命中其他城市
    if not _is_in_hangzhou(lat, lng):
        return None

    return (lat, lng)


def geocode(name: str, *, client: httpx.Client) -> tuple[float, float] | None:
    """把单个地名转成 (lat, lng)。失败返 None。

    策略（手工坐标优先）：
        0. 如果 _MANUAL_FALLBACK 里有 → 直接返回（防短地名歧义被 GeoCode 错配）
        1. 否则尝试原地名（带 city=杭州）+ 限流时指数退避重试 ≤3 次
        2. 若返回结果不在杭州主城区范围 → 加「杭州市」前缀再试
        3. 仍失败 → 返 None
    """
    # 步骤 0：手工坐标优先级最高（防 GeoCode 短地名歧义）
    if name in _MANUAL_FALLBACK:
        lat, lng = _MANUAL_FALLBACK[name]
        return (lat, lng)

    candidates = [name, f"杭州市{name}", f"杭州{name}"]
    for candidate in candidates:
        for attempt in range(MAX_RETRIES_PER_NAME):
            result = _geocode_once(candidate, client=client)
            if isinstance(result, tuple):
                lat, lng = result
                if candidate != name:
                    print(
                        f"  ↻ {name}：原查询失败，用 {candidate!r} 命中 → "
                        f"{lat:.4f}, {lng:.4f}"
                    )
                return (lat, lng)
            if result == "RETRY":
                # 指数退避：0.6s / 1.2s / 2.4s
                backoff = 0.6 * (2**attempt)
                time.sleep(backoff)
                continue
            # None：永久失败，跳到下一个候选
            break
    print(f"  ✗ {name}：所有候选都失败")
    return None


# ============================================================
# 手工兜底坐标（高德 GeoCode 查不到 / 命中错位置的地名）
# ============================================================
# 这些坐标是本地知识 + 高德地图实际位置手工核对后填入；
# **优先级高于** 高德 GeoCode 返回值（防止短地名歧义命中错位置，
# 例如「西溪银泰」高德会命中滨江同名地点而非蒋村西溪银泰城）
_MANUAL_FALLBACK: dict[str, tuple[float, float]] = {
    # 西溪片区（30.27-30.29 lat / 120.07-120.09 lng）
    "西溪天街": (30.288, 120.083),
    "西溪银泰": (30.273, 120.080),       # 高德误命中滨江
    "西溪文创园": (30.290, 120.078),     # 高德误命中城西
    "西溪湿地北门": (30.285, 120.083),
    # 城西片区（高德里没"城西万象城"独立 POI）
    "城西万象城": (30.280, 120.108),
    "城西银泰": (30.280, 120.110),
    # 西湖片区
    "断桥": (30.255, 120.144),           # 高德误命中西溪
    # 钱江新城
    "钱江新城万象城": (30.247, 120.207),  # 高德误命中武林广场
    "万象城": (30.247, 120.207),         # 同上
    "钱江新城": (30.250, 120.207),
    "钱江世纪城": (30.241, 120.244),
    # 武林片区
    "嘉里中心": (30.276, 120.166),       # 高德返回值偏南
    # 大运河沿岸
    "运河南端": (30.275, 120.175),       # 高德误判位置（南/北端反了）
    "运河边": (30.288, 120.176),
    # 余杭良渚
    "良渚文化村": (30.395, 120.020),     # 高德返回值偏南 4km
    # 上城区
    "上城区": (30.232, 120.169),         # 高德返回值偏东
    # 玉皇山
    "玉皇山南": (30.211, 120.155),
    "玉皇山路": (30.213, 120.149),
    # 嘉里中心北侧
    "玉古路": (30.269, 120.118),
}


# ============================================================
# JSON 文件批量更新
# ============================================================

def collect_unique_locations(*paths: Path) -> set[str]:
    """从多个 JSON 文件里抽出所有 location.name 去重。"""
    names: set[str] = set()
    for p in paths:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            loc = item.get("location") or {}
            name = loc.get("name")
            if name and isinstance(name, str):
                names.add(name.strip())
    return names


def enrich_file(
    path: Path,
    coord_map: dict[str, tuple[float, float]],
    *,
    force: bool = False,
) -> tuple[int, int]:
    """给一个 JSON 文件补 lat/lng。返回 (已补全数, 跳过数)。

    Args:
        force: True 时即使 location 已有 lat/lng 也覆盖（用于修复历史错误坐标）
    """
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    enriched = 0
    skipped = 0
    for item in data:
        loc = item.get("location") or {}
        name = loc.get("name")
        if not name:
            skipped += 1
            continue
        # 已有 lat 时：force=False 跳过，force=True 仍覆盖
        if (
            not force
            and loc.get("lat") is not None
            and loc.get("lng") is not None
        ):
            skipped += 1
            continue
        coord = coord_map.get(name.strip())
        if coord is None:
            skipped += 1
            continue
        lat, lng = coord
        loc["lat"] = lat
        loc["lng"] = lng
        item["location"] = loc
        enriched += 1

    # 写回（保留中文 + 缩进 2，与原文件风格一致）
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")  # POSIX 风格末尾换行
    return enriched, skipped


# ============================================================
# 主入口
# ============================================================

def main() -> int:
    if not AMAP_REST_KEY:
        print("✗ AMAP_REST_KEY 未设置。请在 backend/.env 填入 Web 服务 Key 后重试。")
        return 1

    if not POIS_PATH.exists() or not RESTAURANTS_PATH.exists():
        print(f"✗ mock 文件不存在：{POIS_PATH} / {RESTAURANTS_PATH}")
        return 1

    force = "--force" in sys.argv
    if force:
        print("⚠ --force 模式：会覆盖已有的 lat/lng（用于修正历史错误坐标）\n")

    print(f"扫描独立地名：")
    names = collect_unique_locations(POIS_PATH, RESTAURANTS_PATH)
    print(f"  共 {len(names)} 个独立 location.name")

    print(f"\n开始地理编码（city={DEFAULT_CITY}）：")
    coord_map: dict[str, tuple[float, float]] = {}
    with httpx.Client() as client:
        for i, name in enumerate(sorted(names), 1):
            coord = geocode(name, client=client)
            if coord is not None:
                lat, lng = coord
                source = "手工" if name in _MANUAL_FALLBACK else "高德"
                print(f"  [{i:>2}/{len(names)}] {name} → {lat:.4f}, {lng:.4f} ({source})")
                coord_map[name] = coord
            else:
                print(f"  [{i:>2}/{len(names)}] {name} → 跳过")
            # 手工坐标不调网络，不需要限速
            if name not in _MANUAL_FALLBACK:
                time.sleep(REQUEST_INTERVAL_S)

    print(f"\n地理编码完成：成功 {len(coord_map)} / 失败 {len(names) - len(coord_map)}")

    print(f"\n回写 mock 文件{'（强制覆盖）' if force else ''}：")
    e1, s1 = enrich_file(POIS_PATH, coord_map, force=force)
    print(f"  pois.json：补全 {e1} 个，跳过 {s1} 个")
    e2, s2 = enrich_file(RESTAURANTS_PATH, coord_map, force=force)
    print(f"  restaurants.json：补全 {e2} 个,跳过 {s2} 个")

    print("\n✓ 完成。Mock 数据现已带坐标，可由前端 MapOverlay 直接消费。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
