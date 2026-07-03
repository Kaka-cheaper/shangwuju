"""router_prompt —— cta chip 常量（ADR-0011 E-2-c 精简）。

历史：Phase 0.8 时本模块还装着旧 `classify_input`（6 类分类器）的 system prompt
+ few-shot（`ROUTER_SYSTEM_PROMPT`/`ROUTER_FEW_SHOTS`/`FEEDBACK_CONTEXT_HINT`）。
ADR-0011 E-2-c 把 `classify_input` 整体退役（见 `agent/intent/router.py` 模块
docstring），这些旧 prompt 内容随之删除——统一路由脑子的 prompt 在
`agent/routing/brain_prompt.py`，不在这里。

本模块现在只剩**引导按钮的受控词表**——这是跨多处消费的单一真相源（壳2
canonical 字面短路 / 壳3 保守地板 / 路由脑子的 cta_chips 白名单，都从这里取，
不各自维护一份）：
- `PRIMARY_CTAS`：8 个演示场景的简化引导 chip。
- `FLOOR_CLARIFY_CTAS` / `FLOOR_REPLAN_SEND`：地板澄清三选项。

不负责：
- 路由脑子的 prompt 与调用（agent/routing/brain.py + brain_prompt.py）。
- 渲染（frontend/components/ChitchatBubble.tsx）。
"""

from __future__ import annotations


# ============================================================
# 引导按钮白名单（cta_chips.send 必须精确等于其中之一）
# ============================================================

# 主线引导（最常用，覆盖 8 演示场景的简化版）
PRIMARY_CTAS = [
    {
        "label": "陪老婆孩子",
        "send": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
        "icon": "👨‍👩‍👧",
    },
    {
        "label": "和朋友热闹",
        "send": "今天下午想和朋友出去玩几小时，4 个人 2 男 2 女，别离家太远。",
        "icon": "👫",
    },
    {
        "label": "陪父母散步",
        "send": "周日下午想带外公外婆出去走走，别走太远他们腿不好。",
        "icon": "👴",
    },
    {
        "label": "一个人放空",
        "send": "这周加班加得想吐，下午想一个人安安静静待几个小时再回家。",
        "icon": "🌿",
    },
    {
        "label": "陪女朋友",
        "send": "周日下午带着女朋友去看个展，顺便找个安静能聊天的地方吃饭。",
        "icon": "💑",
    },
    {
        "label": "闺蜜下午茶",
        "send": "周末下午约了闺蜜想找个网红的地方拍拍照吃个下午茶。",
        "icon": "👯",
    },
    {
        "label": "商务接待",
        "send": "下午临时被叫去接个外地客户，对方是商务人士，帮我安排下。",
        "icon": "💼",
    },
    {
        "label": "妈妈生日",
        "send": "周日是我妈生日，全家 6 个人想一起出去吃顿好的，她想吃粤菜。",
        "icon": "🎂",
    },
]


# 保守地板（ADR-0011 决策 2 / E-1）"有方案"分支的三个澄清 chip。
# 送出文本是**壳2 canonical 短路**的常量表来源之一（agent/routing/canonical_shortcut.py）：
# 用户点击后原样回传，字面精确匹配即可确定性路由，不依赖 LLM/关键词猜测。
# 不进 PRIMARY_CTAS/_WHITELIST_SENDS——这三个 chip 由 fallback_decision 直接构造，
# 不经 classify_input 的 LLM 白名单校验，两套白名单职责不同不可混用。
FLOOR_REPLAN_SEND = "重新规划一个"
"""地板澄清 chip「重新规划一个」的 canonical send 文本(单一真相源)。

这五个字本身不含任何出行要素,语义是「重做**我的**需求」——消费点(主聊天
intent 路径 / 房间 _trigger_fresh_plan)识别到这个字面时必须复用上一事件的
raw_input 重解,否则会把它当新需求解析出空泛意图(E-1 已知缺口修复,
ADR-0011 落地状态节有案)。"""

FLOOR_CLARIFY_CTAS = [
    {"label": "调整一下方案", "send": "调整一下方案", "icon": "🛠️"},
    {"label": "重新规划一个", "send": FLOOR_REPLAN_SEND, "icon": "🔄"},
    {"label": "就这样挺好", "send": "就这样挺好", "icon": "👍"},
]


__all__ = ["PRIMARY_CTAS", "FLOOR_REPLAN_SEND", "FLOOR_CLARIFY_CTAS"]
