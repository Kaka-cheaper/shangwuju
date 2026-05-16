# AGENTS.md

> 这份文档是写给 **AI Agent（Cascade / Claude Code / Cursor 等）** 看的。
> 人类读者请先看 `docs/00-overview/如何使用这套文档.md`。

## 一、你是谁、在做什么

你正在协助开发 **晌午局**——一个面向本地生活的半日出行管家 Agent。

**项目背景**：美团 AI Hackathon 命题赛道 06「本地探索·周末闲时活动规划」参赛作品。

**一句话定位**：

> 用户一句话 → Agent 编排 Tool → 输出可执行 + 可转发的下午行程。

**核心范式**：LLM Function Calling / ReAct 循环 + Mock Tool 层（不接真实美团 API）。

**当前阶段**（截至 session 1，2026-05-08）：**架构选型全部完成，可进入编码**。

**时间盒**：1 个月（3 人团队，至 2026-06-08 左右）。

**已锁定的选型**（详见 `docs/01-requirements/架构选型.md` + `docs/00-overview/progress.md` 决策记录）：

- **LLM（D1）**：DeepSeek-V3 主 + 通义 Qwen-Plus 备；通过 `openai` Python SDK + `base_url` 切换
- **前端（D2）**：Next.js 14 App Router + TypeScript + Tailwind CSS + shadcn/ui
- **后端（D6）**：FastAPI + Pydantic v2 + sse-starlette（SSE 流式）
- **目录（D7）**：`backend/ frontend/ mock_data/ docs/ tests/` 前后端分离
- **场景策略（D9）**：D 全开放底层 + A 演示 6-8 场景快捷按钮
- **意图抽取 schema SoT（D-SoT）**：`需求分析.md` §5.7 为唯一权威定义

三份原始参考材料（详见 §七 文件分组规则）：

- `比赛详情.md`——赛题原文 + 6 个命题赛道描述（我们打 06）【冻结、不动】
- `chatgpt分析.md`——ChatGPT 对赛题的拆解与系统能力分类【冻结、不动】
- `技术架构.md`——初步推荐技术架构候选（已被 D1/D2/D6/D7 加速实例化；D-SoT 后已解锁为活文档）

**下一步**：按 `progress.md` §二 的 Week 1-4 路线图，session 2 初始化项目骨架 + 意图解析模块。

## 二、每次进入项目，先做 3 件事

1. **读 `docs/00-overview/progress.md`**——知道上次做到哪、下次从哪开始
2. **读本文件后面的 MUST / MUST NOT 条款**——知道什么能做什么不能做
3. **读 `docs/03-implementation/pitfalls.md`**——避免重复踩坑

只有在用户明确提出新功能 / 大改动时，才需要进一步通读 `docs/` 下全部文档与赛题原文。

## 三、MUST（必须遵守）

### 3.1 评委导向，不是完美架构导向

Hackathon 的评分核心是 **Demo 闭环 + Agent 行为可见性 + 异常韧性**，不是工程美学。
任何取舍冲突时，按以下顺序决定：

1. Demo 是否能跑通"输入 → 规划 → 确认 → 执行 → 转发"完整闭环
2. 评委能否看到 Agent 的"决策过程"（Tool 调用链路 + 中间状态）
3. 异常分支是否被显式触发与处理（餐厅没位 / 门票售罄等）
4. Mock 数据是否"像真的"（字段密度 + 业务约束完整度）
5. 工程整洁度

**前 4 条是评分项，第 5 条不是**。在前 4 条不冲突的前提下追求第 5 条。

### 3.2 文档优先级

当需求、设计、实现冲突时，按以下顺序决定谁说了算：

1. `比赛详情.md`——题目原文（最高，**唯一不可改**）
2. `docs/01-requirements/验收标准.md`——内部把题目原文转为可验证条款
3. `docs/01-requirements/MVP定义.md`——本次比赛要交付的最小闭环
4. `docs/01-requirements/需求分析.md`——拆解与隐含约束推导
5. `docs/01-requirements/架构选型.md`——决策记录（含 ChatGPT 分析与技术架构推荐的取舍依据）
6. 具体实现代码

**实现代码不是验收依据，验收标准与赛题原文才是**。

### 3.3 4 层架构边界（一旦确定后必须遵守）

> ⚠️ 当前选型未定。下表是默认推荐方案；最终在 `架构选型.md` 拍板后必须更新本节。

每一层只做自己的事，跨层污染立即拒绝：

- **Web UI 层**：聊天框 + 行程卡片 + Tool 调用日志 + 转发预览。**不**写业务逻辑、不直接调 LLM
- **Agent 编排层**（Planner + Executor）：Intent 解析 / 规划循环 / 工具选择 / 异常重规划。**不**自己实现查询逻辑（必调 Tool）、**不**直接写死行程模板
- **Tool 层**：每个 Tool 严格按 Function Calling JSON Schema 定义，分查询类 / 执行类。**不**调其他 Tool（避免链式黑魔法）、**不**包含规划决策
- **Mock 数据层**：JSON / SQLite 静态数据。**不**含运行时状态、**不**做权限校验

### 3.4 Tool 设计纪律

- Tool 数量控制在 **8–10 个**，宁少勿滥（参考 `技术架构.md` §2.3）
- 每个 Tool **必须**有：明确的输入 JSON Schema + 结构化输出 + 失败分支
- 至少 **2 个 Tool 在 Mock 数据里埋失败案例**（餐厅没位 / 门票售罄）—— 评委要看异常韧性

### 3.5 场景策略（D9 决议：D 全开放底层 + A 演示形态）

> session 1 决议（详见 `架构选型.md` D9）：**不**采用「场景枚举」路径（家庭/朋友二选一）；走**意图解析全开放 + 演示场景集**双轨。

- **主线开发「深度」**：家庭场景跑透（5 岁孩 + 减肥老婆）——约束最密、出彩点最多，是主压力测试载体
- **主线开发「广度」**：意图解析层必须对任意自然语言输入鲁棒——**不**写关系类型枚举、**不**写 `if scene_type == "family"` 分支
- **演示场景集**：详见 `docs/01-requirements/演示场景集.md`，列 6-8 个开放场景（家庭/朋友/情侣/带父母/闺蜜/独处 等）——**仅扩 Mock 数据 + 输入用例，不动 Tool 代码**
- **现场两种入口并存**：6-8 个「快捷输入」按钮（已压测、演示稳） + 输入框（评委可即兴扔任意输入，体现开放性）
- **核心防线**：Tool 输入参数 / Agent 中间状态 / Mock 数据查询均对「场景类型」无感——只看具体约束（人数 / 年龄 / 偏好 / 距离 / 时长 / 标签）

### 3.6 每次完成一项实现，必须给验收证据

按 `验收标准.md` 模板记录：

```text
验收对象：
对应验收项：（引用验收标准第 X 节）
输入：
执行方式：
实际输出：
是否通过：
备注：
```

不接受"代码已写"、"理论上可以"、"看起来没问题"作为验收依据。

### 3.7 每次 session 结束前

- 如果有进展：更新 `docs/00-overview/progress.md`
- 如果踩了坑或发现陷阱：追加到 `docs/03-implementation/pitfalls.md`
- 如果有未决定的设计问题：写进 `progress.md` 的"待决策"段
- 如果用户提了问题并已解决：追加到根目录 `problem.md`（会话日志，每问必记；与 pitfalls 的分工见 §4.5）

### 3.8 代码风格（session 1 拍板 D1-D7 后确定）

**后端 Python**：

- Python 3.11+；依赖管理用 `pyproject.toml`（uv 或 poetry，user 自选）
- FastAPI + Pydantic v2；Tool 输入/输出均用 `BaseModel` 不用裸 dict
- LLM 客户端仅一个 wrapper：`backend/agent/llm_client.py`，支持 `provider` 参数切换 DeepSeek / 通义
- Pydantic model 用 `model_config = ConfigDict(extra="forbid")` 防止字段漂移（对应 pitfalls P2-预埋 LLM 混入字段）
- 模块顶部 docstring 说明职责边界与**不负责什么**；不在类型声明里写 `# type: ignore`
- 日志用 `structlog` 或 Python stdlib `logging`，Tool 调用进/出必写 info 级日志（给评委看中间过程）

**前端 TypeScript**：

- Next.js 14 App Router + TypeScript strict 模式；ESLint 开启
- 组件库用 shadcn/ui + Tailwind；**不手写复杂组件**以节省时间
- 状态管理优先用 React 19 Server Components + `useActionState`；客户端复杂状态用 Zustand
- SSE 用 `EventSource` API 直接消费后端 `/chat/stream` 端点

**通用**：

- commit message 用英文，但注释 / docstring / UI 文案用中文
- 谁写代码谁 PR 自检：grep 不出现 `scene_type` / `relation_type` / `if scene ==`（D9 硬条款）
- 字段名按 `需求分析.md` §5.7 schema，**不发明字段**

## 四、MUST NOT（禁止做的事）

### 4.1 禁止跨层污染

- ❌ 在 Tool 层写规划决策（必须由 Agent 编排层决定调哪个 Tool）
- ❌ 让 LLM 直接修改 Mock 数据（Mock 数据是只读快照；执行类 Tool 通过日志体现"已下单"）
- ❌ 在 UI 层调用 LLM API（必须经后端 Agent 层中转）
- ❌ 一个 Tool 内部调另一个 Tool（避免不可观测的链式调用）

### 4.2 禁止 Hackathon 反模式

- ❌ 接真实美团 / 高德 / 支付 API（题目明令禁止 + 浪费时间）
- ❌ 真实支付 / 真实地图 / 真实地图（所有“下单/预约”都是 Mock 返回成功，**用日志体现“已执行”**）
- ❌ 追求 LLM 多么强（评委看 Agent 编排能力，不是模型能力——`技术架构.md` §4）
- ❌ **多个场景同时改 Tool 代码** / **写 `if scene_type == "family"` 这种枚举分支**（Tool 与 Agent 必须对场景类型无感，约束通过参数体现）
- ❌ **把场景作为枚举 / dropdown 实现**（题目要求「接受一句自然语言」——意图解析必须开放）
- ❌ Tool 数量膨胀（>10 个就要砍）
- ❌ Mock 数据“假”（字段太少 / 缺业务约束 / 没失败案例 / 只 cover 2 种关系类型）

### 4.3 禁止造新文件 / 目录的情况

- ❌ 在没决定选型前新建 `backend/` 或 `frontend/` 顶层目录（`架构选型.md` 拍板前不要 prematurely commit）
- ❌ 新建 `utils/` 之类的垃圾桶目录
- ❌ 复制粘贴 `chatgpt分析.md` / `技术架构.md` 内容到 docs/——这两份是参考材料，保留原文，docs/ 只放"决策与结论"

### 4.4 禁止的验收方式

- ❌ "我写好了，你跑一下看看"
- ❌ 不贴输入 / 输出就声称通过
- ❌ Demo 跑通主路径就算完——必须**显式触发**至少一个异常分支并恢复（评分项）

### 4.5 禁止的通用 AI 习惯

- ❌ 创建 README 之外的自娱自乐型 `.md` 文件（白名单：`progress.md` / `pitfalls.md` / `problem.md`，三者职责见下）
- ❌ 在代码里写 "TODO" 而不在 `progress.md` 里登记
- ❌ 没读 `比赛详情.md` 原文就改 MVP 定义
- ❌ 没读 `chatgpt分析.md` 与 `技术架构.md` 就推翻已有架构推荐
- ❌ 在 debug 阶段大改架构——先在 `pitfalls.md` 记录现象

> **三份记录文件分工（避免重叠）**：
>
> - `progress.md`：项目级 session 衔接、决策记录、阶段路线图——回答「当前做到哪」
> - `pitfalls.md`：技术警示集，按 P1/P2/P3 分级，**每条必有「防再犯」字段**——回答「下次不要再踩什么」
> - `problem.md`：会话日志（chronological），用户每提一个问题、AI 解决后追加一条流水账——回答「上次这个问题怎么处理的」
>
> **重叠处理**：当一次 Bug 修复同时具备「值得防再犯」的特征时，问题/方案进 `problem.md`，根因/防再犯额外升级一条进 `pitfalls.md`；纯咨询、文档调整、需求讨论类只进 `problem.md`。

## 五、上下文恢复快速通道

如果你是**新开的 session**，按下面顺序 30 秒内进入状态：

1. 读 `AGENTS.md`（本文件）
2. 读 `docs/00-overview/progress.md` 的"当前位置"和"下一步"段
3. 读 `docs/03-implementation/pitfalls.md` 的最近 3 条
4. 用户会告诉你本次任务，再按任务决定读哪些设计文档

这 4 步总 token 预算应 < 3k。如果远超，说明 `progress.md` 或本文件已经膨胀，需要精简。

## 六、文档导航速查

| 我想知道 | 去哪看 |
|---|---|
| 题目原文是什么 | `比赛详情.md`（赛道 06 段） |
| ChatGPT 怎么拆解的 | `chatgpt分析.md` |
| 产品逻辑就说明（人类友好、非技术可读） | `项目说明.md` |
| 推荐技术栈背景 / 架构思路 | `技术架构.md`（初期候选，已被 D1-D9 实例化） |
| 当前选型决策依据 | `docs/01-requirements/架构选型.md` |
| 必须做什么、优先级 | `docs/01-requirements/需求分析.md` |
| 第一版做到什么算完 | `docs/01-requirements/MVP定义.md` |
| 怎么证明做完了 | `docs/01-requirements/验收标准.md` |
| 演示场景有哪些、输入文案 | `docs/01-requirements/演示场景集.md` |
| 当前进度到哪 | `docs/00-overview/progress.md` |
| 团队分工与时间线 | `docs/00-overview/团队分工.md` |
| 已知踩过的坑 | `docs/03-implementation/pitfalls.md` |
| 上次这个问题怎么处理的 | `problem.md`（根目录，会话日志） |
| 怎么用这套文档（人类视角） | `docs/00-overview/如何使用这套文档.md` |

## 七、与原始参考材料的关系

项目根目录现有 5 类 Markdown 文件，分两组管理：

**冻结原文（2 份，不要动）**：

- `比赛详情.md`：赛题 ground truth，验收标准的最终上游
- `chatgpt分析.md`：需求拆解参考，`需求分析.md` 的灵感来源

**可修订的活文档（2 份，D-SoT 后解锁）**：

- `技术架构.md`：初期架构候选，已被 D1-D9 决策实例化；D-SoT [2026-05-08] 后可随后续决议修订（示例：同步 字段 schema / 场景策略）
- `项目说明.md`：产品逻辑说明（人类友好入口），session 1 创建，接下来随功能交付迭代

**入口铁律 1 份**：

- `AGENTS.md`：本文件，AI Agent 编码铁律入口

**原则**：`比赛详情.md` 与 `chatgpt分析.md` 是冻结项目输入，不动；`技术架构.md` / `项目说明.md` 是可迭代的结论性文档；`docs/` 下为库内结构化文档；根目录仅放顶层入口与冻结参考。

## 八、本文件与 Polisim AGENTS.md 的关系（背景）

本文件骨架借鉴 [Polisim 的 AGENTS.md](https://github.com/Kaka-cheaper/Polisim/blob/main/AGENTS.md) ——一个深度文档驱动的多实体仿真引擎项目。

**关键差异**：

- Polisim 是长期项目（v0.1 → v0.2 → ...），Hackathon 是 1 次性短跑——本文件**比 Polisim 短约 50%**，去掉了"6 层架构""实现映射"等重量级条款
- Polisim 的 MUST 第一条是"分层纪律"，本文件第一条是"评委导向"——目标函数不同
- Polisim 禁止做 UI / 多场景 / 第二阶段助手，本文件禁止做真实 API / 两个场景都做一半——边界不同

如果你之前读过 Polisim AGENTS.md，注意上述差异。如果没读过，**忽略本节即可**。

<!-- BEGIN: CodeSee integration -->
## CodeSee 功能图维护

本项目接入了 CodeSee — 把功能逻辑以语义流程图呈现的工具。

### 触发规则

**触发 1：首次扫描**
若 `.codesee/features.json` 不存在或缺少 `version` 字段：
→ 执行 `.codesee/prompts/scan.md`（会自动判断走 light / heavy / planning 哪一档）

**触发 2：每轮代码改动后**
完成改动后，在回复用户前主动：
→ 执行 `.codesee/prompts/sync.md`

跳过条件：纯样式/重构/重命名（无语义变化），或用户明确要求跳过。

**触发 3：用户显式要求**
"刷新功能图""更新 codesee""扫一下" → 按上述策略执行。

### 项目阶段

- **规划阶段**（只有文档）→ 走 planning 模式，产出 `tags: ['planned']` 的功能图
- **实现阶段**（有代码）→ 走 light/heavy 模式，产出正式功能图
- **混合阶段**（部分实现）→ sync 自动把 `planned` 升级为 `implemented`

### 核心约束

- ❌ 不修改 `.codesee/prompts/` 与 `.codesee/scripts/` 下的文件
- ❌ 不修改 `locked: true` 的 feature
- ❌ 不重命名既有 id（废弃用 tags: ['deprecated']）
- ❌ 不跳过校验（`node .codesee/scripts/validate-features.mjs`）
- ✓ step.name 必须中文动词短语，不要写代码标识符
- ✓ flow.kind 必填，不能省略
- ✓ 写入后必须跑校验，退出码 1 必须修复

### 参考文件

- Schema + 示例：`.codesee/prompts/_schema.md`
- 规则详情：`.codesee/prompts/_rules.md`
- 扫描：`.codesee/prompts/scan.md`
- 同步：`.codesee/prompts/sync.md`
- 校验：`.codesee/scripts/validate-features.mjs`
- 数据：`.codesee/features.json`

> 执行 scan/sync 前先告诉用户你要做什么。
<!-- END: CodeSee integration -->
