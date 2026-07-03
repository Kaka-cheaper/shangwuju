# -*- coding: utf-8 -*-
"""verify_schema_hardening —— Phase·Agent A 验证脚本。

目标：用真 LLM 跑 5 个输入样本，断言 schema 加固 + prompt 中文词典强约束生效：
    1. IntentExtraction 全字段非省略（companions / physical / dietary / experience 即使空也显式输出 []）
    2. tag 字段全部命中中文词典（不出现 "family" / "healthy" / "low-fat" 等英文）
    3. 部分语义断言：「家庭场景」必须抽到 companions ≥ 2、dietary 含「低脂」、physical 含「亲子友好」等
    4. RouterDecision 极简输入「你是谁」必须分类为 input_kind=meta 且 cta_chips 显式

跑法：
    # SKIPPED 模式（CI / 无 LLM key）
    $env:LLM_PROVIDER='stub'
    .venv\\Scripts\\python.exe -m scripts.verify_schema_hardening

    # 完整验证模式（需要真 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）
    $env:LLM_PROVIDER='openai-compatible'  # 或留空
    .venv\\Scripts\\python.exe -m scripts.verify_schema_hardening

退出码：
    0 = 全部通过 / SKIPPED
    1 = 至少一个样本失败
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# 确保能 import backend.* 模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _is_stub_mode() -> bool:
    return (os.getenv("LLM_PROVIDER") or "").strip().lower() == "stub"


def _bullet(ok: bool, msg: str) -> str:
    return ("  ✓ " if ok else "  ✗ ") + msg


# ============================================================
# 5 个样本（4 个 IntentExtraction + 1 个 RouterDecision）
# ============================================================

INTENT_SAMPLES: list[dict[str, Any]] = [
    {
        "id": "S-family",
        "input": "今天下午想和老婆孩子出去玩，孩子 5 岁，老婆减肥",
        "asserts": {
            "min_companions": 2,
            "must_have_dietary": ["低脂"],
            "must_have_physical": ["亲子友好"],
            "social_context_in": ["家庭日常"],
        },
    },
    {
        "id": "S-elderly",
        "input": "周日下午带外公外婆散步",
        "asserts": {
            "min_companions": 2,
            "must_have_physical": ["适合老人"],
            "max_distance_km": 3,
        },
    },
    {
        "id": "S-solo",
        "input": "我累死了想一个人安静一会",
        "asserts": {
            "max_companions": 0,
            "must_have_experience": ["独处舒缓", "安静聊天"],  # 任一即可
        },
    },
    {
        "id": "S-ambiguous",
        "input": "今天下午想出去玩",
        "asserts": {
            "min_ambiguous_fields": 1,
            "max_parse_confidence": 0.6,
        },
    },
]


ROUTER_SAMPLES: list[dict[str, Any]] = [
    {
        "id": "S-chitchat",
        "input": "你是谁",
        "asserts": {
            # ADR-0011 E-2-c：6→5 InputKind 塌缩，meta 併入 chitchat（语气差异
            # 交 tone 字段承载）。
            "input_kind": "chitchat",
        },
    },
]


# ============================================================
# 词典 / 字段必出验证（schema 层）
# ============================================================

REQUIRED_INTENT_FIELDS = (
    "companions",
    "physical_constraints",
    "dietary_constraints",
    "experience_tags",
    "social_context",
    "start_time",
    "duration_hours",
    "distance_max_km",
    "raw_input",
    "parse_confidence",
    "ambiguous_fields",
)


def _check_intent_fields_present(payload_dict: dict[str, Any]) -> list[str]:
    """检查 LLM 输出 dict 中所有必传字段都显式存在。

    注意：这里检查的是「LLM 是否显式输出该字段」，而不是 Pydantic 校验后的对象状态——
    所以我们直接拿 model_dump() 后的 dict 来看 key 集合（Pydantic v2 会把所有字段
    都序列化出来，但若 LLM 漏字段，model_validate 阶段就会失败并被外层捕获）。
    """
    missing = [f for f in REQUIRED_INTENT_FIELDS if f not in payload_dict]
    return missing


def _check_no_english_in_tags(intent_dict: dict[str, Any]) -> list[str]:
    """检查三类 tag + social_context 不出现明显英文/拼音/自创词。

    Pydantic Literal 校验已经能拦截非词典词，但这里再做一次 ASCII 字符检查
    作为额外信号（用于打印时给人看）。
    """
    suspicious: list[str] = []
    for field_name in ("physical_constraints", "dietary_constraints", "experience_tags"):
        for tag in intent_dict.get(field_name) or []:
            if not isinstance(tag, str):
                continue
            # 含 ASCII 字母 → 可疑（中文词典里都不含 ASCII 字母）
            if any("a" <= c.lower() <= "z" for c in tag):
                suspicious.append(f"{field_name}={tag!r}（含英文字母）")
    sc = intent_dict.get("social_context")
    if isinstance(sc, str) and any("a" <= c.lower() <= "z" for c in sc):
        suspicious.append(f"social_context={sc!r}（含英文字母）")
    return suspicious


# ============================================================
# IntentExtraction 单样本验证
# ============================================================


def _verify_intent_sample(sample: dict[str, Any]) -> tuple[bool, list[str]]:
    """跑一个 IntentExtraction 样本，返回 (是否通过, 行报告列表)。"""
    from agent.intent.parser import IntentParseError, parse_intent
    from agent.core.llm_client import get_llm_client

    sid = sample["id"]
    user_input = sample["input"]
    asserts = sample["asserts"]
    lines: list[str] = []
    lines.append(f"\n[Sample {sid}] input={user_input!r}")

    client = get_llm_client()
    try:
        intent = parse_intent(user_input, client=client, max_retries=1)
    except IntentParseError as e:
        lines.append(_bullet(False, f"parse_intent 失败：reason={e.reason}"))
        if e.last_validation_error:
            # 只打印前 200 字避免淹没控制台
            lines.append("    " + e.last_validation_error[:200])
        return False, lines

    payload = intent.model_dump()

    # 1. 字段必出（model_dump 后 key 必齐）
    missing = _check_intent_fields_present(payload)
    ok_fields = not missing
    lines.append(
        _bullet(ok_fields, f"全字段非省略：missing={missing}" if missing else "全字段非省略 ✓")
    )

    # 2. tag 不含英文
    suspicious = _check_no_english_in_tags(payload)
    ok_dict = not suspicious
    lines.append(
        _bullet(
            ok_dict,
            ("tag 词典出口 OK" if ok_dict else f"tag 出现非词典词：{suspicious}"),
        )
    )

    # 3. 语义断言
    sem_ok = True
    if "min_companions" in asserts:
        n = len(payload.get("companions") or [])
        cond = n >= asserts["min_companions"]
        sem_ok &= cond
        lines.append(_bullet(cond, f"companions 数量 ≥ {asserts['min_companions']} (实际 {n})"))
    if "max_companions" in asserts:
        n = len(payload.get("companions") or [])
        cond = n <= asserts["max_companions"]
        sem_ok &= cond
        lines.append(_bullet(cond, f"companions 数量 ≤ {asserts['max_companions']} (实际 {n})"))
    if "must_have_physical" in asserts:
        actual = set(payload.get("physical_constraints") or [])
        wanted = set(asserts["must_have_physical"])
        cond = bool(actual & wanted)
        sem_ok &= cond
        lines.append(
            _bullet(
                cond,
                f"physical_constraints 命中 {wanted} 任一 (实际 {actual})",
            )
        )
    if "must_have_dietary" in asserts:
        actual = set(payload.get("dietary_constraints") or [])
        wanted = set(asserts["must_have_dietary"])
        cond = bool(actual & wanted)
        sem_ok &= cond
        lines.append(
            _bullet(
                cond,
                f"dietary_constraints 命中 {wanted} 任一 (实际 {actual})",
            )
        )
    if "must_have_experience" in asserts:
        actual = set(payload.get("experience_tags") or [])
        wanted = set(asserts["must_have_experience"])
        cond = bool(actual & wanted)
        sem_ok &= cond
        lines.append(
            _bullet(
                cond,
                f"experience_tags 命中 {wanted} 任一 (实际 {actual})",
            )
        )
    if "social_context_in" in asserts:
        actual = payload.get("social_context")
        cond = actual in asserts["social_context_in"]
        sem_ok &= cond
        lines.append(
            _bullet(cond, f"social_context ∈ {asserts['social_context_in']} (实际 {actual!r})")
        )
    if "max_distance_km" in asserts:
        actual = payload.get("distance_max_km")
        cond = isinstance(actual, (int, float)) and actual <= asserts["max_distance_km"]
        sem_ok &= cond
        lines.append(_bullet(cond, f"distance_max_km ≤ {asserts['max_distance_km']} (实际 {actual})"))
    if "min_ambiguous_fields" in asserts:
        n = len(payload.get("ambiguous_fields") or [])
        cond = n >= asserts["min_ambiguous_fields"]
        sem_ok &= cond
        lines.append(_bullet(cond, f"ambiguous_fields 数量 ≥ {asserts['min_ambiguous_fields']} (实际 {n})"))
    if "max_parse_confidence" in asserts:
        c = payload.get("parse_confidence")
        cond = isinstance(c, (int, float)) and c <= asserts["max_parse_confidence"]
        sem_ok &= cond
        lines.append(_bullet(cond, f"parse_confidence ≤ {asserts['max_parse_confidence']} (实际 {c})"))

    return ok_fields and ok_dict and sem_ok, lines


# ============================================================
# RouterDecision 单样本验证
# ============================================================


def _verify_router_sample(sample: dict[str, Any]) -> tuple[bool, list[str]]:
    """ADR-0011 E-2-c：验证对象从退役的 `classify_input` 换成统一路由脑子
    `agent.routing.brain.classify_turn`（无方案场景，context_text 给最小占位）。"""
    from agent.core.llm_client import get_llm_client
    from agent.routing.brain import classify_turn

    sid = sample["id"]
    user_input = sample["input"]
    asserts = sample["asserts"]
    lines: list[str] = []
    lines.append(f"\n[Sample {sid}] input={user_input!r}")

    client = get_llm_client()
    judgment = classify_turn("（无会话历史）", user_input, False, client=client)
    if judgment is None:
        lines.append(_bullet(False, "classify_turn 失败：返回哨兵 None"))
        return False, lines

    payload = judgment.model_dump()
    payload["input_kind"] = payload.pop("label")

    # cta_chips 必须存在（即使空数组）
    chips_present = "cta_chips" in payload
    lines.append(_bullet(chips_present, "cta_chips 字段显式存在"))

    # input_kind 断言
    kind_ok = True
    if "input_kind" in asserts:
        actual = payload.get("input_kind")
        # InputKind 序列化后是小写字符串
        actual_str = actual if isinstance(actual, str) else getattr(actual, "value", str(actual))
        cond = actual_str == asserts["input_kind"]
        kind_ok = cond
        lines.append(_bullet(cond, f"input_kind == {asserts['input_kind']!r} (实际 {actual_str!r})"))

    # tone 必须是预期 4 类之一
    tone_ok = payload.get("tone") in ("warm", "neutral", "empathetic", "playful")
    lines.append(_bullet(tone_ok, f"tone ∈ 4 类之一 (实际 {payload.get('tone')!r})"))

    # reply_text 必须中文且非空
    reply = payload.get("reply_text") or ""
    reply_ok = bool(reply) and any("\u4e00" <= c <= "\u9fff" for c in reply)
    lines.append(_bullet(reply_ok, f"reply_text 含中文 (前 30 字: {reply[:30]!r})"))

    return chips_present and kind_ok and tone_ok and reply_ok, lines


# ============================================================
# 主入口
# ============================================================


def main() -> int:
    print("=== verify_schema_hardening · 真 LLM 端到端验证 ===")

    if _is_stub_mode():
        print("LLM_PROVIDER=stub → SKIPPED（不调真 LLM；CI 兼容模式）")
        return 0

    # 加载 .env（沿用项目惯例）
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    pass_count = 0
    fail_count = 0
    all_lines: list[str] = []

    print("\n--- IntentExtraction 4 样本 ---")
    for sample in INTENT_SAMPLES:
        ok, lines = _verify_intent_sample(sample)
        all_lines.extend(lines)
        for line in lines:
            print(line)
        if ok:
            pass_count += 1
        else:
            fail_count += 1

    print("\n--- RouterDecision 1 样本 ---")
    for sample in ROUTER_SAMPLES:
        ok, lines = _verify_router_sample(sample)
        all_lines.extend(lines)
        for line in lines:
            print(line)
        if ok:
            pass_count += 1
        else:
            fail_count += 1

    print("\n=== Summary ===")
    print(f"PASS: {pass_count}")
    print(f"FAIL: {fail_count}")
    total = pass_count + fail_count
    print(f"Result: {pass_count}/{total}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
