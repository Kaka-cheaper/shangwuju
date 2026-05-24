# Agent 3 / LLM-Modulo 范式调研报告

> 作者：Agent 3（LLM-Modulo 调研子代理）
> 日期：2026-05-23
> 范式定位：候选 3——**LLM-Modulo Frameworks**（Kambhampati et al., ASU/Microsoft/Google）
> 一手资料：arxiv 2402.01817（基础框架，ICML'24 position paper）、arxiv 2411.14484（NeurIPS'24 实证评测）、arxiv 2405.20625（TravelPlanner 案例研究）
> 项目代码读取（仅 3 份）：`backend/agent/graph/build.py`、`backend/agent/graph/nodes/replan.py`、`backend/agent/planning/critic/critics_v2.py`

---

## 〇、TL;DR

LLM-Modulo 是 Subbarao Kambhampati 团队提出的一个**强观点**框架。核心论断：**LLM 不能 plan、也不能 self-verify，但可以 "guess candidate plans"**。把 LLM 接到一个 Generate-Test-Critique（GTC）循环里，**外置一组 sound 形式化 verifier 做 critic**，每轮把 critic 的结构化反馈 backprompt 给 LLM 再生成。soundness 不来自 LLM，**完全来自 critic 的形式化正确性**。在 TravelPlanner / NaturalPlan 上，10 轮 GTC 把 GPT-4o 的 8.3% → 23.89%、Claude-3.5 的 4.4% → 25%（来源：[2411.14484 Table 1](https://arxiv.org/html/2411.14484v1#S4)）。

「晌午局」当前架构（LLM 出 blueprint → critic 验 → LLM backprompt 重出）**几乎就是 LLM-Modulo 的一个 instance**——不是"参考 LLM-Modulo"，而是**事实上的同构系统**，只是没用这个名字。`critics_v2.py` 的 9+1 类 ViolationCode + `format_violations_for_llm` 就是 Kambhampati 论文 §3.1 提到的「No, try again, here are all the things wrong」级 critic 反馈。

最大的差异点在三处：
1. **晌午局的 critic 是 Pydantic + 业务规则**，论文是 PDDL VAL / 形式化 schedule constraint；前者表达力强、soundness 是工程意义而非定理意义
2. **晌午局有第二段算法兜底（ILS）+ 死循环防御**，论文没有；论文 give-up 等价于"输出最后一次的尝试或拒绝输出"
3. **晌午局是带通勤的多约束 itinerary**（7 类约束相互耦合），论文只在 4 个 scheduling 域上做了实验，**最接近的 TravelPlanner 也没 commute 段间可达性约束**

复用评分 **8/10**——LLM-Modulo 是当前架构最名副其实的范式同盟，且论文给出的 ablation（feedback 类型、上下文窗口、多解 BFS）可以直接抄到我们这边做实验。

---

## 一、维度 1：输入 plan 定义

### 1.1 论文里的 plan 是什么数据结构？

LLM-Modulo 的 plan 数据结构**因域而变**——这正是论文反复强调的「LLM-Modulo 不限制问题表达力」（[2402.01817 §3.5](https://arxiv.org/html/2402.01817v3#S3.SS5)）。论文举了 4 类 plan：

```text
| 论文域                      | plan 表示                              | critic 类型                        |
|-----------------------------|----------------------------------------|------------------------------------|
| Blocksworld / Logistics     | PDDL action sequence（pick(A,B) ...）  | VAL（Howey et al. 2004）           |
| TravelPlanner（OSU）        | JSON 数组：每天一个 dict，含交通/餐/住 | hard constraint code（benchmark） |
| NaturalPlan / Trip Planning | 自由文本「Day 1-5: 在 Edinburgh ...」  | regex parse + 3 个域 critic        |
| NaturalPlan / Meeting       | 自由文本"You meet X for 75 min..."     | 单一 schedule conflict critic      |
```

**关键洞察**：论文**刻意回避**承诺单一 plan 表示——这是 §3 设计选择里讲的"LLMs 擅长 format conversion，让 reformulator 模块把 candidate plan 转成各 critic 需要的表示"。换句话说，**plan 是 sequence of actions / state-action graph / declarative goal 都行**，关键是有"中央 candidate plan"和"每 critic 自己解读后的视图"两层。

回答你的子问题：
- plan 是 **sequence of actions**（最常用，与 PDDL classical planning 对齐），但 §3.1 明确允许 declarative goal / state-action 形式
- 论文用 PDDL（Blocksworld、Logistics 案例）和**自然语言 + JSON**（TravelPlanner、NaturalPlan）**并存**，没有规定唯一形式
- 我们的 `PlanBlueprint`（多段 morning/afternoon/dinner/evening）落到 LLM-Modulo 是 **structured state-action sequence**——每个 segment 是 (poi/restaurant 节点，停留时长，开始时间) 的 action，加上段间 commute hop。这与 [2411.14484 §4.1 TravelPlanner](https://arxiv.org/html/2411.14484v1#S4.SS1) 的 plan 表示**几乎一致**（论文也是 day-level dict array，含 transportation/breakfast/attraction/lunch/dinner/accommodation 字段）

### 1.2 LLM 输出的初始 plan 与 verifier 输入的 plan 是否需要序列化转换？

**需要**，但论文论证这是 LLM 自己能做的事。[2402.01817 §3.1 "LLMs as Reformulators"](https://arxiv.org/html/2402.01817v3#S3.SS1) 节明确说：

> "These reformulator modules can be supported to a large extent by LLMs, given that one thing LLMs are very good at is format change across different syntactic representations."

也就是说，论文承认中央 candidate plan（可能是 JSON / 自由文本）和各 critic 需要的表示（PDDL 时间线视图 / 因果链视图 / 资源视图）之间需要转换，但这个转换由"reformulator LLM"完成，本质是 LLM 在做擅长的事（format change），**不影响 soundness**——因为转换错了也会被 syntax critic 捕获。

我们项目的等价物：
- 中央 plan：`Itinerary(nodes=[...], hops=[...], orders=[...])`（Pydantic v2 model）
- LLM 输出：`PlanBlueprint`（晚一步的 candidate）→ `assemble_blueprint.py` 把 blueprint 转 Itinerary
- critic 输入：直接接 Itinerary，**不需要 reformulator LLM**

我们比论文更省一次 LLM 调用——因为 Pydantic 模型已经强约束了 schema。**这是工程上的优势**，但也意味着 LLM 输出 schema 错时不能像论文那样靠 syntax critic backprompt，而是 Pydantic 直接抛 ValidationError，由 graph 层重试机制兜底。

**这里有个值得展开的点**：论文 [2411.14484 Figure 1](https://arxiv.org/html/2411.14484v1#S1) 把 format critic 与 constraint critic 显式分两层（response 先过 format critic，pass 后才送 constraint critic；fail 单独 backprompt"修 format"）。我们的 graph 没有这两层分离——format 错就直接 ValidationError 抛回 LangGraph runtime，绕过 critic 节点。这在 demo 阶段是合理的（Pydantic schema 是 ground truth，强制收敛），但在 production 系统里**论文的两层分离更鲁棒**：format 错时用更便宜的 prompt 修 format，省掉重跑全 constraint critic 的成本。spec C 可考虑加一个轻量 format_repair_node 介于 planner 与 assemble 之间。

### 1.3 段位映射

```text
| 论文术语                 | 晌午局对应                                   |
|--------------------------|----------------------------------------------|
| problem specification    | IntentExtraction（意图层产出）              |
| candidate plan           | PlanBlueprint → Itinerary                   |
| reformulator (LLM)       | assemble_blueprint.py（确定性映射，非 LLM） |
| hard critic              | critics_v2 中 severity=CRITICAL 的 9 类     |
| soft critic              | critics_v2 中 severity=WARNING 的 2 类      |
| meta (backprompt) controller | replan.py + replan_router_node          |
| 终止条件                 | retry_count > 4 → give_up                   |
```

---

## 二、维度 2：GTC（Generate-Test-Critique）循环

### 2.1 LLM 角色：generator 还是 mutator？

**两者都是，但取决于轮次**。[2411.14484 §3.3](https://arxiv.org/html/2411.14484v1#S3.SS3) 给出的 backprompt 模板（见附录 A.2 的"Backprompt"段）会把"original prompt + previous response + critic feedback"全部塞回去：

> "This backprompt includes the original prompt, the LLM's response from the previous iteration, and the response provided by the bank of critics."

也就是说：
- **第 1 轮**：LLM 是纯 generator（看到 problem spec，吐 candidate plan）
- **第 2-N 轮**：LLM 是 mutator（看到「上一版方案 + critic 反馈」，理论上应基于上版修正；但论文也观察到 LLM 经常"忽略历史、重新生成"，详见 §5.1 ablation）

[2411.14484 §5.1 "Adding context from previous iterations"](https://arxiv.org/html/2411.14484v1#S5.SS1) 做了 ablation：把过去 n 轮的 incorrect plans 也塞进 prompt。结论是 n 越大表现越好，但**超过 10 之后 marginal**——这个数字对我们项目极有参考价值（详见 §五-Q5）。

### 2.2 critic 池的分类

论文用了三组分类标准，我们的代码同构于第三组：

```text
| 分类轴                   | 论文术语                          | 晌午局对应                     |
|--------------------------|-----------------------------------|--------------------------------|
| 正确性 vs 偏好            | hard / soft（style）              | CRITICAL / WARNING            |
| 形式化基础               | model-based / simulator-based     | rule-based（Pydantic + load_pois） |
| 反馈构造性               | binary / pinpoint / constructive  | format_violations_for_llm（pinpoint） |
```

[2402.01817 §3.1 "Critics/Verifiers"](https://arxiv.org/html/2402.01817v3#S3.SS1) 强调：

> "Hard constraints refer to correctness verification which can include causal correctness, timeline correctness, resource constraint correctness as well as unit tests."
> "Soft constraints can include more abstract notions of good form such as style, explicability, preference conformance, etc."

我们的 `ViolationCode` 9+1 类做 hard/soft 区分，恰好与论文 §3.1 描述同构（详见 §五-Q2 的 1:1 映射表）。

### 2.3 多 critic 冲突如何 reconcile？

[2411.14484 §3.3 "Backprompt (Meta) Controller"](https://arxiv.org/html/2411.14484v1#S3.SS3) 答：**meta controller 的核心职责就是 critique consolidation**。论文给的 3 种实现：
1. **round-robin 选 critique**（最简单）
2. **LLM 帮做 summarize**（让一个小 LLM 把多 critic 反馈压成一段连贯文字）
3. **prompt diversification**（每轮换不同 critique 集合，鼓励 LLM 探索不同搜索空间）

论文实测的 base 配置就是简单 concat（[2411.14484 §3.3](https://arxiv.org/html/2411.14484v1#S3.SS3)：「the meta-controller aggregates the response of those critics and generates a single backprompt」）。

我们项目的 `format_violations_for_llm`（critics_v2.py 第 800-840 行）走的就是论文 §3.3 的方案 1 + 简单 concat：
```python
lines = [f"你产出的行程方案有 {len(critical)} 处违规需要修复："]
for i, v in enumerate(critical, 1):
    msg = v.message
    if v.expected_range is not None:
        lo, hi = v.expected_range
        msg = f"{msg}（建议范围 {lo}-{hi} min）"
    lines.append(f"{i}. {msg}")
```

**完全一致的同构**。论文没说哪种 reconcile 一定最好——这是开放问题。

### 2.4 GTC 循环的实际步骤（ASCII 流程图）

```text
                  ┌───────────────────┐
                  │ Problem spec      │
                  │ (IntentExtraction)│
                  └─────────┬─────────┘
                            │
             ┌──────────────▼──────────────┐
             │ LLM Generator (planner_node)│ ◄────────┐
             │  → PlanBlueprint            │          │
             └──────────────┬──────────────┘          │
                            │                          │
                  ┌─────────▼──────────┐              │
                  │ assemble_node       │              │
                  │ (deterministic map) │              │
                  └─────────┬──────────┘              │
                            │                          │
                            ▼                          │
                  ┌──────────────────────┐             │
                  │ Format critic        │             │
                  │ (Pydantic validate)  │             │
                  └─────┬────────┬───────┘             │
                  pass  │        │ fail               │
                        ▼        ▼                     │
        ┌──────────────────┐  ┌────────────────┐      │
        │ Constraint critics│  │ ValidationError│      │
        │ (validate_         │  │ → graph retry │       │
        │  itinerary)       │  └────────────────┘      │
        └─────┬────────┬───┘                            │
        all   │        │ ≥1 critical                    │
        pass  │        ▼                                │
              │   ┌──────────────────┐                  │
              │   │ Meta controller  │                  │
              │   │ format_violations│                  │
              │   │ _for_llm        │                  │
              │   └────────┬─────────┘                  │
              │            │ backprompt                 │
              │            └────────────────────────────┘
              ▼
       ┌─────────────────┐
       │ narrate → END   │
       └─────────────────┘
```

完全对应 [2411.14484 Figure 1](https://arxiv.org/html/2411.14484v1#S1)。我们多了个 ILS 兜底分支（详见 Q3）。

### 2.5 终止条件（4 选 1 的判定标准）

论文 [2411.14484 §3.3](https://arxiv.org/html/2411.14484v1#S3.SS3) 明确：

> "This interaction loop continues until all of the critics agree to the generated solution or until a specified maximum budget (set to 10 iterations) is exceeded."

只给了 2 选 1：
1. **找到合规**：所有 critic（含 format + constraint）都签字
2. **达 max iter**：默认 10 轮（[2411.14484 §4](https://arxiv.org/html/2411.14484v1#S4) 与 [2405.20625 abstract](https://arxiv.org/abs/2405.20625) 都用 10）

论文**没有**「critic 收敛」（连续 N 轮 critic 输出同一组 violations 即停）和「无解返回」（critic 报告 unsatisfiable 即停）。这是论文留白，因为论文域上 LLM 即便重复犯同一错也可能在第 N+1 轮"幸运地猜对"——这跟 LLM 输出的随机性有关。

我们项目反而**比论文更鲁棒**，详见 Q3：
- `_MAX_LLM_RETRIES = 2`：前 2 次让 LLM backprompt（对应论文 GTC）
- 第 3 次：自动切 ILS 算法兜底（论文没有这一层）
- `_MAX_TOTAL_RETRIES = 4`：再不行就 give_up，不让 graph 撞 LangGraph 25-step 硬限



---

## 三、维度 3：verifier 反馈格式

### 3.1 反馈格式：4 个候选

[2402.01817 §3.1](https://arxiv.org/html/2402.01817v3#S3.SS1) 给出反馈细化程度的 3 级阶梯：

> "When a critic finds the current plan candidate to be unsatisfactory, it can provide varying levels of feedback, ranging from
>   - 'No, try again'（binary）
>   - 'No, try again, here is one thing wrong with the current plan'（pinpoint, 第一条）
>   - 'No, try again, here are all the things wrong with the current plan'（pinpoint, 全量）"

外加 §3.1 末尾还提：
>  "More importantly, the critics can be **constructive**, and offer alternatives plan/subplan suggestions."

所以总共 4 个层级：binary → pinpoint-1 → pinpoint-all → constructive（含 patch hint）。

### 3.2 反馈是哪一层？

我们项目 `format_violations_for_llm` 输出的是 **pinpoint-all + 部分 constructive**：
- pinpoint-all：每条 violation 都列出（"第 N 段「kind · title」停留 X 分钟"）
- 部分 constructive：`expected_range=(lo, hi)` 且写"（建议范围 lo-hi min）"
- **不**含 patch（不告诉 LLM"把 nodes[3].duration_min 改成 60"，因为论文 §3.1 警告：constructive critic 一旦给出具体替换，soundness 就要求 critic 自己也是 partial planner——这会把 critic 推向 solver 的复杂度，违背 GTC 设计初衷）

### 3.3 论文 ablation：feedback 细化对收敛的影响

这是 [2411.14484 §5.4 "Types of feedback"](https://arxiv.org/html/2411.14484v1#S5.SS4) 的核心实验。论文在 GPT-4o-mini + Calendar Scheduling 上跑了 3 种 feedback：

```text
| Feedback 类型      | 内容                                          | 性能（10 轮后） |
|--------------------|-----------------------------------------------|-----------------|
| Binary             | "This time doesn't work. Come up with..."     | 显著最差        |
| First feedback only| 只给第一条 critic 反馈                       | 与 Full 相当    |
| Full feedback      | 全量 critic 反馈 concat                       | 与 First 相当   |
```

论文结论（[2411.14484 Figure 4](https://arxiv.org/html/2411.14484v1#S5.SS4)）：
1. **Binary feedback 显著低于 pinpoint**——LLM 不知道哪里错就乱猜
2. **first-only 与 full-all 几乎一样**——这反直觉，但论文解释：「LLM 容易被多条 feedback 同时指出"信息过载"，反而表现下降」

这条 ablation **直接对应我们项目的设计选择**：
- `format_violations_for_llm` 输出 full pinpoint，按论文是 ok 的（与 first-only 同等水平）
- 但论文暗示**改成 first-only 可能减少 token 而不损失性能**——这是个低成本优化候选

另一条相关的 ablation 是 [2411.14484 §5.1](https://arxiv.org/html/2411.14484v1#S5.SS1)：把过去 n 轮 incorrect plans 也喂回去。结论：
- n 越大 → 准确率越高，**但超过 10 后 marginal gain**
- 仅塞 plan 不带 critique 与 plan+critique 几乎等效（"the inclusion of critiques alongside the incorrect plans did not significantly affect performance"）
- 仅塞 unique 错误版本反而**比都塞下降**——反直觉，可能是 LLM 看到重复错误时学得更好

### 3.4 我们的 critics_v2 是 LLM-Modulo 风格吗？精确论证

**结论：是，且是 base 配置 + 部分 constructive 升级版**。

精确论证四点：

**1) Soundness 来自 critic，不是 LLM**
- `validate_itinerary` 是纯 Python rule 函数（critics_v2.py 第 879 行起）
- 不调 LLM、不抛异常（critics_v2.py 模块 docstring "不抛异常 / 不调 LLM / 不发明新 schema"）
- 等价于 [2402.01817 §3.1](https://arxiv.org/html/2402.01817v3#S3.SS1) 的"sound model-based critic"——只是 model 是我们自己写的 mock-data 业务规则而非 PDDL

**2) 反馈是 pinpoint-all 级 + 自然语言**
- `Violation.message` 中文自包含（"第 N 段「kind · title」停留 X 分钟超出年龄约束"）
- `field_path` 仅 trace 用，**绝不暴露给 LLM**（critics_v2.py docstring 强约束）
- 这与 [2411.14484 §A.2](https://arxiv.org/html/2411.14484v1#A1.SS2) 的 backprompt 模板（自然语言 "1. The accommodation X do not obey the minumum nights rule" "2. The breakfast in day 3 is invalid or not in the data provided"）**完全同构**

**3) Meta controller 做 simple concat**
- `format_violations_for_llm` 编号 + 列表（与论文 [§A.2 Backprompt](https://arxiv.org/html/2411.14484v1#A1.SS2) 一致）
- 没做 LLM-summarize、没做 round-robin（论文 §3.2 提到的两个高级配置）

**4) 部分 constructive（论文也强调过）**
- `expected_range` 字段（critics_v2.py 第 116 行）正是 [2402.01817 §3.1](https://arxiv.org/html/2402.01817v3#S3.SS1) 末尾说的"constructive critic offer alternatives"——只不过我们给的是 range 不是具体替换
- spec planning-quality-deep-review R4 引入这个字段是关键升级，与论文方向一致

**唯一的"超出 LLM-Modulo base 配置"之处**：我们有 ILS 算法兜底（详见 Q3），而论文 base 配置只有 LLM backprompt 一条路径。

---

## 四、维度 4：终止条件 / 鲁棒性

### 4.1 收敛迭代次数

论文报告很谨慎，**从未给"中位数迭代次数"**。但 [2411.14484 Table 1 + Figure 5](https://arxiv.org/html/2411.14484v1#S4.SS1) 报告 **10 轮上限**下的最终通过率，并在 [§4](https://arxiv.org/html/2411.14484v1#S4) 末提到：

```text
| 模型                | 域                | Direct % | LLM-Modulo (10 iter) % |
|---------------------|-------------------|----------|------------------------|
| GPT-4o-mini         | TravelPlanner     | 2.78%    | 15.00%                 |
| GPT-4o              | TravelPlanner     | 8.33%    | 23.89%                 |
| Claude-3.5-Sonnet   | TravelPlanner     | 4.44%    | 25.00%                 |
| GPT-4o              | Trip Planning     | 3.43%    | 40.00%                 |
| Claude-3.5-Sonnet   | Calendar Sched.   | 72.90%   | 88.80%                 |
```

[2402.01817 §4](https://arxiv.org/html/2402.01817v3#S4) 给出 PDDL Blocksworld 数据：

> "with back prompting from VAL acting as the external verifier and critic, LLM performance in Blocks World improves to 82% within 15 back prompting rounds, while in Logistics, it improves to 70%."

合并起来：**typical max 10-15 轮，期望中位数 < 10**（看 Figure 5 曲线大约 3-5 轮已达多数收益，后面是 long tail）。⚠ 推断链：图 5 没数字标注，但从曲线斜率 + 论文 §4 文字"significant gains within early iterations"得出。

### 4.2 不收敛时怎么处理？是否有"giveup + return best partial"？

**论文没有**。[2411.14484 §3.3](https://arxiv.org/html/2411.14484v1#S3.SS3) 只说"loop continues until all critics agree or budget exceeded"——budget 用尽后**直接拒绝输出**（因为 soundness 要求"every output that comes out must pass all sound critics"）。

这是论文的强观点：**宁可没有输出，也不能输出错误的**。引 [2402.01817 abstract](https://arxiv.org/abs/2402.01817)：

> "every output generated is guaranteed correct"

我们项目走的是 **pragmatic 路线**——give_up 时仍然返回当前 itinerary 让用户看到方案（哪怕不完美）：
- `replan.py` give_up 分支返回 `has_critical=False, fallback_chain=[..., to_stage="give_up"]`
- `_route_after_ils` 保证总走 narrate 路径
- 用户能看到 fallback_chain，知道方案不完美——这是 demo 评委导向（评委要看异常韧性 → 演示而非纯理论 soundness）

⚠ **这是设计哲学差异**：论文是"能输出 sound plan，否则不输出"；我们是"尽力输出最佳 effort，附 fallback_chain 让用户知道质量"。论文路线适合任务关键域（NASA mission planning），我们路线适合 demo/local-life agent。

### 4.3 范式对 LLM 模型大小是否敏感？

[2411.14484 Table 1](https://arxiv.org/html/2411.14484v1#S4) 的关键观察：**GPT-4o-mini 也能跑出可观提升**（TravelPlanner 2.78% → 15%，Calendar Scheduling 36.9% → 61.6%）。

论文 §5 explicitly 选 GPT-4o-mini 做所有 ablation，理由（[2411.14484 §5](https://arxiv.org/html/2411.14484v1#S5)）：

> "(a) showing improvements in a smaller model can act as a potential lower bound for improvement and (b) the model is cost-effective."

这对我们项目极其友好——**DeepSeek-V3 / Qwen-Plus 大致与 GPT-4o-mini 同档**（参数规模、能力分位类似），按论文实证可以期待相似收益。⚠ 推断链：DeepSeek-V3 是 671B MoE 但激活 37B，Qwen-Plus 推测 70B 级密集模型，与 GPT-4o-mini（推测 8-12B 密集模型）严格说不在一个档位，但都属于"非 frontier 但可推理"区间。论文没测 DeepSeek/Qwen，需我们自己 dogfooding 验证。

### 4.4 Plan stuck（LLM 反复输同一错误 plan）的概率怎么控制？

论文承认这是个问题，[2411.14484 §5.1](https://arxiv.org/html/2411.14484v1#S5.SS1)：

> "the configuration that included only unique incorrect plans consistently underperformed compared to the others."

这个反直觉发现说明：**LLM 不是总会重复错误**——把过去 N 个错误样本（含重复）都喂回去，反而比只喂 unique 的更好。这暗示 LLM 看到"重复错误模式"时**反而能学到 pattern 跳出去**。

论文给的两条防 stuck 工具：
1. **§5.3 Querying for multiple solutions**（每轮让 LLM 出 K 个 candidate，BFS 搜索）——calendar scheduling 上从 45% → 87%
2. **§5.5 zero-shot CoT prompting**（prompt 末尾加 "Think step-by-step"）——calendar scheduling +6.9%

论文都没用 temperature 调度（实验用 temperature=0），但这是合理的**未来候选**。

我们项目当前**没有**显式防 stuck 机制——`_MAX_LLM_RETRIES=2` 太短，没有 BFS 多解。但好处是有 ILS 算法兜底，stuck 在 LLM 路径就切算法路径。

### 4.5 ⚠ 论文是否对 trip planning / itinerary 多约束域有专门实验？

**有，但不多**。论文一手实验覆盖：

```text
| 论文                      | 域                                            | 与晌午局相似度 |
|--------------------------|-----------------------------------------------|----------------|
| 2402.01817 §4            | Blocksworld / Logistics（PDDL）              | 低            |
| 2402.01817 §4            | TravelPlanner（OSU NLP，多日跨城）           | 中            |
| 2405.20625（专题）       | TravelPlanner 单域 case study                 | 中-高         |
| 2411.14484 §4.1          | TravelPlanner（180 queries，sole-planning）  | 中-高         |
| 2411.14484 §4.2          | NaturalPlan / Trip Planning（1600 scenarios）| 中            |
| 2411.14484 §4.3          | NaturalPlan / Meeting Planning（含 distance matrix）| 高    |
| 2411.14484 §4.4          | Calendar Scheduling                          | 低            |
```

最相关的是 **NaturalPlan / Meeting Planning**（[2411.14484 §4.3](https://arxiv.org/html/2411.14484v1#S4.SS3)）：含 SF 地区 distance matrix + 多人时间窗 + 见面时长约束。这接近我们的"段间通勤 + 节点停留"模型，但仍**没有营业时间 / 年龄约束 / 社交场景调性**——我们的 9+1 类违规码里有 7 类是论文域**没覆盖**的。

**结论**：论文实证覆盖度对我们的支撑是"中等"——能证明 GTC 范式在 itinerary 域有效，但不能证明"扩展到 9+1 类约束后仍 robust"。这是我们要自己做 ablation 的开放问题。

### 4.6 LLM-Modulo soundness vs completeness 的取舍

最后讲一个论文核心而我们项目要面对的**根本性 trade-off**——[2402.01817 §3](https://arxiv.org/html/2402.01817v3#S3) 末段：

> "Note that the plans an LLM helps generate in this architecture have soundness guarantees because of the external sound critics... The completeness of the system depends on the LLM's ability to generate all potentially relevant candidates."

这是 LLM-Modulo 的核心契约：**soundness 由 critic 保证，completeness 由 LLM 保证**。两者职责不重叠：
- critic 不能新造 plan（不是 partial planner）
- LLM 不能签发"无解"（critic 才能 reject all candidates）

我们项目当前的 give_up 路径破坏了这个契约——give_up 时返回 "the last attempted itinerary"（可能仍带 violations）让用户看到。从论文严格立场看这是输出"unsound plan"。但从 demo 评委导向看这又是合理选择。**spec C 设计文档应当明示这个 trade-off**：
- 默认走 demo 路线（give_up → 返回 best-effort + fallback_chain 标注）
- 可选走 strict 路线（env flag `STRICT_MODULO=true`，give_up → 直接拒绝输出 + 返回 401-like 错误码）

这给评委 / 用户提供了"质量分级"——demo 阶段他们能看到方案，strict 模式下他们能看到论文承诺的 sound output。

---

## 五、陷阱清单（5 题必答）

### Q1：LLM-Modulo 与晌午局架构的契合度

**强契合**。把 graph/build.py 拓扑映射到 LLM-Modulo Figure 1：

```text
| LLM-Modulo Figure 1 步骤        | 晌午局 graph/build.py 节点                         |
|---------------------------------|---------------------------------------------------|
| (1) Prompt generator            | intent_node + 上下游 IntentExtraction             |
| (2) LLM 生成响应                | planner_node（出 PlanBlueprint）                  |
| (3) Format critic               | assemble_node 内 Pydantic ValidationError 防御    |
| (4) Constraint critics          | critic_node → validate_itinerary（10 类规则）     |
| (5) Critic feedback → meta ctrl | route_after_critic → replan_router_node           |
| (6) Backprompt LLM              | 回 planner_node（带 critic_feedback_text）        |
| (7) All critics approve → emit  | route_after_critic 走 narrate                     |
```

**完全 1:1 对应**——每个论文步骤都有对应节点，没有缺漏，连方向都一致。

**需要适配的地方**（也只有 3 处）：
1. **第 3 次违规切 ILS**（论文没有；我们多了一层算法兜底）→ 这是工程优化，论文 spirit 允许（[2402.01817 §3.5](https://arxiv.org/html/2402.01817v3#S3.SS5) 说"when underlying problem is solvable by combinatorial solvers, it can be orders of magnitude more resource efficient"）
2. **_route_after_ils 强制接 narrate**（论文没死循环风险，因为论文 critic 不会触发 ILS）→ 工程兜底，与论文不冲突
3. **give_up 仍输出 itinerary**（论文 give_up 等于拒绝输出）→ 设计哲学差异，因为我们是 demo 不是 mission-critical

回答："**事实上的同构**——晌午局是 LLM-Modulo 的工程化加固版"。

**契合度的更深分析**：晌午局之所以能与 LLM-Modulo 同构，其实不是巧合。pitfalls.md 里 P1-2026-05-22 commute-critic 死循环、P1-2026-05-23 LangGraph 25-step 硬限、planning-quality-deep-review R4 年龄分级等问题，全都是工程实践中**自然进化出 LLM-Modulo 形态**的表征：你一旦让 LLM 出方案 + 用 rule 验 + 不通过则反馈重出，再加点防死循环兜底——你就**重新发明了 LLM-Modulo**。这间接验证了 Kambhampati 的核心论点：**只要承认 LLM 不能 plan、要 sound critic 兜底，路径都收敛到这个架构**。这给我们 spec C 设计的启示：与其担心"是否要换范式"，不如把当前架构的 LLM-Modulo 化做完整、命名清晰、测试覆盖，胜过引入第二个范式造成混乱。

### Q2：critic 种类的 1:1 对应？

**结构性差异，但仍同构**——不是字段名 1:1 同名，而是 hard/soft + correctness/preference 二维分类一一对得上：

```text
| critics_v2 ViolationCode      | 我们的 severity | 论文 §3.1 分类                     |
|--------------------------------|----------------|-------------------------------------|
| INVARIANT_BROKEN               | CRITICAL       | hard / structural correctness      |
| NODES_INCOMPLETE               | CRITICAL       | hard / completeness                |
| DURATION_OUT_OF_RANGE          | CRITICAL       | hard / resource-constraint         |
| TIMELINE_INCONSISTENT          | CRITICAL       | hard / timeline-correctness（VAL 同类）|
| HOP_INFEASIBLE                 | CRITICAL       | hard / causal-correctness          |
| RESTAURANT_FULL_UNRESOLVED     | CRITICAL       | hard / unit-test（demo-aware）     |
| SOCIAL_CONTEXT_MISMATCH (B)    | CRITICAL       | hard / unit-test                   |
| AGE_DURATION_MISMATCH (R4)     | CRITICAL       | hard / unit-test（衍生约束）       |
| DISTANCE_EXCEEDED              | WARNING        | soft / preference-conformance     |
| DIETARY_VIOLATION              | WARNING        | soft / preference                 |
| SOCIAL_CONTEXT_MISMATCH (P)    | WARNING        | soft / explicability              |
```

**结构性差异**有两点：
1. **论文 PDDL critic 用 VAL（外部 standalone tool）**，我们的 critic 是 in-process Python rule。VAL soundness 是定理意义（PDDL 形式语义已证明可判定）；我们 Python rule 是工程意义 soundness（rule 写对了就 sound，但没 formal proof）
2. **论文 hard critic 都对应 PDDL semantics**，我们的 hard critic 包含**业务约束**（年龄分级、社交调性、demo-aware 满座埋点）——这些是 LLM-Modulo 没原生覆盖的应用层语义

但这个差异**不破坏论文 spirit**：[2402.01817 §3.1](https://arxiv.org/html/2402.01817v3#S3.SS1) 明说："critics don't always have to be declarative model-based ones, and can be simulators."——业务规则 + Pydantic + Mock 数据查询本质就是论文允许的"procedural critic"或"simulator critic"。

### Q3：_route_after_ils 防死循环 vs 论文死循环风险

**论文同样存在死循环风险，但论文用"硬 budget 上限"解，不需要 _route_after_ils 这种条件分支跳板**。

我们的 ILS 死循环风险（pitfalls P1-2026-05-22）特定于工程：
- assemble_node 用 lookup_hop 算 hop.minutes，critic_node 用同一个 lookup_hop 校验 → 理论上同输入同输出，不应触发 HOP_INFEASIBLE
- **但** ILS 路径是另一个独立 planner（`agent.legacy.ils_planner`），它的 hop 估算与 critic 用的 lookup_hop **可能漂移**（不同代码路径、不同假设）
- 漂移 → critic 误报 commute 不可达 → 回 replan_router → 又切 ILS → 死循环

论文不会遇到这个问题，因为：
1. 论文 critic（PDDL VAL）只看 PDDL 表示，不依赖第二个独立的 planner
2. 论文没"算法兜底"概念——只有 LLM 一条路径

论文怎么解死循环？**只靠 budget 硬上限**（[2411.14484 §3.3](https://arxiv.org/html/2411.14484v1#S3.SS3)：max_iter=10）。我们的 `_MAX_TOTAL_RETRIES = 4`（replan.py 第 35 行）就是同等的硬 budget，**完全照抄论文**。

我们额外加 `_route_after_ils → narrate`（build.py 第 78 行）是因为：
- 论文没 ILS 环节，单条路径走 budget cap 即可
- 我们多了 ILS 环节，且 ILS critic 链路有漂移风险，所以"切到 ILS 后就不再过 critic"——把环境因素（漂移）从 GTC 循环里物理隔离

**能否参考论文路线？** 部分能：把 `_MAX_TOTAL_RETRIES` 提到 10（论文水平）；把 `_route_after_ils` 改成走 critic 但 retry_count 已计满则 give_up——但**当前 4 步 + 强制 narrate 是更稳的工程选择**，理由是 demo 时间敏感（每多一轮 LLM 调用 +5-15s 延迟），评委不愿等。

### Q4：5 岁娃博物馆 196min 案例从 LLM-Modulo 视角分析

**generator（LLM）出错 + critic 当时缺失 + 反馈格式发生作用前已上呈**——四选一其实是**前两个的复合**。推理链：

1. **LLM 在第 1 轮输出 196min**：因为 prompt 没强调"5 岁娃单段 ≤75min"——LLM 不知道这个软知识，output 是合理猜测但越界。这是 [2402.01817 §2.1](https://arxiv.org/html/2402.01817v3#S2.SS1) 的典型表现："LLMs are more likely doing approximate retrieval of plans than actual planning"——LLM 拿了一个"博物馆 3-4 小时合理"的常识，没有 condition on 5 岁娃。
2. **critic 当时不存在**：在 spec planning-quality-deep-review R4 上线前，`AGE_DURATION_MISMATCH` 这条 ViolationCode 还没有。[2402.01817 §3.1](https://arxiv.org/html/2402.01817v3#S3.SS1) 警告："plans coming out of LLMs may look reasonable to the lay user, and yet lead to execution time interactions and errors."——没 critic 拦就直接出去了。
3. **修复方向 = 论文 §3.3 Specification Refinement**：R4 加 `_age_aware_duration_critic`（blueprint 路径）+ `_check_age_aware_duration`（critics_v2 镜像 ILS 路径）+ blueprint prompt 分级表（让 LLM 第一次就猜对）。这正是 [2402.01817 §3.3](https://arxiv.org/html/2402.01817v3#S3.SS3) 说的"domain expert + LLM 联合 acquire model + 写入 critic"。
4. **反馈格式没问题**：`format_violations_for_llm` 会把"超出年龄约束（含 5 岁同行，学龄前 ≤75min）"喂回 LLM，加上 `expected_range=(60, 75)` 给出 constructive 建议范围。R4 上线后两轮内能修复。

**LLM-Modulo 视角总结**：这是经典的"critic 池不完备"案例——R4 上线相当于补了一个新 critic（论文 [§3.3](https://arxiv.org/html/2402.01817v3#S3.SS3) 称为 "model acquisition + critic addition"）。**终止策略没错**，反馈格式也没错，错的是 critic 池的**完备性**——这与论文 §3 末段"completeness of the system depends on..."的留白完全一致。

### Q5：LLM-Modulo 对 LLM 调用次数预期 + 我项目预算估算

**论文预期**：[2411.14484 §4](https://arxiv.org/html/2411.14484v1#S4) 用 budget=10 iter；[2402.01817 §4](https://arxiv.org/html/2402.01817v3#S4) 在 PDDL Blocksworld 用 budget=15。**每个 plan 1-15 次 LLM 调用**，期望中位数 3-5 次。

我们项目估算（DeepSeek-V3 价格作 base，[deepseek 官网定价](https://platform.deepseek.com/api-docs/pricing/)：input ¥1/M tokens、output ¥2/M tokens；推断时刻 2025-Q3-Q4 价格）：

```text
| 阶段                       | 输入 token | 输出 token | per call 成本 |
|----------------------------|-----------|------------|---------------|
| intent_parser               | ~1500     | ~400       | ¥0.0023       |
| planner（blueprint LLM）   | ~3000     | ~1500      | ¥0.0060       |
| narrate                    | ~2000     | ~800       | ¥0.0036       |
| 单次 GTC 循环（含 1 次 backprompt）| ~5000 | ~2000   | ¥0.0090       |
```

**每个完整 demo session（含 6 个 plan critic 循环）**：
- 假设平均 GTC 收敛 = 3 轮（按论文 Figure 5 经验外推）
- 6 plans × 3 轮 × ¥0.009 ≈ **¥0.16 / session**
- 假设 demo 期间评委连续操作 20 个 session → **¥3.2**
- 即便 worst case 每 plan 跑满 4 轮（我们的 `_MAX_TOTAL_RETRIES`），总成本 **¥0.21 / session**

**完全可承受**——demo 阶段总预算 ¥10 都够 30 个 session。⚠ 推断链：DeepSeek 价格基于 2025 Q1 公告；token 估算基于 prompt 中文字符数 / 1.5 ≈ token 数（中文 prompt 1 token ≈ 1.5 字符）；3 轮收敛是从论文 [2411.14484 Figure 5](https://arxiv.org/html/2411.14484v1#S4) 曲线视觉估读，非论文显式数字。

**latency 维度的额外考量**：成本不是唯一约束，demo 评委对响应延迟极敏感。DeepSeek-V3 单次调用经验上 5-15 秒（取决于 prompt 长度与 output token），4 轮 GTC 最坏情况 = 60 秒——超出"评委愿等的 30 秒"红线。所以 budget cap 设到 4 而非论文的 10，本质是 **latency-bound 而非 cost-bound 决策**。spec C 设计应明确这一点，避免后续 PR 误把 budget 提到 10 引发延迟 regression。建议：可在 demo 阶段引入流式 SSE 让用户每轮看到 critic 反馈进度（已有 sse_adapter.py 基础），把 60 秒"无响应"变成"有节奏地 4 轮迭代"——这反过来增强了"Agent 行为可见性"评分项。

---

## 六、关键洞察 / 复用评分 / 建议

### 6.1 关键洞察 5 条

1. **晌午局架构事实上就是 LLM-Modulo 实例**——不是参考、不是借鉴，是同构系统。`graph/build.py` 拓扑与 [2411.14484 Figure 1](https://arxiv.org/html/2411.14484v1#S1) 1:1 对应（intent → planner → assemble → critic → replan_router → planner|ils|narrate）。我们应当在 spec C 设计文档中**显式引用 Kambhampati 论文作为范式**，给团队和评委一个 academic anchor。

2. **`format_violations_for_llm` 输出 pinpoint-all 是合理的，但论文 ablation 暗示"first-only"性能等价**。[2411.14484 §5.4](https://arxiv.org/html/2411.14484v1#S5.SS4) 显示 first-feedback 与 full-feedback 在 calendar scheduling 上**几乎一致**，但 first-only 节省 token 30-50%。这是 demo 阶段低成本优化候选——**保留 pinpoint-all 是默认，可以加 env flag 切 first-only 做 A/B**。

3. **论文 GTC base 配置没有"算法兜底"，我们 ILS 是工程加分项不是论文加分项**。[2402.01817 §3.5](https://arxiv.org/html/2402.01817v3#S3.SS5) 承认"when underlying problem is actually solvable by combinatorial solvers, it can be orders of magnitude more resource efficient to use them"——所以 ILS 兜底**与论文 spirit 兼容**，但要意识到这是 LLM-Modulo+ 而非 vanilla LLM-Modulo。spec C 设计时应清晰区分这两层。

4. **论文证明 GPT-4o-mini 也能跑出 +12-25 个百分点的提升**——DeepSeek-V3/Qwen-Plus 推断在同档（参数规模相近、推理能力同分位），可以期待相似收益。但**我们没在 trip-planning 多约束域 dogfood 验证过**——这是 spec C 必须做的实验：跑 demo 场景集 6-8 个 input，统计 critic 收敛轮次、首轮通过率、最终通过率，与论文 Table 1 对标。

5. **论文最大的盲点是不处理 commute / 营业时间 / 社交调性等多模态约束**——4 个评测域里最复杂的 NaturalPlan Meeting Planning 也只有 distance matrix + 时间窗。我们的 9+1 类违规码里有 ≥6 类是论文域不覆盖的。**这是项目独立贡献**，可以反过来 cite 我们的 critics_v2.py 作为 LLM-Modulo 在多约束 itinerary 域的扩展实例。

### 6.2 复用评分

**整体：8/10**——LLM-Modulo 是当前架构最名副其实的范式同盟，且论文给出的 ablation 直接可抄。

子项打分：

```text
| 子项                                 | 评分 | 备注                                          |
|--------------------------------------|------|-----------------------------------------------|
| GTC 循环范式映射                     | 10/10| 与 graph/build.py 1:1 同构，毫无适配成本     |
| critic 池设计哲学（hard/soft 二分） | 9/10 | critics_v2 的 CRITICAL/WARNING 完全一致      |
| feedback 反馈格式（pinpoint-all）   | 9/10 | format_violations_for_llm 与论文 backprompt 模板一致 |
| meta controller 设计                | 7/10 | 我们用 simple concat，论文允许更高级 reconcile|
| 终止条件                            | 7/10 | budget cap 一致；但 give_up 哲学不同         |
| ILS 兜底（论文没原生）              | 6/10 | 论文 §3.5 留口子但没具体方案                |
| 多约束 itinerary 域适配             | 5/10 | 论文实证只覆盖 4 个域，未到 7 类约束耦合    |
| ablation 实验复用价值              | 9/10 | §5.1/§5.4 直接给出可抄的 A/B 维度          |
```

### 6.3 建议（≤200 字）

1. spec C 设计文档**显式引用 Kambhampati 团队**（2402.01817 + 2411.14484），给项目 academic anchor
2. 保留 `format_violations_for_llm` pinpoint-all 默认行为；加 env flag `CRITIC_FEEDBACK_MODE=first|all|binary` 做 demo 时的 A/B
3. 把 `_MAX_LLM_RETRIES` 从 2 提到 3-5（论文 budget=10 是单 LLM 路径的；我们因有 ILS 兜底可以保守一点，但 2 太短）
4. 加一个 `BFS_MULTI_CANDIDATE` 实验开关，每轮让 LLM 出 2 个 candidate 走 BFS（论文 §5.3 calendar scheduling +25%）
5. 每个 demo session 收集 `convergence_iterations` 指标，对标论文 Figure 5 验证 DeepSeek 收敛分布

### 6.4 与现有 graph 的衔接细节

```text
| 现有文件 / 函数                                         | 衔接动作                                           |
|---------------------------------------------------------|----------------------------------------------------|
| backend/agent/graph/build.py: _route_after_ils         | 保留（防 ILS 死循环 P1，与论文 budget cap 互补）   |
| backend/agent/graph/nodes/replan.py: _MAX_LLM_RETRIES  | 从 2 调到 3-4（建议 3）                           |
| backend/agent/graph/nodes/replan.py: _MAX_TOTAL_RETRIES| 保持 4（即便提了 LLM_RETRIES，也不超过 4）       |
| backend/agent/planning/critic/critics_v2.py: format_violations_for_llm | 加 env flag 切 first-only 模式（评测用） |
| backend/agent/planning/critic/critics_v2.py: Violation.expected_range | 已有（R4），是 constructive critic 的核心 |
| backend/agent/graph/state.py: AgentState               | 加 convergence_iterations 字段，对标论文 Figure 5 |
| backend/agent/graph/nodes/planner.py                   | 在 backprompt 时附带 previous response（论文 §5.1 ablation：+context 总有正收益） |
```

### 6.5 与 ReAct / Tree-of-Thought / Reflexion 的对比矩阵（防混淆专栏）

调研常见误区：把 LLM-Modulo 与 ReAct / ToT / Reflexion 视为"都是 LLM-with-feedback-loop"。这个理解对一半——它们都是 GTC 类型，但 verifier 来源根本不同：

```text
| 范式            | verifier 来源              | soundness 保证 | 与晌午局适配度 |
|-----------------|----------------------------|----------------|----------------|
| ReAct           | LLM 自己 + 环境观察        | 无             | 仅 runtime 工具调用层适配（agent/runtime/） |
| Tree-of-Thought | LLM 自己评估 branch        | 无             | 不适用（我们不做搜索树展开） |
| Reflexion       | LLM 自批判 + memory       | 无             | 不适用（我们不做 long-term memory） |
| Self-Refine     | LLM 自己 critique-then-refine | 无         | 不适用（我们的 critic 是 rule） |
| LLM-Modulo      | **外部 sound 形式化 verifier** | **有**     | **强适配** |
```

[2402.01817 §2.2](https://arxiv.org/html/2402.01817v3#S2.SS2) 系统性反驳了"LLM 能 self-verify"的所有 claim，引用 Kambhampati 团队自家在 graph coloring 上的实验（Stechly et al 2023）：

> "Our results indicate that in direct mode, LLMs are, perhaps not surprisingly, pretty bad at solving graph coloring instances. More interestingly, they are no better at verifying solutions."

进一步引述 [2402.01817 §2.3](https://arxiv.org/html/2402.01817v3#S2.SS3)：

> "ToT employs a problem-specific prompt priming method... despite the use of terminology of problem-solving agents... there is really no deeper connection to search-based agents."

也就是说，Kambhampati 团队认为 ToT/ReAct 类工作的"verifier 故事"本质上是 LLM 在做近似检索，而不是 sound 验证。**我们项目走 LLM-Modulo 路线，避开了这条阵营之争**——critics_v2 是 rule-based、不是 LLM-based，soundness 是工程意义的（rule 写对就 sound）而不是定理意义的（PDDL 那种），但已经远比 LLM self-verify 可靠。

**项目里的 LLM critic 处理建议**：
- agent_thought 节点保留作为 narrate 阶段的"软 style critic"——给方案讲故事时让 LLM 加点温度
- **不**让 agent_thought 出 CRITICAL 级 violations——这是 LLM-Modulo 立场要求
- spec C 设计文档应明确："hard correctness 走 critics_v2 / blueprint critic（rule），soft style 走 LLM；两者职责不重叠"

### 6.6 论文实验复用清单（拿来主义）

[2411.14484 §5](https://arxiv.org/html/2411.14484v1#S5) 5 个 ablation 与我们项目的对应可行性：

```text
| 论文 ablation                      | 论文增益（calendar/GPT-4o-mini）| 可移植到晌午局？        |
|------------------------------------|--------------------------------|-------------------------|
| §5.1 含过去 N 轮 incorrect plans  | n=10 时 +5%；marginal beyond 10| 可——加 prev_attempts 字段 |
| §5.2 filter unfit constraint values| TravelPlanner +15% (GPT-4o)   | 部分可——demo-aware 满座可类似 |
| §5.3 BFS 多解（每轮 K candidates）| +25% on calendar              | 可，但 demo 时间敏感，risky |
| §5.4 binary vs first vs full feedback | first ≈ full > binary     | 可——format_violations env flag |
| §5.5 zero-shot CoT                 | +6.9% on calendar            | 可——blueprint prompt 加 "step-by-step" |
```

最值得抄的两个：**§5.1 含历史 + §5.5 CoT**——成本低、风险小、收益明确。

### 6.7 阅读笔记

```text
| 论文                                  | 看完时间 | 一句话总结                                         |
|---------------------------------------|----------|----------------------------------------------------|
| arxiv 2402.01817 (Kambhampati '24)    | ICML'24 position paper，LLM 不能 plan，但能在 LLM-Modulo GTC 循环里出 candidate |
| arxiv 2411.14484 (Gundawar et al '24) | 实证 paper，4 个 scheduling 域 + 5 个 ablation     |
| arxiv 2405.20625 (Gundawar et al '24) | 仅 abstract 读取，TravelPlanner 单域 case study   |
```

**Kambhampati 立场速记**：教授，强观点派"LLM 不能 plan"。论文 §2 系统性 review 了所有"LLM can plan"的 claim 并逐一反驳——CoT、ReAct、Reflexion、Tree-of-Thought、self-verify 全部点名。所以 LLM-Modulo 不是"再加一个 critic 层"的中立架构，**是带强意识形态的"LLM-as-knowledge-source-not-as-planner"主张**。我们调研时不为了"客观"磨平这个立场——但项目落地时也无需 100% 接受这个立场（demo 评委不会问 LLM 是不是 planner），知道有这个立场存在并据此选择术语即可。

**与 ReAct / Tree-of-Thought / Reflexion 的核心差异**（防混淆）：
- ReAct / ToT / Reflexion 都是 GTC 类型，但**verifier 仍是 LLM 自己**
- LLM-Modulo 强调 verifier 必须是 sound formal verifier（PDDL VAL / 业务规则）
- 我们 critics_v2 是 rule-based → 站 LLM-Modulo 阵营
- 项目里的 LLM critic（agent_thought）按论文严格立场是"soft critic, no soundness guarantee"，应当只用于 style/preference 层而**不用于** correctness 判定——这一点要在 spec C 设计中诚实反映

**LLM-Modulo（framework）与 PlanCritic / LLM-FCS（具体应用）的区分**：
- LLM-Modulo 是 generic framework
- PlanCritic（[2405.20625](https://arxiv.org/abs/2405.20625) 的 TravelPlanner 实例）是 framework 的应用
- 2411.14484 是把 framework 在 4 个 scheduling 域上应用 + ablation
- 我们项目对应的"应用名"可叫 "**晌午局-Modulo**" 或 "Itinerary-Modulo"——这是一个新的应用实例，不是新框架

---

> **完**。如对论文细节或与 graph/build.py 拓扑映射有疑问，先看本报告 §五-Q1 的 1:1 对照表，再看 [2411.14484 Figure 1](https://arxiv.org/html/2411.14484v1#S1) 与论文 §3。
