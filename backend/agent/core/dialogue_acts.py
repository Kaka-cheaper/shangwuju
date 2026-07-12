"""agent.core.dialogue_acts —— 会话内「确认 / 预约指令」的零成本规则识别（ADR-0011 E-2-c 精简）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
业务逻辑（锁定，E-2-c 收口说明——本模块的职责范围比 C2 时期收窄了）：

  历史：C2 时期本模块是"会话内对话行为统一判定"的收口点，`classify_dialogue_act`
        把提问（QUESTION）/ 预约（BOOKING）/ 确认（CONFIRM）/ 提约束（SOFT_CONSTRAINT）
        四种对话行为串成一条 Layer 3 级联，在 `route_turn.py` 的 Layer 2（LLM 分类）
        **之后**统一接管。

  E-2-c 改造：ADR-0011 决策 1/2 把 Layer 2 + Layer 3 塌缩成一次"路由脑子"调用
        （`agent/routing/brain.py`）。原 Layer 3 的四个子判定逐一重新归位：
    - QUESTION  → 本就是规则 + 数据查表（`itinerary_qa.py`），不需要过脑子，
                  `route_turn.py` 现在直接调 `itinerary_qa.build_question_decision`，
                  跑在脑子**之前**（零成本规则短路，同强信号反馈的地位）。
    - SOFT_CONSTRAINT → 对话轮路由规则层重构（2026-07-12）已删除其**路由角色**
                  （原"提约束·没说改 → 主动问气泡"的判定），改由路由脑子少样本
                  承接同一 UX（见 `agent/routing/brain_prompt.py` BRAIN_FEW_SHOTS
                  的 clarify + 换成X的 chip 样本）；`looks_like_explicit_revise`
                  判据独立到 `agent/core/revise_cues.py`，供本模块与
                  `itinerary_qa.py` 平权引用。
    - BOOKING / CONFIRM → **仍留在本模块**（两者都是纯关键词规则，零 LLM 依赖，
                  历史上就是本模块唯一"自己拥有"而非转发自邻居模块的逻辑），
                  同样前移到脑子调用之前。

  为什么 QUESTION/BOOKING/CONFIRM 都能安全前移到"脑子调用之前"：
        旧 Layer 3 无论 Layer 2（LLM）判了什么，只要 `has_itinerary` 就无条件重跑
        这几个子判定，命中就**覆盖** Layer 2 的结果（`route_turn.py` 旧代码：
        `if outcome is not None: return outcome`，不问 Layer 2 判的是什么）。
        这意味着"重排到 LLM 调用之前"是**行为不变的优化**——命中即覆盖的最终结果
        完全一样，只是命中时不再需要先烧一次昂贵的 LLM 调用（省调用、省延迟），
        不命中时才轮到脑子。

  BOOKING 与 CONFIRM 现在都映射到 route_kind="confirm"（ADR-0011 决策 1 的"确认"
  义务）而不再是旧世界的 "chitchat"——两者都是"认可/主动执行"的强表态，下游都是
  "引导到显式确认按钮，绝不自动下单"（L0 全局禁令 1），没有理由分裂成两个路由目标；
  两者都带同一枚「确认预约」action chip（`CONFIRM_CTA_CHIP`）——B3（2026-07-04）
  之前 CONFIRM 硬编码空 chips，是全系统确认出口里唯一无引导按钮的路径，现与
  booking / 脑子 confirm 的 `_apply_label_chip_policy` 对齐。

  判定顺序（同旧 C2，未变）：BOOKING 优先于 CONFIRM（"给我预约吧"不该先被 CONFIRM
  的宽松词表误吞——虽然两者当前都归 "confirm" 路由，顺序仍保留以维持
  `build_booking_decision` 的确认预约 chip 优先展示）。

  边界：
    - 「确认」必须**整句覆盖**——命中确认词后，剩余内容须全由覆盖度闸的填充
      集吸收，否则弃权（"好的但太远"覆盖不完"但太远"的实义残余、"可以近一点吗"
      覆盖不完"近一点吗"，都弃权交回兜底）。
    - 「追加」（还想喝杯咖啡）不在这里拦：它本就该走 feedback → refiner 增量合并；
      E-2-c 之后这类输入交给脑子直接判 feedback（见 `brain_prompt.py` 少样本），
      不再依赖"识别不出来 → 兜底"的间接路径。本模块不再需要专门的 `_ADD_HINTS`
      排除表——"行，加个咖啡"里"加个咖啡"覆盖度闸下就是非空残余，自动弃权，
      不必单独枚举追加词。

  【对话轮路由规则层重构（2026-07-12）：覆盖度闸收口】
    `looks_like_confirm` / `looks_like_booking` 原先各自手搓一套排除逻辑
    （confirm 的"纯肯定否则交回反馈/疑问/明改/追加"四连判、booking 的撤销
    语境/疑问/反馈/明改四道排除、`_NEGATION_PREFIXES` 否定前缀边界、
    `_CONFIRM_EXACT_UTTERANCES`"行"家族独立成句特判），现全部收口成一个共享
    判据：`agent.core.coverage_gate.covers`——命中锚点词后，用**覆盖度**
    （锚点 ∪ 冻结填充集是否覆盖整句）取代"逐项排除"。旧的四道排除表本质上都是
    "残余里有反馈/疑问/明改/追加内容 → 不覆盖"的具体案例，覆盖度闸结构性
    地统一吸收（不需要逐条排除表分别维护）；"行"家族独立成句同理——它的锚点
    就是整句本身，覆盖度天然成立，不再需要单独的 `_CONFIRM_EXACT_UTTERANCES`
    + 标点剥离特判。否定前缀边界（"不可以"）现由覆盖度闸的残余判据自然产出
    （"这样不可以"剥掉锚点"可以"后残余"这样不"非空 → 弃权），不再需要
    `_NEGATION_PREFIXES` 这个专门机制。

  不负责：提问回答（itinerary_qa）、"明说改"祈使词判定（agent/core/revise_cues.py）、
        覆盖度闸本身（agent/core/coverage_gate.py）、重规划（refiner）、
        脑子调用（agent/routing/brain.py）、RouteKind → graph 边映射
        （route_turn.py）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

from schemas.router import CtaChip, InputKind, RouterDecision

from .coverage_gate import covers


# ============================================================
# 预约确认 chip（BOOKING 唯一授权的执行入口，brain.py 的 confirm 标签复用同一枚）
# ============================================================

CONFIRM_CTA_CHIP = CtaChip(label="确认预约", send="确认预约", icon=None, action="confirm")
"""「确认预约」action chip——点击直接触发 `/chat/confirm`（replay 已挂好的
pending_actions），不发对话消息。`build_booking_decision` 与
`agent.routing.brain._apply_label_chip_policy`（label=="confirm" 时强制钉死）
共用同一枚，避免两处各造一个导致视觉/行为漂移。"""


# ============================================================
# 确认（CONFIRM 对话行为）
# ============================================================
# 【锚点词表纪律（覆盖度闸收口后延续 B1 词表剪枝精神，2026-07-04 → 2026-07-12）】
# 级联分类的规则层是"命中即拍板、无兜底"的确定性层——锚点词表只负责"哪些词
# 算数"，精度由覆盖度闸（残余是否为空）把关，不再需要每条锚点词自己套一层
# 排除逻辑。"行"独立成句（"行"/"行吧"/"那行"）不再靠子串词表+标点剥离特判
# ——它们的锚点就是整句本身（见 _CONFIRM_EXACT_UTTERANCES），覆盖度天然成立；
# "银行卡里没钱了"（银行）/"这个行程安排的，还行吧"（行程/还行）不会误命中，
# 因为子串词表根本不收录裸"行"，只有整句恰好等于独立形式才算。

# 纯肯定词（"ok" 用小写匹配，中文不受 lower 影响）；子串锚点，覆盖度闸把关精度
_CONFIRM_WORDS: tuple[str, ...] = (
    "好的", "好嘞", "好呀", "好啊", "可以", "确定", "没问题",
    "就这个", "就它", "就这样", "就这么定", "就酱", "挺好", "没意见",
    "听你的", "ok", "可以的", "妥了", "没毛病",
)

# "行"家族：整句恰为其一才算独立成句形式（子串匹配碰撞面太大——银行/行程/
# 还行/不行/自行车，独立成句时精度才接近百分百）。覆盖度闸对这类"锚点=整句"
# 的判定天然成立（残余必空），这里仍需要一个专门集合来判定"整句是否恰为
# 独立形式"本身（覆盖度闸不能替代"识别出这是行家族"这一步，只能替代"识别后
# 还要不要再排除"那一步）。
_CONFIRM_EXACT_UTTERANCES: frozenset[str] = frozenset(
    {"行", "行吧", "行啊", "行呀", "那行", "那行吧", "行行", "行行行"}
)

# 独立成句判定用的首尾标点集（"行！""那行吧。"仍算独立成句）
_PUNCT_STRIP = " \t\r\n，。！？!?~～…、"


def _confirm_anchors(text_lower: str) -> tuple[str, ...]:
    return tuple(w for w in _CONFIRM_WORDS if w in text_lower)


def looks_like_confirm(text: str) -> bool:
    """是不是「纯确认 / 采纳」。

    命中确认锚点词（子串词表，或"行"家族独立成句——锚点即整句），且覆盖度闸
    判定锚点 + 冻结填充集覆盖了整句——残余非空（反馈/疑问/明改/追加/否定前缀
    等任何实义内容）一律弃权交回兜底，不再逐项排除。
    """
    if not text:
        return False
    t = text.strip()
    tl = t.lower()

    if tl.strip(_PUNCT_STRIP) in _CONFIRM_EXACT_UTTERANCES:
        return True  # 锚点=整句，覆盖度天然成立

    anchors = _confirm_anchors(tl)
    if not anchors:
        return False
    return covers(tl, anchors)


def build_confirm_decision(text: str) -> RouterDecision | None:
    """纯确认 → 肯定 + 引导下一步（confirm 出口，不重规划）；否则 None。

    B3（2026-07-04）：带上与 booking / 脑子 confirm 路径同一枚「确认预约」action
    chip——此前这里硬编码 cta_chips=[]，使壳2 canonical「就这样挺好」与 Layer 1.8
    纯确认成为全系统确认出口里唯一无引导按钮的回复（reply_text 说"想订位…随时
    招呼我"却没有可点的入口）。仍守 L0 全局禁令 1：chip 只是显式确认按钮的入口，
    绝不自动下单。
    """
    if not looks_like_confirm(text):
        return None
    return RouterDecision(
        input_kind=InputKind.CONFIRM,  # ADR-0011 决策 1：确认独立出口，不再塞进 chitchat
        confidence=0.85,
        reply_text="好嘞，那就按这个来。想订位、出张分享海报，或者再改两笔，随时招呼我。",
        tone="warm",
        cta_chips=[CONFIRM_CTA_CHIP],
        rationale="dialogue_act_confirm",
    )


# ============================================================
# 预约指令（BOOKING / commit-to-execute 对话行为）
# ============================================================
# 区别于「确认」（好的/就这个=认可方案，弱 ack）：预约指令是**主动发起终态执行**
# （给我预约吧/帮我订/下单=strong commitment，Clark grounding 里的动作级证据）。
# 它绝不能落 feedback 重规划——而是给用户一个一键确认按钮，复用 /chat/confirm 真预约。
#
# 锚点词表纪律（覆盖度闸收口后，同 confirm）：复合锚点（"帮我订"/"给我预约"）
# 优先于裸动词（"预约"）——"给我预约吧"这类礼貌祈使前缀（给我/帮我）本身不
# 携带判别语义，但它不在覆盖度闸的冻结填充集里（铁律：填充集不因个别句式
# 补丁式加词），正确的收口方式是让锚点词表本身多收一个复合形式，把"给我/
# 帮我+动词"当一个整体锚点——这是"扩大规则自己的锚点词表"，不是"往共享填充
# 集里加词"，两者是不同层级的决策（见 coverage_gate.py 模块 docstring 铁律）。
_BOOKING_WORDS: tuple[str, ...] = (
    "给我预约", "给我订", "帮我订", "帮我约",  # 复合锚点：吸收"给我/帮我"祈使前缀
    "预约", "订位", "下单", "预定", "订吧", "约位", "去订", "约一下",
)


def _booking_anchors(text: str) -> tuple[str, ...]:
    return tuple(w for w in _BOOKING_WORDS if w in text)


def looks_like_booking(text: str) -> bool:
    """是不是「主动发起预约 / 下单」的执行指令。

    命中预约锚点词，且覆盖度闸判定锚点 + 冻结填充集覆盖了整句——残余非空
    （撤销语境"取消预约"、疑问"可以预约吗"、反馈"别预约太远"、明确改方案、
    否定前缀"先不预约"等任何实义内容）一律弃权交回兜底，不再逐项排除。
    """
    if not text:
        return False
    t = text.strip()
    anchors = _booking_anchors(t)
    if not anchors:
        return False
    return covers(t, anchors)


def build_booking_decision(text: str) -> RouterDecision | None:
    """预约指令 → 回引导 + 一键「确认预约」chip（action=confirm，点击走真 confirm）。"""
    if not looks_like_booking(text):
        return None
    return RouterDecision(
        input_kind=InputKind.CONFIRM,  # ADR-0011 决策 1：确认独立出口
        confidence=0.9,
        reply_text="好的，点一下「确认预约」就帮你把整桌都锁定（订位 / 门票 / 加购一并搞定）。",
        tone="warm",
        # 不带 emoji icon（前端对 action=confirm 的 chip 用 lucide Check 图标 + 主题实心按钮渲染，
        # 避免 label/icon 双对钩 + emoji 塑料感）。
        cta_chips=[CONFIRM_CTA_CHIP],
        rationale="dialogue_act_booking",
    )


__all__ = [
    "CONFIRM_CTA_CHIP",
    "looks_like_confirm",
    "build_confirm_decision",
    "looks_like_booking",
    "build_booking_decision",
]
