# frontend —— Next.js 14 App Router

> P3 W3 启动时由 B 同学初始化。本文件先占位 + 锁定与后端的契约引用。

## 启动前必读

1. **后端契约**：`backend/api_contract.md`（HTTP 路径 + SSE 事件序列）
2. **类型来源**：`backend/schemas/sse.py` + `backend/schemas/itinerary.py` + `backend/schemas/intent.py`
3. **演示场景**：`docs/01-requirements/演示场景集.md`（8 个场景的输入文案 + 调性）
4. **环境变量**：`.env.local` 内填 `NEXT_PUBLIC_API_BASE=http://localhost:8000`

## 启动方式

```bash
# 后端（stub 模式，不需要 LLM API key）
cd backend && uv run uvicorn main:app --port 8000

# 前端
cd frontend && pnpm dev
```

## TypeScript 类型同步策略

后端 Pydantic 模型 → 前端 TS 类型，本项目采用 **手抄方案**（`lib/types.ts`），原因：

- `tags.Literal` 在 JSON Schema 上会被展开成大量 anyOf，自动生成结果难读
- 项目体量手抄维护成本最低（~150 行）
- 后端字段变动 → 先改 `backend/schemas/`，再 grep + 同步本目录 `lib/types.ts`，再 grep 组件代码

## 与后端的硬契约

```text
| 字段            | 来源                  | 改动纪律                             |
|-----------------|-----------------------|--------------------------------------|
| SseEvent        | schemas/sse.py        | 双方同时改，不能单边                 |
| Itinerary       | schemas/itinerary.py  | 同上                                 |
| IntentExtraction| schemas/intent.py     | §5.7 D-SoT，绝对禁止前端发明字段     |
| /chat/stream    | api_contract.md §2    | 路径/方法/事件序列固定               |
```

## 校验脚本

```bash
pnpm verify:all   # lint + typecheck + vitest + next build 一气过
pnpm test         # 仅 vitest（23 项 SSE 解析鲁棒性单测）
```

后端起来后还可以跑：

```bash
node frontend/scripts/pressure-test-scenarios.mjs   # 8 场景 SSE 端到端
```

## 演示录屏脚本

录屏不入仓（`recordings/` 已加 `.gitignore`）。建议工具：OBS / Loom / Win+G 自带录屏。

### 通用准备

1. **关掉所有可能弹窗**：钉钉 / 微信 / 邮件通知
2. **清空浏览器**：`Ctrl+Shift+Delete` 清掉前次会话；地址栏 `localhost:3000`
3. **窗口大小**：建议 1440×900（DevTools Device Toolbar 设 Desktop）
4. **后端先跑稳**：`curl http://localhost:8000/health` 看到 `"status":"ok"` 才开始
5. **每次开拍前**：点页面右上「重置」按钮，确保是 fresh state

### 3 分钟版（家庭主路径 + E1 异常恢复）

```text
| 时间   | 操作                       | 旁白要点                                       |
|--------|----------------------------|------------------------------------------------|
| 0-15s  | 介绍页面布局：8 场景 / 聊天 / 行程 / Tool 链路 | 「这是晌午局，本地半日出行管家」  |
| 15-25s | 鼠标 hover S1 按钮看 tooltip 显示完整文案     | 「评委可一键提交 8 个预设场景」    |
| 25-30s | 点击「S1 · 家庭主线」按钮                    | 「我是减肥的妈妈带 5 岁孩子」      |
| 30-50s | 看意图卡片实时渲染：抽出 5 岁 / 低脂 / 家庭   | 「Agent 听懂了约束」               |
| 50-100s| 看 Tool 链路逐条出：用户画像 → POI → 餐厅 → 17:00 满 → 重规划 | 「评分项 5：异常韧性」 |
| 100-130s| 看 17:30 改约成功 → 行程卡片六段时间轴渲染   | 「方案出来了」                     |
| 130-160s| 点「确认并预约」→ 订单号 + 转发文案出现      | 「转发文案给老婆，一键复制」       |
| 160-180s| 点「复制到剪贴板」→ 打开微信 / 文本框验证    | 「评委可以亲自看到文案落到剪贴板」 |
```

### 5 分钟版（含异常 + 一个开放场景）

3 分钟版基础上：

- 在第 130s 后增加 **S7 · 独处放空** 演示（评委想看不同社交语境）
- 注意点 S7 前先点页面右上「重置」让 trace 面板归零
- 旁白点出「同一套 Tool 不写 if scene_type 分支也能 cover 8 种社交场景」

### 完整版（8 场景全跑通）

按 S1 → S8 顺序点完所有按钮。每个场景之间「重置」一次以避免 trace 残留。脚本：

```bash
# 后端 + 前端起好后，开 OBS 录屏；按下面顺序点
# 实时间预算：每个场景平均 30s（流式 + 看链路）= 4 分钟
# 加上意图旁白 1-2 分钟 = 总长 5-6 分钟
node frontend/scripts/pressure-test-scenarios.mjs
```

把上面这条命令的输出当作「8 场景压测全过」的字幕证据。

### 录屏文件管理

- 存放：`recordings/`（仓库根目录，**已 gitignore**）
- 命名：`shangwuju_3min_<YYYYMMDD>.mp4` / `shangwuju_5min_<YYYYMMDD>.mp4` / `shangwuju_full_<YYYYMMDD>.mp4`
- 备份：录完上传到云盘 / 飞书；`recordings/README.md` 记一行链接，**链接才入仓，文件不入仓**

## 已决定

- 状态管理库：**Zustand**（lib/store.ts）
- 主题色：暖橙 brand-orange + 沉静蓝灰 ink；避开紫粉
- 组件库：Tailwind 自手写组件（shadcn 原料 cn + clsx + tailwind-merge）
