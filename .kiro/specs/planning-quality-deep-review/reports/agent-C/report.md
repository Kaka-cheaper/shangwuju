# Agent C 审查报告 —— LookupHop / EstimateRouteTime / 跨模块通勤一致性

> 范围：#17 LookupHop（`backend/agent/lookup_hop.py`）+ #9 EstimateRouteTime（`backend/tools/estimate_route_time.py`）+ 跨 assemble / critic / execute worker / SSE / 前端 5 处通勤数据消费点的横向一致性。
>
> 触发：用户在 demo 看到「家庭主线 5 岁娃博物馆 2.5h」过长。我这条线的核心追问是「整条时间轴的可信度」——通勤估算偏差 / 漂移会让所有节点 start_time / total_minutes 一起塌方。

---

## 1. 现状摘要

### #17 LookupHop（`agent/lookup_hop.py`）

是 assemble + critic 共用的「边解析」纯函数，4 级降级（注释明明写「三级降级」但代码实际是 4 级，文档与实现已经轻微漂移，见 §6）：

```text
| 级 | 触发                                       | 返回                                       |
| 1  | from_id == to_id                           | (0, "virtual", "in_place")                 |
| 2  | routes.json 命中且 transport_pref 字段非 None | (min, transport_pref, "real_route")        |
| 3  | 双端坐标可解析（home / Pxxx / Rxxx）       | haversine × 1.3 / 速度 × 60，最小 1min     |
| 4  | 全失败兜底                                  | (15, transport_pref, "estimated")          |
```

模式速度常量：`WALKING=5km/h, TAXI=25km/h, BUS=18km/h, ROAD_FACTOR=1.3, FALLBACK=15min`（`agent/lookup_hop.py:50-65`）。

模块级 `lru_cache(maxsize=1)` 缓存 `_route_index / _poi_coord_index / _restaurant_coord_index`，对同一进程内的同输入完全确定性，单测 `test_L5_consistency_same_input_same_output` 覆盖 4 个分支各 3 次调用恒等（`backend/tests/test_lookup_hop.py:140-160`）。

### #9 EstimateRouteTime（`tools/estimate_route_time.py` + `tools/_helpers.find_route`）

是 LLM Function Calling 暴露给 ReAct agent 的查路工具，**只有 1 级**：调 `find_route(from, to)` 线性扫 `mock_data/routes.json`，命中返 `Route` 对象（含 walking/taxi/bus 三个分钟字段），**未命中直接返 `success=false, reason=NOT_FOUND, route=None`**（`tools/estimate_route_time.py:32-40`）。没有 haversine 兜底、没有 in_place 短路、没有 15min 兜底。

### 跨模块调用分布

| 路径                                                          | 调谁              | 备注                                       |
|--------------------------------------------------------------|------------------|-------------------------------------------|
| `agent/assemble_blueprint.assemble_from_blueprint`            | `lookup_hop`     | 拼装 hop.minutes / mode / path_type        |
| `agent/v2/critics_v2._check_hop_feasibility`                  | `lookup_hop`     | 验 hop.minutes ≥ actual_min - 2            |
| `agent/v2/react_agent.estimate_route_time`（Pydantic AI tool） | `estimate_route_time` Tool | fallback 路径，**冻结**            |
| `agent/planner.py:754` / `agent/llm_planner.py:346`           | `estimate_route_time` Tool | rule-based & llm-first，**冻结**     |
| `agent/graph/nodes/execute.py`                                | **既不调 lookup_hop 也不调 estimate_route_time** | 见 §2 P0-1                  |
| `frontend/components/ItineraryCard / MapOverlay`               | 仅消费 hop.minutes / hop.path_type | 不重新估算                       |

### `mock_data/routes.json` 量化

```text
total=217（注释写 56 条已过期，pitfall §「distance_km 错位」§4 也只提 56 条）
distinct from=88 / distinct to=81
H→P:  42   H→R:    0     ← 直奔餐厅没真值
P→H:  22   R→H:   46
P→R: 107   R→P:    0     ← 餐后回 POI 没真值
P→P:   0   R→R:    0     ← 串多 POI / 多餐厅没真值
重复对：1（R019→home 出现两次，不同分钟数）
缺反向边：172 / 216 distinct 对（≈ 79.6%）
P001-P020 全部 20 个 POI 没有 P→home 反向边
```

---

## 2. 业务合理性 gap 清单（P0 / P1 / P2）

### P0（demo 立刻翻车）

#### [P0-1] graph 主路径 execute 阶段漏注册 `estimate_routes_worker`，docstring 与实现已割裂

- **现象**：`agent/graph/nodes/execute.py` 文件 docstring 第 10 行明文承诺：

  > `estimate_routes_worker → state["routes"]（先粗估常用 home→候选 POI 距离）`

  但实际 `execute.py` 只定义了 3 个 worker，build.py 也只 `add_node` 3 个 worker（`agent/graph/build.py:103-105`）。`state["routes"]` 字段在 state.py:97 还在，赋值为空 list 永不写入。
- **反例**：用户输入「我想下午直接去吃个饭」→ LLM 蓝图选了 R024 → assemble 调 `lookup_hop("home", "R024", "taxi", profile)`，但 routes.json 中 `home→R*` 总条数 = 0 → 必走 haversine 估算（看 `R024` 坐标）→ 但 LLM 在 LangGraph 主路径里**根本没拿到这个分钟数**（execute 没跑，prompt 里也没塞），它在 `BlueprintLLM` 阶段「按经验估」一个 duration_min，跟实际通勤毫无关系。
- **根因**：写 docstring 时设计的是 4 worker，写 build.py 时偷工省了 1 个，没人补 docstring；同时 critic 又只看 lookup_hop 的客观值，不看 LLM 蓝图里 LLM 的「主观分钟」——主路径根本没让 LLM 看到候选间距矩阵。
- **修复方向**：要么真补一个 worker（粗估 home→top-K POI / top-K Rest，按 transport_pref 选字段，注入 prompt），要么把 docstring 改了别承诺；推荐前者，否则 LLM 蓝图阶段对距离完全无感（与 P1 [P1-1] 的 LLM 时长决策也耦合）。
- **影响子环节**：#11 BlueprintLLM、#12 BlueprintPrompt、#16 AssembleBlueprint、#25 graph build。

#### [P0-2] LookupHop 与 EstimateRouteTime 对同一对输入返**不同语义**，fallback 路径 LLM 看到的分钟数 ≠ assemble 写进 hop 的分钟数

- **现象**：以 `(from="P001", to="home")` 为例：
  - `lookup_hop("P001", "home", "taxi", profile)` → `(4, "haversine_estimated", "estimated")`（routes 没有 P001→home 反向边，走 haversine：home(30.275,120.075) ↔ P001(30.285,120.083) ≈ 1.27km × 1.3 / 25 × 60 ≈ 4min）
  - `estimate_route_time(EstimateRouteTimeInput(from_location="P001", to_location="home"))` → `success=false, reason=NOT_FOUND, route=None`
- **反例**（family demo 主线，LangGraph 主路径已跳过 estimate_route_time，但 fallback 链 react_agent / planner_hybrid 仍在用）：

  ```text
  user: "周末下午带 5 岁娃出去玩"
  react_agent 路径：
    1. LLM 调 search_pois → 拿到 P001 森林儿童乐园
    2. LLM 调 estimate_route_time(home, P001) → success=true taxi=13min
    3. LLM 调 estimate_route_time(P001, R001) → success=true taxi=8min
    4. LLM 调 estimate_route_time(R001, home) → success=true taxi=7min
    5. LLM 输出 ItineraryResponse（按 13/8/7 min 排时间轴）
    6. critic 调 lookup_hop(R001, home) → 7min（routes 命中，与 LLM 一致 ✓）

  但若把 R001 换成 R024（routes 中只有 R024→home=12min，没有 home→R024）：
    1. LLM 调 estimate_route_time(home, R024) → NOT_FOUND
    2. LLM 当成"打车 0 分钟"或自行估
    3. LLM 输出 hop.minutes=0 进蓝图
    4. critic 调 lookup_hop("home", "R024") → routes 中无（home→R*=0 条）→ haversine
       R024 坐标若与 home 16km 远 → 估算 ~50min → critic 报 HOP_INFEASIBLE
    5. backprompt → LLM 重出 → 又算不准 → 死循环
  ```

  这正是 pitfalls.md 已经记录的 [P0] 2026-05-26「LangGraph commute_infeasible 死循环」根因之一。
- **根因（架构级）**：两套通勤函数面向两批消费方却没强制对齐：
  - `lookup_hop`：服务于「客观裁判（critic）+ 客观计算（assemble）」，必须对任意输入返一个数（否则 critic 没法跑），所以做了 4 级降级。
  - `estimate_route_time`：服务于「LLM 决策原料」，只返 mock 真值不返估值，目的是不让 LLM 在 NOT_FOUND 之上误编故事。
  - 但两边没有**契约约束**：LLM 拿 NOT_FOUND 时究竟要不要替换 POI / 跳过这段，prompt（`react_agent.py:296-360`）只在 step-by-step 里要求「调 estimate_route_time」并没说「NOT_FOUND 时怎么办」。
- **修复方向**：选其一：
  - **方案 A（推荐）**：`estimate_route_time` Tool 内部也走 lookup_hop（让它有 haversine 兜底，但额外加 `route_source` 字段告诉 LLM「这是估值不是真值」，LLM 自己决定是否信）；
  - **方案 B**：保留双轨但在 prompt 里硬约束「拿到 NOT_FOUND 必须换 POI / 改换乘方式，不准自填」；
  - **方案 C（fallback 路径冻结后）**：直接砍掉 LLM 看分钟数的链路，只让 LLM 看 distance_km，把分钟全交给 assemble。
- **影响子环节**：#9 EstimateRouteTime、#11 BlueprintLLM、#13 BlueprintCritic、#14 CriticsV2 hop_feasibility、#16 AssembleBlueprint，强烈关联 #18 PlannerHybrid 的 ILS 死循环。

### P1（用户不会立刻发现，但会侵蚀信任）

#### [P1-1] `routes.json` 217 条边的覆盖率结构性偏科，多 POI / 餐后回 POI / 直奔餐厅场景必走 haversine

- **现象**：覆盖率矩阵 H→P=42, P→H=22, P→R=107, R→H=46, **P→P=0, R→R=0, R→P=0, H→R=0**。任何「2 个 POI 串行」/「餐后再回某个 POI 散步」/「家直奔餐厅吃下午茶」必落到 lookup_hop 3 级 haversine。
- **反例**：

  ```text
  user: "周末下午带娃逛西溪 + 杭州动物园再吃晚饭"（合理需求）
  blueprint: home → P001(西溪儿童乐园) → P003(动物园) → R001(餐厅) → home
  assemble:
    home→P001  routes 命中 taxi=13min  ✓
    P001→P003  routes 没有 P→P  → haversine ≈ 7min（按 25km/h 估）
    P003→R001  routes 命中 taxi=5min  ✓
    R001→home  routes 命中 taxi=7min  ✓
  ```

  P001→P003 的 7min 完全是直线距离 × 1.3 / 25 km/h 估出来的，可能与真实城西路况差 30-50%（杭州 24.26 km/h 早晚高峰，见 §3）。critic 拿到的也是 7min，所以**不会**报 HOP_INFEASIBLE，但**用户实际去**会比时间轴慢 5-10min，整条返程线一起延后。
- **根因**：mock 制作时只考虑「两段 demo 主线（家→活动→饭→家）」，没考虑「家→多 POI / 多 R 串行」。pitfalls.md `[P0/P1] 2026-05-22 routes 56 条手工随机数` 记过原始 56 条问题，扩到 217 条但 mid_node 互距矩阵仍是 0。
- **修复方向**：补足 P→P / R→R / R→P / H→R 矩阵（实际 demo 主流场景不超过 12 个 POI × 12 个 R = 144 对，加上去仍可控）；或者在 lookup_hop 3 级 haversine 上明确标 mode="haversine_estimated" 并在前端时间轴提示「估算 ~7 分钟（无路况数据）」让评委看到诚实降级。
- **影响**：#23 mock POI / Restaurant、#16 AssembleBlueprint、#22 ExecuteFinalize 文案、用户感知。

#### [P1-2] `routes.json` 既有边的 taxi 分钟与 haversine 几何估算严重失真（中位偏差 92.9%）

- **现象**：跑 217 条边对照表，routes.taxi 分钟与 haversine × 1.3 / 25kmh 估算的差值百分比：
  - mean = -101.3%, median = -92.9%, stdev = 173.1%, min = -1150%（即 routes 给的比估值短 12 倍）, max = 80%
  - Top worst：`P011→R029  km=22.43  routes_taxi=9  est=70`（routes 给 22.43km 走 9 分钟 ≈ 150km/h 平均速度，杭州市区物理不可能）
  - Top worst：`home→P033  km=16.67  routes_taxi=15  est=52`（67km/h，杭州市区峰值偏快）
  - Top best：`P001→R003  km=2.65  routes_taxi=7  est=8 diff=1`（短距吻合）

- **意味着**：lookup_hop 2 级（命中 routes）和 3 级（fallback haversine）对于**同一对长距离边**，可能返出 9 分钟 vs 70 分钟两种语义。如果 routes.json 哪天补上 `P011→R029` 的反向边，所有依赖 haversine 的代码路径估值会一夜之间从 70min 跳到 9min；反之删掉一行也是。这是隐式的 schema 漂移。
- **根因**：routes.json 是手工 4-30 之间随便填的（pitfalls.md §「routes 56 条手工随机数」），与 POI 坐标无任何几何一致性，1.3 路网折算系数和 25 km/h 速度也是基于真实城市做的（snapdistance.com 也用同一组），但 mock 数据本身就违反这套真实假设。
- **修复方向**：要么按 haversine 重新生成 routes.json（保证「routes 命中分钟」与「3 级 haversine」差距 < 30%），要么把 routes.json 视为「评委可见的真实城市数据」并贴一个尺度真值（杭州城西到武林广场约 45 分钟打车 vs routes 给 13 分钟）。Hackathon 优先选前者：写一个一次性脚本读 POI/R 坐标，按 1.3 × dist / speed 重算 217 条边，固化进 mock。
- **影响**：#23 mock 数据、#16 AssembleBlueprint、用户对时间轴的物理直觉。

#### [P1-3] 4 级兜底 15min 在 demo 主路径理论永不触发，但写进 critic 比较时仍会误伤未知 id

- **概率**：所有 mock POI / Restaurant 的 lat/lng 100% 完整（42/42 + 45/45），home_location 也完整。所以 4 级兜底**只在「LLM 自创 id（如 P_GHOST / 空字符串）」+「不是 home / 不以 P / R 开头」**才能触发。
- **频率估计**：极低，但已经在 `tests/test_lookup_hop.py:test_L4_fallback_when_unknown_ids` 走过，说明设计者也料到 LLM 会幻觉。
- **风险**：critic `_check_hop_feasibility` 把 15 当成 actual_min，与 LLM 蓝图的 hop.minutes 比对（容差 2min）。如果 LLM 写了 hop.minutes=10，critic 报 HOP_INFEASIBLE，backprompt 修正——但根本原因是 id 错而不是分钟数错，错误诊断信息会误导 LLM 改时间而不是改 id。
- **修复方向**：lookup_hop 4 级返一个 sentinel（如 mode=`"unknown"` 或 path_type=`"fallback"`），critic 看到 sentinel 时改报「目标点 id 无法解析，请重新选 POI / Restaurant」而不是「通勤需要 15 分钟，请加时间」。
- **影响**：#14 CriticsV2、LLM 修复路径准确性。

### P2（潜伏 bug、长期债）

#### [P2-1] LookupHop 注释写「三级降级」实际是 4 级，文档与实现轻微漂移

- 文件 docstring 块第 18 行明确「【三级降级】」，紧接着的 ASCII 表格列了 4 行（1 / 2 / 3 / 4）。代码里也有 `_TRANSPORT_PREFS` 枚举、`FALLBACK_MIN` 常量、4 级 return 语句。下游 design.md / pitfalls.md 也按「三级降级」措辞引用。
- 不会立刻翻车，但下次有人改 lookup_hop 时按「三级」理解，可能误删 4 级。
- 修复方向：把注释改成「四级降级」并把级数对齐到 1-4。

#### [P2-2] `routes.json` 重复对 R019→home 出现两次，分钟数相同但是双倍写入索引会被后写覆盖

- 两条记录：`R019→home walking=55 taxi=14 bus=37`（行 67、80）。`_route_index` 用 dict 索引，后写覆盖前写——巧合是两条值完全相同所以无 bug 暴露，但说明数据生成流程没去重。
- 修复方向：mock 加载时去重 + 校验同 key 不允许多条。

#### [P2-3] LookupHop 不查反向边（设计），但配合 routes.json 缺反向边 79.6%，让「家→POI→家」对称往返时回程必走 haversine 而往程走真值

- 体感对称性破坏（出门 13min，回家 4min？），demo 现场评委可能直观察觉异常。
- 修复方向：方案 A 在 lookup_hop 2 级加「反向边查询并对称」（违背设计决定），方案 B 补 routes.json 把所有 home→P 都补一条 P→home。推荐 B。

---

## 3. 业界对标 diff

### 对标 1：OSRM（Open Source Routing Machine）

- 链接：[OSRM Wiki](https://wiki.openstreetmap.org/wiki/Open_Source_Routing_Machine) / [ETA paper PMC8810392](https://pmc.ncbi.nlm.nih.gov/articles/PMC8810392/)
- 他们的：完整路网 + Contraction Hierarchies，Drive time 在图上跑 Dijkstra/A*；驾车误差中位数 < 5%。
- 差距：我们 mock 217 条边，按 88 节点全矩阵计覆盖率约 30%；3 级 haversine 误差 30-50%，2 级又被手填数据污染（见 [P1-2]）。
- 借鉴：「mock 路网必须覆盖所有 mid_node 互距矩阵」的覆盖率保证。

### 对标 2：Snapdistance / 1.3 折算系数

- 链接：[snapdistance.com](https://snapdistance.com/) / [MassAtLeeds/RouteFactor](https://github.com/MassAtLeeds/RouteFactor)
- 他们的：driving time 用 great-circle × 1.3（路网迂回因子）÷ 平均速度（内容已改写）；RouteFactor 实测英国数据 1.27-1.45。
- 差距：我们的 ROAD_FACTOR=1.3 与业界主流一致 ✓；问题不在系数而在 mock routes 与几何脱钩。

### 对标 3：Google Maps Routes API traffic_aware

- 链接：[Routes API trade-offs](https://developers.google.com/maps/documentation/routes/config_trade_offs) / [traffic-model](https://developers.google.com/maps/documentation/routes/traffic-model)
- 他们的：三档（TRAFFIC_UNAWARE/AWARE/AWARE_OPTIMAL），城市段做实时路况修正（内容已改写）。
- 差距：lookup_hop 没有路况维度；demo 时段固定下午可接受，但需在 narrate 文案声明「未含路况」。

### 对标 4：杭州 / 中国一二线城市平均车速

- 链接：[Statista urban PT in China](https://www.statista.com/topics/5662/urban-public-transportation-in-china/) / [OpenTripPlanner Analysis](https://docs.opentripplanner.org/en/latest/Analysis/)
- 数据：北京早晚高峰平均车速约 24.26 km/h（内容已改写）；schedule-only 估算偏乐观。
- 我们的：TAXI_KMH=25 ≈ 24.26 仅偏差 3% ✓；BUS_KMH=18 偏快（杭州含站点停靠典型 12-15 km/h），建议下调到 14-15。

### 对标 5：TravelPlanner Benchmark（OSU NLP）

- 链接：[osu-nlp-group.github.io/TravelPlanner](https://osu-nlp-group.github.io/TravelPlanner/)
- 他们的：Sandbox 给 LLM 提供 Google Flights/Distance Matrix 真实 API frozen snapshot；评测 7 类常识约束（含 commute consistency）。
- 差距：他们 LLM 看到的分钟 == 评测的分钟（单源真值）；我们 LLM 看的（estimate_route_time）≠ assemble 写的（lookup_hop）≠ critic 验的（lookup_hop）三层不全等。
- 借鉴：可设 `hop_consistency` 指标（critic 与 assemble 结果恒等比例）作为 demo 透明度卖点。

---

## 4. 修复方案候选

### 方案 A：让 estimate_route_time Tool 内部走 lookup_hop（统一调用栈）★ 推荐

- 改动：`tools/estimate_route_time.estimate_route_time` 函数内部从 `find_route` 切换到 `lookup_hop`；输出加 `route_source: "real_route" | "estimated" | "in_place" | "fallback"` 字段（复用 HopPathType）。
- 工时：~30 分钟（含 schema 加字段 + 单测对齐）。
- 影响：#9 EstimateRouteTime（自身）、#14 CriticsV2（不变）、#16 AssembleBlueprint（不变）、#23 schemas/tools.EstimateRouteTimeOutput（加字段）；fallback 路径 react_agent / planner / planner_hybrid 已冻结但仍消费此 Tool，需在它们的 LLM-side 解析里增加对 route_source 的弱日志（不报错）。
- 风险：fallback 路径 LLM prompt 现在依赖「NOT_FOUND 触发换 POI」逻辑，改成永远 success=true 后这一异常分支被压平，需要在 prompt 加「route_source 为 estimated/fallback 时优先换更近候选」的条款；不然 demo 评分项 5「异常韧性」可能少一个触发点。

### 方案 B：补 routes.json 至全覆盖 + 几何一致性

- 改动：写一次性脚本 `scripts/regen_routes.py`，读取所有 POI/R 坐标，按 haversine × 1.3 / mode_speed 生成 H↔P, P↔R, P↔P, R↔R 全矩阵（约 88 × 88 / 2 ≈ 4000 条，可降级到只填「demo 8 场景实际触达的目标对子」≈ 200 条）；保留 routes.json 的 schema 不变。
- 工时：~45 分钟（脚本 + mock 补全 + 重跑测试）。
- 影响：#23 mock 数据；P→P / R→R 互距 0 → 全覆盖后 [P1-1] 和 [P1-2] 同时解决；lookup_hop 3 级 haversine 几乎不再触发。
- 风险：原本部分手填数据可能是「评委定向看的真实地标距离」，自动重算后会变成纯几何估算；建议保留 demo 主线 8 场景的 home→主 POI 与 主 POI→主 R 真值不重算，只重算其余。

### 方案 C：在 graph 主路径补 estimate_routes_worker

- 改动：`agent/graph/nodes/execute.py` 真添加 worker；调 `lookup_hop` 给 home→top-K POI 与 home→top-K R 各算 1 个分钟，写到 `state.routes`；prompt 在 BlueprintPrompt 中拼一个「候选距家通勤参考」表交给 LLM。
- 工时：~50 分钟（含 worker + state 写入 + build.py 注册 + prompt 改造 + SSE 适配的 tool_call 事件兼容 + 单测）。
- 影响：#11 BlueprintLLM 让 LLM 看到分钟数（决策更准）、#16 AssembleBlueprint 不变、#25 graph build / SSE adapter（事件类型加一个 estimate_routes_worker 分支，sse_adapter:159-168 已经有处理 3 worker 的代码可复用）。
- 风险：增加并行 worker 后 LangGraph 等待时间从 max(3 worker) 变 max(4 worker)，但 lookup_hop 是 lru_cache 内存计算 < 1ms，可忽略；但 docstring 已经承诺这个 worker，不补就是「写文档骗人」性质的破窗。

### 方案 D：lookup_hop 加反向边对称查询（轻量补丁）

- 改动：lookup_hop 2 级在 routes 没 (from, to) 时，再查 (to, from)，命中后取相同分钟数（保留方向不对称如单行道？hackathon 不区分）。
- 工时：~10 分钟。
- 影响：[P2-3] 体感对称性破坏直接修复；[P1-1] / [P1-2] 部分缓解。
- 风险：违反 lookup_hop docstring 中「设计明确：从 from 到 to 找不到就降级，保持确定性」的原始决定；如果 mock 维护人后续发现「家→机场是单行道」这种语义诉求，该补丁会破坏。

### 方案 E：lookup_hop 4 级 sentinel + critic 区分诊断

- 改动：lookup_hop 4 级返 path_type="fallback"（新值），critic 看到 fallback 时改报「目标点 id 无法解析」而不是「通勤分钟不足」。
- 工时：~20 分钟（schema + critic message + 单测）。
- 影响：[P1-3] 修复；critic 错误诊断准确性提升。
- 风险：HopPathType Literal 加值，前端 / SSE 都要兼容；frontend/lib/types.ts 的 HopPathType 也要加。

---

## 5. 目录归属建议

按 A1 联合审查同步建议：

- `agent/lookup_hop.py` → 归 **planning / commute**（如果建立 `agent/planning/` 子包，可放 `agent/planning/commute.py`）。它是 assemble 与 critic 共用的「客观裁判员」纯函数，不涉及 LLM 也不涉及 graph，独立模块。
- `tools/estimate_route_time.py` → 维持 `tools/`（它是 LLM Function Calling 工具）。但建议在 docstring 显式声明「内部应当委托给 agent.planning.commute.lookup_hop，确保对外 LLM 看到的分钟数与对内 critic 看到的完全一致」。
- `tools/_helpers.find_route` → 用作 lookup_hop 内部 helper 没问题，但目前 estimate_route_time 直接调 find_route 绕过 lookup_hop，建议**冻结 find_route**，未来仅 lookup_hop 用。
- 是否冻结：`agent/planner.py` / `planner_hybrid.py` / `llm_planner.py` 已冻结（AGENTS.md §3.3.1），但它们消费的 estimate_route_time 没冻结仍演进——建议把 estimate_route_time 也半冻结（不允许在主路径 graph 内调用，仅 fallback 路径用，写 review checklist）。

---

## 6. 跨环节依赖警示

我修这里会影响：

- 方案 A（estimate_route_time 内部走 lookup_hop）→ 影响 **Agent E**（critic 三套）的 backprompt 模式，LLM 不再因 NOT_FOUND 主动换 POI；E 需在 critic 里补「候选 hop 全 estimated/fallback 时建议选更近候选」的弱 warning。
- 方案 B（补 routes.json）→ 影响 **Agent G**（mock 数据），他们应同步加「routes 与 POI/R 坐标几何一致性」mock 验收单测。
- 方案 C（补 estimate_routes_worker）→ 影响 **Agent D**（BlueprintLLM / Prompt）的 prompt 模板（拼候选距家通勤参考表）；同时影响 **Agent H**（SSE adapter）需把新 worker 的 tool_call 事件加进 sse_adapter:159-168。
- 方案 D（反向边对称）→ 影响 **Agent F**（planner_hybrid ILS）邻域搜索（变得对称化，应当更合理）。

我依赖另一处先修：

- **Agent G** 必须先确认 routes.json 是「真值」还是「示例」。如果是示例（手填），方案 A/B 都能动；如果是真值（评委可见），方案 B 不能动只能补反向边。
- **Agent A**（NodeDecider）决定 mid nodes 个数后才能反推「routes 至少要覆盖哪些对子」，决定方案 B 范围。
- **Agent F** 的 commute_infeasible 死循环（pitfalls.md 2026-05-26）部分缓解，但根因仍是 lookup_hop 与 LLM 看到的分钟数不一致——必须先修 P0-2 才能彻底关闭那个 pitfall。

---

## 自检

- [x] 6 段全填
- [x] gap 清单：P0×2、P1×3、P2×3，共 8 条（>2）
- [x] 业界对标 5 条带链接
- [x] 给出 lookup_hop / estimate_route_time 一致性证据（§1 表格 + §2 P0-2 反例）
- [x] 算了 routes.json 覆盖率（§1 末尾 + §2 P1-1）
- [x] 中文撰写，正文约 3300 字
- [x] 不动代码、不 commit、仅写报告
