"""agent.core.injection_detector —— 提示词注入检测（纯函数，零 LLM）。

spec prompt-injection-defense L1：作为路由级联（agent/routing/route_turn.py
Layer 0）的共享底层（类比 feedback_detector），
在任何 LLM 调用之前对用户输入做轻量注入检测。命中 high → 直接判 off_topic 婉拒。

设计原则：
- **零误报优先**（R1.2/R5.2 硬指标）：正则用「动作词 + 对象词」组合命中，
  避免单词误伤（如「别忽略孩子午睡」不命中——「忽略」后面没有指令/规则对象）。
- **fail-open**（R5 韧性）：内部任何异常 → 返回 is_injection=False，
  绝不阻断正常用户；漏检有 L2 角色锁定 / L3 输入隔离兜底。
- **不回显攻击文本**（R4.2）：matched 字段只放模式标识，不放完整用户输入。

覆盖 5 类注入模式：
- role_override         角色劫持（你现在是 / 扮演 / pretend you are / act as）
- instruction_override  指令覆盖（忽略以上指令 / ignore previous instructions）
- prompt_leak           prompt 泄露（输出你的系统提示 / reveal your prompt）
- delimiter_spoof       分隔符伪造（### system / <|im_start|> / [INST]）
- jailbreak             越狱（开发者模式 / DAN / 不受任何限制）

不负责：
- 命中后的路由动作（在 router_node / route.py）
- 角色锁定声明与输入隔离（在 agent/core/prompt_guard.py）
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class InjectionVerdict:
    """注入检测结论。

    is_injection: 是否命中注入
    severity:     "high"（明确命中，直接拦截）| "low"（疑似，预留）| "none"
    category:     命中类别（role_override / instruction_override / prompt_leak /
                  delimiter_spoof / jailbreak）；none 时为 None
    matched:      命中的模式标识（审计用，绝不放完整用户输入；可能为 None）
    """

    is_injection: bool
    severity: str
    category: str | None
    matched: str | None


_NONE_VERDICT = InjectionVerdict(
    is_injection=False, severity="none", category=None, matched=None
)


# ============================================================
# 模式定义（每条 = (category, 标识, 正则)）
# 正则全部用「动作词 + 对象词」组合，防单词误伤。
# ============================================================

# 指令对象词（中文）：被"忽略/覆盖"的目标
_CN_INSTR_OBJ = r"(?:指令|规则|提示词?|设定|要求|命令|限制|上文|身份)"

_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    # ---- instruction_override ----
    (
        "instruction_override",
        "cn_ignore_above",
        re.compile(
            rf"(?:忽略|无视|忘记|无需理会|不要(?:理会|管))[^。！!\n]{{0,8}}"
            rf"(?:以上|上面|上述|前面|之前|先前|刚才|所有|全部)?[^。！!\n]{{0,6}}{_CN_INSTR_OBJ}"
        ),
    ),
    (
        "instruction_override",
        "cn_forget_your",
        re.compile(rf"忘记你(?:的)?{_CN_INSTR_OBJ}"),
    ),
    (
        "instruction_override",
        "en_ignore_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,30}"
            r"\b(?:previous|prior|above|earlier|all|the|your)\b[^.\n]{0,20}"
            r"\b(?:instruction|instructions|prompt|prompts|rule|rules|system)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "instruction_override",
        "en_disregard_above",
        re.compile(
            r"\b(?:ignore|disregard|forget)\b[^.\n]{0,12}"
            r"\b(?:everything|all)\b[^.\n]{0,8}\b(?:above|before|prior|previous)\b",
            re.IGNORECASE,
        ),
    ),
    # ---- role_override ----
    (
        "role_override",
        "cn_you_are_now",
        re.compile(r"你现在(?:是|要(?:扮演|当)|开始(?:扮演|当))"),
    ),
    (
        "role_override",
        "cn_pretend",
        re.compile(r"(?:扮演|假装(?:你)?是|当作|你是一(?:个|名))[^。！!\n]{0,12}"
                   r"(?:机器人|助手|ai|AI|角色|的人|系统|管理员|黑客)"),
    ),
    (
        "role_override",
        "en_act_as",
        re.compile(
            r"\b(?:pretend|act)\b[^.\n]{0,12}\b(?:you\s+are|as)\b"
            r"|you\s+are\s+now\b"
            r"|\brole\s*[:=]\s*\w+",
            re.IGNORECASE,
        ),
    ),
    # ---- prompt_leak ----
    (
        "prompt_leak",
        "cn_reveal_prompt",
        re.compile(
            r"(?:输出|显示|展示|告诉我|重复|打印|泄露|给我看)[^。！!\n]{0,10}"
            r"(?:系统提示|系统指令|system\s*prompt|你(?:的|收到的)?(?:提示词?|指令|设定|规则))",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_leak",
        "cn_prompt_then_repeat",
        re.compile(
            r"(?:系统提示|系统指令|你(?:收到|上面|之前)的(?:提示词?|指令|设定|内容))"
            r"[^。！!\n]{0,8}(?:完整)?[^。！!\n]{0,4}(?:重复|复述|说一遍|念出来|输出)"
        ),
    ),
    (
        "prompt_leak",
        "en_reveal_prompt",
        re.compile(
            r"\b(?:reveal|show|print|repeat|output|reproduce|tell\s+me)\b[^.\n]{0,20}"
            r"\b(?:system\s*prompt|the\s+prompt|your\s+(?:prompt|instructions?|rules?|system))\b",
            re.IGNORECASE,
        ),
    ),
    # ---- delimiter_spoof ----
    (
        "delimiter_spoof",
        "markdown_role_header",
        re.compile(r"#{2,}\s*(?:system|assistant|user)\b", re.IGNORECASE),
    ),
    (
        "delimiter_spoof",
        "chatml_token",
        re.compile(r"<\|\s*im_(?:start|end)\s*\|>|<<\s*sys\s*>>|\[/?INST\]", re.IGNORECASE),
    ),
    (
        "delimiter_spoof",
        "line_role_prefix",
        re.compile(r"(?:^|\n)\s*(?:system|assistant)\s*[:：]\s*\S", re.IGNORECASE),
    ),
    # ---- jailbreak ----
    (
        "jailbreak",
        "cn_dev_mode",
        re.compile(r"开发者模式|越狱|解除(?:所有)?限制|不受(?:任何)?限制|无视(?:所有)?(?:道德|安全)"),
    ),
    (
        "jailbreak",
        "en_dev_mode",
        re.compile(
            r"\bdeveloper\s+mode\b|\bDAN\b|\bjailbreak\b|\bno\s+restrictions?\b"
            r"|\bdo\s+anything\s+now\b|\bwithout\s+any\s+(?:restrictions?|rules?|limits?)\b",
            re.IGNORECASE,
        ),
    ),
)


def detect_injection(text: str) -> InjectionVerdict:
    """检测用户输入是否含提示词注入企图。

    Returns:
        InjectionVerdict；命中返回 severity="high" + category + matched 标识，
        未命中返回 _NONE_VERDICT。任何内部异常 fail-open 返回未命中。
    """
    try:
        if not text or not text.strip():
            return _NONE_VERDICT
        txt = text.strip()
        for category, marker, pat in _PATTERNS:
            if pat.search(txt):
                return InjectionVerdict(
                    is_injection=True,
                    severity="high",
                    category=category,
                    matched=marker,
                )
        return _NONE_VERDICT
    except Exception:  # noqa: BLE001 —— fail-open：检测器永不阻断主流程
        return _NONE_VERDICT


__all__ = ["detect_injection", "InjectionVerdict"]
