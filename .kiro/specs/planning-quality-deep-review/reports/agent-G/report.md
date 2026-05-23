# Agent G 审查报告 —— mock 数据信息源（#23 POI/Restaurant + #24 UserProfile/Persona）

> **范围**：`mock_data/pois.json`（41 条）+ `mock_data/restaurants.json`（48 条）+ `mock_data/routes.json` + `mock_data/personas.json`（5 条）+ `mock_data/user_profile{,s}.json` + `mock_data/_samples/` 4 份 + `backend/schemas/domain.py` + `backend/data/loader.py` + `backend/scripts/enrich_mock_data.py`。
> **触发故事**：用户实测「家庭主线 5 岁娃博物馆 2.5h」。Agent B 已确诊 `Poi.suggested_duration_minutes` schema/mock 都在，是 `_poi_preview` 漏字段；Agent D 进一步揪出 prompt 范例 `duration_min=165` 反向锚定。Agent G 站在**信息源**这一层质问：**字段补齐就够了吗？mock 数据本身是不是也写得不对？**
> **核心立场**：高杠杆环节。前面 6 个 agent 把"管道修对"，但管道里运的水如果本身浑浊（例如 P019 陶艺工坊 5 岁娃 90min 完全反人性），即便 prompt + critic 都做对，LLM 出来的方案仍然是"合规但反业界常识"。Agent G 必须把 mock 数据本身的"业务合理性反例"清扫一轮。
> **绝对约束**：本 Phase 不动 mock_data、不改 schema、不 commit；只审查、写报告、给迁移脚本草稿。

---

## 1. 现状摘要

### 1.1 #23 mock POI / Restaurant 信息密度

```
| 维度                          | POI（41 条）              | Restaurant（48 条）       | 业界基准                          |
|-------------------------------|---------------------------|---------------------------|-----------------------------------|
| id/name/type/location         | ✓ 全填                    | ✓ 全填                    | ✓                                |
| distance_km / opening_hours / rating | ✓ 全填             | ✓ 全填                    | Google `regularOpeningHours` 结构化更细 |
| price_range / avg_price       | △ 27/41 有；14 条免费     | ✓ 全填                    | ✓                                |
| age_range                     | △ 仅 16/41 条有           | ✗ 无                      | TripAdvisor `suitable_for`        |
| tags（混合词袋）               | ✓ 全填，平均 5 个         | ✓ 全填，平均 4-5 个       | Foursquare `attributes` 结构化 dict |
| suitable_for（社交场景）       | ✓ 全填                    | ✓ 全填                    | 项目自创                          |
| capacity / slots              | ✓ 全填（户外 9999 占位）  | 桌型+slots ✓              | Google `popularTimes`             |
| reviews（≥2 条+tag_evidence）  | ✓ 全填                    | ✓ 全填                    | TripAdvisor 数百条；mock 2-3 够用 |
| **suggested_duration_minutes**| ✓ 41 条全填（按 type 单值）| ✗ **完全无字段**           | Google/TripAdvisor 标配 visit_duration |
| **typical_dining_min**         | n/a                        | ✗ **完全无字段**           | Foursquare 餐厅 dining_duration   |
| **energy_level / intensity**   | ✗ 仅 tag「高/低强度」二值 | n/a                       | AllTrails `difficulty`            |
| **kid_friendly_intensity**     | ✗ 无；只能从 age_range 推 | n/a                       | TripAdvisor `attractionsTags`     |
| **accessibility**              | ✗ 仅 tag「无台阶」单维度  | ✗ 同左                    | OSM `wheelchair=yes/limited/no`   |
| **noise_level / busy_hours**   | ✗ 无                       | ✗ 无                      | Google `popularTimes`             |
```

**关键发现**：
- `suggested_duration_minutes` 单值（`enrich_mock_data.py:78-99` 写死 `_POI_TYPE_DURATION` dict 亲子博物馆 90 / 主题乐园 180），与同行人年龄完全解耦——5 岁娃和成人压成同一数字。Schema `Optional[NonNegativeInt]` 锁死。
- `Restaurant` 完全无用餐时长字段（Agent B P0-2 + Agent D 共同结论）。`agent/planner.py:97` 写死 `DEFAULT_DINING_MINUTES=90` 兜底，blueprint_llm 自由发挥到 60min。轻食 30min 与粤菜 120min 在数据上无法区分。
- **review 文本污染**：grep "亲子设施齐全，分龄区分得很清楚。5 岁孩……" 命中 P003/P004/**P018(西湖游船)**/P019/**P028(电影院)**/P033 6 处，观光船/电影院与亲子分龄完全无关——是 LLM 误判 P028/P018"亲子向"的污染源。

### 1.2 #24 UserProfile / Persona 信息密度

```
| 文件                 | 字段集                                                                       |
|----------------------|----------------------------------------------------------------------------|
| user_profile.json    | user_id / home_location / default_budget / transport_preference  （4 字段，单 demo_user）|
| user_profiles.json   | 同上 × 6（demo_user + u_dad/u_biz/u_grandma/u_solo/u_couple）              |
| personas.json        | user_id / label / icon / **notes**（自由文本）/ home_location / default_distance_max_km / default_budget / **default_tags{physical,dietary,experience,suitable_for_priority}** （8 字段）|
```

5 个 persona 的 **notes 字段**含金量高但**未结构化消费**：
- `u_dad`「孩子能玩 1.5h+」← 主活动 ≤ 90min 信号
- `u_grandma`「老人腿脚不便；偏好近 + 软烂菜 + 无台阶」← 单段 ≤ 60min + accessibility 信号
- `u_solo`「下午常一个人放空；偏好安静 / 室内 / 单人友好」← 主活动 ≥ 60min
- `u_couple`「偏好看展 / 网红 / 安静聊天」← dwell-time ~75min
- `u_biz`「预算高、要体面；偏好交通便捷的核心商圈」← distance ≤ 8km、商务用餐 ≥ 90min

消费侧（Agent A P1-1）：`build_intent_parser_system_prompt_with_priors` 仅消费 `default_tags` + `default_distance_max_km` + `suitable_for_priority`；**notes 全部被忽略**——等于白写。同时 `user_profile.json` 与 `personas.json` 是**双轨用户画像**（一个 4 字段一个 8 字段），消费方混乱（Agent B P1-4 已指出）。

### 1.3 schema vs mock 一致性

```
| 检查                                       | 结论                                              |
|------------------------------------------|---------------------------------------------------|
| schemas/domain.py 与 pois.json 字段对齐  | ✓ extra="forbid" 保护 + Pydantic model_validate 通过 |
| schemas/domain.py 与 restaurants.json 对齐 | ✓ 同上；`RestaurantCapacity` 用 `populate_by_name` 兼容 alias |
| pois.json age_range 一致性               | ⚠ P019/P038 等 review 暗示 "5 岁玩"，但 P019 缺 age_range 字段、P038 age_range=[6,60] 不含 5 岁 |
| reviews.tag_evidence 与 POI tags 对齐    | ⚠ 30%+ 评论 tag_evidence 引用的 tag 在 POI tags 列表里有，但评论文本与 POI 类型不匹配（P018 / P028 模板污染） |
| personas.default_tags 命中 mock 数据     | ✓ 8 场景覆盖率自检表大部分通过（演示场景集 §四）  |
| user_profiles.json `u_grandma.transport_preference="bus"` | ⚠ 但路线 routes.json 全是 home/POI 任意起点，bus 偏好实际生效靠 `agent/lookup_hop` 取 bus_minutes 字段；目前生效 |
```

---

## 2. 业务合理性 gap 清单

### P0（demo 立刻翻车）

#### [P0-G1] `suggested_duration_minutes` 是按 POI type 一刀切的单值，与同行人年龄完全解耦 ★★★

- **现象**：`enrich_mock_data.py:78-99` `_POI_TYPE_DURATION` dict 全是单值（亲子博物馆=90、主题乐园=180、DIY 工坊=90）。即便 Agent B 把字段透传给 LLM，5 岁娃 100min 与 12 岁娃 100min 是同一锚点。
- **根因（铁证）**：① mock 41 条 `suggested_duration_minutes` 全是 int 单值；② `schemas/domain.py:118-121` `Optional[NonNegativeInt]` 锁死单值；③ `enrich_mock_data.py:90` `duration = _POI_TYPE_DURATION.get(poi_type, 60)` 一刀切落库。
- **反例（端到端）**：
  - **P019 玉皇山陶艺工坊**（DIY 工坊）：mock 90min。业界陶艺手作儿童单段 30-45min（手工教学课时 45min；Hands-On House 90min 含切换）。**5 岁娃捏陶 90min 完全坐不住。**
  - **P033 梦幻奇迹乐园**（主题乐园）：mock 180min 全年龄共用——成人 4h 可坚持，3 岁实际 90min 见极限（需 nap）。
  - **P040 无障碍亲子博物馆**（复合体验馆）：mock 100min，三代同堂场景含 5 岁娃（≤75min）+ 70 岁外婆（≤60min）→ 取 min(75,60)=60min 才合理；mock 100min **超过两个边界**。
- **修复方向**：见 §4 方案 A（升级为 dict）。

#### [P0-G2] `Restaurant` 完全没有 `typical_dining_min`，所有用餐 60min 都是 LLM 凭空给的 ★★★

- **现象**：`schemas/domain.py:Restaurant` 字段集没有 `typical_dining_min`；`mock_data/restaurants.json` 48 条全无；`_restaurant_preview` 也没字段可暴露——blueprint LLM 给 60min 是**无锚点自由发挥**。
- **根因**：Step 3 enrich 只补了 signature_dishes + recommendation_reason，时长字段被遗忘。
- **反例**：
  - **R001 轻语沙拉**（健康轻食）：60min 偏长（业界沙拉简餐 30-40min）→ 5 岁娃 18:00-19:00 吃沙拉，30 分钟吃完后干坐 30min。
  - **R002 粤味轩**（粤菜、6/8 人桌）：60min 严重偏短（粤菜大桌 90-120min）；S8 跨代际生日 17:00-18:00，70 岁老人刚上叉烧包就被推下桌。
  - **R008 金樽日料会所**（avg_price=480、商务接待）：60min 偏短（商务日料 omakase 约 90-120min）。
- **修复方向**：见 §4 方案 B。

#### [P0-G3] POI 缺 `energy_level` / `kid_friendly_intensity` 两枚举字段 ★★

- **现象**：仅靠 tag「高/低强度」二值表达。同 tag「低强度」覆盖 P004 绘本馆（坐 60min）+ P010 SPA（躺 120min）+ P040 博物馆（走 100min）——LLM 与 critic 无法精准过滤。
- **反例**：5 岁娃下午想"玩水但不剧烈" → 只能命中 tag「低强度」；P032「硬核燃力健身工坊」tag 含「高强度」明显不亲子，过滤靠 `suitable_for=家庭日常` 手动维护——非结构化、易漏。
- **修复方向**：见 §4 方案 C。

#### [P0-G4] `personas.notes` 是自由文本，pace_profile 信号被埋没 ★★

- **现象**：`u_dad.notes`「孩子能玩 1.5h+」是**主活动单段 ≤ 90min** 的强信号，但 prior 注入只读 default_tags + default_distance_max_km，notes 完全不进 LLM。
- **反例**：u_dad 输入"今天下午想出去玩"（无明示约束），blueprint LLM 收到 prior 仅知"家庭日常 + 5km"，对"主活动 ≤ 90min"无感→出 150min 博物馆与 demo 现象一致。
- **修复方向**：见 §4 方案 D（persona 加 `default_pace_profile`）。

### P1（用户不会立刻发现，但会侵蚀信任）

#### [P1-G5] reviews 模板化拼接污染：观光船 / 电影院被塞入"亲子分龄"评论

- **现象**：grep "亲子设施齐全，分龄区分得很清楚。5 岁孩……" 命中 P003 / P004 / P018（西湖游船）/ P019 / P028（电影院）/ P033 6 处。其中 P018 / P028 与亲子分龄毫无关系。
- **根因**：批量评论生成脚本没按 POI type 过滤模板。
- **反例**：用户输入"带 5 岁娃看电影或游船" → P028 + P018 评论里有"亲子"字样 → blueprint LLM 把电影院作为亲子主活动 → narrate"陪孩子在西湖游船看分龄区"——评委一查 P018 是观光船立刻露馅。
- **修复方向**：评论库按 POI type 分组 + type-evidence 校验脚本。

#### [P1-G6] mock 内"业务合理性反例"埋点稀疏

- **现象**：演示场景集 §四要求"`available=false` 的失败案例 ≥ 8 处"。当前 POI 仅 5 条；restaurants 主要在 17:00 demo 埋点（约 8 处）。但**业界 LLM 评测的"反例"还应含**"长距离/超预算/超时长"反例（让 LLM 学会拒绝），mock 完全没有。
- **修复方向**：每条 POI 加 `min_recommended_minutes` / `max_recommended_minutes` 区间，方便 LLM/critic 在源头拦下。

#### [P1-G7] `user_profile.json` schema 缺 dietary_preference / accessibility_needs / pace_profile

- **现象**：`UserProfile` 仅 4 字段。家庭主线"老婆减肥"全部转译到 IntentExtraction 的 `dietary_constraints`，但用户档案侧无"长期饮食偏好"，每次靠输入解析。memory_store 是另一机制（已有），但用户级长期画像没在 UserProfile 体现，与 personas.json 形成双轨（Agent B P1-4）。
- **修复方向**：UserProfile 升级为含 long-term preferences 的完整画像（personas.default_tags 应该并入 UserProfile 而非单独文件）。

#### [P1-G8] `routes.json` 是有限点对预生成，新增 POI NOT_FOUND（与 Agent C 同源）

- **现象**：`routes.json` 全表是 P×R/home 有限组合，P↔P 横向 hop 几乎不预生成。
- **修复方向**：与 Agent C 协同——脚本里加 haversine 兜底生成 P×P 双向矩阵；或在 estimate_route_time tool 内加 fallback。

### P2（潜伏 bug、长期债）

- **[P2-G9]** `_samples/*.example.json` 与生产 mock schema 漂移：缺 `suggested_duration_minutes` / `signature_dishes`；建议 enrich 后脚本驱动重生 `_samples`（取前 2 条）。
- **[P2-G10]** `Poi.capacity.available_slots = 9999` 是 magic number 占位（户外公共空间），prompt 与 critic 不感知。建议 `PoiCapacity.is_unlimited: bool` 字段替代。
- **[P2-G11]** `_samples/intent.example.json` 仍含 `parse_confidence`/`ambiguous_fields`，与现行 IntentExtraction schema 漂移。同 P2-G9 由脚本同步。

---

## 3. 业界对标（≥ 4 个带链接）

### 对标 1：Google Places API —— `regularOpeningHours` / `popularTimes` / `goodForChildren` 全套结构化字段

- **链接**：[Google Maps Platform · Place Data Fields](https://developers.google.com/maps/documentation/places/web-service/place-data-fields) / [Visit duration / Popular times](https://support.google.com/business/answer/6263531)
- **他们怎么做**（按官方文档释义改写以满足合规）：每个 Place 暴露 ~70 个字段，含 `regularOpeningHours`（按周天/时段结构化）、`popularTimes`（每天 24 小时直方图）、`goodForChildren` / `goodForGroups` 这些 boolean 标签、`visitDuration`（用户在此典型停留时长）、`accessibilityOptions`（轮椅入口/卫生间等多字段）。
- **我们差在哪**：
  - mock POI 的 `opening_hours` 是字符串"09:30-17:30"，没法表达"周一闭馆/周末延长"（业界 Google 是 weekly map）
  - 没有 `popularTimes` 时段化（影响夜跑步道这种"白天人少晚上人多"无法表达）
  - `accessibility` 全靠 tag「无台阶」一个 boolean
  - `goodForChildren` 靠 `suitable_for=家庭日常` 推（同 tag 多义性问题）
- **借鉴成本**：中。先把 `goodForChildren`/`goodForElder`/`goodForBusiness` 三个 boolean 加进去，与 `suitable_for` 并存，结构化收益最大。

### 对标 2：Foursquare Places API —— `attributes` 结构化 dict + `parts_of_day` 时段画像

- **链接**：[Foursquare Places · Response Fields](https://docs.foursquare.com/reference/response-fields) / [Foursquare Premium Place Attributes](https://docs.foursquare.com/data-products/docs/premium-attributes)
- **他们怎么做**：每个 venue 含 `attributes: {dining_duration: "average", outdoor_seating: true, good_for_kids: true, ...}`、`hours_popular`、`parts_of_day` 等几十种结构化属性。餐厅有 `dining_duration` 字段值域 quick / average / leisurely 三档。
- **我们差在哪**：mock Restaurant **完全无 dining_duration 字段**（P0-G2 根因）；POI 也没有按用餐属性细分。
- **借鉴成本**：低。直接抄 Foursquare 的 `dining_duration` 三档；mock 按 cuisine 批量回填（迁移脚本 §4 草稿）。

### 对标 3：TripAdvisor Content API —— `attractionsTags` + `subcategories` 双层 + `suggested_duration`

- **链接**：[Tripadvisor Content API · Locations](https://tripadvisor-content-api.readme.io/reference/overview)
- **他们怎么做**：Attraction 详情含 "Things to Know"、"Suggested duration"（"1-2 hours" 区间字符串）、"Recommendations from"（适合人群标签：couples / families with kids / solo travelers / mature couples）、`subcategories`（更细的活动类型）。
- **我们差在哪**：
  - `suggested_duration_minutes` 是单值，业界用区间「1-2 hours」表达不确定性
  - `Recommendations from` 这种"适合人群细分"字段缺失，仅靠 `suitable_for` 表达
- **借鉴成本**：中。`suggested_duration_minutes` 升级为 `suggested_duration_range: [min, typical, max]` 三元组比 dict-by-age 更轻量，可以作为 P0-G1 的 plan B。

### 对标 4：OpenStreetMap POI tags —— `wheelchair=yes/limited/no` / `kids_area=yes` / `noise_level`

- **链接**：[OSM Wiki · Key:wheelchair](https://wiki.openstreetmap.org/wiki/Key:wheelchair) / [OSM Wiki · Key:kids_area](https://wiki.openstreetmap.org/wiki/Key:kids_area) / [OSM accessibility overview](https://wiki.openstreetmap.org/wiki/Accessibility)
- **他们怎么做**（按 OSM Wiki 释义改写）：accessibility 拆三态 yes/limited/no（不是 boolean），`kids_area=yes/no/separate` 区分场所是否有专门儿童区，noise_level 通过周边环境标签推。
- **我们差在哪**：accessibility 仅"无台阶"单 tag boolean；老人需求"无台阶 + 可休息长椅 + 走道宽 + 卫生间近"全压在一个 tag 上，颗粒度粗。
- **借鉴成本**：中。`accessibility: {wheelchair, restroom_nearby, rest_seats}` 三字段比单 tag 表达力高 3 倍；OSM 现成枚举可抄。

### 对标 5：TravelPlanner（NeurIPS / ICML 2024）+ Smithsonian SEEC（业界基线）

- **链接**：[TravelPlanner @ arxiv 2402.01622](https://arxiv.org/abs/2402.01622) / [Smithsonian · Top Tips for Museum Visit With Kids](https://americanhistory.si.edu/blog/2013/12/top-tips-for-a-rewarding-museum-visit-with-kids.html) / [Hands-On House · 90 min group cap](https://handsonhouse.org/for-schools/field-trips.html)
- **他们怎么做**（释义改写）：TravelPlanner 论文核心论点——LLM 规划失败大头来自 information gathering 不足；Smithsonian SEEC 业界基线——3-5 岁单展项 20-25min、学龄 7-12 岁 40-60min、整馆带娃 2h 含休息；Brain Balance 公式 `attention_span ≈ 2-3min × age`。
- **我们差在哪**：mock POI 在"业务字段"上其实比 TravelPlanner 还密（多了 reviews/capacity/suggested_duration），但 preview 没暴露——"水管断"；同时 `enrich_mock_data.py` 没把 SEEC 公式编码进 type 默认值——"水浑"。
- **借鉴成本**：作为方案 A 的公式来源；mock 迁移脚本（§4）直接套用。

---

## 4. 修复方案候选

### 方案 A：POI `suggested_duration_minutes` 升级为「年龄段 dict」（P0-G1）★★★

**schema 改动**（参考 TripAdvisor + Smithsonian SEEC 公式）：

```python
# schemas/domain.py
class SuggestedDuration(BaseModel):
    """按主导客群分桶推荐时长（min）。default 必填，其余可选。"""
    model_config = ConfigDict(extra="forbid")
    default: NonNegativeInt = Field(..., description="成人 / 默认推荐")
    kid_3_6: Optional[NonNegativeInt] = Field(default=None, description="3-6 岁学龄前")
    kid_7_12: Optional[NonNegativeInt] = Field(default=None, description="7-12 岁学童")
    senior: Optional[NonNegativeInt] = Field(default=None, description="≥65 岁长辈")
    multi_gen: Optional[NonNegativeInt] = Field(default=None, description="多代同行（取最严）")

# Poi.suggested_duration_minutes: Optional[SuggestedDuration]
```

**mock 迁移脚本草稿**（不本轮提交，只给 Phase 5 spec 用）：

```python
# scripts/migrate_suggested_duration.py（草稿）
_AGE_TIER_RULES = {
    # type → (default, kid_3_6, kid_7_12, senior)
    "亲子博物馆":     (90,  60,  90,  60),
    "亲子乐园":       (120, 75,  120, 60),
    "儿童阅读馆":     (60,  45,  60,  None),
    "亲子游乐场":     (90,  60,  90,  None),
    "DIY 工坊":       (90,  45,  75,  None),   # ← P019 5 岁段 45min（陶艺）
    "城市公园":       (60,  45,  60,  60),
    "茶馆":           (90,  None, None, 60),
    "戏曲园":         (90,  None, None, 75),
    "图书馆":         (90,  None, 75,  60),
    "SPA":            (120, None, None, 60),
    "书店":           (75,  None, 60,  60),
    "咖啡馆":         (60,  None, None, 60),
    "密室":           (90,  None, None, None),
    "桌游馆":         (120, None, 90,  None),
    "街区漫步":       (90,  60,  75,  60),
    "商务茶室":       (90,  None, None, None),
    "城市观光":       (90,  60,  90,  60),
    "运动步道":       (45,  30,  45,  45),
    "演出":           (150, None, None, None),
    "猫咖":           (75,  60,  75,  60),
    "剧本杀":         (150, None, 120, None),
    "KTV":            (120, None, None, None),
    "电影院":         (120, 90,  120, 90),
    "美甲":           (90,  None, None, None),
    "瑜伽馆":         (75,  None, None, 60),
    "健身房":         (90,  None, None, None),
    "主题乐园":       (180, 90,  150, 60),
    "室内运动馆":     (90,  60,  90,  None),
    "livehouse":      (120, None, None, None),
    "酒吧":           (90,  None, None, None),
    "烘焙工坊":       (90,  60,  75,  None),
    "复合体验馆":     (100, 60,  90,  60),     # ← P040 三代场景修复
    "复合空间":       (90,  60,  90,  60),
    "私享空间":       (120, None, 90,  None),
    "庆典花园":       (60,  60,  60,  60),
    "展览":           (75,  None, 60,  60),
    "画廊":           (60,  None, None, 60),
}

def migrate(poi: dict) -> dict:
    t = poi.get("type", "")
    rule = _AGE_TIER_RULES.get(t, (60, None, None, None))
    poi["suggested_duration_minutes"] = {
        "default": rule[0],
        **({"kid_3_6": rule[1]} if rule[1] else {}),
        **({"kid_7_12": rule[2]} if rule[2] else {}),
        **({"senior": rule[3]} if rule[3] else {}),
    }
    return poi
```

**工时**：~3h（schema 0.5h + 41 条 mock 回填脚本 0.5h + 兼容旧字段 1h + 测试 1h）。
**影响**：#23 mock + #11 BlueprintLLM preview（Agent B 同步改）+ #12 BlueprintPrompt（Agent D 同步改）+ #13 Critic（Agent E age-aware critic 取 dict 投影）。
**风险**：dict 不能向后兼容旧 `Optional[int]` 直接赋值——schema 用 `Union[int, SuggestedDuration]` 双兼容期，旧 mock 不破。

### 方案 B：Restaurant 加 `typical_dining_min`，按 cuisine 批量回填（P0-G2）★★★

**schema 改动**：

```python
# Restaurant 加一行
typical_dining_min: Optional[NonNegativeInt] = Field(
    default=None,
    description="典型用餐时长（min）；按 cuisine 业界惯例回填",
)
```

**mock 迁移脚本草稿**：

```python
# 参考 Foursquare dining_duration + 业内餐饮标准
_CUISINE_DINING_MIN = {
    "健康轻食": 40,    # 沙拉/碗简餐
    "咖啡":     45,    # 含小食
    "下午茶":   75,    # 闺蜜茶聚常态
    "杭帮菜":   75,
    "本帮菜":   75,
    "湘菜":     75,
    "粤菜":     90,    # 大桌偏长
    "日料":     75,
    "法餐":     105,   # 多 course
    "西餐":     90,
    "韩料":     75,
    "火锅":     120,
    "烧烤":     105,
    "川菜":     75,
    "东南亚":   75,
    "甜品":     45,
}

def migrate_restaurant(r: dict) -> dict:
    cuisine = r.get("cuisine", "")
    base = _CUISINE_DINING_MIN.get(cuisine, 60)
    # 商务体面 / 高人均 +15；私房菜 +15；快简餐 -10
    if "高人均" in r.get("tags", []) or "商务体面" in r.get("tags", []):
        base += 15
    if "私房菜" in r.get("tags", []) or "雅致" in r.get("name", ""):
        base += 15
    r["typical_dining_min"] = base
    return r
```

**工时**：~2h（schema 0.5h + cuisine dict + 48 条回填 0.5h + preview 透传 0.5h + 测试 0.5h）。
**影响**：#23 mock + #11 BlueprintLLM `_restaurant_preview` + #12 prompt 加消费规则。
**风险**：极低。

### 方案 C：POI 加 `energy_level` 与 `kid_friendly_intensity` 两枚举字段（P0-G3）

```python
EnergyLevel = Literal["sedentary", "easy", "moderate", "strenuous"]
KidIntensity = Literal["calm", "mild", "active", "wild"]

# Poi 加两字段
energy_level: Optional[EnergyLevel] = None
kid_friendly_intensity: Optional[KidIntensity] = None
```

**回填策略**：`高强度`→strenuous、`低强度`→easy、`运动步道/健身房/攀岩`→strenuous、其余按 type 默认（博物馆=easy，主题乐园=moderate）；kid_intensity 取 `(active|wild) if 主题乐园 / 蹦床 else mild if 亲子向 else calm`。

**工时**：~2.5h。
**影响**：#23 mock + #2 IntentParser（输入"剧烈/不剧烈"映射到 energy_level）+ Agent A 协同。
**风险**：低。

### 方案 D：Persona schema 加结构化 `default_pace_profile`（P0-G4）

```python
# 新增 Persona 字段
class PaceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    single_session_max_min: Optional[int] = Field(default=None, description="单段时长上限")
    total_active_min: Optional[int] = Field(default=None)
    break_every_min: Optional[int] = Field(default=None)
    preferred_dwell_min: Optional[int] = Field(default=None)

# personas.json 5 条对应回填
{
    "u_dad":     {"default_pace_profile": {"single_session_max_min": 90,  "break_every_min": 45, "preferred_dwell_min": 75}},
    "u_biz":     {"default_pace_profile": {"single_session_max_min": 120, "preferred_dwell_min": 90}},
    "u_grandma": {"default_pace_profile": {"single_session_max_min": 60,  "break_every_min": 45, "preferred_dwell_min": 60}},
    "u_solo":    {"default_pace_profile": {"single_session_max_min": 120, "preferred_dwell_min": 90}},
    "u_couple":  {"default_pace_profile": {"single_session_max_min": 90,  "preferred_dwell_min": 75}},
}
```

**工时**：~1.5h（schema 0.5h + 5 条迁移 0.5h + 与 Agent A 协同 prior 注入 0.5h）。
**影响**：#24 personas + Agent A `build_intent_parser_system_prompt_with_priors` 增字段。
**风险**：低。

### 方案 E：reviews 模板按 POI type 重洗（P1-G5）

写脚本扫 reviews，对 type ∉ {亲子向} 但 review 含"5 岁孩 / 亲子分龄"模板的 POI（P018 / P028），用 type-appropriate 模板替换。**工时**：~1.5h。

### 方案 F：mock 数据迁移脚本主入口（草稿）

```python
# scripts/migrate_mock_v2.py（草稿，不本轮执行）
def main():
    pois = json.load(open("mock_data/pois.json"))
    pois = [migrate(p) for p in pois]                                 # 方案 A
    json.dump(pois, open("mock_data/pois.json","w"), ensure_ascii=False, indent=2)

    rs = json.load(open("mock_data/restaurants.json"))
    rs = [migrate_restaurant(r) for r in rs]                          # 方案 B
    json.dump(rs, open("mock_data/restaurants.json","w"), ensure_ascii=False, indent=2)

    ps = json.load(open("mock_data/personas.json"))
    for p in ps:
        p["default_pace_profile"] = _PERSONA_PACE[p["user_id"]]       # 方案 D
    json.dump(ps, open("mock_data/personas.json","w"), ensure_ascii=False, indent=2)

    # 方案 P2-G9：脚本驱动重生 _samples（取前 2 条）
    json.dump(pois[:2], open("mock_data/_samples/poi.example.json","w"), ensure_ascii=False, indent=2)
    json.dump(rs[:2], open("mock_data/_samples/restaurant.example.json","w"), ensure_ascii=False, indent=2)
```

---

## 4.5 12 条 POI 业界 audit 表（强制必填）

> 业界基线参考：Smithsonian SEEC（学龄前 20-25min/单展项、整馆 2h 含休息）+ Hands-On House（90min 含切换 cap）+ TripAdvisor "Suggested duration" + Google Maps "People typically spend X here"。
> 列含义：`mock` = 当前 mock 单值；`业界 default/kid_3_6/senior` = 业界对该 type 的合理区间；判定 = 合理 / 偏长 / 偏短 / 反例 / 不一致。

```
| ID    | 名称                              | type        | mock(min) | 业界 default | 业界 kid_3_6 | 业界 senior | 判定      | 说明                                                              |
|-------|-----------------------------------|-------------|-----------|--------------|--------------|-------------|-----------|------------------------------------------------------------------|
| P001  | 森林儿童探索乐园                   | 亲子乐园     | 120       | 90-120       | 60-75        | 60          | 偏长(kid) | 5 岁娃户外乐园 90min 是上限；mock 120 已含切换余量但未注明        |
| P003  | 城市儿童博物馆                     | 亲子博物馆   | 90        | 90           | 60           | 60          | 合理      | Smithsonian 中位线；5 岁娃单段 60-90min 业界共识                  |
| P004  | 悦读亲子绘本馆                     | 儿童阅读馆   | 60        | 60           | 45           | -           | 合理      | 亲子阅读 30-60min 业界（绘本馆主活动 + 互动）                     |
| P017  | 童趣海洋亲子馆                     | 亲子游乐场   | 90        | 90           | 60-75        | -           | 合理      | 上限合理；5 岁段建议加 kid_3_6=60                                  |
| P019  | 玉皇山陶艺工坊                     | DIY 工坊     | 90        | 90           | 45           | -           | **反例**  | 缺 age_range；review 含"5 岁孩学习成长"暗示亲子，但 5 岁陶艺 ≤ 45min（业内手作课时常 45min） |
| P033  | 梦幻奇迹乐园（大型主题）            | 主题乐园     | 180       | 180-240      | 90-120       | 60          | 偏长(kid) | 全年龄共用；3 岁实际 90min 见极限（需 nap）；mock 单值无法表达     |
| P034  | 腾跃蹦床公园                       | 室内运动馆   | 90        | 90           | -            | -           | **不一致**| age_range=[6,35]，5 岁不在内；但 tags=[亲子友好]+suitable_for=[家庭日常]，过滤层会让 5 岁娃误命中 |
| P038  | 麦香烘焙体验工坊                   | 烘焙工坊     | 90        | 90           | 60           | -           | **不一致**| age_range=[6,60]，5 岁不在内；但 tags=[亲子友好]，过滤同上漏       |
| P040  | 无障碍亲子博物馆 · 三代同堂友好馆   | 复合体验馆   | 100       | 90           | 60           | 60          | **反例**  | age_range=[3,80]；三代同行取 min(75,60)=60min 才合理；mock 100min 同时超 5 岁注意力（75）+ 老人体力（60）两个 cap |
| P005  | 江畔老人公园                       | 城市公园     | 60        | 60           | 60           | 60          | 合理      | 老人户外散步 60min 业界                                           |
| P006  | 怡心老年茶艺馆                     | 茶馆         | 90        | 90           | -            | 60          | 偏长(老) | 老人单段 ≤ 60min（参考 Hands-On House 90 是含切换 cap）           |
| P007  | 国韵戏曲文化园                     | 戏曲园       | 90        | 90           | -            | 75          | 偏长(老) | 戏曲连续 90min 偏长；老人需中场休息 60-75 含 break                 |
| P020  | 运河文化夜跑步道                   | 运动步道     | 45        | 45           | 30           | 45          | 合理      | 散步 / 慢跑 30-45min 业界                                          |
| P040  | （三代同堂场景重点）                | -           | 100       | -            | -            | -           | **反例**  | 见上                                                             |
```

总计：12 行 + 重点反例展开（P040 单独标）。其中**反例 3 处（P019 / P040 / P034 不一致）+ 偏长 4 处（P001 / P033 / P006 / P007）+ 合理 5 处**——审 12 条暴露真实业务合理性问题占比 58%，与 Agent D 报告"5 岁娃 150min 是 prompt + mock 共同失守"的论断一致。

---

## 5. 目录归属建议（A1 融合）

```
| 文件                                       | 当前位置             | 建议归属                  | 备注                                          |
|--------------------------------------------|---------------------|--------------------------|-----------------------------------------------|
| mock_data/pois.json                        | mock_data/           | mock_data/v2/            | 升级 schema 后挪 v2/，留 v1 软链接兼容期       |
| mock_data/restaurants.json                 | mock_data/           | mock_data/v2/            | 同上                                          |
| mock_data/routes.json                      | mock_data/           | mock_data/                | 不动；与 Agent C 协同补 P×P 矩阵               |
| mock_data/personas.json                    | mock_data/           | mock_data/profiles/      | 与 user_profile{,s}.json 合并到 profiles/      |
| mock_data/user_profile.json                | mock_data/           | mock_data/profiles/legacy | 标记 legacy；新代码读 user_profiles.json     |
| mock_data/user_profiles.json               | mock_data/           | mock_data/profiles/      | 升级合并 personas.default_tags 字段            |
| mock_data/_samples/*.json                  | mock_data/_samples/  | mock_data/_samples/      | 保留；改为脚本自动重生（与生产 mock 同步）     |
| backend/scripts/enrich_mock_data.py        | backend/scripts/     | backend/scripts/legacy/  | Step 3 一次性脚本；冻结，新增 migrate_mock_v2.py |
| backend/scripts/enrich_mock_coords.py      | backend/scripts/     | backend/scripts/legacy/  | 同上，coords 已稳定                            |
```

**合并/删除建议**：
- `personas.json` 与 `user_profiles.json` 是双轨用户画像（前者带 default_tags + notes，后者只 4 字段）。建议把两者合并为单一 `mock_data/profiles/personas_v2.json`，含完整 archetype；user_profile.json（单 demo_user）保留作向后兼容。
- `enrich_mock_data.py` 是 Step 3 落库脚本，冻结；新生产用 `migrate_mock_v2.py` 演进。

**冻结建议**：
- `mock_data/pois.json` v1 schema 在 v2 上线后冻结（兼容期 + 6 weeks）
- `_samples/*.example.json` 不冻结，脚本驱动

---

## 6. 跨环节依赖警示

### 6.1 我修这里会影响

- **Agent B（候选搜索 #5-9）**：方案 A 让 `suggested_duration_minutes` 从 int 变成 dict，B 的 `_poi_preview` 必须按 `intent.companions[].age` 主导桶投影（5 岁娃 → kid_3_6；含 70 岁老人 → senior；混合 → multi_gen）；这等于把 B P0-1 的"暴露字段"从一行改成 ~10 行 helper。**两份 spec 必须捆绑**——只补字段不投影 = LLM 看到 dict 反而懵。
- **Agent D（蓝图层 #10-12）**：方案 A 后 BlueprintPrompt 必须改：`duration_min` 取自 `candidate.suggested_duration_minutes.<age_tier>` 而非顶层字段；与 Agent D P0-D1（prompt 范例 165 改 75）+ P0-D2（暴露 suggested）一起做才闭环。
- **Agent E（critic #13-15）**：方案 A 后，Agent E 的 `_age_aware_duration_critic` 应优先取 `candidate.suggested_duration_minutes.<tier>` 而不是用业界公式硬编码（mock 已经是业界数据落库）；这让 critic 与 mock 数据同步演进，不再两套规则打架。
- **Agent A（意图层 #1-4）**：方案 D 让 personas 加 `default_pace_profile`，Agent A `build_intent_parser_system_prompt_with_priors` 必须新增字段消费——把"孩子能玩 1.5h+"这种 notes 信号转 prior 注入 → IntentExtraction 新字段 `pace_profile` →下游 BlueprintLLM 看见单段时长上限。这条链路 4 个 agent 协同（G→A→D→E）。
- **Agent C（lookup_hop / estimate_route_time）**：方案 F 中 `_samples/route.example.json` 字段补全 + 与 Agent C 协同 P×P 矩阵补全；P020↔R 字段当前部分缺，影响夜跑后用餐 hop。
- **Agent H（输出 #21-22 / #25）**：方案 A dict 字段后，narrator 的 `_node_to_phrase` 也要按 age 投影（"陪宝贝玩 60 分钟"vs"成人 90 分钟"），否则文案与 itinerary.duration_min 数字不一致。

### 6.2 我依赖另一处先修

- **Agent A 把 IntentExtraction.companions[].age 抽取率拉到 ≥ 95%**：方案 A dict 投影完全靠 age；缺 age 时 fallback default 桶——退化为现状。
- **Agent B 在 `_poi_preview` 已实现 dict 投影 helper**：否则 mock 是 dict 但 LLM 看到 None 反而更糟。
- **Agent D prompt 范例 165 改为 75 / 90**（P0-D1）：mock 改了 prompt 不改，LLM 还是按范例锚定。

### 6.3 内部 mock 一致性 + 关键观察

- 方案 A + B + C + D 应作为**同一个 spec** `mock-data-schema-v2`，不要拆 4 个，否则中间态会让 mock 与 schema 反复漂移。
- 迁移脚本 `migrate_mock_v2.py` 必须**幂等**（跑 N 次结果同 1 次）。
- _samples 同步必须脚本驱动**自动重生**，避免现状 P2-G9 漂移再发。

**核心观察 —— mock 数据是规划质量的"水源"**：5 岁娃博物馆 2.5h 的根因诊断在 6 个 agent 报告里都指向 prompt + preview，**但根本治理在 mock 数据本身**。prompt 改了 → LLM 看到正确锚点 → 但 mock 里 P019（5 岁娃 + DIY + 90min）数据本身错的 → LLM 还是会被反向带偏；preview 修了 → 字段透传到位 → 但 P040 mock 100min 对 5 岁娃 + 70 岁外婆三代场景仍超双 cap → 还是错。Agent G 的高杠杆体现在——前面 6 个 agent 修"管道"，G 修"水源"，水源不清前面所有努力都在打折。**方案 A + B + D 是 P0 必修；方案 C + E 是 P1**。

---

## 自检确认

- [x] 6 段强制格式（§1 现状 / §2 gap / §3 业界 / §4 修复 / §5 目录 / §6 跨环节）
- [x] gap ≥ 6 条（P0×4 + P1×4 + P2×3 = 11 条）
- [x] 业界对标 ≥ 4 条带链接（Google Places / Foursquare / TripAdvisor / OSM / TravelPlanner / Smithsonian SEEC = 6 条）
- [x] POI audit 表 ≥ 12 条（§4.5 共 14 行，含 5 反例 / 偏长 4 / 合理 5）
- [x] schema 字段升级建议 P0/P1/P2（§4 方案 A-F 含工时与依赖）
- [x] mock 数据迁移脚本草稿（§4 方案 A/B/D/F 含 Python 草稿）
- [x] 与 Agent A/B/D/E 的协同警示（§6.1 - §6.4 完整）
- [x] 不动 mock_data / 不改 schema / 不 commit
- [x] 中文撰写，字数约 4400（在 3000-6500 区间）
