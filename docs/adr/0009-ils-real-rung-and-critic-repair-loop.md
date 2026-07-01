# ADR-0009 · ILS 升为真实 replan 梯级 + critic-to-solver 闭环修复

- **状态**：Accepted（2026-07-01 · grill-with-docs 会话；承接 ADR-0007 单栈 / ADR-0008 统一 critic）
- **范围**：规划层 ILS 兜底 planner（`planners/ils_planner.py`）、其在 LangGraph 的接入（`graph/nodes/replan.py`）、组装器（`planners/rule_planner.py:_assemble_itinerary` + `blueprint/assemble_blueprint.py`）、以及统一 critic（ADR-0008 `validate`）在 ILS 路径的落地。

## 背景（现状诊断，已代码核实）

ADR-0008 收口了 critic 校验层，但把「ILS 接统一 critic」列为 Phase C 未落地。深挖 ILS 路径后发现，Phase C 远比「加个 adapter」复杂——**ILS 今天是装饰，而要展示的闭环亮点是三重坏的**：

- **地基 A：ILS 选中的候选被丢弃（生产 + 测试都是）。** `graph/nodes/replan.py:_RULE_ASSEMBLER_ADAPTER`（约 191-207）签名收 `candidate` 却不用，直接 `plan_itinerary(intent)` 重跑规则地板；`tests/test_planner_hybrid.py:_rule_assembler`（约 400）同样「镜像」这一丢弃。后果：`plan_hybrid` 的 utility 选点、黑名单、重搜对最终产物**零影响**，critic 校验的是规则地板的产物。ILS 从没被端到端验证过——测试只断言 `success`，从不断言「ILS 选的点真出现在产物里」。
- **地基 B：POI 停留时长与「选哪个 POI」「同行人年龄」都无关。** `_assemble_itinerary` 给 POI 节点定 `duration_min = main_activity_minutes`，后者由 `_resolve_time_window(intent)` 按 `duration_hours` 分段派生、`min(…, 120)`（`rule_planner.py:955`），只随 `chosen_time` 补偿只增不减；`assemble_from_blueprint` 逐字节透传（`duration_min=bp_node.duration_min`，约 374），不做任何年龄 cap。
  - 推论 1：`AGE_DURATION_MISMATCH` 换 POI 修不动——组装后时长与 POI 无关，带幼童时几乎必然触发且全体候选同样触发。
  - 推论 2：ILS `_overload_penalty` 惩罚的是 POI 的 `suggested_duration`（投影到客群），critic 量的是**排定的** `duration_min`——**两个量纲对不上**，penalty 在 rule 路径上近乎空转；ADR-0008 X-1「grounding 按同表预过滤 age 即消 thrash」对本码不成立（grounding 过滤 suggested，堵不住 scheduled 超 cap）。
- **retry 不 gate（真 bug）。** `plan_hybrid`（`ils_planner.py:384-390`）在 `_retry_with_critic_feedback` 之后 `return HybridResult(success=True, …, critic_report=run_critics(retried, intent))`——`run_critics` 调了，但 `success=True` **无条件**，从不检查是否 pass。换点修 A、修出 B，会被当成功返回（带病放行）。
- **黑名单键值错位（会静默毁掉旗舰演示）。** `check_demo_restaurant_full` 读**组装后的** `node.start_time`，而 `blacklist_rest_time` 存的是**候选的** `failed.dining_time`；`_assemble_itinerary` 的时长补偿会把两者错开。错位时拉黑的元组匹配不上真正满座那个钟点，「17:00→17:30」移时段会「看着在改、其实没绕开满座」，闭环静默不收敛。
- **grounding 结构上做不了营业时间预过滤。** `_grounding_filter_poi` 跑在**组装前**（无 `start_time`），只查 `business_status∈{closed}`，无法拿 `opening_hours` 对排定时段预检。所以 `OPENING_HOURS` 天然落在**组装后的 critic + retry** 这一侧（那里才有真实到达时间）——ADR-0008 字面的「营业时间 grounding 预过滤」不可行。
- **一条白送的架构对齐。** `validate` 的 Stage 0 命中即短路：闭环消费 `validate` 输出时，要么收到「一批纯结构码」（全落地板），要么收到「Stage 1/2 语义码」（才谈修复）。**ILS 能修的码集合 恰好等于「结构干净后才可见」的码集合**——闭环天然不会在结构烂掉的方案上浪费重搜。

## prior-art（映射表与闭环的既有范式）

- **min-conflicts / 启发式修复**（Minton et al. 1992, AIJ）：从违反约束的完整赋值出发，选一个「参与某条被违反约束的变量」重赋为冲突最少的值。一条 ViolationCode 指认冲突变量（哪个 POI/餐厅/时段），修复算子即重赋该变量。同篇两条硬教训：纯 min-conflicts 困于局部最优、需噪声（ILS 的扰动 + 5% 接受劣解正是）；**一步修复可能引入新冲突**（swap-induced，见决策 5）。
- **LLM-Modulo**（Kambhampati ICML'24；TravelPlanner 案例 Gundawar arXiv:2405.20625）：generate-test-critique 闭环，**hard critic 决定接受、soft 只建议**，metacontroller 汇总反馈回灌生成器，预算有界。本项目新意：被 backprompt 的「求解器」是 **ILS 算法而非 LLM**——critic-to-solver 闭环自纠，正是要展示的亮点。
- **Iterated Local Search**（Lourenço/Martin/Stützle 2001）：critic 反馈驱动的重搜 = 一次**有向扰动**（directed perturbation），而非随机扰动。
- **tabu / nogood**（Glover；CSP）：黑名单 = 从失败学到的 nogood/tabu 属性；`blacklist_rest_time` 是 (餐厅,时段) 上的 nogood；黑名单把某维掏空 = tenure 覆盖全域 → 需 aspiration 放宽或落地板。
- **TravelPlanner**（Xie et al. 2024）：hard vs commonsense 约束、多约束难同时满足、需反馈重规划——为「hard 驱动重搜 / soft 不 gate」背书。

## 决策

1. **ILS 真实化（承接 ADR-0007 replan 阶梯）**：修 `_RULE_ASSEMBLER_ADAPTER`（生产）+ `_rule_assembler`（测试）真调 `_assemble_itinerary(main_poi=candidate.main_poi, chosen_restaurant=candidate.restaurant, chosen_time=candidate.dining_time, …)`，让 ILS 选择进入产物。ILS 由此成为一条**产出被真正采用、区别于 rule 地板**的 replan 梯级，而非同效冗余横档。
2. **年龄约束进组装器（方案 α）**：`_assemble_itinerary` / 组装层读 `age_caps.py`，把 POI 节点时长夹到 `min(段时长, cap_for_age(companions))`；与总时长冲突时**优先年龄合规**（年龄是对人的硬约束，凑满 `duration_hours` 只是偏好，`check_duration` 的 ±30 容差先吸收）。`age_caps.py` 由此成为 **组装器（执行）/ critic（兜底）/ grounding（质量）/ ILS `_overload_penalty`（偏置）四方共读**的单一真相源。范围界定：只加 cap 读取 + 冲突取舍，**不**重做时长模型、**不**做「加填充短节点」。
3. **critic-to-solver 闭环（方案 甲，核心亮点）**：`plan_hybrid` 改吃统一 `validate`（经薄 adapter：`list[Violation]` → 其所需 report 形状）；路由真键是 **`(code, severity)`**，按 `.severity` 分流（hard 才驱动重搜，soft 只叙事）；`_classify_violation` 从「按旧 critic 名」改「按 **ViolationCode**」路由（旧 `ils_score_critic` 的 `.critic` 名已废）；删 `ils_score_critic.run_critics`。
4. **可执行违规落地（兑现 ADR-0008 决策 9）**：填 `Violation.node_ref`（B-2 预留、留空至今），让 blame assignment 从「整份候选拉黑」升级为「按肇事节点**定向**拉黑」——尤其 `SOCIAL_CONTEXT_MISMATCH`（现状 POI+餐厅无差别一起拉黑，偏钝）与 `OPENING_HOURS`。这是 ADR-0008 把 node_ref 推迟到有消费者再填的兑现点。
5. **修旗舰三坑（缺一则闭环演不出）**：① 决策 1 的 adapter；② retry 后**重跑 `validate` 并 gate**——仅当干净才 `success=True`，否则落 rule 地板；③ `blacklist_rest_time` 的键改用**组装后的 `node.start_time`**（或让 shift 落到「经组装仍成立」的 slot），消除键值错位。
6. **ViolationCode → ILS 重搜动作 映射表**（见下）。原则：能靠换 POI/餐厅/移时段廉价修的进闭环；结构性 / intent 派生 / 候选不变量的落 rule 地板（D2 安全，ADR-0008 X-2「不过度教 ILS 修每种」）。
7. **retry 有界**：ILS 内 backprompt 保持 1（至多 2）轮，够演「满座→改期」一跳即可；超轮 / slot 池无解 / 黑名单把某必需维掏空 → 落 rule 地板（tabu 耗尽 → 不产残缺行程）。轮数走 env-flag 让「延迟 vs 收敛」显式可调。
8. **删死 blueprint critic 层**：`run_blueprint_critics` 及其 critics（`_opening_hours_critic` 已于 B-2b 移植、`_age_aware_duration_critic` / `_resolve_age_caps` 被 age_caps.py 取代）已确认**无生产调用者**（`planner_llm_first` 随 ADR-0007 删除，仅测试 + 文档注释引用），随本 ADR 删除、更新相关测试。

### 映射表（ViolationCode → ILS 重搜动作，调和自两路独立推导 + 主代理收口）

| 判决 | ViolationCode | 动作 |
|---|---|---|
| **闭环重搜（可展示）** | `RESTAURANT_FULL_UNRESOLVED` 🚩旗舰 | 拉黑 (餐厅,时段) → 移到有空时段；不行拉黑餐厅换店 |
| | `DIETARY_VIOLATION` | 拉黑餐厅 → 换饮食兼容 |
| | `CAPACITY_REQUIREMENT_VIOLATED` | 拉黑餐厅 → 换大桌/包间（**5 人这档 utility 只预筛 ≥6，真会触发**） |
| | `SOCIAL_CONTEXT_MISMATCH`（HARD/BLOCKING） | 按 `node_ref` **定向**拉黑肇事那一个实体，换适配者 |
| | `OPENING_HOURS_VIOLATION` | 餐厅：移时段/换店；POI：只能拉黑换（其 start_time 非 ILS 变量） |
| | `MEAL_TIME_UNREASONABLE` | 移到饭点槽 / 换茶点类 cuisine / 落地板（受 `dining_slots` 池约束） |
| **soft·不进重搜** | `DISTANCE_EXCEEDED`、`SOCIAL_CONTEXT_MISMATCH`（SOFT/POOR） | 只叙事；已被 utility 的 time_score / ctx_match 消化 |
| **落 rule 地板（ILS 修不动）** | `INVARIANT_BROKEN`、`NODES_INCOMPLETE`、`TIMELINE_INCONSISTENT`（可解析 + 对齐）、`TOOL_RESPONSE_INCONSISTENCY`、`HOP_INFEASIBLE` | 结构/幻觉码，ILS 搜索变量不参与；HOP 与组装器共享 `lookup_hop` 近乎不触发 |
| | `DURATION_OUT_OF_RANGE` | 弱杠杆：超长且因通勤 → 试拉黑最远实体换近点；否则地板 |
| | `AGE_DURATION_MISMATCH` | 决策 2（α）后**组装期已预防**；万一触发 → 地板（非 retry 可修） |

演示价值集中在能被 ILS 产物**真正触发**的 6 个：旗舰满座 / 5 人桌 / 非饭点 / 营业时间 / 社交 BLOCKING / 放宽后 dietary。结构码与已预筛码近乎不触发——**不为不会触发的码写华丽修复**。

## 边界（不在本 ADR）

- **ILS 元启发式是否「表演」**（greedy_init 已对 ≤75 点穷举取最优，扰动+局部搜索理论上找不出更优）——本 ADR 选择「把 ILS 修成真的用」而非「删梯级」，是否进一步精简算法归架构审查候选 #8，单独立项。
- **时长模型重做**（按年龄加/删填充节点凑满时长）——超范围；α 只在组装器加 cap + 冲突取舍。
- **`dining_slots` 池扩展**（移时段类修复的射程受这 3-5 个槽锁死；三码 RESTAURANT_FULL/MEAL_TIME/OPENING_HOURS 共用同一池）——接受现状，无解落地板；扩池超范围。
- **前端 `ViolationCode` 副本同步**（陈旧）——跟进项。

## 备选与拒因

- **年龄只留 critic、ILS 路径落地板（β）**——拒：带幼童/高龄的长时段几乎必落地板，「尊重孩子体力」的人性化亮点做没。
- **年龄只归 LLM 主路径（γ）**——拒：ILS/rule 路径也该人性化；概念完整性上时长是组装器决定的，年龄 cap 就该在组装器，不能因路径不同放弃约束。
- **删掉 ILS 梯级（候选 #8 的简化路）**——拒：路演北极星是「展示差异化亮点」，critic-to-ILS 闭环修复是可对评委讲的核心，删了亮点没了。
- **soft 码也进重搜**——拒：hard/soft 划分（hard=可行域闸门、soft=目标罚项）；距离升 hard 与 grounding「稀疏放宽距离」对冲成 thrash（ADR-0008 已定距离留 soft）。
- **逐码写 ILS handler**——拒：结构/派生码统一出口落地板，符合 ADR-0008 X-2「不过度教 ILS 修每种」；只给「能被真正触发」的码写实修复。
- **保留 `_RULE_ASSEMBLER_ADAPTER` 现状只做最小 critic swap**——拒：ILS 装饰不除，adapter/路由/映射全是给没接传动轴的轮子打蜡（「能跑但没做对」）。

## 子步拆分（实现期 · 本 ADR 未动代码 · 各步 TDD 全套绿）

依赖序：**C-1 →（C-2 ‖ C-3）→ C-4 → C-5**。旗舰亮点要真能演，靠 **C-1 + C-3 + C-4** 三步一起到位。

- **C-1 组装器真用 ILS candidate**〔地基，解锁一切〕：修生产 + 测试两处 adapter；测试钉「ILS 选的点/餐厅/时段真出现在产物里」。
- **C-2 年龄进组装器（α）**：assembler 读 `age_caps.py` 夹 POI 时长；冲突→优先年龄；四方共读 SoT（含 ILS `_resolve_age_cap` 第三份副本并入）。
- **C-3 critic 接入 + 映射表**：`plan_hybrid` 吃统一 `validate`；`(code, severity)` 路由替 `_classify_violation`；填 `node_ref` 定向拉黑；删 `ils_score_critic`。
- **C-4 收旗舰三坑**：retry 后重校验并 gate（修「带病当成功」）；黑名单键改组装后 `start_time`；端到端测试钉「满座→17:30」真收敛。〔可并入 C-3，看粒度〕
- **C-5 删死代码**：`run_blueprint_critics` 及 blueprint critics（无生产调用者）+ 更新测试。

## 落地状态

✅ **已落地**（决策 2026-07-01 · grill-with-docs 会话 · C-1→C-5 分步 test-first 落地）。
C-1 assembler 真组装候选（f35fccb）/ C-2 年龄进组装器 α + 餐厅时刻自洽 乙（973566d）/
C-3 接统一 critic + ViolationCode 映射表 + 删 ils_score_critic（3957037）/
C-4 有界修复闭环 + gate（abd09c8）/ C-5 删死 blueprint critic 层（fb29dcf）。
全套 950 passed / 1 skipped；旗舰「满座→改期」端到端逐步收敛（16:30→17:00→17:30）。
