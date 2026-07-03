"""test_routing_brain_real_llm —— 统一路由脑子真实 LLM 集成测试（不 mock）。

验证 ADR-0011 E-2-c 落地：统一路由脑子在真实 LLM 下能正确判出 6 类标签，
尤其是 stub 测不出的"同一句话不同上下文不同判法"（8 场景少样本的设计初衷）
与"追加请求应判 feedback 而非 clarify"的已知历史缺口。

运行条件：
- backend/.env 配好真 LLM（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）；缺则整类 skip。
- 会真打 API（约 1-4s/用例），断言对 LLM 输出做"方向性"宽松校验，不卡字面。

仿 test_refiner_real_llm.py 先例（加载 .env、顶掉 conftest 的 stub 默认、清 lru_cache）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


def _load_backend_dotenv() -> None:
    """把 backend/.env 读进 os.environ（pytest 不会自动加载）。已存在的键不覆盖。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_backend_dotenv()

from agent.core.llm_client import get_llm_client, reset_llm_client_cache  # noqa: E402
from agent.routing.brain import classify_turn  # noqa: E402


def _llm_configured() -> bool:
    return bool(
        os.getenv("LLM_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("QWEN_API_KEY")
    )


pytestmark = pytest.mark.skipif(
    not _llm_configured(),
    reason="未配置真实 LLM（backend/.env 无 LLM_API_KEY），跳过真测",
)


@pytest.fixture(autouse=True)
def _force_real_llm(monkeypatch):
    """顶掉 conftest 的 stub 默认，让本类真打 LLM（见 test_refiner_real_llm.py 同款注释）。"""
    monkeypatch.setenv("LLM_PROVIDER", "")
    reset_llm_client_cache()
    yield
    reset_llm_client_cache()


_NO_PLAN_CONTEXT = (
    "【首轮原始需求】\n（无）\n\n【会话轮次】(共 0 轮)\n（无）\n\n"
    "【方案版本志】(共 0 版)\n（暂无方案版本）\n\n【当前方案摘要】\n（暂无方案）\n\n"
    "【画像】\n（无画像数据）\n\n【待澄清】\n（无）\n\n【待确认态】\n（未决定）\n\n"
    "【台账生效条目】(共 0 条)\n（暂无生效诉求）"
)


def _with_plan_context(first_request: str, plan_lines: list[str]) -> str:
    turn_log = f"user: {first_request}\nagent: 已为你规划好……"
    summary = "\n".join(f"- {line}" for line in plan_lines)
    return (
        f"【首轮原始需求】\n{first_request}\n\n"
        f"【会话轮次】(共 2 轮)\n{turn_log}\n\n"
        f"【方案版本志】(共 1 版)\n- v1: 按『{first_request[:12]}』出方案\n\n"
        f"【当前方案摘要】\n{summary}\n\n"
        "【画像】\n（无画像数据）\n\n【待澄清】\n（无）\n\n【待确认态】\n（未决定）\n\n"
        "【台账生效条目】(共 0 条)\n（暂无生效诉求）"
    )


class TestRoutingBrainRealLLM:
    """真打 LLM。断言只做方向性宽松校验，避免被措辞差异卡死；每条用例打印耗时。"""

    def _classify(self, context_text: str, user_input: str, has_itinerary: bool):
        client = get_llm_client()
        t0 = time.monotonic()
        judgment = classify_turn(context_text, user_input, has_itinerary, client=client)
        elapsed = time.monotonic() - t0
        print(f"\n[brain smoke] input={user_input!r} elapsed={elapsed:.2f}s")
        assert judgment is not None, f"脑子应能正常解析真 LLM 输出，input={user_input!r}"
        return judgment, elapsed

    def test_first_turn_planning_request(self):
        judgment, _ = self._classify(
            _NO_PLAN_CONTEXT,
            "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁",
            False,
        )
        assert judgment.label == "planning", judgment

    def test_vague_request_asks_for_clarification(self):
        judgment, _ = self._classify(_NO_PLAN_CONTEXT, "出去玩", False)
        assert judgment.label == "clarify", judgment
        assert judgment.reply_text

    def test_off_topic_homework_request_gets_defended(self):
        judgment, _ = self._classify(_NO_PLAN_CONTEXT, "帮我写一段快速排序的代码", False)
        assert judgment.label == "defense", judgment

    def test_add_on_request_with_plan_is_feedback_not_clarify(self):
        """ADR-0011 决策 2 点名的历史缺口：'还想加个喝咖啡的地方'应判 feedback，
        不是 clarify（旧世界 classify_dialogue_act 识别不出来才兜底成 ambiguous）。"""
        context = _with_plan_context(
            "今天下午想和朋友出去玩几小时，4 个人 2 男 2 女，别离家太远。",
            ["娱乐·剧本杀馆（14:00）", "用餐·川味小厨（17:00）"],
        )
        judgment, _ = self._classify(context, "还想加个喝咖啡的地方", True)
        assert judgment.label == "feedback", (
            f"追加请求应判 feedback，实际 {judgment.label}（{judgment.rationale}）"
        )

    def test_context_sensitive_tiredness_low_energy_plan_stays_chitchat(self):
        """同一句「有点累了」：方案本身已是独处放空的低强度安排时，应共情而非
        改方案（8 场景少样本设计初衷之一）。"""
        context = _with_plan_context(
            "这周加班加得想吐，下午想一个人安安静静待几个小时再回家。",
            ["休闲·城市书房（14:00）", "用餐·安静茶室（16:30）"],
        )
        judgment, _ = self._classify(context, "有点累了", True)
        assert judgment.label == "chitchat", (
            f"低强度独处方案下的『有点累了』应共情，不擅自改方案，实际 {judgment.label}"
        )

    def test_context_sensitive_tiredness_packed_plan_becomes_feedback(self):
        """同一句「有点累了」：方案是连轴转的紧凑局时，应识别为想放慢节奏的反馈。"""
        context = _with_plan_context(
            "今晚和兄弟出来撸串喝点酒，人均 50 左右就行",
            ["娱乐·台球厅（18:00）", "用餐·老张烧烤（19:30）", "娱乐·清吧续摊（21:30）"],
        )
        judgment, _ = self._classify(context, "有点累了", True)
        assert judgment.label == "feedback", (
            f"紧凑连轴转方案下的『有点累了』应判反馈想放慢节奏，实际 {judgment.label}"
        )

    def test_strong_distance_complaint_with_plan_is_feedback(self):
        context = _with_plan_context(
            "周五晚上和室友 4 个人想去 K 歌，预算别太贵",
            ["娱乐·量贩式 KTV（19:30）", "用餐·湘味小馆（21:30）"],
        )
        judgment, _ = self._classify(context, "KTV 有点远，能不能换近一点的", True)
        assert judgment.label == "feedback", judgment

    def test_pure_confirm_with_plan(self):
        context = _with_plan_context(
            "周日下午带着女朋友去看个展，顺便找个安静能聊天的地方吃饭。",
            ["文化·当代美术馆（14:00）", "用餐·安静小馆（16:30）"],
        )
        judgment, _ = self._classify(context, "就这样挺好，给我预约吧", True)
        assert judgment.label == "confirm", judgment
