# Requirements Document

## Introduction

本需求文档定义「晌午局」前端体验创新功能集，涵盖两大方向：前端体验创新（行程时间轴动画增强、地图联动、对比视图、Agent 思考过程可视化）和多模态输出（行程海报生成、语音播报）。目标是在 Hackathon 评审中通过视觉冲击力和交互创新性获得加分，同时保持 Demo 闭环稳定。

**代码适配审查结论**（2026-05-21）：
- 现有 ItineraryCard 已有 `animate-fade-in-up` 逐段动画 + shimmer 骨架屏 → R1 是增强而非新建
- 现有 ToolTracePanel 已实现 Chain-of-Thought 可视化（按 Epic 分组） → R4 是独立补充面板
- Mock 数据中 POI 部分有坐标、Restaurant 全部无坐标 → R2 需要坐标补全策略
- Refine 流程会清空 itinerary 再重建 → R3 触发点是新 itinerary_ready 而非 refinement_done
- Store 中 thoughts 结构为 `{ seq, text }` 缺少 timestamp_ms → R4 需要 store 层改动
- 已有 ShareMessage 文字复制功能 → R5 是增量"图片版"

## Glossary

- **Timeline_Animator**: 对现有 ItineraryCard 时间轴的动画增强逻辑（stagger delay + 进入动画），不是独立新组件
- **Map_Overlay**: 地图联动组件，使用 Leaflet 或 Canvas 在行程卡片下方标注 POI/餐厅位置和路线
- **Comparison_View**: 对比视图组件，在 ItineraryCard 内部或旁边并排展示新旧方案
- **Thought_Panel**: Agent 思考过程可视化面板，与现有 ToolTracePanel 同级但独立，展示 agent_thought 事件的语义级决策过程
- **Poster_Generator**: 行程海报生成器，使用 html2canvas 将行程渲染为可分享图片，放在现有 ShareMessage 旁边
- **TTS_Player**: 语音播报组件，使用 Web Speech API 朗读行程摘要
- **SSE_Event**: 后端通过 Server-Sent Events 推送的结构化事件，前端通过 `frontend/lib/sse.ts` 的 fetch+ReadableStream 解析器消费
- **ItineraryStage**: 行程中的单个阶段对象，结构为 `{ kind, start, end, title, poi_id?, restaurant_id?, note? }`
- **Mock_Coordinate**: 用于地图展示的模拟经纬度坐标。POI 数据部分已有 `location.lat/lng`，Restaurant 数据无坐标需补全
- **useChatStore**: Zustand store（`frontend/lib/store.ts`），管理 SSE 事件分发后的所有前端状态
- **ToolTracePanel**: 现有组件（`frontend/components/ToolTracePanel.tsx`），按 Epic 分组展示 Tool 调用链路，标题为"Agent 思考链路"

## Requirements

### Requirement 1: 行程时间轴逐段生长动画（增强现有 ItineraryCard）

**User Story:** As a 评委/用户, I want 行程规划结果以逐段动画形式呈现（有明显的 stagger 延迟和进入效果）, so that 我能直观感受到 Agent 的规划过程而非一次性弹出静态结果。

**现有基线**：ItineraryCard 已有 `animate-fade-in-up` 类名在每个 stage `<li>` 上，但所有 stage 同时触发（无 stagger delay）。需要改为逐段延迟渲染。

#### Acceptance Criteria

1. WHEN itinerary 从 null 变为非 null（store.itinerary 更新）且 stages 数组包含 3 个及以上元素, THE Timeline_Animator SHALL 按 stages 数组索引为每个 `<li>` 设置递增的 animation-delay（每段间隔 400ms），使其逐段出现而非同时渲染
2. WHILE stages 正在逐段渲染中（最后一个 stage 的 delay 尚未触发）, THE ItineraryCard SHALL 将用户交互按钮（确认并预约/说说哪不对/取消方案）保持 disabled 状态不可点击
3. WHEN 所有 stages 的进入动画完成（最后一个 stage 的 delay + duration 结束）, THE ItineraryCard SHALL 将用户交互按钮切换为可用状态
4. IF 用户在动画进行中点击「跳过动画」按钮（新增于时间轴区域顶部）, THEN THE Timeline_Animator SHALL 立即移除所有 animation-delay 使剩余 stages 瞬间可见，并启用交互按钮
5. WHEN 行程仅包含 1 至 2 个 stages, THE Timeline_Animator SHALL 仍执行逐段动画但将间隔缩短至 200ms
6. IF itinerary.stages 为空数组, THEN THE ItineraryCard SHALL 显示空状态提示并直接启用反馈/取消按钮，不执行动画

**实现约束**：修改 `frontend/components/ItineraryCard.tsx` 的 timeline `<ol>` 渲染逻辑，使用 CSS `animation-delay` 或 `useState` + `setTimeout` 控制逐段可见性。不新建独立组件。

### Requirement 2: 地图联动标注（高德地图 JS API）

**User Story:** As a 评委/用户, I want 行程中的 POI 和餐厅在高德地图上实时标注并展示路线, so that 我能直观看到行程的空间分布和路线走向，且评委能看到该方案可直接接入美团生态。

**技术选型决策**：
- 使用**高德地图 JS API 2.0**（`@amap/amap-jsapi-loader`），不用 Leaflet/OpenStreetMap
- 原因：高德是美团地图底座，评委会看是否具备真实接入能力。用 Leaflet = "这是个 demo"，用高德 = "这个能直接接入"
- 高德 JS API 个人开发者免费额度（每日 30 万次）足够
- 当前数据源为 Mock 坐标，但代码调用方式与真实高德 API 完全一致，切换数据源即可上线

**数据现状**：
- `mock_data/_samples/poi.example.json` 中 POI 部分有 `location.lat/lng`（如 P001: lat=30.27, lng=120.07），部分只有 `location.name`
- `mock_data/_samples/restaurant.example.json` 中 Restaurant 全部只有 `location.name`，无坐标
- 需要在 Mock 数据层补全所有地点的坐标（统一杭州范围内）

**标注策略**：逐段标注（配合 R1 stagger 动画）
- 规划流程中，最终选定地点在 `itinerary_ready` 事件到达时才确定（stages 含 poi_id/restaurant_id）
- 配合 R1 的 visibleCount stagger，每个 stage 在时间轴上出现时，对应地图标注点同步亮起，路线逐段延伸

#### Acceptance Criteria

1. WHEN store.itinerary 从 null 变为非 null, THE Map_Overlay SHALL 使用高德地图 JS API 2.0（AMap.Map）在 ItineraryCard 下方渲染地图实例，中心点为行程地点的地理中心
2. THE Map_Overlay SHALL 配合 R1 的 visibleCount 逐段标注：当第 N 个 stage 在时间轴上可见时，对应的地图标注点（AMap.Marker）同步出现，并在第 N-1 和第 N 个标注点之间绘制路线段（AMap.Polyline）
3. THE Map_Overlay SHALL 使用不同图标区分地点类型：POI 使用蓝色标注（AMap.Marker + 自定义 icon），餐厅使用橙色标注，标注上显示序号
4. WHEN 用户点击某个地图标注点, THE Map_Overlay SHALL 使用 AMap.InfoWindow 显示该地点的名称、时间段（start-end）和类型（kind）
5. WHEN 所有 stages 标注完成, THE Map_Overlay SHALL 调用 map.setFitView() 自动调整视野使所有标注点可见
6. THE Map_Overlay SHALL 预留高德真实能力接口：路线规划（AMap.Driving/AMap.Walking）当前用 Polyline 直连模拟，切换为真实 API 只需替换一行调用；地点搜索（AMap.PlaceSearch）当前用 Mock 坐标，切换为真实 API 只需替换数据源
7. THE Map_Overlay SHALL 对 Mock 数据中缺少坐标的地点，在 Mock 数据层统一补全坐标（杭州西湖周边 30.20~30.30, 120.05~120.20 范围内），确保所有 poi_id/restaurant_id 都有对应经纬度
8. IF 高德地图 JS API 加载失败（网络问题/key 无效）, THEN THE Map_Overlay SHALL 降级为纯文字地点列表（序号 + 地点名称 + 时间段），不阻塞主流程
9. THE Map_Overlay SHALL 通过环境变量 `NEXT_PUBLIC_AMAP_KEY` 读取高德 API Key，不硬编码在代码中

**实现约束**：
- 新建 `frontend/components/MapOverlay.tsx`
- 使用 `@amap/amap-jsapi-loader` 加载高德 JS API（需 npm install）
- 在 `HomeView.tsx` 的 ItineraryCard section 内部渲染
- Mock 坐标数据统一放在 `frontend/lib/coord-map.ts`（与 mock_data 中的 ID 对齐）
- 高德 API Key 放 `.env.local`（已有 `.env.example` 模板）
- 组件接收 R1 的 visibleCount 作为 prop 或从共享 context 读取，实现逐段标注联动

### Requirement 3: Refine 前后对比视图

**User Story:** As a 评委/用户, I want 在反馈重规划后看到新旧方案的并排对比, so that 我能清楚了解 Agent 根据反馈做了哪些调整。

**流程适配**：
- 用户点击「说说哪不对」→ RefinementDialog → store.refine(feedbackText)
- store.refine 会 `set({ itinerary: null, toolCalls: [], replans: [], thoughts: [] })`（清空当前行程）
- 后端重新走 tool_call 序列 → 推送新的 itinerary_ready
- 所以对比视图的触发点是：**新 itinerary 到达时，且 store.lastRefinement 非空**（表示这是 refine 后的结果）
- 旧方案快照需要在 refine 清空前保存

#### Acceptance Criteria

1. WHEN store.refine() 被调用（用户提交反馈）, THE 前端 SHALL 在清空 itinerary 前将当前 itinerary 深拷贝保存为 `previousItinerary` 快照（新增 store 字段）
2. WHEN store.itinerary 从 null 变为非 null 且 store.lastRefinement 非空且 store.previousItinerary 非空, THE Comparison_View SHALL 在 ItineraryCard 上方或内部并排展示旧方案（左侧/上方）和新方案（右侧/下方）
3. THE Comparison_View SHALL 按 stage 索引顺序逐段对齐新旧方案，对每个 stage 逐字段比较（start/end 时间、title 地点名称、kind 类型），将值不同的字段使用视觉差异标记（如浅橙色背景）高亮显示
4. WHEN 新方案的 stages 数量与旧方案不同, THE Comparison_View SHALL 在缺失侧显示占位符标记（「已移除」标签标注旧方案多出的 stage，「新增」标签标注新方案多出的 stage）
5. WHEN 用户点击「收起对比」按钮, THE Comparison_View SHALL 折叠旧方案仅显示新方案，并将按钮文案切换为「展开对比」
6. IF store.previousItinerary 为 null（首次规划或 session 重置后）, THEN THE Comparison_View SHALL 不显示，仅展示当前方案
7. THE Comparison_View SHALL 复用现有 ItineraryCard 的 stage 渲染样式（时间轴竖线 + 时间点 + kind chip + title），避免重复造轮子

**实现约束**：
- store.ts 新增字段 `previousItinerary: Itinerary | null`
- store.refine() 方法中在 `set({ itinerary: null })` 前保存快照
- 新建 `frontend/components/ComparisonView.tsx`，在 ItineraryCard 内部条件渲染

### Requirement 4: Agent 思考过程可视化

**User Story:** As a 评委/用户, I want 看到 Agent 的思考过程以折叠面板形式呈现, so that 我能理解 Agent 的决策逻辑而不仅仅是 Tool 调用日志。

**现有基线**：
- `ToolTracePanel` 已展示 Tool 调用链路（按 Epic 分组），标题为"Agent 思考链路"
- store.thoughts 已存储 `{ seq, text }[]`，但**缺少 timestamp_ms**
- ChatDock peek 态已显示最近 thoughts（但只是简单文本列表）
- 需要一个独立的 Thought_Panel 组件，与 ToolTracePanel 并列，专注展示语义级决策过程

#### Acceptance Criteria

1. WHEN agent_thought 类型的 SSE_Event 到达, THE store handleEvent SHALL 将 `{ seq, text, timestamp_ms: ev.timestamp_ms }` 追加到 thoughts 数组（需修改 store.ts 的 thoughts 类型为 `{ seq: number; text: string; timestamp_ms: number | null }[]`）
2. THE Thought_Panel SHALL 默认以折叠状态展示，仅显示最新一条思考文本的前 50 个字符作为摘要（超出以"…"截断），并在摘要旁显示总条数徽标
3. WHEN 用户点击展开按钮, THE Thought_Panel SHALL 展开显示完整思考过程列表，按 seq 升序排列，每条显示完整文本与相对时间戳（如"3秒前"）
4. WHILE store.streaming === true, THE Thought_Panel SHALL 在最新条目旁显示脉冲圆点动画指示器
5. WHEN replan_triggered 事件到达（store.replans 新增条目）, THE Thought_Panel SHALL 在思考列表中插入一条视觉分隔线并标注「重新规划」文字及原因（取 replan.reason），区分前后两轮思考
6. THE Thought_Panel SHALL 在 HomeView 布局中与 ToolTracePanel 作为同级兄弟组件渲染（ToolTracePanel 下方），两者可分别独立折叠/展开
7. WHILE Thought_Panel 处于折叠状态, WHEN 新的 agent_thought 到达, THE Thought_Panel SHALL 保持折叠不自动展开，仅更新摘要文本和总条数
8. IF 当前会话 store.thoughts 为空数组, THEN THE Thought_Panel SHALL 不渲染（不显示空面板占位）

**实现约束**：
- 修改 `frontend/lib/store.ts` 的 thoughts 类型和 handleEvent 逻辑
- 新建 `frontend/components/ThoughtPanel.tsx`
- 在 `frontend/components/HomeView.tsx` 的中间栏（ConstraintFeed + ToolTracePanel 所在 section）追加 ThoughtPanel

### Requirement 5: 一键生成行程海报

**User Story:** As a 用户, I want 将行程生成为一张精美海报图片, so that 我能转发到微信群分享给同行人。

**现有基线**：ItineraryCard 已有 `ShareMessage` 组件（文字版转发文案 + 复制按钮）。海报生成是"图片版"增量功能，应放在 ShareMessage 旁边。

#### Acceptance Criteria

1. WHEN store.itinerary 非空, THE Poster_Generator SHALL 在 ItineraryCard 的 ShareMessage 区域旁边显示「生成海报」按钮
2. IF store.itinerary 为 null, THEN THE「生成海报」按钮 SHALL 不渲染
3. WHEN 用户点击「生成海报」按钮, THE Poster_Generator SHALL 使用 html2canvas 将行程信息渲染为一张竖版图片（宽 375px，2x 输出即 750px 物理宽度），包含：标题（itinerary.summary）、时间轴（各 stage 的 start-end + title）、总时长、底部「晌午局」品牌标识
4. WHEN 海报渲染在 5 秒内完成, THE Poster_Generator SHALL 显示预览弹窗（Modal）并提供「保存到本地」按钮触发浏览器下载（PNG 格式，文件名含日期）
5. IF 海报渲染超过 5 秒未完成, THEN THE Poster_Generator SHALL 中止渲染并显示 toast 错误提示，同时提供「重试」按钮
6. THE Poster_Generator SHALL 在海报中最多展示 8 个行程段，超出部分以「等 N 个地点」汇总；summary 最多 60 字符截断
7. IF html2canvas 库加载失败或 Canvas API 不可用, THEN THE Poster_Generator SHALL 显示 toast 错误提示并提供「复制文字版行程」降级按钮（复用现有 ShareMessage 的复制逻辑）
8. THE Poster_Generator SHALL 在海报底部包含「晌午局」品牌标识和 slogan（"半日出行管家"）

**实现约束**：
- 新建 `frontend/components/PosterGenerator.tsx`
- 在 ItineraryCard 的 ShareMessage 区域旁边或下方渲染
- 依赖：`html2canvas`（需 npm install）
- 海报模板用隐藏 DOM 节点渲染后截图，样式复用 Tailwind

### Requirement 6: 语音播报行程摘要

**User Story:** As a 用户, I want 听到语音播报行程摘要, so that 我能在不看屏幕的情况下了解行程安排。

#### Acceptance Criteria

1. WHEN store.itinerary 非空, THE TTS_Player SHALL 在 ItineraryCard 的操作按钮区域显示「语音播报」按钮（🔊 图标）
2. IF store.itinerary 为 null, THEN THE「语音播报」按钮 SHALL 不渲染
3. WHEN 用户点击「语音播报」按钮, THE TTS_Player SHALL 使用 Web Speech API（window.speechSynthesis）朗读行程摘要文本，文本格式为自然语句（如「下午两点出发，先去森林乐园玩一个半小时，然后去绿野鲜厨吃晚饭」），由 stages 的 start/title/kind 拼接生成，总长度不超过 500 字符
4. WHILE 语音正在播报中, THE TTS_Player SHALL 将按钮切换为播放状态（波形动画图标）并提供「暂停」和「停止」操作
5. WHEN 用户点击「暂停」, THE TTS_Player SHALL 调用 speechSynthesis.pause()；点击「继续」调用 speechSynthesis.resume()
6. WHEN 语音播报完毕（onend 事件）或用户点击「停止」（speechSynthesis.cancel()）, THE TTS_Player SHALL 恢复按钮为初始状态
7. IF 浏览器不支持 Web Speech API（typeof window.speechSynthesis === 'undefined'）, THEN THE TTS_Player SHALL 不渲染按钮（静默降级）
8. THE TTS_Player SHALL 使用中文语音（lang = 'zh-CN'），语速 rate = 1.0

**实现约束**：
- 新建 `frontend/components/TtsPlayer.tsx`
- 在 ItineraryCard 的操作按钮行（确认/反馈/取消 三按钮下方）渲染
- 纯前端实现，无后端改动
- 摘要文本生成逻辑：遍历 itinerary.stages，拼接为"[start] 去 [title]（[kind]）"格式
