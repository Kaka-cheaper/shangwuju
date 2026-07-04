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
    - SOFT_CONSTRAINT → 本就是规则表 + 可选 LLM 兜底（`soft_constraint_sniffer.py`），
                  同理直接从 `route_turn.py` 调用，跑在脑子之前。
    - BOOKING / CONFIRM → **仍留在本模块**（两者都是纯关键词规则，零 LLM 依赖，
                  历史上就是本模块唯一"自己拥有"而非转发自邻居模块的逻辑），
                  同样前移到脑子调用之前。

  为什么 QUESTION/SOFT_CONSTRAINT/BOOKING/CONFIRM 都能安全前移到"脑子调用之前"：
        旧 Layer 3 无论 Layer 2（LLM）判了什么，只要 `has_itinerary` 就无条件重跑
        这四个子判定，命中就**覆盖** Layer 2 的结果（`route_turn.py` 旧代码：
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
    - 「确认」必须是**纯肯定**——含反馈 / 疑问 / 明确改 / 追加词的，都不算确认（"好的但太远"
      是反馈、"可以近一点吗"是请求），交回兜底。
    - 「追加」（还想喝杯咖啡）不在这里拦：它本就该走 feedback → refiner 增量合并；
      E-2-c 之后这类输入交给脑子直接判 feedback（见 `brain_prompt.py` 少样本），
      不再依赖"识别不出来 → 兜底"的间接路径。

  不负责：提问回答（itinerary_qa）、软约束气泡（soft_constraint_sniffer）、重规划
        （refiner）、脑子调用（agent/routing/brain.py）、RouteKind → graph 边映射
        （route_turn.py）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

from schemas.router import CtaChip, InputKind, RouterDecision

from .feedback_detector import looks_like_feedback
from .itinerary_qa import looks_like_question
from .soft_constraint_sniffer import looks_like_explicit_revise


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
# 【B1 词表剪枝纪律（2026-07-04 路演前小修批）】
# 级联分类的规则层是"命中即拍板、无兜底"的确定性层——每一条词目必须单独接近
# 百分百精度，召回损失由脑子（LLM，慢 2-4 秒但带上下文）补。判据：能想象一个
# 日常语境里词目出现但用户不是在确认，就剪掉或加边界条件（宁剪勿留）。
# 实锤碰撞（路由勘察程序化实测 + 本批复核）：单字"行"作子串命中"银行卡里没钱了"
# （银行）与"这个行程安排的，还行吧"（行程/还行）——从子串词表剪除，只保留
# "独立成句"形式（见 _CONFIRM_EXACT_UTTERANCES）；"不可以/不确定"类否定前缀
# 碰撞加 _NEGATION_PREFIXES 边界条件。被剪话术落到脑子是设计预期，不是回归。

# 纯肯定词（"ok" 用小写匹配，中文不受 lower 影响）；子串匹配 + 否定前缀边界
_CONFIRM_WORDS: tuple[str, ...] = (
    "好的", "好嘞", "好呀", "好啊", "可以", "确定", "没问题",
    "就这个", "就它", "就这样", "就这么定", "就酱", "挺好", "没意见",
    "听你的", "ok", "可以的", "妥了", "没毛病",
)

# "行"家族只认独立成句（整句去首尾标点/空白后恰为其一）——子串匹配碰撞面太大
# （银行/行程/还行/不行/自行车），独立成句时精度才接近百分百。
_CONFIRM_EXACT_UTTERANCES: frozenset[str] = frozenset(
    {"行", "行吧", "行啊", "行呀", "那行", "那行吧", "行行", "行行行"}
)

# 否定前缀（B1c 短词目碰撞审计）：词目起点紧跟在这些字后面时不算命中
# （"不可以""还不确定""先不预约"）。只看词目前一个字符，"没问题"这类
# 自带"没"的词表原词不受影响（检查的是词目外侧，不拆词目本身）。
_NEGATION_PREFIXES: tuple[str, ...] = ("不", "别", "没", "未", "非", "难")

# 独立成句判定用的首尾标点集（"行！""那行吧。"仍算独立成句）
_PUNCT_STRIP = " \t\r\n，。！？!?~～…、"

# 追加词：含这些是"加一项"（→ refiner），不是确认
_ADD_HINTS: tuple[str, ...] = ("加个", "加一", "还想", "再来", "再去", "再加", "顺便", "另外加")


def _hit_words_without_negation(text: str, words: tuple[str, ...]) -> bool:
    """词目子串命中，但跳过被否定前缀直接修饰的出现位置（B1c 边界条件）。"""
    for w in words:
        start = 0
        while True:
            idx = text.find(w, start)
            if idx == -1:
                break
            if idx == 0 or text[idx - 1] not in _NEGATION_PREFIXES:
                return True
            start = idx + 1
    return False


def looks_like_confirm(text: str) -> bool:
    """是不是「纯确认 / 采纳」。

    必须含确认词（子串词表·带否定前缀边界，或"行"家族独立成句），且不含
    反馈 / 疑问 / 明确改 / 追加——否则不是纯确认（交回兜底）。
    """
    if not text:
        return False
    t = text.strip()
    tl = t.lower()
    hit = _hit_words_without_negation(tl, _CONFIRM_WORDS) or (
        tl.strip(_PUNCT_STRIP) in _CONFIRM_EXACT_UTTERANCES
    )
    if not hit:
        return False
    if looks_like_feedback(t):
        return False  # "好的但太远了" 是反馈
    if looks_like_question(t):
        return False  # "可以近一点吗" 是请求
    if looks_like_explicit_revise(t):
        return False
    if any(h in t for h in _ADD_HINTS):
        return False  # "行，加个咖啡" 是追加
    return True


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
_BOOKING_WORDS: tuple[str, ...] = (
    "预约", "订位", "下单", "预定", "帮我订", "订吧", "约位", "去订", "帮我约", "约一下",
)

# B1c 短词目碰撞审计：撤销语境词——"取消预约/退订"含预约词但语义相反，
# 命中即排除（交回脑子），不给"确认预约"chip。
_BOOKING_CANCEL_HINTS: tuple[str, ...] = ("取消", "退订", "不订了", "不约了")


def looks_like_booking(text: str) -> bool:
    """是不是「主动发起预约 / 下单」的执行指令。

    含预约词（带否定前缀边界，B1c："先不预约"不算），且不是撤销语境（"取消
    预约"）、不是疑问（"可以预约吗"=提问）、不是反馈（"别预约太远"=约束）、
    不是明确改方案——这些都交回各自通道。
    """
    if not text:
        return False
    t = text.strip()
    if not _hit_words_without_negation(t, _BOOKING_WORDS):
        return False
    if any(h in t for h in _BOOKING_CANCEL_HINTS):
        return False
    if looks_like_question(t):
        return False
    if looks_like_feedback(t):
        return False
    if looks_like_explicit_revise(t):
        return False
    return True


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
