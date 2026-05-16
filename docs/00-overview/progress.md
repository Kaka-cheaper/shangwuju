# 开发进度

> 本文件是 **session 衔接文件**。每次新开会话的 AI 先读这里，就能在 30 秒内知道上次做到哪、下次从哪开始。
>
> 每次 session 结束前必须更新本文件。格式见文末"更新规则"。

## 一、当前位置

**阶段**：**架构选型全部完成**（session 1 完结，2026-05-08＠晚）

**时间盒**：**1 个月**（3 人团队，至 2026-06-08 左右）

**已完成**：

- 项目代号确定：**晌午局**
- 收到 3 份赛题输入：`比赛详情.md` / `chatgpt分析.md` / `技术架构.md`
- 文档体系骨架建立：`AGENTS.md` + `docs/{00-overview,01-requirements,03-implementation}/` 8 份文件（含本文件）
- 选定主赛道：命题赛道 06「本地探索·周末闲时活动规划」
- **D9 拍板**：场景策略走「D 全开放底层 + A 演示形态」——不枚举关系类型，仅准备 6-8 个压力测过的快捷输入作为现场 Demo 保障
- **需求体系从「2 场景」重构为「约束词典 + 演示场景集」**：`需求分析.md` M1+§五 / `MVP定义.md` MVP-2.2 / `架构选型.md` D4+D9 / `验收标准.md` A17+A18 / 新增 `演示场景集.md` / `pitfalls.md` 3 条预埋坑
- **D-SoT 拍板 + 全文档 schema 对齐**：`需求分析.md` §5.7 锁定为唯一 SoT；7 处跨文档字段收敛完成
- **D1/D2/D6/D7 选型拍板**：架构选型.md 全部从"待决策"改为"已决策 [2026-05-08]"
- **AGENTS.md 编码铁律完善**：§1 阶段+时间盒 / §3.8 代码风格（Python 3.11+ FastAPI Pydantic v2 / Next.js 14 TS strict shadcn）/ §七 文档分组规则（冻结 2 份 + 活文档 2 份）
- **技术架构.md 修复 7 处冲突**：加 D-banner、对齐 D1/D4/D7/D9、解锁为可修订活文档
- **新建 `项目说明.md`**：人类友好产品逻辑入口（~120 行，5 分钟可读），覆盖核心能力 + 8 场景 + 对话流程 + 特色亮点
- **`如何使用这套文档.md` 同步**：目录树补 项目说明.md / 演示场景集.md；加「非技术队友 / 家人」阅读路径；文档纪律段对齐 §七

**未完成 / 待决策**（session 1 后续）：

- ✅ 全部核心选型 + 文档体系完成！可直接进入 session 2 编码

## 二、下一步（1 个月四周阶段路线图）

按 D9 双轨 + 1 个月时间盒拆分：

### Week 1 (当前周剩余) —— 拆分为 P0/P1/P2/P3 子阶段

- ✅ **P0 契约基座**（2026-05-16 完成）：`backend/schemas/` 7 份 Pydantic v2 模型 + `mock_data/_samples/` 4 份典范样本 + `verify_schemas.py` 自检 6 项全过；含 D9 禁止字段拦截 + 词典外 tag 拦截两条反向测试
- ✅ **P1 数据+Tool**（2026-05-16 完成，C 扛，分两轮）：
  - **第一轮 commit bcdc2e7**：mock_data 落 17 POI + 19 餐厅 + 56 路线 + 1 用户画像；7 个真 Tool 实现（search_pois / search_restaurants / check_restaurant_availability / estimate_route_time / reserve_restaurant / generate_share_message / get_user_profile）；tests/test_tools.py 33 项含演示场景集 §四 8 条覆盖率断言；CodeSee sync 7 个 owner=C 的 feature planned → implemented；verify_schemas 6/6 + verify_phase0_5 8/8 + pytest 39/39
  - **第二轮 commit 376dedd**：mock 扩到 D4 规模（21 POI + 30 餐厅，健康轻食 12 条，9+ 处失败埋点含 P_SOLD/P021）；buy_ticket Tool 实现 5 失败分支 + E2 触发；test_tools 追加 6 项 buy_ticket + 4 项 D4 规模断言（共 14 项覆盖 gate）；CodeSee sync f-buy-ticket planned → implemented + 6 个 owner=C feature 更新 mock 行号；test_8_scenarios.py（W2 owner）8 场景全部端到端跑通；pytest 69 passed + 1 xpassed；已 push origin/main
- ✅ **P2 Agent**（2026-05-16 完成，A 扛）：意图解析 + 规划循环 + 异常重规划，端到端测试通过（commit a88c34f / ae8bfd4）
- ✅ **P3 前端 + SSE 网关**（2026-05-16 完成，B 扛）：
  - `backend/main.py`：FastAPI + sse-starlette，4 端点（/health /chat/stream /chat/confirm /scenarios），stub 模式按 api_contract.md §2 完整事件序列推送（含 E1 异常 → 重规划 → 成功）
  - `frontend/`：Next.js 14 App Router + TS strict + Tailwind + shadcn 风格自实现；pnpm + 淘宝镜像（绕开 npm fsevents 元数据 bug）
  - `frontend/lib/types.ts`：手抄 schemas/{sse,itinerary,intent,errors}.py 关键类型
  - `frontend/lib/sse.ts`：手写 fetch+ReadableStream 解析器（替代 EventSource，支持 POST）
  - `frontend/lib/store.ts`：Zustand 状态机；arrival 计数保证 stream/confirm 跨流时序
  - 组件：HomeView / QuickScenarios（8 按钮）/ ChatPanel / IntentSummary / ToolTracePanel（含「已替换」灰显 + 异常重规划高亮）/ ItineraryCard（六段时间轴 + 已为你预留 + 复制按钮）
  - 验证：`scripts/verify_sse.py` 端到端 14+6 事件全过；浏览器 DevTools 验 SSE chunked 流；`pnpm build` 通过、零 console error

### Week 2

- ✅ **6 个核心 Tool 实现** + **buy_ticket** 共 8 个（参数按 §5.7 schema，无 `scene_type` / `relation`，由 P1 第二轮 376dedd 提前完成）
- ⬜ **家庭主场景端到端闭环**（CLI 先跑通） + 1 个异常分支 E1 — 已经 P2 端到端测试覆盖（test_e1_restaurant_full_recovery_in_family_scene）；待补 CLI 演示脚本
- ⬜ **MVP-1 验收齐全**（按 验收标准.md A1-A15）— 待按模板录证据

### Week 3

- ✅ **Tool 扩到 8 个**（含 `buy_ticket` + `estimate_route_time`），E2 已端到端触发（test_buy_ticket_sold_out + test_e2_ticket_sold_out_recovery）
- ✅ **Mock 数据扩到 21 POI + 30 餐厅**（健康轻食 12 条），覆盖 7 种 `suitable_for` 走向（家庭/情侣/闺蜜/独处/老人/朋友/商务/纪念日/同学重聚）
- ✅ **Next.js 前端上线**：聊天框 + 行程卡片 + Tool 调用链路可视化 + 流式 SSE（P3 已完成）
- ✅ **8 个快捷输入按钮上线**（P3 完成 8 按钮 + W3 压测脚本 efa8ee8）；`演示场景集.md` S1-S8 通过 test_8_scenarios.py 全部端到端跑通

### Week 4

- **MVP-3 开放鲁棒性压测**：10-20 句未预演输入场内验证
- **设计文档 1-2 页**（A16）
- **现场演示预演 ≥3 次**，录屏兜底 3 个版本（3 分钟 / 5 分钟 / 完整）
- **提交 + 修复最后翻车点**

## 三、待决策清单

> 任何 session 中冒出的"先记下来、不当下决定"的问题进这里。决策后挪到下面"决策记录"。

- ✅ 核心选型全部锁定（D1 / D2 / D-team / D6 / D7 / D3 / D4 / D5 / D8 / D9）
- ⬜ 编码阶段中冒出的新问题在这里追加

## 四、决策记录

> 每个决策一条目，格式：`Dx [决定时间] 决定 / 备选 / 理由 / 影响`

- **D0** [2026-05-07]：项目代号 = **晌午局**
  - 备选：WeekendPlanner / 美团周末规划 Agent / LocalDayAgent / 办成了 / 下午卷 / 机宜
  - 理由：用户偏好中文 + 设计感；"晌午局"3 字短，"晌午"= 下午（题目核心时间窗），"局"含饭局 / 棋局 / 社交局三重隐喻
  - 影响：仓库名建议 `wuju` / `shangwuju` / `shangwu-ju`；slogan 候选「晌午局，一句话搞定下午行程」

- **D-default-1** [2026-05-07]：默认主场景 = 家庭场景（5 岁孩 + 减肥老婆）
  - 理由：约束更密、出彩点更多；作为主压测载体
  - 影响：第一版 Mock 数据优先按家庭场景画像（亲子 POI ≥8、健康餐厅 ≥12，随 D9 后扩充）
  - 后续被 D9 细化：不是「主场景 vs 辅场景」的二分法，而是「主压测场景 vs 开放输入」的两轨

- **D9** [2026-05-07]：场景策略 = D 全开放底层 + A 演示形态
  - 候选：A 扩关系枚举 / B 同类型细分 / C 环境约束轴 / D 全开放 / D + A 演示组合
  - 选择：D + A 演示组合
  - 理由：D 合赛题原意「接一句自然语言」，拿「场景理解准确度 20%」评分项满分；A 演示保障现场 5 分钟不翻车；开发量可控（3 人 +25h）
  - 影响：联动更新 7 份文档 + 新增 `演示场景集.md`，详见 `架构选型.md` D9 §影响

- **D-SoT** [2026-05-08]：意图抽取 schema source of truth
  - 决定：`需求分析.md` §5.7 为全项目唯一权威定义；其他文档字段均指向此处
  - 理由：dry run 发现 time/start_time 字段漂移、companions 子字段不对齐——锁定 SoT 后下游代码只需 grep + 替换
  - 影响：7 处跨文档 schema 引用已收敛

- **D-team** [2026-05-08]：团队 3 人 + 1 个月时间盒（至 2026-06-08）
  - 细分：团队内至少 1 人有 React/Next.js 实战经验
  - 影响：D2 获得满配条件；4 周四阶段路线图出炉

- **D1** [2026-05-08]：LLM 选型 = DeepSeek-V3 主 + 通义 Qwen-Plus 备
  - 候选：DeepSeek-V3 / 通义 Qwen-Plus / OpenAI GPT-4o / 混合
  - 选择：DeepSeek 主 + 通义备 + OpenAI 兼容 SDK 统一客户端（base_url 切换即可）
  - 理由：Function Calling 稳定性 ⭐⭐⭐⭐⭐ / 中文意图抽取质量 ⭐⭐⭐⭐⭐ / 国内直连无 VPN / 价格便宜（~¥1/¥2 每百万 token） / 首 token < 2s
  - 影响：编码时统一用 `openai` Python SDK + `base_url="https://api.deepseek.com/v1"`；session 2 立即需 user 提供 DeepSeek API Key

- **D2** [2026-05-08]：前端 = Next.js 14 App Router + FastAPI + SSE 流式
  - 候选：Streamlit / Gradio / Next.js + FastAPI / 纯 CLI
  - 选择：Next.js 14 App Router + FastAPI + SSE
  - 理由：团队有 React 实战经验 + 1 个月时间够磨出精致 UI + Tool 调用链路可视化最能出视觉冲击
  - 影响：Tailwind + shadcn/ui 组件库默认；`frontend/` 目录立刻可创建（D9 文档纪律解锁）

- **D6** [2026-05-08]：backend 框架 = FastAPI + Pydantic v2（自动级联自 D2）
  - SSE 流式用 `sse-starlette`；Tool 输入校验用 Pydantic v2 BaseModel
  - LLM 客户端封装：`openai` SDK 兼容 DeepSeek/通义

- **D7** [2026-05-08]：目录结构 = 对应 架构选型.md D7 方案 A（FastAPI + Next.js）
  - 顶层：`backend/ frontend/ mock_data/ docs/ tests/`
  - `backend/` 内：`agent/ tools/ prompts/ schemas/ main.py`
  - `frontend/` 内：`app/ components/ lib/`

## 五、更新规则

每次 session 结束前必须更新：

1. **"当前位置"段**：把本 session 的进展从"待完成"挪到"已完成"
2. **"下一步"段**：根据当前位置重写
3. **"待决策清单"**：新冒出的问题加进去；已决定的挪到"决策记录"
4. **"决策记录"**：新决策按 `Dx [日期] ... ` 格式追加

如果 session 没产生进展（比如只是讨论），可以只更新"待决策清单"。

## 六、命名约定

- **session N**：第 N 次开发会话（session 0 = 本次文档骨架建立）
- **Dx**：第 x 个决策项（D0 / D1 / D-default-1 / D-known-1 等）
- **Px**：第 x 个 pitfall（见 `pitfalls.md`）
- **MVP-x**：MVP 定义里第 x 个里程碑
