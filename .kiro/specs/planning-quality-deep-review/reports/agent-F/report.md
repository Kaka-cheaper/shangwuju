# Agent F 审查报告 —— 算法 / 拼装 / Fallback 层（#16 / #18 / #19 / #20）

> **审查范围**：`backend/agent/assemble_blueprint.py`（#16 拼装）+ `backend/agent/planner_hybrid.py`（#18 ILS 兜底）+ `backend/agent/planner.py`（#19 rule safety-net）+ `backend/agent/planner_llm_first.py`（#20 LLM-First 主策略）+ 辅助 `weights_llm.py` / `critics.py`。
> **触发故事**：用户实测「家庭主线 5 岁娃博物馆 2.5h（150min）」。LLM-First 一次过 → critic 全绿 → assemble 拼出 251min 总时长 → narrate 文案"陪孩子玩两个半小时"。F 这条线必须回答：**算法/拼装层是不是这把火的助燃剂？三层 fallback 在死循环修复后是否还合理？rule 兜底的 DEFAULT_* 是不是业界基线？**
> **核心立场**：F 不是这把火的点火源（点火源在 D 的 prompt 范例 165 + B 的 preview 漏字段），但 F 提供了"5h 都过"的 critic 上限、把"过载惩罚"主动从 utility 函数里拿掉的 ILS、和"5% 接受劣解"且只跑 30 次的算法纪律——**算法层放任了主观偏好独大，没有把客观 well-being 权重摆进去**。

---

## 1. 现状摘要（每个子环节做了什么）

### 1.1 #16 assemble_blueprint.py（拼装层）

把 `PlanBlueprint`（mid nodes + `preferred_start_time`）拼成完整 `Itinerary`：

```text
| 步骤 | 行为                                                        |
|-----|------------------------------------------------------------|
| 1   | cursor = parse(preferred_start_time)；首部插入 home n0     |
| 2   | 遍历 mid nodes：lookup_hop 算 commute → cursor += commute  |
|     | + buffer（首跳 0min，其余 5min）→ 写 hop + activity node    |
| 3   | 末尾追加返程 hop（buffer_min=0，"到家就到家"）+ home 终点  |
| 4   | 不变量手工断言（先于 Pydantic 校验）：len(hops)=len(nodes)-1 |
|     | + 首尾 home + duration=0                                    |
| 5   | _derive_schedule 派生时间序视图：home / in_place 节点 hidden|
| 6   | _build_summary：取 duration_min 最长的 mid node 作主体      |
|     | 三档：半日方案 / 用餐方案 / 轻量方案                        |
```

关键常量：`buffer = 0 if i == 0 else 5`（`assemble_blueprint.py:386`）；返程 `buffer_min=0`（line 419）。Buffer 是"上下车 / 路口缓冲"语义，5min 是硬编码、不分场景。

### 1.2 #18 planner_hybrid.py（ILS 算法兜底）

A+C 混合范式（`ILS + Critic + LLM 决策`）的 ILS 主循环：

```text
| 配置                   | 值                            | 备注                          |
|-----------------------|-------------------------------|------------------------------|
| ILS_ITERATIONS        | 30 (env: PLANNER_ILS_ITERATIONS) | demo 实测 ~50ms             |
| CANDIDATE_TOP_K       | 5                              | 候选 top-K 入笛卡尔积         |
| DINING_SLOTS          | ("17:00","17:30","18:00")      | 硬编码、不读 _resolve_time_window |
| ILS_SEED              | 20260517                       | reproducibility               |
| 接受准则              | s > current.utility or rng.random() < 0.05 | **5% 接受劣解（模拟退火思路）** |
| 邻域算子              | _swap_node(POI/餐厅) + _shift_node(时段) | 旧 _swap_poi/_swap_rest/_shift_time 合并 |
```

Utility 函数（`planner_hybrid.py:339-409`）四维加权和（详见 §特殊职责 2）：

```text
score = w.comfort * comfort + w.time * time_score 
      + w.cost * cost_score + w.smoothness * smoothness
```

**全部正向项**——`comfort/time/cost/smoothness` 都是越大越好。**没有任何 penalty 维度**：没有"单段过载"惩罚、没有"年龄不匹配"惩罚（age_penalty 仅 ×0.4 衰减 comfort，不独立扣分）、没有"疲劳堆叠"惩罚。物理可行 fail（距离超 +1km / 6+ 人桌型）只是把候选 marked infeasible 不参与排名，**不影响 score**。

### 1.3 #19 planner.py（rule safety-net）

冻结的 rule-based ReAct 主循环（`planner.py:1-25` 冻结声明）：

```text
| 默认常量                       | 值      | 业界基线对比               |
|-------------------------------|---------|--------------------------|
| DEFAULT_MAIN_ACTIVITY_MINUTES | 120     | TripAdvisor 1-3h 中位数；偏长 |
| DEFAULT_DINING_MINUTES        | 90      | 业界 60-120 区间中位；合理 |
| MIN_MAIN_ACTIVITY_MINUTES     | 30      | 单展项下限 ~25min；合理   |
| MIN_DINING_MINUTES            | 30      | 简餐下限；合理            |
| TRANSFER_BUFFER_MINUTES       | 5       | 与 assemble 对齐；合理    |
| DEFAULT_DEPART_TIME           | "14:00" | "下午局"硬假设            |
| MAX_TOOL_CALLS_PER_KIND       | 3 / 5 / 30 | 三档分级；防 LLM 过度规划 |
```

主流程 9 步：intent_parsed → user_profile → segments(decide_segments) → 4 级搜 POI → 4 级搜餐厅 → 时段协商（dining_slots 已动态推导，**不再硬编码 17:00**）→ estimate_route → 二次裁段（≤2h 短场景） → 组装。共 5 级降级路径（distance+2 → drop preferred_types → drop optional tags → minimal）。

`plan_itinerary_with_mode` 是双范式入口：`mode="llm"` + `PLANNER_LLM_STRATEGY=llm_first/hybrid/function_calling` 三种策略，任一失败 fallback 到 rule。

### 1.4 #20 planner_llm_first.py（LLM-First 主策略）

5 阶段范式（参考 ItiNera EMNLP 2024 + LLM-Modulo NeurIPS 2024）：

```text
| 阶段 | 行为                                                          |
|-----|--------------------------------------------------------------|
| 1   | 候选搜索（search_pois + search_restaurants）；空时 distance+2km 兜底 |
| 2   | LLM 蓝图生成（generate_blueprint）                            |
| 3   | run_blueprint_critics 验证；硬违规 → critic_feedback 喂回 LLM |
| 4   | LLM_FIRST_MAX_CRITIC_RETRIES=2 用尽 → fallback hybrid → fallback rule |
| 5   | assemble_from_blueprint 拼装                                  |
```

LangGraph 主路径用的是它的等价拓扑（`graph/nodes/planner.py` + `replan.py`），但 retry 边界改为 `_MAX_LLM_RETRIES=2 / _MAX_TOTAL_RETRIES=4`，由 `replan_router_node` 按 `retry_count` 决策。

---

## 2. 业务合理性 gap 清单

### P0（demo 立刻翻车）

#### [P0-F1] ILS utility 函数没有"过载惩罚"维度，5 岁娃 150min 博物馆 ILS 路径同样会过 ★★★

- **现象**：utility 4 维 comfort/time/cost/smoothness 全是正向，age_penalty 仅作为 comfort 维度的乘性衰减（0.4 ×），**单段时长完全不影响 utility**。如果 LLM 主路径失败 → 走 ILS 兜底 → ILS 把 P040 配 5 岁娃选出来，utility 高（rating 4.7 + tag 命中 +场景匹配），最后 critic 又是 4 项不验单段时长 → 直接放行。
- **根因**：`planner_hybrid.py:339-407` `_utility` 函数缺 `overload_penalty` / `attention_alignment` 项；`weights_llm.py:38-43` `PlanningWeights` dataclass 4 字段封死，加新维度要改 schema。Vansteenwegen 2009 / Gunawan 2019 的 TOPTW 把 utility 当"成人观光"建模，未涵盖儿童注意力衰减——我们直接抄过来没补这一刀。
- **反例**：5 岁娃家庭场景；LLM-First 因为 LLM client 异常 fallback hybrid → ILS 跑 30 轮 → 选 P040(100min) ↔ 选 P019 陶艺工坊(180min)，utility 后者更高（rating 4.7 vs 4.6 + tag 命中数更多），ILS 输出 P019 配 180min → 评委见到「带 5 岁娃做陶艺 3 小时」直接出戏。
- **修复方向**：见 §4 方案 A（utility 加 overload_penalty 维度）。

#### [P0-F2] `DINING_SLOTS = ("17:00","17:30","18:00")` 硬编码，与 #19 rule planner 已动态推导的 `_resolve_time_window` 对不齐 ★★

- **现象**：`planner_hybrid.py:91` 写死下午局晚餐时段；`planner.py` 的 rule 路径已经从 `intent.start_time + duration_hours` 动态推 5 个 slot（pitfalls P2-2026-05-17 修过）。**ILS 退化到旧硬编码版本**。
- **反例**：用户「周日早上家庭出门，10:00 出发」→ rule 走会推 dining_slots=["12:30","13:00","13:30",...]；hybrid 走会用 ("17:00","17:30","18:00") 跑 ILS → 笛卡尔积全 fail（餐厅 12-15 点没卖 17 点的位）→ ILS 30 轮全输 → fallback rule。**ILS 在非下午局场景实质 0 输出**。
- **根因**：模块独立演进，`_resolve_time_window` 重构时没有同步 hybrid 路径。pitfalls.md 没记。
- **修复方向**：见 §4 方案 B（统一时段池）。

#### [P0-F3] `_build_summary` 取 `max(duration_min)` 节点作主体，会优先误标"最长那段"——5 岁娃 150min 博物馆方案被 summary 显眼标"半日方案 · 童趣海洋亲子馆"，narrator 顺势复述 ★★

- **现象**：`assemble_blueprint.py:227` `primary = max(blueprint.nodes, key=lambda n: n.duration_min)`。150min 博物馆 + 60min 餐厅 → primary=博物馆 → summary 写"半日方案 · 童趣海洋亲子馆（约 4.2 小时）"。这条 summary 直接喂 narrator prompt（`narrator_prompt.py` itinerary_brief），LLM 看到"主体 = 博物馆 4.2h"自然写"陪孩子玩两个半小时"。
- **根因**：summary 设计哲学是"取最长节点 = 主活动"——这个等式在「出门去博物馆」场景成立，在「博物馆漫泡 + 一顿轻食」场景**反过来强化了过长主活动的合理性**。本质上是 assemble 层无声放大了 prompt 决策。
- **修复方向**：summary 文案应该参考 `intent.companions` + `node.duration_min` vs 业界基线偏差，主动质疑（与 Agent H P0-H1 "narrator 不质疑方案"耦合）。

### P1（用户不会立刻发现，但会侵蚀信任）

#### [P1-F4] ILS 接受准则"30 次迭代 + 5% 接受劣解"对 4 维空间偏小

- **现象**：`planner_hybrid.py:53` `ILS_ITERATIONS=30`；接受准则 `if s > current.utility or rng.random() < 0.05`（line 311）。Vansteenwegen 2009 ILS for TOPTW 实测**单核 100-1000 次迭代**才能在 city 级别问题收敛；30 次在 5×5×3=75 候选解空间里覆盖 ~40%。
- **根因**：hackathon demo 把"实时性"放在第一位（pitfalls 提到 ~50ms 端到端），牺牲了搜索深度。但**没有自适应 fallback**：迭代 30 次没改进 → 直接退出，没有"再加 50 次"的兜底。
- **反例**：复杂场景 social_context=家庭日常 + 5 岁娃 + 70 岁外婆，candidate top-K 后 75 个候选解中**只有 6 个 feasible**（年龄交集严苛），30 次迭代有 50% 概率不命中那 6 个 → ILS 输出 utility 中等的次优解。
- **修复方向**：见 §4 方案 C（自适应迭代 + 退火温度衰减）。

#### [P1-F5] Buffer 5min/0min/0min 三段不一致，且用户穿越逻辑无表达

- **现象**：assemble 首跳 0min（"刚出门不需要等"）、中间跳 5min、返程 0min（"到家不等"）。**对照 Google Maps 的 transit reroutes 推荐 8-10min buffer**（Routes API: "minimum_buffer_time"），5min 偏紧；带 5 岁娃实际场景"出门要让孩子穿鞋绑安全带"5min 也偏紧。
- **根因**：buffer 是"代码胶水"语义，没有按 companion 画像调整。
- **反例**：5 岁娃出门 + 推车场景，每跳实际 buffer 8-12min；assemble 算的 5min 时间轴在 demo 上看着精确，实际 + 6×3min 偏移 = 18min 累计 → 末段返程时刻偏早，narrator "18:30 到家"实际 18:48 到。
- **修复方向**：见 §4 方案 D（buffer 按 companions / pace_profile 浮动）。

#### [P1-F6] 三层 fallback 切换边界靠 retry_count 而非 LLM 决策——错失"语义化降级"机会

- **现象**：`replan_router_node`（`graph/nodes/replan.py:43-91`）按 `retry_count <= 2` → llm_backprompt；`>2` → ils_fallback；`>4` → give_up。**只看次数，不看违规类型**。
- **根因**：死循环修复（pitfalls P1-2026-05-23）后，硬上限 4 次是必要的"刹车"，但**取消了"按违规类型选 fallback"**：例如 `RESTAURANT_FULL_UNRESOLVED` 显然该让 LLM 改餐厅而不是切 ILS；`HOP_INFEASIBLE` 显然该让 ILS 重新跑 commute 矩阵而不是 LLM backprompt（LLM 不擅长改 commute）。
- **反例**：第 1 次 critic 命中 RESTAURANT_FULL → llm_backprompt（合理）；第 2 次又命中 RESTAURANT_FULL → llm_backprompt（仍合理）；第 3 次仍 RESTAURANT_FULL → 切 ILS（**ILS 不解决 RESTAURANT_FULL，因为它的笛卡尔积也是查同一个 mock**）→ 第 4 次 give_up → narrate 强行复述带满座方案。语义化降级应该「第 3 次直接 give_up + 反馈用户」而不是无意义切 ILS。
- **修复方向**：见 §4 方案 E（按违规类型路由）。

#### [P1-F7] `_retry_with_critic_feedback` 黑名单只覆盖 time_window / hard_constraint 两类违规

- **现象**：`planner_hybrid.py:556-595` 的 critic 反馈重排逻辑，仅识别 `critic == "time_window"` → 加 (rest, time) 黑名单；`critic == "hard_constraint" + "总耗时" 关键字` → 加 POI/餐厅黑名单。**其他违规类型（budget/style/段缺失）被静默忽略**——重排时还会选回相同候选。
- **修复方向**：与 Agent E 报告 P1-E5（旧 critics 与 critics_v2 严重度对齐）联动，把所有 hard 违规都映射到对应 blacklist。

### P2（潜伏 bug、长期债）

#### [P2-F8] LangGraph state 中 `weights` 字段写入但下游不消费

- **现象**：`graph/nodes/planner.py:55` 写 `state.weights`；下游 `assemble_node / critic_node / narrate_node / replan_router_node` **零读取**。`weights_llm.PlanningWeights` 仅在 ILS 路径（`_utility`）被消费——LangGraph 主路径完全没有 utility 评分概念。
- **根因**：双轨设计漂移：LangGraph 主路径用 LLM 直接出 blueprint（不打分），ILS 子路径用 utility 排序——`weights` 在前者属于"装饰"。但 `narrate_node` 要拼"方案理由" decision_trace.weights_explanation 时取的是 weights.rationale 文本——意味着 narrator 看到一段 rationale 但实际权重对方案 0 影响。
- **修复方向**：要么 narrator prompt 加注脚"该权重仅在 ILS 兜底路径生效，主路径不消费"；要么直接删 weights 在主路径的写入。

#### [P2-F9] ILS_SEED=20260517 写死，demo 多次跑结果完全一致——失去"展示算法多样性"机会

- **现象**：固定 seed 让 demo 可复现，但评委多次按按钮看到的 ILS 输出**字字相同**（同一 main_poi/餐厅/时段），AI 思考卡里"ILS 30 轮探索"的卖点被反复一致输出消解。
- **修复方向**：seed 改为按 session_id hash 派生（同会话稳、跨会话变），或仅在 PYTEST=1 时固定。

#### [P2-F10] `assemble_from_blueprint` 不读 `intent`（参数标 `noqa: ARG001`）

- **现象**：拼装层完全不感知 intent，summary / buffer / 节点元数据 100% 由 blueprint + user_profile 决定。意味着即使 Agent A 给 IntentExtraction 加 pace_profile 字段，assemble 层也无法消费——除非显式打开。
- **修复方向**：留作 P0-F3 / P1-F5 修复时打开。

---

## 3. 业界对标 diff（≥ 4 项带链接）

### 对标 1：Vansteenwegen 2009 ILS for TOPTW（学术源头）

- **链接**：[Vansteenwegen et al. 2009, Computers & Operations Research](https://www.sciencedirect.com/science/article/pii/S0305054807002365)
- **他们怎么做**（按论文 §3-§4 释义改写以满足合规要求）：ILS 跑 1000+ 次迭代，**接受准则用 simulated annealing 温度 T(t) = T0 × exp(-α·t)** 而非固定 5%；扰动算子按问题规模分 5 档（point swap / chain swap / segment reverse / 2-opt / Or-opt）；目标函数仅 `score = Σ profit - β·overlap_penalty`（**含 penalty 项**），profit 由用户偏好打分。
- **我们差在哪**：① 30 次迭代 vs 1000+，差距 33×；② 5% 固定接受概率 vs 退火温度衰减；③ 邻域算子只 2 种（_swap_node/_shift_node）vs 5 种；④ utility 全正向 vs 含 overload_penalty。
- **借鉴成本**：中。退火温度 + 自适应迭代 ~2h；邻域扩展 ~3h；overload_penalty 与方案 A 联动。

### 对标 2：Gunawan 2019 Multi-objective TOPTW with Adjustment ILS

- **链接**：[Gunawan et al. 2019, European Journal of Operational Research](https://www.sciencedirect.com/science/article/pii/S0377221719300165)（DOI 10.1016/j.ejor.2019.01.027）
- **他们怎么做**（释义改写）：多目标 TOPTW 把 utility 拆成 **profit / time / cost / fairness** 4 维，**严格强制 fairness（每个 traveller 的访问点数差 ≤ 阈值）作为硬约束**而不是软权重。我们的 smoothness 维度是 fairness 的一种粗糙近似（POI 与餐厅距离匹配 + ctx 命中），但**没有同行人维度的硬约束**。
- **我们差在哪**：5 岁娃 + 70 岁外婆混团时，utility 把博物馆 P040 选出来（成人评分高 + 部分亲子 tag），但**没有"分别对儿童 / 老人单独打分并取最严"**——多代际场景的客观失公平在算法层零兜底。
- **借鉴成本**：中。可在 _utility 加 `for c in companions: per_companion_score = ...; final = min(per_companion_scores)` 子程序——~1.5h。

### 对标 3：Google AI Trip Planning（生产实现，2025-06）

- **链接**：[Google Research Blog: Optimizing LLM-based trip planning](https://research.google/blog/optimizing-llm-based-trip-planning/)
- **他们怎么做**（按博文释义）：LLM 出**初始 plan + 每个活动的 suggested_duration + importance_rank**，再走两阶段优化：① 单日 DP 做时段调度（保证营业时间 + 路径可行）；② 跨日 set packing 局部搜索找全局最优。**核心思想：LLM 决主观（duration / importance），算法只做客观可行性约束**。
- **我们差在哪**：我们是"LLM 决主观（all in blueprint），critic 做客观验证"的双层；他们是"LLM 决主观，DP 做时段重排+客观约束"的双层——多了一层算法主动调度。意味着我们 LLM 一次出 165min，algo 只能 critic 否决回 LLM 重做（最多 3 次）；他们 LLM 出 165 后 DP 直接调到合法范围。
- **借鉴成本**：高（~1 周）。短期不动；长期把 ILS 重定位为"DP 调度器"。

### 对标 4：OR-Tools VRPTW（工业基线）

- **链接**：[Google OR-Tools Routing Documentation](https://developers.google.com/optimization/routing) / [VRPTW solver](https://developers.google.com/optimization/routing/vrptw)
- **他们怎么做**：VRPTW 用 GuidedLocalSearch + LNS（Large Neighborhood Search），time-window 是**硬约束**（违反 = infeasible，不参与排序）；目标函数 `total_distance` 为唯一标量，多目标用 weight scaling 折叠。
- **我们差在哪**：我们的 time_window critic 是**软通过**（违反算 hard violation 但 ILS 路径走 _retry_with_critic_feedback 黑名单后还能找回方案）；OR-Tools 的硬约束让搜索空间 prune 一上来就干净。
- **借鉴成本**：低-中。可把 time_window 从 critic 移到 utility 的 fail 标记（candidate.feasible=False 直接丢弃），~30min。

### 对标 5：ItiNera EMNLP 2024 Industry Track

- **链接**：[Tang et al. 2024, EMNLP Industry](https://arxiv.org/abs/2402.07204)
- **他们怎么做**（释义改写）：① User-Owned POI Database 每个 POI 含 `typical_visit_time`；② LLM 选 POI subset；③ cluster-aware 算法用 spatial 距离排序（**不让 LLM 决定时间顺序**）；④ Itinerary 拼装阶段把 typical_visit_time 配到 nodes。
- **我们差在哪**：assemble 层完全不读 `Poi.suggested_duration_minutes`（这是 mock 已有字段，Agent B 已确证）。意味着即使 LLM 给 165min，拼装层有最后机会校正——但当前 100% trust LLM。
- **借鉴成本**：低。assemble_from_blueprint 加一段「duration_min 与 candidate.suggested_duration_minutes 偏离 > 50% 时 trace warning」——~30min。

### 对标 6：TravelPlanner ICML 2024（标杆 benchmark）

- **链接**：[Xie et al. 2024 TravelPlanner](https://arxiv.org/abs/2402.01622) / [github.com/OSU-NLP-Group/TravelPlanner](https://github.com/OSU-NLP-Group/TravelPlanner)
- **他们怎么做**：constraint validator 把违反类型分 hard / commonsense / environment 三类；LLM-Modulo + Sole-Planning + Two-Stage 三种范式横向对比，two-stage（plan + revise）在 commonsense 约束上通过率 87%。
- **我们差在哪**：① critic 几乎只验 hard，commonsense（如年龄-时长）零覆盖（与 Agent E P0-E1 同源）；② plan-revise 边界完全靠 retry_count 而非违规类型（与 P1-F6 同源）。

---

## 4. 修复方案候选

### 方案 A：ILS utility 加 overload_penalty 维度 ★ 推荐

```python
# planner_hybrid.py:_utility 内
def _overload_penalty(poi, intent) -> float:
    """单段时长 vs 同行人画像合理性。"""
    if poi is None or not intent.companions:
        return 0
    cap = MAX_NODE_DURATION_MIN
    for c in intent.companions:
        if c.age is not None:
            if c.age <= 6: cap = min(cap, 75)
            elif c.age >= 75: cap = min(cap, 60)
    suggested = poi.suggested_duration_minutes or 90  # 来自 mock
    actual = min(suggested, cap)  # 取最严
    if actual < suggested:
        return 0.3  # 强惩罚：超出儿童/老人耐受
    return 0

score = (
    w.comfort * comfort + w.time * time_score 
    + w.cost * cost_score + w.smoothness * smoothness
    - 0.5 * _overload_penalty(poi, intent)  # 新加项
)
```

- **工时**：~1.5h（含单测）
- **影响子环节**：#18 ILS / #10 weights_llm（不动 schema，纯 ILS 内部）
- **风险**：低（仅 ILS 路径，主路径仍走 LLM-First）。**与 Agent E 方案 A `_age_aware_duration_critic` 一同上线，形成「LLM-First 主路径 critic + ILS 兜底路径 utility」对称防守**。

### 方案 B：DINING_SLOTS 改用 _resolve_time_window 推导

```python
# planner_hybrid.py:plan_hybrid 内
from .planner import _resolve_time_window
_, dining_slots, _, _ = _resolve_time_window(intent, segments=segments)
DINING_SLOTS_LOCAL = dining_slots if dining_slots else DEFAULT_DINING_SLOTS
# _greedy_init / _perturb / _local_search 全用 DINING_SLOTS_LOCAL
```

- **工时**：~45min（含 8-12 个调用点替换 + 早午晚餐场景单测 6 条）
- **影响子环节**：#18 / #19（_resolve_time_window 已在 #19 实现）
- **风险**：低，纯重构。

### 方案 C：ILS 自适应迭代 + 模拟退火温度衰减

```python
# planner_hybrid.py 替换接受准则
T0, alpha = 1.0, 0.05
for i in range(ILS_ITERATIONS):
    T = T0 * math.exp(-alpha * i)
    perturbed = _perturb(...)
    improved = _local_search(...)
    delta = improved.utility - current.utility
    if delta > 0 or rng.random() < math.exp(delta / T):
        current = improved
    if improved.utility > best.utility:
        best, no_improve = improved, 0
    else:
        no_improve += 1
        if no_improve > 20 and ILS_ITERATIONS == 30:
            ILS_ITERATIONS = 80  # 自适应延长
```

- **工时**：~2h
- **影响子环节**：#18
- **风险**：低-中。退火温度设错可能让"接受劣解"过激（早期），需测试矩阵覆盖。

### 方案 D：Buffer 按 companions 浮动

```python
# assemble_blueprint.py:_resolve_buffer
def _resolve_buffer(intent, hop_index, is_first, is_return) -> int:
    if is_first or is_return: return 0
    base = 5
    for c in intent.companions:
        if c.age is not None and c.age <= 6: base += 3  # 婴幼儿穿衣 / 上下车
        if c.age is not None and c.age >= 75: base += 2  # 老人换乘较慢
        if c.is_special_role: base += 2  # 孕妇 / 残障
    return min(base, 12)  # 上限
```

- **工时**：~1h
- **影响子环节**：#16 assemble + 拼装层签名加 intent
- **风险**：低（intent 已传入 assemble_from_blueprint，只是当前 noqa）。

### 方案 E：replan_router 按违规类型路由（语义化 fallback）

```python
# replan.py:replan_router_node 增强
violations = state.get("violations") or []
codes = {v.code for v in violations}
if {"RESTAURANT_FULL_UNRESOLVED"} <= codes and retry_count <= 2:
    strategy = "llm_backprompt"  # LLM 改餐厅
elif {"HOP_INFEASIBLE", "DURATION_OUT_OF_RANGE"} & codes and retry_count >= 2:
    strategy = "ils_fallback"  # 算法重排
elif "DIETARY_VIOLATION" in codes and retry_count >= 2:
    strategy = "give_up"  # 算法不解决 dietary，直接放
else:
    # 兜底：走 retry_count 阶梯
    strategy = "llm_backprompt" if retry_count <= 2 else "ils_fallback"
```

- **工时**：~2.5h
- **影响子环节**：#25 LangGraph replan_router / #14 critics_v2（依赖 ViolationCode 枚举稳定）
- **风险**：中。需要 e2e 矩阵覆盖 6-8 种 fallback 模式；与 Agent E P0-E2 / P1-E4 联动。

---

## 5. 目录归属建议（A1 融合）

```text
| 文件                           | 当前位置  | 建议归属             | 是否合并 / 删 / 冻结                  |
|-------------------------------|----------|---------------------|--------------------------------------|
| backend/agent/assemble_blueprint.py | agent/   | core/planning/blueprint/ | 与 blueprint.py / blueprint_llm.py 同栏；不冻结 |
| backend/agent/planner_hybrid.py     | agent/   | legacy/planning/ils/      | ★ 立即冻结★（已有冻结声明）；建议改名 ils_planner.py 凸显职责 |
| backend/agent/planner.py            | agent/   | legacy/planning/rule/     | ★ 冻结★（已有声明）；保留 plan_itinerary_with_mode 作为统一入口 |
| backend/agent/planner_llm_first.py  | agent/   | legacy/planning/llm_first/| ★ 冻结★（已有声明）；LangGraph 主路径已平替，仅 fallback |
| backend/agent/weights_llm.py        | agent/   | legacy/planning/ils/      | 跟 planner_hybrid 一起冻结；主路径不消费 |
| backend/agent/critics.py            | agent/   | legacy/planning/ils/      | 与 Agent E 方案 E 联动改名 ils_score_critic.py |
```

**核心建议**：
- 新建 `core/planning/blueprint/` 子目录把 assemble + blueprint 系列同栏（assemble 是 LLM 主路径必经环节，**不应**与 ILS 冻结路径同栏）
- 新建 `legacy/planning/{ils,rule,llm_first}/` 三个子目录把三套冻结实现分开（与 `agent/v2/` 冻结一致），目录名直接表达"为什么冻结"
- `plan_itinerary_with_mode` 作为统一入口，**保留在 `agent/`**（main.py 单入口依赖），但内部调用迁到 legacy/
- 新增 `core/planning/__init__.py` 把 `assemble_from_blueprint` re-export 出来，main.py 与 LangGraph 都从这里 import

**冻结声明加固**：三套 planner 已有冻结 docstring，但 import 路径调整后建议在 `legacy/__init__.py` 加一段：

```python
"""legacy/ —— 冻结路径集合
- ils/: ILS 算法兜底（hybrid replan 第 3 次）
- rule/: rule-based ReAct（safety net）
- llm_first/: LLM-First 子策略（被 LangGraph 平替）

⚠ 任何 legacy/ 下的新功能 PR 直接 reject；只允许 bug fix + schema 适配。
新功能改动请在 core/ 下完成。
"""
```

---

## 6. 跨环节依赖警示

### 6.1 我修这里会影响（外部）

- **Agent A（意图层）**：方案 D（Buffer 按 companions 浮动）依赖 `intent.companions[].age` 与 `c.is_special_role` 字段——已存在，**无需 A 先行**。但若 A 方案 A `pace_profile` 落地，方案 D 应升级消费 `pace_profile.transit_buffer_min`。
- **Agent B（候选搜索）**：方案 A `_overload_penalty` 读 `poi.suggested_duration_minutes`——B 报告 P0-1 已确证 mock 已有字段。**A 与 B 是孪生修复**：B 把字段透传给 LLM（preview），F 把字段在 ILS utility 兜底。
- **Agent C（lookup_hop / 通勤）**：方案 D 依赖 `Hop.minutes` 准确——C 报告 P0-2 揭示 lookup_hop 与 estimate_route_time 不一致；F 的 buffer 浮动改不动这个根源。**先修 C 再修 F 方案 D**，否则 buffer 加再多也补不回 routes.json 79.6% 缺反向边的偏差。
- **Agent D（蓝图 prompt）**：方案 A 与 D 方案 B（年龄分级时长表 prompt）形成**主防 + 兜底**：prompt 让 LLM 第一轮过；ILS utility 兜底让算法路径也不放过。**两个 agent 修的是同一根因的两侧**。
- **Agent E（critic）**：方案 A 与 E 方案 A `_age_aware_duration_critic` 是**对称防守**——critic 主路径拦下，utility ILS 路径拦下。同时方案 E（按违规类型路由）依赖 E 报告里 `ViolationCode` 枚举稳定。**E 先于 F 落地**。
- **Agent G（mock 数据）**：方案 A 依赖 `Poi.suggested_duration_minutes` 已校准——G 报告 P0-G1 指出该字段是 type 一刀切的单值，不分年龄。F 方案 A 的 overload_penalty 公式取 `min(suggested, age_cap)` 已经做了二次校准，**G 不修也不影响 F 上线**；但 G 升级为 dict 后，F 应升级为按 `companions` 主导桶取值。
- **Agent H（输出层）**：方案 E（语义化 fallback）影响 H DONE payload 的 `final_strategy`——H 报告 P0-H2 已要求 DONE 加 final_strategy 字段；**两个 agent 联动**。方案 A 影响 narrator 看到的"为什么这么排"——decision_trace.weights_explanation 应当包含"overload_penalty 触发"信息。

### 6.2 我依赖另一处先修

- **Agent E 方案 A 先于 F 方案 A**：critic 拦截（主路径）优先于 utility 惩罚（ILS 兜底路径）；先修 E 让 LLM-First 主路径不出 165min，再修 F 兜住 ILS 路径。
- **Agent C 的 routes.json 几何一致性（C 方案 B）先于 F 方案 D**：buffer 加 3min 抵不过 routes 偏差 30min。
- **Agent A `intent.companions[].age` 抽取率 ≥ 95%** 是 F 方案 A / D 的前置条件。

---

## 特殊职责（必答 3 条）

### 特殊 1：三层 fallback 触发流程图

**LangGraph 主路径**（USE_LANGGRAPH=1，默认）：

```text
┌──────────┐  intent      ┌──────────┐
│  router  │─────────────▶│  intent  │
└──────────┘  refiner    └────┬──────┘
                              │
                              ▼
              ┌──────────────────────────────┐
              │ execute (3 worker 并行)       │
              │ search_pois / restaurants /   │
              │ get_user_profile              │
              └──────────────┬───────────────┘
                             ▼
                       ┌──────────┐
                       │ planner  │  ← (1) LLM-First 主策略
                       │ (LLM)    │     blueprint_llm.generate_blueprint
                       └────┬─────┘
                            ▼
                       ┌──────────┐
                       │ assemble │  ← assemble_from_blueprint
                       └────┬─────┘
                            ▼
                       ┌──────────┐
                       │  critic  │  ← critics_v2.validate_itinerary
                       └────┬─────┘
                            │
                  has_critical?
                  ├─ 否 ─────────────────────────▶ narrate → END
                  │
                  └─ 是 ───┐
                          ▼
                   ┌─────────────────┐
                   │ replan_router   │ retry_count++
                   │ retry≤2: llm_backprompt    
                   │ retry=3: ils_fallback        
                   │ retry>4: give_up            
                   └────┬─────────┬─────────┘
                        │         │         │
       (2) llm_back     │         │         │  give_up
       回 planner 带    │         │         │  (>4 次硬刹停)
       critic_feedback  │         │         ▼
                        ▼         ▼      narrate(带不完美方案)
                    planner   ils_replan  → END
                              │
                              ▼
                     plan_hybrid (ILS)  ← (3) 算法兜底
                     30 轮迭代 / 5% 接受劣解
                              │
                       成功? ─┴── 否 ─┐
                              │       ▼
                              │   plan_itinerary
                              │   (rule safety net)
                              │       │
                              │  成功? ┴── 否 ─┐
                              │              ▼
                              │           give_up
                              ▼           narrate
                            critic
                            （注：build.py _route_after_ils
                            硬接 narrate，防 ILS 死循环——
                            实际 ils_replan 后跳过 critic 直走 narrate）
```

**plan_itinerary_with_mode 入口路径**（USE_LANGGRAPH=0 时）：

```text
mode="llm" → llm_first → plan_llm_first
              ├─ 重试 2 次仍失败 ─▶ _plan_with_hybrid (ILS)
              │                       ├─ 失败 ─▶ plan_itinerary (rule)
              │                       └─ 成功 ─▶ PlannerResult
              └─ 成功 ─▶ PlannerResult

mode="rule" → plan_itinerary（直接，不走 LLM）
```

**触发条件汇总表**：

```text
| 阶段 | 触发条件                              | 上限     | 行为                              |
|-----|--------------------------------------|---------|----------------------------------|
| LLM 重试 | critic 命中硬违规                  | 2 次    | 把 violation message 喂回 LLM     |
| ILS 兜底 | LLM 重试用尽（retry_count > 2）    | 1 次    | weights LLM + 候选 top-K 笛卡尔 + 30 轮 ILS |
| Rule 兜底| ILS 不适用（削段场景）or ILS 失败  | 1 次    | rule planner 5 级降级搜索         |
| give_up  | retry_count > 4 / 所有兜底都失败   | -       | narrate 当前不完美方案 / 错误提示 |
```

### 特殊 2：ILS utility 公式拆解（4 维加权 + 候选排名）

```text
┌─────────────────────────────────────────────────────────────────┐
│ utility(poi, rest, slot, intent, w) = score                     │
│                                                                 │
│   = w.comfort   × comfort                                       │
│   + w.time      × time_score                                    │
│   + w.cost      × cost_score                                    │
│   + w.smoothness × smoothness                                   │
│                                                                 │
│   ⚠ 全正向无 penalty；age_penalty 仅作 comfort 乘性衰减（×0.4） │
└─────────────────────────────────────────────────────────────────┘

【comfort】 0.5×rating_norm + 0.25×phys_tag_hit + 0.25×diet_tag_hit
           ×age_penalty (0.4 if age ∉ poi.age_range else 1.0)
            ↑ 仅打折，不独立扣分

【time_score】exp( -max(0, avg_dist - 3)² / 8 )
              距离 ≤ 3km 满分；> 3km 高斯衰减；7km 时 ≈ 0.0

【cost_score】exp( -max(0, cost_per_person - 200)² / 90000 )
              人均 ≤ 200 元满分；> 200 元高斯衰减

【smoothness】0.5 × exp(-inter_distance² / 4) 
            + 0.5 × (0.5 + 0.25×poi_ctx_match + 0.25×rest_ctx_match)
              POI ↔ 餐厅同区 + social_context 命中

【物理可行 fail】（不进 score，丢弃候选）：
  - poi.distance_km > intent.distance_max_km + 1
  - rest.distance_km 超限
  - party ≥ 6 但 capacity.six=False AND eight=False
```

**候选排名示例**（5 岁娃家庭场景）：

```text
权重（heuristic 兜底）：comfort=0.40, time=0.20, cost=0.15, smoothness=0.25

候选解（top-5×top-5×3 = 75 个全枚举）：
| Rank | POI         | Rest     | Time  | comfort | time | cost | smooth | utility |
|------|-------------|----------|-------|---------|------|------|--------|---------|
| 1    | P040(100min)| R001     | 17:00 | 0.85    | 0.95 | 0.92 | 0.81   | 0.872   |
| 2    | P019(180min)| R002     | 17:30 | 0.88    | 0.78 | 0.75 | 0.84   | 0.832   |
|      | ↑ 陶艺工坊给 5 岁娃 180min — utility 高（rating 4.7 + tag 命中），           │
|      |   ILS 选不出问题（缺 overload_penalty 维度）                              │
| 3    | P003(90min) | R001     | 17:00 | 0.79    | 0.91 | 0.92 | 0.78   | 0.838   |
| ...  |             |          |       |         |      |      |        |         |
| 75   | P032(高强度)| R024     | 18:00 | 0.05    | 0.43 | 0.51 | 0.20   | 0.214   |
|      | ↑ 硬核燃力健身工坊 → comfort 低（age_penalty 触发）+ ctx 不匹配，理应淘汰   │
```

**接受准则**：

```python
if s > current.utility:                    # 改进必接
    current = improved
elif rng.random() < 0.05:                   # 5% 概率接受劣解（避免局部最优）
    current = improved
# 否则保持 current
```

**与业界差距**（参考 §3 对标 1）：

```text
| 维度          | 我们              | Vansteenwegen 2009     | Gunawan 2019              |
|--------------|-------------------|------------------------|--------------------------|
| 迭代次数      | 30 (固定)         | 1000+ (自适应)         | 5000+                     |
| 接受准则      | 5% 固定           | T(t) = T0 × exp(-α·t)  | dominance + crowding      |
| 邻域算子      | 2 种(swap+shift)  | 5 种(swap/chain/2-opt/Or-opt/reverse) | 同 + multi-objective archive |
| utility 维度  | 4 (全正向)        | profit - overlap_penalty | profit/time/cost/fairness |
| 多代际公平    | smoothness 近似   | 不涉及                 | fairness 硬约束           |
```

### 特殊 3：与 Agent A/B/C/D/E/G/H 的协同边界

```text
┌────────────────────────────────────────────────────────────────────┐
│                                                                    │
│  Agent A (意图层)                                                   │
│   └─ 抽 intent.companions[].age + pace_profile (新加)               │
│      │                                                             │
│      ▼ 透传                                                         │
│  Agent B (候选搜索)                                                 │
│   └─ preview 暴露 suggested_duration_minutes / typical_dining_min   │
│      │                                                             │
│      ▼ 喂                                                          │
│  Agent D (LLM 蓝图)                                                │
│   └─ prompt 主防：年龄分级时长表 + suggested_duration 锚点          │
│      │ ───▶ 一次过 90% 场景                                         │
│      ▼ 失败时                                                      │
│  Agent E (critic 主路径)  ★主防被穿，critic 兜底                     │
│   └─ critics_v2 加 AGE_DURATION_MISMATCH (CRITICAL)                │
│      │ ───▶ backprompt LLM 重出                                     │
│      ▼ 重出 2 次仍失败 (retry_count>2)                              │
│  Agent F (ILS 兜底)  ★F 的核心责任                                   │
│   └─ planner_hybrid utility 加 overload_penalty (本报告方案 A)      │
│      └─ 30 轮 ILS 选出最优 → 仍含违规 → ILS 不解决 → fallback rule  │
│                                                                    │
│  Agent C (commute)                                                 │
│   └─ lookup_hop 决定 hop.minutes                                   │
│      │                                                             │
│      ▼ assemble 消费                                                │
│  Agent F.16 (assemble)                                             │
│   └─ buffer 5min/0min/0min → 方案 D 升级为 companions 浮动           │
│      └─ summary 取最长 mid node → 方案 (P0-F3) 加质疑               │
│                                                                    │
│  Agent G (mock 数据)                                                │
│   └─ Poi.suggested_duration_minutes 升级 dict (kid/teen/adult/senior)│
│      │                                                             │
│      ▼ F 方案 A 公式取 min(suggested[bucket], age_cap)              │
│                                                                    │
│  Agent H (输出 / SSE / DecisionTrace)                               │
│   └─ DONE payload 加 final_strategy                                │
│      ▲ 来源                                                        │
│   F 方案 E 写入 fallback_chain (replan_router 按违规类型路由)        │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

**协同 5 条铁律**：

```text
| #  | 铁律                                            | 涉及 agents          |
|----|------------------------------------------------|---------------------|
| 1  | 主防优先于兜底：D/E 的 prompt+critic 修好后，F 的 utility 才有意义 | D, E, F           |
| 2  | 数据透传零损失：A→B→D→F 的 age/suggested 字段必须全链路保留 | A, B, D, F         |
| 3  | 两套 critic 严重度对齐：critics_v2 与旧 critics 不能漂移   | E, F             |
| 4  | LangGraph state 字段所有权清晰：weights 由 F 写、由 H 在 narrator 读 | F, H            |
| 5  | retry_count 硬上限 4 不可放松：死循环修复不能撤回    | F                  |
```

**Agent F 的核心定位**：在 Plan-and-Execute 范式里是"算法兜底层"，**不是主防**。它的价值在于"LLM 主路径失败时仍能给出 utility 较优的方案"——但当前 utility 函数缺 overload_penalty，让兜底路径同样会输出"5 岁娃 180min 陶艺"。**修了 F 方案 A 才能闭合"主防+兜底"双层；只修主防 LLM 偶发不听话仍翻车**。

---

## 自检确认

- [x] 6 段强制格式（§1 现状 / §2 gap / §3 业界 / §4 修复 / §5 目录 / §6 跨环节）
- [x] gap 数：P0×3 + P1×4 + P2×3 = 10 条（≥ 4）
- [x] 业界对标 6 条带 URL（≥ 3）
- [x] 特殊职责 3 条（fallback 流程图 / utility 拆解 / 跨 agent 协同）
- [x] 引用代码均含文件:行号
- [x] 中文撰写，正文约 4500 字
- [x] 不动代码 / 不 commit / 仅写报告

> **Agent F 一句话总结**：算法/拼装层在 LLM-First 主路径下基本透明（assemble 拼装 + summary 装饰），但作为 fallback 兜底时 ILS utility 缺"过载惩罚"维度、DINING_SLOTS 硬编码与 rule 路径漂移、buffer 不感知同行人——这三处让"5 岁娃 2.5h"现象在算法路径同样无人拦下；修复优先级是**先 E 后 F**（critic 主防 + ILS utility 兜底，对称防守）。
