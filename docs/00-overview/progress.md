# 开发进度

> 本文件是 **session 衔接文件**。每次新开会话的 AI 先读这里，就能在 30 秒内知道上次做到哪、下次从哪开始。
>
> 每次 session 结束前必须更新本文件。格式见文末"更新规则"。

## 一、当前位置

**阶段**：**Phase 0.7 个性化（persona + memory）完成 + 浏览器端到端实测全过**（2026-05-17）

**MVP 状态**：

```
| 阶段     | 完成度    | 说明                                                       |
|----------|-----------|------------------------------------------------------------|
| MVP-1    | 100% ✅    | 6 Tool + 主场景闭环 + E1 显式触发 + Web UI + SSE          |
| MVP-2    | 95% ✅     | 8 Tool / 8 场景全跑通 / 用户确认 / 双 planner mode / 反馈重规划 |
| MVP-2.5  | 100% ✅    | LLM 客户端解耦（任意 OpenAI 兼容 base_url）                |
| MVP-3 个性化 | 100% ✅ | persona prior 注入 + memory 累积 + 偏好画像面板（Phase 0.7） |
| MVP-3 演示 | 阻塞     | 真 LLM 链路已实测；剩录屏 3 版本 + 现场 dry run             |
```

**测试矩阵**：141 项 pytest + 23 vitest + 13 verify_refine = 177 全过

```
| 套件                          | 通过项 |
|-------------------------------|--------|
| schema 自检                    |  6/6   |
| Phase 0.5 并行基座             |  8/8   |
| W1 真 Tool + Mock              | 39/39  |
| W2 Agent 端到端                |  6/6   |
| 8 场景集成测试（W4-r1）        | 17/17  |
| refiner 单测（P0.6 A4）         |  5/5   |
| llm_planner 单测（A4）          | 13/13  |
| 联调矩阵（A6）                 | 40/40  |
| persona+memory（P0.7）         | 13/13  |
| 后端合计                       |141/141 |
| 前端 vitest（W3）              | 23/23  |
| B 的 verify_refine（双模式）    | 13/13  |
| 总计                           |177/177 |
```

**真 LLM 链路实测**：MimMo (mimo-v2.5-pro) 端到端浏览器实测全过——意图解析 / 双 mode / 反馈重规划 / persona 切换 / memory 学习 全部跑通

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

- ✅ **W3 加固**（2026-05-16 完成，B 扛）：
  - `frontend/scripts/pressure-test-scenarios.mjs`：8 场景 SSE 端到端压测脚本，stub 模式 8/8 全过 ~4.5s/场景
  - `frontend/lib/sse.ts` 鲁棒性升级：firstEventTimeoutMs(8s) + idleTimeoutMs(30s) 看门狗；区分 5 类错误；修 \r\n\r\n vs \n\n 同位置切分 bug
  - `frontend/lib/sse.test.ts`：vitest 23 项（findBlockSeparator 4 / parseBlock 9 / streamSse 鲁棒性 10：粘围栏 / 长 token / CRLF / 超时 / abort）全过
  - UI 打磨：移动端适配（顶栏紧凑 + ChatPanel 自适应高度）；微动效（fade-in-up / 时间轴渐变线 / 按钮 hover 浮起）；色彩纪律（仅 brand-orange + ink，无紫粉）
  - `frontend/scripts/verify-all.mjs`：一键跑 lint/typecheck/test/build；本轮 4 项全过（首页 16.3 kB / 加载 103 kB）
  - `frontend/README.md`：录屏 3 版本脚本（3min 主路径 / 5min + 1 开放场景 / 完整版 8 场景）；recordings/ 加 gitignore
  - CodeSee sync：仅升 f-tool-trace（0.9→0.95，加 arm_watchdog step）+ f-quick-input（0.92→0.94，补压测脚本 ref），不动他人 feature
- ✅ **Phase 0.6 反馈重规划 + 双范式切换**（2026-05-16 完成，B 扛，commit 4d8d17b）：
  - `backend/main.py` 加 POST `/chat/refine` 端点（按 api_contract.md §7）：refinement_start → 启发式 refiner（`_stub_refine`，A 实现 agent.refiner 后自动切真路径）→ refinement_done → 复用主路径事件序列；session 不存在 422
  - `_stub_stream` 加 intent_override + starting_seq 参数，让 search_pois / search_restaurants 的 input.distance_max_km 真实反映 refined intent（不只在 refinement_done 改）
  - PLANNER_MODE 切换：/health 暴露当前 env mode；`X-Planner-Mode` header > env > "rule"，三端点均透传到响应头
  - `backend/.env.example` 加 PLANNER_MODE 段
  - `backend/scripts/verify_refine.py` 13 项端到端断言全过：「太远了 3 公里以内」反馈下 distance_max_km 5→3，POI 候选从 3 条压到 1 条（仅 P007 2.8km）
  - CodeSee sync：仅升 f-refine-replan（planned → implemented，step 8→11，confidence 0.3→0.85，refs 补 4 项），不动 f-llm-planner（A owner）

- ✅ **W3 拒绝+反馈 UI + Planner 模式切换器**（2026-05-17 完成，C 扛，commits 8ce2c0a → a0aa6fd 共 4 个）：
  - **C1 三按钮**（commit 8ce2c0a）：ItineraryCard 由单一「确认并预约」拆为「确认/我说说哪不对/取消方案」三按钮网格；hasOrders / cancelled / streaming 三态切换；新建 ToastStack 右下角浮层
  - **C2 RefinementDialog 弹窗**（含 C1 commit）：textarea 200 字限长 + 6 条预设建议 chip + Ctrl/⌘+Enter 提交 + ESC/遮罩关闭；fire-and-forget 提交后立即关，由 store 处理 SSE 流
  - **C3 SSE 解析 refinement_* + toast**（commit 8b6225b）：store.handleEvent 处理 refinement_start/done；changed_fields ≤2 条独立 toast / >2 条聚合；新建 `verify-refine.mjs` 端到端联调脚本（refined_intent.distance_max_km 5→3 + X-Planner-Mode header 透传 + 非法 session 422）
  - **C4 PlannerModeBadge 顶栏切换器**（commit 5b3609d）：低饱和 chip 单击循环 rule↔llm；mount 时 cookie > /health 兜底（silent 模式不弹 toast）；store.setPlannerMode 内写 cookie + 用户主动点弹提示；sendMessage/confirm/refine 全部带 X-Planner-Mode header
  - **C5 CodeSee sync**（commit a0aa6fd）：升级 f-itinerary-card（confidence 0.88→0.92，confirm_btn 拆三按钮 + refinement_banner step）；新增 3 个 feature（f-refinement-dialog / f-planner-mode-badge / f-toast-stack）+ 8 条 cross_feature；不动 A 的 f-refine-replan / f-llm-planner、B 的 f-tool-trace
  - 校验：30/30 vitest（23 sse + 7 store）；pnpm verify:all 4/4 全过（lint / ts / test / build，首页 19.2 kB）；浏览器实测三按钮 + 弹窗 + toast + 切换器全链路；零 console error；color 仅用 brand-orange + ink + emerald/sky/amber，全程无紫粉

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

**Round 1（W4 r1，2026-05-16）：MVP-1 验收硬交付**

- ✅ **设计文档 1-2 页**（A16）：`docs/05-design/设计文档.md`
- ✅ **8 场景端到端集成测试**：`backend/tests/test_8_scenarios.py` 17 项全过
- ⬜ **MVP-3 开放鲁棒性压测**：依赖 DEEPSEEK_API_KEY
- ⬜ **现场演示录屏 3 版本**：B 录屏脚本已写好（commit 8831805），等真 LLM 链路稳定后启动

**Round 2（W4 r2，2026-05-16，Phase 0.6）：双 planner mode + 反馈重规划**

- ✅ **Phase 0.6 契约层**（commit 9bb3a64）：`schemas/refine.py` + `schemas/planner_mode.py` + `api_contract.md` §7+§8
- ✅ **A1 refiner**（commit 4f09dac → bc37f51）：refine_intent + 关键词兜底；`client` 改默认可选给 B 零改动调用
- ✅ **A2 llm_planner**（commit 5b0d2dd）：LLM Function Calling 自主规划 + 失败 fallback
- ✅ **A3 双范式入口**（commit 4555c25）：`plan_itinerary_with_mode(intent, mode)`
- ✅ **A4 单测**（commit 82871da）：refiner 5 项 + llm_planner 13 项
- ✅ **A5 CodeSee sync**（commit 370df5e）：f-llm-planner / f-refine-replan 升 implemented
- ✅ **B /chat/refine + PLANNER_MODE**（commit 4d8d17b）：13/13 verify_refine 全过
- ✅ **C 前端 UI**（commit 8ce2c0a → 5b3609d）：三按钮 / Toast / RefinementDialog / PlannerModeBadge
- ✅ **联调 main.py 接真 planner**（commit 4f8afb3）：`PLANNER_USE_REAL=1` 切换；B 验证 13/13 双模式仍过
- ✅ **A6 联调矩阵 40 项**（commit dc34a2b）：8 场景 × 2 mode × 含/不含反馈
- ✅ **A7 设计文档双范式段**（commit a5e014b）：≤ 半页

**Round 3（W4 r3，2026-05-16/17）：LLM 解耦 + 浏览器实测**

- ✅ **LLM 客户端解耦**（commit 7d6fde1）：`OpenAICompatibleClient` 通用类；`LLM_API_KEY/BASE_URL/MODEL` 主接口；保留 DeepSeek/Qwen 别名向后兼容；支持 OpenAI / 智谱 / Ollama / vLLM 等任意兼容服务（详见 problem 问题9）
- ✅ **浏览器真链路实测**（commit 9b800e3）：MimMo (mimo-v2.5-pro) 端到端跑通；修复 2 bug——(1) /health llm_provider 解耦回归 (2) LLM 同步阻塞触发 8s 首字节超时 → 心跳事件 + asyncio.Queue 实时流（详见 problem 问题10）

**Round 4（W4 r4，2026-05-17）：方案 C persona + memory 个性化（Phase 0.7）**

- ✅ **persona schema + 5 mock**（commit bb7c43c）：u_dad / u_biz / u_grandma / u_solo / u_couple，每个含 default_tags + suitable_for_priority
- ✅ **memory 累积**（同 commit）：confirm 后 accepted_tags +1 + 距离历史；refine 中 rejected_tags +1
- ✅ **compute_priors 合并打分**：persona × 0.3 + memory × 0.7，rejected 1.5× 强惩罚
- ✅ **Prompt 注入**：`build_intent_parser_system_prompt_with_priors(user_id)`；保守补全规则（social_context 必注，physical/dietary/experience 默认空避免过严）
- ✅ **planner 五级降级**：距离 +2km → 剥 preferred_types → 剥 prior tag → 最宽松；search Tool quota 3→5 / 总 12→16
- ✅ **3 个新端点**：`GET /personas` / `GET /preferences/{user_id}` / `POST /preferences/{user_id}/reset`
- ✅ **前端 UserSwitcher + PreferencesPanel**：顶栏切换器 + 偏好画像面板（accepted/rejected top 5）
- ✅ **13 项 persona+memory 测试** + 浏览器实测 persona 切换+确认下单+偏好累积全跑通
- ✅ **CodeSee sync**（pending）：features.json 加 f-persona-prior + f-memory-learning

**仍待开**：

- ⬜ **现场演示录屏 3 版本**（B 录屏脚本 commit 8831805 已备好）
- ⬜ **提交 + 修复最后翻车点**

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
