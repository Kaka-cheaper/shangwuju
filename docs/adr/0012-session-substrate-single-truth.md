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
7. **记忆双轨的真相(2026-07-02 深挖补充,推翻"二选一"直觉)**:两套记忆**不是同一件事的两份实现**——`memory_writer.persist_memory`(graph confirm 触发)写 `user_profile.json` 的 **recent_trips**;`data/memory_store`(仅 stub confirm 经 `_accumulate_memory_after_confirm` 触发)写 per-user 的**偏好标签/距离累积**(UserMemory)。而 UserMemory 的读者全在**主路径**上:`persona_qa`(画像问答)、`intent_parser_prompt`(persona prior)、`search_adapter`、`/preferences` API。**由此暴露一个今天就活着的 bug:主 App 确认走 graph confirm,从不累积偏好标签——主路径自己的画像问答/意图先验读的库,只有协作房间的确认在喂。**个性化闭环是断的、还接反了。

## 决策

1. **会话跨轮唯一真相源 = LangGraph 图状态(checkpointer)**。一切跨轮会话事实——当前方案、确认态、未来的轮次日志/方案版本志/澄清态(ADR-0011)——以图状态为准。SESSION_STORE **正名为「确认与建房的读侧投影端口」**:两种会话底座(图会话/房间会话)都往这里投影当前方案,确认流只认这个端口取数——这正是一条确认流能同时服务两种底座的结构前提(见决策 5);它不承载图状态没有的真相,真相源写侧仍在图状态。**刻意不让确认流读 checkpointer**:房间会话没有图存档,读侧绑死 checkpointer 会把房间永远锁死在岔路上(2026-07-02 复审修订,推翻"读写都走真相源更纯粹"的初版直觉)。
2. **确认不进图,完成后回写;"不进图"做成结构事实**:保持 HTTP 旁路(真 interrupt/Command 是大手术,SSE 适配层已承担三按钮语义,对 demo 无新增可见价值——拒);confirm 成功后用 checkpointer 的 `aupdate_state(thread_id, {"itinerary": 含 orders 的终版, "user_decision": "confirm"})` **回写一笔**(仅图会话;房间会话无 checkpoint,跳过)。三条纪律:
   - **结构诚实**:`execute_finalize` 从图里**退注册**(它是无入边的永达不到节点),`sse_adapter` 只为它准备的 `emit_execute_finalize` 死分支同删;函数本体保留供确认流直调。只改注释不够。
   - **失败策略**:确认成功**不依赖**回写——回写失败(如 redis 抖动)记日志降级,不回滚已完成的下单;投影端口里仍有终版方案兜底。
   - **防"写而无读"**(refine_feedback 同款病):回写字段的第一个真实消费者是 E-2 打包器(过渡供体;长期真相在 E-2 方案版本志——"v3 已确认下单"天然是历史记录)。E-0 验收必须带最小真实读者:图级测试断言确认后下一轮 turn 的图状态含 orders 与 `user_decision="confirm"`。
3. **ConversationState/repo 全删(葬礼)**:两处 confirm 流的 `record_confirm_result` 喂血删除;`collab.py` 「路径 1」删除(实证全靠路径 2 活着);`agent/runtime/conversation.py` 模块退役删除(`verify_repository.py` 同退)。连带清掉消息词汇债——pydantic_ai `ModelMessage` 退场,E-2 轮次日志唯一词汇 = langchain `BaseMessage`(AgentState.messages 既有声明)。
4. **重置纪律收口 = 「字段生命周期表」**,不止是"一个函数":今天方案能跨轮存活,靠的是 `make_initial_state` "没写这个字段=保留旧值"的**隐式机制**(persistence-by-omission)。收口做法:`state.py` 里显式声明三档生命周期——**轮级**(每 turn 清零:user_input/路由结果/trace 四件套…)/**事件级**(新需求或反馈时重置:itinerary/blueprint/critic 状态/advisories/user_decision…)/**会话级**(跨轮持久:messages、未来的版本志/pending_clarification、user_id…)——初始化与重置函数都对表走,refiner 与 intent 路径共用同一张表。E-2 的新字段一律先登记生命周期再上车。这是 E-1/E-2 动路由的前置硬门(背景 5 的定时炸弹)。
5. **确认流合一(2026-07-02 复审升级,原"保留 stub 给房间"作废)**:核实 `_graph_confirm` 不跑图、不需要 checkpoint,只读投影端口+直调下单函数;房间确认前本就自己把方案写进投影端口再让 stub 读回(room.py:373-380)——**分叉没有承重理由**。定案:协作房间切到同一条确认流,`_stub_confirm` 整体删除,`USE_LANGGRAPH` 连带自然死亡(它唯一残余语义就是切 confirm 实现)。两条硬门:
   - **统一后的确认流必须同时执行两种记忆副作用**——`memory_writer`(recent_trips→user_profile.json)**和** `memory_store` 标签累积(UserMemory,背景 7)。两库存不同数据、各有活读者,**不是二选一**;这一步顺带修复背景 7 的真 bug(主路径确认从不累积偏好标签)。两库长期是否合并为单一画像存储,另立议题。
   - **特征化测试先行**:迁移房间确认前,先对房间 WS 确认的事件序列写特征化测试钉住现状,再切流,断言不变。
6. **死字段处置**:删 `intent_overrides`/`refine_feedback`;`scenario_id` 保留、声明为 E-2 RoutingContext 打包器的画像素材(接线归 E-2);`messages` 保留——E-2 第一块砖接水(见 ADR-0011 修订「前置核实」节)。

## 边界(不在本 ADR)

协作房间架构(孤岛上下文、每次重规划一次性 session_id)维持现状,E-2 明示「房间轮次不进会话日志」——**但立路标**:房间最终应迁到图会话底座(消灭一次性 session_id hack 与第四份上下文),触发条件=协作升级为一等功能时;redis 三件套 TTL 不齐随 E-2「持久化等级声明」落;前端尸体脚本(`pressure-test-scenarios.mjs`/`verify-refine.mjs` 打已删除端点)顺手清不单列;两个记忆**库**(user_profile.json vs UserMemory)是否合并为单一画像存储另议(两种**副作用**的合流已进决策 5,不再是 backlog)。

## 备选与拒因

- **confirm 进图(真 interrupt)**——拒:工程大、回报小;回写一笔即可让真相源成立。
- **确认流读侧切到 checkpointer("读写都走真相源")**——拒(2026-07-02 复审自我推翻):房间会话没有图存档,读侧绑死 checkpointer 就把确认流永久劈成两条;投影端口是两底座共用一条流的结构前提。
- **保留 `_stub_confirm` 给房间(本 ADR 初版)**——拒:核实后分叉无承重理由(graph 流不需要 checkpoint,读的是同一个投影端口),留着=确认永远两条路 + 记忆闭环永远断在主路径。
- **两套记忆二选一("留 memory_writer 删 memory_store")**——拒:核实后两库存**不同数据**(recent_trips vs 偏好标签),UserMemory 的读者全在主路径(persona_qa/意图先验/preferences),删哪个都断闭环;正解是统一确认流同时喂两库。
- **ConversationState 降级留作归档**——拒:无读者的归档 = 僵尸,词汇债永久化,「一处打包」的唯一性承诺开局即破。
- **不收口重置、出问题逐字段修**——拒:E-2 删归并后「满足-首轮」变可达,散装重置是已知引爆点,被动修等于把架构债转成线上 bug。

## 实施(E-0,三片,每片独立可交付、可回滚;演示风险从低到高排序)

- **E-0-a 葬礼 + 回写 + 结构诚实**:删 ConversationState 全链路;confirm 后 `aupdate_state` 回写(带失败降级);`execute_finalize` 图内退注册 + 死 emit 分支删除;虚构注释改真话。小尾巴同片清:`graph/__init__.py:37`「旧 ReAct 保留为 fallback」谎话、`.env` 两处 USE_LANGGRAPH 死配置注释、`frontend/lib/store.ts:425` 引用 ConversationStore 的注释。
- **E-0-b 重置收口 + 死字段**:字段生命周期表(决策 4)落进 `state.py`;删 `intent_overrides`/`refine_feedback`;`scenario_id` 用途声明。
- **E-0-c 确认流合一**(决策 5;**唯一动演示关键路径的片,独立 commit,可单独回滚**):房间确认特征化测试 → 切统一流 → `_stub_confirm` 删除 → 统一流同时执行两种记忆副作用 → `USE_LANGGRAPH` 退役。**若路演在即,本片可延后——E-1/E-2 只依赖 a/b,不依赖 c**(打包器要的回写在 a;c 修的是房间旁路与记忆闭环)。
- 验收:全量测试零回归;实跑探针两条——①确认下单后再发一轮 turn,图状态可见含 orders 的终版方案与 `user_decision="confirm"`;②主 App 确认后 UserMemory 标签有累积、画像问答能读到(E-0-c 后)。

## 落地状态

⏳ **待实现**(决策 2026-07-02;先收官 ADR-0010 D-8;证据锚点待回填)
