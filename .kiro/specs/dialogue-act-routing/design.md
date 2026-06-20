# Design Document — dialogue-act-routing

> 把被切碎、且三处共谋出 bug 的「识别反馈」，巩固成一个**完备的对话行为分类器（dialogue act classifier）**：已有方案后用户的每句话，归到**唯一一种**对话行为，再路由。绞杀榕（Strangler Fig）式局部巩固，**不重写架构、不动 L0 注入闸与横切防御**。

本文档按方法论组织（见 memory `ground-decisions-in-prior-art`）：每个关键决策都先命名成经典问题、给出业界成熟范式、用证据论证为什么对、并列出备选为什么不选。

---

## 0. 触发本 spec 的 bug（证据先行）

已有方案后输入「我妈最近膝盖不太好，走不远」→ 被**直接当反馈重规划**，不出气泡。复现数据（在 has_itinerary=True 下）：

| 探针句 | L1 强信号判反馈? | 该重规划? |
|---|---|---|
| 我妈膝盖不太好走不远 / 我太累了 / 不喜欢吃辣 / 不喜欢人多 / 孩子腻了 / 真没意思 / 身体不太好 | 7/7 **是** | 7/7 **不该** |

误吞词：`不太好 / 太累 / 不喜欢 / 没意思 / 腻了`。其中**只有「膝盖」那句**软约束 sniff 能命中——证明「命中软约束词才放行」的补丁只救得了 1/7。

**历史溯源**（方法论：先搞懂前一个决策为什么这么做）：`feedback-routing-fix` 的 design C1 当初为修「语义反馈漏判」，主动把 `不太好/不喜欢/一般/没意思` 加进 `_FEEDBACK_KEYWORDS`。方向对（全集是高召回粗筛），但后续这些**歧义词又渗进了 `_STRONG_FEEDBACK_KEYWORDS`（强信号子集）**——而强信号子集的契约是「命中即不调 LLM 直接拍板」。歧义词拿到了「直接拍板」的特权，就是这次的根因。

---

## 1. Prior Art — 每个决策点对应的经典问题与成熟范式

| # | 这个项目里的点 | 它是哪个经典问题 | 成熟范式 / 为什么这样才对 | 证据 |
|---|---|---|---|---|
| P1 | 判断「用户这句话是什么」 | 对话行为分类（Dialogue Act Classification） | 话语要分到一组**互斥且完备(MECE)**的 act（INFORM/REQUEST/CONFIRM/...）。完备体系保证每句有唯一归属，消灭「垃圾桶类」。当前 6 类不完备 → ambiguous 成垃圾桶 | Stolcke 2000（42 互斥 DA）; Montenegro 2019（任务型 taxonomy 含 question/inform/agree）[arXiv 2012.04080](https://arxiv.org/pdf/2012.04080) |
| P2 | 规则在前、大模型在后 | 级联分类（cascade / tiered classification） | cheap-first，但**规则层只配放高精度信号**（命中即几乎一定对），歧义下沉给能看上下文的大模型。把歧义词放规则层=用高精度岗位干低精度活 | routing best practices [Arize](https://arize.com/blog/best-practices-for-building-an-ai-agent-router/) |
| P3 | 回答「这家贵不贵/远吗」 | 接地问答 + 校准弃答（grounded QA + abstention） | 答案必须接地到查到的数据；查不到就**弃答**、明说不知道、不编造（编造=faithfulness hallucination）。凭经验补充时**标注来源** | [arXiv 2409.11242](https://arxiv.org/pdf/2409.11242)、[Hallucination Survey 2510.06265](https://arxiv.org/html/2510.06265v2) |
| P4 | 「膝盖不好」→ 填「适合老人」 | 槽位填充（slot filling） | 约束是往语义框架增量填 slot，不是每次推翻重来；NLU = intent + slot 两段式 | [Slot filling survey 2011.00564](https://arxiv.org/pdf/2011.00564) |
| P5 | 要不要整体重构路由层 | 重构 vs 重写（refactor vs rewrite） | **架构能支撑未来 2-3 年目标 → 增量重构几乎总是更安全更快**；Strangler Fig 逐环节替换，不大爆炸；重写会丢失已积累的正确性 | [Strangler Fig, microservices.io](https://microservices.io/post/refactoring/2023/06/21/strangler-fig-application-pattern-incremental-modernization-to-services.md.html)、[Rewrite vs Refactor](https://www.nustechnology.com/blog/rewrite-vs-refactor-why-we-almost-always-choose-incremental-modernization)、[Anthropic: 只在确有必要时加复杂度](https://www.anthropic.com/engineering/building-effective-agents) |

---

## 2. 现状：5 环节质量评估（绞杀榕只动该动的）

| 环节 | 代码 | 业界对应 | 质量 | 处置 |
|---|---|---|---|---|
| L0 拦截攻击 | `injection_detector` | 输入消毒 / 纵深防御 | ✅ 高（动作词+对象词组合命中、零误报、fail-open、不回显） | **不动** |
| L1 识别反馈 | `feedback_detector.looks_like_feedback_strong` | 级联分类规则层 | ❌ 歧义词当强信号（违反 P2） | **改 C1** |
| L1.5 快路 | `_looks_like_new_planning` | 级联分类规则层 | ⚠️ 组合命中对，但「有方案→feedback」与 L3 重复 | **C2 收口** |
| L2 大模型分类 | `classify_input`+`router_prompt` | 对话行为分类(LLM) | ⚠️ 工程扎实，但 6 类不完备 + prompt 教唆「不太好→ambiguous」 | **改 C5** |
| L3 兜底归并 | `router_node` | 上下文延续 | ❌ ambiguous 一刀切当 feedback | **改 C3** |
| L3.5 软约束 | `soft_constraint_sniffer` | 槽位填充 | 新加，散挂在最后 | **C2 收编** |
| 横切 | `prompt_guard`/`fallback`/`_sanitize_cta_chips` | 纵深防御/降级/防漂移 | ✅ 高 | **不动** |

## 2.1 病根 — 三重共谋（联动证据，单改 L1 无效）

```
L1 词表:「不太好」是强信号        → 秒判 feedback
L2 prompt: 明文「不太好→判模糊」    → 判 ambiguous   （就算 L1 改了，这里还推）
L3 归并:「模糊→当反馈」            → feedback        （就算 L2 改了，这里还兜）
```

→ **C1 + C5 + C3 必须一起改**，否则按下葫芦浮起瓢。

---

## 3. 目标设计：完备 MECE 的对话行为 → 路由

已有方案后，用户每句话归到唯一一类（依据 P1）：

| 对话行为 | 例子 | 去向 | 依据 |
|---|---|---|---|
| 嫌方案·要改 | 太远了换近点 | refiner 重规划 | REQUEST-change |
| 提约束·没说改 | 我妈膝盖不好 / 我太累了 | **主动问（气泡+按钮）** | P4 + 礼貌确认 |
| 提约束·明说改 | 帮我换成适老的 | refiner 重规划 | REQUEST+inform |
| 提问 | 这家贵不贵 / 远吗 | **查数据接地回答** | P3 |
| 确认采纳 | 好的就这个 | confirm 流程 | CONFIRM |
| 追加一项 | 还想喝咖啡 | refiner 加站 | P4 增量 |
| 闲聊/情绪 | 你好 / 没意思 | 闲聊气泡 | social |

完备互斥：每句话落唯一一格，没有垃圾桶。

---

## 4. 关键决策与备选（Alternatives Considered）

**D1 — 路 Y（采纳）vs 路 X：怎么补完备分类**
- 路 X：直接把 LLM 6 类扩成 8 类（加 question/inform-constraint）。最贴 P1，但要重写 prompt、重测分类、赌 LLM 重分类稳定性 → **风险高，不选**。
- 路 Y（选）：LLM 仍 6 类，用**高精度规则**在 L1 清洗 + 在 L3 拆桶前把 提问/提约束/确认 捞出来；**认不出的模糊仍兜成反馈**。增量、局部、风险低，符合 P5（Anthropic「只在确有必要时加复杂度」）。

**D2 — 提问 QA 回答方式：混合（采纳）**
- 简单字段问题（远吗/贵吗/几点关门）→ **模板**生成（稳、零漂移）。
- 综合/多字段问题 → 喂查到的字段给 LLM 做**接地生成**。
- 查不到字段 → **弃答 + 标注经验**（依据 P3）。

**D3 — 整体重写 vs 局部巩固：局部巩固（采纳）**
- 依据 P5：架构骨架健康（cheap-first 级联 / 注入纵深 / 白名单 / fallback 全对），缺陷局部同源（L1/L2/L3 的识别反馈），L0 证明代码库能写好关。整体重写会丢 L0/横切防御等**已做对的资产** → 局部巩固。

---

## 5. Components（改哪几处 · Strangler 式）

**C1 — L1 强信号词表清洗（精确到子集）**
文件 `feedback_detector.py`。从 **`_STRONG_FEEDBACK_KEYWORDS`（强信号子集）** 移除 8 个歧义词：`太累 / 腻了 / 节奏 / 不太好 / 不喜欢 / 不太行 / 不合适 / 没意思`。
- **保留**在 `_FEEDBACK_KEYWORDS`（全集）里——全集是高召回粗筛、不直接拍板路由（refiner 等仍用），不受影响。
- 保留在 strong 的明确词：`太远/近一点/N公里以内/太赶/太满/太久/太长/盯不住/扛不住/紧凑/太贵/便宜点`（几乎只指方案）。

**C2 — 对话行为收口（消重复 + 收编 L3.5）**
把散在 L1.5 / L3 / L3.5 的「有方案时这句是什么」收拢成一处判定（一个小分类器函数或 router_node 内一段清晰逻辑）。消除 L1.5 与 L3 的「有方案→feedback」重复。

**C3 — L3 拆桶（守红线）**
has_itinerary + ambiguous 时，按顺序：① sniff 命中软约束 + 没明说改 → 气泡（提约束）；② 提问句式（`吗/呢/吧/?/几点/多少/贵不贵/远不远` 等正则）→ 走 C4 提问 QA；③ 明确确认词（好的/就这个/可以/确认）→ confirm；④ **以上都不是的模糊 → 仍当反馈兜底**（红线：真反馈不漏）。

**C4 — 提问 QA（grounded + abstention）**
从当前 itinerary 的 node `target_id` 反查 `load_pois()/load_restaurants()`（复用 `memory.py` 反查套路），取字段：`distance_km / avg_price / price_range / opening_hours / rating / capacity / reservation_slots.queue_minutes / age_range / tags / reviews`。命中字段 → 模板或 LLM 接地回答；缺字段 → 诚实「没查到」+ 可选经验（标注来源）。**不造一堆新 tool。**

**C5 — L2 prompt 修正（解共谋）**
`router_prompt.FEEDBACK_CONTEXT_HINT` 删掉「不太好/换一个→判 ambiguous」这种把歧义词往反馈推的教唆；改为「分清 提约束 / 提问 / 嫌方案」。

**不动**：L0 `injection_detector`、`prompt_guard`（输入隔离）、`fallback_decision`（降级）、`_sanitize_cta_chips`（白名单）。

---

## 6. 不利影响与红线

- 降级 L1 歧义词：真反馈（「这版不太好」）多走一次 LLM，但实测 → L2 判 ambiguous → L3 兜成 feedback，**结果不变**（已验证）。
- **红线**：C3 拆桶时，认不出的模糊**必须继续当反馈兜底**，否则真反馈漏成答非所问。
- 提问 QA 弃答体验是半成品（「没查到」），但远好过「答非所问地重规划」。
- 规则识别提问/确认有边界漏判（反问句），低频，接受。

---

## 7. 分刀提交（降风险、可回滚）

- **刀 1**：C1 + C5 + C3 的「提约束→气泡」分支 — 治本次 bug + 解三重共谋。
- **刀 2**：C4 提问 QA（grounded + abstention）。
- **刀 3**：C2 收口（消 L1.5/L3 重复、收编 L3.5）+ 确认/追加分支。

每刀独立跑全量测试 + 新增针对性测试（含真 LLM 类）。

## 8. 边界 / Non-Goals

不动 LangGraph 拓扑；不动 L0 与横切防御；不重训 LLM 6 类（路 Y）；不引入新 tool（C4 复用反查）。

---

> 按 Kiro 惯例，`requirements.md` / `tasks.md` 可在本设计获认可后补齐（tasks 即第 7 节三刀的细化）。
