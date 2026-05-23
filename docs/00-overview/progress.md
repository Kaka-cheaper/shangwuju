# 开发进度

> 本文件是 **session 衔接文件**。每次新开会话的 AI 先读这里，就能在 30 秒内知道上次做到哪、下次从哪开始。
>
> 每次 session 结束前必须更新本文件。格式见文末"更新规则"。

## 一、当前位置

**阶段**：**Phase 0.20 LangGraph Plan-and-Execute 业界标配重构完成**（2026-05-20）

**MVP 状态**：

```
| 阶段     | 完成度    | 说明                                                       |
|----------|-----------|------------------------------------------------------------|
| MVP-1    | 100% ✅    | 6 Tool + 主场景闭环 + E1 显式触发 + Web UI + SSE          |
| MVP-2    | 95% ✅     | 8 Tool / 8 场景全跑通 / 用户确认 / 双 planner mode / 反馈重规划 |
| MVP-2.5  | 100% ✅    | LLM 客户端解耦（任意 OpenAI 兼容 base_url）                |
| MVP-3 个性化 | 100% ✅ | persona prior 注入 + memory 累积 + 偏好画像面板（Phase 0.7） |
| MVP-3 输入域路由 | 100% ✅ | LLM 前置 6 类分类 + 暖心气泡 + 引导按钮（Phase 0.8）  |
| MVP-3 LLM-First Planner | 100% ✅ | LLM 自主决段 + critic backprompt + 4 级 fallback（Phase 0.10.3）|
| MVP-3 ReAct 单一 Agent | 100% ✅ | LLM 看全部 8 工具自主决策 + critic 兜底（Phase 0.12，现 fallback）|
| MVP-3 商业抽象层 | 100% ✅ | ToolProvider / ConversationRepository / observability 三层骨架（Phase 0.11）|
| MVP-3 LangGraph 主架构 | 100% ✅ | Plan-and-Execute + Routing + Evaluator-Optimizer 三大业界范式（Phase 0.20）|
| MVP-3 演示 | 阻塞     | 真 LLM 链路已实测；剩录屏 3 版本 + 现场 dry run             |
```

**测试矩阵**：267 项 pytest + 23 vitest + 7 store + 13 verify_refine + 7 verify_router + 4 verify_llm_first + 5 verify_react_agent + 5 verify_v2_react = 331 全过

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
| 输入域路由 router（P0.8）      | 14/14  |
| segment_decider（P0.10.1）     | 22/22  |
| 1h 反馈防御（P0.10.2）         | 21/21  |
| Blueprint 数据结构（P0.10.3）  | 20/20  |
| Blueprint LLM 生成（P0.10.3）  |  9/9   |
| Blueprint 拼装（P0.10.3）      |  9/9   |
| critics_v2 单测（P0.12）        | 11/11  |
| 后端合计                       |267/267 |
| 前端 vitest（W3）              | 23/23  |
| 前端 store 测试                |  7/7   |
| B 的 verify_refine（双模式）    | 13/13  |
| 路由端到端 verify_router        |  7/7   |
| LLM-First 真 LLM e2e（P0.10.3）|  4/4   |
| ReAct Agent 真 LLM e2e（P0.12）|  5/5   |
| /chat/turn ReAct e2e（P0.12） |  4/4   |
| 总计                           |331/331 |
```

**真 LLM 链路实测**：
- MimMo (mimo-v2.5-pro) 端到端浏览器实测全过——意图解析 / 双 mode / 反馈重规划 / persona 切换 / memory 学习 全部跑通
- LLM-First Planner 4 场景真 LLM 跑通：1h 反馈 73min/3 段 / 只想吃饭 19:00 用餐 / 独处沉浸 220min/3 段 / 家庭半日 250min/4 段
- **ReAct 单一 Agent 4 场景真 LLM 跑通（Phase 0.12）**：闲聊不调工具 / POI Q&A 调 search_pois / 完整规划 11 工具调用 / dock 反馈"太远了 3km"自动识别（不再需要前端区分对话框 vs 反馈按钮）；烟花 64 粒子完整 1.78s 生命周期

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

- ✅ **Phase 0.9 A+C 混合规划范式**（2026-05-17 完成，A 扛，commit 4299b14）：
  - **学术依据**：Vansteenwegen 2009（TOPTW + ILS）+ Gunawan 2019（多目标 TOPTW 加权和）+ Kambhampati NeurIPS 2024（LLM-Modulo Critic）+ ItiNera EMNLP 2024（LLM 决主观 + 算法决客观）
  - **A 段**：`backend/agent/weights_llm.py` 4 维加权效用权重（comfort/time/cost/smoothness），LLM 出权重失败兜底启发式（按 social_context + 同行人 + raw_input 关键词）；`backend/agent/planner_hybrid.py` 候选生成（top-K=5）+ utility 函数（rating/标签/距离/预算/连贯）+ ILS 30 次迭代（扰动 swap_poi/swap_rest/shift_time + 邻域贪心，5% 接受劣解）
  - **C 段**：`backend/agent/critics.py` 4 个 Critic（HardConstraint 段缺失/总耗时；TimeWindow 查 mock reservation_slots；Budget 软违规；Style suitable_for 软违规）；硬违规 → 黑名单候选 + 重排
  - **集成**：`plan_itinerary_with_mode` 新增 stub_check（保单测稳定）+ PLANNER_LLM_STRATEGY=hybrid|function_calling 切换；hybrid 复用 rule planner 已有的 `_resolve_time_window` / `_estimate` / `_assemble_itinerary` helper 拼装六段，零重写
  - **失败兜底**：四级 fallback——stub client → API 不可用 → ILS 失败 → Critic 重排失败，全部回 rule planner 保 demo
  - **演示**：`backend/scripts/verify_planning.py` 4 场景对比（S1 家庭 / S4 老人 / S6 商务 / S8 纪念日）；S1 hybrid 修复 rule 的 R023 hard 违规；S6 hybrid 修复 rule 的 R008 style soft 违规
  - **测试**：170/170 全过（含新增 15 项 hybrid 单测：权重启发式 6 项 + Critic 5 项 + utility 1 项 + 端到端 3 项）+ 4 个原 verify 脚本无回归
  - CodeSee sync：仅升 `f-llm-planner`（owner=A），summary + step 9→12 + flow 按 strategy 分支 + refs 补 5 个新文件 + confidence 0.78→0.88；不动其他 owner 的任何 feature

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

**Round 5（W4 r5，2026-05-17）：LLM-First Planner（Phase 0.10.3，产品级架构重构）**

- ✅ **诊断**：用户反馈"耦合还是有点严重"——hybrid 仍然是算法层用 `decide_segments` 启发式决定段集合，违反 LLM-Modulo「LLM 决主观、算法决客观」分工；用户场景"只想吃饭/夜宵/24h营业/反序"用 if 启发式枚举不完
- ✅ **Commit 1 蓝图数据结构**：`agent/blueprint.py`（PlanBlueprint / BlueprintStage / BlueprintTargetKind / 3 critic：时序 / 时长 / 营业时间）；`tests/test_blueprint.py` 20 项✓
- ✅ **Commit 2 LLM 蓝图生成器**：`agent/blueprint_llm.py`（generate_blueprint + build_candidate_preview + BlueprintGenError 围栏剥离 + 校验）；`agent/prompts/blueprint_prompt.py`（system prompt 强调段集合自由 + raw_input 精确数字必须遵守）；`tests/test_blueprint_llm.py` 9 项✓
- ✅ **Commit 3 蓝图→Itinerary 拼装**：`agent/assemble_blueprint.py`（按 segments 自适应 summary）；`tests/test_assemble_blueprint.py` 9 项✓
- ✅ **Commit 4 主流程**：`agent/planner_llm_first.py`（候选搜索 → LLM 蓝图 → critic backprompt 重试 ≤2 次 → 拼装）
- ✅ **Commit 5 planner 集成**：`_plan_with_llm_first` 适配器；`PLANNER_LLM_STRATEGY` 默认 hybrid → llm_first；4 级 fallback 链 llm_first → hybrid → rule
- ✅ **Commit 6 真 LLM e2e**：`scripts/verify_llm_first.py` 4 场景全过
  - S1 1h 反馈 → 73min/3 段（出发→用餐→返回，不再硬加主活动）
  - S2 只想吃饭 → 19:00 出发用餐方案，LLM 自主选晚餐时间，无主活动
  - S3 独处沉浸 → 220min/3 段（出发→主活动→返回），LLM 自主决定不加用餐
  - S4 家庭半日 → 250min/4 段（自动省略转场段路程缓冲）
- ✅ **Commit 7 文档**：problem 问题 15 / pitfalls P1-2026-05-17-llm-first / progress D-llm-first / .env.example llm_first 选项
- ✅ **测试矩阵**：256/256 pytest（含新增 38 项）+ verify_schemas 6/6 + verify_llm_first 4/4 真 LLM e2e

**仍待开**：

- ⬜ **现场演示录屏 3 版本**（B 录屏脚本 commit 8831805 已备好）
- ⬜ **提交 + 修复最后翻车点**

**Round 6（W4 r6，2026-05-17）：跨 turn 上下文管理 + Pydantic AI 重构（Phase 0.11 前置）**

- ✅ **Commit 1 ConversationStore + Pydantic AI 单一入口**（commit 81141cb）：`agent/v2/conversation.py` ConversationStore 单进程 dict + ConversationState 持有 messages/intent_snapshot/itinerary_snapshot；新增 `agent/v2/orchestrator.py`（旧路径决策 looks_like_feedback / decide_turn_kind）；`backend/main.py` POST `/chat/turn` 端点
- ✅ **Commit 2 narrator 暖语气导游开场白 + IntentChips**（commits d37f959 / 6691320）
- ✅ **Commit 3 Dock 可拖动调整高度 + 多 session 切换 UI**（commit 08589a6）：解决"对话框和右侧不对称"——dock 玻璃浮窗范式，支持上下拖动调整高度

**Round 7（W4 r7，2026-05-17）：Phase 0.11/0.12 multi-agent 并行 ReAct 重构 + 商业抽象层（commit 8c06326 推送 12 commits）**

> 用户决策：「方案 2，范围全做，边界不需要了」——一次性完成所有 7 个 Agent 的工作。
> 学术依据：LLM-Modulo (Kambhampati NeurIPS 2024) ReAct + Critic 闭环 / Pydantic AI 框架 (Pydantic team 官方支持)

- ✅ **Agent A schema 加固 + 中文词典强约束**（commit 1f94235）：`schemas/intent.py` companions/physical/dietary/experience 4 字段从 default_factory 改为必传（值可空）；`schemas/router.py` cta_chips 必传（max_length=4）；6 个 prompt 加「字段抽取义务」+「中文词典强约束」段（system / router / refiner / narrator / llm_planner / blueprint）；新增 `verify_schema_hardening.py` 5/5 真 LLM e2e
- ✅ **Agent B ToolProvider 数据源抽象 + observability 结构化日志**（commit 0b470db）：
  - `agent/v2/tool_provider.py` ToolProvider Protocol（@runtime_checkable，8 工具）+ MockToolProvider（asyncio.to_thread 包同步 Tool）+ GaodeToolProviderStub + DianpingToolProviderStub
  - `agent/v2/observability.py` get_logger / bind_session_context / clear_session_context / trace_span，幂等配置；LOG_FORMAT=text|json 切换
  - `backend/.env.example` 加 DATA_PROVIDER=mock|gaode|dianping + LOG_FORMAT=text|json
  - `backend/scripts/verify_tool_provider.py` 5/5 验证脚本
- ✅ **Agent C ConversationRepository 重构（Phase 0.11 主菜）**（commit e3767ca）：
  - `agent/v2/conversation.py` 引入 ConversationRepository Protocol + InMemoryRepository（demo 默认）+ RedisRepositoryStub（Milestone 2 接入点，5 个写方法抛友好 NotImplementedError）
  - `get_default_repo()` 单例工厂；`SESSION_STORE=memory|redis` 解析；非法值 fail fast
  - 旧名 `ConversationStore = InMemoryRepository` + `get_default_store()` 委托保持向后兼容（main.py / orchestrator.py 0 改动）
  - `_reset_default_repo_for_tests()` 让 verify 脚本能切 backend
  - `verify_repository.py` 5/5 + pytest 256/256 + verify_v2_turn 通过
- ✅ **Agent D 商业演进文档 6 篇 + 路演大纲**（commit ec03a16，8 docs 创建 + README +11 行）：
  - `docs/05-design/设计文档.md` 加附录 A/B/C（ReAct 范式说明 + 跨 turn 上下文 + 商业演进概览）
  - `docs/06-business/01-数据源切换路径.md`（10.9 KB，三阶段 + 切换工作量）
  - `docs/06-business/02-持久化演进.md`（7.5 KB，dict→Redis→PG）
  - `docs/06-business/03-观测性骨架.md`（7.8 KB，structlog→OTel）
  - `docs/06-business/04-商业模式.md`（8.5 KB，3 候选 + 单位经济）
  - `docs/06-business/05-差异化定位.md`（7.9 KB，四象限对比）
  - `docs/06-business/06-增长路径.md`（7.8 KB，0→10K+ 三阶段）
  - `docs/07-pitch/路演大纲.md`（16.6 KB，10 页 PPT 大纲）
- ✅ **Agent E ReAct 单一 Agent 主体（Phase 0.12 主菜）**（commit f48ab65 / dc2fdae）：
  - `agent/v2/react_agent.py`（约 720 行）unified_agent: Agent[AgentDeps, AgentOutput] 模块级实例
  - 8 工具用 `@unified_agent.tool` 装饰参数化展开 + `trace_span` 包裹（不传整个 Input 模型给 LLM）
  - `@instructions` 动态绑定 user_id / session_id 上下文
  - `@output_validator` 接 critics_v2（try/import 兜底 F 未合流）
  - 三层 MiMo 容错：prompt 警示 + 入参 _coerce_* 函数 + `_FlexibleItineraryResponse` 子类
  - `agent/v2/output_types.py` ChatResponse / ItineraryResponse / AgentOutput Union（commit dc2fdae 补漏 stage）
  - `run_react_turn_inner` 公共入口
  - `backend/scripts/verify_react_agent.py` 5 场景：闲聊 / POI Q&A / 完整规划 / 拒答 / 上下文反馈；LLM_PROVIDER=stub 时 SKIPPED
- ✅ **Agent F critic 兜底层（LLM-Modulo 范式）**（commit bd9eb83）：
  - `agent/v2/critics_v2.py` 7 类 ViolationCode（DURATION / DISTANCE / STAGES / RESTAURANT_FULL / TIMELINE / SOCIAL / DIETARY）+ 2 级 Severity + Violation 模型 + validate_itinerary 主入口 + format_violations_for_llm helper
  - 与旧 `agent/critics.py` 解耦：旧的是 hybrid 内部组件，新的是给 ReAct Agent 的 output_validator
  - `tests/test_critics_v2.py` 11 项端到端断言（合法零 critical / 段数缺失 / 时长高低两端 / 时序反序 / format 仅 critical / dietary 命中与未命中 / demo-aware 17:00 开关）
- ✅ **Agent G /chat/turn 接 ReAct + USE_REACT_AGENT flag**（commit 330cc80）：
  - `agent/v2/orchestrator.py` 加 `run_react_turn` 流式包装器（拦截 unified_agent.iter() 的 tool_call 推 SSE 事件）
  - `backend/main.py` /chat/turn 端点优先走 ReAct 路径；探活失败自动 fallback 旧 router→planner / refiner 双路径
  - `USE_REACT_AGENT=1` 默认 ON，`=0` 走旧路径（demo 安全兜底）
  - `verify_v2_react.py` 4 场景（闲聊 / POI Q&A / 完整规划 / 反馈）真 LLM 跑通
- ✅ **浏览器真 LLM 实测**：4 测试全过——闲聊不调工具 / POI Q&A 调 search_pois / 完整规划 11 工具调用 / dock 直接反馈"太远了 3km"自动识别为反馈而非新需求；烟花 64 粒子完整 1.78s 生命周期；夜色玻璃 dock 拖动 + 多 session 切换均跑通

**遗留小 bug**（用户决议不修先 push）：
- 第二次 confirm 后 orders / share_message 偶发不渲染（abortController 时序问题）

**Round 8（W4 r8，2026-05-20）：Phase 0.20 LangGraph Plan-and-Execute 业界标配重构（commits 52d8535 → 5c16144 共 4 个核心 + 1 sync）**

> 用户决策：「先按业界成熟范式来，创新可以之后再加」+「ILS 应该结合现有算法」+「就按推荐路线，先看效果」
> 学术依据：LangChain 官方 workflows-agents 三大范式（Routing + Plan-and-Execute + Evaluator-Optimizer）+ AWS Nova travel agent 案例 + 学术 2025 旅行规划论文（arxiv 2509.21842 / 2512.11271 / 2405.18208）
> 框架选型依据：LangGraph 1.2.0（2025-10 GA，47M+ monthly downloads）+ langchain-openai ChatOpenAI（base_url 接 MiMo）+ InMemorySaver checkpointer

**Phase 0 烟雾测试**（commit 52d8535）：

- ✅ 加依赖 `langgraph 1.2.0` + `langchain-openai 1.2.1` + `langchain-core` 14 个新包
- ✅ `backend/scripts/smoke_langgraph_mimo.py` 4 步烟雾测试全过
- ✅ **关键发现并修复 MiMo thinking 模式 + LangGraph 兼容性问题**：MiMo v2.5 Pro 是 thinking 模型，第二轮调用要求传回 reasoning_content；LangGraph 默认不携带 → 加 `extra_body={"enable_thinking": False}` 关 thinking（参考 MiMo 官方 vllm recipe），与 DeepSeek-R1 / Kimi K2 thinking / o1 同类问题的标准解法

**Phase 1-9 拓扑构建**（commit 1cdd40c）：

- ✅ `backend/agent/graph/` 子包建立：state.py + build.py + sse_adapter.py + 11 nodes/
- ✅ **AgentState TypedDict** 复用 v1 已有 schema（IntentExtraction / Itinerary / PlanBlueprint / PlanningWeights / Violation / RouterDecision），不发明新结构；messages 字段用 langgraph.graph.message.add_messages reducer 自动 merge
- ✅ **16 节点拓扑全部 build**：router / chitchat / intent / refiner / 4 个并行 execute worker / planner / assemble / critic / replan_router / ils_replan / narrate / execute_finalize
- ✅ **Plan-and-Execute 范式核心**：LLM 决主观（PlanBlueprint 段集合/段顺序/每段时长/target_id）+ 算法决客观（critics_v2 7 类约束）+ critic backprompt 重试（≤2 次）+ ILS 算法兜底
- ✅ **InMemorySaver checkpointer + thread_id=session_id**：跨 turn 持久化 messages

**Phase 10-14 接入与验证**（commit 7c07441）：

- ✅ `backend/main.py` /chat/turn 端点加 USE_LANGGRAPH=1 主路径；探活失败 fallback USE_REACT_AGENT=1 → fallback rule planner（三层 fallback 链让 demo 永不翻车）
- ✅ `backend/agent/graph/sse_adapter.py` 275 行：graph.astream(stream_mode='updates') → 现有 SseEventType 序列；前端零改动复用旧事件 schema
- ✅ `backend/scripts/verify_langgraph.py` 端到端 3 场景全过：planning 主路径 20 事件含 critic backprompt + ILS replan / chitchat / feedback-like 鲁棒性
- ✅ **真 LLM 浏览器实测**：S1 家庭主线 LangGraph 路径完整跑通——20 事件 / 4 段→5 段 critic backprompt 闭环 / 暖语气 narration / X-Turn-Kind: langgraph header；feedback 反馈环 distance 5km→3km / POI 切换悦读绘本馆→童趣海洋亲子馆 / 餐厅切换轻语沙拉→绿野鲜厨

**Phase 15 文档对齐**（commit 5c16144）：

- ✅ `.codesee/features.json` 全量 sync：+1 graph epic / +11 graph feature / 4 legacy 标记 / 1 fallback 标记 / +27 cross_feature / +5 epic_flow（graph epic depends_on 5 旧 epic 表达「复用关系」）
- ✅ 所有现有算法零代码废弃：PlanBlueprint / weights_llm / critics_v2 / planner_hybrid ILS / narrator / refiner / segment_decider 全部嵌进 graph 节点

**测试矩阵**（Phase 0.20 后）：

```
| 套件                          | 通过项     |
|-------------------------------|-----------|
| 后端 pytest（旧测试零破坏）    | 267/267   |
| verify_langgraph 真 LLM e2e   |   3/3     |
| smoke_langgraph_mimo（Phase 0）|   4/4     |
| 浏览器实测 LangGraph 路径     | 主路径+反馈环全过 |
```

**对评分项的影响**：

```
| 评审维度  | 重构前        | 重构后                                          |
|----------|--------------|------------------------------------------------|
| 创新性   | 中            | 高（LLM-Modulo + Plan-and-Execute + Hybrid 合体）|
| 完整性   | 中            | 高（LangGraph 业界标配 + plan 显式 + checkpoint）|
| 应用效果 | 中            | 高（execute 阶段并行 + 算法兜底 + critic backprompt）|
| 商业价值 | 中-高         | 高（业界共识架构 + 三层抽象就绪 → 真产品演进路径）|
```

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

- **D-segments** [2026-05-17]：行程段集合是 IntentExtraction 的函数，不是模板常量
  - 候选：A 维持 5 段写死（最快）/ B 段=intent 函数（本决策）/ C Itinerary 升级为段图（重构）
  - 选择：B
  - 理由：用户反馈"我只有一个小时"暴露 5 段写死的反模式；refiner 改 duration 后下游不消费段维度变化（详见 `pitfalls.md` P1-2026-05-17）。把段决策抽到 `agent/segment_decider.py` 让所有规划路径（rule / hybrid fallback）都走它，保证「反馈→约束→段集合」单向数据流。
  - 影响：
    - `演示场景集.md §三` 的"5 段期待结构"语义改为"典型场景下的默认段集"，不是必要约束
    - `_assemble_itinerary` 接受 `segments` 参数，main_poi/chosen_restaurant 改为可空
    - `HardConstraintCritic` 按 `decide_segments(intent)` 判段缺失
    - hybrid 路径检测到削段直接 fallback rule（ILS 假设 POI×餐厅 笛卡尔积）
    - 4 处测试断言从「硬要 5 段」改为「按 intent 期望」
    - 引申：未来加 segment 类型（如「下午茶」「city walk」「附加加购」）只需扩 `segment_decider`，无需改 planner / critics
  - **后续被 D-llm-first 取代**：D-segments 仍然解决了 rule + hybrid 路径的"5 段写死"，但段决策仍是算法层用 if 启发式做。D-llm-first 把段决策完全交给 LLM；segment_decider 仅在 fallback 到 rule/hybrid 时使用。

- **D-react-unified** [2026-05-17]：ReAct 单一 Agent 取代「router → planner / refiner 双路径」作为 /chat/turn 的默认实现
  - 候选：A 维持双路径（router 6 类分类 + 旧 planner / refiner）/ B ReAct 单一 Agent（LLM 看全 8 工具自主决策）/ C 多 Agent 协作（KidAgent + ElderlyAgent 等专家 Agent）
  - 选择：B
  - 理由：用户在 dock 直接输入"太远了 3 公里以内"暴露双路径耦合——LLM 看不到「上次方案」上下文，把反馈当成新需求重解析（详见 `pitfalls.md` P1-2026-05-17-react 与 `problem.md` 问题 18-19）。多 Agent 现阶段不必要：8 个 Tool + 双 mode + 关键词 fallback 三层防御已经覆盖鲁棒性需求。让 LLM 在 ReAct 循环里 Reason+Act+Observe，跨 turn message_history 用 ConversationRepository 持久化。
  - 影响：
    - 新增 `backend/agent/v2/react_agent.py`（unified_agent: Agent[AgentDeps, AgentOutput] 模块级实例 + 8 工具参数化展开 + critics_v2 output_validator + 三层 MiMo 容错）
    - 新增 `backend/agent/v2/output_types.py`（ChatResponse / ItineraryResponse Union 让 LLM 自己选输出形态）
    - 新增 `backend/agent/v2/critics_v2.py`（与旧 `agent/critics.py` 解耦：旧给 hybrid，新给 ReAct）
    - 新增 `backend/scripts/verify_react_agent.py`（5 场景）+ `verify_v2_react.py`（4 场景）
    - `backend/main.py` /chat/turn 端点优先走 ReAct 路径；`USE_REACT_AGENT=1` 默认 ON
    - 评分项 1（场景理解 20%）：LLM 看 raw_input + tool 结果可自主分类反馈 vs 新需求，无需启发式 looks_like_feedback
    - 评分项 2（规划链路 25%）：LLM-Modulo 教科书级实现——LLM 决主观、critic 验客观
    - 评分项 5（异常韧性 15%）：探活失败自动 fallback 旧路径；critic critical 违规 ModelRetry 让 LLM 自纠错

- **D-business-abstraction** [2026-05-17]：引入 ToolProvider / ConversationRepository / observability 三层抽象
  - 候选：A 维持具体实现（demo 简洁但商业演进时全部重写）/ B 引入 Protocol 抽象 + 默认实现 + Stub 实现（本决策）/ C 直接接真高德 / 真 Redis（赛题禁止真 API）
  - 选择：B
  - 理由：用户决议「这是参赛作品+希望真做成产品」——商业大奖维度需要让评委看到「demo 不止于 demo」的扩展性。Protocol 抽象 + Stub 实现是「数据源切换 / 持久化切换 / 观测性接入」的最低成本演示路径——评委只需 grep 一处签名就能验证「换数据源真不需要改业务代码」。
  - 影响：
    - `backend/agent/v2/tool_provider.py` ToolProvider Protocol + MockToolProvider + Gaode/Dianping Stub
    - `backend/agent/v2/conversation.py` ConversationRepository Protocol + InMemory + Redis Stub（向后兼容旧 ConversationStore）
    - `backend/agent/v2/observability.py` get_logger + bind_session_context + trace_span
    - `backend/.env.example` DATA_PROVIDER + LOG_FORMAT + SESSION_STORE 三段
    - `docs/06-business/01-数据源切换路径.md` / `02-持久化演进.md` / `03-观测性骨架.md` 三篇商业演进文档
    - 评委「商业价值」维度直接得分：每个抽象都有「Demo→MVP→真产品」三阶段演进路径

- **D-llm-first** [2026-05-17]：LLM-First Planner 取代 hybrid 作为 mode=llm 的默认实现
  - 候选：A 维持 hybrid（LLM 出权重 + ILS 启发式 + critic）/ B LLM-First Planner（LLM 出蓝图 + critic backprompt + 拼装）/ C 让 LLM 直接 Function Calling 自由调 Tool
  - 选择：B
  - 理由：用户反馈"耦合还是有点严重"——hybrid 仍然是算法层用 `decide_segments` 启发式决定段集合，LLM 只出权重。这违反 LLM-Modulo (Kambhampati NeurIPS 2024) 「LLM 决主观、算法决客观」分工原则。用户场景里有大量反 5 段的需求（"只想吃饭" / "夜宵" / "24h 营业" / "先吃饭再看展" / "独处沉浸"），用 if 启发式枚举不完。引入 PlanBlueprint 中间数据结构作为 LLM 主观决策的契约，让 LLM 自主决定段集合 / 段顺序 / 每段时长 / target_id。详见 `pitfalls.md` P1-2026-05-17-llm-first 与 `problem.md` 问题 15。
  - 影响：
    - 新增 `backend/agent/blueprint.py`（PlanBlueprint 数据结构 + 3 个 critic：时序 / 时长 / 营业时间）
    - 新增 `backend/agent/blueprint_llm.py`（LLM 蓝图生成器 + 围栏剥离 + Pydantic-style 校验 + critic backprompt 重试机制）
    - 新增 `backend/agent/assemble_blueprint.py`（蓝图→Itinerary 拼装；纯函数）
    - 新增 `backend/agent/planner_llm_first.py`（主流程：候选搜索 → LLM 蓝图 → critic backprompt（重试 ≤2 次）→ 拼装）
    - 新增 `backend/agent/prompts/blueprint_prompt.py`（蓝图生成 system prompt + user message builder）
    - 新增 `backend/scripts/verify_llm_first.py`（4 场景真 LLM e2e 验证脚本）
    - `backend/agent/planner.py` 加 `_plan_with_llm_first` 适配器；`PLANNER_LLM_STRATEGY` 默认 hybrid → llm_first；fallback 链改为 llm_first → hybrid → rule
    - `backend/.env.example` PLANNER_LLM_STRATEGY 注释加 `llm_first` 选项
    - 测试矩阵：256/256 pytest 全过（含新增 38 项蓝图相关测试）+ verify_schemas 6/6 + verify_llm_first 4/4 真 LLM e2e
    - 评分项 2（规划链路 25%）：是 LLM-Modulo 教科书级实现——LLM 出 candidate plan，外部 critic 验证，硬违规 backprompt 重生成
    - 评分项 5（异常韧性 15%）：四级 fallback 链（LLM 蓝图重试 → hybrid → rule）让任何失败都有兜底
    - 加新 segment 类型（夜跑 / 晨练 / city walk）→ 只改 prompt 不改代码
  - 学术依据：LLM-Modulo (Kambhampati NeurIPS 2024) + ItiNera (EMNLP 2024) + LLM as Planning Backbone

- **D-langgraph** [2026-05-20]：LangGraph Plan-and-Execute 取代手写 ReAct 单一 Agent 作为 v1 业界标配主架构
  - 候选：A 维持 Pydantic AI ReAct 单一 Agent（v0.12 主架构）/ B LangGraph + Plan-and-Execute（业界共识，本决策）/ C LangGraph + ReAct（业界但视觉密度更强）/ D LangGraph + Plan-and-Execute + Hybrid 合体（本决策的合体形态）
  - 选择：D（B + 把现有 ILS / blueprint / weights_llm 全嵌进 graph 节点）
  - 理由：
    - 用户反问「业界是否有成熟范式」+「先按业界标配，创新可以之后再加」+「ILS 应该结合现有算法」三连问 → 暴露手写 ReAct 是偷懒方案
    - 学术界 + LangChain 官方共识：旅行规划属于「多步可预测 + 多约束」场景，**Plan-and-Execute 优于 ReAct**（参考 langchain blog 2024-02 + arxiv 2509.21842 / 2512.11271 / 2405.18208）
    - AWS Nova travel agent / DocentPro 多 Agent 旅行同伴等业界案例几乎一致选 LangGraph
    - 手写 ReAct + ConversationRepository + ToolProvider + observability 等抽象层等同于「重新发明 LangGraph 的子集」——评委「商业价值」维度本应认知到这是业界共识架构
    - PlanBlueprint（Phase 0.10.3）天然就是 P-and-E 的 plan 形态，**这次重写正好把所有 Phase 0.6-0.12 创新合并进 v1**，不再「v2 创新章节口头讲」
  - 影响：
    - 新增 `backend/agent/graph/` 子包（state.py / build.py / sse_adapter.py + 11 nodes/）共 ~1700 行
    - 新增 `langgraph 1.2.0` + `langchain-openai 1.2.1` + 14 个新依赖
    - 节点全部复用现有算法（PlanBlueprint / weights_llm / critics_v2 / planner_hybrid ILS / narrator / refiner / segment_decider 0 代码废弃）
    - `backend/main.py` /chat/turn 加 USE_LANGGRAPH=1 主路径；三层 fallback 链：LangGraph → ReAct → rule planner
    - 评分项 1（场景理解 20%）：router_node + 启发式 _looks_like_feedback 双层判断 + LLM 自主决策
    - 评分项 2（规划链路 25%）：教科书级 LLM-Modulo + Plan-and-Execute 合体——LLM 出 PlanBlueprint，critic 验证客观约束，硬违规 backprompt 重生成 ≤2 次，ILS 算法兜底
    - 评分项 3（应用效果）：execute 阶段 4 个 worker 并行（LangGraph 多边并发触发）vs ReAct 串行约 3 倍速
    - 评分项 5（异常韧性 15%）：四级 fallback（LLM 重试 → ILS → rule give_up → 三层架构 fallback），每层都推 agent_thought 让评委可见
    - 商业价值评分项：业界共识架构（LangGraph）+ Checkpointer 演进路径（InMemorySaver→SqliteSaver→PostgresSaver）+ 0 代码废弃的「v2 创新可加分」组件库
  - **关键风险与修复**：
    - MiMo v2.5 Pro 是 thinking 模型，第二轮调用要求传回 reasoning_content；LangGraph 默认不携带 → 加 `extra_body={"enable_thinking": False}` 关 thinking（参考 MiMo 官方 vllm recipe + LiteLLM #23828）。这是 DeepSeek-R1 / Kimi K2 thinking / o1 同类问题的标准解法
    - search_adapter invoke_tool 返 dict 不返对象（旧 ReAct 路径未触发的兼容性 bug）→ 加 isinstance + Poi/Restaurant.model_validate 兜底
  - 测试结果：267/267 pytest（旧测试零破坏）+ 3/3 verify_langgraph 真 LLM e2e + 4/4 smoke_langgraph_mimo + 浏览器实测主路径（含 critic backprompt 4 段→5 段）+ 反馈环（distance 5→3km / POI 切换 / 餐厅切换）全过

### D-PLANNING-QUALITY-DEEP-REVIEW [2026-05-23]：spec planning-quality-deep-review 全部 8 task 落地

**决定**：执行业务质量 spec A（5 wave / 8 task / +97 项测试）。

**核心防御链路 7 层全部到位**：
- mock 信息源（SuggestedDuration dict / typical_dining_min / persona pace_profile）
- LLM 主防（BlueprintPrompt 范例 75 + 分级表 + 候选预览消费规则）
- critic 主路径（_age_aware_duration_critic + expected_range 自然语言）
- critic 镜像（critics_v2._check_age_aware_duration 防 ILS 路径绕过）
- 算法 utility（_overload_penalty -0.5 项）
- Narrator 出口（critic_summary 喂 + 主动质疑规则 + 模板兜底）
- Refiner 反馈（"太久" → pace_profile 缩 30%）

**关键决策（来自 adversarial-review §2 取舍）**：
- D 主防 + E 兜底，**拒** A 升级 NodeDecider
- expected_range 弱化版（自然语言"建议 45-75min"，**不**暴露字段名）
- mock dict 升级直接原地 + Pydantic Union 双兼容，**拒** mock_data/v2/ 子目录
- fallback 路由保留 retry_count 阶梯，**拒** F 方案 E 按违规类型路由
- meta_critic_node 本 spec **不加**（留 spec C）

**测试基线**：482 → 560 全过 + 24/24 verify_planning_quality + 84/84 audit_review_template。

**5 个 commit**：5b9cef3 / b399af3 / f05ea59 / 1a74aba / f48f272。

### D-AGENT-RESTRUCTURE [2026-05-23]：spec agent-directory-restructure 落地

**决定**：把 `backend/agent/` 从「25 个扁平 .py + v2/ + graph/ 三套并存」重组为 6 子目录（`core/` / `intent/` / `planning/` / `runtime/` / `graph/` / `legacy/`）+ `__init__.py`。

**理由**：新人 / AI Agent 接入项目时无法识别新代码该写在哪、哪些是冻结模块。spec A 落地后业务质量稳定，正是做目录重组的合适时机；hackathon 评委演示前打磨结构清晰度也是加分项（"工程整洁度"）。

**前置硬约束**：
- spec A 全部 8 task 完成 ✅
- spec A e2e 验收（24/24 + 84/84）✅
- v-spec-a-done git tag 打上 ✅

**实施过程**：6 批次串行（core / intent / planning / runtime / legacy / 收尾），每批 smartRelocate + PowerShell 批改 import + pytest 验证。

**关键发现**：smartRelocate 工具**不自动更新 import 引用**（仅移动文件 + 更新 `__path__`）——与 spec B design.md 假设不符，需要手工 PowerShell 批改 import 路径。

**测试基线**：560 → 599 全过（+39 项 test_import_paths.py），FastAPI import OK。

**新产物**：
- `backend/scripts/verify_legacy_frozen.py`（守 8 个 legacy 模块的 FROZEN 标记）
- `backend/tests/test_import_paths.py`（5 类新路径 + 33 条旧路径 negative 测试）
- `agent/legacy/__init__.py` docstring 说明冻结纪律
- 8 个 legacy `.py` 文件顶部加 `# FROZEN: 详见 AGENTS.md §3.3.1`
- `agent/planning/weights_llm.py` 加 `# FROZEN: 仅 ILS 路径`

**AGENTS.md §3.3.1 同步更新**：目录树代码块换为新结构 + MUST/MUST NOT 段补充新约束。

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
