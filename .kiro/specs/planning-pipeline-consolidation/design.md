# Design Document

## Overview

合并解决「三路线架构混乱」+「规划质量 bug」。四块改动：
- 块 A（R1）：用餐时段合理性——blueprint prompt 加饭点约束 + critic 新增 MEAL_TIME_UNREASONABLE 校验
- 块 B（R2+R3）：意图忠实——intent parser prompt 强化关键约束保留 + blueprint prompt 强化「不凭空加活动」
- 块 C（R4）：端点统一——前端「说说哪不对」按钮改打 /chat/turn（V3），不重写 V1 refine_real
- 块 D（R5）：架构重构——三路线隔离标注 + 死配置清理 + AGENTS.md 架构表 + 矛盾注释修正

全程不动 LangGraph 拓扑（graph/build.py 节点与边不变）；不删 V1/V2 代码（保留 fallback）。

## Architecture

### 当前路线全貌（子代理代码级确认）

```text
前端交互 → 端点 → 路线
  sendMessage（首轮/打字反馈）→ /chat/turn → V3 LangGraph（USE_LANGGRAPH=1）★
  「说说哪不对」按钮 + 反馈      → /chat/refine → V1（不读 USE_LANGGRAPH）✗ 不一致
  「确认并预约」               → /chat/confirm → V1 stub_confirm（执行类 Tool 派发）

V3 LangGraph 内部（graph/build.py，不动）：
  router → [intent | refiner | chitchat]
         intent/refiner → search_pois/restaurants/profile（并行）→ execute_collect
         → planner（generate_blueprint = LLM-First）→ assemble → critic
            ├ 通过 → narrate → END
            └ 违规 → replan_router → llm_backprompt(回planner) | ils_replan | give_up

共享底层（改一处影响多线）：
  blueprint_llm.generate_blueprint   ← V3 planner + V1 llm_first
  assemble_blueprint                 ← V3 assemble + V1 llm_first
  critics_v2 / _rules                ← V3 critic + V2 output_validator + V1
  weights_llm（FROZEN 注释过时）       ← V3 planner 仍 import + V1 hybrid
  ils_planner / rule_planner          ← V1 主 + V3 replan 兜底
```

### 死配置（USE_LANGGRAPH=1 下不生效）

```text
| env                  | V3 生效? | 读取位置              |
|---------------------|---------|----------------------|
| USE_LANGGRAPH        | ✓       | chat.py:195          |
| PLANNER_MODE         | ✓       | chat.py + planner.py:47（V3 rule/llm 子模式）|
| PLANNER_LLM_STRATEGY | ✗       | rule_planner.py:1258（仅 V1）|
| PLANNER_USE_REAL     | ✗       | health.py:30（仅 V1 端点）|
| USE_REACT_AGENT      | ✗       | chat.py:236（V1 命中前已 return）|
```

## Components and Interfaces

### 块 A：用餐时段合理性（R1）

#### A1: blueprint prompt 加饭点约束

文件：backend/agent/planning/blueprint/prompts/blueprint_prompt.py

在 BLUEPRINT_SYSTEM_PROMPT 加一段「用餐时段规则」：
- 正餐（非下午茶/甜品/咖啡/酒馆夜宵）的节点起始应落在午餐 11:00-13:30 或晚餐 17:00-20:00
- 下午茶/甜品类可落 14:00-16:30；夜宵/酒类可落 21:00 之后
- preferred_start_time + 各 duration_min 推算下来若导致正餐落在非饭点（如 14:00 出发先吃正餐），应调整节点顺序或 preferred_start_time
- 不强制餐厅排最后（KTV 先吃后唱、下午茶塞中间都允许）——只约束「正餐时段不离谱」

#### A2: critic 新增 MEAL_TIME_UNREASONABLE 校验

文件：backend/agent/planning/critic/_rules/types.py + checks.py

- types.py：ViolationCode 加 MEAL_TIME_UNREASONABLE = "meal_time_unreasonable"
- checks.py：新增 check 函数，遍历 itinerary.nodes，对 target_kind=restaurant 且 cuisine 非「下午茶/甜品/咖啡/酒馆」类的节点，校验 start_time 是否落在午餐/晚餐窗口；离谱 → WARNING（不阻断，但触发 narration 提示 + 可选 backprompt）
- 餐厅 cuisine 分类：正餐类（火锅/粤菜/日料/烧烤/中餐等）vs 茶点类（下午茶/甜品/咖啡/轻食）——用 cuisine 字段 + tags 判定
- Severity 用 WARNING（不阻断 demo；离谱时 narration 文案体现「时段已调整」即可）；若实测 LLM 不自纠再升 CRITICAL

### 块 B：意图忠实（R2 + R3）

#### B1: intent parser prompt 强化关键约束保留（R3）

文件：backend/agent/intent/prompts/intent_parser_prompt.py

- 加规则：用户输入含明确餐饮/活动关键词（撸串/烧烤/夜宵/K歌/喝酒）时，必须在 experience_tags 或 dietary_constraints 保留对应词（在词典范围内选最接近的）
- 加反例约束：独处场景（social_context=独处放空）不得在 experience_tags 放「安静聊天」（一个人不聊天）——改用「独处舒缓/安静」
- 不发明词典外 tag（沿用现有词典出口防御）

#### B2: blueprint prompt 强化「不凭空加活动」（R2）

文件：backend/agent/planning/blueprint/prompts/blueprint_prompt.py

- 加规则：若用户意图是单一诉求（只想吃/只想玩某项，experience_tags 稀疏 + 无明确主活动诉求），nodes 应≤ 用户实际表达的活动数，不凭空加 POI 主活动
- 「撸串喝酒」类 → 单 restaurant 节点足矣（参考现有「单段允许」规则，强化为「单诉求就单段」）
- 餐厅类型匹配：选 target_id 时优先匹配用户指定 cuisine（烧烤匹配烧烤摊，不选火锅）

#### B3: 验证 mock 数据有对应候选

确认 mock_data 有烧烤类餐厅（R046 老王烧烤摊等之前 task 加过）；若 search 没返回烧烤候选，是 search_restaurants 的 tag 匹配问题，需查。

### 块 C：端点统一到 V3（R4）

#### C1: 前端「说说哪不对」改打 /chat/turn

文件：frontend/lib/store.ts（refine action）

背景澄清——反馈有两套独立实现，底层共享 refine_intent 但外层编排不同：
- V1：api/_streams/refine_real.py（/chat/refine 端点）→ plan_itinerary_with_mode（V1 三档）
- V3：agent/graph/nodes/refiner.py（/chat/turn 里 router 判 feedback 触发）→ graph planner 闭环

改动方案（最省成本，不碰任何后端 refine 实现）：
- 现状：前端 refine() 打 `${API_BASE}/chat/refine`（命中 V1）
- 改为：打 `${API_BASE}/chat/turn`，message = 反馈文本（V3 router 判 feedback → refiner 节点）
- **后端 refine_real.py 完全不动**（保留作 fallback + /chat/refine 旧端点向后兼容）
- **V3 refiner 节点完全不动**（feedback-routing-fix spec 已验证能处理反馈）
- 净改动 = 前端一行 URL

效果：用户点「说说哪不对」后反馈走 V3，与首轮规划（也是 V3）统一。
风险：/chat/turn 的 V3 feedback 依赖 checkpointer 跨 turn 恢复 itinerary（同 session_id）——前端 refine 时 session_id 不变，满足条件。

#### C1.1: 前端 refinement 事件依赖排查（C1 副作用）

改 URL 前必须 grep 前端哪些组件依赖 `refinement_start` / `refinement_done` SSE 事件：
- RefinementDialog（反馈输入弹框）——大概率只管输入，不依赖事件
- ComparisonView（refine 前后对比视图）——可能依赖 refinement_done 取新旧方案
- store 的 event-handlers——可能有 refinement 事件处理分支

排查结果决定：
- 若无组件依赖这俩事件 → 直接改 URL（C1 完成）
- 若 ComparisonView 依赖 → 两选一：(a) V3 也补发 refinement 事件；(b) ComparisonView 改用 V3 现有事件（如 itinerary_ready 的前后快照）；(c) 暂时接受对比视图在反馈时不渲染（降级，记录）
- 这一步是 C1 的前置 gate，tasks 里先排查再改 URL

#### C2: 验证 V3 feedback 质量 ≥ V1 refine

对同一反馈（如「太远了，3公里以内」「想轻松点」），对比：
- 旧：/chat/refine（V1）产出
- 新：/chat/turn 带反馈（V3）产出
确认 V3 的 distance/duration 调整正确、行程合理。

#### C3: /chat/confirm 不动

confirm 是执行类 Tool 派发（reserve/buy_ticket/share_message），不依赖规划逻辑，与 V3 itinerary schema 已兼容（confirm 从 SESSION_STORE 取 itinerary，V3 已同步写入）。保持现状。

### 块 D：架构重构（R5）

#### D1: AGENTS.md §3.3.1 加权威架构表

在现有编排纪律段补充「三路线 + 端点映射 + env 生效表 + 共享底层表」（即本 design 的 Architecture 段精炼版）。

#### D2: .env / .env.example 死配置标注

- PLANNER_LLM_STRATEGY / PLANNER_USE_REAL / USE_REACT_AGENT 注释段加「⚠ 仅 V1 旧路径（USE_LANGGRAPH=0）生效；当前 V3 主路径不读」

#### D3: weights_llm.py 矛盾注释修正

- 顶部 "# FROZEN: 仅 ILS 路径，不被 graph 路径消费" 与 V3 planner.py:23 import get_planning_weights 矛盾
- 改为准确描述：「get_planning_weights 被 V3 planner + V1 hybrid 调用；ILS 专用部分 FROZEN」

#### D4: V2 ReAct deprecated 标注（端点统一后评估）

- C1 后，对话框反馈走 /chat/turn(V3)；评估 V2 react_agent.unified_agent 是否还有触发路径
- 若 USE_LANGGRAPH=1 恒命中第 1 层 → V2 主体不执行 → 在 react_agent.py 顶部加 deprecated 注释（不删，保留 USE_LANGGRAPH=0 fallback）

#### D5: CodeSee features.json 更新（如适用）

按 .codesee/prompts/sync.md，反馈路由 + 端点统一改动后跑 sync。

## Data Models

无新增 schema。仅 ViolationCode 枚举加 MEAL_TIME_UNREASONABLE 一个值。复用现有 IntentExtraction / PlanBlueprint / Itinerary / Violation。

## Error Handling

```text
| 场景                          | 处理                                      |
|------------------------------|------------------------------------------|
| MEAL_TIME_UNREASONABLE 触发   | WARNING 级 → narration 文案体现时段调整；不阻断 demo |
| 前端 /chat/turn 反馈但 checkpointer 无 itinerary | V3 router 判非 feedback → 当新规划（降级，不崩）|
| blueprint 加约束后 LLM 仍违规  | critic backprompt 重试（现有机制）         |
| 烧烤候选搜不到                 | search_restaurants 优雅返空 → blueprint 用次优候选 + rationale 说明 |
```

## Testing Strategy

### 单元 / 集成测试

- test_meal_time_critic.py（新增）：构造正餐排 14:00 的 itinerary → 断言 MEAL_TIME_UNREASONABLE 触发；正餐排 17:30 → 不触发；下午茶排 15:00 → 不触发
- test_intent_keyword_retention.py（新增）：「撸串喝酒」→ 断言 experience_tags/dietary 保留烧烤相关；「独处」→ 断言不含「安静聊天」
- 现有 test_blueprint_prompt.py / test_critics_v2*.py 不回归

### 真 LLM 端到端验证（评委真实路径）

- 复用之前的 8 场景评测脚本（临时），对比修复前后：
  - S2 撸串：应给烧烤类餐厅，不凭空加真人 CS（R2/R3）
  - S4 朋友：正餐不排 14:05（R1）
  - S1/S5/S6/S7：保持"优"不回归（R6.3）
- 端点统一验证（C2）：同反馈对比 V1 refine vs V3 feedback 质量

### 回归

- backend pytest 全过（R6.1）
- 前端 pnpm verify:all 4/4（R6.2）

## Verification Plan

```text
| 需求 | 验证方式                                          |
|-----|--------------------------------------------------|
| R1  | test_meal_time_critic + S4 真 LLM 实测正餐时段     |
| R2  | S2 真 LLM 实测不凭空加活动 + 餐厅类型匹配           |
| R3  | test_intent_keyword_retention + S2/S8 真 LLM 实测  |
| R4  | 前端 refine 走 /chat/turn + V1/V3 质量对比          |
| R5  | AGENTS.md 架构表 + .env 标注 + weights_llm 注释 review |
| R6  | 全量 pytest + 前端 verify + 8 场景不回归            |
```

## Correctness Properties

### Property 1: 正餐时段合理

正餐节点（非茶点类）的 start_time 必须落在午餐（11:00-13:30）或晚餐（17:00-20:00）窗口。验证：MEAL_TIME critic 单测 + S4 真 LLM 实测。

**Validates: Requirements 1.1, 1.2, 1.4**

### Property 2: 单一诉求不膨胀

单一诉求输入（如「只想撸串」）产出的 blueprint nodes 数 ≤ 用户表达的活动数，不凭空加 POI 主活动。验证：S2 真 LLM 实测。

**Validates: Requirements 2.1, 2.3**

### Property 3: 意图关键词保留

用户输入的明确活动/餐饮关键词（烧烤/K歌/撸串）保留在 IntentExtraction 相应字段。验证：test_intent_keyword_retention 单测 + S2 实测。

**Validates: Requirements 3.1, 3.3, 2.2**

### Property 4: 独处场景标签自洽

social_context=独处放空 时，experience_tags 不含「安静聊天」等需要同伴的标签。验证：单测。

**Validates: Requirements 3.2**

### Property 5: 端点统一后质量不降

端点统一后，V3 feedback 对同一反馈的产出质量 ≥ V1 refine（distance/duration 调整正确、行程合理）。验证：V1/V3 对比实测。

**Validates: Requirements 4.1, 4.5**

### Property 6: 不破坏既有架构边界

不删 V1/V2 代码、不动 graph/build.py 拓扑。验证：code review + 全量 pytest。

**Validates: Requirements 5.6, 6.1**

