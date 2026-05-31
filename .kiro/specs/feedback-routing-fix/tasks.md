# Implementation Plan

## Overview

按 TDD 顺序修复反馈路由。Task 1-2 是独立基础改动（词典 + classify_input 上下文），Task 3 依赖前两者（router_node 整合），Task 4 独立（checkpointer），Task 5 回归验证依赖 1-4 全完成，Task 6 收尾。

## Task Dependency Graph

```text
Task 1（feedback_detector 词典）──┐
                                  ├─→ Task 3（router_node 重构）──┐
Task 2（classify_input 上下文）───┘                              ├─→ Task 5（回归）─→ Task 6（收尾）
                                                                 │
Task 4（checkpointer 注册，独立）────────────────────────────────┘
```

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1", "2", "4"], "rationale": "三个独立基础改动，可并行：词典扩充 / classify_input 上下文 / checkpointer 注册互不依赖" },
    { "wave": 2, "tasks": ["3"], "rationale": "router_node 重构依赖 Task 1（扩充词典）与 Task 2（新 classify_input 签名）" },
    { "wave": 3, "tasks": ["5"], "rationale": "端到端回归依赖 Task 1-4 全部完成" },
    { "wave": 4, "tasks": ["6"], "rationale": "收尾（problem.md + commit）依赖回归通过" }
  ]
}
```

- Task 1 与 Task 2 可并行（互不依赖）
- Task 3 依赖 Task 1（用扩充词典）+ Task 2（用新 classify_input 签名）
- Task 4 独立于 1/2/3，可任意时序
- Task 5 依赖 Task 1-4 全部完成
- Task 6 依赖 Task 5

## Tasks

- [x] 1. feedback_detector 扩语义词典 + 单测（R2）
  - 先写 backend/tests/test_feedback_detector.py：7 个漏判措辞断言 True（太赶了/节奏太快/想轻松点/再优雅一点/行程太满了/能不能轻松些/这个不太好）+ 3 个新需求措辞断言 False（今天下午想带孩子出去玩/周末带爸妈吃饭/换成和朋友打球）
  - 跑测试看红灯（确认现状漏判）
  - 在 backend/agent/core/feedback_detector.py 的 _FEEDBACK_KEYWORDS 增加：节奏类（节奏/太赶/赶/轻松/悠闲/慢一点/紧凑/太满/满）+ 主观评价类（不太好/不喜欢/一般/没意思/优雅/高级/普通）+ 疲惫类补全
  - 跑测试看绿灯；确认新需求用例仍 False（不误伤）
  - 验证命令：pytest tests/test_feedback_detector.py -v
  - _需求: R2.1, R2.2, R2.3_

- [x] 2. classify_input 增加 has_itinerary 上下文参数 + router prompt（R3）
  - 在 backend/agent/intent/prompts/router_prompt.py 新增 FEEDBACK_CONTEXT_HINT 常量（说明「用户已有方案 + 反馈措辞倾向 ambiguous + 全新场景仍 planning」）
  - 改 backend/agent/intent/router.py classify_input 签名加 has_itinerary: bool = False；has_itinerary=True 时在 messages 注入上下文提示
  - 写 backend/tests/test_router_context.py：mock client 验证 has_itinerary=True 时 messages 含上下文提示；has_itinerary=False 时不含（行为不变）
  - 验证命令：pytest tests/test_router_context.py -v
  - _需求: R3.1, R6.4_

- [x] 3. router_node 三层重构 + 集成测试（R1, R3, R4）
  - 先写 backend/tests/test_router_node_feedback.py（stub client）：
    - R1：has_itinerary=True + 4 长反馈 → route_kind=feedback
    - R4：has_itinerary=True + 2 真新需求 → route_kind=planning
    - R6.4：has_itinerary=False + 任意输入 → 不进新分支（行为同原逻辑）
  - 跑测试看红灯
  - 改 backend/agent/graph/nodes/router.py：Layer 1 保留；Layer 2 调 classify_input 传 has_itinerary；Layer 3 去掉 <15字 限制，改为「has_itinerary AND route_kind != planning → feedback」
  - 跑测试看绿灯
  - 验证命令：pytest tests/test_router_node_feedback.py -v
  - _需求: R1.1-R1.4, R3.2, R3.3, R4.1-R4.3_

- [x] 4. checkpointer 类型注册（R5）
  - 查 langgraph 实际版本的 serde 注册 API（langgraph.checkpoint.serde 或 InMemorySaver(serde=...)）
  - 在 backend/agent/graph/build.py 的 build_graph 内注册 AgentState 涉及的 Pydantic 类型（schemas.domain.Poi/Restaurant、schemas.intent.IntentExtraction、schemas.itinerary.Itinerary、schemas.router.InputKind/RouterDecision、schemas.tools.GetUserProfileOutput、agent.planning.critic._rules.types.*）
  - 写跨 turn 调用脚本/测试验证无 "Deserializing unregistered type" 警告
  - 不改 AgentState schema 字段
  - 验证命令：跑跨 turn graph 调用，grep 日志确认无 unregistered 警告
  - _需求: R5.1, R5.2, R5.3_

- [x] 5. 端到端回归 + 强信号反馈不破验证（R6）
  - 端到端脚本（stub 模式）：Turn1 规划 → Turn2 强信号反馈「太远了，3公里以内」→ 确认 distance 真变小（强信号不破）
  - 端到端脚本：Turn1 规划 → Turn2 长反馈「感觉这个安排有点累，想要更轻松悠闲一些的」→ 确认 route_kind=feedback（核心 bug 已修）
  - 跑全量 backend pytest：pytest -x -q --ignore=tests/test_browser → 729+ 全过
  - 跑前端 pnpm run verify:all → 4/4（本 spec 不动前端，应自然通过）
  - _需求: R6.1, R6.2, R6.3_

- [x] 6. 收尾：problem.md 记录 + git commit
  - 按 problem.md 格式追加本次会话日志（问题/方案/修改文件/效果/验证证据）
  - git commit（中文 message）；stage 范围仅本 spec 改动的文件 + spec 三件套
  - _需求: 全部_

## Notes

- 全程不动 LangGraph 拓扑（graph/build.py 节点与边不变），只动 InMemorySaver 构造参数
- Task 4 checkpointer 注册 API 需按实际 langgraph 版本验证；若该版本无对应 API，保留警告（不阻断功能），在 problem.md 记录
- stub LLM client 用于隔离测试（避免真 LLM 慢 + 不确定性）
- 改动隔离在 has_itinerary 分支：无方案 session 行为完全不变

