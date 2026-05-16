"use client";

import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/** 右下角 Toast 堆叠：refine changed_fields 提示 / 取消反馈 / mode 切换提示。 */
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
      {toasts.map((t) => (
        <button
          key={t.id}
          onClick={() => dismiss(t.id)}
          className={cn(
            "pointer-events-auto text-left text-xs leading-relaxed rounded-md shadow-md",
            "px-3 py-2 border animate-fade-in-up",
            t.kind === "success" &&
              "bg-emerald-50 border-emerald-200 text-emerald-800",
            t.kind === "info" && "bg-sky-50 border-sky-200 text-sky-800",
            t.kind === "warn" && "bg-amber-50 border-amber-200 text-amber-800",
          )}
          aria-label="点击关闭通知"
        >
          {t.text}
        </button>
      ))}
    </div>
  );
}
