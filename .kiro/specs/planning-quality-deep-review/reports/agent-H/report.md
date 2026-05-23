# Agent H 审查报告 —— 输出与图编排层（#21 Narrator / #22 ExecuteFinalize / #25 LangGraph 主路径）

> 触发故事：用户实测「家庭主线 5 岁娃博物馆 2.5h」+ narrator 文案"陪孩子玩两个半小时"。
>
> 本层核心追问：narrator 拿到 itinerary 后是不是该"质疑方案"，还是只复述？LangGraph state 流转中有没有数据漂移？SSE 推送给前端的 payload 是否完整反映了规划质量？DecisionTrace 暴露的"为什么这么排"足够回答用户疑问吗？

---

## 1. 现状摘要（每个子环节做了什么）

### 子环节 #21 Narrator（`agent/narrator.py` + `agent/prompts/narrator_prompt.py`）

- 入口：`generate_narration(intent, itinerary, stage, use_llm)`，被 `graph/nodes/narrate.py:narrate_node` 调用。
- LLM 模式：温度 0.7、单轮 chat、超长截断到 280 字、剥围栏与中英文引号。失败回落到模板兜底 `_template_narration`。
- 模板：用 `intent.social_context` 选开场（家庭/独处/商务/老人/朋友等 9 类），用 `_node_to_phrase` 把每个 mid node 拼一句话（home 节点跳过）。
- Prompt（`narrator_prompt.py`）显式写了「直接输出文案，不要 JSON / markdown」「不写 POI / 候选 / score」「不要表情、不要省略号」「不要超过 200 字」「不要分点列表」。
- **关键观察**：prompt 里没有任何「质疑方案」指令。它的设计目标是"暖语气 + 信息密度 + 邀请反馈"，没有「检查活动时长是否合理 / 检查与同行人画像是否匹配」的输出口子；用户输入的 5 岁孩 + 博物馆 2.5h，narrator 收到的是结构化 itinerary（duration_min=150），它会忠实复述成"陪孩子玩两个半小时"。

### 子环节 #22 ExecuteFinalize（`graph/nodes/execute_finalize.py` + `agent/executor.py`）

- LangGraph 节点 `execute_finalize_node`：仅在 confirm 后触发；找出 `nodes` 中第一个 `target_kind=="restaurant"` 的节点，调 `reserve_restaurant`，然后调 `generate_share_message`。失败任何一步都吞异常（`except Exception: pass`），不影响流程。
- `agent/executor.py:execute_plan` 是同份逻辑的 v1 入口（被 main.py 旧路径用），多了 `buy_ticket_for_main_poi` 开关、把 `failed_tools` 显式回传，且会区分 `RESTAURANT_FULL` 是否阻塞。两套实现已经轻微漂移（`executor.py` 走 tracer.emit "tool_call_start/end"；`execute_finalize_node` 不走 tracer），是冻结纪律 §3.3.1 的典型并存路径。
- 都按 edge_v1 字段路径取 `target_kind` / `target_id` / `start_time`。
- **关键观察**：`execute_finalize_node` 用 `next(... target_kind=="restaurant")` 只取第一个用餐节点，蓝图里同时含「正餐 + 夜宵」时（kind 不同但 target_kind 都是 restaurant）只下单一份；与 critic 的扫描逻辑 `[n for n in nodes if target_kind=="restaurant"]`（全量）不对齐——critic 会要求全量约束满足，executor 只为第一家下单。

### 子环节 #25 LangGraph 主路径（`graph/state.py` + `build.py` + `sse_adapter.py` + 11 个 nodes）

- **AgentState**（`state.py`）：`TypedDict, total=False`，34 个字段。`messages` 用 `Annotated[..., add_messages]` 走合并 reducer；其它字段全是默认覆盖语义。`pois_relaxed_tags` / `restaurants_relaxed_tags` 拆 key（pitfalls 已记录的并行 worker 冲突修复）。
- **Build 拓扑**：START → router → (chitchat | intent | refiner) → 3 worker 并行 → execute_collect → planner → assemble → critic → (narrate | replan_router) → (planner 回潮 | ils_replan | narrate)。`_route_after_ils` 硬接 narrate 防死循环（pitfalls P1 2026-05-23）。`InMemorySaver` checkpointer 按 thread_id=session_id 持久化 messages。
- **节点行为**：
  - `router_node`：3 层防御（启发式强信号 → LLM 分类 → 短输入兜底），把 `route_kind` 写入 state。
  - `intent_node`：调 `intent_parser.parse_intent`，写 `intent`。
  - `refiner_node`：合并反馈后**主动重置** plan/critic 状态（`blueprint=None / itinerary=None / violations=[] / has_critical=False / retry_count=0 / plan_attempt=0 / pois=[] / restaurants=[] / routes=[]`），但 **没有重置** `critic_attempts / fallback_chain / alternatives / decision_trace`。
  - `execute.search_*_worker`：3 个并行 worker；execute_collect 是 join point 不写 state。
  - `planner_node`：出 weights + blueprint，写 `alternatives`（top-2 POI + top-2 餐厅，按 rating 排序）。
  - `assemble_node`：拼装 Itinerary、注入 DecisionTrace；`final_strategy` 按 fallback_chain 最后一跳推导（pitfalls P2 2026-05-23 修复）。
  - `critic_node`：跑 `validate_itinerary`，累积 `critic_attempts`（同 attempt 内重复 code 用 Counter 合计，防 React 同 key）。
  - `replan_router_node`：1-2 次 LLM backprompt → 3 次 ILS → 4 次以上 give_up；`_MAX_TOTAL_RETRIES=4` 硬上限；累积 fallback_chain。
  - `narrate_node`：调 narrator + 把 `decision_trace.final_strategy` 改写到定稿状态、把最后一条 critic_attempt 标 resolved。
  - `execute_finalize_node`：reserve + share message，**不调 narrator** 推 confirm 阶段文案。
- **SSE 适配**：`sse_adapter.run_graph_stream` 监听 `astream(stream_mode="updates")`；按节点名映射到 11 类 SseEventType。`assemble` 节点已**砍掉** ITINERARY_READY 推送（pitfalls P2 2026-05-23），只在 narrate / execute_finalize 推一次定稿。`DONE` 事件 payload 是空 dict。

---

## 2. 业务合理性 gap 清单（按 P0/P1/P2 + 配反例）

### P0（demo 立刻翻车）

#### P0-H1：narrator 完全不质疑方案，5 岁娃博物馆 2.5h 被当作合规事实复述
- **现象**：用户输入"家庭，5 岁娃，下午半天"，蓝图把博物馆 stage `duration_min=150`，narrator 文案输出"陪孩子玩两个半小时"。用户反馈"5 岁娃逛博物馆怎么也得不了 2.5h"。
- **根因**（多层并存）：
  1. `narrator_prompt.NARRATOR_SYSTEM_PROMPT` 的角色定义是「导游开场白」「让用户听一遍就明白安排了什么」「感觉这是个用心的安排」——目标函数是**讨好**而非**审查**。
  2. `build_narrator_user_message` 喂给 LLM 的 `itinerary_brief` 只含 nodes/orders 字段，**不喂 `decision_trace.critic_attempts` / `violations` / `weights_explanation`**。哪怕 critic 跑过 4 次 backprompt，narrator 也看不到。
  3. `narrate_node` 不读 `state.has_critical` / `state.violations`；只读 `intent` 和 `itinerary`。
  4. critic 自身的违规码列表里**没有「年龄-时长适配」**这一条（参见 `critics_v2.ViolationCode`：INVARIANT_BROKEN / NODES_INCOMPLETE / DURATION_OUT_OF_RANGE / TIMELINE_INCONSISTENT / HOP_INFEASIBLE / DISTANCE_EXCEEDED / RESTAURANT_FULL_UNRESOLVED / DIETARY_VIOLATION / SOCIAL_CONTEXT_MISMATCH），所以 violations=[] → has_critical=False → narrate 直接放行。
- **反例**：
  - 输入：`{social_context: "家庭日常", companions: [{role: "孩子", age: 5, count: 1}, {role: "妻子", count: 1}], duration_hours: [4, 6]}`
  - 蓝图产出：`nodes=[home, 博物馆 (duration_min=150), 餐厅 (duration_min=90), home]`
  - 期望：narrator 输出"5 岁娃博物馆建议 60-90 分钟，2 小时已经超过专注力上限了，要不要拆成博物馆 1h + 旁边公园 1h？"
  - 实际：narrator 输出"陪孩子玩两个半小时……哪里不合适跟我说一声。"
- **修复方向**：见 §4 方案 A（在 narrator prompt 注入 critic_attempts 与年龄词典） + 方案 D（新增 meta-critic 节点）。

#### P0-H2：DONE 事件 payload 是空 dict，规划质量信号在流末完全丢失
- **现象**：`sse_adapter.py` 流末仅 `yield _ev(seq, SseEventType.DONE, {})`。前端 EventSource 收到 DONE 后只能从历史事件里拼凑 final_strategy / has_critical / fallback_chain。
- **根因**：sse_adapter 的设计假设是「事件流是源真值，前端按发生顺序消费」。但实际场景下评委可能滚动到流末才看，需要 DONE 是 summary。
- **反例**：评委看到 18 次 critic 迭代刷屏后流停了，看不到「最终走的是 llm_backprompt 还是 give_up」「总耗时 X 秒」「ITINERARY 是否已就绪」。需要展开 DecisionTraceCard 才能看到 → 多一步操作 → 评分项 2「Tool 编排合理性可见性」掉分。
- **修复方向**：DONE payload 加 `{final_strategy, plan_attempts, critic_attempt_count, fallback_hops_count, total_ms, has_itinerary}` 6 个字段。

### P1（用户不会立刻发现，但会侵蚀信任）

#### P1-H3：refiner 重置 plan/critic 状态，但 trace 累积字段没重置 → 用户反馈后 AI 思考卡显示前一轮的 fallback
- **现象**：用户先发"5 岁娃博物馆"，触发 4 次 fallback 走到 ils；然后用户反馈"再近一点"，refiner_node 重置 `retry_count / plan_attempt / blueprint / itinerary / pois / restaurants` 等 11 个字段，但漏掉 `critic_attempts / fallback_chain / alternatives`。
- **根因**：`refiner_node` 的 return dict（`graph/nodes/refiner.py:43-58`）只列了"会让流程从 execute 重跑的字段"，没把 trace 字段视为同生命周期。trace 字段是 LangGraph TypedDict 的覆盖语义，不写就保留旧值。
- **反例**：用户反馈后看到的 DecisionTraceCard 上仍显示「3 跳 fallback：llm_first → llm_backprompt → ils → rule」，但实际新一轮一次过（critic 通过）—— trace 与定稿矛盾，"AI 思考"卡片直接砸招牌。
- **修复方向**：refiner_node return 字典补 `critic_attempts=[] / fallback_chain=[] / alternatives=[]`。

#### P1-H4：execute_finalize 找用餐节点的逻辑只取第一个 `target_kind=="restaurant"`，与 critic 全量扫描不对齐
- **现象**：`execute_finalize_node:42-46` 用 `next(... target_kind=="restaurant", None)`，只下单第一个用餐节点。
- **根因**：edge_v1 已支持「主活动 + 用餐 + 夜宵」3 段（kind 不同但 target_kind 同）；critic 与 narrator 是全量遍历，executor 是 first-match——三方读法不一致。
- **反例**：蓝图含 `[餐厅 17:30 正餐, 餐厅 21:00 夜宵]` → critic 都验通过 → narrator 文案两个都讲 → 但只 reserve 了 17:30。用户预约成功界面只看到一份订单，与 narrator 矛盾。
- **修复方向**：用 `[n for n in nodes if target_kind=="restaurant"]` 全量遍历，每个都 reserve；或在 schema 上限制每行程最多一个 restaurant 节点（向 critic 加约束）。

#### P1-H5：AGENT_NARRATION stage 永远是 "stream"，confirm 阶段没有暖文案
- **现象**：`sse_adapter.py:386` 写死 `{"text": text, "stage": "stream"}`；execute_finalize_node 不调 narrator → confirm 后没有"都搞定了，可以放心了"的安抚式文案推送。
- **根因**：narrator 函数本身支持 `stage="confirm"`（`_template_narration` 里有专门尾句"都给你搞定了，可以放心出门了"），但 execute_finalize_node 没调；sse_adapter 也没区分 narrate vs execute_finalize 节点的 stage 标。
- **反例**：用户点"确认并预约"，前端只看到 ITINERARY_READY 含 share_message，没有专门的 confirm 文案气泡 → 整个交互在 confirm 后断档。
- **修复方向**：execute_finalize_node 调 `generate_narration(stage="confirm")`；sse_adapter 按节点名区分 stage 标。

#### P1-H6：state.itinerary 在 narrate_node 被原地 mutate，违反 LangGraph 不可变 diff 范式
- **现象**：`narrate_node:38-50` 直接改 `itinerary.decision_trace.final_strategy = ...` 与 `itinerary.decision_trace.critic_attempts[-1].resolved = True`，然后 return `{"narration": text, "itinerary": itinerary}`。
- **根因**：LangGraph 节点应返回**新对象**作为 state diff，让 reducer / checkpointer 看到差异。原地 mutate 在 InMemorySaver 上能跑（同进程内引用），但切到 PostgresSaver / SqliteSaver 时 pickle 一致性会出问题；time-travel debugging 也会显示"前一帧已经是改后值"。
- **反例**：开 LangGraph Studio time-travel 回看 assemble 节点的状态，看到的 itinerary.decision_trace.final_strategy 已经是 narrate 节点改后的值（因为 Python 同一对象引用）。
- **修复方向**：narrate_node 用 `itinerary.model_copy(update={"decision_trace": ...})` 返回新对象。

#### P1-H7：LangGraph state 字段一致性漂移点（5 处）
见 §3 后专表。

### P2（潜伏 bug、长期债）

#### P2-H8：`state.routes` 字段死字段，永远是 []
- **现象**：`state.py:97` 声明 `routes: list[Any]`；`make_initial_state` 与 refiner 都把它置 []；execute.py docstring 写"estimate_routes_worker → state.routes"，但**实际 build.py 没注册这个 worker**——assemble 直接通过 `lookup_hop` 取通勤分钟。
- **根因**：early design 中 plan_attempt 前要预先粗估路线，后来 lookup_hop 改为同步 lazy，没回头删 state.routes 与 docstring。
- **修复方向**：state.routes 删；execute.py docstring 更新；refiner 的 reset 列表删 routes。

#### P2-H9：sse_adapter 的 last_state 累积逻辑不对——存的是 node_diff 不是合并后的 state
- **现象**：`sse_adapter.py:403` 写 `last_state = node_diff`；意图是为"narrate 节点取不到 itinerary 时兜底"用，但 `node_diff` 是当前节点的输出 diff，不是合并后的 state。
- **根因**：LangGraph `astream(stream_mode="updates")` 给的就是 diff；要拿合并 state 得用 `stream_mode="values"`。
- **反例**：narrate 节点 diff 不含 itinerary（虽然 narrate 实际 return 了 itinerary，但其它路径可能不 return），sse_adapter 兜底取 `last_state.get("itinerary")` 拿到上一节点（如 critic）的 diff，里面只有 violations，没 itinerary → ITINERARY_READY 推不出去。
- **修复方向**：维护 `merged_state: AgentState = dict(initial)`，每个 chunk 都合并进来。

#### P2-H10：DecisionTrace 信息不足以回答"为什么这么排"
- **现象**：`schemas/decision_trace.py` 5 个字段：blueprint_rationale / weights_explanation / critic_attempts / alternatives_considered / fallback_chain。**没有「目标→stage 的映射理由」**——为什么选这家博物馆而不是公园、为什么 14:00 出门而不是 13:30、为什么用餐 17:30 而不是 18:30。
- **反例**：用户问"为什么去博物馆 2.5h"，DecisionTrace 答得出"权重舒适 0.35 + LLM 一次过"，但答不出"5 岁娃 + 物理约束 kid-friendly + 博物馆 P002 命中 kid-friendly + duration_min 是 LLM 主观给的"——后半段决策链路缺失。
- **修复方向**：alternatives 之外加 `node_decisions: list[NodeDecision]`，每个节点带 `why_chosen / duration_rationale`。

#### P2-H11：没有专门的 meta-critic 节点（架构 gap）
详见 §3 业界对标 + §4 方案 D。

---

## 3. 业界对标 diff（必查 ≥ 3）

### 对标项目 1：LangGraph 官方 Plan-and-Execute 教程
- **链接**：[langgraph 官方 plan-and-execute](https://blog.langchain.com/planning-agents/) / [Discussion #571](https://github.com/langchain-ai/langgraph/discussions/571) / [Thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph)
- **他们怎么做**：标准 Plan → Execute → Replan 三角，Replan 节点的 prompt **强制带上"已执行的 step + 还剩什么 + 是否要修改 plan"**，replan 之前 LLM 要看 execution trace。返回的 state 是 immutable diff，由 reducer 合并。
- **我们差在哪**：
  1. 我们 narrate 节点 mutate `itinerary.decision_trace`，违反 immutable diff 范式（P1-H6）。
  2. 我们 replan_router 是**算法决策**而非 **LLM 反思**——直接看 `retry_count` 数字判 strategy，不让 LLM 看 critic 反馈说"我觉得这次该 ILS 还是再试一次 backprompt"。LangGraph 官方版让 LLM 在 replan 节点决策，更灵活。
  3. 我们没有 `Annotated[..., reducer]` 在大多数字段上 → 默认覆盖语义；并行 worker 写同 key 会冲突（pitfalls 已记录 relaxed_tags 翻车）。
- **借鉴成本**：低（只动 narrate / replan，~30min）。

### 对标项目 2：Anthropic / 业界 Reflection Loop pattern（Reflexion 论文 / Self-RAG）
- **链接**：[Reflection Loop @ agentic-patterns.com](https://agentic-patterns.com/patterns/reflection/) / [Self-RAG with LangGraph @ IBM](https://www.ibm.com/think/tutorials/build-self-rag-agent-langgraph-granite) / [Anthropic trustworthy agents](https://www.anthropic.com/research/trustworthy-agents)
- **他们怎么做**：在 generator 节点之外**显式建一个 critic / reflector 节点**，用 LLM 评估 draft 的 quality，输出 `{score, weakness, suggested_revision}`；阈值未达就回 generator 修。Self-RAG 进一步把 reflection token 训进模型（CRITIC / RETRIEVE），让 LLM **主动 emit 我不确定的信号**。
- **我们差在哪**：我们的 `critic_node` 是**算法 critic**（验时间 / 通勤 / 不变量），**不是业务/质量 critic**——5 岁娃博物馆 2.5h 在所有 9 类 ViolationCode 里**没有任何一条会触发**（duration 是 [4,6] 内合法、hop 通勤可达、不变量满足）。业界的 reflection loop 在我们的图里完全缺失。narrator 的 prompt 也没有"reflection"取向。
- **借鉴成本**：中（新增 meta_critic_node + LLM prompt + 接入 build.py，~2h）。

### 对标项目 3：OR-Tools CP-SAT solver search log
- **链接**：[CP-SAT Primer · understanding the log](https://d-krupke.github.io/cpsat-primer/understanding_the_log.html) / [OR-Tools 官方 constraint_solver Solver](https://developers.google.com/optimization/reference/constraint_solver/constraint_solver/Solver)
- **他们怎么做**：CP-SAT 求解器在跑的过程中输出**结构化 search log**——每次找到更好解、每次 backtrack、每个 constraint 被 violation 的次数都打 timestamp + 缩进表达 search tree。最终给出 `objective_value / num_branches / num_conflicts / explained_constraints` summary。这是"为什么这个解是最优"的工程化解释。
- **我们差在哪**：DecisionTrace 已经做了一半（critic_attempts / fallback_chain），但缺 search log 的"每次决策的目标值变化"——比如蓝图迭代 4 次，每次 utility_score 是多少、哪个 violation 被消除了、第几次 attempt 终于通过 critic。前端 DecisionTraceCard 看到的"第 2 次 已修正"是布尔，看不到 critic feedback 怎么把方案推动了。
- **借鉴成本**：低（critic_attempts 加 utility_score + delta_summary 字段，~1h）。

### 对标项目 4（补充）：OpenAI Swarm / handoff pattern
- **链接**：[OpenAI cookbook / Routines and Handoffs](https://cookbook.openai.com/examples/orchestrating_agents) / [Swarm vs Supervisor architecture](https://focused.io/lab/multi-agent-orchestration-in-langgraph-supervisor-vs-swarm-tradeoffs-and-architecture)
- **他们怎么做**：Swarm 用 handoff 把 routing 嵌入 LLM 工具调用——每个 agent 通过 return another agent 实现切换。Supervisor 模式则是显式 LLM-as-router。两者共同点是**让 LLM 在路由节点本身决策**，而不是写死 if/else。
- **我们差在哪**：我们的 router 是 LLM 分类（OK），但 replan_router 是写死 if/else（retry_count <= 2 → llm_backprompt; else → ils）。Swarm/Supervisor 风格会让 replan 节点也由 LLM 看 critic 反馈选策略——例如 commute_infeasible 直接跳 ILS、distance_exceeded 走 backprompt。
- **借鉴成本**：低（replan 加 LLM 选策略路径，~1h），但风险：LLM 可能选错策略陷入死循环，需配合 _MAX_TOTAL_RETRIES 硬上限。

---

## 3.5 LangGraph state 字段一致性检查表（强制必填）

```
| 字段                    | 写入节点                               | 读取节点                          | 重置点               | 漂移风险                                          |
|------------------------|---------------------------------------|----------------------------------|---------------------|--------------------------------------------------|
| user_input             | make_initial_state                    | router / intent / refiner        | 无（每轮新输入）     | ✓ 一致                                           |
| route_kind             | router_node                           | route_after_router               | 无                  | ✓ 一致                                           |
| router_decision        | router_node（feedback 路径写 None）   | sse_adapter / chitchat_node      | 无                  | ⚠ feedback 路径下 router_decision=None，chitchat fallback 取 reply_text 兜底 |
| intent                 | intent_node / refiner_node            | search_*_worker / planner / critic / narrator / execute_finalize | refiner 重置为新 intent | ✓ 一致                                           |
| pois / restaurants     | search_*_worker                       | planner / assemble               | refiner 重置为 []   | ✓ 一致                                           |
| pois_relaxed_tags / restaurants_relaxed_tags | 拆分 key（已修） | sse_adapter                      | 无重置              | ⚠ refiner 后没清空，前端可能看到旧 relaxed 信号 |
| user_profile           | get_user_profile_worker               | assemble._resolve_user_profile   | 无重置              | ⚠ refiner 不重置（OK，profile 不会变）            |
| routes                 | （无 worker 写入）                    | （无 reader）                    | refiner 重置为 []   | **❌ 死字段（P2-H8）**                            |
| weights                | planner_node                          | assemble                         | 无                  | ✓ 一致                                           |
| blueprint              | planner_node                          | assemble                         | refiner 重置为 None | ✓ 一致                                           |
| blueprint.nodes        | LLM 出（mid only，不含 home）         | assemble（注入 home 首尾）       | -                   | ⚠ 隐式契约：blueprint.nodes 长度 != itinerary.nodes 长度，没显式断言 |
| itinerary              | assemble / replan(ILS) / narrate / execute_finalize | critic / narrate / sse / execute_finalize | refiner 重置为 None | **❌ narrate 原地 mutate（P1-H6）**               |
| itinerary.decision_trace | assemble 写 / narrate mutate        | sse / 前端 DecisionTraceCard     | -                   | ❌ 同上                                           |
| violations             | critic_node                           | replan_router / sse              | refiner 重置为 []   | ✓ 一致                                           |
| has_critical           | critic_node / replan(ils 重置 False) | route_after_critic / sse         | refiner 重置为 False | ✓ 一致                                           |
| critic_feedback_text   | critic_node / replan 重置 None       | planner（backprompt）            | refiner 重置 None   | ✓ 一致                                           |
| critic_attempts        | critic_node 累积                      | sse / assemble（注入 trace）     | **refiner 不重置（P1-H3）** | ❌ 跨轮泄漏                                      |
| fallback_chain         | replan_router / replan(ils) 累积     | assemble（注入 trace）           | **refiner 不重置（P1-H3）** | ❌ 跨轮泄漏                                      |
| alternatives           | planner_node                          | assemble（注入 trace）           | **refiner 不重置（P1-H3）** | ❌ 跨轮泄漏                                      |
| retry_count            | replan_router 累计                    | replan_router / 兜底硬上限       | refiner 重置为 0    | ✓ 一致                                           |
| plan_attempt           | planner_node 累计                     | sse（CRITIC_FIX_ATTEMPT）        | refiner 重置为 0    | ✓ 一致                                           |
| replan_strategy        | replan_router / replan(ils 写 give_up) | route_after_replan / sse        | 无重置              | ⚠ refiner 后保留旧值，下一轮第一次进 replan 前不会被读，OK |
| narration              | narrate_node                          | sse                              | 无                  | ✓ 一致                                           |
| user_decision          | （前端 confirm 路径）                 | execute_finalize                 | refiner 重置 None   | ✓ 一致                                           |
| orders / share_message | execute_finalize                      | sse / 前端                       | -                   | ✓ 一致                                           |
| chitchat_*             | chitchat_node                         | sse                              | -                   | ✓ 一致                                           |
| messages               | （Annotated reducer 自动 add）        | -                                | -                   | ✓ 一致                                           |
```

**核心结论**：
1. **死字段 1 个**：`routes`。
2. **跨轮泄漏 3 个**：`critic_attempts / fallback_chain / alternatives` —— refiner 重置漏掉。
3. **原地 mutate 1 处**：`narrate_node` 改 `itinerary.decision_trace`。
4. **隐式契约 1 处**：`blueprint.nodes` 长度 ≠ `itinerary.nodes` 长度（前者 mid only / 后者首尾含 home），没显式断言。
5. **router_decision 在 feedback 路径下被置 None**，chitchat_node 有 fallback 但 sse_adapter 直接 `decision.model_dump()` 没判 None（已隐式由 route_after_router 路由到 refiner 而不是 chitchat 化解，但代码可读性差）。

---

## 3.6 是否需要新增 meta-critic 节点 —— 架构判定

**建议结论：必须新增**，理由如下。

```
| 当前 critic 验的 9 类                | meta-critic 该验的            |
|-------------------------------------|------------------------------|
| INVARIANT_BROKEN（结构不变量）        | AGE_DURATION_MISMATCH（年龄-时长）|
| NODES_INCOMPLETE（中间节点 < 1）      | ENERGY_OVERLOAD（疲劳堆叠）       |
| DURATION_OUT_OF_RANGE（总时长 ±30min）| TRAFFIC_REALISM（早晚高峰预测）   |
| TIMELINE_INCONSISTENT（时间错位）      | THEMATIC_MONOTONY（同质活动连排） |
| HOP_INFEASIBLE（通勤不可达）          | MEAL_TIMING_HUMAN（用餐时间反人性）|
| DISTANCE_EXCEEDED（超距离上限）        | BUDGET_REALITY（成本与社交场不符）|
| RESTAURANT_FULL_UNRESOLVED（17:00 满座）| TAG_NEGATION（违反 social_compat 隐含约束）|
| DIETARY_VIOLATION（餐厅 tags）        | -                            |
| SOCIAL_CONTEXT_MISMATCH（场景兼容性） | -                            |
```

当前 9 类全是**结构 / 物理可行性**，没一条是**业务质量**。5 岁娃博物馆 2.5h、独处场景安排 6h 不停转场、家庭场景把唯一用餐排在 14:30、商务场景安排在郊区——全部能通过当前 critic。

**meta-critic 节点设计要点**：
- 输入：完整 `Itinerary` + `intent` + `decision_trace`
- 输出：`list[QualityIssue]`，severity ∈ {info, warn, block}
- 实现：LLM-based critique（独立 prompt），prompt 显式列年龄-时长词典 / 疲劳曲线 / 用餐时间合理区间 / 同质活动检测规则
- 接入位置：`critic_node` 之后；通过则进 narrate；info 级 issue 进 narrator prompt 让 narrator 主动提一句
- 与 narrator 的协作：meta-critic 输出 `narrator_hint: list[str]`（如 "5 岁娃博物馆 2.5h 偏长"），narrator prompt 加段「如有 narrator_hint，必须在文案中提一句质疑性建议」

无 meta-critic → narrator 不可能"质疑方案"——它的输入根本没有"哪条不合理"信号。

---

## 4. 修复方案候选（每条带工时 + 跨环节依赖）

### 方案 A：narrator 接入 critic / quality 信号（治 P0-H1 第一阶段）
- 工时：~45min
- 改动：
  1. `build_narrator_user_message` 加入 `critic_summary: str`（最多 3 条 critical 违规），`fallback_summary`（fallback_chain 简述），`quality_warnings: list[str]`（meta-critic 输出，方案 D 配套）
  2. `NARRATOR_SYSTEM_PROMPT` 加 「如收到 critic_summary 或 quality_warnings，必须在文案中提一句"我注意到 X，要不要换 Y"」
  3. `narrate_node` 把 `state.violations`（哪怕已经 resolved，作历史也要喂）+ `state.fallback_chain` 转字符串注入
- 影响子环节：#21 narrator + #14 critics_v2（输出格式微调）+ 配合方案 D
- 风险：LLM 可能"过度质疑"，反而每次都在文案末加质疑句让用户烦——配合温度 0.7 + few-shot 控制

### 方案 B：DONE event payload 加 6 字段总结（治 P0-H2）
- 工时：~15min
- 改动：`sse_adapter.run_graph_stream` 末尾改 `yield _ev(seq, SseEventType.DONE, {final_strategy, plan_attempts, critic_attempts_count, fallback_hops, has_itinerary, has_critical_warnings})`
- 影响子环节：#25 sse_adapter + 前端解析（前端默认丢弃 unknown payload，OK）
- 风险：低

### 方案 C：refiner_node 重置 trace 累积字段（治 P1-H3）
- 工时：~5min
- 改动：`refiner_node` return dict 加 `critic_attempts=[] / fallback_chain=[] / alternatives=[]`
- 影响子环节：#25 graph/nodes/refiner.py 单点
- 风险：零

### 方案 D：新增 meta_critic_node（治 P0-H1 第二阶段 + P2-H11 架构 gap）
- 工时：~2.5h
- 改动：
  1. 新增 `agent/v2/meta_critic.py`（LLM-based）+ `prompts/meta_critic_prompt.py`（年龄-时长词典 / 疲劳曲线 / 用餐时间合理区间 / 同质活动检测）
  2. 新增 `agent/graph/nodes/meta_critic.py:meta_critic_node` 节点
  3. `build.py` 拓扑改：`critic → meta_critic → narrate`（has_critical 走 replan 不变）
  4. `state.py` 加 `quality_issues: list[QualityIssue]` / `narrator_hints: list[str]`
  5. narrator prompt 接入（配合方案 A）
  6. 前端 DecisionTraceCard 加 quality_issues 段（可选）
- 影响子环节：#13 #14 #15 critic 三套 / #21 narrator / #25 graph/build / 前端 trace 卡
- 风险：新增 LLM 调用增加延迟（~2-3s）；在主路径慢时可能掉用户耐心。建议默认开 + 配 ENV 开关 ENABLE_META_CRITIC

### 方案 E：execute_finalize 全量遍历 restaurant 节点 + 调 confirm narrator（治 P1-H4 + P1-H5）
- 工时：~30min
- 改动：
  1. `execute_finalize_node` 改 `for n in nodes if target_kind=="restaurant": reserve(n)`
  2. execute_finalize 末尾调 `generate_narration(stage="confirm")`，写入 state.narration_confirm
  3. sse_adapter 在 execute_finalize 分支推 AGENT_NARRATION 带 `stage="confirm"`
- 影响子环节：#22 execute_finalize / #25 sse_adapter
- 风险：低

### 方案 F：narrate_node 用 model_copy 替代 mutate（治 P1-H6）
- 工时：~10min
- 改动：narrate_node 用 `itinerary.model_copy(update={"decision_trace": new_trace})` 返回新对象
- 影响子环节：#25 graph/nodes/narrate.py 单点
- 风险：零

### 方案 G：DecisionTrace 加 NodeDecision 字段（治 P2-H10）
- 工时：~1h
- 改动：`schemas/decision_trace.py` 加 `NodeDecision { node_id, why_chosen, duration_rationale, key_constraints_applied }`；planner_node 写入；前端 DecisionTraceCard 加段渲染
- 影响子环节：#11 blueprint_llm（要求 LLM 自报每节点理由）+ #21 narrator（可消费）+ 前端
- 风险：LLM prompt 复杂度上升

### 方案 H：state.routes 删除（治 P2-H8）
- 工时：~5min
- 改动：state.py 删 routes 字段；refiner / make_initial_state 删 routes=[]；execute.py docstring 更新
- 影响子环节：#25 单点
- 风险：零

### 方案优先级建议
- 先做：C → F → H（零风险快速修复，10min 内）
- 然后：A → B → E（用户可见的质量信号，~1.5h）
- 最后：D → G（新架构能力，~3.5h；hackathon 时间盒下评估再做）

---

## 5. 目录归属建议（A1 融合）

```
| 文件                                   | 当前位置              | 建议归属        | 是否合并 / 删 / 冻结        |
|---------------------------------------|----------------------|----------------|----------------------------|
| backend/agent/narrator.py             | agent/                | runtime/       | 保留；不冻结（narrator 是主路径活跃模块） |
| backend/agent/prompts/narrator_prompt.py | agent/prompts/     | runtime/prompts | 保留                       |
| backend/agent/executor.py             | agent/                | legacy/runtime  | **建议冻结 + 注释**：与 graph/nodes/execute_finalize.py 并存；后者是 LangGraph 主路径，前者是旧 ReAct 路径兜底（fallback 链路）。冻结它，新功能只动 execute_finalize_node。 |
| backend/agent/graph/state.py          | agent/graph/          | core/graph     | 保留（核心 schema）         |
| backend/agent/graph/build.py          | agent/graph/          | core/graph     | 保留                       |
| backend/agent/graph/sse_adapter.py    | agent/graph/          | runtime/graph  | 保留                       |
| backend/agent/graph/nodes/*.py        | agent/graph/nodes/    | runtime/graph/nodes | 保留 11 个；新增 meta_critic.py |
```

**核心建议**：
- `agent/executor.py` 与 `graph/nodes/execute_finalize.py` 双实现并存，是 §3.3.1 编排层冻结纪律的典型 case。建议把 `executor.py` 顶部 docstring 加「⚠ 冻结：与 graph/nodes/execute_finalize.py 并存；新功能改后者」标记，避免 Agent 误改。
- 新增 meta-critic 时放 `agent/graph/nodes/meta_critic.py` + `agent/v2/meta_critic.py`（LLM 实现）+ `prompts/meta_critic_prompt.py`，不要建新顶层目录。
- 不要新建 `agent/quality/` 之类垃圾桶子目录。

---

## 6. 跨环节依赖警示（你看到但其他 agent 看不到的）

### 6.1 我修这里会影响

- **方案 A（narrator 接入 critic 信号）→ 影响 Agent E（critic 三套）**：critic_attempts.message / feedback_summary 字段会被 narrator 读取，message 文案的"语气"和"信息密度"会直接影响最终 narration 文案。建议 Agent E 做 critics_v2.py 优化时与本节方案 A 协同——critic message 应从"给 LLM 看的修复种子"演进为"也能给 narrator 转述"。
- **方案 D（meta-critic 节点）→ 影响 Agent E + Agent A（intent 层）**：meta-critic 要查"年龄词典"，需要 intent 提供 age 字段；intent_parser 当前 `Companion.age` 是 `Optional`，meta-critic 必须容错。Agent A 的"意图理解"层如果能提升 age 解析率，meta-critic 触达率才高。
- **方案 G（NodeDecision 字段）→ 影响 Agent D（blueprint_llm）**：blueprint LLM prompt 必须输出每节点 rationale；Agent D 改 prompt 时要预留位置。
- **方案 H（删 state.routes）→ 影响 Agent C（lookup_hop / estimate_route_time）**：估值偏差审查 Agent 如果想引入 routes 缓存，要重新设计；不能直接复用本来死字段。

### 6.2 我依赖另一处先修

- **方案 D（meta-critic）依赖 Agent G（mock POI / Restaurant schema）**：年龄-时长词典需要 POI 自身有 `age_range` / `recommended_duration_min` 字段。如果 Agent G 没在 mock POI 里加这俩字段，meta-critic 只能凭 LLM 常识判定（误差大）。建议 Agent G 优先评估 POI schema 加字段。
- **方案 D 依赖 Agent A（intent layer）的 age 解析率**：当前 intent prompt 已要求 age 必传，但实际 LLM 可能漏。Agent A 的"意图理解上限"审查应专门看 age 字段缺失率。
- **方案 A（narrator 接入 critic 信号）依赖 Agent E 的 violation message 文本质量**：critic message 当前是"给 LLM 看的修复种子"（如 "第 2 段去往第 3 段的通勤实际需要约 18 分钟，但只留了 8 分钟"），直接给 narrator 当输入会出现机器味。Agent E 应规范 message 的双面性——既给 LLM、也能给 narrator 转述。

### 6.3 评分项映射

- 评分项 1「场景理解」(20%)：方案 D + G 直接提分（Agent 能解释"为什么这么排"）
- 评分项 2「Tool 编排合理性」(25%)：方案 B 直接提分（DONE summary）+ 方案 A（narrator 主动质疑显示思考力）
- 评分项 3「Demo 闭环 + 异常韧性」：方案 C + F + H（state 一致性）+ 方案 E（confirm 阶段不断档）
- 评分项 4「Mock 数据像真的」：方案 D 触发 Agent G 给 POI 加 age_range / recommended_duration_min

### 6.4 Hackathon 时间盒优先级建议

```
| 优先级 | 方案    | 工时    | 价值                              |
|-------|---------|--------|----------------------------------|
| P0    | C+F+H   | 20min  | 零风险快速修复，state 一致性提升   |
| P0    | B       | 15min  | DONE summary，评分项 2 直接提分    |
| P1    | E       | 30min  | confirm 阶段闭环                  |
| P1    | A       | 45min  | narrator 接入信号（先用 violations 兜底，不依赖 D） |
| P2    | D       | 2.5h   | meta-critic 架构升级（如时间允许） |
| P3    | G       | 1h     | NodeDecision（演示精修）          |
```

如时间紧，P0+P1 共 ~2h 即可解决 demo 翻车风险（5 岁娃博物馆 2.5h 至少 narrator 会提一句"建议拆短"）；P2 是架构投入，演示中显式提"我们做了 meta-critic"是评分加分项。

---

## 自检确认

- [x] 6 段全填（含 §3.5 字段一致性表 + §3.6 meta-critic 评估，按要求合并入 §3）
- [x] gap ≥ 3 条（P0×2 + P1×4 + P2×4 = 10 条）
- [x] 业界对标 ≥ 3 条带链接（LangGraph Plan-and-Execute / Reflection Loop / OR-Tools CP-SAT / OpenAI Swarm = 4 条）
- [x] LangGraph state 字段一致性检查表（§3.5，27 字段全覆盖）
- [x] 评估"是否需要新增 meta-critic 节点"（§3.6 + 方案 D，给出明确建议：必须新增）
- [x] 中文 + 字数 ~3800 字（区间内）
