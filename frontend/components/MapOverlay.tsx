"use client";

/**
 * MapOverlay —— 高德地图行程标注（spec R8 / edge_v1）。
 *
 * 设计动机：
 *   - 评委要看「这个方案能直接接入美团生态」——用高德（不是 Leaflet）
 *   - 行程地点在地图上实时标注，配合 R1 stagger 动画逐段亮起
 *
 * 数据流（edge_v1 重构后，2026-05-XX）：
 *   - itinerary.nodes 已带 target_kind / target_id / lat / lng / address
 *     （后端 assemble 时注入；home 节点 lat/lng 可缺，但前端本来就不画 home）
 *   - 旧 stages 模型已删除，不再读 itinerary.stages
 *   - 真接入美团 POI 时，POI 接口直接返坐标 → 数据形态不变
 *
 * 高德能力：
 *   - 标注（Marker）+ 真实路线规划（Driving）+ InfoWindow 详情
 *   - Driving 失败时 fallback 到直连 Polyline，保证 Demo 不挂
 *
 * 哪些节点会画 marker：
 *   - 仅 target_kind ∈ {poi, restaurant} 且坐标完整的节点
 *   - home 节点（target_kind="home"）永远不画 —— 它是抽象起终点
 *
 * 路径连法：
 *   - 按 visible nodes 顺序两两相连
 *   - hops 信息（如 hop.minutes）当前仅用于 InfoWindow 文案；路线渲染仍走 Driving / fallback
 *
 * 降级：
 *   - 没 NEXT_PUBLIC_AMAP_KEY → 渲染文字列表
 *   - 高德 SDK 加载失败 → 渲染文字列表
 *   - 当前 node 无坐标 → 在文字列表中标注「位置待定」
 */

import { useEffect, useRef, useState } from "react";
import { MapPin } from "lucide-react";

import { useChatStore } from "@/lib/store";
import type { ActivityNode, Itinerary } from "@/lib/types";
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

/** 一个可渲染 marker 的节点（含坐标 + UI 元数据）。 */
interface NodeWithCoord {
  /** 在 visibleNodes（非 home）数组里的下标，1-based 用作 marker 编号 */
  visibleIdx: number;
  node: ActivityNode;
  lat: number;
  lng: number;
  type: "poi" | "restaurant";
  displayName: string;
}

/**
 * 解析 Itinerary 的 nodes：跳过 home，跳过缺坐标的节点，返回可画在地图上的 NodeWithCoord 列表。
 *
 * - target_kind === "home" 直接跳过（home 不画 marker，它是起终点抽象）
 * - target_kind ∈ {poi, restaurant} 但缺 lat/lng 跳过（容错）
 */
function buildNodeCoords(itinerary: Itinerary): NodeWithCoord[] {
  const out: NodeWithCoord[] = [];
  let visibleCounter = 0;
  itinerary.nodes.forEach((node) => {
    if (node.target_kind === "home") return;
    if (node.lat == null || node.lng == null) return;
    const type: "poi" | "restaurant" =
      node.target_kind === "restaurant" ? "restaurant" : "poi";
    visibleCounter += 1;
    out.push({
      visibleIdx: visibleCounter,
      node,
      lat: node.lat,
      lng: node.lng,
      type,
      displayName: node.address || node.title,
    });
  });

  // spec algorithm-redesign 收尾防御性增强（2026-05-24）：
  // 同坐标微扰——mock_data 多个店铺常共用同坐标（同 location.name 同 lat/lng），
  // 同坐标后画的 marker 会盖住先画的（截图复现：P026 KTV 与 R034 火锅店都标
  // 30.273/120.080，地图上只看到 marker 2）。
  // 给同组内第 2+ 个 marker 在屏幕上沿圆弧均匀分布微扰：
  // - 半径约 50m（0.00045 度纬度）
  // - 视觉上分得开，不动 itinerary.nodes 数据本身
  // 业界地图组件（Mapbox Cluster / Google Maps MarkerClusterer）的标准做法
  const RADIUS_DEG = 0.00045;
  const coordKey = (lat: number, lng: number) =>
    `${lat.toFixed(5)}_${lng.toFixed(5)}`;
  const coordGroups = new Map<string, NodeWithCoord[]>();
  for (const nc of out) {
    const key = coordKey(nc.lat, nc.lng);
    if (!coordGroups.has(key)) coordGroups.set(key, []);
    coordGroups.get(key)!.push(nc);
  }
  for (const [, group] of coordGroups) {
    if (group.length <= 1) continue;
    // group[0] 不动；group[1..N-1] 沿 360° 圆弧均匀分布（起始 -45° 让 2 号在右下方）
    const n = group.length - 1;
    for (let i = 1; i < group.length; i++) {
      const angle = (Math.PI * 2 * (i - 1)) / n - Math.PI / 4;
      group[i].lat = group[i].lat + RADIUS_DEG * Math.cos(angle);
      group[i].lng = group[i].lng + RADIUS_DEG * Math.sin(angle);
    }
  }

  // 临时诊断：地图 marker 不出来 → 看是不是所有 mid node 都没 lat/lng
  if (typeof window !== "undefined") {
    // eslint-disable-next-line no-console
    console.debug(
      "[MapOverlay] nodes 总数=",
      itinerary.nodes.length,
      "可见(非 home)节点=",
      out.length,
      "missing-coord-nodes=",
      itinerary.nodes
        .filter(
          (n) =>
            n.target_kind !== "home" && (n.lat == null || n.lng == null),
        )
        .map((n) => ({
          kind: n.kind,
          title: n.title,
          target_kind: n.target_kind,
          target_id: n.target_id,
        })),
    );
  }
  return out;
}

/** 从 "HH:MM" + 分钟数算结束时刻 "HH:MM"；解析失败兜底回原值。 */
function addMinutesToHHMM(hhmm: string, minutes: number): string {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!m) return hhmm;
  const total = Number(m[1]) * 60 + Number(m[2]) + minutes;
  if (!Number.isFinite(total) || total < 0) return hhmm;
  const hh = Math.floor(total / 60) % 24;
  const mm = total % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

// ============================================================
// 主组件
// ============================================================

interface MapOverlayProps {
  /**
   * 配合 R1 stagger 动画 —— 只展示前 N 个非 home 节点 marker。
   * -1 表示全部展示。
   *
   * 语义说明（edge_v1）：
   *   - ItineraryCard 传进来的 visibleCount 是「schedule visible entries 索引」
   *     （含 node 和 hop 的混合时间轴行号）
   *   - MapOverlay 这里把它直接当作「非 home 节点可显示数」上限——
   *     由于 schedule entries 数 ≈ 2× node 数，所以 markers 会早早全部显示完，
   *     再继续推进时间轴动画。这个偏差是可接受的 UX 取舍：
   *     地图先满亮、时间轴慢出，能让评委先看到「都去哪」再细品「啥时候」
   *   - home 节点本来就不参与可视化，所以 home 不计入这里
   */
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
  // 加载高德 SDK + 初始化地图（仅挂载一次，不随 itinerary 变化重建）
  // ============================================================
  useEffect(() => {
    if (!AMAP_KEY || !containerRef.current) return;

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
          mapStyle: "amap://styles/normal", // 浅色主题匹配 UI
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ============================================================
  // itinerary 变化时清空 markers/routes，让下一个 useEffect 重新增量加
  // ============================================================
  const lastItineraryRef = useRef<Itinerary | null>(null);
  useEffect(() => {
    if (!mapReady) return;
    if (lastItineraryRef.current !== itinerary) {
      // itinerary 引用变了 → 旧 marker/route 全清
      markersRef.current.forEach((m) => {
        try {
          m.setMap(null);
        } catch {
          // 忽略
        }
      });
      markersRef.current = [];
      routeOverlaysRef.current.forEach((ov) => {
        try {
          ov.setMap?.(null);
          ov.clear?.();
        } catch {
          // 忽略
        }
      });
      routeOverlaysRef.current = [];
      lastItineraryRef.current = itinerary;
    }
  }, [itinerary, mapReady]);

  // ============================================================
  // 配合 visibleCount 逐段标注 + 真实路线规划
  // ============================================================
  useEffect(() => {
    if (!mapReady || !mapRef.current || !AMapRef.current) return;
    if (!itinerary) return;

    const AMap = AMapRef.current;
    const map = mapRef.current;
    const nodeCoords = buildNodeCoords(itinerary);
    if (nodeCoords.length === 0) return;

    const targetCount =
      visibleCount === -1 ? nodeCoords.length : visibleCount;

    // 增量加 marker
    while (
      markersRef.current.length < targetCount &&
      markersRef.current.length < nodeCoords.length
    ) {
      const i = markersRef.current.length;
      const nc = nodeCoords[i];

      const marker = new AMap.Marker({
        position: [nc.lng, nc.lat],
        // marker 编号用 visibleIdx（1-based，对齐 ItineraryCard 时间轴的可见节点序号）
        content: buildMarkerHtml(nc.visibleIdx, nc.type),
        offset: new AMap.Pixel(-14, -14),
        title: nc.displayName,
      });
      marker.setMap(map);

      const infoHtml = buildInfoWindowHtml(nc);
      marker.on("click", () => {
        const infoWindow = new AMap.InfoWindow({
          content: infoHtml,
          offset: new AMap.Pixel(0, -28),
          closeWhenClickMap: true,
        });
        infoWindow.open(map, [nc.lng, nc.lat]);
      });

      markersRef.current.push(marker);
    }

    // 删除超出 targetCount 的标注（visibleCount 减少时）
    while (markersRef.current.length > targetCount) {
      const m = markersRef.current.pop();
      if (m) m.setMap(null);
    }

    // 重绘路线：清空旧路线，按当前已显示的 marker 顺序两两相连
    // 用 AMap.Driving 真实驾车路线；Driving 失败 fallback 到直连 Polyline
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
      const visibleNodes = nodeCoords.slice(0, markersRef.current.length);

      // 逐段调 AMap.Driving，失败则 fallback 到直连 Polyline
      visibleNodes.forEach((from, idx) => {
        if (idx === 0) return;
        const to = visibleNodes[idx];
        const prev = visibleNodes[idx - 1];
        // hop 元数据（如同地复用 path_type=in_place 时本来就 minutes=0，
        // Driving 仍可正常画一段最短路线，无需特殊跳过；
        // 这里只是为后续可能的视觉差异化预留）
        drawSegment(AMap, map, prev, to, routeOverlaysRef.current);
      });
    }

    // 全部出来后 setFitView 自动调整视野
    if (
      markersRef.current.length === nodeCoords.length &&
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
      <div className="px-4 py-2.5 border-b border-black/[0.06] flex items-center gap-1.5">
        <MapPin className="w-3.5 h-3.5 text-brand-600" strokeWidth={2} />
        <span className="text-sm font-semibold text-ink-900 tracking-tight">
          行程地图
        </span>
        <span className="text-xs text-ink-500">
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
  from: NodeWithCoord,
  to: NodeWithCoord,
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
  from: NodeWithCoord,
  to: NodeWithCoord,
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

function buildInfoWindowHtml(nc: NodeWithCoord): string {
  const { node, displayName, type } = nc;
  const typeLabel = type === "poi" ? "活动地点" : "餐厅";
  const typeBg =
    type === "poi"
      ? "background:rgba(96,165,250,0.1); color:#3b82f6;"
      : "background:rgba(251,146,60,0.1); color:#c2410c;";
  const endTime = addMinutesToHHMM(node.start_time, node.duration_min);
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
      ">${typeLabel} · ${escapeHtml(node.kind)}</div>
      <div style="
        font-size: 14px;
        font-weight: 600;
        color: #1f1f1f;
        line-height: 1.4;
        margin-bottom: 6px;
      ">${escapeHtml(node.title)}</div>
      <div style="
        font-size: 11px;
        color: #737373;
        font-family: ui-monospace, monospace;
      ">${node.start_time} - ${endTime}</div>
      ${
        displayName && displayName !== node.title
          ? `<div style="font-size:11px;color:#525252;margin-top:4px;">${escapeHtml(displayName)}</div>`
          : ""
      }
      ${
        node.note
          ? `<div style="
              margin-top: 6px;
              padding-top: 6px;
              border-top: 1px solid rgba(0,0,0,0.06);
              font-size: 11px;
              color: #525252;
              line-height: 1.5;
            ">${escapeHtml(node.note)}</div>`
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
  // 仅展示非 home 节点；home 节点不进列表
  const items = itinerary.nodes
    .filter((n) => n.target_kind !== "home")
    .map((node, idx) => {
      const hasCoord = node.lat != null && node.lng != null;
      const endTime = addMinutesToHHMM(node.start_time, node.duration_min);
      return {
        idx,
        title: node.address || node.title,
        timeRange: `${node.start_time}-${endTime}`,
        kind: node.kind,
        hasCoord,
      };
    });

  if (items.length === 0) return null;

  // 顺便提示有几条 hop（让评委看到「即使地图加载失败，也有通勤元数据」）
  const hopCount = itinerary.hops.filter((h) => h.path_type !== "in_place")
    .length;

  return (
    <div className="card mt-3">
      <div className="px-4 py-2.5 border-b border-black/[0.06] flex items-center gap-1.5">
        <MapPin className="w-3.5 h-3.5 text-ink-500" strokeWidth={2} />
        <span className="text-xs font-medium text-ink-900 tracking-tight">
          行程地点
        </span>
        <span className="text-xs text-ink-500">
          地图未加载，仅显示列表
          {hopCount > 0 ? ` · 共 ${hopCount} 段通勤` : ""}
        </span>
      </div>
      <ol className="px-3 py-2.5 space-y-1.5">
        {items.map((item) => (
          <li
            key={item.idx}
            className={cn(
              "flex items-start gap-2 px-2 py-1.5 rounded-md",
              "border border-black/[0.06] bg-black/[0.02]",
            )}
          >
            <span className="mono text-xs text-ink-500 mt-0.5 tabular-nums">
              {item.idx + 1}.
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-xs text-ink-800 font-medium tracking-tight">
                {item.title}
              </div>
              <div className="text-xs text-ink-500 mt-0.5 mono">
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

