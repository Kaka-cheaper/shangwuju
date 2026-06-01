"""tests.test_detect_unmet_poi —— spec narration-and-intent-fidelity R4 兜底检测。

背景：用户明说「看展」，但重排后方案里仍一个展都没有（本地无此类场所 / 被距离过滤）
     → 应像 cuisine 版一样诚实告知「看展这次没安排上，先帮你选了替代」。

本测试验证纯检测函数 detect_unmet_poi_preference 的行为（确定性）；
narrator 文案的暖语气包装靠 prompt（概率性，不在此断言）。
与 cuisine 版 test_narrator_honest_substitution.py 对称。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


def _install_agent_stub() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    agent_dir = backend_root / "agent"
    if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
        stub = types.ModuleType("agent")
        stub.__path__ = [str(agent_dir)]
        sys.modules["agent"] = stub


_install_agent_stub()

from agent.intent.narrator import detect_unmet_poi_preference  # noqa: E402


def test_unmet_when_kanzhan_absent() -> None:
    """用户要看展，行程 POI 全是猫咖/咖啡馆 → 报「看展」未满足。"""
    unmet = detect_unmet_poi_preference(
        preferred_poi_types=["看展"],
        itinerary_poi_types=["猫咖", "咖啡馆"],
        itinerary_poi_names=["毛球先生猫咖", "花漾咖啡"],
        itinerary_poi_tags=["拍照友好", "网红打卡", "热闹"],
    )
    assert "看展" in unmet, "行程无展类 POI，看展应判未满足"


def test_satisfied_when_type_matches() -> None:
    """行程里有 type=展览 的 POI → 看展已满足，不报。"""
    unmet = detect_unmet_poi_preference(
        preferred_poi_types=["看展"],
        itinerary_poi_types=["展览", "咖啡馆"],
        itinerary_poi_names=["西溪艺术展中心", "花漾咖啡"],
        itinerary_poi_tags=["看展", "网红打卡"],
    )
    assert unmet == [], "行程含展类 POI，不应报未满足"


def test_satisfied_when_tags_match() -> None:
    """type 不直接命中但 tags 含「看展」→ 仍判满足。"""
    unmet = detect_unmet_poi_preference(
        preferred_poi_types=["看展"],
        itinerary_poi_types=["复合体验馆"],
        itinerary_poi_names=["某复合馆"],
        itinerary_poi_tags=["看展", "亲子友好"],
    )
    assert unmet == []


def test_unmet_ktv() -> None:
    """用户要 KTV，行程无 KTV → 报未满足。"""
    unmet = detect_unmet_poi_preference(
        preferred_poi_types=["KTV"],
        itinerary_poi_types=["猫咖"],
        itinerary_poi_names=["毛球先生猫咖"],
        itinerary_poi_tags=["拍照友好"],
    )
    assert "KTV" in unmet


def test_no_preference_returns_empty() -> None:
    """用户没明示诉求 → 永不报未满足。"""
    assert detect_unmet_poi_preference([], ["猫咖"], ["x"], ["y"]) == []


def test_cuisine_token_delegated_to_cuisine_version() -> None:
    """含明显餐饮 token 的诉求（烧烤/火锅）交给 cuisine 版，POI 版跳过不重复计。"""
    unmet = detect_unmet_poi_preference(
        preferred_poi_types=["烧烤", "火锅"],
        itinerary_poi_types=["猫咖"],
        itinerary_poi_names=["毛球先生猫咖"],
        itinerary_poi_tags=["拍照友好"],
    )
    assert unmet == [], "餐饮品类诉求不应在 POI 版重复报未满足"


def test_dedup_preserves_order() -> None:
    """重复诉求词去重保序。"""
    unmet = detect_unmet_poi_preference(
        preferred_poi_types=["看展", "看展", "密室"],
        itinerary_poi_types=["猫咖"],
        itinerary_poi_names=["毛球先生猫咖"],
        itinerary_poi_tags=["拍照友好"],
    )
    assert unmet == ["看展", "密室"]
