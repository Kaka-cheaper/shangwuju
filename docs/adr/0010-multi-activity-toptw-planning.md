# ADR-0010 · 规划从「1 活动 + 1 餐」升为「按用户需求涌现的多活动 TOPTW」

- **状态**：Accepted（2026-07-01 · solve-it-right + grill-with-docs 会话 · 架构审查候选 #8）
- **范围**：规划层的**搜索层**（`ils_planner` 的候选模型/构造/搜索、`rule_planner` 的 `decide_nodes` 使用、`age_caps`/`_resolve_time_window` 的时长来源）+ critic 的 `check_nodes_incomplete`。承接 ADR-0007（单栈 + rule 地板）/ ADR-0008（统一 critic）/ ADR-0009（ILS 真实化 + critic-to-solver 闭环）。

## 背景（现状诊断，已代码核实）

架构审查候选 #8「ILS 是否表演」深挖后确认，**并非 ILS 算法错，而是问题被错误地削小了**：

- **ILS 元启发式 provably + 实证 inert**：`_greedy_init` 穷举 `poi_top × rest_top × dining_slots`（≤75-125 点）取全局 utility 最优；30 轮扰动 + `_local_search` 只在同一网格的**子集**里找，`if s > best_score`（ils_planner.py 388 附近）**不可达**。实测 5 场景 × 30 轮 = 150 次迭代，「发现更优解」触发 **0 次**。
- **根因是 1+1 削减，不是算法**：`decide_nodes` 只返 `[主活动]`/`[用餐]`/`[主活动,用餐]`（node_decider.py:146-164），**永不给多个 POI**；`CandidatePlan` 单 `main_poi` + 单 `restaurant`。用户记得的「5 段」是**旧段模型**（出发/主活动/转场/用餐/返回），node_decider.py:73 明写「≡ (主活动,用餐) 二节点」——那 5 段其实就是 1 活动 + 1 饭。搜索空间因此塌成平凡网格，把为**大组合空间**设计的 ILS 逼成了摆设。
- **周边系统本就 N-ready（已验证）**：`assemble_from_blueprint` 遍历 `blueprint.nodes`（list）；critic 遍历 `itinerary.nodes`；`execute_finalize` `for n in restaurant_nodes / for n in poi_nodes` 逐个下单；前端 `ItineraryCard.tsx` 时间轴/orders 均 `.map` 遍历渲染（唯一 1+1 残留是摘要文案的 `firstRestaurant`，小事）。**只有搜索层被卡在 1+1**——重构范围因此收敛到搜索层。

**定位（ROI，明确）**：主用户输出走 **LLM 主路径**（本就多活动、靠判断力出品味）；**ILS 是异常处理/兜底 + critic-to-solver 闭环的 demo 亮点**。故本重构的价值**不在**「主输出变丰满」（LLM 已做），而在：① 修 #8 的代码诚实性（删 provably-inert 的假 ILS）；② 让闭环亮点有真 ILS 撑；③ **让兜底方案结构性地好**（多活动 + 有节奏，而非单薄 1+1）。范围据此校准为「好兜底 + 好亮点」，**不追 LLM 级品味**（见决策 8）。

## 问题定性（solve-it-right）

> 这个问题本质上是：**带时间窗 + 节奏留白（slack）的团队定向问题（TOPTW）**——在「在外时长」预算内，从候选活动池（POI ∪ 餐厅，同质）里选一个**子集 + 顺序 + 时刻**，每活动有时间窗（餐厅=饭点 / POI=营业时间）与自然时长，使「这组人会爱上」最大化。活动数量、要不要餐厅、几点吃，**全随 intent 涌现，零硬编码**。

## prior-art

- **TOPTW + ILS**：[Vansteenwegen et al. 2009 · Iterated local search for the TOPTW]（**代码原本就引用的那篇**）+ [Gunawan et al. 2019 · Adjustment ILS for multi-objective TOPTW]。标准解法：**插入式构造**（逐个插访问、验后续时间窗仍成立，MaxShift = 每访问可容忍的最大延迟）+ **shake / 扰动**逃局部最优，有界迭代。应用于 [时间依赖 TOP · 旅游路线规划]。→ E 把问题还原成这些论文本来要解的问题，ILS 名副其实。
- **反过度打包 / 节奏**：纯 OP「最大化总分」倾向填满预算；旅游规划文献 + 注意力/体力基线（age_caps 已引 Smithsonian SEEC）支持「slack/留白是真价值、由客群决定」。
- **约束满足 vs 通用求解器**（Fowler/Evans Specification）：域规则表达为**约束（数据）**、求解器保持通用。

## 决策

### 1. 模型：均质活动池 + 涌现组成，不特权任何类型
POI、餐厅都是「一次访问（visit）」，各带 (类型、自然时长、时间窗、utility)。**没有「主活动/用餐」特权划分，没有硬编码的必到餐厅**。`decide_nodes`「决定要哪些 kind」对 ILS 路径**作废**（搜索来决定组成）。

### 2. 三层解耦架构
- **① 约束 + utility 构建层（域知识住这）**：为每个候选活动算 **时间窗**（餐厅默认=饭点 11:00-13:30/17:00-20:00/21:00+；POI=营业时间）+ **utility**。**用户明确需求在这层覆盖默认**（「6 点吃饭」→ 该餐厅窗收窄到 ~6 点）。
- **② 通用 TOPTW 求解器（ILS 住这，零域知识）**：只吃「一堆活动（时长+窗+utility）+ 总预算」，求子集+顺序+时刻。它**不知道也不关心哪个是餐厅**——只认「这活动窗是 [X]」。加新活动类型只需在 ① 给窗+utility，② 一行不改。
- **③ critic 兜底**：组装后拿同一套约束（meal_time/opening_hours/age/dietary…）复检。

### 3. 锚定谱：pinned → soft-anchored → emergent（统一「饭的中心性」与「明确需求」）
- **硬钉 pinned**：用户明说的（「去 XX 馆」「6 点吃饭」「川菜」）→ 必进方案，窗收窄到指定值。
- **软锚 anchored**：上下文强信号（商务接待/纪念日 → 饭近乎必有；跨饭点 + 有 dietary → 想吃）→ 先定，防被高分 POI 挤成配角。
- **涌现 emergent**：可有可无，utility 说了算。
- 构造**两段**：先放锚点，再围着它们涌现填充。**锚不锚由用户语境定 → 不是硬编码「必须有饭」，兑现「以用户需求为准」。**

### 4. 时长 / 预算 / 节奏模型（心脏；回改 C-2）
- **每活动用自然时长**（POI `suggested_duration`、餐厅 `typical_dining_min`），再夹年龄 cap。**替代旧的「`_resolve_time_window` 按段切 duration_hours」**——`duration_hours` **角色变为「在外时长的总预算（上限）」**。
- **C-2 回改**：组装器 cap 输入从 `min(按段时长, age_cap)` → `min(suggested_duration, age_cap)`；`age_caps.py` 成为「组装器/critic/grounding/ILS penalty/**搜索**」共读的时长真相源。C-2 的 cap 机制 + `not_before_start` 全留用，只换输入——是演进非白做。
- **slack（留白）是一等公民**：行程 = 活动 + 通勤 + **slack**，一起填满在外时长。**slack 由节奏决定**（幼童/高龄/独处 → 多 slack、活动更少；朋友热闹 → 少 slack、活动更多）。节奏**不是新字段**，从 companions/age + social_context 推（与驱动 age cap 同一批信号）。`not_before_start` 的餐前空闲已是 slack 的一种表示。
- **区间填充**：用「活动 + slack」把在外时长填到 **≥ 下限 lo**（防 `check_duration` 因太短判不合格 → 甩地板），在 [lo, hi] 间按节奏停。**数量在时长区间约束内涌现，非无约束涌现。**
- **反过度打包**：自然时长本身限制数量（大块）；age cap 会反噬（幼童短活动 → 塞更多 → 更累），由 slack 化解（幼童多留白 = 少而精 + 有休息）。

### 5. 窗感知调度器（真正的硬核）
「插入一个活动」= **给这堆带窗+时长的活动找「顺序 + 各自开始时刻」使 都在窗内、不重叠、通勤够、总时长 ≤ 预算**——是小型 CSP + 迷你 TSP，**不是按游标 append**（否则餐厅会被排到饭点窗外）。甲 的实现：**插入位搜索（保持既有顺序、试各插入位；非全排列枚举）+ `not_before_start` 钉窗 + 重跑时间轴验可行**（≤5 活动瞬间，**不需要 MaxShift**——那是大路线的事，归乙）。**可行性从 `_utility` 剥离**（原 `fail_detail` 失效）→ 归调度器；utility 退成纯打分。

两条调度纪律（不守则静默砸下游）：
- **餐厅时刻 snap 到预约槽网格**（17:00/17:30/…半点粒度）：`check_demo_restaurant_full` 靠 `node.start_time` 精确匹配 `reservation_slots` 才触发，排在 17:23 会让满座检查**静默失明**（旗舰「满座→改期」哑火）、且与 execute 的 `_ceil_to_half_hour` 预约时刻错位（重蹈 C-2 修过的自洽 bug）。
- **slack 摆放策略（甲·简单版）**：间隙优先落在 **带窗节点前**（餐前休息，`not_before_start` 天然表达）与 **路线尾**（早点从容收尾）；不在活动中间乱插。前端把空档渲染成「自由休息」块归前端跟进（见「边界」）。

### 6. utility 拆分（甲：additive + 轻量多样性）
从「(POI,餐厅) 单元组联合打分」拆成：**每活动基分**（comfort/cost/语义/社交匹配）+ **路线级**（通勤紧凑 + **同类别扣分**的轻量多样性）。插入用**边际分**挑。LLM 权重（comfort/time/cost/smoothness）照旧套新结构。

### 7. shake 存废由实测定（内化 #8 教训，防换马甲重演）
**先只做贪心插入构造；再实测「贪心 vs 贪心+shake」在 S1-S8 上的结构差异——shake 明显让某场景更对才留，说不出哪更对就砍**，诚实叫「插入式贪心 + critic 修复闭环」。**注意**：节奏 slack 把路线压回 2-4 活动的小规模，greedy 很可能已够好——**E 让 ILS「有可能」名副其实，不等于「自动」**，必须验。

### 8. UX 是目标，TOPTW 是机器；兜底不追品味
- **目标函数不是「utility 最大」，是「这组人会爱上这份行程」**；utility 是被 UX 因子（节奏/留白/饭的中心性/明确需求/叙事弧）塑形的代理。
- **职责分工**：LLM 主路径靠判断力产出品味/精细叙事弧（UX 的灵魂）；**ILS 是兜底 + critic 闭环的 demo 亮点，只负责「结构性 UX 差异化」**（数量/节奏对、该有饭时有饭、明确需求被满足），**不追品味**（追不上、且撑爆甲）。两条路**共享「好行程」的定义（本 UX 规格），各用各的机制达成**。

### 9. 其余定案
- **rule 地板保持极简 1+1 兜底**（D2 = 可靠性优先，只在 LLM+ILS 都失败时被打到；decide_nodes 留给地板）。
- **critic `check_nodes_incomplete`** 从「按 decide_nodes 要求某几种 kind」改「**≥1 活动（非空）**」——涌现组成的逻辑必然。
- **明确需求冲突** → 检测并**告诉用户**哪个满足不了，不静默丢弃（异常出口）。

### 10. 业务规则细化（「好兜底」标准，非「完美」）
- **节奏聚合**：混合同行取**最受限者**（幼童/高龄 → relaxed，与 age cap 取最严一致）；无同行人 → 中等默认。
- **软锚触发（定死，复用 `_DINING_FOCUSED_CONTEXTS`）**：饭被软锚 iff ① `social_context ∈ dining-focused 集`（商务/纪念日/家宴），或 ② 出行窗完整跨某饭点窗 **且** 有 dietary 信号；否则涌现。
- **稀缺兜底**：填不到下限时**宁可短而好，不塞次优凑数**。**连带修订 ADR-0008 tier 表**：`check_duration` **拆向**——超长保持 HARD（挤爆用户时间是硬伤），**不足降为 SOFT/advisory**（「比你要的短了些，附近合适的选择有限」——告知而非毙掉；否则「短而好」活不过 critic gate、必落 1+1 地板，本条即死条款）。
- **钱（软）**：utility 加软项——路线总花费超 `default_budget` 扣分（兜底 cost-aware，不做硬预算约束）。
- **顺序软偏好（flow）**：可行顺序间，饭点窗把饭推中后段 + 「活跃点靠前、舒缓点靠后」当轻 tiebreak，让被展示的 ILS 方案不那么机械（精细叙事弧仍归 LLM）。

### 11. 首要 UX 铁律：绝不默默忽略用户的明确请求
用户任何明确请求，只要不能（完整）满足，**必须告知原因 + 给出路**，绝不静默丢弃/截断。经 **advisory 通道**（planner → narration；区别于 critic 的 hard 违规——这不是缺陷，是「限制/建议」）呈现。三类触发：
- **路线规模（三层，都不静默）**：① **时间可行性（硬·物理）**——预算塞不下就不塞，告知「时间不够，要延长时长 / 去掉一个吗」；② **产品软限（半天 2-4 个才不赶）**——超了**告知会赶，但用户坚持且塞得下则照给**（以用户需求为准）；③ **延迟安全上限（≤5-6）**——设得足够高、平时不该撞到的兜底。**决不出现「已有 5 个、用户要加、反馈重规划仍 5 个、一言不发」。**
- **明确需求冲突**（pinned 间无解，如「6 点在 40km 外吃饭 + 5 点看展」）：告知哪个满足不了 + 建议取舍。
- **过预算 / 无匹配候选**：告知并建议放宽。

（实现连带：需一个轻量 **advisory / 未满足请求** 通道从 planner 流到 narration；本 ADR 记为待建，归 D-7。）

## 边界（不在本 ADR）

- **乙（完整 TOPTW）**：MaxShift 精确调度 + 多算子 shake + 边际/多样性精细 utility——**留待路线真的变大/需要时**，甲 是其地基、接口不变、可增量长上去。
- **精细叙事弧 / 品味**：归 LLM 主路径；ILS 只做粗顺序（饭点窗把饭推中后段）。
- **前端多活动打磨**：时间轴/orders 已验证 `.map` 遍历可画 N 节点；残留两件跟进——摘要文案 `firstRestaurant` 只提第一家餐厅（两顿饭时漏述）、**空档渲染成「自由休息」块**（否则 slack 看起来像排程漏洞）。
- **narration 覆盖多活动**：3 个活动要讲清「为什么这几个、为什么这个顺序」，否则多活动反而更让人困惑——归 narrate 层跟进（与 LLM prompt 对齐同批）。
- **intent 层 pin 抽取**（schema + 解析 prompt，「我要去XX馆/6 点吃饭」→ 结构化 pins）——D-7 的跨层依赖，单独立项。
- **LLM 主路径 prompt 与本 UX 规格对齐**（鼓励合理数量/节奏）——单独跟进。

## 备选与拒因

- **A｜删掉 inert ILS、保留 1+1**：拒——治标不治本，rule/ILS 方案永远单薄（半天 1 活动+1 饭），且回避了「问题被错削」的根。
- **乙｜一步到位完整 TOPTW**：拒（缓）——半日 1-4 活动的规模，MaxShift/多算子/边际 utility 边际价值低、风险高；甲 拿到全部核心价值。
- **硬编码「必须有饭」**：拒——违背「饭随需求」；改用**条件性锚定**（语境强信号才软锚）。
- **纯涌现、无锚定**：拒——漏了「用户明说的必须满足」(UX-2) 和「商务局饭是主角」(UX-1)。
- **纯 TOPTW 分数最大化**：拒——过度打包、无视节奏；改用 **slack + 区间填充**。
- **ILS 追 LLM 级品味**：拒——启发式追不上判断力，且撑爆甲；分工：ILS 做结构、LLM 做品味。

## 验收（UX 驱动，同时是 ILS 价值的判据）

**跑 S1-S8，断言输出的结构性差异**（可测代理「因组而异」）：
- S1 家庭+娃 / S4 老人：活动数少 + slack 高（老人最高）；有娃能吃/软食的饭。
- S2 朋友热闹：活动更多、slack 低。
- S3 情侣 / S7 独处：不排满 / 1 活动 + 大留白。
- S6 商务：餐厅是最高 utility 的节点（饭为主角）。
- S8 生日全家：饭 + 蛋糕（extra_service）+ 一个记忆点。
- 明确需求场景：pinned 活动/时间/品类**一定**出现。
- **绝不默默忽略**：造「路线已满仍要加 / pinned 冲突 / 超预算 / 无匹配候选」场景，断言产出**带 advisory 告知**（不静默截断）。
**shake 是否保留：看它能否让上述某场景的结构输出「明显更对」——说不出即砍。**

## 子步实施计划（实现期 · 本 ADR 未动代码 · 各步 TDD 全套绿）

依赖序（每步可独立验）：

- **D-1** 约束+utility 构建层：utility 拆成「每活动基分 + 路线级紧凑/多样性」；候选池带时间窗（餐厅=饭点/POI=营业，intent 覆盖）。**含候选池扩容/分层**：单口味搜索 top-5 会给出同质池（5 个展馆），多样性罚无米下锅——为路线构造提高 TOP_K 或按类别分层取样，这直接决定「兜底效果好」。
- **D-2** 窗感知调度器：给定活动集 → 插入位搜索 + `not_before_start` 钉窗 + 可行性 → 返回排程或 None。**含餐厅槽网格 snap + slack 摆放策略**（见决策 5）。可孤立测。
- **D-3** 时长/预算/节奏 + C-2 回改：每活动自然时长夹年龄（`min(suggested, cap)`）；slack 由 companions/social_context 推；区间 [lo,hi] 填充；**`check_duration` 拆向（不足→SOFT，超长保持 HARD，修订 ADR-0008 tier 表）**。
- **D-4** 贪心插入构造（锚定两段：pinned/软锚先放 → emergent 边际填）：替换 `_greedy_init`/`CandidatePlan`→路线。**无 shake**。**这是 big-bang 步**：`CandidatePlan` 被替换即波及 C-1/C-3/C-4 的既有测试，须在本步一并迁移或显式适配。
- **D-5** **修复闭环迁移到路线模型**（旗舰续命，缺了 D-4 落地即断）：C-3/C-4 的黑名单/重搜从三元组升级到路线——blame 沿用 field_path→节点定位；黑名单形状改为「按活动实体 /（餐厅,槽）」；`_search_best_avoiding` 改为对路线做**移除/替换/改时刻**的修复算子；retry gate + 有界轮次语义原样保留。端到端重验「满座→改期」在多活动路线上仍收敛。
- **D-6** shake + **实测决策**：加扰动，跑 S1-S8 结构差异，greedy vs greedy+shake，据实定去留。
  **✅ 已实测，结论：砍（不接线）。** S1-S8 结构验收落成 `backend/tests/test_s1_s8_structural.py`
  （13 个测试，通用不变量×8 + 5 条场景差异方向性断言，全部基于真实 mock 候选池 +
  StubLLMClient，无 monkeypatch）。shake 实现于 `backend/agent/planning/planners/
  route_shake.py`（标准 ILS shake：随机移除 1 个已选活动 → 复用 `route_builder.
  _greedy_fill_emergent` 贪心重填 → 严格更优才接受，K=20，seed=42），**刻意不接入
  `build_route`/`plan_hybrid`**。实测脚本 `backend/scripts/measure_shake.py` 跑
  8 场景 + 3 组 duration/distance 变体（11 组），结果：9/11 组 20 轮内 0 次找到更优解
  （score_delta=0.0000）；仅 2/11（S5、S7+duration=[3,5]）各有 1 轮被接受，
  score 提升 +0.0044 / +0.0032（相对 base score ~2.2-2.6，<0.2%），且都只是同规模
  下的单点候选替换（3 活动 → 3 活动，非结构性差异），不构成"肉眼可辨更对的结构
  输出"。诊断确认 shake 找不到改善的根因不是算法/参数缺陷，而是候选池本身（如 S2
  "朋友热闹"候选普遍 90-150min 大块活动，5h 预算贪心选 2 个后穷举全部剩余候选+
  20 轮扰动均无法再塞入第 3 个）——ADR 决策 4"自然时长本身限制数量"在 D-4 单独
  已经把路线压到小规模，local shake 的邻域里没有可爬的坡。按 ADR 决策 7 原话
  "说不出哪更对就砍"执行：`route_shake.py`/`measure_shake.py` 保留在仓库作实验
  存档（不删代码，便于后续路线规模变大、`build_route` 候选池扩容后重新验证结论
  是否仍成立），但不接入生产路径，诚实叫"插入式贪心 + critic 修复闭环"。
- **D-7** 明确需求 pinning + 冲突异常出口（UX-2）+ **advisory 通道**（planner→narration，承载「路线满/请求冲突/超预算/无匹配」等未满足请求的告知，兑现「绝不默默忽略」铁律）。**范围声明**：本步做「planner 接受结构化 pins + advisory 产出」；`IntentExtraction` 无 pin 字段、prompt 也不抽取——**intent 层的 pin 抽取（schema + 解析 prompt）是跨层依赖，单独立项**，未落地前 pinning 仅可单测（手工构造 pins）。
- **D-8** 收尾：ILS 弃 decide_nodes；critic `nodes_incomplete`→「≥1 活动」；死代码清理。

## 落地状态

⏳ **部分实现**（决策 2026-07-01 · solve-it-right + grill-with-docs + 两轮全面红队复审 · 按 D-1→D-8 分步 test-first；UX 验收 = S1-S8 结构差异 + 不静默；证据锚点待各步 commit 回填）。
**D-6（S1-S8 结构验收 + shake 存废实测）已完成**：结论"实测砍除"（shake 不接线，见上方 D-6 条目的完整数据与判读）。
**D-7（pinning + advisory 通道）已完成**：`PinSpec`（schemas/pin.py，kind+target_id，时间钉留 intent 层立项时一并设计）+ `Advisory` 5 码（schemas/advisory.py）；`plan_hybrid` 接受 pinned、修复闭环 pin 默认保护/万不得已牺牲必记账；advisory 附着「最终交付方案」（成员资格过滤）、同码合并成单句防 narrator 截断自吞；链路 planner→state→narrate→AGENT_NARRATION.payload.messages（`{kind,code,text}`，按 ADR-0011 决策 5 统一消息面形状）；1073 passed 零回归。范围外遗留：intent 层 pin 抽取（单独立项）、rule 地板不产 advisory。D-8（decide_nodes 收尾 + 死代码清理）仍待实现。
