"use client";

import { useChatStore } from "@/lib/store";
import { scenarioIcon } from "@/lib/icon-map";
import { cn } from "@/lib/utils";

/** 8 个快捷场景按钮（黄昏深色主题：玻璃描边 + hover 暖光晕）。 */
export default function QuickScenarios({ enlarged = false }: { enlarged?: boolean }) {
  const scenarios = useChatStore((s) => s.scenarios);
  const streaming = useChatStore((s) => s.streaming);
  const sendScenario = useChatStore((s) => s.sendScenario);

  if (!scenarios.length) {
    return (
      <div className="card px-4 py-3 text-sm text-ink-500">
        正在拉取演示场景...
      </div>
    );
  }

  return (
    <div className="card px-4 py-3">
      <div className="mb-2.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="section-title">演示场景</span>
          <span className="text-sm text-ink-400">8 个</span>
        </div>
        <div className="hidden sm:flex items-center gap-1 text-sm text-ink-400">
          <span>或按</span>
          <span className="kbd">⌘</span>
          <span className="kbd">K</span>
          <span>打开命令面板</span>
        </div>
      </div>
      <div className={cn(
        "grid gap-2",
        enlarged
          ? "grid-cols-2 sm:grid-cols-4 lg:grid-cols-4"
          : "grid-cols-2 sm:grid-cols-4 lg:grid-cols-8",
      )}>
        {scenarios.map((s) => {
          const Icon = scenarioIcon(s.id);
          return (
            <button
              key={s.id}
              disabled={streaming}
              onClick={() => sendScenario(s.input, s.id)}
              className={cn(
                "group relative flex flex-col items-start gap-2 rounded-xl",
                "bg-white border-2 border-[#FFD100]",
                "text-left transition-all duration-200",
                "hover:border-[#e6bc00] hover:shadow-sm",
                "active:scale-[0.98]",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                "disabled:hover:bg-white disabled:hover:border-black/[0.08] disabled:hover:shadow-none",
                "overflow-hidden",
                enlarged ? "px-4 py-4 pb-5" : "px-3 py-2.5 pb-4",
              )}
              title={s.input}
            >
              {/* hover 时底部黄色提示条 */}
              <span
                aria-hidden
                className="pointer-events-none absolute bottom-0 left-1/2 -translate-x-1/2 h-[5px] w-10 rounded-full bg-[#FFD100] opacity-0 group-hover:opacity-100 transition-opacity duration-200"
              />
              <Icon
                className={cn(
                  "relative text-ink-600 group-hover:text-ink-800 transition-colors",
                  enlarged ? "w-6 h-6" : "w-5 h-5",
                )}
                strokeWidth={1.75}
              />
              <span className={cn(
                "relative font-medium text-ink-700 group-hover:text-ink-900 line-clamp-1 tracking-tight",
                enlarged ? "text-base" : "text-sm",
              )}>
                <span className="mono text-sm text-ink-500 mr-1">
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

