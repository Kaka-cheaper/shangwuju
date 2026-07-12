"use client";

import { Icons } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

// 去绿归色（配色克制设计终稿）：success 是"成功/确认"语义，和 CTA 一脉相承
// 走暖金（accent），不用游离调色板外的 emerald——success 用 accent-700/深金渐
// 变，info 沿用既有 accent-500 浅色系，两者色相相同、深浅有别以保持可辨识。
const KIND_STYLES = {
  success:
    "border-accent-600/35 text-accent-800",
  info: "border-accent-500/30 text-accent-700",
  warn: "border-amber-500/30 text-amber-700",
} as const;

const KIND_GRADIENT = {
  success:
    "linear-gradient(135deg, rgba(217,119,6,0.14) 0%, rgba(217,119,6,0.05) 100%)",
  info: "linear-gradient(135deg, rgba(245,158,11,0.10) 0%, rgba(251,191,36,0.04) 100%)",
  warn: "linear-gradient(135deg, rgba(245,158,11,0.12) 0%, rgba(245,158,11,0.04) 100%)",
} as const;

const KIND_ICONS = {
  success: Icons.success,
  info: Icons.spark,
  warn: Icons.warn,
} as const;

const KIND_ICON_TINT = {
  success: "text-accent-700",
  info: "text-accent-600",
  warn: "text-amber-500",
} as const;

/** 右下角 Toast 堆叠（黄昏深色主题：玻璃半透 + 边发光）。 */
export default function ToastStack() {
  const toasts = useChatStore((s) => s.toasts);
  const dismiss = useChatStore((s) => s.dismissToast);

  if (toasts.length === 0) return null;

  return (
    <div
      className="pointer-events-none fixed bottom-[calc(108px+env(safe-area-inset-bottom,0px))] left-4 right-4 z-40 flex flex-col gap-2 sm:bottom-4 sm:left-auto sm:w-[320px] max-w-[calc(100vw-2rem)]"
      aria-live="polite"
      aria-atomic="false"
    >
      {toasts.map((t) => {
        const Icon = KIND_ICONS[t.kind];
        return (
          <button
            key={t.id}
            onClick={() => dismiss(t.id)}
            className={cn(
              "pointer-events-auto text-left text-xs leading-relaxed",
              "rounded-md border animate-fade-in-up backdrop-blur-xl",
              "px-3 py-2.5 flex items-start gap-2 tracking-tight",
              "shadow-[0_8px_32px_-8px_rgba(0,0,0,0.6)]",
              KIND_STYLES[t.kind],
            )}
            style={{ background: KIND_GRADIENT[t.kind] }}
            aria-label="点击关闭通知"
          >
            <Icon
              className={cn("w-3.5 h-3.5 mt-0.5 shrink-0", KIND_ICON_TINT[t.kind])}
              strokeWidth={2}
            />
            <span className="flex-1">{t.text}</span>
          </button>
        );
      })}
    </div>
  );
}
