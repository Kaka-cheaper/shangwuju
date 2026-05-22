"use client";

/**
 * MapOverlay —— 高德地图行程标注（spec R2）。
 *
 * 设计动机：
 *   - 评委要看「这个方案能直接接入美团生态」——用高德（不是 Leaflet）
 *   - 行程地点在地图上实时标注，配合 R1 stagger 动画逐段亮起
 *
 * 数据流（重构后，2026-05-22）：
 *   - itinerary.stages 已带 lat/lng/address（后端 assemble 时注入）
 *   - 不再调 /poi-locations 二次查询
 *   - 真接入美团 POI 时，POI 接口直接返坐标 → 数据形态不变
 *
 * 高德能力：
 *   - 标注（Marker）+ 真实路线规划（Driving）+ InfoWindow 详情
 *   - Driving 失败时 fallback 到直连 Polyline，保证 Demo 不挂
 *
 * 降级：
 *   - 没 NEXT_PUBLIC_AMAP_KEY → 渲染文字列表
 *   - 高德 SDK 加载失败 → 渲染文字列表
 *   - 当前 stage 无坐标 → 在文字列表中标注「位置待定」
 */

import { useEffect, useRef, useState } from "react";
import { MapPin } from "lucide-react";

import { useChatStore } from "@/lib/store";
import type { Itinerary, ItineraryStage } from "@/lib/types";
import { cn } from "@/lib/utils";

const AMAP_KEY = process.env.NEXT_PUBLIC_AMAP_KEY ?? "";
// 后端代理路径——高德 JS API 会把 restapi.amap.com 的请求改写成 ${serviceHost}/xxx
// 后端在 /_AMapService 注入 jscode 后转发，浏览器永远看不到 jscode
const AMAP_SERVICE_HOST = `${
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000"
}/_AMapService`;
const AMAP_VERSION = "2.0";

// 加载所需高德插件（含真实驾车路线规划 Driving）
const AMAP_PLUGINS = [
  "AMap.Marker",
  "AMap.Polyline",
  "AMap.InfoWindow",
  "AMap.Driving",
];

// 杭州市中心兜底（map 无标注点时的中心位置）
const FALLBACK_CENTER: [number, number] = [120.155, 30.255]; // [lng, lat]

// ============================================================
// 类型与工具
// ============================================================

interface StageWithCoord {
  idx: number;
  stage: ItineraryStage;
  lat: number;
  lng: number;
  type: "poi" | "restaurant";
  displayName: string;
}

function buildStageCoords(itinerary: Itinerary): StageWithCoord[] {
  const out: StageWithCoord[] = [];
  itinerary.stages.forEach((stage, idx) => {
    if (stage.lat == null || stage.lng == null) return;
    out.push({
      idx,
      stage,
      lat: stage.lat,
      lng: stage.lng,
      type: stage.poi_id ? "poi" : "restaurant",
      displayName: stage.address || stage.title,
    });
  });
  return out;
}

// ============================================================
// 主组件
// ============================================================

interface MapOverlayProps {
  /** 配合 R1 stagger 动画——只展示 idx < visibleCount 的标注。-1 表示全部展示。 */
  visibleCount?: number;
}

export default function MapOverlay({ visibleCount = -1 }: MapOverlayProps) {
  const itinerary = useChatStore((s) => s.itinerary);
  const containerRef = useRef<HTMLDivElement>(null);

  // 高德对象引用
  const mapRef = useRef<any>(null);
  const AMapRef = useRef<any>(null);
  const markersRef = useRef<any[]>([]);
  // routeOverlaysRef：存所有路线段（每段可能是 Driving 渲染的多 polyline 或 fallback 直连 polyline）
  const routeOverlaysRef = useRef<any[]>([]);

  // 状态
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mapReady, setMapReady] = useState(false);

  // ============================================================
  // 加载高德 SDK + 初始化地图
  // ============================================================
  useEffect(() => {
    if (!itinerary || !AMAP_KEY || !containerRef.current) return;

    let canceled = false;

    (async () => {
      try {
        // 高德 JS API 2.0 安全配置：走后端代理（serviceHost）注入 jscode
        // 浏览器永远看不到 jscode；只能看到 NEXT_PUBLIC_API_BASE 这个公开 URL
        // 见 https://lbs.amap.com/api/jsapi-v2/guide/abc/load
        if (typeof window !== "undefined") {
          (window as { _AMapSecurityConfig?: unknown })._AMapSecurityConfig = {
            serviceHost: AMAP_SERVICE_HOST,
          };
        }

        // 动态 import（SSR 不支持 window）
        const AMapLoaderModule = await import("@amap/amap-jsapi-loader");
        const AMapLoader = AMapLoaderModule.default;

        const AMap = await AMapLoader.load({
          key: AMAP_KEY,
          version: AMAP_VERSION,
          plugins: AMAP_PLUGINS,
        });

        if (canceled) return;
        AMapRef.current = AMap;

        const map = new AMap.Map(containerRef.current, {
          zoom: 12,
          center: FALLBACK_CENTER,
          mapStyle: "amap://styles/dark", // 深色主题匹配 UI
          viewMode: "2D",
          features: ["bg", "road", "building"], // 不要 POI 层（避免和我们的标注冲突）
        });
        mapRef.current = map;
        setMapReady(true);
      } catch (e) {
        if (canceled) return;
        const msg = e instanceof Error ? e.message : String(e);
        console.warn("[MapOverlay] 高德 SDK 加载失败:", msg);
        setLoadError(msg);
      }
    })();

    return () => {
      canceled = true;
      if (mapRef.current) {
        try {
          mapRef.current.destroy();
        } catch {
          // 忽略
        }
        mapRef.current = null;
      }
      markersRef.current = [];
      routeOverlaysRef.current = [];
      setMapReady(false);
    };
  }, [itinerary]);

  // ============================================================
  // 配合 visibleCount 逐段标注 + 真实路线规划
  // ============================================================
  useEffect(() => {
    if (!mapReady || !mapRef.current || !AMapRef.current) return;
    if (!itinerary) return;

    const AMap = AMapRef.current;
    const map = mapRef.current;
    const stageCoords = buildStageCoords(itinerary);
    if (stageCoords.length === 0) return;

    const targetCount =
      visibleCount === -1 ? stageCoords.length : visibleCount;

    // 增量加 marker
    while (
      markersRef.current.length < targetCount &&
      markersRef.current.length < stageCoords.length
    ) {
      const i = markersRef.current.length;
      const sc = stageCoords[i];

      const marker = new AMap.Marker({
        position: [sc.lng, sc.lat],
        content: buildMarkerHtml(i + 1, sc.type),
        offset: new AMap.Pixel(-14, -14),
        title: sc.displayName,
      });
      marker.setMap(map);

      const infoHtml = buildInfoWindowHtml(sc);
      marker.on("click", () => {
        const infoWindow = new AMap.InfoWindow({
          content: infoHtml,
          offset: new AMap.Pixel(0, -28),
          closeWhenClickMap: true,
        });
        infoWindow.open(map, [sc.lng, sc.lat]);
      });

      markersRef.current.push(marker);
    }

    // 删除超出 targetCount 的标注（visibleCount 减少时）
    while (markersRef.current.length > targetCount) {
      const m = markersRef.current.pop();
      if (m) m.setMap(null);
    }

    // 重绘路线：清空旧路线，按当前已显示的 marker 间用 AMap.Driving 真实驾车路线
    routeOverlaysRef.current.forEach((ov) => {
      try {
        ov.setMap?.(null);
        // Driving 的渲染对象有 clear 方法
        ov.clear?.();
      } catch {
        // 忽略
      }
    });
    routeOverlaysRef.current = [];

    if (markersRef.current.length >= 2) {
      const visibleStages = stageCoords.slice(0, markersRef.current.length);

      // 逐段调 AMap.Driving，失败则 fallback 到直连 Polyline
      visibleStages.forEach((from, idx) => {
        if (idx === 0) return;
        const to = visibleStages[idx];
        const prev = visibleStages[idx - 1];
        drawSegment(AMap, map, prev, to, routeOverlaysRef.current);
      });
    }

    // 全部出来后 setFitView 自动调整视野
    if (
      markersRef.current.length === stageCoords.length &&
      markersRef.current.length > 0
    ) {
      try {
        // 包含所有 marker，setFitView 会自动 zoom 到合适范围
        map.setFitView(markersRef.current, false, [40, 40, 40, 40]);
      } catch {
        // 忽略
      }
    }
  }, [mapReady, itinerary, visibleCount]);

  // ============================================================
  // 渲染
  // ============================================================

  if (!itinerary) return null;

  if (!AMAP_KEY || loadError) {
    return <FallbackList itinerary={itinerary} />;
  }

  return (
    <div className="card mt-3 overflow-hidden">
      <div className="px-4 py-2.5 border-b border-white/[0.06] flex items-center gap-1.5">
        <MapPin className="w-3.5 h-3.5 text-brand-400" strokeWidth={2} />
        <span className="text-[12px] font-medium text-ink-900 tracking-tight">
          行程地图
        </span>
        <span className="text-[10px] text-ink-500">
          高德地图 · 实时路径规划
        </span>
      </div>
      <div
        ref={containerRef}
        className="w-full"
        style={{ height: "320px" }}
        aria-label="行程地图"
      />
    </div>
  );
}

// ============================================================
// 路线渲染：优先 AMap.Driving 真实驾车路线；失败 fallback 直连 Polyline
// ============================================================

function drawSegment(
  AMap: any,
  map: any,
  from: StageWithCoord,
  to: StageWithCoord,
  overlayBucket: any[],
): void {
  // Driving 实例不能复用（每次 search 会清空之前的路线）→ 每段用一个独立实例
  let driving: any;
  try {
    driving = new AMap.Driving({
      map,
      hideMarkers: true, // 我们自己有标注
      showTraffic: false,
      autoFitView: false,
      policy: AMap.DrivingPolicy?.LEAST_TIME ?? 0,
    });
  } catch (e) {
    console.warn("[MapOverlay] Driving 实例化失败，fallback 直连:", e);
    drawFallbackPolyline(AMap, map, from, to, overlayBucket);
    return;
  }

  overlayBucket.push(driving);

  driving.search(
    [from.lng, from.lat],
    [to.lng, to.lat],
    (status: string, _result: any) => {
      if (status !== "complete") {
        // 路线规划失败（如距离过近 / API 限流）→ fallback 直连
        try {
          driving.clear?.();
        } catch {
          // 忽略
        }
        drawFallbackPolyline(AMap, map, from, to, overlayBucket);
      }
      // status === "complete" 时高德 SDK 自动渲染路线到 map 上
    },
  );
}

function drawFallbackPolyline(
  AMap: any,
  map: any,
  from: StageWithCoord,
  to: StageWithCoord,
  overlayBucket: any[],
): void {
  try {
    const polyline = new AMap.Polyline({
      path: [
        [from.lng, from.lat],
        [to.lng, to.lat],
      ],
      strokeColor: "#fb923c",
      strokeWeight: 3,
      strokeStyle: "dashed",
      strokeOpacity: 0.7,
      lineJoin: "round",
      lineCap: "round",
    });
    polyline.setMap(map);
    overlayBucket.push(polyline);
  } catch (e) {
    console.warn("[MapOverlay] fallback polyline 失败:", e);
  }
}

// ============================================================
// 自定义标注 HTML（高德 Marker 用 content 字符串）
// ============================================================

function buildMarkerHtml(index: number, type: "poi" | "restaurant"): string {
  const bg =
    type === "poi"
      ? "linear-gradient(135deg, #60a5fa, #3b82f6)" // 蓝色 POI
      : "linear-gradient(135deg, #fb923c, #ec4899)"; // 橙粉 餐厅
  const ring =
    type === "poi" ? "rgba(96,165,250,0.35)" : "rgba(251,146,60,0.35)";
  return `
    <div style="
      position: relative;
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: ${bg};
      box-shadow: 0 0 0 4px ${ring}, 0 4px 12px rgba(0,0,0,0.35);
      display: flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      animation: amap-marker-pop 360ms cubic-bezier(0.34, 1.56, 0.64, 1);
    ">
      ${index}
    </div>
    <style>
      @keyframes amap-marker-pop {
        0% { transform: scale(0); opacity: 0; }
        70% { transform: scale(1.15); opacity: 1; }
        100% { transform: scale(1); opacity: 1; }
      }
    </style>
  `;
}

function buildInfoWindowHtml(sc: StageWithCoord): string {
  const { stage, displayName, type } = sc;
  const typeLabel = type === "poi" ? "活动地点" : "餐厅";
  const typeBg =
    type === "poi"
      ? "background:rgba(96,165,250,0.1); color:#3b82f6;"
      : "background:rgba(251,146,60,0.1); color:#c2410c;";
  return `
    <div style="
      min-width: 200px;
      max-width: 260px;
      padding: 10px 12px;
      font-family: 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
    ">
      <div style="
        display: inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        ${typeBg}
        font-size: 10px;
        font-weight: 600;
        margin-bottom: 6px;
      ">${typeLabel} · ${stage.kind}</div>
      <div style="
        font-size: 14px;
        font-weight: 600;
        color: #1f1f1f;
        line-height: 1.4;
        margin-bottom: 6px;
      ">${escapeHtml(stage.title)}</div>
      <div style="
        font-size: 11px;
        color: #737373;
        font-family: ui-monospace, monospace;
      ">${stage.start} - ${stage.end}</div>
      ${
        displayName && displayName !== stage.title
          ? `<div style="font-size:11px;color:#525252;margin-top:4px;">${escapeHtml(displayName)}</div>`
          : ""
      }
      ${
        stage.note
          ? `<div style="
              margin-top: 6px;
              padding-top: 6px;
              border-top: 1px solid rgba(0,0,0,0.06);
              font-size: 11px;
              color: #525252;
              line-height: 1.5;
            ">${escapeHtml(stage.note)}</div>`
          : ""
      }
    </div>
  `;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ============================================================
// 降级：纯文字地点列表
// ============================================================

function FallbackList({ itinerary }: { itinerary: Itinerary }) {
  const items = itinerary.stages.map((stage, idx) => {
    const hasCoord = stage.lat != null && stage.lng != null;
    return {
      idx,
      title: stage.address || stage.title,
      timeRange: `${stage.start}-${stage.end}`,
      kind: stage.kind,
      hasCoord,
    };
  });

  if (items.length === 0) return null;

  return (
    <div className="card mt-3">
      <div className="px-4 py-2.5 border-b border-white/[0.06] flex items-center gap-1.5">
        <MapPin className="w-3.5 h-3.5 text-ink-500" strokeWidth={2} />
        <span className="text-[12px] font-medium text-ink-900 tracking-tight">
          行程地点
        </span>
        <span className="text-[10px] text-ink-500">地图未加载，仅显示列表</span>
      </div>
      <ol className="px-3 py-2.5 space-y-1.5">
        {items.map((item) => (
          <li
            key={item.idx}
            className={cn(
              "flex items-start gap-2 px-2 py-1.5 rounded-md",
              "border border-white/[0.06] bg-white/[0.02]",
            )}
          >
            <span className="mono text-[10px] text-ink-500 mt-0.5 tabular-nums">
              {item.idx + 1}.
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-[12px] text-ink-800 font-medium tracking-tight">
                {item.title}
              </div>
              <div className="text-[10px] text-ink-500 mt-0.5 mono">
                {item.timeRange} · {item.kind}
                {!item.hasCoord && (
                  <span className="text-amber-400 ml-1">· 位置待定</span>
                )}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
