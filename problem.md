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

