# ADR-0011 · 路由层重设计:一脑三壳 —— LLM-first + 义务闭集 + 上下文打包器

- **状态**:Accepted(2026-07-02 · solve-it-right + grill 会话)
- **范围**:路由层(`agent/routing/` + `agent/core/dialogue_acts.py` + `agent/intent/router.py` 的分类/兜底)+ 会话上下文供给 + 澄清链路。承接 ADR-0001~0006(路由收口)、[L0 响应义务契约](../L0-响应义务契约.md)(本 ADR 的行为规范来源)、ADR-0010 决策 11(advisory)。

## 背景(现状诊断,全部实跑/读码钉死,2026-07-02)

1. **`ambiguous` 存在但被谋杀**:`RouteKind` 有 ambiguous、LLM 分类会产出,但 `route_turn.py:301` 把「有方案 + planning/ambiguous」**强行归并成 feedback 去重规划**;无方案时与 chitchat 同路气泡。**没有任何下游会「问」**——实测「我不想玩这个了」(有方案)→ feedback → 硬猜重规划。
2. **降级地板方向反了**:`fallback_decision` 明写「LLM 不可用:直接判 PLANNING」。实测 stub 模式:「你好」「asdfgh」「帮我写作业」无方案时**全部 → planning**,有方案时**全部 → feedback(重规划)**——听不懂就动手,违反 L0 禁令 1。
3. **规则层在追覆盖**:规划 fast-path 四张信号表(时间/动作/同伴/约束几十词)+ 反馈强信号大表(含「一般/普通/优雅/无聊」等纯语义词)——自然语言表面形式无穷,关键词追覆盖必败,误吞面大(route_turn:272 注释自认「读着像新规划的话也当反馈」)。
4. **一轮烧两次 LLM**:有方案时 `classify_input`(Layer 2)+ `classify_dialogue_act`(Layer 3)各一次,两套词汇靠 `_ACT_TO_ROUTE_KIND` 映射表缝合;「确认」被映成 chitchat(词汇债)。
5. 词汇冗余:chitchat/emotional/meta 三个 kind 下游同为气泡,只差语气——路由层背了三胞胎。

## 决策

### 1. 路由标签闭集:6 标签(= L0 契约的路由投影)
`满足-首轮` / `满足-反馈` / `澄清` / `防御` / `陪聊` / `确认`。
- 确认独立(下游=引导 execute,与陪聊完全不同;文本确认**只引导显式按钮,不自动下单**——L0 禁令 1 的正面样板,保持现状语义);
- chitchat/emotional/meta 塌缩为「陪聊」,语气差异交回复生成的 tone,不再是路由分支;
- ambiguous → 「澄清」(从被归并变被响应);
- 「告知」刻意不在闭集(它是满足义务的附属输出,planner 侧 advisory,轴不同——见 L0 契约注)。

### 2. 一脑三壳(级联塌缩;吸收 dialogue_acts,ADR-0002 待 E-2 落地标 Superseded)
```
[壳1·安全规则]  注入/攻击 → 防御(LLM 前,不可谈判;拒绝文案不回显攻击内容)
[壳2·字面短路]  FP≈0 字面匹配:问候 / 字面确认 / 画像问答(可选,纯省调用)
[脑子·LLM 路由] 一次调用 → 6 标签 + 槽位(指代节点/字段、澄清要问什么、反馈调整方向)+ 置信度
                置信度低 → 归并成「澄清」(绝不再归并成 feedback)
[壳3·保守地板]  LLM 失败 → 无方案:陪聊+引导 chips;有方案:澄清式引导。绝不默认规划/重规划
```
- 吸收合一:`classify_input` + `classify_dialogue_act` 并成一次调用(成本降——原来 1-2 次,恒为 1 次);dialogue_acts 的字面规则部分(「就这样吧」→确认)下放壳 2/3 保留;ADR-0002 的「独立可测 seam」不丢——seam 从独立函数变为「义务标签 + 轨道 A 语料断言面」。
- 旧词表去向:注入检测→壳1;数字+单位类强信号(「3公里以内」)+ 字面确认 + 画像问答→壳2;**全部语义词删除**(「太远/太累/一般/优雅」及规划 fast-path 时间/动作/同伴表——职责整体移交脑子);兜底归并(route_turn:301)删除。
- 八个预设场景与路由的结合方式:**场景作为少样本范例进 prompt**(同一句「有点累了」在 S1/S4/S7 的不同判法)+ **作为轨道 A 语料维度**——不是 if social_context 分支。

### 3. 会话上下文打包器(路由节点的一等组件,一处打包多处消费)
- 每轮一次,确定性产出结构化 `RoutingContext`:轮次日志(消毒后)+ 方案版本志(一行志:「v2: 应『太远了』换近的」)+ 当前方案摘要(每节点一行)+ 画像(场景/同行/节奏)+ `pending_clarification` + 待确认态;
- **全量为默认**(会话规模天然小,千级 token;不做摘要/检索机制),**保险丝上限**兜边界(约最近 40 轮/8K token,溢出丢最老闲聊轮,**钉锚永不丢**:首轮原始需求、全部方案版本志、pending_clarification);
- **消毒纪律**:被壳1拦截的轮次打码(不把已挡的攻击原文回灌 prompt),超长粘贴截断标记;
- 主消费者=路由脑子;refiner/narration 消费切片。**禁止各节点自己拼上下文**(防三份拼法三种漂移);
- 纯函数、可单测(不碰 LLM);
- **底座无关(2026-07-03 增补,ADR-0013 联动)**:打包器吃抽象的「会话上下文来源」——主聊天来源=图状态,协作房间来源=按成员归档的房间台账;**一个打包器,多个来源**,绝不因房间另建第二个打包器。同理:统一路由脑子保持纯函数(房间消息处理器是它的又一层薄壳,享受同一套判定与升级);澄清状态在房间语境下按成员分身(pending_clarification 带提问对象维度,E-3 设计时落)。多人协作的路由健全度与单人场景**同权**,靠共用一个脑子实现,不靠复制。

### 4. 澄清状态机
- 显式会话状态 `pending_clarification`(问了什么/给了哪些选项/因哪句而问)——不靠 LLM 从历史「看出来」,显式状态驱动 chips UI 与解释优先级;
- 有 pending 时本轮输入优先按「回答」解释(「第二个」可解析);用户说新话题 → 脑子判「未回答」→ pending 作废按新输入路由(不纠缠);
- **同一话题至多澄清一次**:再不清 → 保守解释行动 + advisory「我按 X 理解了,不对点这里」(防死循环、防审问式体验);
- 呈现零新件:澄清 = 现有「气泡 + cta_chips」,选项即按钮,点 chip = 结构化回答。

### 5. 统一 agent 消息面(与 D-7 的交汇点)
澄清 / advisory / 婉拒 / 陪聊 = 同一个「agent 对用户说话」的出口。**D-7 的 advisory 载体按通用 agent 消息设计**(而非 planner 专用字段),E-3 澄清直接复用——两场工程共管道,不建平行管。

## 前置核实(2026-07-02 已完成——只读架构审查 + 主代理复核;底座烂账另立 ADR-0012)

**① 会话层持久化现状:轮次日志与方案版本志都不存在。** 全系统唯一完整轮次日志在前端 Zustand store;后端每轮覆盖(user_input/intent/itinerary 均 last-value)。已拍板(用户 2026-07-02):**会话日志基础设施从 E-3 提前为 E-2 第一块砖**,选型 = AgentState 既有 `messages` 通道(`add_messages` reducer + checkpointer 序列化均已核实可用,langgraph msgpack 内置放行 langchain 消息类型)+ 方案版本志新增**累积**字段。四条护栏(不写进 spec 就是踩空):
1. 新累积字段**必须带 reducer**(如 `Annotated[list, operator.add]`)——照抄现有 last-value 字段的写法必错,每轮被 `make_initial_state` 的空值清零;条目用纯 dict/str,否则要同步补 `build.py` 的 serde allowlist;
2. confirm 轮次靠 ADR-0012 决策 2 的「确认后回写图状态」补全,否则日志天然缺确认轮;
3. 协作房间轮次**明示不进会话日志**(房间每次重规划用一次性 session_id,维持现状);
4. 消毒在**写入时**做(壳1 verdict 当时已知);持久化等级 = checkpointer 等级(memory 模式重启即失;redis saver 未配 TTL,随本砖补齐)。**另注**:决策 3 的保险丝(40 轮/8K)限的是喂给路由脑子的**上下文包**,不是底层存储——messages 通道与 checkpoint 历史本身无界增长(内存模式每 checkpoint 全留),E-2 spec 须给修剪策略或写明「demo 规模显式接受」,二选一,不许默认;
5. 会话日志按 session(thread_id)键控,而 `resolve_user_id` 每请求可变——**用户中途换 persona 不换 session 时日志跨人**的语义(旧 ConversationRepository 有"换人清史",checkpointer 无此概念)在 E-2 spec 里显式定,不许靠默认行为。

**② RouteKind 7→6 塌缩迁移面。** 图内小(3 处):`route_after_router` 三分支、`build.py` 条件边表、`emit_router` 的 SSE 事件三分支。图外是大头:`schemas/router.py` 的 `InputKind` 经 CHITCHAT_REPLY payload **直达前端**(`frontend/lib/types.ts:135-141` 硬编码同值枚举)——标签闭集改名 = 前后端契约同步改;归并删除点 `route_turn.py:300-302`。stub 测试迁移清单(E-1 动手时的 intentional 清单,按四类):
- **A 翻转断言**:`test_router.py` 的「fallback 恒 planning」测试(反转对象本身);
- **B 垫桩改道**:`test_d2_failure_drain.py` 6 个用例——输入靠规划词表快路进 planning,词表删除后需 monkeypatch classify_fn 钉住 planning(它们测规划链降级,不是路由);
- **C 退役/改写**:`test_router_node_planning_fast_path.py` 整文件(测的就是要删的词表)、`test_router_node_feedback.py`(:105 归并断言删除;:84「ambiguous+有方案→feedback」是行为反转的标志性断言,新世界应变澄清)、`test_feedback_detector.py`(语义词删/字面信号留,逐条分拣)、`test_dialogue_acts.py`(行为契约平移到 6 标签断言面)、`test_router_context.py`(旧 prompt 机制消亡);
- **D 断言搬家**:`test_itinerary_qa.py`/`test_persona_qa.py`(画像问答→壳2)、`test_soft_constraint_sniffer.py`(**ADR 未写明的联动点**:emotional 塌缩进「陪聊」后嗅探器挂点需重新指定,E-2 spec 时定)、`test_router_node_injection.py`(壳1 保留,断言字面值随闭集改名)。

## 边界(不在本 ADR)
- intent 层 pin 抽取(D-7 跨层依赖,单独立项);轨道 B judge 审计工程(语料 eval 弧);narration 质量;前端新组件(不需要——chips 复用)。

## 备选与拒因
- **保留两段式分类**(路由器+dialogue_acts 独立)——拒:双倍调用、两套词汇的缝永在、「确认」永远靠映射表转译。
- **关键词继续追覆盖**——拒:实测已证失败(「你好」→planning / 歧义→硬猜重规划);表面形式无穷,误吞代价 > token 成本。
- **上下文不设上限**——拒:无界输入没有定义好的失败模式;保险丝 + 钉锚是有定义的退化。
- **澄清问到清楚为止**——拒:审问式体验 + 死循环;一次上限 + 保守解释 + chips 出路。
- **LLM 挂时维持「默认规划」**——拒:违反 L0 禁令 1;降级往保守退不往鲁莽退。

## 实施拆步(E 系列;先收官 ADR-0010 的 D-7/D-8,再开本弧。2026-07-02 修订:插入 E-0、日志提前)
- **E-0** 会话底座收口(**见 ADR-0012**):图状态单一真相源、ConversationState 葬礼、confirm 回写、重置纪律收口、USE_LANGGRAPH 退役——E-1/E-2 的前提,不做则打包器与「满足-首轮」都踩浮沙。
- **E-1** 地板反转 + 词表清洗:`fallback_decision` 改保守(无方案陪聊引导/有方案澄清引导);语义词表删除、字面短路保留;stub 图级测试按「前置核实②」的 A-D 四类清单 intentional 迁移。
- **E-2** 统一路由器 + 打包器:**第一块砖 = 会话日志**(轮次日志接 `messages` 通道 + 方案版本志累积字段,带「前置核实①」四条护栏);然后一次调用 6 标签+槽位;吸收 dialogue_acts(ADR-0002 标 Superseded);RouteKind 塌缩 + graph 边迁移 + 前端 InputKind 枚举同步;八场景少样本进 prompt。
- **E-3** 澄清链路(瘦身版,日志已由 E-2 承担):pending_clarification 状态 + chips 呈现 + 一次上限语义。
- **E-4** 轨道 A 路由语料(标注回归,场景×话术×状态维度)上线 CI;轨道 B judge 审计脚本(离线)。

## 落地状态
🔁 **部分落地**(决策 2026-07-02;前置核实已完成并回填本文;D-7/D-8/E-0 全落地)。
**E-1 已完成**(2026-07-03):地板反转(fallback_decision 无方案→陪聊引导/有方案→澄清引导+三 chip,绝不 PLANNING;make_planning_decision 工厂分家)+ 兜底归并(route_turn:301)删除+四张规划信号表整删+反馈词表逐词分拣(纯品味词删,「无聊/腻了」族经实证保留——钉着 refiner 节奏收缩契约)+ **壳2 canonical 字面短路**(PRIMARY_CTAS+地板三 chip+八场景文案,单一真相源,断网演示"点场景卡即规划"通道)。深审修正:壳2 提到 Layer 1 之前——canonical 精确全串确定性高于启发式强信号,否则含保留词的场景文案(S1「别太贵」)中期点卡会被吞成 feedback。实测:「我不想玩这个了」(有方案)→澄清引导气泡,不再硬猜重规划;1106 passed。余 E-2/E-3/E-4(排 F 系列后)。
**E-1 已知缺口(F-6 联动审查坐实,修复已排队)**:地板 chip「重新规划一个」点击后按 planning 走全新意图解析,但解析输入是这五个字本身——**用户原始需求丢失**(主聊天:解析出空泛意图;房间:零上下文新 session 退化为陪聊)。修复方向:canonical 字面「重新规划一个」命中时复用上一事件的 raw_input 重解(主聊天 intent 路径窄规则;房间 `_trigger_fresh_plan` 传 `current_intent_dict.raw_input`),F-1 落地后小修,行为已有测试钉住现状防遗忘。
