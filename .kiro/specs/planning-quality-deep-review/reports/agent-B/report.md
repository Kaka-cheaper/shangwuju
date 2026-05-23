# Agent B 审查报告 —— 候选搜索层（#5-9）

> 审查范围：`tools/get_user_profile.py` / `search_pois.py` / `search_restaurants.py` / `check_restaurant_availability.py` / `estimate_route_time.py` + `_helpers.py` + `registry.py` + `schemas/domain.py` + `schemas/tools.py` + `agent/blueprint_llm.py:_poi_preview / _restaurant_preview / build_candidate_preview`
> 主线追问：「家庭主线 5 岁娃博物馆主活动 2.5h（150min）」是否由「LLM 看不到 POI 推荐时长」造成？
> 结论先行：**是，铁证。`Poi.suggested_duration_minutes` 在 schema 与 mock 都存在（41 个 POI 全填），但 `_poi_preview` 这层投影把它丢了 —— LLM 拿不到这个信号，只能凭训练数据先验拍脑袋出 150min**。

---

## 1. 现状摘要（每个子环节做了什么）

```
| #  | 子环节               | 做什么                                                                  |
|----|---------------------|-----------------------------------------------------------------------|
| 5  | get_user_profile     | 按 user_id 取 persona（u_dad / u_biz / u_grandma / u_solo / u_couple）；alias `demo_user` 兜底默认 persona；其它未知 user_id 返 NOT_FOUND（保护 W1 旧测试）；返回 UserProfile（home_location / default_budget / transport_preference）|
| 6  | search_pois          | 二段过滤：第一道硬过滤（id 黑名单 / 距离 / experience_tag「任一即过」/ social_context 必须在 suitable_for / preferred_types / age_in_party 必须落入 age_range）；第二道按软优先级渐进放宽 physical_constraints（gold list 含「亲子友好」「适合 5-10 岁」「适合老人」「无台阶」最后才丢）；按 rating 降序取前 limit；候选源支持 NearbySearchProvider（带 lat/lng）或回退到 mock 预填 distance_km |
| 7  | search_restaurants   | 同 #6 二段结构，针对 dietary 渐进放宽；附加桌型过滤（capacity_requirement → two/four/six/eight）和 require_private_room；同样按 rating 降序取前 limit |
| 8  | check_restaurant_avail | 按 restaurant_id+time 查 reservation_slots；不存在 → NOT_FOUND；slot.available=false → RESTAURANT_FULL 并尝试给 suggested_alternative_time（先选 later，否则选 earlier 中最大的）；party_size 仅日志透传，不参与桌型校验 |
| 9  | estimate_route_time  | 调 `_helpers.find_route` 在 routes.json 里精确匹配 from_location / to_location；找不到 → NOT_FOUND；不做距离超限判断（让 Agent 编排层决定） |
```

注册侧（`tools/registry.py`）已统一了 invoke_tool 流程：Pydantic 二次校验输入 + 输出，duration_ms 进 SSE，非常规范。问题不在编排层，而在 **Tool 给上层 LLM 喂的字段集合**。

---

## 2. 业务合理性 gap 清单（按 P0/P1/P2 + 配反例）

### P0（demo 立刻翻车）

#### [P0-1] `_poi_preview` 漏掉 `suggested_duration_minutes` —— 5 岁娃 2.5h 博物馆的直接根因

- **现象**：用户实测 demo「家庭主线 5 岁娃」，主活动博物馆 LLM 给出 `duration_min=150`（2.5h）。行业常识 60-90min（见 §3 业界对标）。
- **根因（铁证三连）**：
  1. `backend/schemas/domain.py:117-120` 已定义 `Poi.suggested_duration_minutes: Optional[NonNegativeInt]`
  2. `mock_data/pois.json` 41 条 POI 全部填了该字段（grep 命中 41 条；含 P003「城市儿童博物馆」= 90 / P040「无障碍亲子博物馆 · 三代同堂友好馆」= 90）
  3. **`backend/agent/blueprint_llm.py:92-106` 的 `_poi_preview` 字段清单里偏偏没有这一字段**：仅暴露 id / name / type / tags / suitable_for / distance_km / opening_hours / rating / age_range / price_range / review_excerpts。
- **反例**：输入「带 5 岁娃下午活动」，候选 P003（亲子博物馆，mock 标 90min）。LLM 看到的 preview 不含时长信号，按训练先验自填 150min；下游 critic 不验证「是否合理」，只验证总时长 / 时序 / 营业时间，全部能过。结果 demo 现场出现 2.5h 博物馆，评委一眼出戏。
- **修复方向**：`_poi_preview` 直接补 `"suggested_duration_minutes": p.suggested_duration_minutes`；blueprint_prompt 加一行「如候选含 suggested_duration_minutes 字段，duration_min 必须取该值或在 ±25% 区间内」。这是 **5 行代码就能堵的最高 ROI 修复**。

#### [P0-2] `Restaurant` 完全没有「典型用餐时长」字段 —— 餐厅 60min 全靠默认常量

- **现象**：`search_restaurants` / `check_restaurant_availability` 都不喂 LLM 用餐时长信号；`agent/planner.py:97` 写死 `DEFAULT_DINING_MINUTES=90`，blueprint LLM 自己给 60。粤菜婚宴桌（应 90-120）和沙拉简餐（应 30-45）全被一把按 60 处理。
- **根因**：`schemas/domain.py:Restaurant` 缺 `typical_dining_min` 字段；mock 数据也没有。`_restaurant_preview`（blueprint_llm.py:108-121）暴露 id / name / cuisine / tags / suitable_for / distance_km / opening_hours / avg_price / rating / review_excerpts —— 同样无时长信号。
- **反例**：S8 祖父母 70 大寿粤菜 6 人桌，6 人粤菜实际 90-120min，LLM 给 60min → critic 不知道这有问题 → 时间轴显示「18:00-19:00 用餐」，70 岁老人刚上叉烧包就被推下桌。
- **修复方向**：`Restaurant` 加 `typical_dining_min: Optional[NonNegativeInt]`；按 cuisine 给默认（健康轻食 30 / 咖啡馆 45 / 日料 60 / 粤菜 90 / 火锅 120 / 商务正餐 90）；`_restaurant_preview` 暴露；prompt 加同款约束。

#### [P0-3] `Poi.suggested_duration_minutes` 没有「年龄段差异化」语义

- **现象**：mock 中 P003「城市儿童博物馆」=90、P040「无障碍亲子博物馆 · 三代同堂友好馆」=90、P038（150）等。但同一个博物馆，3 岁娃推荐 60、5-7 岁 75-90、12+ 岁陪同观察 120 —— 完全不同。当前是单值，**任何人群进来都同一时长**。
- **根因**：`enrich_mock_data.py:78-99` 按 POI type 给单一默认值（亲子博物馆=90），没有按 age_range / energy level 做矩阵。
- **反例**：3 岁娃 + 12 岁少年混团进同一个博物馆，LLM 看 90min 取了均值；3 岁娃 60min 后崩溃，12 岁少年 120min 没逛完；中位数其实是误导。
- **修复方向**：mock 字段升级为 `suggested_duration_minutes: {"adult": 90, "kid_3_6": 60, "kid_7_12": 75, "senior": 60}` 或 `suggested_duration_range: [min, typical, max]`；preview 按 `age_in_party` 主导人群投影出单值 —— 5 行 helper 即可。

### P1（用户不会立刻发现，但会侵蚀信任）

#### [P1-1] search_pois / search_restaurants 排序只看 rating，**完全不考虑约束契合度**

- **现象**：`search_pois.py:78` `candidates.sort(key=lambda p: p.rating, reverse=True)`；`search_restaurants.py:73` 同。
- **根因**：rating 是 POI 内禀属性，与「这个用户当前需求多匹配」无关。一家 4.7 分但只挂「成人安静」的茶馆，会排在 4.6 分挂全 5 个亲子 tag 的乐园前面。
- **反例**：5 岁娃家庭场景，输入命中 `tags ⊇ {亲子友好, 适合 5-10 岁}`。mock 内 P001（4.6 / 4 个亲子 tag 全中）和 P038（4.7 / 仅 1 个亲子 tag）都过过滤，rating 排序后 P038 顶到第 1。LLM 看 top_k=5 preview 选了 P038，结果亲子契合度更低。
- **修复方向**：score = α·rating + β·tag_overlap_count + γ·age_match + δ·suitable_for_match - ε·(distance_km / max_km)。`_helpers.py` 加一个 `score_candidate(poi, intent)` 函数，sort key 换成它。Foursquare / Yelp 都用 relevance score 而不是裸 rating（见 §3）。

#### [P1-2] 距离兜底放宽 +2km 后**没在 preview 里标注「实际放宽过」**

- **现象**：`planner_llm_first.py:271` 第二次调用 `search_pois` 把 `distance_max_km` +2 后 candidates 仍按原样返；preview 只在 tag 放宽时记录 `relaxed_tags`，distance 放宽不记。LLM 拿到候选不知道这些是「放宽搜索的结果」。
- **根因**：`SearchPoisOutput.relaxed_tags` 只覆盖 tag 维度；distance 放宽是 planner 层的旁路逻辑，未回写到 output。
- **反例**：用户说「5km 内」，原约束 0 候选 → planner 偷偷放到 7km 才搜到 P017。LLM 在 preview 看到 P017 distance=6.8km，按用户输入 5km 上限本应 reject，但 LLM 看到候选既然来了就用了 → demo 现场被评委追问「为什么远了」。
- **修复方向**：`SearchPoisOutput` 补 `effective_distance_max_km` 字段；planner_llm_first 兜底放宽时回写；blueprint_prompt 加「如候选 distance 超 intent.distance_max_km，rationale 必须显式说明已放宽搜索」。

#### [P1-3] `check_restaurant_availability` 的 party_size **空跑** —— mock 6 人桌满 LLM 也不会知道

- **现象**：`check_restaurant_availability.py:60-79` 注释「party_size 仅作日志透出，未来可叠加桌型校验（当前 mock 不细分）」。但 search_restaurants 已用 `capacity_requirement` 过滤过桌型 —— 这意味着 6 人需求过来的候选**理论上**都有 6 人桌；可时段满了一样要换桌型不是？
- **根因**：mock 的 `ReservationSlot` 不含「这个时段哪种桌型可用」的细分。check 时只看时段总 available。
- **反例**：S8 6 人桌粤菜，17:00 时段 available=true，但事实上 17:00 只剩 4 人桌（mock 没法表达）→ 真接入会翻车。
- **修复方向**：`ReservationSlot` 升级为 `available_capacities: list[int]`（如 `[2, 4]` 表示 2 人 4 人桌可用）；check 时与 party_size 做匹配。这是 schema 升级，工时较大，可放 P1。

#### [P1-4] `get_user_profile` 兜底语义混乱 —— alias 含 `"demo_user"` 但没含主线 5 个 persona ID

- **现象**：`_KNOWN_ALIASES = ("demo_user",)` 只白名单了 demo_user。但 docstring 注释说「未知 user_id 兜底回默认 persona（u_dad）」与代码矛盾——其实是「未知非 alias → NOT_FOUND」。
- **根因**：docstring 与实现不一致，前端一旦传入 `"u_xxxx"` 错拼或第三方接入新 persona，就会失败而非兜底。
- **反例**：前端在「家庭主线」按钮上误传了 `"u_kid"`（设计稿里有过这个 ID），get_user_profile 直接 NOT_FOUND，整个规划链路在最前端就挂掉，没有 fallback。
- **修复方向**：要么改 docstring 与实现统一（直接 NOT_FOUND），要么在 alias 列表里维护「向前兼容」的旧 ID 集合，或前端用 `available_personas` API 做强约束。

### P2（潜伏 bug、长期债）

#### [P2-1] `top_k_preview=5` 默认值在 P003 / P040 这种「同 type 候选稠密」场景下**信噪比恶化**

- **现象**：blueprint_llm.py:182 `top_k_preview=5`；mock 41 个 POI 中亲子向 ≥10 个，rating 降序后 top-5 全是亲子博物馆 / 乐园（同质度极高）。LLM 选项空间退化为「5 个同类型」，丧失了「换 type 解决问题」的机会（如 5 个博物馆都没命中娃需求时，LLM 看不到第 6 名「儿童阅读馆」）。
- **根因**：纯 rating 排序在同 type 稠密时缺 type 多样性约束。
- **修复方向**：top_k=8 + 强制 type 多样性（每 type 最多 2 条），或仿 Google Places 的「diversification」做 MMR 重排。

#### [P2-2] `_poi_preview` 暴露 `price_range` 但不暴露 `capacity.available_slots` —— 售罄信号丢失

- **现象**：mock 中 P002 / P006 / P010 / P013 是「展览售罄」失败案例（available_slots=0）。LLM 看到 preview 没这个字段，可能直接选 P002 进 blueprint，下游买票时才发现售罄 → 被迫 replan，增加 LLM 调用一轮。
- **根因**：`_poi_preview` 字段裁剪过激。
- **修复方向**：暴露 `available_slots` 或派生字段 `is_available: bool`，让 LLM 在选 target_id 阶段就避开售罄项。注意：search_pois.py 模块 docstring 第 11 行声明「售罄状态体现在 capacity.available_slots=0，但仍可作为候选返回」—— 这个设计取舍可保留，但**至少在 preview 里给 LLM 看到状态**。

#### [P2-3] `estimate_route_time` 完全依赖 routes.json 精确匹配，POI↔POI 未预生成路线时直接 NOT_FOUND

- **现象**：`_helpers.find_route` 用 `from_location == ... and to_location == ...` 精确比对。新加 POI 必须显式生成与所有其他 POI 的双向路线，否则 NOT_FOUND。
- **根因**：mock 数据债。pitfalls P2-2026-05-22 已记录「routes.json 暂保留作为 mock 已校准时段优先源，但 planner 已切到 haversine 兜底」—— 但 estimate_route_time **没切到 haversine 兜底**，仍是单数据源。
- **修复方向**：与 lookup_hop 对齐，加 haversine fallback。

#### [P2-4] `search_pois.exclude_visited_ids` 是 Tool 入参但 caller 未必从 memory_store 读取 —— 责任分散

- **现象**：search_pois.py:81 `excluded = set(inp.exclude_visited_ids or [])`。Tool 自己不查 memory；调用方负责传。但 `planner_llm_first._query_pois` 没传，只有 ReAct 路径会传。
- **根因**：调用路径不一致 → demo 切到 LangGraph 主路径后，「已访问 POI 不重复推荐」承诺可能落空。
- **修复方向**：要么 Tool 自己接 memory（违反单一职责），要么明确每条调用路径都强制透传。pitfalls 已记 alias 漂移类问题，本条同源。

---

## 3. 业界对标 diff

### 对标 1：Google Maps / Places API —— `visit_duration` 是一等字段

> [About popular times, wait times & visit duration data](https://support.google.com/business/answer/6263531?hl=en) — Google Business 显示「Visit duration : This data shows how much time customers typically spend at your location.」

- **他们怎么做**：Google 把「visit duration」与 popular_times 并列为商家 metadata 一等字段；UI 上每个 POI 详情页显示「人们通常在这里停留：X-Y 小时」。Places API 通过 `currentOpeningHours` / `regularOpeningHours` / `popularTimes` 等结构化字段暴露给开发者。
- **我们差在哪**：schema 已经有 `suggested_duration_minutes`，**但是 LLM 看不到** —— 等于建了水管不通水。Google 的设计哲学是「时间维度信号必须显式上抛给应用层」，我们漏在了应用层投影。
- **借鉴成本**：极低。`_poi_preview` 加 1 行；prompt 加 1 句约束。

### 对标 2：Foursquare Places API —— `attributes` + `parts_of_day` 让客户端按场景挑选

> [Foursquare Response Fields](https://docs.foursquare.com/reference/response-fields) — Places Rich Data 含 attributes / hours_popular / parts_of_day / popularity 等结构化字段

- **他们怎么做**：venue 文档暴露 `attributes`（如 outdoor_seating / good_for_kids / parts_of_day）+ `popularity`（0-1 归一化分），让客户端按场景做相关性 ranking。restaurants 还会带 `dining_duration` 类型的 attribute（按菜系区分）。
- **我们差在哪**：① 没有 `typical_dining_min` 字段；② search_restaurants 只按 rating 排，没 popularity / relevance 概念；③ POI / Restaurant 的 `tags` 是混合词袋而非结构化 attributes（不能按维度查询）。
- **借鉴成本**：中等。dining_min 字段补很容易（30min 工时）；relevance score 重写排序中等（90min 工时）。

### 对标 3：TripAdvisor Content API —— Attractions 含「平均访问时长」与「分级建议」

> [Tripadvisor Content API - Location Details](https://tripadvisor-content-api.readme.io/reference/overview) — Locations 包含 hotels / restaurants / attractions 三类，每类有专属字段集

- **他们怎么做**：Attractions location 详情含「Things to Know」「Suggested duration」「Recommendations from」「Suitable for」等结构化字段；UI 显示「建议时长：1-2 小时」是标配。Content API 不只给 review，给 **structured trip planning hints**。
- **我们差在哪**：mock 字段密度其实够了（`suitable_for` / `age_range` / `tags` / `suggested_duration_minutes` 已具备 TripAdvisor 同款），但**预览层没传给 LLM**。等于把 TripAdvisor 数据下载下来了又自己删掉。
- **借鉴成本**：极低（同对标 1）。

### 对标 4：TravelPlanner 基准（ICML'24，OSU-NLP-Group）—— attractions 参考字段集

> [TravelPlanner: A Benchmark for Real-World Planning with Language Agents](https://osu-nlp-group.github.io/TravelPlanner/) + [HF dataset](https://huggingface.co/datasets/osunlp/TravelPlanner)

- **他们怎么做**：每个 attraction 的 reference data 至少包含 Name / Latitude / Longitude / Address / Phone / Website / City。论文核心论点：LLM 规划失败大头来自 **information gathering**（候选数据稀疏 / 字段缺失）而非推理。
- **我们差在哪**：我们 candidate preview 完全没 lat/lng / phone / website（demo 不需要这些；但说明「候选数据维度」是规划质量的瓶颈，TravelPlanner 论文已用大规模实验证明）。
- **借鉴成本**：取舍即可，不必硬加。但「information gathering 影响规划质量」的判断对 P0-1 是补强论据。

### 对标 5：行业实证 —— 5 岁娃博物馆 60-90min 是公认基线

> Smithsonian SEEC：[「20-25 minutes with a preschooler successful」](https://americanhistory.si.edu/blog/2013/12/top-tips-for-a-rewarding-museum-visit-with-kids.html)；同馆官方 FAQ「Many visitors find that about two hours is the right amount of time」 + 「plan to take lots of breaks, especially if you have very young children」。
> Hands-On House Children's Museum：[「Group visits have an allotted time of 90 minutes inside the museum, with an additional 60 minutes outside」](https://handsonhouse.org/for-schools/field-trips.html)。
> Deutschlandmuseum：[「experience 2,000 years of history in just 60 minutes」](https://www.deutschlandmuseum.de/en/kids/)。

- **他们怎么做**：儿童博物馆官方文案普遍把「60-90 分钟」作为亲子参观推荐时长，Smithsonian 进一步给到学龄前 20-25 分钟单点连续注意力 + 多次 break 才能凑到 2h（含休息）。
- **我们差在哪**：mock 数据 P003 = 90min 正好踩在行业中位线。LLM 输出 150min（2.5h）= 行业 max（含 break）的高位 → 5 岁娃**净参观时间**仍超 60min 阈值。
- **借鉴成本**：用作 P0-1 的反例锚点，无需开发动作。

---

## 4. 修复方案候选（每条带工时 + 跨环节依赖）

> 总策略：P0 必上、P1 选 1-2 条入下个 spec、P2 进 backlog。

### 方案 A：candidate preview 字段补全（P0 必修，最高 ROI）

- **动作**：
  1. `_poi_preview` 加 `suggested_duration_minutes` + `available_slots`（共 2 行）
  2. `_restaurant_preview` 加 `typical_dining_min`（schema 先扩字段，2 行）
  3. `Restaurant` schema 加 `typical_dining_min: Optional[NonNegativeInt]`
  4. `mock_data/restaurants.json` 按 cuisine 批量回填（脚本 `enrich_mock_data.py` 加 `_RESTAURANT_CUISINE_DURATION` dict）
  5. `blueprint_prompt.py` 加一段：「target 的 suggested_duration_minutes / typical_dining_min 字段是参考时长，duration_min 必须取该值或在 ±25% 区间内（如博物馆 90 → [70, 110]）。如有特殊场景偏离请在 rationale 解释」
- **工时**：~60 分钟（含 mock 回填 + 测试）
- **影响子环节**：#6 #7（preview）；#11 #12（blueprint LLM）；#23（mock）；间接 #13 #14（critic 可选加 duration 合理性 critic）
- **风险**：极低；现有测试已覆盖 preview 形态（test_blueprint_llm.py:113），增字段是 superset 不破。

### 方案 B：search_pois / search_restaurants 排序升级为相关性分

- **动作**：`_helpers.py` 加 `score_candidate(item, intent) -> float`；search Tool sort key 切换。
- **公式**：`score = 0.4·rating/5 + 0.3·tag_overlap_ratio + 0.15·age_match + 0.1·suitable_for_match + 0.05·(1 - distance/max_distance)`
- **工时**：~90 分钟（含单测 5 条）
- **影响子环节**：#6 #7；间接 #11（preview top-5 选什么变了）
- **风险**：低，但需回归测：现有 e2e 多个测试断言「P040 在前」可能被打破，需要按新分数微调。

### 方案 C：suggested_duration 升级为年龄段矩阵

- **动作**：mock 字段从单值升级为 `{"default": 90, "kid_3_6": 60, "kid_7_12": 75, "senior": 60}`；schema 改为 `dict[str, NonNegativeInt]`；preview 按 `intent.age_in_party` 主导桶投影。
- **工时**：~120 分钟（含 41 条 mock 回填策略 + helper + 测试）
- **影响子环节**：#6 #11 #12 #23
- **风险**：中。mock 写多了不易维护；建议用脚本按 type+age_range 自动推导。

### 方案 D：preview 加「relaxed signals」让 LLM 看到放宽路径

- **动作**：`SearchPoisOutput` 加 `effective_distance_max_km` / `relaxed_distance: bool`；planner 兜底放宽时回写；preview 透传给 LLM；prompt 加约束「如 effective_distance > intent.distance_max_km，rationale 必须明示」
- **工时**：~45 分钟
- **影响子环节**：#6 #7；间接 #11（rationale 要求变了）
- **风险**：低。

### 方案 E（建议作为新 spec）：餐厅时段精细化

- **动作**：`ReservationSlot.available_capacities: list[int]`；`check_restaurant_availability` 校验 party_size 与 available_capacities；mock 回填。
- **工时**：~3 小时
- **影响子环节**：#7 #8 #23
- **风险**：中。schema 升级牵动多处测试。
- **建议**：进 P1 但非本轮修复（demo 当前 5 岁娃主线 party_size=3 用 4 人桌，不会立即触发）。

---

## 5. 目录归属建议（A1 融合）

```
| 文件                                        | 建议归属    | 备注                                          |
|--------------------------------------------|------------|---------------------------------------------|
| backend/tools/get_user_profile.py           | tools/profile/  | 与 backend/data/memory_store.py 强耦合，可考虑做成 profile 子模块 |
| backend/tools/search_pois.py                | tools/discovery/| 与 search_restaurants 同源，命名更准确 |
| backend/tools/search_restaurants.py         | tools/discovery/| 同上 |
| backend/tools/check_restaurant_availability.py | tools/availability/ | 查询类但带可订状态，独立子模块 |
| backend/tools/estimate_route_time.py        | tools/routing/  | 与 lookup_hop 紧密配合，可考虑合并 |
| backend/tools/_helpers.py                   | tools/_shared/  | 内部共享 |
| backend/tools/registry.py                   | tools/        | 顶层入口，不动 |
```

合并 / 删除建议：
- `_helpers.relax_tag_search` 与 `planner.py` 的 `distance_max_km +2` fallback 链是「相关性放宽」的两个不同维度，目前分散在两层。建议在 `tools/_shared/` 抽出统一的 `RelaxationStrategy` —— 但**这是 P2，不在本轮 demo 修复路径**。
- `estimate_route_time` 与 `agent/lookup_hop` 都是「两点间时间估算」入口，前者 Tool 形态后者 Python 函数形态；目录重组时建议明确「Tool 是给 LLM 调的，lookup_hop 是给 assemble 调的」并互引。

冻结建议：本轮**不冻结**任何 Tool；它们都是活跃演进路径。

---

## 6. 跨环节依赖警示（你看到但其他 agent 看不到的）

### 我修这里会影响

- **#11 BlueprintLLM / #12 BlueprintPrompt（Agent D 范围）**：方案 A 的 `_poi_preview` 加字段直接改了 Agent D 看到的 candidates_json 形态。Agent D 必须同时调整 prompt 加「duration_min 必须参考 suggested_duration_minutes」约束，否则字段塞进去 LLM 也不会用。
- **#13 BlueprintCritic / #14 CriticsV2（Agent E 范围）**：建议 Agent E 评估「是否新增 `_duration_reasonable_critic`：检查 node.duration_min 与 candidate.suggested_duration_minutes 偏差 > 50%」。这是把 P0-1 的修复闭环。当前 critic 只验证「时序无重叠 / 营业时间覆盖 / 总时长不超 24h」，**没有任何「单段时长合理性」校验** —— 这是 demo 翻车的真实原因之一（critic 不挡）。
- **#23 mock POI / Restaurant（Agent G 范围）**：方案 A 的 Restaurant.typical_dining_min / 方案 C 的 age 矩阵都是 mock schema 升级，**Agent G 必须主导回填**。建议两个 agent 报告联合产出 mock 数据迁移脚本。
- **#22 ExecuteFinalize（Agent H 范围）**：候选预览暴露 `available_slots` 后，LLM 选 target_id 时会主动避开售罄 → 下游 buy_ticket 触发 ticket_sold_out 异常的概率降低。Agent H 应注意：演示场景集 §四 自检「至少 1 次显式触发异常并恢复」可能反而难触发；需要在 mock 里更精准设计「LLM 看不到的售罄」（比如 buy_ticket 阶段动态置 0）。

### 我依赖另一处先修

- **#23 mock 数据先扩 `Restaurant.typical_dining_min`**（Agent G 主导）：方案 A 步骤 3-4 必须先有 mock 数据，否则字段是 None，preview 输出 None 反而误导 LLM。
- **#10 WeightsLLM（Agent D 范围）**：如果方案 B 的相关性分公式参数（α/β/γ/δ）希望由 LLM 动态决定（按场景调权），需要 weights_llm 增配「ranking weights」字段。当前先用硬编码即可。
- **#1-4 意图层（Agent A 范围）**：方案 C 的「年龄段主导桶」需要 IntentExtraction.age_in_party 字段已正确填充。当前 schema 已支持，但 Agent A 需确认意图解析在「带 5 岁娃」场景能正确填 [5] 而不是漏字段。

### 关键观察（联合审查可参考）

- **业务 gap 的根因 80% 在 schema/preview 层**：候选搜索本身（filtering / capacity check / route lookup）逻辑健全，真正卡点是 **「候选搜索拿到了好数据，blueprint LLM 看到的是阉割版」**。修这一层（P0-1, P0-2, P0-3）的边际收益远高于改 ranking 算法（P1-1）或调 critic（属 Agent E）。
- **修 5 行代码就能堵 demo 最显眼翻车**：`_poi_preview` 补 1 行 + prompt 加 1 句约束，5 岁娃 2.5h 博物馆问题立即从「LLM 拍脑袋」变为「LLM 有锚点拍脑袋」，期望偏差从 ±70min 收敛到 ±20min。这是本次 review 的最高优先级动作项，建议 Phase 4 联合审查时直接列入「立即修」清单。
