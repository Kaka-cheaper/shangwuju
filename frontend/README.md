# 晌午局前端

晌午局是一个对话式的「半日出行」规划界面：用户用一句话描述下午的安排，前端实时展示意图解析、工具调用链路与最终行程卡片。本目录是 Web 前端，基于 **Next.js 14 App Router + TypeScript + Tailwind CSS** 构建，所有规划逻辑都由后端通过 SSE / WebSocket 推送，前端只负责实时渲染与交互。

## 技术栈与版本

版本以 `package.json` 为准。

| 类别 | 依赖 | 版本 |
| --- | --- | --- |
| 框架 | next | 14.2.18（App Router，本地 / 容器 `standalone`，GitHub Pages `export`） |
| UI 库 | react / react-dom | 18.3.1 |
| 状态管理 | zustand | 4.5.5 |
| 样式 | tailwindcss | 3.4.13（配套 postcss 8.4.47 / autoprefixer 10.4.20） |
| 地图 | @amap/amap-jsapi-loader | ^1.0.1（高德 JS API 2.0） |
| 图标 | lucide-react | 0.453.0 |
| 海报截图 | modern-screenshot | ^4.7.0（`domToBlob`，不使用 html2canvas） |
| className 合并 | clsx 2.1.1 + tailwind-merge 2.5.4（`lib/utils.ts` 的 `cn`） |
| 语言 | typescript | 5.6.2（`strict: true`） |
| 测试 | vitest | ^2.0.0 |
| Lint | eslint 8.57.1 + eslint-config-next 14.2.18 |

字体在 `app/layout.tsx` 通过 `next/font/google` 加载 Inter（正文）与 JetBrains Mono（等宽，用于工具 JSON / session id / 订单号）。

## 快速开始

前置条件：

- Node.js 18 或更高版本（CI 与镜像使用 Node 20）
- pnpm 9（项目按 pnpm 9 系锁定，`pnpm-lock.yaml` 为 lockfileVersion 9.0；Docker/CI 钉死 `pnpm@9.15.9`）
- 后端服务运行在 `http://localhost:8000`

```bash
pnpm install
pnpm dev
```

打开 http://localhost:3000 。前端不带任何 mock 数据，必须先把后端跑起来（`/scenarios`、`/personas`、`/chat/turn` 等接口都来自后端），否则页面只会显示空状态。

> 提示：`pnpm dev` 与 `pnpm build` 都会先执行 `predev` / `prebuild` 钩子里的 `scripts/clean-next.mjs` 强删 `.next/`，规避 Windows + pnpm + standalone 组合下的 EPERM 删除失败问题。

## 环境变量

复制 `.env.local.example` 为 `.env.local` 后按需填写。变量都以 `NEXT_PUBLIC_` 开头，会在 `next build` 时打进浏览器 bundle（属于 build-time 注入，切换部署环境需重新 build）。

| 变量 | 说明 | 缺省行为 |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE` | 后端基址，对话 / 协作 / 地图代理都基于它。 | 代码默认 `http://localhost:8000`（见 `lib/utils.ts`） |
| `NEXT_PUBLIC_BASE_PATH` | 静态站点部署子路径；GitHub Pages 构建时为 `/shangwuju`。 | 缺省为空，本地路径不加前缀 |
| `NEXT_PUBLIC_AMAP_KEY` | 高德地图 Web 端 JS API Key（公开 key，可暴露给浏览器；配套的 jscode 安全密钥放在后端，通过 `/_AMapService` 代理注入）。 | 缺省时 `MapOverlay` 自动降级为纯文字地点列表，不影响主流程 |

`.env.local.example` 只内置了 `NEXT_PUBLIC_API_BASE`；如需地图，请自行在 `.env.local` 追加 `NEXT_PUBLIC_AMAP_KEY=<你的高德 Web Key>`。

## 目录结构

```
frontend/
├── app/                      # Next.js App Router
│   ├── layout.tsx            # 根布局（字体 + metadata）
│   ├── page.tsx              # 首页（渲染 HomeView）
│   ├── globals.css           # 全局样式 + Tailwind 指令
│   └── room/                 # 协作房间入口（/room?room_id=... 静态导出可用）
├── components/               # 29 个 React 组件（详见下文）
├── lib/                      # 状态、SSE/WS 客户端、类型、工具
│   ├── store.ts              # 主对话 store（useChatStore）
│   ├── store/                # 拆分后的 store 内部模块
│   │   ├── types.ts          #   状态/记录类型定义
│   │   ├── initial-state.ts  #   初始状态
│   │   ├── event-handlers.ts #   SSE 事件分发大 switch
│   │   └── arrival-counter.ts#   跨流到达计数
│   ├── collab-store.ts       # 协作房间 store（useCollabStore）
│   ├── sse.ts                # 手写 SSE 解析（streamSse）
│   ├── ws.ts                 # WebSocket 客户端 + 自动重连
│   ├── types.ts              # 与后端对齐的 TS 类型（手工维护）
│   ├── icon-map.tsx          # 工具/节点 → 图标映射
│   ├── utils.ts              # cn / API_BASE / cookie / session 等工具
│   └── *.test.ts             # vitest 单测
└── scripts/                  # Node 运维/联调脚本（.mjs）
```

## 核心模块

### 两个 Zustand store

- **`useChatStore`（`lib/store.ts`）** —— 主对话状态：消息流、意图、工具调用记录、重规划记录、思考、行程、叙述、Toast、planner 模式、当前用户、偏好画像等。`sendMessage` / `confirm` / `refine` 等 action 直接驱动 SSE 请求；store 实现拆分到 `lib/store/` 下（类型 / 初始状态 / 事件分发 / 到达计数）。
- **`useCollabStore`（`lib/collab-store.ts`）** —— 协作房间状态：成员、约束池、投票、锁定段、WS 连接。下行的 `planning_event` 会复用主 store 的 `handleEvent`，确保协作通道与单人通道渲染逻辑一致。

### 手写 SSE 解析（`lib/sse.ts`）

浏览器原生 `EventSource` 只支持 GET，无法携带 POST body 与自定义 header，因此这里用 `fetch` + `ReadableStream` 手写了 `streamSse`，负责：

- 按 SSE 规范以双换行（`\n\n` / `\r\n\r\n`）切分事件块，处理粘包与跨 chunk 的长 token；
- 解析 `event:` / `data:`（多行 data 自动 join），把 JSON 反序列化为 `SseEvent`；
- 首字节超时（默认 8s）与空闲超时（默认 60s）看门狗；
- HTTP 错误读取后端 `detail` 字段；错误原因映射为中文（见 `lib/utils.ts` 的 `STREAM_ERROR_LABEL`）。

### WebSocket 协作（`lib/ws.ts`）

`createWsClient` 从 `API_BASE` 派生 `ws://` / `wss://` 地址，连接 `/ws/{roomId}?user_id=&nickname=`，支持 25s 心跳保活、最多 3 次指数退避重连（1s / 2s / 4s），并把下行消息按 `type` 分发给协作 store。GitHub Pages 上会连到 `NEXT_PUBLIC_API_BASE` 对应的 FC 域名；本地默认连 `ws://localhost:8000/ws/...`。

### 高德地图（`components/MapOverlay.tsx`）

用 `@amap/amap-jsapi-loader` 动态加载高德 JS API 2.0，按行程节点绘制 Marker、调用 `AMap.Driving` 真实路线规划（失败回退到直连 Polyline）。安全密钥通过 `window._AMapSecurityConfig.serviceHost` 指向后端 `/_AMapService` 代理。无 key 或加载失败时整体降级为文字地点列表。

## 与后端的接口

API 基址来自 `lib/utils.ts` 的 `API_BASE`（取 `NEXT_PUBLIC_API_BASE`，默认 `http://localhost:8000`）。

| 用途 | 方法与路径 | 说明 |
| --- | --- | --- |
| 首轮对话 / 反馈重规划 | `POST /chat/turn`（SSE） | 首轮规划与反馈都走这条；后端据上下文判定是规划还是 feedback |
| 确认预约 | `POST /chat/confirm`（SSE） | 接续已有方案，追加预约 / 购票 / 加购 / 转发文案等事件 |
| 演示场景 | `GET /scenarios` | 首页快捷场景按钮数据 |
| 用户档案 | `GET /personas` | 用户切换器的多 persona 列表 |
| 偏好画像 | `GET /preferences/{userId}`、`POST /preferences/{userId}/reset` | 读取 / 清空当前用户记忆 |
| 协作建房 | `POST /room/create` | 返回 `room_id` 用于生成分享链接 |
| 协作实时通道 | `WS /ws/{roomId}` | 成员 / 约束 / 投票 / 规划事件回放 |
| 地图代理 | `GET /_AMapService/...` | 高德 REST 请求经后端注入 jscode 后转发 |

请求头：对话类接口带 `X-Planner-Mode`（`rule` / `llm`）与 `X-User-Id`（当前演示用户）。`lib/sse.ts` 仍保留对历史 `/chat/refine` 形态的兼容解析逻辑，但当前前端默认统一走 `/chat/turn`。

## 可用脚本

`package.json` 中的 npm scripts：

| 命令 | 作用 |
| --- | --- |
| `pnpm dev` | 启动开发服务器（先 `clean-next`） |
| `pnpm build` | 生产构建（先 `clean-next`；默认 `standalone`，`GITHUB_PAGES=true` 时静态导出） |
| `pnpm start` | 运行生产构建产物 |
| `pnpm lint` | `next lint`（ESLint） |
| `pnpm typecheck` | `tsc --noEmit` |
| `pnpm test` | `vitest run`（单测） |
| `pnpm test:watch` | vitest 监听模式 |
| `pnpm clean:next` | 手动强删 `.next/` |
| `pnpm verify:all` | 串行跑 lint → typecheck → vitest → build（任一失败退出码非 0） |

`scripts/` 下的 Node 脚本：

| 脚本 | 作用 |
| --- | --- |
| `scripts/clean-next.mjs` | 强删 `.next/`，处理 Windows EPERM/EBUSY，由 `predev`/`prebuild` 自动触发 |
| `scripts/verify-all.mjs` | `pnpm verify:all` 的实现，依次跑 4 项静态校验 |
| `scripts/verify-refine.mjs` | 需后端在线：端到端验证反馈重规划事件序列与 `X-Planner-Mode` 透传 |
| `scripts/pressure-test-scenarios.mjs` | 需后端在线：批量发送演示场景并断言 SSE 事件齐全 |

联调脚本用法（先启动后端）：

```bash
node scripts/verify-refine.mjs
node scripts/pressure-test-scenarios.mjs
```

## 测试

单测用 vitest，覆盖 `lib/` 层逻辑，共 3 个测试文件、约 34 个用例：

- `lib/sse.test.ts` —— SSE 解析鲁棒性（粘包、跨 chunk、CRLF、超时等，约 23 个用例）
- `lib/store.test.ts` —— 主 store 行为（约 8 个用例）
- `lib/collab-store.test.ts` —— 协作 store 行为（约 3 个用例）

```bash
pnpm test          # 跑一次
pnpm test:watch    # 监听模式
```

## 组件清单

`components/` 下共 29 个组件：

```
HomeView              页面骨架：顶栏 + 聊天 + 行程/链路双栏
ChatDock              对话输入区与消息流
QuickScenarios        首页演示场景快捷按钮
IntentSummary         实时意图摘要卡片
ToolTracePanel        工具调用链路可视化
ThoughtPanel          Agent 思考过程
DecisionTraceCard     决策依据卡片
ItineraryCard         行程时间轴卡片（确认 / 反馈 / 取消）
ItineraryUtilityBar   行程辅助操作条
RefinementDialog      反馈重规划弹窗
ComparisonView        新旧方案对比视图
MapOverlay            高德地图行程标注（无 key 时降级文字列表）
PosterGenerator       一键生成行程海报（modern-screenshot）
ShareModal            分享 / 建协作房弹窗
ConstraintFeed        协作约束流
VoteButtons           协作分段投票
CollabBar             协作房间成员 / 状态栏
PlannerModeBadge      rule / llm 模式切换
MockModeBadge         mock 模式标记
OfflineReadyBadge     离线就绪标记
UserSwitcher          多用户（persona）切换
PreferencesPanel      偏好画像 + 清空记忆
ChitchatBubble        闲聊 / 共情气泡 + 引导按钮
CommandPalette        命令面板（Cmd/Ctrl+K）
ToastStack            右下角浮层提示
Confetti              成功庆祝动效
ShimmerStripe         流式加载微光条
NumberTicker          数字滚动动画
TtsPlayer             文本转语音播放器
```

## 部署

当前公开 Demo 走 GitHub Pages：`.github/workflows/pages.yml` 在构建时注入 `GITHUB_PAGES=true`、`NEXT_PUBLIC_BASE_PATH=/shangwuju`，并从仓库变量 `NEXT_PUBLIC_API_BASE` 读取 FC 后端地址。`next.config.mjs` 自动切到 `output: "export"`，产物上传 `frontend/out`。

`Dockerfile` 仍保留给容器化部署使用，多阶段构建产出 Next.js standalone 镜像（Node 20 + pnpm 9.15.9，最终镜像约 < 100MB）。`NEXT_PUBLIC_*` 在 build 阶段通过 `--build-arg` 注入，切换部署环境需重新构建。容器默认监听 `3000` 端口。
