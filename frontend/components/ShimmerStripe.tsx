"use client";

/**
 * ShimmerStripe —— C 局部动效（骨架屏流光）。
 *
 * 用法：在 streaming/loading 时替代纯文字+pulse 的弱反馈，提供 Linear / Vercel
 * 派系的标志性流光骨架感。多条参数化的横条，宽度递增/随机，从左到右光带扫过。
 *
 * 用 CSS 渐变 + background-position 滚动实现，不依赖任何 motion 库。
 */

import { cn } from "@/lib/utils";

interface ShimmerStripeProps {
  /** 横条数量，默认 3 */
  rows?: number;
  /** 容器额外 className */
  className?: string;
}

const WIDTH_CLASSES = ["w-full", "w-5/6", "w-2/3", "w-3/4", "w-4/5"];

export default function ShimmerStripe({ rows = 3, className }: ShimmerStripeProps) {
  return (
    <div className={cn("space-y-2", className)} aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className={cn(
            "h-2.5 rounded-md shimmer-skeleton",
            WIDTH_CLASSES[i % WIDTH_CLASSES.length],
          )}
        />
      ))}
    </div>
  );
}
