"""verify_schemas —— Phase 0 自检脚本。

跑一遍把 `mock_data/_samples/*.json` 反向加载为 Pydantic 模型，
确保 schema 与典范样本互相自洽：
- 字段名漂移（schema 写错或样本写错）会立刻被 Pydantic ValidationError 暴露
- tag 词典外的 tag 会被 Literal 校验拦截
- extra="forbid" 会拦截发明的字段
- 顺手验证 IntentExtraction（§5.7 D-SoT）能加载主场景输入

运行：
    cd backend && python -m scripts.verify_schemas
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError

from schemas.domain import Poi, Restaurant, Route
from schemas.intent import IntentExtraction


SAMPLES_DIR = Path(__file__).resolve().parents[2] / "mock_data" / "_samples"


def _load_json(name: str):
    path = SAMPLES_DIR / name
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _check(label: str, fn) -> tuple[bool, str]:
    try:
        n = fn()
        return True, f"  ✓ {label}: {n}"
    except ValidationError as e:
        return False, f"  ✗ {label} 校验失败：\n{e}"
    except Exception as e:  # noqa: BLE001
        return False, f"  ✗ {label} 加载失败：{type(e).__name__}: {e}"


def main() -> int:
    print("=== Phase 0 schema 自检 ===")
    print(f"样本目录: {SAMPLES_DIR}")
    print()

    results: list[tuple[bool, str]] = []

    def load_pois():
        raw = _load_json("poi.example.json")
        items = [Poi.model_validate(x) for x in raw]
        return f"加载 {len(items)} 条 POI（含失败埋点：售罄 P002）"

    results.append(_check("POI", load_pois))

    def load_restaurants():
        raw = _load_json("restaurant.example.json")
        items = [Restaurant.model_validate(x) for x in raw]
        # 检查至少有一条 reservation_slots 含 available=false（E1 埋点）
        has_full = any(
            slot.available is False for r in items for slot in r.reservation_slots
        )
        if not has_full:
            raise ValueError("样本未埋 E1 失败案例")
        return f"加载 {len(items)} 条 Restaurant（含 E1 餐厅满埋点）"

    results.append(_check("Restaurant", load_restaurants))

    def load_routes():
        raw = _load_json("route.example.json")
        items = [Route.model_validate(x) for x in raw]
        return f"加载 {len(items)} 条 Route"

    results.append(_check("Route", load_routes))

    def load_intent():
        raw = _load_json("intent.example.json")
        intent = IntentExtraction.model_validate(raw)
        # 反向核查 D9 硬条款：禁止字段
        forbidden = {"scene_type", "relation_type", "is_family", "is_friends"}
        dumped = intent.model_dump()
        leak = forbidden & set(dumped.keys())
        if leak:
            raise ValueError(f"出现 D9 禁止字段: {leak}")
        return f"加载主场景 IntentExtraction（social_context={intent.social_context}）"

    results.append(_check("IntentExtraction (§5.7 D-SoT)", load_intent))

    # 反向测试：故意加发明字段应当报错
    def negative_extra_field():
        bad = {
            "start_time": "today_afternoon",
            "raw_input": "测试",
            "parse_confidence": 0.5,
            "scene_type": "family",  # D9 禁止字段
        }
        try:
            IntentExtraction.model_validate(bad)
        except ValidationError:
            return "extra='forbid' 成功拦截 scene_type 字段"
        raise AssertionError("Pydantic 未拦截发明字段——schema 配置有误")

    results.append(_check("反向测试：D9 禁止字段被拦截", negative_extra_field))

    # 反向测试：词典外 tag 应当报错
    def negative_invalid_tag():
        bad = {
            "start_time": "today_afternoon",
            "raw_input": "测试",
            "parse_confidence": 0.5,
            "physical_constraints": ["不存在的tag"],
        }
        try:
            IntentExtraction.model_validate(bad)
        except ValidationError:
            return "Literal 成功拦截词典外 tag"
        raise AssertionError("Pydantic 未拦截非法 tag——Literal 配置有误")

    results.append(_check("反向测试：词典外 tag 被拦截", negative_invalid_tag))

    print("\n".join(line for _, line in results))
    print()
    failed = [line for ok, line in results if not ok]
    if failed:
        print(f"→ 失败 {len(failed)} 项")
        return 1
    print(f"✓ 全部 {len(results)} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
