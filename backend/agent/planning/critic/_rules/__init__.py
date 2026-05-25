"""critics_v2 内部规则模块（spec code-modularization-refactor H6）。

把 11 个 _check_xxx 拆出 critics_v2.py（1000+ 行），让主文件只保留：
- 公开类型 / 枚举（ViolationCode / Severity / Violation）
- 入口（validate_itinerary / format_violations_for_llm）
- 反馈模式（compute_reward / _get_feedback_mode）

本子目录：
- types.py    Violation / ViolationCode / Severity / 权重常量（避免循环 import）
- helpers.py  _safe_load_pois / _parse_hhmm / _resolve_node_location 等 8 个 helper
- checks.py   11 个 _check_xxx 函数
"""
