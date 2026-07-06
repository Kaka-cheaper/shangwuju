"use client";

import { Fragment, type ReactNode } from "react";

import { Icons } from "@/lib/icon-map";
import type { NodeDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 节点卡事实展示（节点卡+行程轨-对比.html §① 单人卡布局）——`node_detail` 的
 * 唯一消费组件，Web（ItineraryCard.tsx）与移动端（MobileHomeView.tsx）共用同一份
 * 字段渲染/缺省判定逻辑。
 *
 * 2026-07 改版：原先的"右栏两栏面板"（NodeFactPanel variant="column"，把卡片撑高、
 * 右侧留白）被用户否掉，改成贴权威视觉稿的两段式：
 *   - `NodeHeadline`：评分 ★ + 人均，挂在店名行【右上角】（视觉稿 r1 .hf）；
 *   - 默认导出 `NodeFactPanel`：距离 · 可订 · 营业至 + tag，紧贴店名下方【横向一行】
 *     （视觉稿 r2），Web/移动端共用同一个横排 shape，不再有"column"/"row" 两个变体
 *     ——移动端本来就是这个横排形态，Web 端现在也改成同款，两端组件签名统一。
 *
 * 诚实红线（不变）：只显 `node_detail` 给的真实值——字段缺失（后端 `exclude_none`
 * 已省略）时该位不渲染，绝不补占位符/绝不美化"需排队"/"约满"这类如实告知的负面
 * 可用性文案。
 */

/**
 * 店名行右上角 headline：评分（★ 深金 accent-600，唯一暖色触点）+ 人均（ink-700
 * 半粗）。两者都缺时返回 null（不占位）。用的是纯文本 "★" 字符（视觉稿 r1 .star
 * 原样是文字字符，不是 emoji ⭐——emoji 星会带来"塑料感"色块，文字字符可以用
 * CSS 上色、和整体线性图标语言一致）。
 */
export function NodeHeadline({
  detail,
  className,
}: {
  detail: NodeDetail | undefined | null;
  className?: string;
}) {
  if (!detail) return null;
  const hasRating = detail.rating != null;
  const hasPrice = !!detail.price_text;
  if (!hasRating && !hasPrice) return null;

  return (
    <div
      className={cn(
        "flex shrink-0 items-center gap-2 whitespace-nowrap pt-0.5 text-[13px]",
        className,
      )}
    >
      {hasRating && (
        <span className="font-bold text-accent-600">★ {detail.rating!.toFixed(1)}</span>
      )}
      {hasPrice && <span className="font-semibold text-ink-700">{detail.price_text}</span>}
    </div>
  );
}

/**
 * 事实行：距离 · 可订 · 营业至 + 0-2 个精选 tag，横向一行、幽灰微字（评分/人均已被
 * `NodeHeadline` 拿走，这里不再重复）。Web/移动端共用同一个 shape。
 */
export default function NodeFactPanel({
  detail,
  className,
}: {
  detail: NodeDetail | undefined | null;
  className?: string;
}) {
  if (!detail) return null;

  const tags = detail.tags ?? [];
  const facts: { key: string; node: ReactNode }[] = [];

  if (detail.distance_km != null) {
    facts.push({
      key: "distance",
      node: (
        <span className="inline-flex items-center gap-1">
          <Icons.footprints className="h-3 w-3 shrink-0" strokeWidth={1.7} />
          {detail.distance_km.toFixed(1)}km
        </span>
      ),
    });
  }
  if (detail.availability_text) {
    facts.push({ key: "availability", node: <span>{detail.availability_text}</span> });
  }
  if (detail.open_until_text) {
    facts.push({ key: "open", node: <span>{detail.open_until_text}</span> });
  }

  if (facts.length === 0 && tags.length === 0) return null;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12px] leading-snug text-ink-500",
        className,
      )}
    >
      {facts.map((f, i) => (
        <Fragment key={f.key}>
          {i > 0 && (
            <span className="text-ink-300" aria-hidden>
              ·
            </span>
          )}
          {f.node}
        </Fragment>
      ))}
      {tags.map((t) => (
        <span
          key={t}
          className="rounded bg-black/[0.03] px-1.5 py-[1px] text-[10.5px] font-medium leading-[1.4] text-ink-600"
        >
          {t}
        </span>
      ))}
    </div>
  );
}
