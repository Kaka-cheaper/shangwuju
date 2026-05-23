"""agent.planning —— 规划主路径（spec agent-directory-restructure）。

含 blueprint（蓝图生成 + age-aware critic）/ critic（critics_v2 + social_compat）/
commute（lookup_hop）/ weights_llm 四类。

不放运行时框架（在 runtime/）、不放意图理解（在 intent/）。
"""
