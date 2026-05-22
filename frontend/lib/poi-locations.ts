/**
 * POI / 餐厅坐标字典（前端缓存）。
 *
 * 设计动机（spec frontend-experience-innovation R2）：
 *   - 后端 itinerary stage 只有 poi_id / restaurant_id
 *   - MapOverlay 需要 lat/lng 才能在高德地图上标注
 *   - 后端 GET /poi-locations 返完整字典，前端启动时拉一次缓存到 module-level 内存
 *
 * 不放在 Zustand store 是因为：
 *   - 这是不变数据（mock 数据 enrich 后写死了）
 *   - 不需要参与 React rerender 触发
 *   - 一次拉、永久缓存就够
 */

import { API_BASE } from "./utils";

export interface PoiLocationEntry {
  name: string;
  location_name: string;
  lat: number | null;
  lng: number | null;
}

export interface PoiLocationsResponse {
  pois: Record<string, PoiLocationEntry>;
  restaurants: Record<string, PoiLocationEntry>;
}

// Module-level 缓存（整个会话只拉一次）
let cache: PoiLocationsResponse | null = null;
let inflight: Promise<PoiLocationsResponse | null> | null = null;

/** 拉取 POI / 餐厅坐标字典；幂等 + 自动 in-flight 去重。 */
export async function loadPoiLocations(): Promise<PoiLocationsResponse | null> {
  if (cache) return cache;
  if (inflight) return inflight;

  inflight = (async () => {
    try {
      const r = await fetch(`${API_BASE}/poi-locations`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as PoiLocationsResponse;
      cache = data;
      return data;
    } catch (e) {
      console.warn("[poi-locations] 拉取失败：", e);
      return null;
    } finally {
      inflight = null;
    }
  })();

  return inflight;
}

/** 同步访问：仅在 cache 已就绪时返回，否则 null。 */
export function getCachedPoiLocations(): PoiLocationsResponse | null {
  return cache;
}

/**
 * 按 stage 找坐标。返回 null 表示没有坐标可用（前端应降级为文字列表）。
 *
 * 注：lat 在前；高德 API 用 [lng, lat]，但本函数遵循通用 GeoJSON 习惯返 [lat, lng]，
 * 调用方需要时自行调换。
 */
export function lookupCoord(
  stage: { poi_id?: string | null; restaurant_id?: string | null },
  data: PoiLocationsResponse | null = cache,
): { lat: number; lng: number; name: string } | null {
  if (!data) return null;

  const tryEntry = (
    entry: PoiLocationEntry | undefined,
  ): { lat: number; lng: number; name: string } | null => {
    if (!entry) return null;
    if (entry.lat == null || entry.lng == null) return null;
    return { lat: entry.lat, lng: entry.lng, name: entry.name };
  };

  if (stage.poi_id) {
    const hit = tryEntry(data.pois[stage.poi_id]);
    if (hit) return hit;
  }
  if (stage.restaurant_id) {
    const hit = tryEntry(data.restaurants[stage.restaurant_id]);
    if (hit) return hit;
  }
  return null;
}
