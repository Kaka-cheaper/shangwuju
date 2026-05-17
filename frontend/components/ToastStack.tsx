"use client";

import { Icons } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

const KIND_STYLES = {
  success:
    "border-emerald-500/30 text-emerald-200",
  info: "border-accent-500/30 text-accent-200",
  warn: "border-amber-500/30 text-amber-200",
} as const;

const KIND_GRADIENT = {
  success:
    "linear-gradient(135deg, rgba(16,185,129,0.15) 0%, rgba(16,185,129,0.05) 100%)",
  info: "linear-gradient(135deg, rgba(217,70,239,0.15) 0%, rgba(139,92,246,0.05) 100%)",
  warn: "linear-gradient(135deg, rgba(245,158,11,0.15) 0%, rgba(245,158,11,0.05) 100%)",
} as const;

const KIND_ICONS = {
  success: Icons.success,
  info: Icons.spark,
  warn: Icons.warn,
} as const;

const KIND_ICON_TINT = {
  success: "text-emerald-400",
  info: "text-accent-300",
  warn: "text-amber-400",
} as const;

/** 右下角 Toast 堆叠（黄昏深色主题：玻璃半透 + 边发光）。 */
export default function ToastStack() {
  const toasts = useChatStore((s) => s.toasts);
  const dismiss = useChatStore((s) => s.dismissToast);

  if (toasts.length === 0) return null;

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-40 flex flex-col gap-2 max-w-[calc(100vw-2rem)] w-[320px]"
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
