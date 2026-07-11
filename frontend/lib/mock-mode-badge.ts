/**
 * MockModeBadge 的纯数据层 —— tooltip 文案拼装，不依赖 React（同
 * `lib/trust-belt.ts` 的既有先例：数据判定/文案拼装与渲染分层，可独立单测）。
 *
 * 背景（2026-07-12 P0 修复）：`components/MockModeBadge.tsx` 的 tooltip 曾硬编码
 * 具体数字（48 活动/45 餐厅/241 路线/174 评论），数据集换血（望京活集，
 * 95 POI/120 餐厅/215 路线/430 评论）后与 mock_data/ 实际条目数脱节——评委
 * hover 后翻目录对不上，弄巧成拙。改为组件运行时 fetch `GET /ready`
 * （`backend/api/health.py::ready` 的 `checks.mock_data` 已现成算好四个计数），
 * `buildMockDataTooltip` 只负责把计数渲染成人话，永不硬编码具体数字。
 */

export interface MockDataCounts {
  pois?: number;
  restaurants?: number;
  routes?: number;
  reviews?: number;
}

const FALLBACK_TOOLTIP =
  "接入 mock 数据集（活动地点 / 餐厅 / 路线 / 真实评论），全部经 Pydantic 严格校验。切换到真实 API 简便，业务代码无需改动。";

/**
 * 把 `/ready` 返回的计数拼成 tooltip 文案。
 *
 * `counts` 为 `null`（尚未 fetch 到 / fetch 失败）时返回不含具体数字的通用
 * 文案——宁可少信息也不显示猜测出来的假数据。缺失的单个维度（如后端某次
 * 只算出了 pois/restaurants，routes/reviews 字段缺失）会被跳过，不拼接
 * "undefined" 之类的坏文案。
 */
export function buildMockDataTooltip(counts: MockDataCounts | null): string {
  if (!counts) return FALLBACK_TOOLTIP;

  const parts: string[] = [];
  if (typeof counts.pois === "number") parts.push(`${counts.pois} 个活动地点`);
  if (typeof counts.restaurants === "number") parts.push(`${counts.restaurants} 家餐厅`);
  if (typeof counts.routes === "number") parts.push(`${counts.routes} 条路线`);

  if (parts.length === 0 && typeof counts.reviews !== "number") return FALLBACK_TOOLTIP;

  const scale = parts.length > 0 ? `接入 ${parts.join("、")}，` : "接入 mock 数据集，";
  const reviews = typeof counts.reviews === "number" ? `嵌入 ${counts.reviews} 条真实评论，` : "";
  return `${scale}${reviews}全部经 Pydantic 严格校验。切换到真实 API 简便，业务代码无需改动。`;
}
