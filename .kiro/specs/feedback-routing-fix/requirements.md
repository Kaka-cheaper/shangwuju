# Requirements Document

## Introduction

本 spec 修复「反馈无用」故障：用户在已有行程方案后输入反馈（如「感觉这个安排有点累，想要更轻松悠闲一些的」），Agent 未识别为反馈，而是当作全新规划需求处理，导致用户调整诉求完全丢失。

经端到端实验定位，根因在 `/chat/turn` → `USE_LANGGRAPH=1` → `router_node` 的三层反馈识别逻辑：

```text
| 层      | 逻辑                                          | 缺陷                                  |
|--------|----------------------------------------------|--------------------------------------|
| Layer 1 | feedback_detector 关键词 + 数字单位词典       | 词典覆盖窄，漏判大量自然反馈措辞         |
| Layer 2 | LLM router 6 类分类                          | 看不到「上一轮有 itinerary」上下文，无法区分反馈 vs 新需求 |
| Layer 3 | has_itinerary + <15字 + ambiguous/chitchat   | 字数限制武断，≥15字反馈直接漏          |
```

实测证据：

```text
| 输入                                          | 期望     | 实际（bug）                  |
|----------------------------------------------|---------|----------------------------|
| 太远了，3公里以内                              | feedback | feedback（强信号正常）       |
| 感觉这个安排有点累，想要更轻松悠闲一些的（17字） | feedback | PLANNING（反馈丢失）         |
| 太赶了 / 节奏太快 / 想轻松点 / 行程太满了       | feedback | feedback_detector 漏判       |
```

附带隐患：checkpointer（InMemorySaver）反序列化 `Poi / Restaurant / IntentExtraction / Itinerary` 等未注册类型，当前仅 warning，但提示「blocked in future version」，一旦 langgraph 升级被 block，跨 turn state 恢复失效，所有反馈（含强信号）都将判不成 feedback。

## Glossary

```text
| 术语                | 定义                                                          |
|--------------------|--------------------------------------------------------------|
| router_node         | LangGraph 入口路由节点，决定输入走 planning / feedback / chitchat |
| feedback_detector   | backend/agent/core/feedback_detector.py，关键词+正则启发式反馈判定 |
| route_kind          | 路由结果，feedback 走 refiner，planning 走 intent 重新规划      |
| has_itinerary       | 当前 session 是否已有行程方案（跨 turn 由 checkpointer 恢复）    |
| 强信号反馈           | 含明确关键词/数字单位的反馈（如「3公里以内」），现行逻辑能识别     |
| 语义类反馈           | 无数字单位、靠语义表达的反馈（如「太赶了」「想轻松点」），现行逻辑漏判 |
| checkpointer        | LangGraph InMemorySaver，thread_id=session_id 持久化跨 turn state |
```

## Requirements

### Requirement 1: 长反馈不再被误判为新需求

**User Story:** 作为用户，当我对已有行程提出反馈时（无论长短），我希望 Agent 识别这是反馈并调整方案，而不是推翻重来。

#### Acceptance Criteria

1. WHEN 用户在已有 itinerary 的 session 中输入语义上是反馈的内容 THE SYSTEM SHALL 将其路由为 feedback（走 refiner），而非 planning
2. WHEN 输入「感觉这个安排有点累，想要更轻松悠闲一些的」且 has_itinerary=true THE SYSTEM SHALL 路由为 feedback
3. WHEN 输入「第二个活动我不太喜欢，能换一个吗」且 has_itinerary=true THE SYSTEM SHALL 路由为 feedback
4. WHEN 输入「整体节奏对孩子来说太赶了」且 has_itinerary=true THE SYSTEM SHALL 路由为 feedback

### Requirement 2: feedback_detector 扩充语义类反馈词典

**User Story:** 作为用户，我用自然口语表达反馈（不带数字单位）时，希望系统也能识别。

#### Acceptance Criteria

1. WHEN 输入含「节奏 / 轻松 / 太赶 / 太满 / 优雅 / 累 / 悠闲 / 不太好 / 不喜欢」等语义反馈词 THE feedback_detector.looks_like_feedback SHALL 返回 True
2. WHEN 输入「太赶了」「节奏太快」「想轻松点」「再优雅一点」「行程太满了」「能不能轻松些」「这个不太好」 THE feedback_detector SHALL 全部返回 True
3. WHEN 输入明确的新规划需求（如「今天下午想带孩子出去玩」）THE feedback_detector SHALL 返回 False（不误伤）

### Requirement 3: LLM router 获得「是否已有方案」上下文

**User Story:** 作为系统，我需要让分类逻辑知道当前是否已有方案，才能正确区分反馈和新需求。

#### Acceptance Criteria

1. WHEN router_node 调 LLM 分类且当前 session 已有 itinerary THE SYSTEM SHALL 让分类逻辑感知「这是已有方案后的追加输入」
2. WHEN has_itinerary=true 且 LLM 判非强 planning 信号 THE SYSTEM SHALL 对该输入倾向 feedback（不再受 <15字 限制）
3. WHEN has_itinerary=true THE SYSTEM SHALL 使 Requirement 1 与 Requirement 2 列举的所有反馈措辞都路由到 refiner

### Requirement 4: 不误伤「已有方案后的真新需求」

**User Story:** 作为用户，当我在已有方案后明确发起全新需求时，希望系统重新规划而不是当成反馈。

#### Acceptance Criteria

1. WHEN 用户在已有 itinerary 后输入明确的全新规划需求 THE SYSTEM SHALL 仍路由为 planning
2. WHEN 输入「周末想带爸妈去吃顿好的，换个安排」且 has_itinerary=true THE SYSTEM SHALL 路由为 planning
3. WHEN 输入「那这样，下午改成和朋友打球」且 has_itinerary=true THE SYSTEM SHALL 路由为 planning

### Requirement 5: checkpointer 反序列化类型注册

**User Story:** 作为系统维护者，我希望跨 turn 的状态持久化在 langgraph 升级后不失效。

#### Acceptance Criteria

1. WHEN LangGraph checkpointer 序列化/反序列化 AgentState 里的 Pydantic 业务类型 THE SYSTEM SHALL 注册这些类型（或等价机制），消除「blocked in future version」警告
2. WHEN 跨 turn 调用 graph THE SYSTEM SHALL 日志中无 `Deserializing unregistered type ...` 警告
3. WHEN 注册改动落地 THE SYSTEM SHALL 不改 AgentState schema 字段（仅加注册）

### Requirement 6: 不破现有强信号反馈路径与测试基线

**User Story:** 作为系统维护者，我希望修复反馈识别的同时不破坏已能工作的强信号反馈和现有测试。

#### Acceptance Criteria

1. WHEN 实施 Requirement 1-5 THE SYSTEM SHALL 保持「太远了，3公里以内」等强信号反馈仍正常工作
2. WHEN 修复完成 THE backend pytest（`pytest -x -q --ignore=tests/test_browser`）SHALL 全过
3. WHEN 修复完成 THE 前端 `pnpm run verify:all` SHALL 4/4 通过
4. WHEN router_node 在无 itinerary 的 session 处理输入 THE SYSTEM SHALL 行为与修复前一致（改动隔离在 has_itinerary 分支）
```
