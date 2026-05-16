import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind class 合并工具，shadcn 习惯。 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** 后端基址。开发期默认 http://localhost:8000。 */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

/** 生成一个 demo 级 session_id（约定见 api_contract.md §5）。 */
export function generateSessionId(): string {
  const d = new Date();
  const yyyymmdd =
    `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(
      d.getDate(),
    ).padStart(2, "0")}`;
  const rand = Math.random().toString(36).slice(2, 8);
  return `sess_${yyyymmdd}_${rand}`;
}

/** 失败原因的中文显示。 */
export const FAILURE_REASON_LABEL: Record<string, string> = {
  restaurant_full: "餐厅已满",
  ticket_sold_out: "门票售罄",
  distance_exceeded: "距离超限",
  duration_exceeded: "总时长超限",
  not_found: "未找到资源",
  empty_candidates: "候选为空",
  invalid_input: "参数校验失败",
  upstream_failure: "上游服务失败",
};

/** SSE 流错误原因的中文显示（来自 lib/sse.ts SseStreamError.reason）。 */
export const STREAM_ERROR_LABEL: Record<string, string> = {
  network: "网络错误",
  http: "服务端响应异常",
  no_body: "服务端未返回数据流",
  stream: "数据流异常中断",
  timeout_first_event: "后端无响应（首字节超时）",
  idle_timeout: "数据流长时间无新事件（疑似断流）",
  parse: "解析失败",
};

export function formatStreamError(reason: string, detail?: string): string {
  const label = STREAM_ERROR_LABEL[reason] ?? reason;
  return detail ? `${label}：${detail}` : label;
}

/** 工具名 → 中文标签（评委可读）。 */
export const TOOL_LABEL: Record<string, string> = {
  get_user_profile: "读取用户画像",
  search_pois: "查询活动地点",
  search_restaurants: "查询餐厅",
  check_restaurant_availability: "核对餐厅座位",
  estimate_route_time: "估算路线时间",
  reserve_restaurant: "预约餐厅",
  buy_ticket: "购买门票",
  generate_share_message: "生成转发文案",
  order_extra_service: "加购附加服务",
};


// ============================================================
// PlannerMode cookie 读写（C4 切换器用）
// ============================================================

import type { PlannerMode } from "./types";

const PLANNER_MODE_COOKIE = "shangwuju_planner_mode";

/** 客户端读 cookie；SSR 期间总是返 undefined。 */
export function getPlannerModeFromCookie(): PlannerMode | undefined {
  if (typeof document === "undefined") return undefined;
  const m = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${PLANNER_MODE_COOKIE}=([^;]+)`),
  );
  if (!m) return undefined;
  const v = decodeURIComponent(m[1]).trim().toLowerCase();
  return v === "rule" || v === "llm" ? (v as PlannerMode) : undefined;
}

/** 写 cookie，1 年过期。SameSite=Lax 满足同源跨端口（3000→8000 走 CORS）。 */
export function setPlannerModeCookie(mode: PlannerMode): void {
  if (typeof document === "undefined") return;
  const oneYear = 60 * 60 * 24 * 365;
  document.cookie = `${PLANNER_MODE_COOKIE}=${mode}; Max-Age=${oneYear}; Path=/; SameSite=Lax`;
}


// ============================================================
// Phase 0.7：user_id cookie（评委演示时切换 user 持久化）
// ============================================================

const USER_ID_COOKIE = "shangwuju_user_id";

export function getUserIdFromCookie(): string | undefined {
  if (typeof document === "undefined") return undefined;
  const m = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${USER_ID_COOKIE}=([^;]+)`),
  );
  if (!m) return undefined;
  const v = decodeURIComponent(m[1]).trim();
  return v || undefined;
}

export function setUserIdCookie(userId: string): void {
  if (typeof document === "undefined") return;
  const oneYear = 60 * 60 * 24 * 365;
  document.cookie = `${USER_ID_COOKIE}=${encodeURIComponent(userId)}; Max-Age=${oneYear}; Path=/; SameSite=Lax`;
}
