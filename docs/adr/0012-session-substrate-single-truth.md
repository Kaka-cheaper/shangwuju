# ADR-0012 · 会话底座收口:图状态单一真相源 + 旧藤葬礼

- **状态**:Accepted(2026-07-02 · 只读架构审查 + 主代理抽查复核 + 用户拍板)
- **范围**:会话状态存储(LangGraph checkpointer / `api/_session_store.SESSION_STORE` / `agent/runtime/conversation.ConversationState` / 协作房间上下文)、确认下单链路、AgentState 字段与重置纪律、`USE_LANGGRAPH` 开关。是 ADR-0011(E 系列)的底座前置,实施代号 **E-0**(排 ADR-0010 D-8 之后、E-1 之前)。

## 背景(2026-07-02 只读架构审查钉死,承重结论经主代理逐条对码复核)

1. **会话数据散在 4 个后端容器 + 1 个前端容器**:checkpointer(活,主路径跨轮真相,`graph/build.py:268-273`)/SESSION_STORE 快照(活,confirm 读方案、collab 建房)/ConversationState+repo(**半死**:`messages` 字段 write-only——仅两处 confirm 流后台追加、全仓无读者;`intent_snapshot` 全仓无生产写入,`collab.py:70`「路径 1」永远读到 None)/`Room.llm_context_messages`(孤岛,手拼第三套消息格式)。**全系统唯一完整轮次日志在前端 Zustand store**(`store.ts:110-163`),后端一句不存。
2. **确认结果不回写图状态**:`/chat/confirm` 是 HTTP 旁路——`graph_confirm.py:97` 把 `execute_finalize_node` 当普通函数直调,orders/share_message 只写 SESSION_STORE(:158);**checkpointer 里的 itinerary 停在确认前版本**,下一轮路由从图状态读不到「已下单」。
3. **图内 HITL 是虚构文档**:`build.py:27` docstring 画「confirm → execute_finalize → END」,实际 `execute_finalize` 注册为节点但**无任何入边**(仅 :265 出边);`state.py:135`「HITL(interrupt 后等三按钮)」同谎,全仓无 `interrupt()` 调用。
4. **`USE_LANGGRAPH` 语义漂移**:turn 不看它(`chat.py:88` 无条件走图),它只切 confirm 实现(:54-55)+ lifespan 是否预热 redis checkpointer(`main.py:63`);设 0 出现「turn 走图、confirm 走 stub」杂交态。
5. **每轮重置劈三处、intent 路径不重置**:`make_initial_state` 与 `refiner_node` 各管一摊,「满足-首轮」路径没有重置。今天没爆是因为 `route_turn.py:300-302` 把会话中期新需求归并成反馈,该路径不可达;**E-2 删归并当天**,checkpointer 里陈旧的 `itinerary/blueprint/critic_feedback_text`(`planner.py:55` 直读)会漏进全新规划轮。
6. **死字段**:`intent_overrides`(全死,仅声明)、`refine_feedback`(write-only)、`messages`(声明 + `add_messages` reducer + checkpointer 序列化全部就位但零写入方——铺管没通水)、`scenario_id`(传入到 state 即断,图内无人读)。

## 决策

1. **会话跨轮唯一真相源 = LangGraph 图状态(checkpointer)**。一切跨轮会话事实——当前方案、确认态、未来的轮次日志/方案版本志/澄清态(ADR-0011)——以图状态为准。SESSION_STORE 降级为「读侧快照」(confirm 流与协作房间的取数口),不再承载图状态没有的真相。
2. **确认不进图,完成后回写**:保持 HTTP 旁路(真 interrupt/Command 是大手术,SSE 适配层已承担三按钮语义,对 demo 无新增可见价值——拒);confirm 成功后用 checkpointer 的 `aupdate_state(thread_id, {"itinerary": 含 orders 的终版, "user_decision": "confirm"})` **回写一笔**,「待确认态」从图状态可信读取(E-2 上下文打包器的素材前提)。`build.py`/`state.py` 的虚构 HITL 注释改写为真话。
3. **ConversationState/repo 全删(葬礼)**:两处 confirm 流的 `record_confirm_result` 喂血删除;`collab.py` 「路径 1」删除(实证全靠路径 2 活着);`agent/runtime/conversation.py` 模块退役删除(`verify_repository.py` 同退)。连带清掉消息词汇债——pydantic_ai `ModelMessage` 退场,E-2 轮次日志唯一词汇 = langchain `BaseMessage`(AgentState.messages 既有声明)。
4. **重置纪律收口**:「新一轮开始时哪些字段跨轮保留、哪些必须清」收敛为**单一函数**,refiner 路径与 intent(新需求)路径共用。这是 E-1/E-2 动路由的前置硬门(背景 5 的定时炸弹)。
5. **`USE_LANGGRAPH` 退役**:`/chat/confirm` 恒走 `_graph_confirm`;`_stub_confirm` 保留(协作房间恒走它,`room.py:382`),两者已共用 `replay_confirm_actions` 执行核。两套记忆副作用(stub 走 memory_store、graph 走 memory_writer)并存问题记 backlog,不在本 ADR 扩。
6. **死字段处置**:删 `intent_overrides`/`refine_feedback`;`scenario_id` 保留、声明为 E-2 RoutingContext 打包器的画像素材(接线归 E-2);`messages` 保留——E-2 第一块砖接水(见 ADR-0011 修订「前置核实」节)。

## 边界(不在本 ADR)

协作房间架构(孤岛上下文、每次重规划一次性 session_id)维持现状,E-2 明示「房间轮次不进会话日志」;redis 三件套 TTL 不齐随 E-2「持久化等级声明」落;前端尸体脚本(`pressure-test-scenarios.mjs`/`verify-refine.mjs` 打已删除端点)顺手清不单列;两套记忆副作用合流另议。

## 备选与拒因

- **confirm 进图(真 interrupt)**——拒:工程大、回报小;回写一笔即可让真相源成立。
- **ConversationState 降级留作归档**——拒:无读者的归档 = 僵尸,词汇债永久化,「一处打包」的唯一性承诺开局即破。
- **不收口重置、出问题逐字段修**——拒:E-2 删归并后「满足-首轮」变可达,散装重置是已知引爆点,被动修等于把架构债转成线上 bug。

## 实施(E-0,两个 commit)

- **E-0-a 葬礼 + 回写**:删 ConversationState 全链路;confirm 后 `aupdate_state` 回写;虚构注释改真话;`USE_LANGGRAPH` 退役。
- **E-0-b 重置收口 + 死字段**:单一 reset 函数(声明式:字段 → 跨轮保留/每轮清);删两个死字段;`scenario_id` 用途声明。
- 验收:全量测试零回归;实跑探针——确认下单后再发一轮 turn,路由能从图状态看到含 orders 的终版方案与 `user_decision="confirm"`。

## 落地状态

⏳ **待实现**(决策 2026-07-02;先收官 ADR-0010 D-8;证据锚点待回填)
