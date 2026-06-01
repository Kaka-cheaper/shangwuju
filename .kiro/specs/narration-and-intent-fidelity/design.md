# Design Document

## Overview

两块改动，全部收敛在 narrator + intent prompt + 编排层重排，不动 graph 拓扑、不动 search_pois 工具：

- **块 A（R1）narration 完整复述**：放宽 narrator LLM prompt 字数上限（按节点数动态）+ few-shot 加「餐在中间、餐后还有活动」的范例；模板兜底去掉 `phrases[:3]` 截断，改为复述全部活动节点。
- **块 B（R2+R3+R4）明示诉求轻量词法重排（治本）+ 诚实告知（兜底）**：
  - R2 镜像通道：intent prompt 加「点名的活动品类同时镜像进 preferred_poi_types」一句，给重排提供干净高信号。
  - R3 治本核心：编排层 `search_pois_for_intent` 新增 `_rerank_by_preferred_poi_types`（镜像餐厅侧已有的 `_rerank_by_preferred_cuisine`），把词法相关的 POI 稳定前置进 top_k 预览。
  - R4 兜底：新增 `detect_unmet_poi_preference` 纯函数（与重排同源词法），把活动诉求未满足也接入诚实告知通道。

## 设计取舍：为什么用轻量词法重排，不用 embedding RAG / LLM 打分

```text
| 方案                          | 取舍 | 理由                                                       |
|-------------------------------|------|------------------------------------------------------------|
| embedding / 交叉编码器 RAG    | ✗    | 60 个 mock POI + 固定 type 词表，杀鸡用牛刀；加依赖 + 延迟  |
| LLM 语义打分重排              | ✗    | preference_scorer.py（ItiNera 范式）只在 ILS 路；主路再加 = 多 1 次 LLM 调用拖慢 demo |
| 轻量词法重排（substring 前置）| ✓    | 餐厅侧已有 _rerank_by_preferred_cuisine，POI 侧补对称的；零新依赖、零延迟、架构对称 |
```

```text
| 为什么不把 preferred_types 硬传进 search_pois 工具                          |
|---------------------------------------------------------------------------|
| 工具层是精确 `poi.type not in preferred_types` 匹配。用户说「看展」，       |
| POI 的 type 是「展览/画廊/影像馆」——「看展」∉ 这些 → 精确匹配归零，         |
| 一个候选都搜不到。所以重排放在编排层做宽松双向 substring，召回仍走宽松 OR。  |
```

## Architecture

```text
块A narration 完整性：
  narrate_node → generate_narration
    ├ use_llm=True  → _call_llm_narrator → narrator_prompt（字数按节点弹性 + 餐中 few-shot）
    └ use_llm=False → _template_narration（去 phrases[:3]，全节点复述）

块B 明示诉求重排（治本）+ 诚实告知（兜底）：
  意图期（R2 镜像通道）:
    intent_parser_prompt → 「点名活动品类镜像进 preferred_poi_types」
  检索期（R3 治本核心）:
    execute search_pois_worker → search_pois_for_intent
      └ [新增] _rerank_by_preferred_poi_types(pois, preferred_poi_types)
         · 有诉求 → 扩池 limit=15 → 词法命中前置 → 截断回 limit
         · 无诉求 → 原序（零回归）
  文案期（R4 兜底）:
    narrate_node → _detect_unmet_poi(intent, itinerary)  ← 新增，类比 _detect_unmet_cuisines
      → detect_unmet_poi_preference(...)  ← narrator.py 新增纯函数（与重排同源词法）
      → 未满足 → 并入 unmet 信息 → narrator 诚实告知
```

### 词法匹配规则（R3 重排 + R4 检测共用，单一函数 SoT）

唯一的匹配判定（不引入映射表/白名单，纯字符串运算）：

```text
match(desire_word, poi) :=
    desire_word 与 poi.type 双向 substring 命中
 OR desire_word 与 poi.name 双向 substring 命中
 OR 任一 poi.tags 与 desire_word 双向 substring 命中

双向 substring(a, b) := (a in b) or (b in a)，两者非空
```

为什么够用（对着 mock type 词表验证）：

```text
| 诉求词（preferred_poi_types） | 命中 POI                              | 命中字段          |
|------------------------------|--------------------------------------|------------------|
| 看展                         | P002 西溪艺术展中心 type=展览 tags含「看展」| tags「看展」直接命中 |
| 展览                         | P002 type=展览                        | type「展览」substring |
| KTV                          | P026/P027 type=KTV                    | type 精确         |
| 密室                         | P013 type=密室                        | type             |
| 剧本杀                       | P024/P025 type=剧本杀                 | type             |
| 桌游                         | P014 type=桌游馆                      | type substring   |
| 猫咖                         | P022/P023 type=猫咖                   | type             |
| 攀岩                         | P035 name=Vertical攀岩馆 type=室内运动馆| name「攀岩」substring |
| 真人CS                       | P058 type=真人 CS                     | name/type substring |
| 看电影/电影                  | P028/P029 type=电影院                 | name/tags（「电影」in「电影院」） |
```

「看展」靠 tags 命中（P002 tags 含「看展」），「展览/画廊」靠 type 命中，「攀岩」靠 name 命中——三路并联覆盖，无需维护任何映射字典。

## Components and Interfaces

### 块 A：narration 完整复述

#### A1: narrator_prompt 字数按节点弹性 + 餐中 few-shot（R1.1/R1.2/R1.3）

文件：`agent/intent/prompts/narrator_prompt.py`

- 把「总字数严格控制在 50-80 字」改为**按节点数弹性**：「1-2 个活动 ≤80 字；3 个活动 ≤120 字；4+ 活动 ≤150 字。**必须覆盖每一个活动，不许讲到用餐就收尾**」。
- 加一条**硬规则**：「行程里有几个活动就讲几个；用餐排在中间时，餐后的活动必须讲出来，不能在用餐处收尾」。
- 加 1 条 few-shot：输入「猫咖→甜品店(餐)→电影院」三节点 → 输出把电影院也讲出来的范例。
- build_narrator_user_message 已传 itinerary.nodes（含全部节点）——无需改数据，只改 prompt 指令。

#### A2: _template_narration 去截断（R1.4）

文件：`agent/intent/narrator.py`

- `body = "，".join(phrases[:3])` → 复述全部活动节点；>6 活动时温和截断（前 6 + 「等」）避免极端长文。
- demo 场景最多 3-4 活动，去掉 [:3] 即可让餐后活动出现。

### 块 B：明示诉求重排（治本）+ 诚实告知（兜底）

#### B1: intent prompt 镜像通道（R2）

文件：`agent/intent/prompts/intent_parser_prompt.py`

现有「明示餐饮/活动品类必须保留」段已要求词典外品类写进 preferred_poi_types。本 spec 补一句：**词典内活动品类（如「看展」是 experience_tag 词典词）被点名时，也要镜像写进 preferred_poi_types**，理由说明给 LLM：「preferred_poi_types 是给检索做相关性优先的高信号通道」。

- 守住既有约束：禁止凭空添加（R2.3）、用户没点名则保持空数组（R2.4）。
- few-shot 不强制改（既有 few-shot preferred_poi_types 多为空，符合「没点名就空」）；可选加 1 条「看展」镜像 few-shot 增强稳定性。

#### B2: search_pois_for_intent POI 诉求重排（R3，治本核心）

文件：`agent/runtime/tools/search_adapter.py`

镜像现有 `_rerank_by_preferred_cuisine` + 餐厅扩池逻辑：

```python
def _rerank_by_preferred_poi_types(
    pois: list[Poi], preferred_poi_types: list[str]
) -> list[Poi]:
    """把 type/name/tags 与 preferred_poi_types 任一词双向 substring 命中的候选稳定前置。

    无 preferred_poi_types 或无命中 → 原序返回（稳定排序，零回归）。
    与餐厅侧 _rerank_by_preferred_cuisine 同源词法。
    """
    if not preferred_poi_types:
        return pois
    prefs = [p for p in preferred_poi_types if p]
    if not prefs:
        return pois

    def _match(poi: Poi) -> bool:
        fields = [poi.type or "", poi.name or "", *(poi.tags or [])]
        for pref in prefs:
            for f in fields:
                if f and ((pref in f) or (f in pref)):
                    return True
        return False

    matched = [p for p in pois if _match(p)]
    rest = [p for p in pois if not _match(p)]
    return matched + rest
```

在 `search_pois_for_intent` 中：
- 有 `intent.preferred_poi_types` 时把 `limit` 扩到 `max(limit, 15)`（与餐厅 fetch_limit 对称），搜回来后调 `_rerank_by_preferred_poi_types`，再 `[:limit]` 截断回原 limit。
- 无诉求 → 不扩池、不重排，原样返回（R3.4 零回归）。
- 重排发生在「拿到 candidates list」之后、return 之前；relaxed_tags 不受影响。

#### B3: detect_unmet_poi_preference 纯函数（R4 兜底）

文件：`agent/intent/narrator.py`（与 detect_unmet_cuisine_preference 并列）

```python
def _poi_desire_match(desire: str, poi_type: str, poi_name: str, poi_tags: list[str]) -> bool:
    """与 _rerank_by_preferred_poi_types._match 同源的词法判定（抽成共享 helper）。"""
    fields = [poi_type or "", poi_name or "", *(poi_tags or [])]
    return any(f and ((desire in f) or (f in desire)) for f in fields)

def detect_unmet_poi_preference(
    preferred_poi_types: list[str],
    itinerary_poi_types: list[str],
    itinerary_poi_names: list[str],
    itinerary_poi_tags: list[str],
) -> list[str]:
    """检测明示 POI 诉求是否未出现在最终行程的 POI 里。

    - 对每个 preferred 诉求词：与行程任一 POI 的 type/name/tags 词法命中 → 满足
    - 未命中 → 计入未满足
    - 无 preferred_poi_types → 返空（不告知）
    - fail-safe：异常返 []
    返回未满足诉求词列表（保序去重）。
    """
```

注意：餐饮品类（烧烤/火锅）已被现有 `detect_unmet_cuisine_preference` 处理（走 cuisine 字段）；POI 版只处理活动场所类诉求。两者并列，narrate_node 合并。为避免「烧烤」既走 cuisine 又走 POI 双重告知，POI 检测保留现有 cuisine token 启发式的「反向排除」——含明显餐饮 token 的诉求词交给 cuisine 版，不在 POI 版重复计。

#### B4: narrate_node 接线 + 诚实告知泛化（R4.1）

文件：`agent/graph/nodes/narrate.py`

- 现有 `_detect_unmet_cuisines(intent, itinerary)` 旁新增 `_detect_unmet_poi(intent, itinerary)`：遍历 itinerary 的 POI 节点（target_kind=poi），靠 target_id 查 mock pois 取 type/name/tags，调 `detect_unmet_poi_preference`。
- narrate_node 把 `unmet_cuisines + unmet_pois` 合并成统一 `unmet_desires` 列表传给 generate_narration。
- `generate_narration` / `_call_llm_narrator` / `_template_narration` / `stream_llm_narrator` 的 `unmet_cuisines` 形参语义泛化为「未满足诉求」（保留形参名以减小改动面，或新增 `unmet_pois` 形参合并）——设计选**合并为 `unmet_desires`**，让 narrator prompt 的诚实告知规则对餐饮/活动统一表述。
- narrator_prompt 的「诚实告知规则」文案从「品类」泛化为「诉求」（兼容餐饮品类 + 活动场所），few-shot 各保留 1 条（cuisine 1 条 + 活动 1 条）。

## Data Models

无新增 Pydantic schema。新增内部纯函数 + 共享词法 helper。复用现有 IntentExtraction / Itinerary / Poi。`preferred_poi_types` 字段已存在（schemas/intent.py），无需改 schema。

## Error Handling

```text
| 场景                          | 处理                                          |
|------------------------------|----------------------------------------------|
| _detect_unmet_poi 异常        | fail-safe 返 []（不阻断 narration）            |
| 明示诉求无对应 mock POI        | 重排无命中 → 原序；R4 仍判未满足 → 诚实告知     |
| 重排后匹配 POI 仍未入选         | R4 诚实告知兜底                                |
| narration 节点过多            | 字数上限按档放宽，仍超则温和截断 + "等"          |
| preferred_poi_types 为空      | 重排原序 + 检测返空（零回归）                   |
```

## Testing Strategy

### 单元测试（新增/扩展）

- `test_search_adapter_poi_rerank.py`（新增，R3）：
  - preferred_poi_types=["看展"] → tags 含「看展」的 POI 前置
  - preferred_poi_types=["KTV"] → type=KTV 的 POI 前置
  - preferred_poi_types=[] → 原序返回（零回归断言）
  - 约束 tag 不进 preferred_poi_types（由 intent 层保证，这里测重排函数对空诉求不动）
- `test_detect_unmet_poi.py`（新增，R4）：
  - 看展未满足（行程无展类 POI）→ 命中
  - 看展已满足（行程有 type=展览 或 tags 含看展）→ 不命中
  - preferred_poi_types=["KTV"] 行程无 KTV → 命中
  - 空 preferred_poi_types → 返 []
  - 餐饮 token 诉求（烧烤）不在 POI 版重复计
- `test_narrator_full_nodes.py`（新增，R1）：
  - _template_narration 对 3 节点行程复述全部 3 个（含餐后活动）
  - narrator_prompt 含「餐后活动必须讲」规则 + 三节点 few-shot 关键词
- 扩 `test_intent_parser_prompt`（若存在）/ 新增断言：intent prompt 含「镜像 preferred_poi_types」规则关键词。

### 真 LLM 端到端（评委路径）

- S5 情侣看展：方案含展（重排生效）或诚实告知没安排上展（兜底）+ narration 复述全部活动（含电影院/展）
- S3 家庭主线：narration 复述全部 3 段（含探索乐园）
- S1 KTV / S6 闺蜜：明示诉求被满足，不误报诚实告知
- 既有 S4/S7/S8 不回归

### 回归

- 全量 backend pytest 全过（R5.1）
- intent 解析既有测试不回归（R5.2）
- 前端 verify:all 4/4（R5.5）

## Verification Plan

```text
| 需求 | 验证方式                                                          |
|-----|------------------------------------------------------------------|
| R1  | test_narrator_full_nodes + 真 LLM S3/S5 narration 全节点          |
| R2  | intent prompt 含镜像规则关键词 + 真 LLM S5 抽出 preferred_poi_types=["看展"] |
| R3  | test_search_adapter_poi_rerank + 真 LLM S5 候选预览含展 → 方案含展 |
| R4  | test_detect_unmet_poi + S5 重排仍未满足时诚实告知                  |
| R5  | 全量 pytest + intent 不回归 + 前端 verify + 既有场景不回归         |
```

## Correctness Properties

### Property 1: narration 不漏活动节点
N 个活动节点 → narration 复述 N 个（模板路径确定性可验；LLM 路径靠 prompt + 真 LLM 实测）。**Validates: Requirements 1.1, 1.2, 1.4**

### Property 2: 明示诉求词法命中则前置
preferred_poi_types 非空且存在词法相关 POI → 重排后该 POI 排在所有未命中候选之前。**Validates: Requirements 3.1, 3.2**

### Property 3: 无诉求零回归
preferred_poi_types 为空 → 重排原序返回、检测返 []。**Validates: Requirements 3.4, 5.2**

### Property 4: 重排与检测同源
R3 重排判命中的 POI，R4 检测必判已满足（共用同一词法 helper，不会重排说有、告知说没有）。**Validates: Requirements 4.2, 4.3**

### Property 5: 不把诉求作精确过滤
preferred_poi_types 不传入 SearchPoisInput.preferred_types（避免精确匹配归零）；召回仍走宽松 has_any_tag。**Validates: Requirements 3.3, 3.5**

### Property 6: 不破坏基线
全量 pytest 过、不动 graph 拓扑、不动 search_pois 工具。**Validates: Requirements 5.1, 5.3, 5.4**
