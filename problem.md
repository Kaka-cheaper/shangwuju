# 会话日志（problem.md）

> **定位**：chronological 流水账。用户每提一个问题、AI 解决后追加一条。  
> **回答的问题**：「上次这个问题怎么处理的？改了哪些文件？」  
> **与 pitfalls.md 的分工**（详见 `AGENTS.md` §4.5）：
>
> - 本文件 = 全量记录（含纯咨询、文档调整、需求讨论、Bug 修复）
> - `docs/03-implementation/pitfalls.md` = 选择性技术警示，每条必有「防再犯」
> - **重叠处理**：值得「防再犯」的 Bug → 问题/方案进本文件，根因/防再犯额外升级一条进 pitfalls
>
> **格式**：
>
> ```text
> 问题N：{用户原问}
> 解决方案：{方案概要}
> 修改的代码文件：{文件列表，无则写「无」}
> 应当达成的效果：{预期效果}
> 用户反馈：{有则填，无则省略此行}
> ```

---

## 问题1：阅读项目下的所有文档，建立完整了解

**解决方案**：

按 AGENTS.md §五「上下文恢复快速通道」+ 角色「我是后端 / Agent 编排开发者」的阅读路径，系统性通读以下文档：

- 根目录：`AGENTS.md`、`README.md`、`项目说明.md`、`比赛详情.md`、`chatgpt分析.md`、`技术架构.md`
- `docs/00-overview/`：`progress.md`、`团队分工.md`、`如何使用这套文档.md`
- `docs/01-requirements/`：`需求分析.md`、`MVP定义.md`、`验收标准.md`、`架构选型.md`、`演示场景集.md`
- `docs/03-implementation/`：`pitfalls.md`
- `.codesee/prompts/`：`_schema.md`、`_rules.md`、`scan.md`、`sync.md`、`scan-planning.md`

并形成全局认知总结回复给用户（覆盖项目身份、D1-D9 决策、四层架构、评分函数、MUST NOT 铁律、§5.7 schema、Tool 清单、8 场景、文档协作纪律、CodeSee 状态）。

**修改的代码文件**：无（只读认知任务，仅创建本 problem.md）

**应当达成的效果**：

- AI 后续任务能直接引用决策记录而无需重读
- 严格遵守 AGENTS.md MUST/MUST NOT 条款
- 后续编码以 `需求分析.md` §5.7 IntentExtraction schema 为唯一权威字段定义

---

## 问题2：problem.md 是否与 pitfalls.md 功能重叠？

**解决方案**：

确认存在部分重叠（仅在「Bug 修复」类场景），但定位不同。采纳方案 C：

- `problem.md` = 会话日志（chronological，全量、每问必记）
- `pitfalls.md` = 技术警示集（thematic，选择性、按 P1/P2/P3 分级）
- 重叠场景：Bug 修复同时进两份，方案进 problem，「为什么不要再踩」进 pitfalls

在 `AGENTS.md` 中固化分工：

- §3.7「每次 session 结束前」追加 problem.md 更新条目
- §4.5 改写「禁止创建 .md」白名单为 progress / pitfalls / problem 三份，并附三者职责对比与重叠处理规则
- §六 文档导航速查表加 problem.md 一行

同步把 problem.md 文件头改写为「会话日志」定位说明，使两份文件在内部互相引用、不再产生歧义。

**修改的代码文件**：

- `AGENTS.md`（§3.7、§4.5、§六 三处）
- `problem.md`（重写文件头）

**应当达成的效果**：

- 用户和后来 AI 能从任意一份文件跳转到另一份，分工清晰
- 不违反「workspace 优先于全局」的规则优先级
- 90% 的纯咨询/文档调整只进 problem.md，避免重复劳动


---

## 问题3：跑 CodeSee 流程，生成项目功能图

**解决方案**：

按 `.codesee/prompts/scan.md` 第一步规模自检，项目处于纯文档/规划阶段（无源码），走 **planning 模式**：

1. 用 `manifest.lang = "zh-CN"`（沿用全局规则的中文要求）
2. 按用户旅程划 5 个 Epic：`input → discovery → planning → execution → sharing`
3. 抽 15 个 planned feature，覆盖 8 个 Tool + 意图解析 + 行程组装 + 异常重规划 + 用户确认 + 转发文案 + 链路可视化
4. 每个 feature 标 `tags: ['planned']`、`confidence ≤ 0.5`、`refs: []`（规划阶段无代码）
5. 画 24 条 cross_feature 关系（含 triggers / depends_on / publishes / subscribes 四类）+ 5 条 epic_flow
6. 跑 `node .codesee/scripts/validate-features.mjs` 通过，初版含 3 条 SHOULD 级警告
7. 修复警告：`call_llm` 改名 `extract`；为外部入口 feature 补 error 分支（empty / fail / cancel / fallback / too_far / constraint_fail）；side-effect 链补 async 边
8. 复跑校验：`✓ 通过：未发现结构问题`（零错零警）

**修改的代码文件**：

- `.codesee/features.json`（新建）
- `.codesee/.gitignore` / `.codesee/prompts/*` / `.codesee/scripts/*` 一并入库

**应当达成的效果**：

- 团队成员和后来 AI 在 CodeSee 画布上能直接看到「规划中 vs 已实现」的全景图
- 进入编码阶段后，每完成一个 feature 触发 `sync.md` 把 `planned` 升级为 `implemented`，同时补 refs
- 评分项 1（场景理解）、2（规划链路）、3（Tool 设计）、5（异常处理）的设计意图全部可视化，对应 cross_feature 与 error 分支


---

## 问题4：四层协议盘点 + Phase 0 契约基座

**用户原问**：四层之间的协议都完全确定了吗？是不是应该先打基座？mock 数据层是不是应该第一个来？

**解决方案**：

盘点结果：6 项已定 + 2 项部分定 + 7 项未定（详见聊天回复）。三人并行的前提是契约先定，否则 W1 末联调一定撞车。

将 W1 拆成 P0/P1/P2/P3 四个子阶段，**P0 必须串行做**：

```
P0 契约基座（本次完成）→ P1 数据+Tool（C 扛）→ P2 Agent（A 扛）→ P3 前端（B 扛）
```

Phase 0 落地内容：

- `backend/pyproject.toml`（uv + pydantic 2.13.4 已 sync 通过）
- `backend/schemas/`：tags / errors / intent / domain / itinerary / tools / sse 共 7 份模型
- `backend/scripts/verify_schemas.py`：自检脚本，6 项全过（含 2 条反向测试：D9 禁止字段拦截 + 词典外 tag 拦截）
- `mock_data/_samples/`：4 份典范样本（POI / Restaurant / Route / Intent）

修正了执行流程的一个盲点：**CodeSee sync 应当是每次代码改动的默认收尾步**，而非显式任务。本轮按 sync.md「新文件未接入：不追加」的边界情况处理，待 P1 Tool 实现把 schema 真正消费起来后再触发 planned → implemented 升级。

**修改的代码文件**：

- 新建：`backend/{pyproject.toml, .python-version, schemas/*, scripts/*}`
- 新建：`mock_data/_samples/*.json`

**应当达成的效果**：

- 三方开发者（A/B/C）拿到同一份 Pydantic 模型即可独立推进，不再脑补字段
- 任何下游代码若漂移字段名 / 发明 tag / 用 D9 禁止字段，会被 Pydantic 立刻拦截
- 后续 `python -m scripts.verify_schemas` 是契约 regression 守门员


---

## 问题5：A 角色（Agent 编排层）P2 完成

**用户原问**：（W2 启动指令——5 项任务清单）

**解决方案**：

按 Phase 0 → Phase 0.5 已就绪的契约，独立实现 Agent 4 大模块：

1. **`agent/llm_client.py`**：用 OpenAI 兼容 SDK 实现 DeepSeekClient + QwenClient（`base_url` 切换），`get_llm_client(provider)` 工厂；含围栏剥离、超时 30s + 重试 2 次（指数退避）；`stub` provider 通过 `LLM_PROVIDER=stub` 环境变量启用，给 W3 联调用
2. **`agent/prompts/system_prompt.py`**：意图解析提示词 + 2 条家庭/老人 few-shot；硬约束词典出口（防发明 tag、防 D9 禁止字段、防发明 social_context）
3. **`agent/intent_parser.py`**：`parse_intent()` 含 LLM 调用、围栏剥离、Pydantic 二次校验、错误回灌重试 1 次、raw_input 兜底
4. **`agent/planner.py`**：规则化 ReAct 主循环（plan_itinerary）；6 阶段 → get_user_profile → search_pois → search_restaurants → check_availability（按时段顺序）→ estimate_route_time × 3 → 拼装六段 Itinerary；MAX_TOOL_CALLS_PER_KIND=3 防过度规划
5. **`agent/executor.py`**：用户确认后下发 reserve_restaurant + buy_ticket（可选）+ generate_share_message
6. **`agent/trace.py`**：内部事件采集器 Tracer，可订阅，不与 SSE schema 强耦合

**端到端测试**：6 项全过（家庭主场景跑通 + E1 显式触发 + D9 禁字段反向校验 + Tool quota 上限）。
**全套测试**：53 项（schema 自检 6 + Phase0.5 8 + W1 真 Tool 33 + W2 Agent 6）全过。

**意外发现并修复跨层 bug**（W1 + W2 协议交接处）：

- 现象：search_restaurants 直接调函数能返候选，但通过 invoke_tool 二次校验崩 → UPSTREAM_FAILURE
- 根因：RestaurantCapacity 用 `Field(alias="2")` 配 `two/four/six/eight` 字段名，`model_dump()` 默认输出字段名但 `model_validate()` 默认期待 alias → 字段名/alias 不匹配
- 修复：`model_config` 加 `populate_by_name=True`（不改字段名 / 不改 mock_data 写法）
- 已追加到 `pitfalls.md` P2 级

**修改的代码文件**：

新建：
- `backend/agent/{llm_client,intent_parser,planner,executor,trace}.py`
- `backend/agent/prompts/{__init__,system_prompt}.py`
- `backend/tests/{conftest,test_agent_flow,test_intent_parser}.py`

修改：
- `backend/schemas/domain.py`（RestaurantCapacity 加 populate_by_name）
- `backend/pyproject.toml` + `uv.lock`（依赖升级 openai/dotenv/structlog 进 dependencies）
- `docs/03-implementation/pitfalls.md`（P2 alias 漂移坑）
- `.codesee/features.json`（CodeSee sync：f-intent-parse / f-plan-assembly / f-exception-replan 三个 feature 从 planned 升级为 implemented，补 refs，confidence 升至 0.7-0.85）

**应当达成的效果**：

- W3 同学可立刻用 `LLM_PROVIDER=stub` 启动后端联调前端，不依赖真 LLM API key
- 评分项 1（场景理解 20%）+ 项 2（规划链路 25%）+ 项 5（异常韧性 15%）已端到端验证可拿
- 后续 P3 W3 写 `backend/main.py` SSE 网关时直接订阅 Tracer 事件转 SseEvent，不需要再设计协议
- pitfalls 新增 P2 级警示，未来任何带 alias 的 Pydantic 模型避免重蹈覆辙

---

## 问题6：为什么所有 feature 还是 planned？

**用户原问**：为什么所有的 feature 中的标签还是 planned？

**初次回答（错误）**：

AI 误以为是「sync 漏升级」，写了批量脚本把 10 个不属于自己 owner 的 feature 升级。还在 pitfalls 里写了错误的"长效解法"——加 auto-detect-implemented.mjs 自动扫 refs 文件存在性。

**用户纠正**：

> multi-agent 场景下应该是各个 agent 只改自己的。

也就是说：12 个 feature 还是 planned 是**正常状态**，不是 bug。W1（C）写完真 Tool 后应该自己跑 sync 升级 5 个 discovery feature；W3（B）写完前端后应该自己 sync 升级 quick-input / tool-trace 等。当前窗口（A）只该 sync 自己 owner 的 3 个。

**修正动作**：

1. revert 上一次"批量升级 10 个 feature"的 commit
2. revert 上一次错误的 problem 流水账
3. 又出错一次：`git revert --no-commit` 后 commit 时没限定 stage 范围，把 14 个 W1 owner 的 untracked 文件也带进 commit → 用 `git reset --soft HEAD~1` + `git reset HEAD backend/ mock_data/` 精准 unstage
4. 重新 stage 仅 A owner 范围的 3 个文件
5. 在 pitfalls 写下正确版本的 P2 坑：「multi-agent 场景下 AI 越界 sync」+ 第二次越界（revert 时 stage 范围失控）

**修改的代码文件**：

- `.codesee/features.json`（revert：12 planned 状态恢复）
- `docs/03-implementation/pitfalls.md`（revert + 重写 P2 坑）
- `problem.md`（本条）

**应当达成的效果**：

- features.json 反映「我自己 owner 的 feature sync 完毕；别人的留给别人」的诚实状态
- 不引入越界自动化工具（不写 auto-detect-implemented.mjs）
- 教训写入 pitfalls：sync 前先问 owner 是谁，untracked 文件不要进 revert 范围

