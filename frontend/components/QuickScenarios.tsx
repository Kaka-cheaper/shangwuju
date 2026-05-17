"use client";

import { useChatStore } from "@/lib/store";
import { scenarioIcon } from "@/lib/icon-map";
import { cn } from "@/lib/utils";

/** 8 个快捷场景按钮：B+D 范式（去 emoji + 灰阶 hover）。 */
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
        <div className="flex items-center gap-2">
          <span className="section-title">演示场景</span>
          <span className="text-[11px] text-ink-400">8 个</span>
        </div>
        <div className="hidden sm:flex items-center gap-1 text-[11px] text-ink-400">
          <span>或按</span>
          <span className="kbd">⌘</span>
          <span className="kbd">K</span>
          <span>打开命令面板</span>
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
        {scenarios.map((s) => {
          const Icon = scenarioIcon(s.id);
          return (
            <button
              key={s.id}
              disabled={streaming}
              onClick={() => sendMessage(s.input, s.id)}
              className={cn(
                "group flex flex-col items-start gap-2 rounded-md border border-ink-200",
                "bg-white px-3 py-2.5 text-left transition-colors duration-150",
                "hover:border-ink-300 hover:bg-ink-50/50",
                "active:bg-ink-100",
                "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-white",
              )}
              title={s.input}
            >
              <Icon
                className="w-4 h-4 text-ink-500 group-hover:text-ink-800 transition-colors"
                strokeWidth={1.75}
              />
              <span className="text-xs font-medium text-ink-700 group-hover:text-ink-900 line-clamp-1 tracking-tight">
                <span className="mono text-[10px] text-ink-400 mr-1">
                  {s.id}
                </span>
                {s.title}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
