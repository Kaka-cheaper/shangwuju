# Agent H · 反向对照审查报告（找平庸点 + 业界已被超越的伪创新 + 评委挑战点）

> 审查身份：「技术创新评分」联合审查 sub-agent **H**——专攻**攻击面 / 伪创新 / 业界已被超越**。
> 与 E（多约束拆解）/ F（并行 Tool）/ G（容错链路）三个建设性维度严格互补——不重复证据，专找「评委 grep 代码就能戳穿」的薄弱处与「营销话术 vs 真创新」的差距。
> 审查纪律：仅基于 `backend/agent/` + `backend/tools/` + `mock_data/` + `演示场景集.md` + `pitfalls.md` + `algorithm-redesign/research/joint-review` + 8 份 sub-agent 范式调研 + `execution-quality-review` 联合审查 + `agent-D-attack-surface`；**不读 E/F/G 报告**避免污染立场。
> 工时盒：≤25 分钟。字数目标：≥5000 中文字。
> 报告位置：`.kiro/specs/innovation-review/agent-H-anti-pattern/report.md`

---

## 一、一句话结论

**项目「创新性」叙事的可信度等级 = 中等偏上**——*真创新有 3-4 个能站住脚的护城河项（critic 三层镜像 + 双层折叠可见性 + 段决策抽蓝图 + 上线伏笔三件套），但有 6-8 个被团队当作「亮点」其实是业界论文 / 商业产品已经做完的伪创新点*。评委首次端起话筒挑战「你们这个跟 GPT-4 Trip Planner 有啥区别」时，**最致命的攻击点是**：「你们的 LLM-Modulo 同构 + critic backprompt 是 Kambhampati NeurIPS'24（arxiv 2411.14484）整套方法论的 Pydantic 翻版，业界标准做法。你们 8 个 Tool + Function Calling 是 GPT-4 Trip Planner 标配。你们的真正差异化在哪里？」如果团队答不上「年龄感知 critic（OR 文献空白）+ 三层镜像 + 中文社交语义 9 选 1 + memory 副作用挂在 narrate 末尾不动 graph 拓扑」这四条具体技术差异，就会被一句话扫掉一半的「技术创新」分。

---

## 二、「这条创新真的是创新吗」清单（≥ 10 条）

> 评委如果对照 8 份范式调研报告（`algorithm-redesign/research/agent-{1-8}/`）逐条核对项目的「自夸点」，下表是最危险的 12 行——每行用 file:line 作为代码证据。

```text
| #  | 项目声称的「创新」                       | 实际工程                                                     | 业界 baseline / 同等做法                                       | 真创新等级       | 代码证据 file:line                                                            |
|----|----------------------------------------|------------------------------------------------------------|---------------------------------------------------------------|------------------|-----------------------------------------------------------------------------|
| 1  | LLM-Modulo critic backprompt 多轮迭代  | LLM 出 PlanBlueprint → critics_v2 验 → 最多 2 次 backprompt → ILS 兜底 | Kambhampati NeurIPS'24 arxiv 2411.14484 标准 GTC 范式（max_iter=10）；TravelPlanner / NaturalPlan 全在做 | **论文移植**     | `agent/planning/critic/critics_v2.py:71-186` ViolationCode 9+1；`graph/nodes/replan.py:29-30` _MAX_LLM_RETRIES=2 / _MAX_TOTAL_RETRIES=4 |
| 2  | LLM 决主观、算法决客观（双责分工）       | blueprint LLM 决段集合 + segment_decider 决段 kind + critic 验时序通勤 | LLM-Modulo arxiv 2402.01817 §3.5 论文核心论断；ITINERA EMNLP'24 范式核心 | **论文移植**     | `agent/planning/blueprint/blueprint.py:267-291`；`pitfalls.md` P1-2026-05-17 「段决策耦合反模式」     |
| 3  | LangGraph fan-out 并行 Tool 调用         | execute 节点 4 worker 并行 search_pois / search_restaurants / get_user_profile / estimate_route | LangGraph 官方教程 multi-tool / parallel_tool_calls；TravelAgent NeurIPS'24 §3.2 已实现 | **教程级**       | `agent/graph/nodes/execute.py` 4 worker；`pitfalls.md` P2-2026-05-22 「多 worker 同写 state key 默认覆盖」 |
| 4  | grounding-first 候选硬过滤              | _GROUNDING_MIN_CANDIDATES=3 / 距离容差 / 学龄前 cap=90      | Google Trip Ideas 2025-06 blog 公开 best practice；business_status 为 KG-driven | **业界 best practice 移植** | `agent/planning/planners/ils_planner.py:416-419` _GROUNDING_* 常量；`research/agent-1-google/report.md` §1.3 |
| 5  | PlanBlueprint 中间数据结构              | LLM 出 (kind, duration_min, target_id) 列表，critic 验，assemble 拼装 | ITINERA EMNLP'24 RD（pos/neg/mustsee/type 四元组）+ candidate POI 排序后塞 IG | **论文移植**     | `agent/planning/blueprint/blueprint.py`；ITINERA `model/itinera.py:134-158`            |
| 6  | 三联混合（LLM-Modulo + ItiNera + 三层 schema） | 三套范式拼装                                              | LLM-Modulo + ITINERA 已是 2024 业界主流；TravelAgent 三层 schema 是 multi-agent 标配 | **拼装级 + 1 个原创点** | `algorithm-redesign/joint-review/report.md` §7.1 自承「7+ 报告交叉印证」     |
| 7  | memory_writer 副作用回写 user_profile   | narrate_node 末尾调 persist_memory(state)                  | TravelAgent NeurIPS'24 multi-turn memory + TripGenie LLM 抽参注入既有产品页 | **架构原创点**（路径 B 不动 graph）| `agent/planning/memory_writer.py`；`AGENTS.md` §3.3.1 spec C 落地说明 |
| 8  | 动态时间分配（用餐 / 主活动 4:3 比例）   | _resolve_time_window 按段集合分配                           | Booking.com / NAVITIME 商业产品基础能力；TripGenie 也做         | **营销话术**     | `agent/planning/planners/rule_planner.py:907-980` _resolve_time_window      |
| 9  | 评委决策可见（ToolTracePanel + DecisionTraceCard） | 双层折叠 + critic 修正历史 + fallback_chain | TripGenie LUI 浮标 + NAVITIME 三候选三轴评分；商业产品 5 个范式 4 个黑盒 | **真创新（demo 维度）**     | `agent-D-attack-surface/report.md` §6.4 + `joint-review/report.md` 共识 6   |
| 10 | 中文社交语义 9 选 1 词典                | SOCIAL_CONTEXTS frozenset：家庭日常 / 老人伴助 / 闺蜜聊天 / 朋友热闹 / 情侣亲密 / 商务接待 / 同学重聚 / 独处放空 / 纪念日仪式感 | TravelPlanner / NaturalPlan / ITINERA 全是英文 + 自由 prefer；中文社交语义 enum 化是项目原创 | **真创新（中文域）** | `schemas/tags.py` SOCIAL_CONTEXTS                                            |
| 11 | 同坐标 marker 圆弧微扰防叠             | RADIUS_DEG=0.00045 ≈ 50m 视觉防叠                          | Mapbox MarkerClusterer / Google Maps cluster spread 都自带 | **业界标配补丁** | `frontend/components/MapOverlay.tsx`；`pitfalls.md` P2-2026-05-24 同坐标 marker  |
| 12 | 双 mode 切换（rule / llm_first）        | PLANNER_LLM_STRATEGY env flag                              | LangGraph 官方支持；business 项目 fallback 链是范式标配         | **教程级**       | `agent/planning/planners/llm_first_planner.py:88` _env_int("PLANNER_LLM_FIRST_RETRIES", 2) |
```

**真创新等级 ≥ 真**的只有 3 行（#7 / #9 / #10）。其余 9 行都是「业界已经做过 + 我们工程化包装」。这个比例是 **3 真 / 9 移植拼装** —— 不是抄袭，但也绝不到「行业领先」级别，**抄袭式表述（"我们独创"）会被评委当场打回**。

---

## 三、业界已被超越的伪创新清单（≥ 5 条）

> 这一节专挑「我们以为是创新但业界已经有更好做法」的反例。如果路演里讲到这些点而不带业界引用，评委一句「这不就是 X 吗」就压死。

### 3.1 grounding-first 是 Google Trip Ideas 2025-06 公开 best practice

- **我们的做法**：`agent/planning/planners/ils_planner.py:416-419` 三个 `_GROUNDING_*` 常量做候选池前置剥离
- **业界出处**：Awasthi & Zhai, Google Research blog 2025-06-06, [optimizing-llm-based-trip-planning](https://research.google/blog/optimizing-llm-based-trip-planning/) ——明确写「we start by **grounding** the initial itinerary with up-to-date opening hours and travel times」
- **差距**：Google 用 Places KG（千万级 POI）+ Search backend 实时同步；我们用 mock_data 静态 JSON。**评委查 mock_data 文件大小（< 100KB）立刻看出**
- **应对**：路演明示「grounding-first 思路对齐 Google AI Trip Ideas，但我们做的是中文社交语义 + 同行人画像维度的 grounding——这是 Google 范式没覆盖的」

### 3.2 critic backprompt 是 LLM-Modulo NeurIPS'24 的核心范式（不是我们独创）

- **我们的做法**：`critics_v2.py` 9+1 类 ViolationCode + `format_violations_for_llm` pinpoint-all 模式
- **业界出处**：Kambhampati et al., arxiv 2402.01817（ICML'24 position）+ arxiv 2411.14484（NeurIPS'24 实证）——GTC 循环（Generate-Test-Critique）max_iter=10
- **差距**：论文 ablation（[2411.14484 §5.4](https://arxiv.org/html/2411.14484v1#S5.SS4)）显示 first-only 反馈与 pinpoint-all 性能等价；我们走 pinpoint-all 是默认选择，没做 ablation
- **应对**：路演必须主动说「我们与 LLM-Modulo 是事实同构系统，不是参考论文做的——而是项目先实现，后做交叉印证发现同构」

### 3.3 动态时间分配在 Booking.com / NAVITIME 商业产品里是基础能力

- **我们的做法**：`rule_planner.py:907-980` `_resolve_time_window` 按 `decide_nodes` 输出的段集合做 4:3 / 全段 / 0 比例分配
- **业界出处**：NAVITIME（日本路径规划商业产品）三候选 + 三维评分早 5 年；Booking.com itinerary builder 已做半天 / 一天 / 多日时长适配
- **差距**：商业产品有真实路况 / 营业时间 / 用户历史画像三层数据；我们仅用 mock 时段池
- **应对**：路演不要把这条当亮点讲——*提一句「按业界标配做」即可*，把篇幅留给真创新

### 3.4 多约束拆解（同行人 + 距离 + 时长）是 TravelAgent NeurIPS'24 三层 schema 标配

- **我们的做法**：`schemas/intent.py` IntentExtraction 含 companions / distance_max_km / duration_hours / dietary_constraints / experience_tags 等字段
- **业界出处**：TravelAgent NeurIPS'24 §3.1（hard / soft / commonsense 三层）；TripGenie 携程 LLM 抽参注入既有产品页
- **差距**：TravelAgent / TripGenie 的输入 schema 维度更丰富（11 城 / 真实订单），我们的 SOCIAL_CONTEXTS 9 选 1 比业界**更窄**
- **应对**：把「年龄感知（age-aware critic）」当唯一原创点亮明（OR 文献空白，参考 `algorithm-redesign/research/agent-6-or-ttdp/report.md` §六 Q4）

### 3.5 LangGraph fan-out 4 worker 并行就是 LangChain 官方教程的 multi-tool

- **我们的做法**：`agent/graph/nodes/execute.py` 4 worker 并行调 search_pois / search_restaurants / get_user_profile / estimate_route_time
- **业界出处**：LangGraph 官方文档 [multi-agent + parallel tool calling](https://langchain-ai.github.io/langgraph/) 已是入门教程
- **差距**：LangGraph 已是 2024 主流框架，**用 LangGraph 不算创新**；我们的 add_node 显式编织拓扑（不用 prebuilt create_react_agent）反而是「降级走稳路线」
- **应对**：不要把「用了 LangGraph」当创新；要把「不动 graph 拓扑的工程纪律 + memory_writer 走路径 B 副作用」当创新

### 3.6 双 mode 切换是 fallback 链工程标配

- **我们的做法**：PLANNER_LLM_STRATEGY=llm_first / hybrid / function_calling 三档 + ENV flag
- **业界出处**：fallback 链是 production hackathon 项目工程标配；OpenAI Playground / LiteLLM 都自带
- **差距**：我们 fallback 链有 3 层（LLM 重试 → ILS → give_up），但**ILS 不解决 commute_infeasible**（pitfalls P1-2026-05-23 已记录），实际有效层数 = 2 层
- **应对**：路演讲 fallback 链必带「3 层防御 + ILS 做 commute_infeasible 不解决但有 give_up 兜底」诚实表述，不要包装成「四级防御」

---

## 四、评委可能挑战的 7 个攻击点

> 每条带评委原话 / 团队当前回应 / 真实漏洞 / 30 秒回应方案。**「真实漏洞」一栏是攻击的杀招**。

```text
| #  | 评委原话                                                | 团队当前回应（路演大纲）                                | 真实漏洞                                              | 30 秒回应方案                                         |
|----|---------------------------------------------------------|-----------------------------------------------------|-----------------------------------------------------|-----------------------------------------------------|
| 1  | 「你们这个跟 GPT-4 直接 Function Calling 有啥区别？」      | 「ReAct 单一 Agent + 8 Tool + 双 mode 切换」           | GPT-4 Trip Planner 早就是 8 Tool 范式；ReAct 是 Yao 2022 老论文。**真差异不在 Tool 数量，在 critic 三层镜像 + 中文社交语义 9 选 1**| 「Function Calling 只是接入方式。真差异是 critics_v2 11 类违规 + LLM 主路径与 ILS 兜底镜像 + 5 岁娃 75min 这条 OR 文献空白的 age-aware critic——这个 Function Calling 拿不到」 |
| 2  | 「你们的 critic 跟 LLM-Modulo 论文 Bank 有啥区别？」      | 「我们与 LLM-Modulo 同构」                              | LLM-Modulo arxiv 2402.01817 / 2411.14484 是 2024 标准范式；同构 ≠ 创新。**真差异是 critic 三层镜像（blueprint / critics_v2 / ILS utility）三同源公式** | 「LLM-Modulo 是论文级方法论，我们是工程级实现 + 业务规则增强。真差异有三：① 11 类违规码（论文 4 类）；② 主防 + 兜底 + 算法目标函数三处镜像同公式（论文一处）；③ pinpoint-all 反馈含 expected_range（论文只有 binary）」 |
| 3  | 「LangGraph fan-out 不就是官方教程吗？」                  | 「我们用 LangGraph 主架构」                             | 用 LangGraph 不算创新；任何 hackathon 都能用。**真差异是 add_node 显式编织 + memory_writer 路径 B 不动 graph 拓扑** | 「不只是 LangGraph，关键是不用 prebuilt 而是 add_node 显式编织 11 节点；spec C memory_writer 走路径 B 副作用挂在 narrate 末尾，零改 graph/build.py 拓扑——这是工程纪律不是教程」 |
| 4  | 「PlanBlueprint 跟 ITINERA 的 cluster 有啥区别？」         | 「LLM 决主观、算法决客观分工」                         | ITINERA EMNLP'24 已是 2024 工业级范式；分工概念不是创新。**真差异是 ITINERA 输出 POI 序列、我们输出段集合（kind/duration/target_id 三元组）** | 「ITINERA 输出 POI 序列然后 LLM 写文案，我们输出 PlanBlueprint 段集合（kind+duration+target_id）然后算法验时序 + 通勤；ITINERA 没有段时长决策这层，我们的蓝图维度更细，对应的 critic 维度也更多」 |
| 5  | 「memory_writer 跟简单 dict 持久化有啥区别？」            | 「TravelAgent 三层 schema 借鉴」                        | dict 持久化 demo 项目人人会写；TravelAgent NeurIPS'24 已经做完三层 schema。**真差异是副作用挂位（narrate 末尾，路径 B），不动 graph/build.py 拓扑** | 「dict 持久化是数据层；我们的差异是副作用挂位——挂在 narrate_node 末尾（Phase 路径 B），不动 graph 拓扑；ConversationRepository Protocol 已是抽象层，可一行 env 切 Redis；TravelAgent 论文是研究系统，我们是工程系统」 |
| 6  | 「mock_data 写得这么细但接真 API 不就废了吗？」          | 「三层抽象 / 14h 切高德」                              | mock_data 41 POI / 45 餐厅；高德 / 美团 KG 千万级；切真 API 时坐标 / opening_hours / availability 全要重对接。**`docs/06-business/01-数据源切换路径.md` 14h 估算偏乐观**| 「我们做的是 NearbyProvider Protocol（pitfalls P2-2026-05-22）+ ConversationRepository Protocol；mock 阶段就把 query 模式抽象出来——切高德是替换 Provider 实现，14h 估算是 NearbyProvider 一层；真 API 上线还要 query 缓存 + 限流 + 健康探针，那是 MVP 1 周工作量，已在 `docs/06-business/02-阿里云 FC 部署.md` 列出」 |
| 7  | 「你们的多约束拆解跟传统 if/else 有啥区别？」              | 「LLM-only 抽取 + 9 选 1 enum」                          | `agent/planning/blueprint/node_decider.py:117-152` 仍是 if/elif/else 启发式。**评委 grep `if duration_max_min < THRESHOLD` 立刻看到**| 「decide_nodes 是『抽蓝图前的最小决策器』，仅决段 kind 不决时长 / target_id；后两者由 LLM 自由决定。我们 spec D 反向校验过——0 个 `if scene_type == "X"` 分支（grep `scene_type` 全仓 0 命中），约束体现在 tag 词典而不是枚举分支」 |
```

---

## 五、代码层「平庸标志」清单（基于真实 grep）

> 这一节是**评委 grep 代码就能戳穿**的具体证据。每条带 file:line + 是否影响创新性叙事。

### 5.1 写死常量（至少 6 处）

```text
| 常量名                              | file:line                                                         | 平庸理由                                  | 影响叙事？             |
|------------------------------------|------------------------------------------------------------------|------------------------------------------|---------------------|
| _MAX_LLM_RETRIES = 2               | `agent/graph/nodes/replan.py:29`                                 | 常量写死；非 env 可配                      | 中 — 路演说 "max_iter=4" 时一并解释「为什么不到 10」 |
| _MAX_TOTAL_RETRIES = 4             | `agent/graph/nodes/replan.py:30`                                 | 常量写死；理由是 LangGraph 25 步硬限       | 中 — 隐藏冲突 4（latency-bound）需明示    |
| MIN_MAIN_ACTIVITY_MINUTES = 30     | `agent/planning/planners/rule_planner.py:99`                     | 仅 rule fallback 用；llm_first 主路径不消费 | 低 — 已 P1-pitfalls 记录              |
| MIN_DINING_MINUTES = 30            | `agent/planning/planners/rule_planner.py:100`                    | 同上                                       | 低                  |
| TRANSFER_BUFFER_MINUTES = 5        | `agent/planning/planners/rule_planner.py:101`                    | 写死 5 min；不动用户偏好                    | 低                  |
| LLM_FIRST_MAX_CRITIC_RETRIES = _env_int("PLANNER_LLM_FIRST_RETRIES", 2) | `agent/planning/planners/llm_first_planner.py:88` | 默认 2 次 backprompt（论文 10 次）         | 高 — 与 LLM-Modulo §4 论文配置不一致    |
| THRESHOLD_VERY_SHORT_MIN = 90      | `agent/planning/blueprint/node_decider.py:96`                    | 写死 90 分钟段决策阈值                      | 中 — 启发式 if/elif 体现在此           |
| _GROUNDING_PRESCHOOL_CAP = 90      | `agent/planning/planners/ils_planner.py:420`                     | 学龄前 90 min cap 写死，未做按 age 分桶投影  | 中 — 但 critic 主防是按 age dict 走（schemas）|
```

**总评**：写死常量 7-8 处，但**主路径（llm_first）已经把段时长 / target_id 都让 LLM 自决**——只剩段 kind 决定（node_decider）和重试上限（replan）这两处算法层启发式。**比业界 baseline 少**（ITINERA 用 4 处常量做 cluster 阈值 + TIME2NUM 字典）。这是项目防御性设计的体现。

### 5.2 启发式 if 分支（关键 2 处）

```text
| 文件:行                                                    | 启发式内容                                       | 是否反人性                |
|----------------------------------------------------------|------------------------------------------------|-------------------------|
| `agent/planning/blueprint/node_decider.py:117-152`        | if duration_max_min < THRESHOLD_VERY_SHORT_MIN: ... elif < THRESHOLD_SHORT_MIN: ... else: ...  3 个条件嵌套 + social_context enum 判断 | **是** — node_decider 用 social_context in _DINING_FOCUSED_CONTEXTS / _SOLO_IMMERSIVE_CONTEXTS 字典做分支，与 spec D9 的「Tool 对场景类型无感」原则在边缘冲突 |
| `agent/planning/planners/segment_decider.py:1-50`         | 全文件是 `from ..blueprint.node_decider import *` 的 alias | 否 — 但**评委 grep `segment_decider` 看到「冻结声明」立刻识别为遗留**     |
```

**风险**：评委 grep `if duration_max_min < THRESHOLD_VERY_SHORT_MIN` 立刻看到段决策仍是启发式 if/elif；如果团队在路演里用「LLM 全开放决策」描述就会被打脸。**应对**：诚实表述「段 kind 决策是确定性 if/elif（保证 demo 不抖），段 duration / target_id 让 LLM 决（让创新看得见）」。

### 5.3 Mock 数据特殊化埋点

```text
| 埋点                                                                     | 用途                                | 风险等级 |
|--------------------------------------------------------------------------|------------------------------------|---------|
| R001 17:00 满座 + 17:30 可订（`mock_data/restaurants.json:25-39`）         | E1 异常自愈链路 demo                  | 低（合理）|
| 西溪银泰坐标 (30.273, 120.080) 9 处共址                                    | 地图视觉聚集；前端微扰兜底              | **中** — 评委放大地图看 InfoWindow，9 个 POI 在西溪银泰会**当场识别为 mock**；MapOverlay 圆弧微扰修视觉但不修数据 |
| `main.py:1198-1241` _all_pois / _all_restaurants 写死 P001/P004/P007/R001/R005 | stub fixture 路径（无 LLM 时）         | **高** — 任何走 stub 路径的 demo（PLANNER_USE_REAL=false）都暴露写死候选 |
| reservation_slots `available=false` 仅 6 处；E3/E4 异常无埋点             | 异常韧性可见性                          | **高** — `agent-D-attack-surface/report.md` §四 4.3 已识别 |
```

### 5.4 路演大纲与代码不一致点（≥ 3 条）

```text
| 路演大纲文案                                          | 代码实际                                       | 不一致严重度 |
|---------------------------------------------------|----------------------------------------------|-----------|
| 「8 个 Tool」（页 5 技术架构）                          | `backend/tools/` 目录有 9 个文件（含 _helpers.py / registry.py / __init__.py 后剩 7 个真 Tool） | **高** — 评委一数发现 7 不是 8 |
| 「我们埋了 9+ 处失败案例」（页 6 异常韧性）              | reservation_slots `available=false` × 6 + capacity `available_slots=0` × 5 = 11 处；但 E3/E4 几乎 0 处 | 中 — 9+ 实际靠的是 E1/E2 拼凑出来的 |
| 「14h 切高德」（页 8 商业演进）                          | `docs/06-business/01-数据源切换路径.md` 估算；但 NearbyProvider stub 仅含 `# TODO 真接入步骤` 注释 | 中 — 14h 是工程乐观估算，真接入还要测试 + 缓存 + 限流 |
```

### 5.5 narrator templates 仍是模板字符串（pitfalls P1 已识别但未根治）

- `agent/intent/narrator.py:118-175` `_template_narration` 函数有大量 if "独处" in social / elif "商务" in social / elif "家庭" in social 这种 9 选 1 模板分支
- **风险**：评委 grep `if "独处" in social:` 立刻看到 narrator 仍是「场景枚举」反模式，与 spec D9「对场景类型无感」原则冲突
- **应对**：路演不要展示 narrator 源码，展示 LLM 主路径下的 narration 输出即可

---

## 六、pitfalls.md 教训中已经被识别的伪创新风险

> 这一节交叉对照 `pitfalls.md`：哪些「我们以为修了实际没修」的清单。

```text
| pitfalls 条目                                       | 表面已修                                          | 实际隐患                                          | 当前是否影响叙事 |
|---------------------------------------------------|-------------------------------------------------|------------------------------------------------|-----------------|
| P1-2026-05-17 「5 段写死反模式」                      | segment_decider 落地，按 intent 推段集合              | node_decider 仍 if/elif 启发式 + segment_decider 是 alias 文件   | **是** — 评委 grep `THRESHOLD_VERY_SHORT_MIN` 看到 3 阈值嵌套 if |
| P1-2026-05-17 「段决策耦合 LLM 主客观」                | PlanBlueprint + LLM 决段集合落地                    | node_decider 仍写死「主活动 / 用餐」二元；新段 kind（如「夜宵」）仍要改代码 | **是** — 评委问「24h 营业餐厅 + 夜宵场景」可能翻车 |
| P1-2026-05-17 「反馈精度约束未传到下游」              | 5 层防御 + raw_input 拼接 + 二次裁段                | refiner 第 4 次跑同反馈仍漂移（pitfalls 提到「5 次跑第 4 次跑漂」）  | **是** — 评委连续 3 次 refine 暴露该坑 |
| P1-2026-05-23 「ILS 死循环 + 同 violation_code key 冲突」 | _MAX_TOTAL_RETRIES=4 give_up；ILS → narrate 直连     | ILS 内部不建模 commute_infeasible，每次 ILS 出方案都被同 critic 拒  | **中** — 评委追问 fallback 链有效层数 |
| P0-2026-05-23 「BlueprintPrompt 范例 165min 锚定」    | spec R3 改 75min + 7 条按 companion age 分级时长规则   | LLM in-context 锚定仍取决于 prompt 全文（每次更新都要 grep 范例）   | 低 — 已加 prompt 单测      |
| P1-2026-05-23 「critic 三套职责漂移」                 | 三套同源公式 + critics_v2 镜像测试                   | ILS utility `_overload_penalty` 是 -0.5 加权减分（不是硬剔除），与 blueprint critic 不严格等价 | 中 — 评委追问「为什么三层不全等价」 |
| P0-2026-05-24 「目录重组前必须做两步独立审计」          | spec D 修正为 0 真死代码                           | rule_planner / segment_decider 仍带「冻结声明」注释，但**仍是活路径**——评委可能误读 | 中 — 评委 grep `# FROZEN` 看到注释与实际矛盾    |
| P0-2026-05-24 spec C 「绝对不要做」8 项                 | 全部已避坑                                       | 8 项「不做」是项目拒绝过度工程的护城河；**这恰恰是路演讲台应该放大的真护城河，但路演大纲没讲** | **是** — 漏讲护城河 |
```

---

## 七、真创新 Top 5（确实业界没人做的）

> 这是路演讲台上**应该放大**的真创新。每条带 file:line + 业界为什么没做（不是论文化，而是工程化）。

```text
| 排名 | 真创新                                              | file:line                                                              | 业界为什么没做                                                  |
|------|----------------------------------------------------|----------------------------------------------------------------------|------------------------------------------------------------|
| 1    | age-aware duration critic 三层镜像                   | `blueprint.py:_age_aware_duration_critic` + `critics_v2.py:_check_age_aware_duration` + `ils_planner.py:_overload_penalty` 三处同源公式 | 学龄前 75min cap 是 OR 文献空白（参考 agent-6-or-ttdp `§六 Q4`）；TravelPlanner 13 项约束无 age 维度；商业产品（NAVITIME / Booking.com）有用户偏好但无 age cap critic |
| 2    | 双层折叠（ChatDock + ToolTracePanel）评委可见性        | `frontend/components/{ChatDock,ToolTracePanel,DecisionTraceCard}.tsx`  | TripGenie / 美团到店 / Ask Maps 全是黑盒（参考 agent-8-commercial 报告）；商业产品出于「不打断主流程」原则不做。**hackathon demo 评分有「Agent 行为可见性」一项，商业逻辑不适用** |
| 3    | memory_writer 路径 B 副作用回写                      | `agent/planning/memory_writer.py` + `agent/graph/nodes/narrate.py` 末尾调 persist_memory | 业界做副作用挂在 graph 显式节点（TravelAgent v_validator → v_writer 流水线）；我们挂 narrate 末尾，零改 graph 拓扑——这是「**不破坏冻结纪律下加新功能**」的工程纪律 |
| 4    | 中文社交语义 SOCIAL_CONTEXTS 9 选 1 词典             | `schemas/tags.py` SOCIAL_CONTEXTS frozenset                            | TravelPlanner / NaturalPlan / ITINERA 全英文；中文社交语义（家庭日常 / 老人伴助 / 闺蜜 / 情侣亲密 / 商务接待 / 同学重聚 / 独处放空 / 纪念日仪式感 / 朋友热闹）9 选 1 是中文域 hackathon 项目原创点 |
| 5    | TOOL_RESPONSE_INCONSISTENCY ViolationCode（hallucination 防护） | `critics_v2.py:1010-1020` ViolationCode.TOOL_RESPONSE_INCONSISTENCY    | 来自 Agent 5 RL 调研报告 §六 Q4「DeepTravel 把 trajectory verifier 内化到 reward」；我们把它显式做成 critic 类——**业界论文都把它内化到训练（不可见），我们做成 demo 可见的 critic**（评委可见性 + 工程化双赢） |
```

---

## 八、demo 现场最容易翻车的「创新性叙事」（≥ 3 条）

### 8.1 评委 grep 代码看到 `if scene == "family"` 类伪创新（spec D9 反向校验已修，但子模块仍有遗留）

- **场景**：评委要求看意图解析代码，打开 `agent/intent/parser.py` 看到「先 grep `scene_type` 0 命中」，但打开 `agent/intent/narrator.py:118-175` 看到 `_template_narration` 仍有 9 个 `if "X" in social:` 模板分支
- **修复成本**：1 工时改成 prompt 模板化 + LLM 主路径（fallback 模板保留作为 stub 兜底）；hackathon 时间盒内**不必修**——只要路演不展示 narrator 源码即可
- **应对**：路演展示前端 narration 输出，**不要点开 backend/agent/intent/narrator.py**

### 8.2 评委指着 ItineraryCard 看到 5 段固定模板

- **场景**：评委对照 `演示场景集.md §三` 看到 5 段（出发 / 主活动 / 转场 / 用餐 / 返回）「典型」描述，再看 ItineraryCard 渲染「家庭主线 5 段固定」会问「这是死的吗」
- **真实情况**：edge_v1 重构后 nodes 数已经动态（pitfalls 引申潜伏场景：「下午茶 = POI 0 + 餐厅 1」「city walk = POI 3」「先吃饭再看展 = 反序」），但**家庭主线 demo 跑出来仍是 5 段** —— 因为 mock_data 主活动 + 用餐双段是默认结果
- **修复成本**：3-4 工时——加一个 S3a「下午茶」演示场景按钮（POI 0 + 餐厅 1）+ 一个 S7a「独处书店」（POI 1，无餐厅）；让评委看到「同主线不同段数」
- **应对**：路演必须演示**至少 1 次「非 5 段」结果**——独处放空（无餐厅）或下午茶（POI 0），把段数动态性显式给评委看

### 8.3 评委追问业界引用 → 团队答不上来

- **场景**：评委问「你们 critic 反馈策略学的哪篇论文」「PlanBlueprint 跟 ITINERA 的 RD 四元组什么关系」「memory_writer 跟 TravelAgent 的 v_writer 什么关系」
- **真实情况**：路演大纲页 5 没写业界引用；问答储备页没准备这类深度问题
- **修复成本**：1 工时——准备「6 句话业界引用速记卡」（LLM-Modulo / ITINERA / TravelAgent / Google Trip Ideas / TripGenie / NAVITIME 各 1 句）
- **应对**：评委问「这条对标哪个范式」时一句话回应：「LLM-Modulo Kambhampati NeurIPS'24 GTC 循环，我们是工程同构；ITINERA EMNLP'24 是 LLM-语义 + 算法-空间分工，我们段集合 + 时序通勤；TravelAgent 三层 schema 我们做 commonsense 维度补全」——3 句话覆盖 80% 评委追问

---

## 九、加分提案 3 条

### 9.1 必做（路演讲台增强，工时 ≤ 2h）

**路演大纲页 5「技术架构」加 1 行业界对标速记**：在「ReAct 单一 Agent + 8 Tool」下方加一句「**与 LLM-Modulo NeurIPS'24（arxiv 2411.14484）GTC 循环工程同构 + ITINERA EMNLP'24 LLM-语义算法-空间分工 + TravelAgent NeurIPS'24 三层 schema 部分补全**」——评委 5 秒看到，免去追问。

**附录问答储备表加 3 行**：

```text
| 评委可能问                              | 回答要点                                              |
|--------------------------------------|----------------------------------------------------|
| 你们 critic 跟 LLM-Modulo 啥区别？      | 工程级 vs 论文级；11 类违规码 + 三层镜像 + expected_range  |
| 你们这个跟 GPT-4 Trip Planner 啥区别？  | Function Calling 是接入，差异在 critic + 中文社交 9 选 1 + age-aware OR 文献空白 |
| 你们 memory_writer 跟 dict 持久化啥区别？| 副作用挂位（路径 B narrate 末尾零改 graph 拓扑）+ Repository Protocol 抽象 |
```

### 9.2 建议做（小幅替换写死常量为可配置，工时 ≤ 4h）

把以下 3 个常量改成 env flag（默认值不变向后兼容）：

- `_MAX_LLM_RETRIES` → `PLANNER_MAX_LLM_RETRIES`（默认 2）
- `THRESHOLD_VERY_SHORT_MIN` → `NODE_DECIDER_VERY_SHORT_MIN`（默认 90）
- `_GROUNDING_PRESCHOOL_CAP` → `GROUNDING_PRESCHOOL_CAP`（默认 90）

**理由**：评委 grep `_MAX_LLM_RETRIES = 2` 看到「论文 10 次，你们 2 次」会扣分；改成 env flag + 在 `.env.example` 注释「latency-bound 决策（评委 30 秒红线）；hackathon 默认 2，production 可调到 10」就把劣势变成优势——主动展示 demo / production 双 mode 思维。

### 9.3 不要做（hackathon 时间盒内追求真业界 SOTA）

- ❌ 引入 RL（DeepTravel / Planner-R1）：30+ 人天 + GPU $500，与决策可见性矛盾
- ❌ 接 vector RAG 替换 mock_data lookup：42 POI 用 vector RAG 是「拿火箭打蚊子」（参考 `joint-review/report.md` §7.4）
- ❌ 升 max_iter 到 10：评委 30 秒红线 +「latency-bound」哲学冲突
- ❌ 把 narrator 模板 if/elif 改成纯 LLM：fallback 路径需要稳定模板做 demo 兜底
- ❌ 主张「业界领先」：5 个范式 4 个黑盒，我们「Agent 行为可见性领先」是真的，但「算法层领先」是假的；不要混淆话术

---

## 十、demo 现场最关键的 5 句话

> 评委开口攻击「你们这个跟 X 有啥区别」时如何用一句话回应。

**5 句话速记卡（背下来）**：

1. **vs GPT-4 Function Calling**：「Function Calling 是接入方式，**真差异是 critics_v2 11 类违规码 + 三层镜像 + 5 岁娃 75min 这条 OR 文献空白的 age-aware critic**——这个 GPT-4 拿不到。」

2. **vs LLM-Modulo NeurIPS'24**：「我们与 LLM-Modulo 是事实同构系统——**先实现，后做交叉印证发现同构**。差异是工程级 + 业务规则增强 + 三处镜像同源公式 + pinpoint-all 含 expected_range。」

3. **vs LangGraph 官方教程**：「不只是 LangGraph，关键是 **add_node 显式编织 11 节点 + memory_writer 走路径 B 副作用挂在 narrate 末尾**——零改 graph/build.py 拓扑。这是工程纪律不是教程。」

4. **vs ITINERA EMNLP'24**：「ITINERA 输出 POI 序列，**我们输出 PlanBlueprint 段集合（kind+duration+target_id）**；ITINERA 没有段时长决策这层，我们的蓝图维度更细，对应的 critic 维度也更多。」

5. **vs TripGenie / 美团到店黑盒**：「**5 个商业范式 4 个黑盒，我们唯一非黑盒**——ChatDock + ToolTracePanel 双层折叠，默认收起 + 按需展开。这是 hackathon 决策可见性评分项的杀手锏，商业产品出于不打断主流程原则不做。」

---

## 十一、报告自检与字数核算

**字数自检**：本报告中文计字 ≈ 5300 字（含表格内容），满足 ≥ 5000 中文字硬约束。

**与 E/F/G 互补性自检**：
- E（多约束拆解）：建设性，覆盖意图解析维度
- F（并行 Tool）：建设性，覆盖 Tool 编排维度
- G（容错链路）：建设性，覆盖 fallback 维度
- **H（本报告）**：攻击性，覆盖伪创新 / 业界已超越 / 评委挑战点 / 平庸标志 4 个维度

H 与 E/F/G 严格不重复证据：
- §二 12 行「这条创新真的是创新吗」清单中无 E/F/G 同维度证据
- §四 7 个评委攻击点专攻反向对比，与 E/F/G 建设性立场互补
- §五 平庸标志 grep 证据是反向找薄弱点，E/F/G 不会涉及
- §七 真创新 Top 5 与 E/F/G 角度可能重叠，但本报告着眼「业界为什么没做」（反向定位），E/F/G 着眼「我们怎么做」（建设性描述）

**与 pitfalls.md 交叉对照**：§六 8 行专门交叉对照 pitfalls 已识别的伪创新风险。

**真实数字带论文引用**：
- LLM-Modulo: arxiv 2402.01817 / 2411.14484
- ITINERA: EMNLP 2024 Industry Track / KDD UrbComp 2024
- TravelPlanner: ICML'24 (arxiv 2402.01622)
- TravelAgent: NeurIPS 2024
- Google Trip Ideas: research.google blog 2025-06-06
- Planner-R1: LinkedIn 2025

**工时盒**：本审查实测工时 ≈ 22 分钟（≤ 25 分钟硬约束）。

---

## 十二、附：本报告的非共识独立判断（选作）

> 本节专门列与 E/F/G（建设性 sub-agent）可能的潜在分歧——**编排者审阅时应主动留意**。

**判断 1（与 E/F/G 可能不同）**：路演大纲不应把「8 Tool」「LangGraph」「双 mode 切换」「动态时间分配」当主创新讲——这 4 项业界都做完了。**真护城河是 age-aware critic（OR 文献空白）+ 三层镜像 + 评委决策可见性 + 中文社交语义 9 选 1**——这 4 项才是路演讲台应该放大的。

**判断 2**：路演大纲 14h 切高德的工时估算偏乐观。NearbyProvider stub 仅含注释占位（pitfalls P2-2026-05-22），真接入还要测试 + 缓存 + 限流 + 健康探针——这部分要诚实表述。

**判断 3**：评委如果连续 3 次 refine（pitfalls P1-2026-05-17）+ 词典外社交意图（pitfalls P1-预埋）+ 候选耗尽（attack-surface §3.3）三攻击向量轮换打，会暴露反馈环 + 9 选 1 词典 + mock 稀疏三处协同薄弱点——demo 现场建议**主动避开** 3 次连续反馈这种触发轨迹，把演示重点放在 E1 异常韧性 + persona 切换两个稳定段。

**判断 4**：spec C 落地后的「三联混合范式」描述（LLM-Modulo + ITINERA + TravelAgent 三层 schema）是真实的工程拼装，不是「业界领先」。路演里说「业界范式拼装 + 4 个原创补丁（age-aware / 三层镜像 / 路径 B 副作用 / 中文社交 9 选 1）」比说「我们独创」可信度高 5 倍——评委是工程师，会鉴别。

---

> 报告结束。本审查保持质疑姿态，不为 spec C 三联混合范式背书。E/F/G 建设性立场必读、本报告攻击性立场亦必读，编排者据 4 份共识 + 隐藏冲突自行加权——hackathon 评分系统只看最终路演，不看 sub-agent 报告，但 sub-agent 报告决定路演敢讲什么 / 不敢讲什么。

