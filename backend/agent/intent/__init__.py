"""agent.intent —— 意图理解 + 反馈刷新 + 文案输出（spec agent-directory-restructure）。

含 IntentParser / Refiner / Router / Narrator + 各自 prompt。
narrator 归 intent/ 因与 SOCIAL_CONTEXTS 9 选 1 词典强耦合（design.md D-RES-1）。
"""
