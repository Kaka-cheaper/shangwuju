"""agent.thought_humanizer —— 把内部 agent_thought 翻译成用户向 user_text。

设计哲学：
- 后端 thought 兼具两种受众：开发者 trace（含权重 / 蓝图 ID / fallback 路径）+ 用户视野
- 单一 text 字段无法兼顾——本模块在 SSE 出口处统一注入 user_text
- 黑名单（调试型）→ user_text=None → 前端隐藏
- 灰名单（关键词驱动）→ 重写成用户向叙述
- 白名单（已经是人话）→ 透传

调用入口：humanize_thought(text: str) -> str | None
- 返回 None 表示前端应隐藏该 thought
- 返回 str 表示前端展示这段（可能与 text 相同）

为什么不用 LLM 翻译：
- 多一次 LLM 调用增加延迟
- 后端 thought 模板化程度高，关键词匹配足够覆盖 95% 情况
- 对蓝图 LLM rationale 的特例处理：保留原文（已经是人话）

不负责：
- 心跳节奏（在 _safe_stream / sse_adapter 处理）
- 翻译失败后 fallback 策略（由调用方决定 None=隐藏 vs 原文兜底）
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# ============================================================
# 黑名单：纯调试型 thought，前端不展示
# ============================================================

_BLACKLIST_PATTERNS = [
    # 权重 / planner 内部参数
    r"权重\s*[:：]?\s*舒适",
    r"出 plan 第 \d+ 次",
    # 蓝图层（评委不需看「蓝图 5 段：用户计划...」这种第三人称）
    r"^蓝图\s*\d+\s*段[:：]",
    r"LLM 蓝图（第 \d+ 次）",
    r"LLM 蓝图生成第 \d+ 次失败",
    r"蓝图 Critic[:：]",
    # fallback 路径（已切回规则规划/ILS 兜底）
    r"已切回规则规划",
    r"切换重排策略",
    r"ILS 迭代",
    r"ILS 算法兜底",
    r"更优解",
    r"stub\s*模式",
    r"客户端不可用",
    # critic 输出（hard_violation 已通过 replan_triggered 事件单独展示）
    r"^\[(CRITICAL|HARD|ERROR|WARN)\]",
    r"硬\s*\d+\s*条",
    # 段决策诊断
    r"实际跨度",
    r"段决策",
    # refine 内部诊断
    r"诊断\s*thought",
]

_BLACKLIST_RE = [re.compile(p) for p in _BLACKLIST_PATTERNS]


# ============================================================
# 灰名单：关键词驱动的用户向重写
#   每条规则：(matcher_regex, rewriter_function)
#   rewriter 可读取 match.group / 原 text 自定义
# ============================================================

def _rewrite_candidates_ready(text: str, m: re.Match) -> str:
    """候选准备就绪：抽 POI / 餐厅数量给用户看。"""
    poi_count = m.group("poi") if m.lastgroup else None
    if poi_count is None:
        # 兜底 regex
        poi_match = re.search(r"POI\s*(\d+)\s*个", text)
        rest_match = re.search(r"餐厅\s*(\d+)\s*个", text)
        poi = poi_match.group(1) if poi_match else "几"
        rest = rest_match.group(1) if rest_match else "几"
        return f"找到 {poi} 个地方和 {rest} 家餐厅，正在挑选最合适的搭配……"
    return text  # 不会走到


def _rewrite_blueprint_assembled(_text: str, _m: re.Match) -> str:
    """蓝图通过 critic / 已组装 itinerary。"""
    return "方案搭好了，正在做最后检查……"


def _rewrite_critic_passed(_text: str, _m: re.Match) -> str:
    """方案验证通过。"""
    return "检查通过，方案准备好了"


def _rewrite_understanding(_text: str, _m: re.Match) -> str:
    """正在理解你的需求……"""
    return "正在理解你的需求……"


def _rewrite_planning_start(_text: str, _m: re.Match) -> str:
    """好的，让我帮你规划一下。"""
    # 已有 understanding + 候选 thought 进来，这条不强必要 → 隐藏
    return ""  # 空字符串 → 调用方转 None


def _rewrite_refine_start(_text: str, m: re.Match) -> str:
    """收到反馈，正在调整……/ 开始根据你的反馈调整……"""
    feedback = ""
    fb_match = re.search(r"「([^」]+)」", _text := m.string)
    if fb_match:
        feedback = fb_match.group(1)
    if feedback:
        return f"收到反馈：「{feedback}」，正在重新调整……"
    return "收到反馈，正在重新调整……"


_REWRITE_RULES: list[tuple[re.Pattern[str], Callable[[str, re.Match[str]], str]]] = [
    # 进度型
    (re.compile(r"正在理解你的需求"), _rewrite_understanding),
    (re.compile(r"^好的[,，]?\s*让我帮你规划"), _rewrite_planning_start),
    (re.compile(r"候选准备就绪"), _rewrite_candidates_ready),
    (re.compile(r"蓝图通过 critic|已组装 itinerary"), _rewrite_blueprint_assembled),
    (re.compile(r"方案验证通过"), _rewrite_critic_passed),
    # refine 型
    (re.compile(r"收到反馈|开始根据你的反馈|开始重新规划"), _rewrite_refine_start),
]


# ============================================================
# 主入口
# ============================================================

def humanize_thought(text: str) -> Optional[str]:
    """把内部 agent_thought 文本翻译为用户向叙述。

    返回 None 表示前端应隐藏该 thought（仅供后端 trace）。
    返回非空 str 表示前端展示这段（可能与原 text 相同）。
    """
    if not text or not text.strip():
        return None

    # 1. 黑名单：直接隐藏
    for pat in _BLACKLIST_RE:
        if pat.search(text):
            return None

    # 2. 灰名单：关键词驱动重写
    for pat, rewriter in _REWRITE_RULES:
        m = pat.search(text)
        if m:
            rewritten = rewriter(text, m)
            if not rewritten:  # 空字符串 → 隐藏
                return None
            return rewritten

    # 3. 白名单：透传（已是用户向自然语言，如 LLM chitchat / narrator 类输出）
    #    单条文本超过 200 字时截断
    if len(text) > 200:
        return text[:197] + "……"
    return text


# ============================================================
# 给 SSE payload 注入 user_text 的辅助函数（在 _safe_stream / sse_adapter 调用）
# ============================================================

def attach_user_text(payload: dict) -> dict:
    """在 agent_thought payload 上附加 user_text 字段。

    - payload 缺 text → 不动
    - 已有 user_text → 不覆盖（允许调用方显式传 user_text）
    - 否则按 humanize_thought 规则填充
    """
    if "text" not in payload:
        return payload
    if "user_text" in payload:
        return payload  # 调用方已经显式指定
    text = payload.get("text", "")
    if not isinstance(text, str):
        return payload
    user_text = humanize_thought(text)
    payload["user_text"] = user_text
    return payload
