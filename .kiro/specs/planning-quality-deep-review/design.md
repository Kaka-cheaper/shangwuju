# Design Document: planning-quality-deep-review

> **范围**：High-Level Design（业务层 / 防守纵深图）+ Low-Level Design（代码改动点 / 公式 / schema）
> **语言**：Python 3.11 + Pydantic v2（后端）；不涉及前端
> **项目模式**：hackathon + demo（评审周内完成；保留 LangGraph 主架构 + edge_v1 模型）
> **规模**：~17h 必修集，分 7 个 wave；联调 +3h ≈ 20h 总工时
> **现状**：itinerary-edge-model-refactor 已完成（Phase 0.20 LangGraph + edge_v1）；本 spec 在此基础上加业务约束层
> **绝对约束**：保留 graph/ 拓扑（不动 build.py edge）；只改节点内部 / prompt / schema / mock 数据 / critic

## Overview

把「形式合规但反业界常识」的规划链路升级为「主防 + 兜底 + 主动质疑」三层防御。

**根因**：5 岁娃博物馆 2.5h 反例的 5 因联动（详见 requirements.md Introduction 表）：

```
[1] prompt 范例 165min（D）→ in-context 锚定                30%
[2] preview 漏 suggested_duration（B）→ LLM 失去权威下限     25%
[3] prompt 无年龄分级表（D）→ 5 岁娃信号被忽略               20%
[4] critic 全无单段年龄校验（E）→ 一路绿灯                   15%
[5] mock POI 单值不分年龄（G）→ 信息源浑浊                   10%
```

**思路**：业界共识三层联动（TravelPlanner ICML 2024 + LLM-Modulo NeurIPS 2024 + Google AI Trip Planning 2025-06）：

```
schema (1) → preview (2) → prompt 主防 (3) → critic 兜底 (4) → narrator 主动质疑 (5)
```

任何单点修复都不彻底。**对称防守**：critic 主路径 拦 LLM 主出错；ILS utility 兜底路径 拦算法兜底。

---

## Architecture

### 防守纵深图（High-Level Design）

```
┌──────────────────────────────────────────────────────────────────┐
│                       五层防守纵深                                 │
└──────────────────────────────────────────────────────────────────┘

用户输入「5 岁娃下午想出去玩」
    │
    ▼
┌─────────────────────────────────────────┐
│ 第 1 层：意图层（A 报告，本 spec R8）     │
│ • IntentExtraction.pace_profile 抽       │
│ • Refiner 识别「太久 / 太长」反馈         │
│ • Persona prior 注入 default_pace_profile │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 第 2 层：信息源（G + B 报告，本 spec R1+R2）│
│ • mock POI suggested_duration_minutes    │
│   升级 dict（按年龄桶分）                  │
│ • mock Restaurant 加 typical_dining_min   │
│ • _poi_preview / _restaurant_preview     │
│   暴露字段，按 age 主导桶投影             │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 第 3 层：主防（D 报告，本 spec R3）        │
│ • BlueprintPrompt 范例改 75（之前 165）  │
│ • Prompt 加紧凑版「年龄分级时长表」       │
│ • Prompt cap 1500 → 2200                 │
│ • Prompt 加候选预览消费规则               │
│ → LLM 一次过命中率 ≥ 90%                 │
└──────────────┬──────────────────────────┘
               │ LLM 偶发不听话 ↓
               ▼
┌─────────────────────────────────────────┐
│ 第 4 层：兜底（E + F 报告，本 spec R4+R5）│
│ • blueprint critic 加 _age_aware（主路径）│
│ • critics_v2 加 AGE_DURATION_MISMATCH 镜像 │
│   （ILS 兜底路径）                          │
│ • Violation 加 expected_range 字段        │
│ • ILS utility 加 overload_penalty         │
│ → backprompt 重生成命中率 ≥ 95% 累计      │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│ 第 5 层：主动质疑（H 报告，本 spec R6）    │
│ • Narrator 接 critic_summary +           │
│   quality_warnings                        │
│ • Narrator prompt 加质疑指令 + few-shot   │
│ • Narrator 模板兜底 fallback 强加质疑文案 │
│ • DONE event payload 加 6 字段            │
│ → 评委看到 "AI 主动质疑方案"             │
└─────────────────────────────────────────┘
```

### 关键决策（来自 Phase 4 联合审查取舍）

```text
| 决策点                                  | 决定          | 理由                                                |
|----------------------------------------|---------------|---------------------------------------------------|
| 单段时长决策权                          | D 主防 + E 兜底，拒 A 升级 NodeDecider | 联合审查冲突 1：prompt + critic 双层足够，algo 升级是过度设计 |
| critic message 暴露 expected_range？   | 弱化版（自然语言"建议 45-75min"，不暴露字段名）| 联合审查冲突 2：业务区间值不算 schema，遵守 design.md "不暴露 dot-path" |
| Restaurant 时长决策                     | B+G+D 必须捆绑同 PR | 单方修都不够，schema + mock + prompt 联动             |
| fallback 路由策略                       | 拒 F 方案 E（按违规类型路由）| 与 pitfalls.md P1-2026-05-23 死循环修复冲突           |
| mock dict 版本管理                      | 直接原地升级 + Union 双兼容，拒 mock_data/v2/ 子目录 | 联合审查冲突 5：mock 不是公共 API，分版本无收益          |
| meta_critic_node 是否本 spec 加         | 不加（留 spec C）；可配 ENV ENABLE_META_CRITIC 开关 | hackathon 时间盒；新增 LLM 调用 +2-3s 延迟         |
| critic 是主防还是兜底                   | 兜底             | LLM-Modulo 范式：verifier 应便宜；主防靠 prompt + preview |
| narrator 用 LLM 还是 template           | 双轨保留：LLM 主路径 + template fallback；模板加质疑兜底 | 风险红旗 4：LLM 行为不可控，模板兜底兜底         |
| 修复优先级                              | 17h 必修集 + 3h 联调缓冲 = 20h | Phase 4 §5.1 优化（W4.4 / W6.5 / W7.7 砍） |
| 目录重组                                | 不本 spec 做（spec B）| Phase 4 §6：业务质量在前，目录重组延后到 demo 验收后  |
```

---

## Components and Interfaces

### Component 1: SuggestedDuration（schema 升级）

**Purpose**：把 POI 推荐时长按主导客群分桶。

**Interface**：

```python
# backend/schemas/domain.py
class SuggestedDuration(BaseModel):
    """按主导客群分桶推荐时长（min）。default 必填，其余可选。"""
    model_config = ConfigDict(extra="forbid")
    default: NonNegativeInt = Field(..., description="成人 / 默认推荐")
    kid_3_6: Optional[NonNegativeInt] = Field(default=None, description="3-6 岁学龄前")
    kid_7_12: Optional[NonNegativeInt] = Field(default=None, description="7-12 岁学童")
    senior: Optional[NonNegativeInt] = Field(default=None, description="≥65 岁长辈")
    multi_gen: Optional[NonNegativeInt] = Field(default=None, description="多代同行（取最严）")


class Poi(BaseModel):
    ...
    # 双兼容：Union[NonNegativeInt, SuggestedDuration]
    suggested_duration_minutes: Optional[Union[NonNegativeInt, SuggestedDuration]] = None


class Restaurant(BaseModel):
    ...
    # 新增
    typical_dining_min: Optional[NonNegativeInt] = None
```

**双兼容期策略**：

- 旧 mock / 旧测试断言 `int` 仍可用（Pydantic 自动接受）
- 新代码用 `get_duration_for_companions(poi, companions) -> int` helper 统一访问
- spec 完全合并 + 1 个 sprint 后删除 int 分支（写入 pitfalls.md 提醒）

### Component 2: PaceProfile（IntentExtraction 升级）

```python
# backend/schemas/intent.py
class PaceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    single_session_max_min: Optional[int] = Field(default=None, description="单段时长上限")
    total_active_min: Optional[int] = Field(default=None)
    break_every_min: Optional[int] = Field(default=None)
    preferred_dwell_min: Optional[int] = Field(default=None)


class IntentExtraction(BaseModel):
    ...
    pace_profile: Optional[PaceProfile] = None  # 新增
```

### Component 3: `get_duration_for_companions` helper（preview 投影）

**Purpose**：根据 intent.companions 推主导客群桶，从 SuggestedDuration dict 取最严值。

```python
# backend/agent/blueprint_llm.py 或 backend/utils/duration_helpers.py
def get_duration_for_companions(
    suggested: Union[NonNegativeInt, SuggestedDuration, None],
    companions: list[Companion],
) -> Optional[int]:
    """按 companions 推主导桶 + 取最严约束。

    返回最适合当前 party 的推荐时长（min）。
    """
    if suggested is None:
        return None
    # 双兼容
    if isinstance(suggested, int):
        return suggested
    # SuggestedDuration dict
    has_young_kid = any(c.age is not None and c.age <= 6 for c in companions)
    has_kid_7_12 = any(c.age is not None and 7 <= c.age <= 12 for c in companions)
    has_senior = any(c.age is not None and c.age >= 65 for c in companions)
    multi_gen = sum([has_young_kid or has_kid_7_12, has_senior]) >= 2
    
    candidates = [suggested.default]
    if multi_gen and suggested.multi_gen: candidates.append(suggested.multi_gen)
    elif has_young_kid and suggested.kid_3_6: candidates.append(suggested.kid_3_6)
    elif has_kid_7_12 and suggested.kid_7_12: candidates.append(suggested.kid_7_12)
    elif has_senior and suggested.senior: candidates.append(suggested.senior)
    
    return min(candidates)  # 取最严
```

### Component 4: `_age_aware_duration_critic`（blueprint critic）

**Purpose**：单段时长按同行人年龄分级 critic。

```python
# backend/agent/blueprint.py（新增函数）
_AGE_KID_THRESHOLD: int = 6
_AGE_TODDLER_THRESHOLD: int = 3
_AGE_TEEN_LO, _AGE_TEEN_HI = 7, 12
_AGE_ELDER_THRESHOLD: int = 75

# 业界依据：Smithsonian SEEC + Hands-On House 90min cap + Brain Balance 公式
_TODDLER_SINGLE_POI_MAX: int = 45      # ≤3 岁婴幼儿
_KID_PRESCHOOL_SINGLE_POI_MAX: int = 75  # 4-6 岁学龄前
_TEEN_SINGLE_POI_MAX: int = 120          # 7-12 岁学童
_ELDER_SINGLE_POI_MAX: int = 60         # ≥75 岁高龄长辈


def _resolve_age_caps(intent: IntentExtraction | None) -> tuple[int, list[str]]:
    """根据 intent.companions 推算单 POI 段时长上限 + 触发依据。
    取最严：5 岁娃 + 70 岁外婆同行 → min(75, 90)=75min。
    """
    if intent is None or not intent.companions:
        return MAX_NODE_DURATION_MIN, []
    
    caps: list[tuple[int, str]] = []
    for c in intent.companions:
        if c.age is None: continue
        if c.age <= _AGE_TODDLER_THRESHOLD:
            caps.append((_TODDLER_SINGLE_POI_MAX, f"含 {c.age} 岁婴幼儿"))
        elif c.age <= _AGE_KID_THRESHOLD:
            caps.append((_KID_PRESCHOOL_SINGLE_POI_MAX, f"含 {c.age} 岁学龄前儿童"))
        elif _AGE_TEEN_LO <= c.age <= _AGE_TEEN_HI:
            caps.append((_TEEN_SINGLE_POI_MAX, f"含 {c.age} 岁学童"))
        elif c.age >= _AGE_ELDER_THRESHOLD:
            caps.append((_ELDER_SINGLE_POI_MAX, f"含 {c.age} 岁高龄长辈"))
    
    if not caps:
        return MAX_NODE_DURATION_MIN, []
    
    caps.sort(key=lambda x: x[0])  # 取最严
    cap_min, _ = caps[0]
    reasons = [r for _, r in caps]
    return cap_min, reasons


def _age_aware_duration_critic(
    blueprint: PlanBlueprint,
    intent: IntentExtraction | None,
) -> list[BlueprintViolation]:
    """同行人年龄敏感的单段时长 critic。仅对 target_kind=POI 节点验。"""
    out: list[BlueprintViolation] = []
    cap_min, reasons = _resolve_age_caps(intent)
    if cap_min >= MAX_NODE_DURATION_MIN:
        return out
    
    reason_str = "、".join(reasons)
    expected_lo = max(45, cap_min - 15)
    
    for i, n in enumerate(blueprint.nodes):
        if n.target_kind != BlueprintTargetKind.POI: continue
        if n.duration_min > cap_min:
            out.append(
                BlueprintViolation(
                    critic="blueprint_age_aware_duration",
                    severity="hard",
                    message=(
                        f"节点[{i}]「{n.kind} · {n.target_id}」时长 {n.duration_min}min "
                        f"超过基于同行人年龄的建议上限 {cap_min}min（{reason_str}）。"
                        f"建议范围 {expected_lo}-{cap_min}min。"
                    ),
                    field_hint=f"nodes[{i}].duration_min",
                    expected_range=(expected_lo, cap_min),  # 新增
                )
            )
    return out
```

接入点：

```python
# backend/agent/blueprint.py:run_blueprint_critics
# 在 _temporal/_duration/_opening_hours 之后加：
for v in _age_aware_duration_critic(blueprint, intent):
    all_violations.append(v)
```

### Component 5: `Violation.expected_range` 字段（critic 升级）

```python
# backend/agent/v2/critics_v2.py
class Violation(BaseModel):
    code: ViolationCode
    severity: Severity
    message: str
    field_path: str
    expected_range: Optional[tuple[int, int]] = None  # 新增

# format_violations_for_llm 拼接
def format_violations_for_llm(violations: list[Violation]) -> str:
    critical = [v for v in violations if v.severity == Severity.CRITICAL]
    if not critical: return ""
    lines = [f"你产出的行程方案有 {len(critical)} 处违规需要修复："]
    for i, v in enumerate(critical, 1):
        line = f"{i}. {v.message}"
        if v.expected_range is not None:
            lo, hi = v.expected_range
            line += f"（建议范围 {lo}-{hi}）"
        lines.append(line)
    lines.append("请按上述建议重新调用工具或调整方案，重新输出 ItineraryResponse。")
    return "\n".join(lines)
```

### Component 6: `_overload_penalty`（ILS utility 升级）

```python
# backend/agent/planner_hybrid.py
def _overload_penalty(poi: Poi, intent: IntentExtraction) -> float:
    """单段时长 vs 同行人画像合理性。返回 [0, 0.3] 强惩罚值。"""
    if poi is None or not intent.companions: return 0.0
    cap = MAX_NODE_DURATION_MIN
    for c in intent.companions:
        if c.age is not None:
            if c.age <= 6: cap = min(cap, 75)
            elif c.age >= 75: cap = min(cap, 60)
    
    # poi.suggested_duration_minutes 双兼容
    suggested_raw = poi.suggested_duration_minutes
    if isinstance(suggested_raw, int):
        suggested = suggested_raw
    elif isinstance(suggested_raw, SuggestedDuration):
        # 取主导桶
        suggested = get_duration_for_companions(suggested_raw, intent.companions) or 90
    else:
        suggested = 90
    
    actual = min(suggested, cap)
    return 0.3 if actual < suggested else 0.0


# _utility 函数公式更新
score = (
    w.comfort * comfort_score
    + w.time * time_score
    + w.cost * cost_score
    + w.smoothness * smoothness_score
    - 0.5 * _overload_penalty(poi, intent)  # 新加
)
```

### Component 7: Narrator 接 critic_summary

```python
# backend/agent/narrator.py:build_narrator_user_message
def build_narrator_user_message(
    intent_dict: dict,
    itinerary_dict: dict,
    stage_label: str,
    critic_summary: str = "",       # 新增
    quality_warnings: list[str] = None,  # 新增（可选 meta-critic 输出）
) -> str:
    ...
    if critic_summary:
        parts.append(f"\n【critic 历史】{critic_summary}")
    if quality_warnings:
        parts.append(f"\n【质量提醒】" + "; ".join(quality_warnings))
    return "\n".join(parts)


# narrator_prompt.py 加规则段
NARRATOR_SYSTEM_PROMPT = """...
【主动质疑规则】
- 如收到 critic_summary（含 critical 历史），必须在文案中提一句质疑性建议。
- 例 1：critic_summary="第 2 段 5 岁娃 165min 已被 critic 拦下重出 75min" → 文案加"考虑到 5 岁宝贝注意力，主活动 75min 不会让宝贝累"
- 例 2：质量提醒含「老人单段过长」→ 加"老人体力有限，单点 60min 留出走走停停的时间"
"""
```

### Component 8: `_template_narration` 兜底质疑

```python
# backend/agent/narrator.py:_template_narration
def _template_narration(intent: IntentExtraction, itinerary: Itinerary, stage: str) -> str:
    ...
    # 兜底质疑：LLM 失败时模板也要让用户感知"AI 在为我考虑"
    has_young_kid = any(c.age is not None and c.age <= 6 for c in intent.companions)
    has_long_session = any(n.duration_min > 90 for n in itinerary.nodes if n.target_kind != "home")
    if has_young_kid and has_long_session:
        # 找出过长节点
        for n in itinerary.nodes:
            if n.target_kind != "home" and n.duration_min > 90:
                phrases.append(f"{n.kind}{n.duration_min}min 略长，宝贝可能会累，可以中途休息")
                break
    
    return "..."
```

---

## Data Models

### Schema 改动汇总

```python
# backend/schemas/domain.py
class SuggestedDuration(BaseModel):  # 新增
    default: NonNegativeInt
    kid_3_6: Optional[NonNegativeInt] = None
    kid_7_12: Optional[NonNegativeInt] = None
    senior: Optional[NonNegativeInt] = None
    multi_gen: Optional[NonNegativeInt] = None

class Poi(BaseModel):
    ...
    suggested_duration_minutes: Optional[Union[NonNegativeInt, SuggestedDuration]] = None  # 升级

class Restaurant(BaseModel):
    ...
    typical_dining_min: Optional[NonNegativeInt] = None  # 新增


# backend/schemas/intent.py
class PaceProfile(BaseModel):  # 新增
    single_session_max_min: Optional[int] = None
    total_active_min: Optional[int] = None
    break_every_min: Optional[int] = None
    preferred_dwell_min: Optional[int] = None

class IntentExtraction(BaseModel):
    ...
    pace_profile: Optional[PaceProfile] = None  # 新增


# backend/agent/v2/critics_v2.py
class ViolationCode(str, Enum):
    ...
    AGE_DURATION_MISMATCH = "age_duration_mismatch"  # 新增

class Violation(BaseModel):
    ...
    expected_range: Optional[tuple[int, int]] = None  # 新增
```

### Mock 数据迁移规则（一次性脚本）

```python
# scripts/migrate_mock_v2.py
# POI suggested_duration_minutes：按 type 升级 dict
_AGE_TIER_RULES = {
    "亲子博物馆":     {"default": 90,  "kid_3_6": 60, "multi_gen": 60},
    "亲子乐园":       {"default": 120, "kid_3_6": 75, "multi_gen": 60},
    "儿童阅读馆":     {"default": 60,  "kid_3_6": 45},
    "DIY 工坊":       {"default": 90,  "kid_3_6": 45},   # P019 修复
    "复合体验馆":     {"default": 100, "kid_3_6": 60, "senior": 60, "multi_gen": 60},  # P040 修复
    "主题乐园":       {"default": 180, "kid_3_6": 90, "senior": 60},  # P033 修复
    # ...（35 type × 1-4 桶 = 完整 audit 表见 Agent G report §4 方案 A）
}

# Restaurant typical_dining_min：按 cuisine
_CUISINE_DINING_MIN = {
    "健康轻食": 40, "咖啡": 45, "下午茶": 75, "杭帮菜": 75, "本帮菜": 75,
    "湘菜": 75, "粤菜": 90, "日料": 75, "法餐": 105, "西餐": 90, "韩料": 75,
    "火锅": 120, "烧烤": 105, "川菜": 75, "东南亚": 75, "甜品": 45,
}
# "高人均" / "商务体面" tag +15；"私房菜" tag +15

# Persona default_pace_profile
_PERSONA_PACE = {
    "u_dad":     {"single_session_max_min": 90, "break_every_min": 45, "preferred_dwell_min": 75},
    "u_biz":     {"single_session_max_min": 120, "preferred_dwell_min": 90},
    "u_grandma": {"single_session_max_min": 60, "break_every_min": 45, "preferred_dwell_min": 60},
    "u_solo":    {"single_session_max_min": 120, "preferred_dwell_min": 90},
    "u_couple":  {"single_session_max_min": 90, "preferred_dwell_min": 75},
}
```

---

## Correctness Properties

### Property 1: Mock dict 双兼容

**Validates: Requirements 1.1, 1.5**

任何 `Poi.suggested_duration_minutes`，无论是 `int` 还是 `SuggestedDuration` dict，`Pydantic.model_validate(p)` 都成功；`get_duration_for_companions(p, companions)` 永远返回合理整数（or None）。

### Property 2: Critic 主路径一致性

**Validates: Requirements 4.1, 4.3**

对同一 (blueprint, intent) 输入，blueprint critic `_age_aware_duration_critic` 与 critics_v2 `_check_age_aware_duration` SHALL 永远返回**等价的违规结果**（违规节点 index + 期望区间相同）。

### Property 3: ExpectedRange 一致性

**Validates: Requirements 4.2, 4.4**

任何 `Violation.expected_range = (lo, hi)`，`format_violations_for_llm` 输出 SHALL 含「建议范围 lo-hi」自然语言；不含字段名 `expected_range`、不含 `nodes[i]`、不含 dot-path。

### Property 4: Narrator 主动质疑触发

**Validates: Requirements 6.1, 6.4**

WHEN intent.companions 含 ≤ 6 岁儿童 + itinerary.nodes 任一 target_kind="poi" 且 duration_min > 90, THE narrator 输出（无论 LLM 还是模板路径）SHALL 含质疑文案（"主活动 N 分钟略长" / "宝贝注意力" / "中途休息" 等关键词之一）。

### Property 5: 端到端命中率

**Validates: Requirements 3.6, 9.3**

5 岁娃家庭场景跑 5-10 次端到端，`duration_min ∈ [60, 90]` 命中率 ≥ 90%（首轮 + backprompt + ILS 兜底累计）。

### Property 6: 字段透传完整性

**Validates: Requirements 2.1, 2.2**

WHEN `_poi_preview(p)` 输出, THE 字段 `suggested_duration_minutes` SHALL 永远是 `int` 或 None（投影后单值；不暴露 dict 结构给 LLM）；同理 `_restaurant_preview` 含 `typical_dining_min`。

---

## Error Handling

### 风险与降级

```text
| 风险                                     | 缓解 / 降级                                                  |
|-----------------------------------------|------------------------------------------------------------|
| LLM 漏抽 companions[].age（< 95% 命中率） | _resolve_age_caps 退化为 default 桶；critic 不阻断；narrator template 兜底 |
| mock 数据 dict 升级让旧测试断言失效        | Pydantic Union 双兼容 + helper 函数 + 单测 fixture migration（W1.1 工时上浮到 5h+）|
| narrator LLM 不听 critic_summary 指令     | template 路径强制兜底 + 温度从 0.7 降到 0.5 + few-shot 加示例 |
| critic 重生成命中率 < 95%                  | ILS 兜底（utility overload_penalty）+ give_up 兜底 + narrator 降级模板 |
| prompt cap 1500→2200 破坏既有 6-10 个测试 | W3 完成时同步改测试断言；e2e 跑全套 backend tests 0 红灯     |
| meta_critic_node 引发"加节点风潮"          | 本 spec 不加；spec C 单独评估 + ENV 开关                      |
```

---

## Decisions Log

```text
| 决策                                     | 决定                       | 来源                            |
|-----------------------------------------|---------------------------|--------------------------------|
| 单段时长决策权                            | D 主防 + E 兜底             | Phase 4 §2 冲突 1               |
| expected_range 暴露                       | 弱化版（自然语言）           | Phase 4 §2 冲突 2               |
| Restaurant 时长 B+G+D 捆绑                 | 同 PR 合入                  | Phase 4 §2 冲突 3               |
| fallback 路由策略                         | 拒按违规类型路由            | Phase 4 §2 冲突 4               |
| mock dict 不分 v1/v2                      | 直接原地升级 + Union 双兼容 | Phase 4 §2 冲突 5               |
| meta_critic_node 不本 spec               | 留 spec C，配 ENV 开关       | Phase 4 §7 红旗 5               |
| NodeDecider 不升级 NodePlanHint           | 拒（W6.5 砍）              | Phase 4 §2 冲突 1 取舍          |
| W4.4 _check_opening_hours_after_assemble | 砍（与 5 岁娃反例无关）     | Phase 4 §5.1                    |
| 演示场景集 §四 加 S9                      | 加（评分加分项）             | Phase 4 §3 漏点 2               |
| pitfalls.md 主动追加防再犯条款            | 加（≥ 3 条）                | Phase 4 §3 漏点 4               |
| spec A vs spec B 顺序                     | A 完结 + 联调通过 + demo 验收后 B 启动 | Phase 4 §6 + §8.4   |
```
