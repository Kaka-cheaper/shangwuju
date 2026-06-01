# Requirements Document

## Introduction

浏览器真 LLM 实测（S3 家庭主线 / S5 情侣看展）暴露两个直接影响评委对「规划质量」与「Agent 可信度」判断的硬伤：

1. **narration 漏段（Bug #1）**：行程实际 3 段（活动→用餐→活动），但暖语气开场白讲到「吃饭」就收尾。S3 漏掉餐后的「森林儿童探索乐园」，S5 漏掉餐后的「万达 IMAX 电影院」（还是行程标题主角）。两场景都复现 = narrator 系统性 bug——LLM prompt 的 50-80 字硬上限 + few-shot 全是「出发→主活动→用餐→回家」收尾，导致 LLM 学会「讲到吃饭就结束」；模板兜底路径还有 `phrases[:3]` 硬截断。

2. **意图诉求在检索阶段没有相关性保证（Bug #2）**：S5 用户明确说「看个展」，experience_tags 也正确抽出「看展」，mock 里也有 P002 西溪艺术展中心（tag 含「看展」、suitable_for 情侣亲密、3.8km ＜ 5km，召回阶段就在候选里），但最终方案是 猫咖→甜品店→电影院，**一个展都没有**。

   根因不是工具不成熟，而是 **POI 检索是「召回宽松、排序只按 rating、top_k 静态截断」的两阶段检索，缺少按用户明示诉求做的相关性重排**（RAG 范式里的 recall → rerank 缺了 rerank）：

   ```text
   | 阶段        | 现状                                    | 问题                          |
   |-------------|----------------------------------------|------------------------------|
   | 召回 recall | has_any_tag 宽松 OR（命中任一 tag 即过）| 召回够，P002 在候选里          |
   | 排序 rank   | 只按 rating 倒序                        | ★ 病根：忽略 query 相关性     |
   | 截断 top_k  | rating 前 5 喂 LLM 预览                 | 高分泛候选把对味的 P002 挤出   |
   ```

   餐厅侧已有对称机制 `_rerank_by_preferred_cuisine`（块B-2 已落地），POI 侧缺失。本 spec 在 POI 侧补上**轻量词法重排**——不引入 embedding / 交叉编码器（60 个固定 mock POI 杀鸡用牛刀），不引入活动诉求白名单（违背 D9 意图开放铁律），不把 preferred_types 硬塞进工具做精确过滤（会归零）。

本 spec 修这两个 bug：① narration 完整复述所有活动节点；② 在 POI 检索层补「按明示诉求的轻量词法重排」，让明示诉求在检索阶段就有相关性优先级，从根上保证「说了看展方案里真有展」。

## Glossary

- **narration（暖语气开场白）**：行程出炉时 narrator 生成的 2-3 句导游口播，复述行程要点。
- **活动节点**：itinerary.nodes 中 target_kind ∈ {poi, restaurant} 的中间节点（不含首尾 home）。
- **明示诉求（explicit desire）**：用户点名想做的活动/想去的场所类型，体现在 `preferred_poi_types`（自由文本，如 展览/KTV/密室）或 `experience_tags`（词典词，如 看展/网红打卡）。
- **召回（recall）**：`search_pois` 用 has_any_tag 宽松过滤 + 距离/social_context 硬过滤得到的候选集合。
- **重排（rerank）**：拿到召回候选后，按用户明示诉求与 POI 的 type/name/tags 词法相关性，把命中诉求的候选稳定前置，使其进入 top_k 预览。本 spec 核心。
- **镜像通道**：用户点名的活动品类若同时是 experience_tag 词典词（如「看展」），intent 解析时**同时**写进 `preferred_poi_types`，作为干净的高信号重排通道（experience_tags 因含氛围词如「安静聊天」会带进泛候选，不适合直接当重排信号）。

## Requirements

### Requirement 1: narration 完整复述所有活动节点

**User Story:** 作为用户/评委，我希望 Agent 的暖语气开场白把行程里**每一个活动**都讲到，不要讲到吃饭就收尾，否则我会以为吃完就回家。

#### Acceptance Criteria

1. WHEN 行程含 N 个活动节点（target_kind ∈ poi/restaurant）THEN narration SHALL 复述全部 N 个节点的关键信息（地点 + 大致时间/顺序），不得在中间节点（如用餐）截断。
2. WHEN 行程是「活动→用餐→活动」结构（餐在中间）THEN narration SHALL 把餐后的活动也讲出来（S3 的探索乐园 / S5 的电影院不得漏）。
3. WHEN LLM narration 因字数限制漏讲节点 THEN 系统 SHALL 按节点数放宽字数上限以容纳全部节点（节点多时允许更长，但仍简洁）。
4. WHEN 走模板兜底路径（规则模式/LLM 失败）THEN 模板 SHALL 同样复述全部活动节点（去掉 phrases[:3] 截断）。
5. narration SHALL 仍保持暖语气、不分点、不用专业词（保留现有风格规范）。

### Requirement 2: 明示诉求镜像到高信号重排通道

**User Story:** 作为系统，当用户点名某活动品类（看展/KTV/密室等）时，即使它恰好也是 experience_tag 词典词，我也要把它放进一个干净的高信号通道，好让检索层据此做相关性重排。

#### Acceptance Criteria

1. WHEN 用户点名活动品类（看展/密室/桌游/KTV/攀岩/看电影等）THEN intent 解析 SHALL 把该品类原样写进 `preferred_poi_types`（自由文本），无论它是否同时落进 experience_tags。
2. SHALL NOT 引入活动品类枚举/白名单/`if type==` 分支（D9 意图开放铁律）——只是「点名的就镜像」这一条通用规则。
3. SHALL NOT 凭空添加用户没提的活动品类到 preferred_poi_types（保持现有 intent prompt 的「禁止凭空添加」约束）。
4. WHEN 用户没点名任何活动品类 THEN preferred_poi_types SHALL 保持空数组（零回归）。

### Requirement 3: POI 检索层按明示诉求做轻量词法重排（治本核心）

**User Story:** 作为用户，我说了「看展」，我希望方案里**真的有展**——靠检索阶段就把对味的展馆排到 LLM 看得见的位置，而不是事后道歉。

#### Acceptance Criteria

1. WHEN 用户有明示诉求（preferred_poi_types 非空）且 mock 内存在词法相关的 POI 候选 THEN `search_pois_for_intent` SHALL 把这些候选稳定前置（与 POI 的 type/name/tags 双向 substring 命中），使其进入 top_k 预览。
2. WHEN 有明示诉求 THEN 检索 SHALL 扩大抓取池（limit→15）后再重排截断回原 limit，避免词法命中的候选在 rating top_k 截断阶段被高分泛候选挤掉（与餐厅侧 `_rerank_by_preferred_cuisine` 扩池策略对称）。
3. 重排 SHALL 是**宽松词法匹配**（双向 substring，命中 type/name/tags 任一即算相关），SHALL NOT 把 preferred_types 作为精确过滤条件塞进 search_pois 工具（精确 `poi.type not in` 匹配会因「看展 ≠ 展览」归零）。
4. WHEN 无明示诉求（preferred_poi_types 为空）THEN SHALL 原序返回（保持现有 rating 排序，零回归）。
5. 重排 SHALL 在编排层（search_adapter）实现，SHALL NOT 改 search_pois 工具（守 §3.4 Tool 对场景无感铁律），SHALL NOT 改 graph/build.py 拓扑，SHALL NOT 破坏既有距离/时长/social_context 约束。

### Requirement 4: 诚实告知兜底（重排仍未满足时）

**User Story:** 作为用户，万一重排后方案里还是没有我要的活动（本地确实没这类场所/被距离过滤），我希望 Agent 像处理餐饮品类一样坦白，而不是默默给个不相关的。

#### Acceptance Criteria

1. WHEN 用户明示诉求（preferred_poi_types）未被行程中任一 POI 节点满足（按 type/name/tags 词法判定）THEN 系统 SHALL 在 narration 中诚实告知「你想要的 X 这次没安排上，先帮你选了替代」（复用现有诚实告知通道）。
2. 检测 SHALL 复用与重排相同的词法匹配逻辑（同源，避免重排判命中但告知判未命中的不一致）。
3. WHEN 明示诉求**已被满足**（行程有词法相关的 POI）THEN 系统 SHALL NOT 触发诚实告知（不画蛇添足）。
4. 诚实告知检测 SHALL 是纯函数、fail-safe（异常返 []，不阻断 narration）。
5. 本需求是**兜底**，与 R3 重排（治本）构成双保险；R3 重排让绝大多数明示诉求在方案里被满足，R4 只在仍未满足时坦白。

### Requirement 5: 不破坏既有行为与基线

**User Story:** 作为维护者，我要确保这些修复不回归既有测试、不破坏其它场景。

#### Acceptance Criteria

1. WHEN 跑全量 backend pytest THEN 全部 SHALL 通过（含既有 narrator / 诚实告知 / blueprint prompt / search_adapter 测试）。
2. WHEN intent prompt 加镜像规则 THEN 既有 intent 解析测试 SHALL 不回归（preferred_poi_types 现状为空的场景仍为空）。
3. WHEN 真 LLM 实测 S5 看展 THEN 方案 SHALL 含展（重排生效）或诚实告知没安排上展（兜底）；narration SHALL 复述全部活动节点。
4. WHEN 真 LLM 实测既有场景（S1/S3/S4/S6/S7/S8）THEN SHALL 不回归（narration 完整、无误报诚实告知、明示诉求被满足）。
5. 前端 verify:all SHALL 4/4（本 spec 纯后端）。

## Out of Scope（本 spec 不处理，记录待办）

- 时间轴 kind 标签错配（猫咖标成「看展」）——属 blueprint LLM 输出 kind 与 target 不一致，单独小修，不在本 spec。
- 对话框 dock「收起」按钮点击无效——前端交互 bug，单独前端修。
- 顶栏 planner 默认「规则」vs 后端「llm」不一致——前端默认值对齐，单独小修。
- blueprint prompt 也可加「优先选匹配诉求的 POI」一句作为锦上添花，但**重排已让对味候选进 top_k 预览**，是治本主力；blueprint prompt 不作为本 spec 的硬依赖（避免动 2200 cap 风险），仅在 R3 重排验证不足时作为补充手段（设计文档备注）。
