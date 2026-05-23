"""agent.planning.blueprint —— 蓝图生成层。

LLM 出 PlanBlueprint + 几个轻量 critic（含 spec planning-quality-deep-review R4 的
_age_aware_duration_critic）+ assemble_from_blueprint + node_decider（仅决 kind）。
"""
