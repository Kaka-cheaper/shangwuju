# Implementation Plan

## Overview

四块改动（A 用餐时段 / B 意图忠实 / C 端点统一 / D 架构重构）。A/B/D 相对独立可并行；C 有前置排查 gate（refinement 事件依赖）。每块改完用真 LLM 实测验证，全部完成后统一回归 + 收尾。

## Task Dependency Graph

```text
块A（用餐时段 critic+prompt）─┐
块B（意图忠实 prompt）────────┤
块D（架构文档+死配置+注释）────┼─→ Task 9 真LLM 8场景回归 ─→ Task 10 收尾
块C（C1排查→C2改URL→C3验证）──┘
```

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1", "2", "3", "6", "7"], "rationale": "块A(用餐时段)、块B(意图忠实)、块D(架构文档)互相独立，可并行" },
    { "wave": 2, "tasks": ["4", "5"], "rationale": "块C 端点统一：先排查 refinement 事件依赖(4)，再改前端 URL(5)" },
    { "wave": 3, "tasks": ["8"], "rationale": "各块单测 + prompt 调优后的针对性真 LLM 抽测" },
    { "wave": 4, "tasks": ["9"], "rationale": "真 LLM 8 场景全量回归(依赖 A/B/C/D 全完成)" },
    { "wave": 5, "tasks": ["10"], "rationale": "收尾(problem.md + commit + CodeSee sync)" }
  ]
}
```

## Tasks

- [x] 1. 块A-1：critic 新增 MEAL_TIME_UNREASONABLE 校验（R1）
  - types.py：ViolationCode 加 MEAL_TIME_UNREASONABLE = "meal_time_unreasonable"
  - checks.py：新增 check 函数——遍历 itinerary.nodes，对 target_kind=restaurant 且 cuisine 属正餐类（火锅/粤菜/日料/烧烤/中餐等，非下午茶/甜品/咖啡/酒馆）的节点，校验 start_time 是否落在午餐(11:00-13:30)或晚餐(17:00-20:00)；离谱 → WARNING
  - 先写 test_meal_time_critic.py：正餐排 14:00 → 触发；排 17:30 → 不触发；下午茶排 15:00 → 不触发；夜宵排 21:30 → 不触发
  - 跑红→绿
  - 验证：pytest tests/test_meal_time_critic.py -v
  - _需求: R1.1, R1.2, R1.4_

- [x] 2. 块A-2：blueprint prompt 加饭点时段约束（R1）
  - blueprint_prompt.py BLUEPRINT_SYSTEM_PROMPT 加「用餐时段规则」段：正餐落午餐/晚餐窗口；下午茶/夜宵按类型落对应时段；不强制餐厅排最后（只约束时段不离谱）
  - 现有 test_blueprint_prompt.py 不回归
  - 验证：pytest tests/test_blueprint_prompt.py -v
  - _需求: R1.1, R1.3_

- [x] 3. 块B-1：intent parser prompt 强化关键约束保留（R3）
  - intent_parser_prompt.py 加规则：含明确餐饮/活动关键词（撸串/烧烤/夜宵/K歌/喝酒）时必须在 experience_tags/dietary_constraints 保留词典内最接近词；独处场景 experience_tags 不得含「安静聊天」
  - 先写 test_intent_keyword_retention.py（真 LLM 或 mock）：「撸串喝酒」→ tags 含烧烤相关；「独处」→ 不含「安静聊天」
  - 验证：pytest tests/test_intent_keyword_retention.py -v
  - _需求: R3.1, R3.2, R3.3_

- [x] 4. 块C-1：前端 refinement 事件依赖排查（C1 前置 gate）
  - grep 前端 RefinementDialog / ComparisonView / store event-handlers 是否依赖 refinement_start/refinement_done 事件
  - 输出排查结论：哪些组件依赖、依赖什么字段
  - 决定 Task 5 的改法（直接改 URL / 还要补事件 / 接受对比视图降级）
  - 验证：grep 结果 + 结论记录
  - 排查结论：① V3 /chat/turn 反馈链路已 emit REFINEMENT_START（emit_router）+ REFINEMENT_DONE（emit_refiner），ComparisonView（依赖 previousItinerary+lastRefinement+itinerary）改 URL 后仍能渲染；② RefinementSummaryBanner 依赖 lastRefinement.changedFields.length>0，而 V3 changed_fields=[]，banner 不显示——可接受降级（对比视图为主，feedback-routing-fix spec 已验 V3 反馈质量）；③ 结论：直接改 URL+body，无需补后端事件
  - _需求: R4.4_

- [x] 5. 块C-2：前端「说说哪不对」改打 /chat/turn（R4）
  - 据 Task 4 结论：store.ts refine() 的 URL `/chat/refine` → `/chat/turn`，body 改 {message: 反馈文本, session_id, user_id}
  - 若 Task 4 发现 ComparisonView 依赖 refinement 事件 → 按选定方案处理
  - 后端 refine_real.py 不动；/chat/refine 端点保留
  - 验证：pnpm tsc + 前端 verify:all
  - _需求: R4.1, R4.2, R4.3, R4.4_

- [x] 6. 块D-1：死配置标注 + weights_llm 注释修正（R5）
  - .env / .env.example：PLANNER_LLM_STRATEGY / PLANNER_USE_REAL / USE_REACT_AGENT 注释加「⚠ 仅 V1 旧路径(USE_LANGGRAPH=0)生效，V3 主路径不读」
  - weights_llm.py 顶部 FROZEN 注释改为准确描述（get_planning_weights 被 V3 planner + V1 hybrid 调用）
  - 验证：grep 确认标注到位
  - _需求: R5.2, R5.3_

- [x] 7. 块D-2：AGENTS.md §3.3.1 加三路线架构表（R5）
  - 补充「三路线 + 端点→路线映射 + env→生效路径 + 共享底层」权威表（design Architecture 段精炼版）
  - V2 ReAct 主体标注 deprecated（端点统一后评估，不删保留 fallback）
  - 验证：review AGENTS.md
  - _需求: R5.1, R5.4, R5.5, R5.6_

- [x] 8. 块B-2：blueprint prompt 强化「不凭空加活动」+ 餐厅类型匹配（R2）
  - blueprint_prompt.py 加规则：单一诉求（experience_tags 稀疏 + 无明确主活动诉求）→ nodes ≤ 用户表达活动数；选 target_id 优先匹配用户指定 cuisine
  - 确认 mock 有烧烤候选（R046 等）；若 search 不返回 → 查 search_restaurants tag 匹配
  - 验证：真 LLM 抽测 S2「撸串」→ 烧烤类 + 无凭空 CS
  - _需求: R2.1, R2.2, R2.3_

- [x] 9. 真 LLM 8 场景全量回归（R6）
  - 复用 8 场景评测脚本（临时），真 LLM 跑全 8 场景 + S2/S4 重点验证 + 端点统一后反馈验证
  - 断言：S1/S5/S6/S7 保持"优"不回归；S2/S4 提升到"良"以上；R4 反馈走 V3 质量 ≥ V1
  - backend pytest 全过；前端 verify:all 4/4
  - 验证：评测脚本输出 + pytest + verify:all
  - _需求: R6.1, R6.2, R6.3, R6.4_

- [ ] 10. 收尾：problem.md + commit + CodeSee sync
  - problem.md 追加本 spec 会话日志
  - git commit（中文 message，精确 stage）
  - CodeSee features.json sync（如适用）+ 校验
  - _需求: 全部_

## Notes

- 全程不动 graph/build.py 拓扑；不删 V1/V2 代码（仅标注 deprecated）
- 块 B 是 prompt 调优（概率性改善），验证靠真 LLM 实测对比，非确定性断言
- C2 改前端一行 URL，后端 refine_real 完全不碰
- MEAL_TIME critic 用 WARNING 级（不阻断 demo）；若实测 LLM 不自纠再考虑升 CRITICAL
- 真 LLM 测试慢（每场景 20-40s），Task 8/9 耗时较长属正常
