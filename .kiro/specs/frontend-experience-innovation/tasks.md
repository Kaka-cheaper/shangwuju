# Implementation Plan: Frontend Experience Innovation

## Overview

本计划实现「晌午局」前端体验创新功能集（6 个需求），按优先级从高到低排列。所有任务均为纯前端改动，后端零改动。预估总工时 ~10.5h。

## Tasks

- [ ] 1. [R4] 修改 store.ts thoughts 类型，补存 timestamp_ms：修改 `frontend/lib/store.ts` 中 thoughts 字段类型从 `{ seq: number; text: string }[]` 改为 `{ seq: number; text: string; timestamp_ms: number | null }[]`，修改 handleEvent 中 agent_thought 分支追加 `timestamp_ms: ev.timestamp_ms ?? null`，确认 ChatDock 引用不受影响
- [ ] 2. [R3] Store 新增 previousItinerary 字段：在 ChatState interface 新增 `previousItinerary: Itinerary | null`，initialState 设为 null，refine() 方法中清空 itinerary 前用 structuredClone 保存快照，reset/startNewSession 时清空
- [ ] 3. [R1] ItineraryCard 时间轴 stagger 动画：新增 visibleCount/animating state + stagger timer（400ms 间隔，≤2 stages 时 200ms），修改 `<li>` 渲染为 idx < visibleCount 时可见，按钮 disabled 加 animating 条件，新增「跳过动画」按钮，streaming=false 时强制 setAnimating(false)
- [ ] 4. [R4] 新建 ThoughtPanel 组件：新建 `frontend/components/ThoughtPanel.tsx`，从 store 订阅 thoughts/replans/streaming，实现折叠态（摘要+badge）和展开态（完整列表+相对时间戳），replan 渲染为分隔线，streaming 时脉冲动画，在 HomeView 中间栏 ToolTracePanel 下方挂载
- [ ] 5. [R6] 新建 TtsPlayer 组件：新建 `frontend/components/TtsPlayer.tsx`，实现 buildSpeechText 拼接自然语句，Web Speech API play/pause/resume/stop 控制，三态 UI，浏览器不支持时 return null，在 ItineraryCard 操作按钮下方挂载
- [ ] 6. [R5] 新建 PosterGenerator 组件：npm install html2canvas，新建 `frontend/components/PosterGenerator.tsx`，实现隐藏 DOM 海报模板 + html2canvas 截图 + 5 秒超时 + 预览 Modal + 下载按钮 + 降级复制文字版，在 ItineraryCard ShareMessage 旁新增按钮
- [ ] 7. [R3] 新建 ComparisonView 组件：新建 `frontend/components/ComparisonView.tsx`，实现 diffStages 按索引对齐比较，并排布局（旧左新右）+ 变化高亮 + added/removed 标记 + 折叠切换，在 ItineraryCard 内条件渲染（itinerary && previousItinerary && lastRefinement）
- [ ] 8. [R2] 新建坐标映射 + MapOverlay 组件（高德地图）：npm install @amap/amap-jsapi-loader，新建 `frontend/lib/coord-map.ts` 硬编码坐标 + getCoordForId 兜底函数 + AMAP_CONFIG，新建 `frontend/components/MapOverlay.tsx` 使用高德 JS API 2.0（AMap.Map + AMap.Marker + AMap.Polyline + AMap.InfoWindow），接收 visibleCount prop 实现逐段标注联动，深色地图主题，预留 AMap.Driving 路线规划接口（当前用 Polyline 直连），加载失败降级为文字列表，.env.local 新增 NEXT_PUBLIC_AMAP_KEY，在 HomeView ItineraryCard section 下方挂载

## Task Dependency Graph

```json
{
  "waves": [
    [1, 2, 3, 5, 6, 8],
    [4, 7]
  ]
}
```

说明：
- Wave 1：Task 1（store thoughts）、Task 2（store previousItinerary）、Task 3（stagger 动画）、Task 5（TtsPlayer）、Task 6（PosterGenerator）、Task 8（MapOverlay）可并行
- Wave 2：Task 4（ThoughtPanel，依赖 Task 1）、Task 7（ComparisonView，依赖 Task 2）

## Notes

- 所有任务均为纯前端改动，后端零改动
- 推荐实施顺序：Task 1 → Task 3 → Task 4 → Task 5 → Task 6 → Task 2 → Task 7 → Task 8
- Task 3（R1 动画）和 Task 4（R4 思考面板）是 P0 优先级，Demo 质变最大
- Task 8（R2 地图）最复杂但非必须，时间不够可跳过
- 每个新组件都有降级方案，失败不影响核心 Demo 闭环
