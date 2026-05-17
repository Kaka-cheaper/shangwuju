"use client";

import { useChatStore } from "@/lib/store";
import { scenarioIcon } from "@/lib/icon-map";
import { cn } from "@/lib/utils";

/** 8 个快捷场景按钮（黄昏深色主题：玻璃描边 + hover 暖光晕）。 */
export default function QuickScenarios() {
  const scenarios = useChatStore((s) => s.scenarios);
  const streaming = useChatStore((s) => s.streaming);
  const sendMessage = useChatStore((s) => s.sendMessage);

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
                "group relative flex flex-col items-start gap-2 rounded-md",
                "border border-white/[0.08] bg-white/[0.03]",
                "px-3 py-2.5 text-left transition-all duration-200",
                "hover:border-brand-500/40 hover:bg-white/[0.06]",
                "hover:shadow-[0_0_24px_-8px_rgb(249_115_22_/_0.4)]",
                "active:bg-white/[0.04]",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                "disabled:hover:bg-white/[0.03] disabled:hover:border-white/[0.08] disabled:hover:shadow-none",
                "backdrop-blur-sm overflow-hidden",
              )}
              title={s.input}
            >
              {/* hover 时左下浮现暖橙光斑 */}
              <span
                aria-hidden
                className="pointer-events-none absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-300"
                style={{
                  background:
                    "radial-gradient(circle at 0% 100%, rgba(249,115,22,0.18) 0%, transparent 60%)",
                }}
              />
              <Icon
                className="relative w-4 h-4 text-ink-600 group-hover:text-brand-400 transition-colors"
                strokeWidth={1.75}
              />
              <span className="relative text-xs font-medium text-ink-700 group-hover:text-ink-900 line-clamp-1 tracking-tight">
                <span className="mono text-[10px] text-ink-500 mr-1">
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
