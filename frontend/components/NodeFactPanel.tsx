"use client";

import { Fragment, type ReactNode } from "react";

import type { NodeDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 安静事实面板（路演PPT/卡片主角化与事实面板设计终稿.md §三）——`node_detail`
 * 的唯一消费组件，Web（ItineraryCard.tsx 右栏两栏）与移动端
 * （MobileHomeView.tsx 店名下一行 chips）共用同一份字段渲染 / 缺省判定逻辑，
 * 只是外层排布（variant）不同：
 *   - "column"：Web 节点卡右栏（~140px），事实纵向堆叠，靠右对齐；
 *   - "row"：移动端窄屏，事实横向一行、自动换行（不强上两栏）。
 *
 * 诚实红线（§3.4/§4"功能内部"自检）：只显 `node_detail` 给的真实值——字段
 * 缺失（后端 `exclude_none` 已省略）时该位不渲染，绝不补占位符/绝不美化
 * "需排队"/"约满"这类如实告知的负面可用性文案。
 *
 * 克制参数（§3.4）：事实文字幽灰微字（text-ink-500，~11-12px）；评分星是
 * 全面板唯一暖色触点（深金 accent-600）；tag 是极小中性 chip
 * （bg-black/[0.03] text-ink-600）——面板整体安静是设计而非省事：不管方案卡
 * 本身的"主角化"视觉后续怎么定（本批不动卡头，§二"暖金顶冠"另案处理），
 * 事实面板都不该抢方案卡的焦点，故克制参数按§3.1原则先行落地。
 */
export default function NodeFactPanel({
  detail,
  variant,
  className,
}: {
  detail: NodeDetail | undefined | null;
  variant: "column" | "row";
  className?: string;
}) {
  if (!detail) return null;

  const tags = detail.tags ?? [];
  const facts: { key: string; node: ReactNode }[] = [];

  // 评分：唯一暖色触点（深金 ⭐），其余事实一律中性幽灰。
  if (detail.rating != null) {
    facts.push({
      key: "rating",
      node: (
        <span className="inline-flex items-center gap-0.5 font-semibold text-accent-600">
          <span aria-hidden>⭐</span>
          {detail.rating.toFixed(1)}
        </span>
      ),
    });
  }
  if (detail.price_text) {
    facts.push({ key: "price", node: <span>{detail.price_text}</span> });
  }
  if (detail.distance_km != null) {
    facts.push({
      key: "distance",
      node: (
        <span className="inline-flex items-center gap-0.5">
          <span aria-hidden>🚶</span>
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

  const tagNodes = tags.map((t) => (
    <span
      key={t}
      className="rounded bg-black/[0.03] px-1.5 py-[1px] text-[10.5px] font-medium leading-[1.4] text-ink-600"
    >
      {t}
    </span>
  ));

  if (variant === "column") {
    return (
      <div
        className={cn(
          "flex flex-col items-end gap-1 text-right text-[11px] leading-snug text-ink-500",
          className,
        )}
      >
        {facts.map((f) => (
          <div key={f.key}>{f.node}</div>
        ))}
        {tagNodes.length > 0 && (
          <div className="mt-0.5 flex flex-wrap justify-end gap-1">{tagNodes}</div>
        )}
      </div>
    );
  }

  // "row"：移动端店名下一行 chips——数字类事实用极淡分隔点串联，tag 仍是
  // 独立的小 chip（不强上两栏，见设计终稿§四"移动端 MobileHomeView 镜像"）。
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
      {tagNodes}
    </div>
  );
}
