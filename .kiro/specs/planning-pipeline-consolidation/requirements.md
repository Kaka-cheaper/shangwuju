# Requirements Document

## Introduction

本 spec 合并两件事：(A) 梳理三条历史规划路线的隔离与死配置；(B) 修复真 LLM 实测发现的规划质量 bug。两者同源——都因「三路线并存 + 共享底层」导致认知混乱与质量缺陷。

经子代理代码级调查 + 真 LLM 8 场景实测，确认以下事实与缺陷。

### 三条历史路线现状（代码级确认）

```text
| 版本   | 触发                              | 核心代码              | 当前状态        |
|-------|----------------------------------|----------------------|----------------|
| V1 自写 | USE_LANGGRAPH=0 且 USE_REACT=0     | planning/planners/rule_planner.py | 仅 fallback / 旧端点 |
| V2 ReAct| USE_REACT_AGENT=1（V1 未命中）      | runtime/react_agent / orchestrator | 主体不执行（V3 命中即 return）|
| V3 LangGraph| USE_LANGGRAPH=1（.env 当前）★    | graph/build / nodes / sse_adapter | 主路径          |
```

### 致命发现：端点路线不一致

```text
| 端点          | 前端调用方           | 实际走的路线              |
|--------------|---------------------|-------------------------|
| /chat/turn    | sendMessage（首轮规划）| V3 LangGraph ✓          |
| /chat/confirm | 确认并预约            | V1（不读 USE_LANGGRAPH）  |
| /chat/refine  | 说说哪不对（反馈）      | V1（不读 USE_LANGGRAPH）  |
```

同一次会话：首轮规划走 V3，反馈/确认走 V1——**两套规划逻辑混用**。

### 死配置（USE_LANGGRAPH=1 主路径下不生效）

```text
| env                  | V3 主路径是否生效 | 读取位置                    |
|---------------------|-----------------|----------------------------|
| USE_LANGGRAPH        | ✓               | chat.py:195                |
| PLANNER_MODE         | ✓（传 V3 rule/llm 子模式）| chat.py + planner.py:47 |
| PLANNER_LLM_STRATEGY | ✗ 死配置         | 仅 rule_planner.py:1258（V1）|
| PLANNER_USE_REAL     | ✗ 死配置         | 仅 health.py:30（V1 端点）  |
| USE_REACT_AGENT      | ✗ 实质死配置     | chat.py:236（V1 命中前已 return）|
```

### 规划质量 Bug（真 LLM 8 场景实测）

```text
| Bug | 现象                                  | 实测证据                    |
|-----|--------------------------------------|----------------------------|
| 1   | 多活动场景餐厅排到主活动前/中间，时间倒置 | S4 14:05吃火锅再攀岩 / S3 轻食塞中间 |
| 2   | 偏离用户意图：凭空加主活动 + 餐厅类型错配 | S2 只要撸串→给「真人CS+火锅」|
| 3   | 意图抽取丢关键约束                     | S2 丢"撸串/烧烤" / S8 混入"安静聊天"|
```

Bug 1 根因：blueprint prompt 明确「节点顺序由 LLM 自主决定」「反序允许」，但**缺少用餐节点位置的软约束**（正餐该收尾 vs 夜宵/下午茶可灵活），LLM 自由排序时把餐厅排错位置。三条路线共用 generate_blueprint，所以同根。

## Glossary

```text
| 术语              | 定义                                              |
|------------------|--------------------------------------------------|
| V1 / V2 / V3      | 三条历史规划路线（自写规则 / ReAct / LangGraph）    |
| LLM-First         | planner 节点内部策略（LLM 出蓝图），非编排框架      |
| blueprint         | LLM 输出的中间节点序列（含 target_id + duration_min + 顺序）|
| assemble          | 蓝图 → 带时间的 Itinerary（忠实按蓝图顺序加时间）   |
| node_decider      | 纯规则决定节点 kind 列表（含"餐后"正确顺序，但 LLM-First 不用它）|
| 用餐位置          | 餐厅节点在多活动行程里排在主活动前/中/后            |
| 死配置            | env 在当前主路径下不被读取                          |
```

## Requirements

### Requirement 1: 用餐时段合理性

**User Story:** 作为用户，我希望正餐安排在合理的饭点时段（午餐 11:30-13:30 / 晚餐 17:00-19:30），不要下午刚出门（如 14:05）就给我安排正餐火锅。

> 注：餐厅排在主活动前/后的"纯顺序"问题不算 bug（家庭加餐、下午茶塞中间、KTV 先吃后唱都合理）。本需求只管「正餐不能排在明显非饭点的时段」。

#### Acceptance Criteria

1. WHEN 行程含正餐餐厅（非下午茶/甜品/咖啡类）THE SYSTEM SHALL 把正餐起始时段安排在午餐（11:00-13:30）或晚餐（17:00-20:00）窗口
2. WHEN S4「朋友下午出去玩」从 14:00 出发 THE SYSTEM SHALL NOT 在 14:05 这种非饭点时段安排正餐火锅
3. WHEN 用户场景是夜宵/KTV/下午茶等 THE SYSTEM SHALL 允许对应时段的餐饮（夜宵 21:00+、下午茶 14:00-16:30）
4. WHEN 行程时段与餐厅类型冲突（如下午 2 点排正餐）THE critic SHALL 能检出并触发重规划或调整时段

### Requirement 2: 规划忠实于用户意图

**User Story:** 作为用户，我说"只想撸串喝酒"，希望系统就安排撸串，不要凭空加我没要的主活动。

#### Acceptance Criteria

1. WHEN 用户表达单一诉求（只想吃/只想玩某一项）THE SYSTEM SHALL NOT 凭空添加用户未要求的主活动
2. WHEN 用户明确指定餐饮类型（撸串/烧烤/火锅/粤菜）THE SYSTEM SHALL 优先匹配该类型的餐厅，匹配不到时优雅降级并说明
3. WHEN S2「和兄弟撸串喝酒人均50」 THE SYSTEM SHALL 安排烧烤/夜宵类餐厅，不安排真人 CS 等无关主活动

### Requirement 3: 意图抽取保留关键约束

**User Story:** 作为系统，我需要在意图抽取阶段保留用户说的关键约束词，不要丢失或错配。

#### Acceptance Criteria

1. WHEN 用户输入含明确餐饮/活动关键词（撸串/烧烤/夜宵/K歌）THE SYSTEM SHALL 在 IntentExtraction 的相应字段保留这些约束
2. WHEN 用户场景是独处 THE SYSTEM SHALL NOT 在 experience_tags 混入"安静聊天"这类与独处矛盾的标签
3. WHEN 意图抽取完成 THE SYSTEM SHALL 使下游 blueprint 能据此匹配正确的候选类型

### Requirement 4: 端点路线统一到 V3

**User Story:** 作为用户，我希望首轮规划和反馈/确认走同一套 V3 LangGraph 逻辑，不要前后矛盾。

#### Acceptance Criteria

1. WHEN USE_LANGGRAPH=1 且用户点「说说哪不对」按钮 THE 反馈 SHALL 走 V3 LangGraph 路线（与 /chat/turn 一致）
2. WHEN 实现端点统一 THE SYSTEM SHALL 优先用「前端按钮改打 /chat/turn」的低成本方案（V3 已内建 router→refiner→execute→planner 反馈闭环），而非重写 V1 的 refine_real
3. WHEN /chat/confirm 派发执行类 Tool THE SYSTEM SHALL 与 V3 产出的 itinerary schema 兼容（confirm 不依赖 V1 规划逻辑）
4. WHEN 端点统一后 THE SYSTEM SHALL 保持 SSE 事件序列契约不变，且前端反馈交互体验不退化
5. WHEN 验证阶段 THE SYSTEM SHALL 对比「V1 refine」与「V3 feedback」对同一反馈的产出，确认 V3 质量不低于 V1

### Requirement 5: 三路线架构重构与清晰隔离

**User Story:** 作为维护者，我希望三条历史规划路线清晰隔离、死配置被清理、主路径一目了然，不被历史包袱误导。

#### Acceptance Criteria

1. WHEN 重构完成 THE SYSTEM SHALL 在 AGENTS.md §3.3.1 产出权威的「三路线 + 端点→路线映射 + env→生效路径 + 共享底层」架构说明
2. WHEN 重构完成 THE .env / .env.example SHALL 明确标注每个 planner 相关 env 对哪条路线生效（PLANNER_LLM_STRATEGY / PLANNER_USE_REAL / USE_REACT_AGENT 在 V3 主路径下标为「仅 V1 旧路径」）
3. WHEN weights_llm.py 顶部 "# FROZEN: 不被 graph 路径消费" 与实际 V3 planner.py:23 仍 import 矛盾 THE SYSTEM SHALL 修正注释与现状一致
4. WHEN 端点统一到 V3（R4）后 THE SYSTEM SHALL 评估 V2 ReAct 主体（react_agent.unified_agent / orchestrator.run_react_turn）是否还有触发路径；若已无 → 标注为 deprecated（不删，保留 fallback 兜底）
5. WHEN 重构完成 THE SYSTEM SHALL 用一张表说明每个共享底层模块（blueprint_llm / assemble_blueprint / critics_v2 / weights_llm / ils_planner / rule_planner）被哪些路线引用，让「改一处影响哪几条线」可查
6. THE SYSTEM SHALL NOT 删除 V1/V2 代码（fallback 兜底 + /chat/stream 等旧端点依赖），重构限于「隔离标注 + 死配置清理 + 文档化 + 矛盾注释修正」
7. WHEN 重构完成 THE CodeSee features.json SHALL 反映三路线的真实隔离关系（如适用）

### Requirement 6: 不破基线

**User Story:** 作为维护者，我希望修复不破坏现有测试和 demo 主路径。

#### Acceptance Criteria

1. WHEN 修复完成 THE backend pytest（`pytest -x -q --ignore=tests/test_browser`）SHALL 全过
2. WHEN 修复完成 THE 前端 `pnpm run verify:all` SHALL 4/4
3. WHEN 修复完成 THE 8 个 demo 场景真 LLM 实测 SHALL 规划质量不回归（之前评级"优"的 S1/S5/S6/S7 保持优）
4. WHEN Bug 1/2/3 修复 THE S2/S3/S4 真 LLM 实测 SHALL 从"差/中"提升到"良"以上
```
