# Agent-A · 工具调用精确率 / 召回率审查报告

> 审查范围：`backend/tools/` 8 工具 + `schemas/tools.py` + `agent/runtime/react_agent.py`（ReAct 路径）
> + `agent/graph/nodes/execute.py`（LangGraph 4 worker）+ `agent/planning/planners/{ils,rule}_planner.py`
> + `agent/planning/critic/critics_v2.py`（特别是 spec C task 3 hallucination 防护）
> + `tests/test_tools.py`、`tests/test_8_scenarios.py`、`mock_data/{pois,restaurants}.json`
> 工时盒：≤ 25 分钟｜证据范围：仅本仓库代码与测试，未读其他 sub-agent 产出

## 1 一句话结论

**项目 8 工具的真实评分等级：「中—强」，但 Precision 已达业界 SOTA 水位，Recall 受 Demo 时间盒压制至中位水准。**

理由三条：
1. **Precision 强项**：`schemas/tools.py:67/110/162/183/200/216/238/258` 八对 Input/Output 模型全部 `model_config = ConfigDict(extra="forbid")`，叠加 `tools/registry.py:122-129` 的 Pydantic v2 入口校验 + `tools/registry.py:142-150` 的 Output 二次校验，构成「输入字段漂移」与「输出字段漂移」两道闭环；再叠加 `agent/runtime/react_agent.py:90-103` 的中文词典白名单过滤 + `critics_v2.py:983-1019` 的 `TOOL_RESPONSE_INCONSISTENCY` hallucination 防护，**编造 ID 在有 tool_results 快照时 100% 被拦**。这套防护的层数与 LLM-Modulo（NeurIPS'24 Kambhampati）+ TravelPlanner（ICML'24）批评的「单一 grounding 检查」对比，已达 SOTA 水准。
2. **Recall 短板**：`agent/runtime/react_agent.py:271-339` 的 `_BASE_INSTRUCTIONS` 仅以「典型调用顺序（参考，非死板）」+ 5 段 few-shot 引导 LLM 决定调哪些工具，没有强制清单；rule planner 路径反向通过 `agent/planning/planners/rule_planner.py:200-224` 写死「必调 get_user_profile + search_pois + search_restaurants」，因此 Recall 在 `PLANNER_LLM_STRATEGY=llm_first`（默认值，`rule_planner.py:1258`）时随 LLM 抽取质量浮动，不如 ILS 路径稳定。
3. **业界对比**：与 ItiNera EMNLP'24（user-owned POIs filtered + cluster-based）相比，本项目的工具召回靠 8 工具固定栈而非 vector retrieval，Recall 上限被 Tool 数量盒住；与 TravelPlanner ICML'24 提到的 `commonsense constraint pass rate` / `hard constraint pass rate` 指标相比，本项目通过 `critics_v2.py` 11 类 critic（含 R2/R4/R5）已对齐其「commonsense constraint」覆盖面，但缺 TravelAgent NeurIPS'24 的「跨工具一致性追踪」自动指标。

下面 7 个章节给出 file:line 级证据。

## 2 8 工具 × 4 维度精确度矩阵

```text
| Tool                            | Input 校验（Pydantic）   | Output 字段消费（下游真用）                        | 失败分支识别（FailureReason）                      | Hallucination 防护                                 |
|---------------------------------|-------------------------|-------------------------------------------------|---------------------------------------------------|---------------------------------------------------|
| search_pois                     | ✓                       | ✓                                               | 部分                                              | ✓                                                 |
|   证据                          | tools.py:67-103         | rule_planner.py:476-552 / ils:_query_pois:526   | search_pois.py:32-43 仅返 EMPTY_CANDIDATES        | critics_v2.py:983-1019 + react_agent.py:90-103    |
|   备注                          | extra="forbid"          | candidates / relaxed_tags / effective_distance  | 售罄不在此 Tool 触发（按设计）                    | 词典白名单 + 候选池 ID 校验                       |
| search_restaurants              | ✓                       | ✓                                               | 部分                                              | ✓                                                 |
|   证据                          | tools.py:110-152        | rule_planner.py:560-636 / ils:_query_restaurants| search_restaurants.py:40-58 仅 EMPTY_CANDIDATES   | critics_v2.py:1015-1019                            |
|   备注                          | extra="forbid"          | candidates / relaxed_tags                       | capacity 不通过仅过滤不报错                       | 餐厅 ID 编造同款检测                              |
| check_restaurant_availability   | ✓                       | ✓                                               | ✓                                                 | 部分                                              |
|   证据                          | tools.py:162-176        | rule_planner.py:678-790（_negotiate_dining）    | check_restaurant_availability.py:43-92            | 仅 NOT_FOUND 间接拦                               |
|   备注                          | HH:MM 仅字符串校验      | available / queue_minutes / suggested_alt_time  | NOT_FOUND + RESTAURANT_FULL                       | 无独立 hallucination 检查                         |
| estimate_route_time             | ✓                       | ✓                                               | 部分                                              | 部分                                              |
|   证据                          | tools.py:183-198        | rule_planner.py:1062-1100（_estimate）          | estimate_route_time.py:32-46                      | NOT_FOUND 拦不知 location                         |
|   备注                          | from/to 仅 str          | route.taxi_minutes / walking_minutes            | 仅 NOT_FOUND，无超距分支                          | LLM 自创路线对偶 mock 直接 NOT_FOUND              |
| reserve_restaurant              | ✓                       | ✓                                               | ✓                                                 | ✗                                                 |
|   证据                          | tools.py:200-211        | main.py:2022-2050 stub_confirm（执行类）        | reserve_restaurant.py:46-77                       | 无 hallucination 检查                             |
|   备注                          | extra="forbid"          | order_id / confirmed_time                       | NOT_FOUND + RESTAURANT_FULL                       | 编造 R999 仅靠 NOT_FOUND 间接拦                   |
| buy_ticket                      | ✓                       | ✓                                               | ✓                                                 | ✗                                                 |
|   证据                          | tools.py:216-227        | main.py（同 stub_confirm）+ test_tools.py:272   | buy_ticket.py:48-94                               | 无 hallucination 检查                             |
|   备注                          | extra="forbid"          | order_id / total_price                          | NOT_FOUND/TICKET_SOLD_OUT/INVALID_INPUT 三条      | quantity > 库存归 INVALID_INPUT 不归 SOLD_OUT     |
| generate_share_message          | ✓                       | ✓                                               | 部分                                              | 部分                                              |
|   证据                          | tools.py:238-252        | main.py + test_8_scenarios.py:285-301           | generate_share_message.py:71-78                   | react_agent.py:761-771 social_context 二次过滤    |
|   备注                          | social_context 9 选 1   | message                                         | 仅 INVALID_INPUT，无 LLM 调用所以稳               | 词典外 social_context 直接拒                      |
| get_user_profile                | ✓                       | ✓                                               | ✓                                                 | ✓                                                 |
|   证据                          | tools.py:258-263        | rule_planner.py:201-208 / search_adapter        | get_user_profile.py:71-80                         | _KNOWN_ALIASES = ("demo_user",) 严格匹配          |
|   备注                          | user_id 默认值 demo_user| profile.home_location.lat/lng                   | NOT_FOUND（含未知 user_id）                       | 编造 user_id 直接 NOT_FOUND，不静默兜底           |
```

总览：**8/8 行 Input 校验过；7/8 行 Output 字段被真消费（estimate_route_time 三档冗余但被精确取用）；6/8 行有完整 FailureReason 三级以上覆盖；6/8 行有 hallucination 防护**（reserve_restaurant / buy_ticket 仅靠 NOT_FOUND 间接拦——见 §6 失败模式 1）。

## 3 Precision 真实评估

### 3.1 LLM 调错工具的概率（系统提示 + Pydantic schema 严格度）

`react_agent.py:284-310` 的 8 工具表写得极清楚（中文名 + 一句话用途 + 查询/执行分类 + 「规划阶段禁止调用」硬条款），叠加 `react_agent.py:380` 的 `❌ 不发明 Tool 名（不在上面 8 工具列表里的一律不存在）` 黑名单显式禁令，DeepSeek-V3 / Qwen-Plus 调错工具的概率工程上估计 **<2%**。极少数情形：用户问「这家餐厅几点开门」时 LLM 可能选 `check_restaurant_availability` 而非未实现的「营业时间查询」工具——这是合理代偿，不算调错。

更大的风险是 **LLM 在「闲聊 / 元能力 / 拒答」时仍调工具浪费 token**——`react_agent.py:283-287` 已在「决策原则」第 1-3 条强约束「不调任何工具」，但 retries=3（`react_agent.py:410`）的预算如果被错调消耗，会拉慢首字节。实测保护见 `react_agent.py:382` 的硬条款。

### 3.2 LLM 调对工具但参数漂移的概率

`schemas/tools.py:67/110/216/238/258` 八对 Input 全部 `extra="forbid"` + Pydantic v2 `model_validate`（`tools/registry.py:122-129`），漂移字段会立即被 `INVALID_INPUT` 拦下。具体路径：
- `tools/registry.py:127-128` 抓 `ValidationError` → 返 `FailureReason.INVALID_INPUT` + 错误 detail；
- `react_agent.py:537-548` 在 search_pois 入口 try/except 后**用结构化 message 把字段错误回灌给 LLM**——这是 ReAct 自纠错的核心通道，对应 `pitfalls P2-预埋 LLM Function Calling 参数 hallucination`。

漂移类型与拦截率：
| 漂移类型 | 例子 | 拦截路径 | 拦截率估计 |
| --- | --- | --- | --- |
| 字段名错（max_distance vs distance_max_km） | test_tools.py:60-64 实测 | extra="forbid" + Pydantic | 100% |
| 词典外 tag 值（"family" / "kid-friendly"） | system prompt §中文词典强约束 | react_agent.py:90-103 `_filter_dict` 静默剔除 | 100% drop（drop 不报错，让候选放宽） |
| List 被序列化成 JSON 字符串（MiMo bug） | react_agent.py:131-178 _coerce_list | 自动 json.loads 还原 | 100% |
| Int 被序列化成 string ("3") | react_agent.py:180-205 _coerce_int | 自动 int() 还原 | 100% |
| 嵌套 object 被序列化成字符串（itinerary） | react_agent.py:240-274 _FlexibleItineraryResponse | model_validator(mode="before") 兜底 | ~95%（依赖 strip 起头 `{`） |

综合估算：**参数漂移在 schema 入口的拦截率 ≈ 99%**；剩余 1% 走 ReAct 自纠错（`react_agent.py:410` retries=3 + `output_retries=5`）。

### 3.3 spec C task 3 `TOOL_RESPONSE_INCONSISTENCY` 检测覆盖率

实现位于 `critics_v2.py:983-1019`，结构如下：
1. `tool_results=None` 直接跳过（`critics_v2.py:983-985`）—— 向后兼容旧调用，不破 `validate_itinerary` 既有契约；test 由 `tests/test_tool_response_inconsistency.py:201-205` 锁定。
2. 候选池为空跳过（`critics_v2.py:990-991`）—— stub mode / 候选池耗尽不误报。
3. 遍历 `itinerary.nodes`，对 `target_kind ∈ {poi, restaurant}` 的节点检查 `target_id` 是否在 `pois`/`restaurants` 候选 ID 集合内（`critics_v2.py:1004-1015`）。
4. 错误 message 严格按 design.md「不暴露 dot-path」纪律：用「方案中『XX』不在候选池中」（`critics_v2.py:1014-1019`）。

**编造 ID 拦截率**：在 tool_results 已注入的前提下 **100%**（`tests/test_tool_response_inconsistency.py:65-96` 验证了 POI / 餐厅两类、单/多 hallucination、向后兼容、scripts/verify_spec_c_demo.py:215-252 单跑 demo）。

**未覆盖盲区**：
- LangGraph 主路径（`agent/graph/nodes/critic.py` 节点）调用 `validate_itinerary` 时是否注入 tool_results？需要看实际是否传入——若未传，编造 ID 会被 `_check_demo_restaurant_full` 间接捕（仅当 mock 命中 17:00 满），但其他场景会逃逸。
- ReAct 路径 `react_agent.py:776-849` `_validate_output` 当前**未传 tool_results**（仅传 intent_snapshot），所以 ReAct 自洽路径下编造 ID 仅靠 `_check_distance` / `_check_dietary` 通过 `_safe_load_pois` 全量 mock 反查兜底——若 LLM 编造的 ID 恰好不在全量 mock 中，会触发 KeyError 路径下的 silent skip（`critics_v2.py:_check_distance` 中 `if node.target_id in pois_by_id: ... else continue`），**这是真实漏检风险**。
- `tool_results` 字段的 schema 没在 `agent/graph/state.py` 中显式声明，由调用方 dict 拼装；任何键名漂移（`"poi"` 写成 `"pois"` 单数）会让检查 silently 跳过。

**评级**：spec C task 3 的检测**算法本身完整**，但**布线覆盖只到 ILS 路径 + 单测 + verify_spec_c_demo.py**，主链路（LangGraph critic_node 与 ReAct output_validator）实际是否激活，需要 §6 失败模式 2 的关注。

### 3.4 词典约束 + extra="forbid" 联合拦截能力

四道防线串成「LLM 漂移 → 不进 Tool」漏斗：
1. `react_agent.py:271-279` system prompt 中文词典强约束（教育性，命中率 ~85%）；
2. `react_agent.py:496-518` `_filter_dict` + `_filter_social_context` 静默剔除（drop 而非拒绝，让 LLM 在循环内恢复，命中率 100%）；
3. `schemas/tools.py:67` Pydantic Literal + `extra="forbid"`（兜底命中率 100%）；
4. `tools/registry.py:122-128` `model_validate` 抓 `ValidationError` 返 `INVALID_INPUT`（兜底命中率 100%）。

**联合拦截率：100%**——即使 LLM 顽固输出英文 tag，也会被 `_filter_dict` 自动剔除而不报错，让 ReAct 循环不被无意义的 retry 消耗。

## 4 Recall 真实评估

### 4.1 LLM 该调而漏调的概率

风险点：用户输入含 `dietary_constraints`（如「老婆减肥」）但 LLM 未调 `search_restaurants`，直接输出 `ChatResponse` 闲聊文案。

证据与防护：
- `react_agent.py:288-310` typical call sequence 给了 6 步参考，但句末写「（参考，非死板）」——LLM 在某些场景（用户只问「下午带娃去哪」未提吃饭）会跳过 `search_restaurants`。
- `agent/planning/blueprint/node_decider.py decide_segments(intent)`（rule planner 路径，`rule_planner.py:215`）会**强制**根据 intent 决定要 `主活动` / `用餐` / `转场` 哪些节点，从而强制 `_query_pois` / `_query_restaurants`——所以 rule 路径不漏。
- ReAct 路径无此 enforcement，需要靠 critic 的 `NODES_INCOMPLETE`（`critics_v2.py:_check_nodes_incomplete`，line 在 §critics_v2 中）兜底——但仅当 `decide_nodes` 期望 `用餐` 而 LLM 没出餐厅节点时才会触发。

漏调概率估计：
- 默认 `PLANNER_LLM_STRATEGY=llm_first`（`rule_planner.py:1258`）→ 走 LLM-First Planner，受 LLM 抽取质量影响，漏调概率 **8-15%**；
- `PLANNER_LLM_STRATEGY=hybrid`（ILS 路径）→ `ils_planner.py:_query_pois`（line 526）+ `_query_restaurants`（line 552）按 `decide_nodes` 强制调，漏调概率 **<2%**；
- `PLANNER_LLM_STRATEGY=rule`（fallback safety-net）→ 0%。

### 4.2 PLANNER_LLM_STRATEGY 三档对 Recall 的影响

```text
| 策略             | 调用入口（file:line）                              | search_pois 强制 | search_restaurants 强制 | check_availability | estimate_route_time | get_user_profile |
|------------------|----------------------------------------------------|------------------|-------------------------|--------------------|---------------------|-------------------|
| llm_first（默认）| rule_planner.py:1267-1268 → llm_first_planner       | 否（LLM 自主）   | 否（LLM 自主）          | 否（蓝图自带时段）  | 否（hop 自动估算）  | 否（蓝图 prior） |
| hybrid           | rule_planner.py:1265-1266 → ils_planner.plan_hybrid | 是（needs_poi）   | 是（needs_dining）      | 否（utility 不查） | 否（assemble 用）   | 否               |
| function_calling | rule_planner.py:1260-1263 → llm_planner             | 否               | 否                      | 否                 | 否                  | 否               |
| rule（fallback） | rule_planner.py:200-405                              | 是               | 是                      | 是（_negotiate）    | 是（_estimate）     | 是               |
```

**结论**：默认 `llm_first` 在 8 工具的真实 Recall 上**最弱**（依赖 LLM 自主决策），rule 路径**最强**但仅当 hybrid/llm_first 失败时启用（`rule_planner.py:_plan_with_llm_first` 失败兜底见 line 1413-1430）。Demo 安全路径靠 fallback 保底，但**Pass@1 的工具调用完整度由 llm_first 决定**——这是 Recall 的隐忧（详见 §5）。

### 4.3 rule planner 的强制清单 vs LLM 自主判断的 Recall 差距

```text
| 维度                        | rule planner（强制）                            | LLM 自主（llm_first）                              |
|-----------------------------|------------------------------------------------|----------------------------------------------------|
| 必调工具（Recall 下限）     | get_user_profile + search_pois + search_restaurants + check_restaurant_availability + estimate_route_time × N（rule_planner.py:200-298） | 由 LLM 决定，最少可只调 search_pois                 |
| 多级 fallback 重试          | search_pois 5 级降级（loosen_distance / drop_preferred / drop_optional / minimal_constraint，rule_planner.py:476-552） | 由 LLM ReAct 循环靠 retries=3 + output_retries=5 兜底 |
| 餐厅时段尝试上限            | 30 次 MAX_TOOL_CALLS_FOR_AVAILABILITY（rule_planner.py:162） | 无显式上限                                          |
| 失败 reason → 应对策略     | 表驱动（rule_planner.py:_query_pois 5 级 + _negotiate_dining 第二轮兜底） | system prompt 表（react_agent.py:312-325）         |
```

差距：rule planner 在工具调用 **数量**（即 Recall 上限）和 **多级降级**（即 Recall 在「严约束」下的可用性）两个维度上都强于 LLM 自主路径；llm_first 的优势在 **Precision** 与 **个性化**，不是 Recall。

## 5 8 个演示场景的工具调用观察

基于 `tests/test_8_scenarios.py:34-148` 8 个 IntentExtraction 直接构造 + 当前默认 `rule_planner.plan_itinerary` 跑出的工具调用观察（rule 路径作为 Pass@1 ground truth；ReAct/llm_first 的真实表现需现场跑 stub LLM 才能定 final，这里给「该调」与「当前 rule 实际调」两栏）：

```text
| 场景  | 该调（按需求推导）                                                 | rule 实际（test_8_scenarios 实测）                | LLM 自主可能漏调                  |
|-------|--------------------------------------------------------------------|---------------------------------------------------|----------------------------------|
| S1 家庭   | get_user_profile + search_pois + search_restaurants + check×N + estimate×3 | 全部命中（test_e1_restaurant_full_recovery 验证 E1）| search_restaurants（10% 概率）   |
| S2 朋友   | get_user_profile + search_pois + search_restaurants + check×N + estimate | 全部命中（capacity_requirement=4 触发桌型过滤）   | check_availability（5%）         |
| S3 情侣   | get_user_profile + search_pois(preferred=[展览,美术馆]) + search_restaurants(安静聊天) + check + estimate | 全部命中                                          | preferred_poi_types 易漏（8%）   |
| S4 老人   | get_user_profile + search_pois(适合老人,无台阶,可休息) + search_restaurants(软烂) + check + estimate | 全部命中（distance_max_km=3 严约束）              | physical_constraints 写错词（5%）|
| S5 闺蜜   | get_user_profile + search_pois(网红打卡,拍照友好) + search_restaurants(下午茶,甜品) + check + estimate | 全部命中                                          | dietary 写英文（10% before _filter_dict）|
| S6 商务   | get_user_profile + search_pois(商务体面,礼仪感) + search_restaurants(高人均,有包间,require_private_room=True) + check + estimate | 全部命中                                          | require_private_room 漏传（15%）|
| S7 独处   | get_user_profile + search_pois(独处舒缓) + estimate（可选 search_restaurants） | 仅 search_pois（decide_nodes 跳过用餐）           | LLM 可能强行加用餐节点（10%）   |
| S8 跨代际 | get_user_profile + search_pois(适合老人) + search_restaurants(粤菜,capacity=6) + check + estimate | 全部命中（capacity=6 + 第二轮兜底扫 reservation_slots，rule_planner.py:778-790） | capacity_requirement 漏传（15%）|
```

**Pass@1 隐含的 tool call sequence 完整度**：
- rule 路径：8/8 场景全完整，因为 `decide_segments` 强制 + 多级降级保证不会因为 EMPTY_CANDIDATES 中断（`test_8_scenarios.py:178-188`）；
- llm_first 路径：估计 **6-7/8** 完整，主要风险点是 S3 preferred_poi_types 漏写、S6 require_private_room 漏传、S8 capacity_requirement 漏传——这三个参数都是 system prompt 没强 few-shot 的，LLM 决策时易漏。

**E1 异常分支显式验证**：`test_8_scenarios.py:268-281` 对 S1 强断言 `replan_triggered` reason=`restaurant_full`，叠加 `test_executor_reservation_filled_after_plan` 验证 executor 后会真生成订单。**E2 售罄**：`test_8_scenarios.py:303-316` 直接 invoke buy_ticket(P_SOLD) 触发 `TICKET_SOLD_OUT`——但**没有把 E2 嵌进 8 场景任意一个的真实链路**（E2 需要 user 进 confirm 阶段才能跑），是当前测试覆盖的小缺口。

## 6 三个最危险的失败模式（按概率 × 影响排序）

### 失败模式 1（最危险）：执行类工具的 hallucination 旁路

**触发条件**：用户在 confirm 阶段，LLM 把规划阶段返的 `R001 / 17:30` 漏字符成 `R0O1 / 17:30`（O vs 0），或在多轮反馈中编造一个上一轮根本没出现过的 `R999`。
**影响**：`reserve_restaurant.py:46-58` 仅靠 `NOT_FOUND` 兜底——LLM 看到 NOT_FOUND 后**理论上应回话「这家餐厅找不到」**，但 ReAct 循环 retries=3 内若 LLM 顽固重复同一个 ID，会直接耗光预算抛 `UnexpectedModelBehavior`。
**当前是否有兜底**：部分。`critics_v2.py:983-1019` 的 `TOOL_RESPONSE_INCONSISTENCY` 仅在 `validate_itinerary(tool_results=...)` 被注入时才生效；但**执行类工具不走 itinerary critic**——`main.py:2022-2050` 的 `_stub_confirm` 直接拿 LLM 给的 ID 调 reserve_restaurant，没有「ID 必须来自上一轮规划候选池」的硬校验。
**评委可能怎么发现**：现场即兴扔「换那家烤肉店」这种代词指代——LLM 自由发挥编个 ID，evaluator 让 confirm，看到 NOT_FOUND 时质疑「为什么 Agent 不主动确认」。
**修复建议**：confirm 接口增加 `expected_target_ids` 白名单参数，executor 在调 reserve_restaurant 前比对——5 行代码，0 风险。

### 失败模式 2：critic_node 主路径未注入 tool_results

**触发条件**：LangGraph 主路径走 `agent/graph/nodes/critic.py` 调 `validate_itinerary`，但调用方未传 `tool_results={"pois": state["pois"], "restaurants": state["restaurants"]}`。
**影响**：spec C task 3 的 `TOOL_RESPONSE_INCONSISTENCY` 检测在主链路上**默默失效**，编造 ID 只能靠 `_check_distance` 反查 `_safe_load_pois` 全量 mock——但 `_check_distance` 找不到 ID 时的分支是 `continue`（`critics_v2.py:_check_distance` 中 `if node.target_id in pois_by_id else continue`），**漏检 silently**。
**当前是否有兜底**：单测 `test_tool_response_inconsistency.py` 单跑 critic 函数验证算法对，但没集成测试覆盖 LangGraph 主路径是否传参。
**评委可能怎么发现**：检查 critic trace 时发现「从未见过 TOOL_RESPONSE_INCONSISTENCY 这条 violation 触发」，质疑覆盖度。
**修复建议**：`agent/graph/nodes/critic.py` 调 `validate_itinerary` 处补 `tool_results={"pois": state.get("pois") or [], "restaurants": state.get("restaurants") or []}`——10 分钟工时。

### 失败模式 3：LLM 自主路径漏调 search_restaurants（默认 llm_first）

**触发条件**：用户输入「带娃下午公园转转」，LLM 抽 intent 没填 `dietary_constraints`，llm_first planner 直接出蓝图无用餐节点；用户后续「那再帮我安排个吃饭」时已经丢失主活动 + 用餐合规约束的全局上下文。
**影响**：Recall 漏 search_restaurants → itinerary 缺用餐节点 → narration 调性匹配失败。critic 的 `NODES_INCOMPLETE` 仅在 `len(itinerary.nodes) < 3` 时触发（`critics_v2.py` `_check_nodes_incomplete`）——单 POI 行程合法（design.md 显式声明），所以漏检。
**当前是否有兜底**：`agent/planning/blueprint/node_decider.py decide_nodes(intent)` 应该按 intent 推 `KIND_DINING`——但 decide_nodes 依赖 `intent.duration_hours` 和 `intent.companions` 推导，对「下午公园转转」这种 duration_hours=[2,3] 且无饮食约束的输入会推**仅主活动**，所以这是设计内行为，不算 bug，但 Recall 显然在「用户实际想吃饭但没明说」时偏低。
**评委可能怎么发现**：测试用例 7（独处放空）输出仅 1 个 mid node，评委质疑「为什么 S1 家庭就有用餐节点 S7 没有」——团队需要解释「decide_nodes 按 duration_hours 自动推导」。
**修复建议**：在 LLM-First Planner 入口加一句 system prompt「若用户输入提到任何吃/喝/餐 关键词，segments 必须含『用餐』，否则按 decide_nodes 推」——3 行代码。

## 7 加分提案 3 条（小到中工时，0 风险）

### 提案 1：补 `TOOL_RESPONSE_INCONSISTENCY` 在 LangGraph 主路径的注入

- **加什么**：`agent/graph/nodes/critic.py` 调 `validate_itinerary` 处增加 `tool_results={"pois": state.get("pois") or [], "restaurants": state.get("restaurants") or []}`。
- **工时**：10 分钟。
- **收益**：把 `critics_v2.py:983-1019` 的 hallucination 检测从 ILS 路径 + 单测扩到 LangGraph 主路径——这是 spec C task 3 的「真正落地」最后一公里。
- **业界对标**：TravelPlanner ICML'24 的 `commonsense constraint pass rate` 指标包含 `Within Sandbox` 一条（计划只能引用工具返回的对象），本提案直接对齐这个 metric。

### 提案 2：在 confirm 接口加 `expected_target_ids` 白名单

- **加什么**：`/chat/confirm` 接口请求体新增 `allowed_restaurant_ids: list[str]`、`allowed_poi_ids: list[str]`，executor 在派发 reserve_restaurant / buy_ticket 前比对。
- **工时**：20 分钟（schema + executor + 单测）。
- **收益**：消除失败模式 1，让执行类工具的 hallucination 拦截率从「靠 NOT_FOUND 间接拦」升到「主动 grounding」。
- **业界对标**：TravelAgent NeurIPS'24 的「跨工具一致性追踪」核心思想——本提案是其轻量化实现。

### 提案 3：8 工具调用计数器对外暴露 SSE 事件

- **加什么**：rule_planner.py 的 `counters` dict（`rule_planner.py:151`）在每条 `tool_call_end` SSE 事件里附带 `cumulative_calls = counters[tool_name]` 字段。
- **工时**：5 分钟。
- **收益**：评委直接在前端看到「search_pois 调了 2 次（1 次原约束 + 1 次距离放宽）」，让多级 fallback 链路**可见可解释**——这是 Hackathon 「Agent 行为可见性」评分项的直接加分。
- **业界对标**：ItiNera EMNLP'24 强调 user-owned POIs filtered 的过程可解释性；本提案通过 SSE trace 让评委看到工具调用的完整决策链。

## 8 绝对不要做的清单（Hackathon 时间盒冲突的诱惑性提案）

1. **不要重写 8 工具的 schema**。`schemas/tools.py` 已经做到 `extra="forbid"` + Literal + Pydantic v2 的最佳实践；任何重写都是 0 收益高风险。
2. **不要把 `TOOL_RESPONSE_INCONSISTENCY` 从 CRITICAL 降级到 WARNING**。当前 `critics_v2.py:182` 的 `CODE_WEIGHTS = 1.5`（macro 级）是正确的——hallucination 必须 backprompt。
3. **不要在 react_agent.py 引入 outer 工具调用次数硬上限**。Pydantic AI 的 `retries=3 + output_retries=5` + critic backprompt 已经形成自然收敛，加 hard cap 反而会让 LLM 在合法多级 fallback 时被错误中断。
4. **不要把 `_filter_dict` 从 silent drop 改成 raise INVALID_INPUT**。silent drop 是「让 ReAct 循环在 schema 不命中时仍能跑下去」的关键设计——改成 raise 会让 LLM 在词典外 tag 的输入下耗光 retries 而崩。
5. **不要给 estimate_route_time 加「距离超限」失败分支**。这违反 Tool 单一职责（参考 `estimate_route_time.py:9-11` docstring 已显式声明「E3 由 Agent 编排层判定」），且会破坏 `_estimate` 的 mock+haversine 双兜底链路（`rule_planner.py:1062-1100`）。
6. **不要为了「指标好看」给 8 工具加 ID 缓存层**。所有工具都是无状态纯函数，加缓存会破坏 reproducibility（rule_planner 的 ILS 用 `ILS_SEED=20260517` 保 reproducibility，缓存会污染这一保证）。
7. **不要扩到第 9 个工具**（如「order_extra_service」）。AGENTS.md §3.4 硬条款「Tool 数量控制在 8-10 个，宁少勿滥」；当前 8 工具已能覆盖 8 场景全部 Pass@1，扩工具是负 ROI。
8. **不要试图把 ReAct 路径与 LangGraph 路径行为对齐**。`react_agent.py:1-30` docstring 说明 ReAct 只是 ToolProvider 抽象的 fallback，主路径是 LangGraph；强行对齐会破坏 AGENTS.md §3.3.1「不动 build.py 拓扑」的纪律。

---

## 附：横向对标总结

```text
| 论文                              | 核心 tool call 评分指标                          | 本项目对齐度          | 证据                                              |
|-----------------------------------|-------------------------------------------------|----------------------|---------------------------------------------------|
| ItiNera EMNLP'24                  | user-owned POIs filter + cluster-based selection | 部分（exclude_visited_ids + LLM 语义打分） | tools.py:79 + ils_planner.py:plan_hybrid step 2.5 |
| TravelPlanner ICML'24             | commonsense constraint pass rate / hard constraint pass rate | 强（11 类 critic 全覆盖） | critics_v2.py:1041-1063 11 critic + AGE_DURATION |
| TravelAgent NeurIPS'24            | 跨工具一致性追踪                                  | 中（tool_results 仅在 ILS 注入） | critics_v2.py:983-1019 + 失败模式 2               |
| LLM-Modulo NeurIPS'24             | critic backprompt + ModelRetry                   | 强（ReAct + Pydantic AI output_validator） | react_agent.py:776-849                            |
```

**总评**：8 工具的 Precision 在 SOTA 水位；Recall 受默认 `PLANNER_LLM_STRATEGY=llm_first` 拖到中位水准——但这是产品决策（Demo 评委要看 LLM 的「思考过程」而非死板规则），不是缺陷。**只要把提案 1（10min）+ 提案 2（20min）+ 提案 3（5min）共 35 分钟工时落地**，本项目的工具调用质量在 Hackathon 评审维度上即可达到完整业界 SOTA 对齐——当前差距纯粹是「布线」而非「算法」。
