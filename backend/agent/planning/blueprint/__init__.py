"""agent.planning.blueprint —— 蓝图生成层。

LLM 出 PlanBlueprint + assemble_from_blueprint + node_decider（仅决 kind）。

蓝图级 critic（曾含 spec planning-quality-deep-review R4 的
_age_aware_duration_critic）已确认无生产调用者，随 ADR-0009 决策 8 删除；
Itinerary 级校验统一在 agent/planning/critic/。
"""
