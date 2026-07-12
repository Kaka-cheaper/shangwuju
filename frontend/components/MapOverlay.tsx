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
 *     （后端 assemble 时注入；home 首尾节点的 lat/lng 取自
 *     user_profile.home_location，参见 assemble_blueprint.py:394/513）
 *   - 旧 stages 模型已删除，不再读 itinerary.stages
 *   - 真接入美团 POI 时，POI 接口直接返坐标 → 数据形态不变
 *
 * 高德能力：
 *   - 标注（Marker）+ 真实路线规划（Driving）+ InfoWindow 详情
 *   - Driving 失败时 fallback 到直连 Polyline，保证 Demo 不挂
 *
 * 哪些节点会画 marker（ADR-0016 起）：
 *   - target_kind ∈ {poi, restaurant} 且坐标完整的节点 → 编号 marker（黄底数字）
 *   - target_kind === "home" 的首尾节点 → 家锚点 marker（墨色底 + 白色 Home
 *     图标，不占编号）。旧决策在此曾写「home 永远不画——它是抽象起终点」；
 *     ADR-0016 推翻的是这条渲染推论，不是「家是锚点不是站」的域概念本身——
 *     抽象锚点也可以可见，身份区分交给形态（独立 pin 样式），不靠「隐藏」。
 *     见 docs/adr/0016-map-home-anchor-visible.md。
 *
 * 路径连法：
 *   - 编号站之间：按 visible nodes 顺序两两相连，实线
 *   - 家↔首尾站（通勤壳）：与站间路线共用同一套实线高亮样式和
 *     Driving/fallback 降级逻辑，避免浅色底图上的黄色虚线不明显
 *   - hops 信息（如 hop.minutes）当前仅用于 InfoWindow 文案；路线渲染仍走 Driving / fallback
 *
 * 编舞（ADR-0016 决策 3）：
 *   - 家 pin：地图初始化即刻就位，不参与站点 stagger 悬念
 *   - 去程路线（家→首站）：随 1 号站一起画出
 *   - 返程路线（末站→家）：末站落定（markersRef 全量出齐）后才画，作为闭环的收束动作
 *
 * 降级：
 *   - 没 NEXT_PUBLIC_AMAP_KEY → 渲染文字列表
 *   - 高德 SDK 加载失败 → 渲染文字列表
 *   - 当前 node 无坐标 → 在文字列表中标注「位置待定」
 *   - home 节点缺坐标 → 静默不画家 pin、不画通勤路线（console.warn 留诊断），不崩
 */

import { useEffect, useRef, useState } from "react";
import { MapPin } from "lucide-react";

import { useChatStore } from "@/lib/store";
import type { ActivityNode, Itinerary } from "@/lib/types";
import { API_BASE, cn } from "@/lib/utils";

const AMAP_KEY = process.env.NEXT_PUBLIC_AMAP_KEY ?? "";
// 后端代理路径——高德 JS API 会把 restapi.amap.com 的请求改写成 ${serviceHost}/xxx
// 后端在 /_AMapService 注入 jscode 后转发，浏览器永远看不到 jscode
const AMAP_SERVICE_HOST = `${API_BASE}/_AMapService`;
const AMAP_VERSION = "2.0";

// 加载所需高德插件（含真实驾车路线规划 Driving）
const AMAP_PLUGINS = [
  "AMap.Marker",
  "AMap.Polyline",
  "AMap.InfoWindow",
  "AMap.Driving",
];

// 望京会场兜底（map 无标注点时的中心位置）——与 mock_data 望京数据集 demo_user
// 的 home 锚点（望京数字创意园B座2号楼）一致。坐标来源：高德 POI 官方词条
// 「望京数字创意园｜望京东路1号」= 116.484563, 40.006730（place/text API 实查，
// GCJ-02，与本地图同坐标系；无楼级词条，园区级即权威粒度）。mock_data 的
// distance_km/routes.json 均按此坐标 haversine 重算，三处同源。
const FALLBACK_CENTER: [number, number] = [116.484563, 40.00673]; // [lng, lat]

const MAP_MARKER_TONES = [
  {
    gradient: "linear-gradient(135deg, #3f6fb7 0%, #2f5f9f 100%)",
    glow: "rgba(63,111,183,0.24)",
  },
  {
    gradient: "linear-gradient(135deg, #d93b76 0%, #bd2d66 100%)",
    glow: "rgba(217,59,118,0.24)",
  },
  {
    gradient: "linear-gradient(135deg, #e49a2f 0%, #d97706 100%)",
    glow: "rgba(228,154,47,0.24)",
  },
  {
    gradient: "linear-gradient(135deg, #37a46f 0%, #23835b 100%)",
    glow: "rgba(55,164,111,0.22)",
  },
  {
    gradient: "linear-gradient(135deg, #7c5cc7 0%, #5f45a8 100%)",
    glow: "rgba(124,92,199,0.23)",
  },
];

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

/** 家锚点（home anchor）—— 独立于编号站之外的第三种坐标实体（ADR-0016）。 */
interface HomeAnchor {
  lat: number;
  lng: number;
  /** InfoWindow 展示名：优先取节点 address（画像 home_location.name），
   *  读不到再退到字面「家」——ADR-0016 决策 4，不硬编码具体地址文案。 */
  displayName: string;
}

/**
 * 解析 Itinerary 的 nodes：跳过 home，跳过缺坐标的节点，返回可画在地图上的 NodeWithCoord 列表。
 *
 * - target_kind === "home" 直接跳过 —— 家不占编号，走独立的 resolveHomeAnchor
 *   渲染路径（ADR-0016：家是锚点不是「第 0 站」，编号只属于中间站）
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

/**
 * 从 Itinerary 的首尾 home 节点解析出唯一的家锚点（ADR-0016）。
 *
 * - assemble_blueprint.py 固定在 nodes 首尾各插入一个 target_kind="home" 节点
 *   （出发 n0 / 回家 n_last），两者坐标恒相同（同一 user_profile.home_location）
 *   —— 只画一个 pin，不会叠两个。
 * - 缺 lat/lng（画像没配 home_location，或 mock 数据缺角）→ 返回 null，
 *   调用方据此静默降级：不画家 pin、不画通勤路线，仅 console.warn 留诊断
 *   （ADR-0016 决策 4：地图绝不能因此崩）。
 * - displayName 优先取 node.address（_resolve_target_meta 里 home 分支填的
 *   是 user_profile.home_location.name，即画像里的真实地名，如「望京数字创意
 *   园B座2号楼（用户家）」），读不到再退字面「家」——不硬编码具体地址文案。
 */
function resolveHomeAnchor(itinerary: Itinerary): HomeAnchor | null {
  const homeNode = itinerary.nodes.find((n) => n.target_kind === "home");
  if (!homeNode || homeNode.lat == null || homeNode.lng == null) {
    if (typeof window !== "undefined") {
      console.warn(
        "[MapOverlay] home 节点缺坐标，静默降级：不画家 pin / 通勤壳路线",
        homeNode
          ? { target_kind: homeNode.target_kind, target_id: homeNode.target_id }
          : "itinerary.nodes 中无 home 节点",
      );
    }
    return null;
  }
  return {
    lat: homeNode.lat,
    lng: homeNode.lng,
    displayName: homeNode.address || "家",
  };
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
   *   - home 节点不计入这里——它不参与编号站的 stagger 计数（ADR-0016 起，
   *     home 锚点走独立的编舞：地图初始化即刻就位，不受 visibleCount 影响）
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
  // ADR-0016：家锚点 pin + 两段通勤路线独立于编号站的 refs/生命周期——
  // 家 pin 即刻就位（不随 visibleCount 增减重建），通勤壳按「去程随 1 号站、
  // 返程收尾」各自的时机画一次，不参与站间 routeOverlaysRef 的整体清空重绘。
  const homeMarkerRef = useRef<any>(null);
  const outboundCommuteRef = useRef<any[]>([]);
  const returnCommuteRef = useRef<any[]>([]);
  const returnCommuteDrawnRef = useRef(false);

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
      // map.destroy() 已经把 home pin/通勤壳 overlay 一并销毁，这里只是重置
      // ref 本身，防止组件重挂载时误判「已经画过」而跳过重建
      homeMarkerRef.current = null;
      outboundCommuteRef.current = [];
      returnCommuteRef.current = [];
      returnCommuteDrawnRef.current = false;
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
      // 新方案 → 家 pin + 两段通勤路线也要清掉重来（旧方案的家坐标/路线不能沿用）
      if (homeMarkerRef.current) {
        try {
          homeMarkerRef.current.setMap(null);
        } catch {
          // 忽略
        }
        homeMarkerRef.current = null;
      }
      [...outboundCommuteRef.current, ...returnCommuteRef.current].forEach(
        (ov) => {
          try {
            ov.setMap?.(null);
            ov.clear?.();
          } catch {
            // 忽略
          }
        },
      );
      outboundCommuteRef.current = [];
      returnCommuteRef.current = [];
      returnCommuteDrawnRef.current = false;
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
    const homeAnchor = resolveHomeAnchor(itinerary);
    if (nodeCoords.length === 0) return;

    const targetCount =
      visibleCount === -1 ? nodeCoords.length : visibleCount;

    // ADR-0016 决策 3：家 pin 地图初始化即刻就位，不参与站点 stagger 悬念——
    // 只画一次（首次拿到 homeAnchor 时），不随 visibleCount 变化重建。
    // homeAnchor 为 null（home 缺坐标）→ 静默跳过，resolveHomeAnchor 内部
    // 已经 console.warn 过，这里不重复告警。
    if (homeAnchor && !homeMarkerRef.current) {
      const marker = new AMap.Marker({
        position: [homeAnchor.lng, homeAnchor.lat],
        content: buildHomeMarkerHtml(),
        offset: new AMap.Pixel(-20, -20),
        title: homeAnchor.displayName,
        zIndex: 200, // 高于编号 marker，闭环起点视觉上压得住
      });
      marker.setMap(map);

      const infoHtml = buildHomeInfoWindowHtml(homeAnchor);
      marker.on("click", () => {
        const infoWindow = new AMap.InfoWindow({
          content: infoHtml,
          offset: new AMap.Pixel(0, -28),
          closeWhenClickMap: true,
        });
        infoWindow.open(map, [homeAnchor.lng, homeAnchor.lat]);
      });

      homeMarkerRef.current = marker;
    }

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
        content: buildMarkerHtml(nc.visibleIdx),
        offset: new AMap.Pixel(-20, -20),
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

    // ADR-0016 决策 3：去程路线随 1 号站一起画出——只在「1 号站 marker 已上屏
    // 且还没画过去程」时画一次，复用 drawSegment 同一套 Driving/fallback
    // 降级逻辑，与站间路线共用同一套高亮实线样式。
    if (
      homeAnchor &&
      markersRef.current.length >= 1 &&
      outboundCommuteRef.current.length === 0
    ) {
      drawSegment(
        AMap,
        map,
        homeAnchor,
        nodeCoords[0],
        outboundCommuteRef.current,
        COMMUTE_ROUTE_STYLE,
      );
    }

    // ADR-0016 决策 3：返程路线在最后一站落定后才画——收束闭环的最后一笔。
    // 复用与「全部站点出齐 → setFitView」同一个判断（markersRef 长度 ===
    // nodeCoords.length），只画一次（returnCommuteDrawnRef 防重复）。
    const allStationsSettled =
      markersRef.current.length === nodeCoords.length &&
      markersRef.current.length > 0;
    if (
      homeAnchor &&
      allStationsSettled &&
      !returnCommuteDrawnRef.current
    ) {
      returnCommuteDrawnRef.current = true;
      drawSegment(
        AMap,
        map,
        nodeCoords[nodeCoords.length - 1],
        homeAnchor,
        returnCommuteRef.current,
        COMMUTE_ROUTE_STYLE,
      );
    }

    // 全部出来后 setFitView 自动调整视野（ADR-0016 决策 4：范围包含家）
    if (allStationsSettled) {
      try {
        const fitTargets = homeAnchor && homeMarkerRef.current
          ? [...markersRef.current, homeMarkerRef.current]
          : markersRef.current;
        map.setFitView(fitTargets, false, [40, 40, 40, 40]);
      } catch {
        // 忽略
      }
    }
  }, [mapReady, itinerary, visibleCount]);

  // ============================================================
  // 渲染
  // ============================================================

  if (!itinerary) return null;

  const fallbackReason = !AMAP_KEY
    ? "未配置高德 Web Key"
    : loadError
      ? `高德 SDK 加载失败：${loadError}`
      : "地图未加载";

  if (!AMAP_KEY || loadError) {
    return <FallbackList itinerary={itinerary} reason={fallbackReason} />;
  }

  return (
    <div className="card amap-map-shell relative isolate z-0 mt-3 overflow-hidden rounded-[28px]">
      <style>{`
        .amap-map-shell .amap-container,
        .amap-map-shell .amap-layers,
        .amap-map-shell .amap-maps {
          z-index: 0 !important;
        }
        .amap-map-shell .amap-logo {
          left: auto !important;
          right: 18px !important;
          bottom: 18px !important;
          z-index: 1 !important;
          pointer-events: none !important;
        }
        .amap-map-shell .amap-copyright {
          left: auto !important;
          right: 18px !important;
          bottom: 2px !important;
          text-align: right !important;
          z-index: 1 !important;
          pointer-events: none !important;
        }
      `}</style>
      <div className="px-5 py-3 border-b border-black/[0.06] flex items-center gap-2">
        <MapPin className="w-4 h-4 text-ink-500" strokeWidth={2} />
        <span className="text-base font-semibold text-ink-900 tracking-tight">
          行程地图
        </span>
        <span className="text-sm text-ink-500">
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
//
// 所有路线段（站间 + 家↔首尾站）共用同一套自定义 Polyline 样式：
//   - Driving 成功：从 result.routes[0].steps[].path 提取真实道路坐标后手动画线
//   - Driving 失败：退化为直连 Polyline，但仍套用同样的颜色、线宽和描边
// 这样既保留真实路径，又能让出发/回家两段不再是浅黄色虚线，视觉上和行程路线一致。
// ============================================================

/** 路线渲染样式：所有行程段共用，保证站间与家↔首尾站一致。 */
interface RouteStyle {
  /** true → 即使 Driving 成功也不用 AMap 自动渲染，手动提取路径画自定义样式 */
  forceCustomRender: boolean;
  strokeColor: string;
  strokeWeight: number;
  strokeStyle: "solid" | "dashed";
  strokeOpacity: number;
  arrowColor: string;
  haloColor: string;
  haloWeight: number;
  haloOpacity: number;
}

/** 统一路线样式：深蓝实线 + 白色描边，在浅色高德底图上更清楚。 */
const STATION_ROUTE_STYLE: RouteStyle = {
  forceCustomRender: true,
  strokeColor: "#2f5f9f",
  strokeWeight: 5,
  strokeStyle: "solid",
  strokeOpacity: 0.92,
  arrowColor: "#ffffff",
  haloColor: "#ffffff",
  haloWeight: 10,
  haloOpacity: 0.9,
};

/** 家↔首尾站与站间路线完全同款，单独常量保留调用语义。 */
const COMMUTE_ROUTE_STYLE: RouteStyle = STATION_ROUTE_STYLE;

/** from/to 的坐标形状：NodeWithCoord 与 HomeAnchor 共用（都只需要 lat/lng）。 */
interface LatLng {
  lat: number;
  lng: number;
}

function drawSegment(
  AMap: any,
  map: any,
  from: LatLng,
  to: LatLng,
  overlayBucket: any[],
  style: RouteStyle = STATION_ROUTE_STYLE,
): void {
  // Driving 实例不能复用（每次 search 会清空之前的路线）→ 每段用一个独立实例
  let driving: any;
  try {
    driving = new AMap.Driving({
      // forceCustomRender 时不传 map：不让 AMap 用默认实线样式自动渲染，
      // 由下面回调里手动提取 result 坐标、自己画 Polyline 套用 style
      map: style.forceCustomRender ? undefined : map,
      hideMarkers: true, // 我们自己有标注
      showTraffic: false,
      autoFitView: false,
      policy: AMap.DrivingPolicy?.LEAST_TIME ?? 0,
    });
  } catch (e) {
    console.warn("[MapOverlay] Driving 实例化失败，fallback 直连:", e);
    drawFallbackPolyline(AMap, map, from, to, overlayBucket, style);
    return;
  }

  overlayBucket.push(driving);

  driving.search(
    [from.lng, from.lat],
    [to.lng, to.lat],
    (status: string, result: any) => {
      if (status !== "complete") {
        // 路线规划失败（如距离过近 / API 限流）→ fallback 直连
        try {
          driving.clear?.();
        } catch {
          // 忽略
        }
        drawFallbackPolyline(AMap, map, from, to, overlayBucket, style);
        return;
      }
      if (!style.forceCustomRender) {
        // 兼容旧配置：AMap 已经把默认实线路线自动渲染到 map 上
        return;
      }
      // 自定义高亮路线：从 result.routes[0].steps[].path 拼出完整坐标序列，
      // 自己画 Polyline，保证每一段都用同一套描边实线样式。
      try {
        const route = result?.routes?.[0];
        const path: any[] = [];
        for (const step of route?.steps ?? []) {
          if (Array.isArray(step?.path)) path.push(...step.path);
        }
        if (path.length >= 2) {
          drawStyledPolyline(AMap, map, path, overlayBucket, style);
        } else {
          // 提取不到坐标点（result 形状异常）→ 退直连，保证路线仍然画出来
          drawFallbackPolyline(AMap, map, from, to, overlayBucket, style);
        }
      } catch (e) {
        console.warn("[MapOverlay] 自定义路线渲染失败，fallback 直连:", e);
        drawFallbackPolyline(AMap, map, from, to, overlayBucket, style);
      }
    },
  );
}

function drawFallbackPolyline(
  AMap: any,
  map: any,
  from: LatLng,
  to: LatLng,
  overlayBucket: any[],
  style: RouteStyle = STATION_ROUTE_STYLE,
): void {
  try {
    drawStyledPolyline(
      AMap,
      map,
      [
        [from.lng, from.lat],
        [to.lng, to.lat],
      ],
      overlayBucket,
      style,
    );
  } catch (e) {
    console.warn("[MapOverlay] fallback polyline 失败:", e);
  }
}

function drawStyledPolyline(
  AMap: any,
  map: any,
  path: any[],
  overlayBucket: any[],
  style: RouteStyle,
): void {
  const halo = new AMap.Polyline({
    path,
    strokeColor: style.haloColor,
    strokeWeight: style.haloWeight,
    strokeStyle: "solid",
    strokeOpacity: style.haloOpacity,
    lineJoin: "round",
    lineCap: "round",
    zIndex: 48,
  });
  halo.setMap(map);
  overlayBucket.push(halo);

  const polyline = new AMap.Polyline({
    path,
    strokeColor: style.strokeColor,
    strokeWeight: style.strokeWeight,
    strokeStyle: style.strokeStyle,
    strokeOpacity: style.strokeOpacity,
    showDir: true,
    dirColor: style.arrowColor,
    lineJoin: "round",
    lineCap: "round",
    zIndex: 52,
  });
  polyline.setMap(map);
  overlayBucket.push(polyline);
}

// ============================================================
// 自定义标注 HTML（高德 Marker 用 content 字符串）
// ============================================================

function buildMarkerHtml(index: number): string {
  const tone = MAP_MARKER_TONES[(Math.max(1, index) - 1) % MAP_MARKER_TONES.length];
  return `
    <div style="
      position: relative;
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: ${tone.gradient};
      border: 3px solid rgba(255,255,255,0.96);
      box-shadow: 0 12px 24px -16px rgba(15,23,42,0.58), 0 0 0 8px ${tone.glow};
      display: flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      font-size: 18px;
      line-height: 1;
      font-weight: 800;
      letter-spacing: 0;
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

/**
 * 家锚点 pin（ADR-0016 决策 1）：墨色底（ink-900 #111827，与信任带检索芯片
 * 同一套「锚点中性」配色哲学）+ 白色 Lucide Home 图标，不写字（图标自明）。
 *
 * SVG path 直接取自 lucide-react 的 House（Home 是它的别名，见
 * node_modules/lucide-react/dist/esm/icons/house.js），保证与全站其它 Lucide
 * 图标视觉一致——不是自己临摹一版近似图形。AMap Marker content 只接受 HTML
 * 字符串，React 组件用不了，所以在这里内联同一份 SVG path。
 *
 * 不复用 buildMarkerHtml：家 pin 是独立形态（方形圆角 vs 编号站的圆形、无
 * 数字、无编号轮转色），语义上是另一种 marker，参数化反而会让两者互相牵扯。
 */
function buildHomeMarkerHtml(): string {
  return `
    <div style="
      position: relative;
      width: 40px;
      height: 40px;
      border-radius: 14px;
      background: #111827;
      border: 3px solid rgba(255,255,255,0.96);
      box-shadow: 0 12px 24px -16px rgba(15,23,42,0.58), 0 0 0 8px rgba(17,24,39,0.16);
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      animation: amap-marker-pop 360ms cubic-bezier(0.34, 1.56, 0.64, 1);
    ">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8" />
        <path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      </svg>
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

/** 家锚点 InfoWindow：只显示画像家名，与站点 InfoWindow 同交互语言但更简（ADR-0016 决策 4）。 */
function buildHomeInfoWindowHtml(anchor: HomeAnchor): string {
  return `
    <div style="
      min-width: 160px;
      max-width: 240px;
      padding: 10px 12px;
      font-family: 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
    ">
      <div style="
        display: inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        background: rgba(17,24,39,0.08);
        color: #111827;
        font-size: 10px;
        font-weight: 600;
        margin-bottom: 6px;
      ">家</div>
      <div style="
        font-size: 14px;
        font-weight: 600;
        color: #1f1f1f;
        line-height: 1.4;
      ">${escapeHtml(anchor.displayName)}</div>
    </div>
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

function FallbackList({
  itinerary,
  reason,
}: {
  itinerary: Itinerary;
  reason: string;
}) {
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
    <div className="card mt-3 overflow-hidden rounded-[28px]">
      <div className="px-5 py-3 border-b border-black/[0.06] flex items-center gap-2">
        <MapPin className="w-4 h-4 text-ink-500" strokeWidth={2} />
        <span className="text-base font-semibold text-ink-900 tracking-tight">
          行程地点
        </span>
        <span className="text-sm text-ink-500">
          {reason}，仅显示列表
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

