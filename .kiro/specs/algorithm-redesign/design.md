# Design Document: Algorithm Redesign (spec C)

> **范围**：High-Level Design（三联混合主架构图 + 防守纵深升级图）+ Low-Level Design（代码改动锚点 / 公式 / schema）
> **语言**：Python 3.11 + Pydantic v2（后端）/ Next.js 14 + TypeScript（前端）
> **项目模式**：hackathon + demo（联合审查后落地；保留 LangGraph 主架构 + spec B 目录）
> **规模**：~12.1h，分 9 个 task；联调 +1h ≈ 13h 总工时
> **现状**：spec A（业务质量 8 task）+ spec B（目录重组 8 task）已落地；本 spec 在两者基础上加算法层产品级骨架
> **绝对约束**：保留 graph/build.py 拓扑（不动 edge）；只改节点内部 / mock 数据 / 前端组件；spec B 冻结 legacy/ 不动
> **范式来源**：联合审查报告 §五 8 维度排名 + §七 独立第二意见 7.2 三联混合（LLM-Modulo + ItiNera-style 分工 + TravelAgent 三层 schema）

## Overview

把项目主架构从「事实上的 LLM-Modulo 同构系统但未显式承认」升级为「显式三联混合产品级骨架」。

**联合审查独立第二意见**（report.md §七 §7.1）：

```text
| 范式贡献                       | 在 spec C 中的角色                  | 报告印证度       |
|-------------------------------|----------------------------------|----------------|
| LLM-Modulo（Agent 3）          | 主架构（保留 graph/ + critic 不动）| 5+ 份合议       |
| ItiNera-style 分工（Agent 2/6）| _utility 末尾加 LLM 语义打分项     | 2 份直接 + 3 份精神相近 |
| TravelAgent 三层 schema（Agent 7）| user_profile.json 扩 hard/soft/commonsense | 3+ 份合议    |
| Google grounding-first（Agent 1）| ils_planner._query_pois 前置硬剔除  | 3 份合议（含 6/5）|
| TravelPlanner reward 思想（Agent 4/5）| critics_v2 加 compute_reward + CRITIC_FEEDBACK_MODE | 4 份合议      |
| TripGenie LUI 浮标（Agent 8）  | ChatDock 默认收起态                | 1 份直接 + 隐藏冲突 1 取舍 |
| NAVITIME 三候选（Agent 8）      | ComparisonView 强化使用             | 1 份直接 + Agent 6 工业派印证 |
| DeepTravel hallucination 防御（Agent 5）| 加 TOOL_RESPONSE_INCONSISTENCY ViolationCode | 1 份直接 + 4 份隐含 |
```

**对比 spec A**：spec A 是"业务规则增强"（年龄 cap / 满座埋点 / dietary）；spec C 是"算法骨架升级"（reward signal / grounding-first / LLM-as-scorer）——两者不冲突，spec C 在 spec A 业务规则之上加产品级抽象。

## Architecture

### 三联混合主架构图（High-Level Design）

```text
┌──────────────────────────────────────────────────────────────────┐
│              spec C 三联混合产品级骨架                              │
└──────────────────────────────────────────────────────────────────┘

用户输入「带 5 岁娃下午出去」+ user_profile（hard/soft/commonsense 三层）
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ 第 1 联：TravelAgent 三层 schema 注入（Agent 7，R5）                │
│ • user_profile.json 含 hard（home/budget）+ soft（dietary 自然语言）│
│   + commonsense（recent_trips list[5]）三层                         │
│ • intent/parser.py 抽 intent 时把匹配 social_context 的 recent_trips│
│   注入 LLM prompt——demo 时评委看到「AI 记得我上次去过 P004」      │
└──────────────────┬───────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ 第 2 联：LLM-Modulo + ItiNera-style 分工（Agent 3 + 2/6，R3+R4）   │
│                                                                   │
│  ils_planner._query_pois                                          │
│    ↓ 加 _grounding_filter（硬约束前置硬剔除，Agent 1 范式）        │
│      - 5 岁娃 + suggested > 90min POI 直接剔除                      │
│      - business_status=closed 直接剔除                              │
│      - distance > intent.max + 1.0 直接剔除                         │
│    ↓ tracer.emit("grounding_filtered") 让评委看见                  │
│  输出：候选池（已去除不可行）                                      │
│           │                                                       │
│           ▼                                                       │
│  preference_scorer.score_pois_with_llm                            │
│    ↓ 批量调一次 LLM 给每个 POI 出 0-1 语义打分（ItiNera 范式）      │
│      prompt：intent 自然语言 + POI 列表 + 严格 JSON 输出           │
│      失败兜底：所有 POI 默认 0.5 分（不阻断主路径）                 │
│  输出：dict[poi_id, float]                                          │
│           │                                                       │
│           ▼                                                       │
│  ils_planner._utility（保留原 4 维 + _overload_penalty）           │
│  + 0.3 * semantic_scores.get(poi.id, 0.5)                         │
│    ←————语义打分末尾追加项（保留 spec A R5 不动）                   │
│           │                                                       │
│           ▼                                                       │
│  ils_planner.plan_hybrid 返回 top_k=3 候选                          │
│    ←————强化 NAVITIME 三候选 UX 的后端基础                           │
└──────────────────┬───────────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ 第 3 联：critic 工程化加固（Agent 5/3，R1+R2）                     │
│                                                                   │
│ critics_v2.validate_itinerary（保留 spec A 10 类 ViolationCode 不变）│
│ + 新加 TOOL_RESPONSE_INCONSISTENCY（防 LLM 编造 POI ID）           │
│ + format_violations_for_llm 支持三档 mode：                       │
│   - pinpoint-all（默认）                                          │
│   - first-only（论文证据等价；token 节省 30-50%）                 │
│   - reward（dense scalar；未来 RL 路径预留挂钩点）                │
│ + compute_reward(violations) -> float（CRITICAL=1.0/WARNING=0.2 + │
│   CODE_WEIGHTS 按 STAR ablation MACRO 半稀疏权重）                │
└──────────────────┬───────────────────────────────────────────────┘
                   │
                   ▼
              narrate_node（spec A R6 主动质疑保留）
                   │
                   ▼
              memory_writer 副作用（在 narrate_node 末尾）
              把 RecentTrip 写回 user_profile.recent_trips
                   │
                   ▼
              SSE → 前端
                   │
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ 前端 UX 双层折叠 + 三候选并列（Agent 8，R6+R7）                    │
│ • ChatDock 默认收起（学携程 LUI 浮标）→ 主区域留给 itinerary       │
│ • ToolTracePanel 默认收起（点击查看决策过程 N 步）                │
│ • ComparisonView 强化：3 候选 + 三轴评分（时长/距离/偏好）        │
│ • 用户切换主行程不发新 SSE，IntentSummary 同步                    │
└──────────────────────────────────────────────────────────────────┘
```

### 关键决策（联合审查取舍 + design 阶段拍板）

```text
| 决策点                            | 决定                          | 理由                                                |
|----------------------------------|------------------------------|---------------------------------------------------|
| critic 反馈细化策略                | env flag 三档（pinpoint/first/reward）| 联合审查冲突 2：first-only 性能等价但 token 省 |
| 硬约束剥离时机                    | grounding-first 前置（Agent 1）+ critic 兜底（Agent 3）双层 | 联合审查冲突 3：分层处理硬约束 vs 软约束 |
| LLM 语义打分接入点                | _utility 末尾加项 + 不替换 4 维  | 风险隔离：原 4 维已被 spec A R5 验证稳定            |
| memory_writer 接入方式            | narrate_node 末尾副作用调用，不动 graph 拓扑 | 编排冻结纪律 §3.3.1（spec B 已锁）|
| 三候选返回方式                    | plan_hybrid top_k=3 + sse_adapter payload 加 candidates | 后端单次请求出 3 方案，前端切换零延迟 |
| ChatDock 默认状态                 | collapsed（学 LUI）            | 联合审查隐藏冲突 1：LUI vs ToolTrace 双层折叠     |
| TOOL_RESPONSE_INCONSISTENCY 接入  | validate_itinerary 加可选参数 tool_results | 向后兼容；critic_node 主动透传            |
| compute_reward 公式               | CRITICAL=1.0/WARNING=0.2 + CODE_WEIGHTS（macro 1.5/细粒度 0.8）| Agent 5 报告 §六 Q4 + STAR MACRO ablation |
| recent_trips 上限                 | 5 条 + LLM 摘要 + 隐私脱敏     | Agent 7 §三 §3.4 隐私 + Agent 5 hallucination 防御 |
| ComparisonView 三轴公式           | 时长合规度 + 距离合理度 + 偏好匹配度 | Agent 7 R7 + Agent 8 NAVITIME 范式            |
| RL / vector RAG / 新增 agent      | 永不做（pitfalls 防再犯）       | 联合审查报告 §七 §7.4 + Phase 1 8 范式不可行印证 |
```


## Components and Interfaces

### Component 1: critics_v2 升级（compute_reward + 三档 mode + TOOL_RESPONSE_INCONSISTENCY）

**Purpose**：让同一个 critic 文件支持 LLM-Modulo backprompt（pinpoint-all 默认）、token 节省 A/B（first-only）、未来 RL reward signal（reward）三种消费模式；同时加 hallucination 防线。

**核心改动**：

1. `ViolationCode` 枚举加第 11 个 `TOOL_RESPONSE_INCONSISTENCY = "tool_response_inconsistency"`（紧跟 AGE_DURATION_MISMATCH 之后）。
2. 新增 `SEVERITY_WEIGHTS` 与 `CODE_WEIGHTS` 模块级常量：
   - `SEVERITY_WEIGHTS = {CRITICAL: 1.0, WARNING: 0.2}`
   - `CODE_WEIGHTS`：macro 级（INVARIANT_BROKEN / NODES_INCOMPLETE / TIMELINE_INCONSISTENT / TOOL_RESPONSE_INCONSISTENCY）取 1.5；细粒度（DIETARY_VIOLATION / DISTANCE_EXCEEDED）取 0.8；其余 1.0。
3. 新增 `compute_reward(violations) -> float`：公式 `-sum(SEVERITY_WEIGHTS[v.severity] * CODE_WEIGHTS.get(v.code, 1.0) for v in violations)`；无违规返回 0。
4. 新增 `_get_feedback_mode() -> str`：从 env 读 `CRITIC_FEEDBACK_MODE`，不在 `{pinpoint-all, first-only, reward}` 范围 → fallback 到 pinpoint-all + stderr 输出 warning（不抛异常）。
5. 升级 `format_violations_for_llm(violations)`（保持原签名向后兼容）：mode=pinpoint-all 时输出原行为；first-only 时仅列第一条 critical；reward 时返回空字符串（占位，本 spec 不消费）。
6. 新增 `_check_tool_consistency(itinerary, tool_results) -> list[Violation]`：tool_results=None 或候选池为空时返回 []；遍历 itinerary.nodes，对 target_kind ∈ {poi, restaurant} 节点检查 target_id 是否在对应候选池 ID 集合里；不在则发 CRITICAL violation（message 含「方案中『XX』不在候选池中，可能是 AI 编造的」，不暴露 dot-path）。
7. 升级 `validate_itinerary` 函数签名加 `tool_results: dict | None = None` 参数（向后兼容）；末尾追加 `violations.extend(_check_tool_consistency(itinerary, tool_results))`。

**接入点**（`backend/agent/graph/nodes/critic.py:critic_node`）：在调用 `validate_itinerary` 时透传 `state.get("tool_results")` 参数。

### Component 2: ils_planner.py grounding-first 前置硬剔除

**Purpose**：把硬约束（年龄 cap / business_status / 距离）从事后 critic 上提到 `_query_pois` / `_query_restaurants` 候选生成阶段。

**核心改动**：

1. 新增 `_grounding_filter_poi(candidates, intent, tracer) -> list[Poi]`：剔除以下情况
   - 含 ≤6 岁同行人 + `get_duration_for_companions(poi.suggested_duration_minutes, intent.companions)` > 90min（用 spec A R2 helper）
   - 含 ≥75 岁同行人 + 主导桶 > 75min
   - `poi.distance_km > intent.distance_max_km + 1.0`
   - `getattr(poi, "business_status", "open") in {"closed", "permanent_closed"}`（mock 当前可能无此字段，getattr 兜底）
   - 每剔除一个候选必 emit `tracer.emit("grounding_filtered", {poi_id, reason})`
   - 候选池 < 3 时自动放宽：仅过滤距离 +2.0km 与 business_status，跳过 age cap
2. 新增 `_grounding_filter_restaurant(candidates, intent, tracer)`：仅过滤距离 + 营业状态（不做 age cap，餐厅 typical_dining_min 不区分客群桶）。
3. 升级 `_query_pois` 末尾的 return 前加 `candidates = _grounding_filter_poi(candidates, intent, tracer)`；同理 `_query_restaurants`。

**保留不动**：spec A R5 的 `_overload_penalty` 与 `_utility` 内 `score -= 0.5 * _overload_penalty(poi, intent)`——作为兜底，避免破坏现有测试基线。

### Component 3: preference_scorer.py LLM 语义打分（ItiNera-style）

**Purpose**：让 LLM 给每个候选 POI 出 0-1 语义契合分，作为 _utility 末尾追加项。

**新建文件**：`backend/agent/planning/preference_scorer.py`

**核心接口**：

- `score_pois_with_llm(intent, pois, *, client=None) -> dict[str, float]`：批量调一次 LLM 给 POI 出语义打分
  - 失败兜底：所有 POI 默认 0.5 分（不阻断 ILS 主路径）
  - Stub 模式：检测 `client.provider == "stub"` 时直接返回全 0.5
  - prompt：intent 自然语言 + POI 列表（id/name/category/tags/rating/description 前 80 字符）+ 严格 JSON 输出 `{"scores": {"P001": 0.85, ...}}`
  - LLM 失败 / JSON 解析失败：返回所有 POI 默认 0.5 分
  - 类型校验 + clip [0, 1]
  - temperature=0.3；max_tokens=500

**接入点**（`backend/agent/planning/planners/ils_planner.py`）：

1. `_utility` 函数签名加 `semantic_scores: dict[str, float] | None = None` 参数（向后兼容）；公式末尾加 `score += 0.3 * semantic_scores.get(poi.id, 0.5) if poi and semantic_scores else 0`（保留原 4 维 + spec A R5 _overload_penalty 不变，仅末尾追加）
2. `plan_hybrid` 入口：调用 `score_pois_with_llm(intent, pois, client=client)` 缓存到局部变量；后续 `_utility` / `_local_search` / `_perturb` 调用透传 semantic_scores 参数

### Component 4: user_profile.json 三层 schema + memory_writer 副作用

**Purpose**：让 demo 时评委看到「AI 记得我上次的偏好」+「同行人画像三层注入主路径」。

**Schema 升级**（`backend/schemas/persona.py`）：

```python
class RecentTrip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: str        # ISO 8601
    social_context: str    # 与 SOCIAL_CONTEXTS 词典对齐
    summary: str           # LLM 生成的 1-2 句脱敏摘要
    success: bool          # 用户是否完成确认（cancel 不算 success）


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    home_location: HomeLocation
    default_budget: float
    transport_preference: str
    # spec C R5 新增三层（全 Optional 向后兼容）
    dietary_preference: Optional[str] = None         # soft 层：自然语言段落
    social_context_history: Optional[list[str]] = None  # commonsense 层：去过的场景
    recent_trips: Optional[list[RecentTrip]] = None   # commonsense 层：最多 5 条
```

**memory_writer 副作用**（接入 `backend/agent/graph/nodes/narrate.py:narrate_node` 末尾，不动 graph 拓扑）：

1. `_summarize_trip(itinerary, intent, *, client) -> str`：LLM 短 prompt（< 200 token）生成脱敏摘要；隐私要求 prompt 显式约束「不出现具体年龄数字（5 岁 → 学龄前儿童）/ 不出现具体地址 / 经纬度」
2. `_persist_memory(state)`：用 `threading.Lock` 跨平台兼容（不依赖 fcntl）；幂等键用 social_context + 5 分钟 timestamp 窗口（同 session 重复不追加）；`recent_trips[:5]` 上限；失败 / cancel 不阻断主流程（try/except 包裹 + warning log）

**intent/parser.py 召回**：`IntentParser._build_user_message`（或对应函数）把匹配 social_context 的最新 1 条 recent_trip 注入 prompt（"用户上次「家庭」场景的行程：{summary}"）。

### Component 5: ComparisonView 三候选 + 三轴评分（NAVITIME 借鉴）

**Purpose**：让评委 demo 现场看到「同一句话 → 3 个不同侧重的方案 + 三轴评分对比」。

**后端**：

1. 新建 `backend/agent/planning/comparison_axes.py`，实现 `compute_axes(itinerary, intent) -> dict[str, int]`：
   - **时长合规度** = `int(100 * (1 - 违规节点数 / 总节点数))`（违规节点 = duration_min > age_caps_from_intent）
   - **距离合理度** = `int(100 * exp(-(总通勤时间 - target_min)² / 800))`（target_min = duration_hours[0] × 60 × 0.2，通勤理想占比 20%）
   - **偏好匹配度** = `int(100 * mean(semantic_scores))`（从 task 5 的 preference_scorer 输出拿；候选池为空则 70 占位）
2. 升级 `backend/agent/planning/planners/ils_planner.py:plan_hybrid`：返回前 3 名 utility 排名候选 + 每个候选的 `comparison_axes`
3. 升级 `backend/agent/graph/sse_adapter.py:_emit_itinerary_ready`：payload 加 `candidates: list[Itinerary]` + `comparison_axes: list[dict]`（保留原 itinerary 字段为主行程，向后兼容）

**前端**（`frontend/components/ComparisonView.tsx`）：

- 3 列并排卡片（mobile 改竖向滑动）+ 每张卡片底部 3 条横向 AxisBar
- 用户点击卡片切换主行程：仅前端 store 状态切换，不发新 SSE，延迟 < 100ms
- 切换后 IntentSummary / ToolTracePanel / ItineraryCard / MapOverlay 同步更新主行程
- candidates 长度 < 2 时不显示 ComparisonView（保持单行程兼容）

### Component 6: ChatDock + ToolTracePanel 双层折叠（隐藏冲突 1 取舍）

**Purpose**：默认收起两个面板（学携程 LUI 浮标 + 决策可见性按需展开），评委想看决策时点开 ToolTracePanel。

**ChatDock 升级**：

- 默认 `expanded=false`（收起态：底部浮标 56×56 圆形按钮 + 未读消息 badge）
- `useEffect` 读 `localStorage.getItem("shangwuju.chatdock.expanded")` 初始化（SSR 默认 collapsed 避免 hydration mismatch）
- 监听 `Cmd+K` / `Ctrl+K` 展开 + `Esc` 收起
- 展开态：480px 卡片 + `bottom-right` 定位 + 关闭按钮
- props 加 `defaultOpen: boolean = false`

**ToolTracePanel 升级**：

- 默认 `expanded=false`（收起态："查看 Agent 决策过程（N 步）"折叠条 + badge）
- 展开态：保留现有按 Epic 分组逻辑不动
- props 加 `defaultOpen: boolean = false`

**HomeView 升级**：把 ChatDock + ToolTracePanel 默认 props 设为 collapsed；用户偏好可写回 localStorage 跨 session 持久（演示场景前可一键切到全展开）。

## Data Models

```python
# 新增 / 升级（汇总）

# backend/schemas/persona.py
class RecentTrip(BaseModel):
    timestamp: str
    social_context: str
    summary: str
    success: bool

class UserProfile(BaseModel):
    user_id: str
    home_location: HomeLocation
    default_budget: float
    transport_preference: str
    dietary_preference: Optional[str] = None
    social_context_history: Optional[list[str]] = None
    recent_trips: Optional[list[RecentTrip]] = None

# backend/agent/planning/critic/critics_v2.py
class ViolationCode(str, Enum):
    # ... 原 10 类不变 ...
    TOOL_RESPONSE_INCONSISTENCY = "tool_response_inconsistency"  # 第 11 个

# backend/agent/planning/comparison_axes.py（新建模块）
def compute_axes(itinerary, intent) -> dict[str, int]: ...

# backend/agent/planning/preference_scorer.py（新建模块）
def score_pois_with_llm(intent, pois, *, client=None) -> dict[str, float]: ...
```

## Correctness Properties

### Property 1: critic 三档反馈策略向后兼容

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7**

WHEN `CRITIC_FEEDBACK_MODE` 不设 / 设为 `pinpoint-all` THEN `format_violations_for_llm` 输出与 spec A 完全一致（diff 应为空）。

### Property 2: TOOL_RESPONSE_INCONSISTENCY 防幻觉

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6**

WHEN itinerary.target_id 不在 tool_results 候选池里 THEN critic_node 必报 CRITICAL violation 触发 backprompt；WHEN tool_results=None THEN 跳过本检查（向后兼容）。

### Property 3: grounding-first 不破现有测试

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

WHEN ils_planner._query_pois 加 `_grounding_filter_poi` THEN 原 spec A R5 验收脚本（5 岁娃 + 老人 + 独处 + 商务 4 场景）SHALL 全部通过；P040 / P033 等违规候选 SHALL 不再出现在 utility 前 5 名。

### Property 4: LLM 语义打分失败兜底

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7**

WHEN LLM API key 未设 / LLM 调用失败 / JSON 解析失败 THEN `score_pois_with_llm` 返回所有 POI 默认 0.5 分；ILS 主路径不阻断；现有 470+ 项 pytest 全过。

### Property 5: memory_writer 幂等 + 隐私脱敏

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7**

WHEN 同一 session 5 分钟内反复跑同一 social_context THEN recent_trips 不重复追加；trip_summary SHALL 不含「5 岁」这种原始数字（脱敏到「学龄前儿童」）。

### Property 6: 双层折叠 UX 持久化

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7**

WHEN 用户首次访问 THEN ChatDock + ToolTracePanel 默认 collapsed；WHEN 用户展开后刷新页面 THEN localStorage 持久化展开状态（跨 session）。

### Property 7: ComparisonView 三轴数学正确性

**Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7**

WHEN 5 岁娃 196min 反例进 ComparisonView THEN 该候选的 `duration_compliance` ≤ 50（违规节点 / 总节点 ≥ 50%）；WHEN 合规候选 THEN `duration_compliance` = 100。

### Property 8: 防再犯条款落库

**Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5**

WHEN spec C 全部 task 完成 THEN `pitfalls.md` SHALL 含 4 条 [P0]「永不做」条款（grep `不要做 RL` / `不要做 vector RAG` / `不要新增 agent` 全 1 命中）。

## Testing Strategy

### 1. 测试基线（spec C 启动前）

执行 spec C **第一步**（task 1）：跑全套测试 + 启动 FastAPI + 启动前端，记录基线。如果有红灯，**立即停止 spec C**，先修复红灯再启动。详见 task 1 验证清单。

### 2. 每个 task 末尾的 smoke test

每个 task 末尾跑 `pytest backend/tests/ -x --tb=short`（`-x` 模式遇到第一个失败立即停）。

### 3. 全部完成后的完整验证（task 9）

```bash
pytest backend/tests/ -v --tb=short
python backend/scripts/verify_planning.py
python backend/scripts/verify_planning_quality.py  # spec A R10
python backend/scripts/verify_legacy_frozen.py     # spec B R3
python backend/scripts/verify_edge_model.py
python -m backend.main & sleep 3 && curl -s http://localhost:8000/health
cd frontend && pnpm verify:all
```

### 4. 新增测试覆盖矩阵

```text
| Task | 新增测试文件                                   | 项数 | 覆盖维度                                  |
|------|----------------------------------------------|------|------------------------------------------|
| T2   | tests/test_critic_feedback_mode.py           | ≥ 8  | 3 档 mode + compute_reward 数值 + env fallback |
| T3   | tests/test_tool_response_inconsistency.py    | ≥ 6  | 编造 ID / 真实 ID / None / home / 多个幻觉 |
| T4   | tests/test_grounding_first.py                | ≥ 5  | 5 岁娃过滤 / 老人过滤 / 候选 < 3 放宽 / restaurant / tracer.emit |
| T5   | tests/test_preference_scorer.py              | ≥ 4  | LLM 高分 / stub / LLM 失败 / JSON 解析失败 |
| T5   | tests/test_utility_with_semantic.py          | ≥ 2  | 数学正确 / None 兼容                       |
| T6   | tests/test_memory_writer.py                  | ≥ 5  | 5 条上限 / 幂等 / 失败不写 / 隐私脱敏 / 文件锁 |
| T6   | tests/test_recent_trips_recall.py            | ≥ 3  | 召回匹配 / 不匹配 / dietary 注入            |
| T7   | frontend/components/ChatDock.test.tsx        | ≥ 5  | 默认收起 / Cmd+K / Esc / localStorage / hydration |
| T8   | tests/test_comparison_axes.py                | ≥ 4  | 196min ≤50 / 合规 = 100 / 距离公式 / 偏好平均 |
| T8   | frontend/components/ComparisonView.test.tsx  | ≥ 2  | 3 候选 / 切换不发新 SSE                    |
| 总计  |                                              | ≥ 44 | 原有 470+ 项 + spec A 30 + spec B 33 + spec C 44 项 |
```

### 5. 不做的测试

- 不做 LLM 真链路 e2e（DEEPSEEK_API_KEY 未必配置；stub 模式覆盖即可）
- 不做大规模性能测试（hackathon 时间盒不允许）
- 不做隐私合规审计（仅单测层做隐私脱敏验证）

## Error Handling

```text
| 风险                                            | 概率 | 影响 | 缓解                                                |
|------------------------------------------------|------|-----|---------------------------------------------------|
| LLM 语义打分调用失败 / 超时                     | 中   | 中  | 失败兜底全 0.5 分；不阻断 ILS 主路径               |
| memory_writer 文件锁竞争                       | 低   | 低  | threading.Lock 进程内 + 异常捕获不阻断 narrate     |
| recent_trips 隐私脱敏不到位（存了「5 岁」）    | 低   | 中  | LLM prompt 显式约束 + tests/test_memory_writer 验证 |
| ChatDock 折叠态 hydration mismatch              | 中   | 低  | useEffect 内读 localStorage，SSR 默认 collapsed     |
| ComparisonView 三候选时 SSE payload 过大        | 低   | 低  | 后端只返回 itinerary 摘要 + axes 数字，不返回全 trace |
| _utility 加 LLM 项后 spec A R5 测试漂移          | 中   | 中  | semantic_scores=None 时不加项（向后兼容）          |
| TOOL_RESPONSE_INCONSISTENCY 误报（候选池为空时）| 中   | 中  | 候选池为空时 _check_tool_consistency 直接返回 []   |
| CRITIC_FEEDBACK_MODE=reward 时漏推 backprompt   | 低   | 高  | reward 模式仅占位，spec C 不消费；narrator 文案保留 |
```

## Decisions Log

```text
| 决策                                       | 决定                       | 来源                              |
|-------------------------------------------|---------------------------|----------------------------------|
| spec C 主架构是不是 LLM-Modulo 单一路径    | 否；三联混合（+ ItiNera + TravelAgent）| 联合审查 §七 §7.1 第二意见 |
| critic 反馈策略                           | 三档 env flag             | 联合审查隐藏冲突 2 + Agent 3 ablation |
| 硬约束剥离时机                            | grounding-first 前置 + critic 兜底 双层 | 联合审查隐藏冲突 3       |
| _utility 加 LLM 项是否替换原 4 维          | 末尾追加项；不替换         | 风险隔离；spec A R5 已验证稳定     |
| memory_writer 接入方式                    | narrate_node 末尾副作用    | 编排冻结纪律 §3.3.1 + spec B 锁    |
| 三候选返回方式                            | plan_hybrid top_k=3 + sse_adapter payload | NAVITIME 范式 + 后端单次请求 |
| ChatDock 默认状态                         | collapsed                  | 联合审查隐藏冲突 1（LUI 范式）    |
| TOOL_RESPONSE_INCONSISTENCY 接入位置       | validate_itinerary 加可选参数 | 向后兼容；critic_node 主动透传    |
| compute_reward 是否替代 format_violations  | 否；前者占位、后者主路径   | 本 spec 不消费 reward；预留挂钩   |
| RL / vector RAG / 新增 agent              | 永不做                     | 联合审查 §七 §7.4                 |
| 多日范式 V2                                | 留 backlog；本 spec 不做  | spec C Out-of-Scope               |
```

## Risk Assessment

```text
| 风险                                              | 概率 | 影响 | 缓解                                                   |
|---------------------------------------------------|------|-----|--------------------------------------------------------|
| LLM 语义打分增加 ~3s latency 破 spec A 锁的预算   | 中   | 中  | 批量调一次（30 POI 一起，~3s）；缓存命中后不重复调      |
| spec A R5 _overload_penalty 与 grounding-first 双重过滤导致候选池过小 | 中 | 中 | grounding 加放宽机制；候选池 < 3 自动 +1km             |
| ChatDock 默认收起后评委不会展开导致 demo 失败       | 中   | 高  | demo 前手动 localStorage 设展开 + Cmd+K 教学一句       |
| memory_writer 文件锁在 Windows 下不兼容            | 低   | 中  | threading.Lock 是跨平台；不依赖 fcntl                  |
| TOOL_RESPONSE_INCONSISTENCY 在 stub 模式误报       | 中   | 中  | tool_results 为空时跳过；stub mode 也不报             |
| 三候选并列让 ItineraryCard / MapOverlay 渲染慢    | 低   | 中  | 仅当 ComparisonView active 时渲染；切换主行程后才更新|
| spec C 落地破 spec A / B 已有 commit 测试           | 中   | 高  | 每个 task 末尾跑全套测试；TASK 1 baseline 必须先过    |
| 联合审查的 7 项必做实施时发现项目代码不支持        | 低   | 高  | TASK 1 baseline 已读项目代码 4 处确认锚点              |
```

## Estimated Effort

```text
| 任务                                                       | 工时        | 备注                                          |
|----------------------------------------------------------|-------------|-----------------------------------------------|
| Task 1: baseline 验证 + spec A/B 完成度核查                 | 0.3h        | 跑测试 + 打 git tag v-spec-c-start            |
| Task 2: critic compute_reward + CRITIC_FEEDBACK_MODE      | 1.0h        | 单文件改动 + 单测 8 项                        |
| Task 3: TOOL_RESPONSE_INCONSISTENCY                       | 0.8h        | 加 ViolationCode + check 函数 + 单测 6 项     |
| Task 4: ils_planner grounding-first 前置硬剔除             | 1.5h        | 加 _grounding_filter_poi/_restaurant + 单测 5 项 |
| Task 5: preference_scorer + _utility 加项                 | 2.0h        | 新建模块 + 改 ils_planner + 单测 6 项         |
| Task 6: user_profile 三层 schema + memory_writer          | 2.5h        | schema + memory_writer + intent 召回 + 单测 8 项 |
| Task 7: ChatDock + ToolTracePanel 双层折叠                 | 1.0h        | 纯前端；单测 5 项                              |
| Task 8: ComparisonView 三候选 + 三轴评分                   | 2.0h        | 后端 + 前端 + 单测 6 项                        |
| Task 9: 联调 + 防再犯条款 + 文档同步 + 一次性 commit       | 1.0h        | e2e + pitfalls/progress/problem 同步           |
| **总计**                                                   | **12.1h**   | ≈ 1.5-2 人日（hackathon 时间盒可承受）        |
```

## Out of Scope（明确不做）

```text
| 不做的事                              | 理由                                |
|--------------------------------------|------------------------------------|
| RL 微调（DeepTravel / Planner-R1）   | 30+ 人天 + GPU $500；与决策可见性矛盾 |
| Google 多日 DP/set packing            | 半日单城退化                        |
| ITINERA cluster + 分层 TSP             | 节点 4-6 时数学失效                  |
| ALNS / MILP exact                     | n=87 极小规模过度工程                |
| vector RAG 替代 mock_data              | 42 POI 用 vector 过度工程            |
| 新增 agent 角色（10+）                 | 当前 5 个已达论文规模                |
| 商业产品算法借鉴（黑盒）               | 工程量天文数字                      |
| 增加 LLM 调用次数预算到 10             | latency 30 秒红线                    |
| meta_critic_node                      | 引入 +2-3s 延迟；spec D 评估         |
| AGE_DURATION_MISMATCH 论文化          | 路演叙事素材；不必再加 critic        |
| 流式 SSE 让评委每轮看 critic 进度      | 后期优化                            |
| 多日范式 V2                           | 产品演进 backlog                    |
| graph/build.py 拓扑改动               | 编排冻结纪律 + spec B 锁            |
```
