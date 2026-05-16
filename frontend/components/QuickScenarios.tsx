"use client";

import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/** 8 个快捷场景按钮：一键填入并提交。 */
export default function QuickScenarios() {
  const scenarios = useChatStore((s) => s.scenarios);
  const streaming = useChatStore((s) => s.streaming);
  const sendMessage = useChatStore((s) => s.sendMessage);

  if (!scenarios.length) {
    return (
      <div className="card px-4 py-3 text-sm text-ink-500">
        正在拉取演示场景...（请确保后端已启动且 NEXT_PUBLIC_API_BASE 指向正确）
      </div>
    );
  }

  return (
    <div className="card px-4 py-3">
      <div className="mb-2.5 flex items-center justify-between">
        <div className="text-sm font-medium text-ink-700">
          演示场景 · 一键提交
        </div>
        <div className="hidden sm:block text-xs text-ink-400">
          点击任一场景即可压测；输入框自由输入也支持
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
        {scenarios.map((s) => (
          <button
            key={s.id}
            disabled={streaming}
            onClick={() => sendMessage(s.input, s.id)}
            className={cn(
              "group flex flex-col items-start gap-1.5 rounded-lg border border-ink-200",
              "bg-white px-3 py-2.5 text-left transition-all duration-200",
              "hover:border-brand-500 hover:shadow-md hover:-translate-y-0.5",
              "active:translate-y-0 active:shadow-sm",
              "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:border-ink-200 disabled:hover:translate-y-0 disabled:hover:shadow-none",
            )}
            title={s.input}
          >
            <span className="text-2xl leading-none" aria-hidden>
              {s.icon}
            </span>
            <span className="text-xs font-medium text-ink-700 group-hover:text-brand-700 line-clamp-1">
              {s.id} · {s.title}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
