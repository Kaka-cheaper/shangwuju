"use client";

import { Icons } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

const KIND_STYLES = {
  success: "bg-white border-emerald-200 text-emerald-800",
  info: "bg-white border-accent-200 text-accent-800",
  warn: "bg-white border-amber-200 text-amber-800",
} as const;

const KIND_ICONS = {
  success: Icons.success,
  info: Icons.spark,
  warn: Icons.warn,
} as const;

const KIND_ICON_TINT = {
  success: "text-emerald-600",
  info: "text-accent-600",
  warn: "text-amber-600",
} as const;

/** 右下角 Toast 堆叠：refine changed_fields / 取消反馈 / mode 切换提示。 */
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
              "rounded-md shadow-elevated border animate-fade-in-up",
              "px-3 py-2.5 flex items-start gap-2 tracking-tight",
              KIND_STYLES[t.kind],
            )}
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
