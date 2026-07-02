"""agent.routing.canonical_shortcut —— 壳2·字面短路（ADR-0011 决策 2）。

FP≈0 精确字面匹配「系统自己发出的 chips send 文本」→ 对应路由，替代旧 Layer 1.5
的规划信号表 fast path（那套是"像不像新规划"的模糊关键词启发式，表面形式无穷、
误吞面大，见 ADR-0011 背景 3）。壳2反过来只认"系统自己吐出来、用户点击后原样
回传的确定字面"，不做任何语义/关键词猜测——FP 天然趋零。

三个来源（单一真相源，谁都别在别处再抄一份）：
  ① PRIMARY_CTAS（agent/intent/prompts/router_prompt.py）—— router LLM 分类结果
     附带的引导 chip 白名单，点击 = 发起对应场景的完整规划请求。
  ② FLOOR_CLARIFY_CTAS（同上文件）—— fallback_decision 保守地板"有方案"分支发的
     三个澄清 chip（调整一下方案 / 重新规划一个 / 就这样挺好）。只在
     has_itinerary=True 时短路：这三句字面只会由地板气泡点击回传；无方案时它们
     字面出现纯属巧合，交回正常级联判定，不强行短路。
  ③ DEMO_SCENARIOS（本模块，单一真相源；api/scenarios.py 从本模块取用，不再自
     己维护一份）—— /scenarios 端点的 8 个演示场景 input 文案。这是断网/stub
     演示下"任意输入→引导气泡→点场景 chip→正常规划"的规划可达通道：LLM 挂了
     不要紧，只要用户点了场景卡片，canonical 文本能确定性把规划步骤打开。

设计取舍（为什么①③不管 has_itinerary，②要管）：
    ①③ 是完整、自洽的规划请求文案，无论会话中期还是首轮命中都应直接开规划
    （会话中期命中即等价于"重新规划一个"——ADR-0011 决策 2 已删掉"有方案+
    planning/ambiguous→强行归并 feedback"的兜底，这条路径本就该可达，不必
    再靠 has_itinerary 分支特判）。②天生只在"有方案"语境下才有意义（地板气泡
    只在 has_itinerary=True 时才会发出这三个 chip），锁 has_itinerary 是防御性
    校验，不是功能依赖。

不负责：LLM 分类（agent/intent/router.py:classify_input）、降级地板语义构造
（同上 fallback_decision）、对话行为判定（agent/core/dialogue_acts.py，Layer 3，
在壳2 之后才跑——"就这样挺好"字面命中即被壳2 拦下，不会重复触达 Layer 3）。
"""

from __future__ import annotations

from agent.core.dialogue_acts import build_confirm_decision
from agent.intent.prompts.router_prompt import FLOOR_CLARIFY_CTAS, PRIMARY_CTAS
from agent.intent.router import make_planning_decision
from agent.routing.outcome import RouteOutcome


# ============================================================
# ③ 演示场景 canonical 输入（单一真相源；api/scenarios.py 从本模块取用）
# ============================================================

DEMO_SCENARIOS: list[dict[str, str]] = [
    {
        "id": "S1",
        "title": "学生党 KTV 局",
        "input": "周五晚上和室友 4 个人想去 K 歌，预算别太贵",
        "icon": "🎤",
    },
    {
        "id": "S2",
        "title": "兄弟撸串夜宵",
        "input": "今晚和兄弟出来撸串喝点酒，人均 50 左右就行",
        "icon": "🍢",
    },
    {
        "id": "S3",
        "title": "家庭主线",
        "input": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
        "icon": "👨‍👩‍👧",
    },
    {
        "id": "S4",
        "title": "朋友 4 人",
        "input": "今天下午想和朋友出去玩几小时，4 个人 2 男 2 女，别离家太远。",
        "icon": "👫",
    },
    {
        "id": "S5",
        "title": "情侣看展",
        "input": "周日下午带着女朋友去看个展，顺便找个安静能聊天的地方吃饭。",
        "icon": "💑",
    },
    {
        "id": "S6",
        "title": "闺蜜下午茶",
        "input": "周末下午约了闺蜜想找个网红的地方拍拍照吃个下午茶。",
        "icon": "👯",
    },
    {
        "id": "S7",
        "title": "商务接待",
        "input": "下午临时被叫去接个外地客户，对方是商务人士，帮我安排下。",
        "icon": "💼",
    },
    {
        "id": "S8",
        "title": "独处放空",
        "input": "这周加班加得想吐，下午想一个人安安静静待几个小时再回家。",
        "icon": "🌿",
    },
]


# ============================================================
# 字面 → 路由映射（模块加载时算好，避免每轮重建）
# ============================================================

_PLANNING_LITERALS: frozenset[str] = frozenset(
    {c["send"] for c in PRIMARY_CTAS} | {s["input"] for s in DEMO_SCENARIOS}
)

_FLOOR_CLARIFY_SENDS: frozenset[str] = frozenset(c["send"] for c in FLOOR_CLARIFY_CTAS)


def canonical_shortcut_decision(
    user_input: str, *, has_itinerary: bool
) -> RouteOutcome | None:
    """壳2 命中 → RouteOutcome；不命中 → None（交回级联继续往下判）。"""
    text = (user_input or "").strip()
    if not text:
        return None

    if text in _PLANNING_LITERALS:
        return RouteOutcome(
            kind="planning",
            decision=make_planning_decision(text, reason="canonical_shortcut"),
        )

    if has_itinerary and text in _FLOOR_CLARIFY_SENDS:
        if text == "调整一下方案":
            return RouteOutcome(kind="feedback", decision=None)
        if text == "重新规划一个":
            return RouteOutcome(
                kind="planning",
                decision=make_planning_decision(text, reason="floor_clarify_replan"),
            )
        if text == "就这样挺好":
            decision = build_confirm_decision(text)
            if decision is not None:
                return RouteOutcome(kind="chitchat", decision=decision)

    return None


__all__ = ["DEMO_SCENARIOS", "canonical_shortcut_decision"]
