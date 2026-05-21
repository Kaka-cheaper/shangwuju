# Technical Design Document

## Overview

本设计文档描述「晌午局」前端体验创新功能集的技术实现方案。所有功能均为纯前端改动（后端零改动），基于现有 Zustand store + SSE 事件流 + ItineraryCard 组件体系进行增强和扩展。

**技术栈**：Next.js 14 App Router + TypeScript + Tailwind CSS + Zustand + html2canvas + Leaflet（可选）

**架构原则**：
- 不新建独立页面，所有功能嵌入现有 HomeView 布局
- 复用现有 store 数据流（thoughts / itinerary / replans），仅做最小扩展
- 每个功能独立组件，可单独开关，互不阻塞
- 降级优先：任何新功能失败不影响核心 Demo 闭环

## Architecture

### 系统层级

```
HomeView (布局容器)
├── ItineraryCard (已有，增强)
│   ├── [R1] Stagger Animation Logic (内部 state)
│   ├── [R3] ComparisonView (条件渲染)
│   ├── [R5] PosterGenerator (按钮 + Modal)
│   └── [R6] TtsPlayer (按钮 + 控制)
├── MapOverlay [R2] (ItineraryCard section 内)
├── ToolTracePanel (已有，不动)
└── ThoughtPanel [R4] (ToolTracePanel 下方)
```

### 数据流

```
后端 SSE → sse.ts 解析 → store.handleEvent() 分发
                                    ↓
              ┌─────────────────────┼─────────────────────┐
              ↓                     ↓                     ↓
    store.itinerary          store.thoughts         store.replans
    store.previousItinerary  (扩展 timestamp_ms)
              ↓                     ↓                     ↓
    ItineraryCard            ThoughtPanel           ThoughtPanel
    ComparisonView                                  (replan 分隔线)
    MapOverlay
    PosterGenerator
    TtsPlayer
```

### 组件通信

所有组件通过 Zustand store 订阅数据，无 props drilling，无跨组件直接通信。新增组件只读 store，不写 store（除 R3 的 previousItinerary 快照由 store.refine() 内部写入）。

## Components and Interfaces

### Component 1: Timeline Stagger Animation（R1）

**修改文件**：`frontend/components/ItineraryCard.tsx`

**接口**：无新 props，内部 state 驱动。

```typescript
// 新增内部 state
interface StaggerState {
  visibleCount: number;    // 当前可见的 stage 数量
  animating: boolean;      // 是否正在逐段动画中
}

// 新增内部方法
function skipAnimation(): void;  // 跳过动画，立即显示所有 stages
```

**方案**：使用 React state 控制逐段可见性（而非纯 CSS animation-delay），因为需要精确控制按钮 disabled 状态和跳过逻辑。

```typescript
const [visibleCount, setVisibleCount] = useState(0);
const [animating, setAnimating] = useState(false);
const animTimerRef = useRef<NodeJS.Timeout | null>(null);

useEffect(() => {
  if (!itinerary) { setVisibleCount(0); setAnimating(false); return; }
  const stages = itinerary.stages;
  if (stages.length === 0) { setVisibleCount(0); setAnimating(false); return; }
  
  setAnimating(true);
  setVisibleCount(0);
  const delay = stages.length <= 2 ? 200 : 400;
  let idx = 0;
  
  const tick = () => {
    idx++;
    setVisibleCount(idx);
    if (idx >= stages.length) {
      setAnimating(false);
    } else {
      animTimerRef.current = setTimeout(tick, delay);
    }
  };
  animTimerRef.current = setTimeout(tick, delay);
  return () => { if (animTimerRef.current) clearTimeout(animTimerRef.current); };
}, [itinerary]);
```

**渲染变更**：
- `<li>` 加条件渲染：`idx < visibleCount` 时显示（带 fade-in-up 动画），否则隐藏
- 按钮 disabled 条件从 `!canAct` 改为 `!canAct || animating`
- 时间轴区域顶部新增「跳过动画 ⏭」小按钮（仅 animating 时显示）

---

### Component 2: MapOverlay（R2）— 高德地图 JS API 2.0

**新建文件**：`frontend/components/MapOverlay.tsx`

**技术选型**：高德地图 JS API 2.0 + `@amap/amap-jsapi-loader`。高德是美团地图底座，代码调用方式与真实接入完全一致。

**加载方式**：

```typescript
// frontend/components/MapOverlay.tsx
"use client";
import { useEffect, useRef, useState } from "react";
import AMapLoader from "@amap/amap-jsapi-loader";
import { useChatStore } from "@/lib/store";
import { getCoordForId } from "@/lib/coord-map";

interface MapOverlayProps {
  visibleCount: number;  // 从 ItineraryCard 传入，配合 R1 stagger
}

export default function MapOverlay({ visibleCount }: MapOverlayProps) {
  const itinerary = useChatStore((s) => s.itinerary);
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstance = useRef<any>(null);
  const markersRef = useRef<any[]>([]);
  const polylinesRef = useRef<any[]>([]);
  const [loadError, setLoadError] = useState(false);

  // 初始化地图
  useEffect(() => {
    if (!mapRef.current || !itinerary) return;
    
    AMapLoader.load({
      key: process.env.NEXT_PUBLIC_AMAP_KEY || "",
      version: "2.0",
      plugins: ["AMap.Marker", "AMap.Polyline", "AMap.InfoWindow", "AMap.Driving"],
    }).then((AMap) => {
      const map = new AMap.Map(mapRef.current, {
        zoom: 13,
        center: [120.15, 30.25], // 杭州中心
        mapStyle: "amap://styles/dark", // 深色主题匹配 UI
      });
      mapInstance.current = map;
    }).catch(() => {
      setLoadError(true);
    });

    return () => {
      mapInstance.current?.destroy();
    };
  }, [itinerary]);

  // 逐段标注（配合 visibleCount）
  useEffect(() => {
    if (!mapInstance.current || !itinerary) return;
    const AMap = (window as any).AMap;
    if (!AMap) return;

    const stages = itinerary.stages;
    
    // 清除超出 visibleCount 的标注（处理跳过动画等场景）
    // 添加新可见的标注
    for (let i = markersRef.current.length; i < visibleCount && i < stages.length; i++) {
      const stage = stages[i];
      const id = stage.poi_id || stage.restaurant_id;
      const coord = getCoordForId(id, i);
      if (!coord) continue;

      // 创建标注
      const marker = new AMap.Marker({
        position: [coord.lng, coord.lat],
        content: buildMarkerContent(i + 1, stage.poi_id ? 'poi' : 'restaurant'),
        offset: new AMap.Pixel(-12, -12),
      });
      
      // InfoWindow（点击显示详情）
      const infoWindow = new AMap.InfoWindow({
        content: `<div style="padding:8px"><b>${stage.title}</b><br/>${stage.start}-${stage.end} · ${stage.kind}</div>`,
        offset: new AMap.Pixel(0, -20),
      });
      marker.on("click", () => infoWindow.open(mapInstance.current, marker.getPosition()));
      
      marker.setMap(mapInstance.current);
      markersRef.current.push(marker);

      // 画路线段（第 2 个点开始）
      if (i > 0) {
        const prevCoord = getCoordForId(
          stages[i-1].poi_id || stages[i-1].restaurant_id, i-1
        );
        if (prevCoord) {
          const polyline = new AMap.Polyline({
            path: [[prevCoord.lng, prevCoord.lat], [coord.lng, coord.lat]],
            strokeColor: "#fb923c",  // 暖橙色
            strokeWeight: 3,
            strokeStyle: "dashed",
            strokeOpacity: 0.8,
          });
          polyline.setMap(mapInstance.current);
          polylinesRef.current.push(polyline);
        }
      }
    }

    // 所有标注完成后 fitView
    if (visibleCount >= stages.length && markersRef.current.length > 0) {
      mapInstance.current.setFitView(markersRef.current, false, [60, 60, 60, 60]);
    }
  }, [visibleCount, itinerary]);

  // 降级：加载失败显示文字列表
  if (loadError) {
    return <FallbackLocationList itinerary={itinerary} />;
  }

  if (!itinerary) return null;

  return (
    <div className="card mt-3 overflow-hidden">
      <div ref={mapRef} style={{ width: "100%", height: "280px" }} />
    </div>
  );
}
```

**预留真实高德能力接口**：

```typescript
// 当前：Mock 直线连接
const polyline = new AMap.Polyline({ path: [from, to], ... });

// 未来切换为真实路线规划（只需替换这一段）：
// const driving = new AMap.Driving({ map: mapInstance.current });
// driving.search(from, to, (status, result) => { /* 自动画路线 */ });

// 当前：Mock 坐标查表
const coord = getCoordForId(poiId, stageIdx);

// 未来切换为真实地点搜索（只需替换数据源）：
// const placeSearch = new AMap.PlaceSearch({ city: "杭州" });
// placeSearch.search(keyword, (status, result) => { /* 拿真实经纬度 */ });
```

**坐标映射**（`frontend/lib/coord-map.ts`）：

```typescript
// 与 mock_data 中的 ID 对齐，坐标在杭州范围内
export const COORD_MAP: Record<string, { lat: number; lng: number }> = {
  // POI（部分来自 mock_data 已有坐标，部分补全）
  P001: { lat: 30.270, lng: 120.070 },  // 森林儿童探索乐园 - 西溪湿地北门
  P002: { lat: 30.265, lng: 120.080 },  // 西溪艺术展中心
  P003: { lat: 30.280, lng: 120.060 },  // 补全
  P004: { lat: 30.255, lng: 120.095 },
  P005: { lat: 30.260, lng: 120.105 },
  P006: { lat: 30.275, lng: 120.085 },
  // Restaurant（mock_data 中无坐标，全部补全）
  R001: { lat: 30.258, lng: 120.090 },  // 轻语沙拉 - 西溪银泰
  R002: { lat: 30.245, lng: 120.120 },  // 粤味轩 - 湖滨商圈
  R003: { lat: 30.262, lng: 120.075 },
  R004: { lat: 30.250, lng: 120.100 },
  R005: { lat: 30.268, lng: 120.065 },
  R006: { lat: 30.272, lng: 120.110 },
};

export function getCoordForId(
  id: string | null | undefined,
  stageIdx: number
): { lat: number; lng: number } | null {
  if (!id) return null;
  if (COORD_MAP[id]) return COORD_MAP[id];
  // 兜底：基于杭州西湖中心 + stage 索引偏移
  const base = { lat: 30.25, lng: 120.15 };
  const angle = (stageIdx * 60) * Math.PI / 180;
  const r = 0.015 + (stageIdx * 0.005);
  return { lat: base.lat + r * Math.sin(angle), lng: base.lng + r * Math.cos(angle) };
}
```

**降级策略**：高德 API 加载失败（断网/key 无效）→ `setLoadError(true)` → 渲染 `FallbackLocationList`（纯文字）。

**依赖**：`@amap/amap-jsapi-loader`（需 npm install）

**环境变量**：`.env.local` 新增 `NEXT_PUBLIC_AMAP_KEY=你的高德key`

---

### Component 3: ComparisonView（R3）

**新建文件**：`frontend/components/ComparisonView.tsx`

**接口**：

```typescript
interface ComparisonViewProps {
  oldItinerary: Itinerary;
  newItinerary: Itinerary;
  refinementNote?: string | null;
}

interface StageDiff {
  oldStage: ItineraryStage | null;
  newStage: ItineraryStage | null;
  changes: Array<'time' | 'title' | 'kind' | 'added' | 'removed'>;
}
```

**Store 扩展**（`frontend/lib/store.ts`）：

```typescript
// 新增字段
previousItinerary: Itinerary | null;

// refine() 方法修改
refine: async (feedbackText: string) => {
  const currentItinerary = get().itinerary;
  set({ previousItinerary: currentItinerary ? structuredClone(currentItinerary) : null });
  // ... 原有清空逻辑
},

// reset / startNewSession 时清空
set({ previousItinerary: null });
```

**Diff 算法**：按 stage 索引逐段对齐，逐字段比较 start/end/title/kind。

---

### Component 4: ThoughtPanel（R4）

**新建文件**：`frontend/components/ThoughtPanel.tsx`

**接口**：

```typescript
// 无 props，从 store 订阅
interface ThoughtTimelineItem {
  kind: 'thought' | 'replan';
  seq: number;
  text: string;
  timestamp_ms: number | null;
  reason?: string;  // replan 时有
}
```

**Store 修改**：

```typescript
// thoughts 类型扩展
thoughts: { seq: number; text: string; timestamp_ms: number | null }[];

// handleEvent 修改
case "agent_thought": {
  const p = ev.payload as unknown as AgentThoughtPayload;
  set((s) => ({
    thoughts: [...s.thoughts, { seq: ev.seq, text: p.text, timestamp_ms: ev.timestamp_ms ?? null }],
  }));
  break;
}
```

**时间线合并逻辑**：将 thoughts + replans 按 seq 排序合并为统一时间线，replan 项渲染为分隔线。

---

### Component 5: PosterGenerator（R5）

**新建文件**：`frontend/components/PosterGenerator.tsx`

**接口**：

```typescript
// 无 props，从 store 订阅 itinerary
interface PosterState {
  generating: boolean;
  previewUrl: string | null;  // Object URL
  error: string | null;
}
```

**实现策略**：
1. 渲染隐藏 DOM 海报模板（absolute, left: -9999px）
2. html2canvas 截图 → canvas → blob → Object URL
3. 预览 Modal + 下载按钮

**超时**：`Promise.race([html2canvas(node, { scale: 2 }), new Promise((_, rej) => setTimeout(() => rej('timeout'), 5000))])`

---

### Component 6: TtsPlayer（R6）

**新建文件**：`frontend/components/TtsPlayer.tsx`

**接口**：

```typescript
// 无 props，从 store 订阅 itinerary
type TtsStatus = 'idle' | 'playing' | 'paused';

// 摘要生成函数
function buildSpeechText(itinerary: Itinerary): string;
```

**实现**：纯 Web Speech API（`window.speechSynthesis`），无外部依赖。

---

## Data Models

### Model 1: Extended Thoughts Record

```typescript
// frontend/lib/store.ts - 修改现有 thoughts 类型
interface ThoughtRecord {
  seq: number;              // SSE 事件序号（单调递增）
  text: string;             // agent_thought payload.text
  timestamp_ms: number | null;  // SSE 事件 timestamp_ms（新增）
}
// store 字段：thoughts: ThoughtRecord[]
```

### Model 2: Previous Itinerary Snapshot

```typescript
// frontend/lib/store.ts - 新增字段
interface ChatState {
  // ... 现有字段
  previousItinerary: Itinerary | null;  // refine 前的行程快照
}
```

### Model 3: Coordinate Map

```typescript
// frontend/lib/coord-map.ts - 新建
// 与 mock_data 中的 POI/Restaurant ID 对齐，坐标在杭州范围内
export const COORD_MAP: Record<string, { lat: number; lng: number }> = {
  // POI（部分来自 mock_data 已有坐标，部分补全）
  P001: { lat: 30.270, lng: 120.070 },  // 森林儿童探索乐园
  P002: { lat: 30.265, lng: 120.080 },  // 西溪艺术展中心
  P003: { lat: 30.280, lng: 120.060 },
  P004: { lat: 30.255, lng: 120.095 },
  P005: { lat: 30.260, lng: 120.105 },
  P006: { lat: 30.275, lng: 120.085 },
  // Restaurant（mock_data 中无坐标，全部补全）
  R001: { lat: 30.258, lng: 120.090 },  // 轻语沙拉
  R002: { lat: 30.245, lng: 120.120 },  // 粤味轩
  R003: { lat: 30.262, lng: 120.075 },
  R004: { lat: 30.250, lng: 120.100 },
  R005: { lat: 30.268, lng: 120.065 },
  R006: { lat: 30.272, lng: 120.110 },
};

// 高德地图配置
export const AMAP_CONFIG = {
  key: process.env.NEXT_PUBLIC_AMAP_KEY || '',
  version: '2.0',
  plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.InfoWindow', 'AMap.Driving'],
};
```

### Model 4: Stage Diff Result

```typescript
// frontend/components/ComparisonView.tsx - 内部类型
interface StageDiff {
  oldStage: ItineraryStage | null;
  newStage: ItineraryStage | null;
  changes: Array<'time' | 'title' | 'kind' | 'added' | 'removed'>;
}
```

## Error Handling

### 降级策略矩阵

```
| 组件 | 失败场景 | 降级行为 |
|------|----------|----------|
| R1 Timeline | setTimeout 异常 | 立即显示所有 stages（等同跳过动画） |
| R2 MapOverlay | Leaflet 加载失败 / Canvas 不支持 | ErrorBoundary → 纯文字地点列表 |
| R3 ComparisonView | previousItinerary 为 null | 不渲染对比视图，仅显示当前方案 |
| R4 ThoughtPanel | thoughts 为空 | 不渲染（return null） |
| R5 PosterGenerator | html2canvas 超时/失败 | toast 错误 + 「复制文字版」降级按钮 |
| R6 TtsPlayer | speechSynthesis 不支持 | 不渲染按钮（静默隐藏） |
```

### 核心原则

- **任何新功能的失败都不能阻塞 Demo 主流程**（输入 → 规划 → 确认 → 执行）
- 所有新组件用 `try/catch` 或 `ErrorBoundary` 包裹
- 降级时推 toast 通知用户（R5），或静默隐藏（R6）

## Correctness Properties

### Property 1: Store Snapshot Consistency

`previousItinerary` 仅在 `refine()` 调用时被写入，且写入值是调用时刻 `itinerary` 的深拷贝（structuredClone）。reset/startNewSession 时清空为 null。

**Validates: Requirements 3.1**

### Property 2: Animation State Machine

`animating === true` 时 `visibleCount < stages.length`；`animating === false` 时 `visibleCount === stages.length`（或 itinerary 为 null 时为 0）。streaming 变为 false 时强制 setAnimating(false) 防止卡住。

**Validates: Requirements 1.2, 1.3**

### Property 3: Button Safety

`canAct` 为 true 的前提是 `!streaming && !animating && !hasOrders && !cancelled`。animating 期间所有操作按钮 disabled。

**Validates: Requirements 1.2, 1.3**

### Property 4: TTS State Machine

status 只能按 `idle → playing → paused → playing → idle` 或 `idle → playing → idle` 转换。onend/onerror 事件保证最终回到 idle。

**Validates: Requirements 6.4, 6.5, 6.6**

### Property 5: Thoughts Monotonic Append

thoughts 数组按 seq 严格递增追加，不会乱序或重复。每次 reset/startNewSession 清空为 []。

**Validates: Requirements 4.1**

## Testing Strategy

### 手动验收测试（Hackathon 优先）

由于是 Hackathon 项目（demo 模式），不强制 TDD。验收方式：

1. **R1**：发送一条规划请求，观察 stages 是否逐段出现（而非同时弹出）；点击跳过按钮验证立即显示
2. **R2**：规划完成后观察地图是否出现标注点和连线；hover 验证 tooltip
3. **R3**：先规划一次，再点击「说说哪不对」提交反馈，观察新旧方案对比是否出现
4. **R4**：规划过程中观察 ThoughtPanel 是否实时追加思考条目；展开/折叠验证
5. **R5**：点击「生成海报」，验证预览弹窗和下载功能
6. **R6**：点击「语音播报」，验证中文语音朗读和暂停/停止控制

### 降级验证

- 禁用 JavaScript 的 speechSynthesis → R6 按钮应不出现
- 断网后刷新 → R2 Leaflet 瓦片加载失败 → 应显示文字列表
- 首次规划（无 previousItinerary）→ R3 不应显示对比视图

## Dependencies

### 新增 npm 包

```
| 包名 | 版本 | 用途 | 大小(gzip) |
|------|------|------|------------|
| html2canvas | ^1.4.1 | R5 海报截图 | ~40KB |
| @amap/amap-jsapi-loader | ^1.0.1 | R2 高德地图 JS API 加载器 | ~3KB |
```

### 已有依赖（无需安装）

- zustand：状态管理
- tailwindcss：样式
- lucide-react：图标
- Web Speech API：浏览器原生

## File Changes Summary

```
| 操作 | 文件路径 | 对应需求 |
|------|----------|----------|
| 修改 | frontend/components/ItineraryCard.tsx | R1 stagger + R3 挂载点 + R5 按钮 + R6 按钮 |
| 修改 | frontend/lib/store.ts | R3 previousItinerary + R4 thoughts.timestamp_ms |
| 修改 | frontend/components/HomeView.tsx | R2 MapOverlay + R4 ThoughtPanel 挂载 |
| 新建 | frontend/components/MapOverlay.tsx | R2 |
| 新建 | frontend/components/ComparisonView.tsx | R3 |
| 新建 | frontend/components/ThoughtPanel.tsx | R4 |
| 新建 | frontend/components/PosterGenerator.tsx | R5 |
| 新建 | frontend/components/TtsPlayer.tsx | R6 |
| 新建 | frontend/lib/coord-map.ts | R2 坐标映射 |
```

## Implementation Priority

```
| 优先级 | 需求 | 理由 | 预估工时 |
|--------|------|------|----------|
| P0 | R1 时间轴动画 | 改动最小（仅 ItineraryCard），视觉冲击力最大 | 1h |
| P0 | R4 思考面板 | store 改动小，评委加分项（Agent 可见性） | 1.5h |
| P1 | R6 语音播报 | 零依赖，纯前端，5 分钟能跑通 | 0.5h |
| P1 | R5 行程海报 | 需装 html2canvas，但逻辑简单 | 2h |
| P2 | R3 对比视图 | 需要 store 扩展 + diff 算法，中等复杂度 | 2.5h |
| P3 | R2 地图联动 | 需装 leaflet + 坐标映射，最复杂但视觉效果好 | 3h |
```

**总预估**：~10.5h（1.5 个工作日）

**建议**：先做 P0（R1 + R4），Demo 立刻有质变；再做 P1（R6 + R5），多模态加分；最后 P2/P3 视时间决定。
