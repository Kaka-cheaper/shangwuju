"""critic 内部规则模块（ADR-0008）。

把 13 个 check_xxx 拆出 critics_v2.py，让主文件只保留：
- 公开类型 / 枚举（ViolationCode / Severity / Violation）
- 入口（validate_itinerary / format_violations_for_llm）

本子目录：
- types.py    Violation / ViolationCode / Severity（避免循环 import）
- helpers.py  safe_load_pois / parse_hhmm / humanize_node 等 helper
- checks.py   13 个 check_xxx 函数
"""
