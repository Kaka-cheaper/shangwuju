"use client";

/**
 * UserSwitcher —— 顶栏用户切换器（黄昏深色主题：玻璃描边）。
 * 配色：用户档案语义采用 caramel 焦糖琥珀色（替代 AI 莓紫，去 AI 味）。
 */

import { useEffect, useRef, useState } from "react";

import { Icons, personaIconFromEmoji } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export default function UserSwitcher() {
  const personas = useChatStore((s) => s.personas);
  const personasLoaded = useChatStore((s) => s.personasLoaded);
  const currentUserId = useChatStore((s) => s.currentUserId);
  const setCurrentUserId = useChatStore((s) => s.setCurrentUserId);
  const loadPersonas = useChatStore((s) => s.loadPersonas);

  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!personasLoaded) loadPersonas();
  }, [personasLoaded, loadPersonas]);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const current = personas.find((p) => p.user_id === currentUserId);
  const CurrentIcon = current
    ? personaIconFromEmoji(current.icon)
    : Icons.user;
  const display =
    current?.label ??
    (currentUserId === "demo_user" ? "默认" : currentUserId ?? "未设置");

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        className="inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-xs text-ink-700 hover:border-white/[0.16] hover:bg-white/[0.06] hover:text-ink-900 transition-colors tracking-tight backdrop-blur"
        onClick={() => setOpen((v) => !v)}
        title="切换演示用户"
      >
        <CurrentIcon className="w-3.5 h-3.5 text-caramel-300" strokeWidth={2} />
        <span className="max-w-[90px] truncate">{display}</span>
        <span
          className={cn(
            "text-[10px] text-ink-500 transition-transform",
            open && "rotate-180",
          )}
        >
          ▾
        </span>
      </button>

      {open && (
        <div
          className="absolute right-0 mt-1.5 w-64 rounded-lg border border-white/[0.08] z-30 overflow-hidden shadow-elevated backdrop-blur-xl"
          style={{ background: "rgba(20, 20, 23, 0.95)" }}
        >
          <div className="px-3 py-2 text-[10px] uppercase tracking-wider text-ink-500 border-b border-white/[0.06]">
            选择演示用户档案
          </div>
          <ul className="py-1 max-h-72 overflow-auto">
            {personas.map((p) => {
              const Icon = personaIconFromEmoji(p.icon);
              const active = p.user_id === currentUserId;
              return (
                <li key={p.user_id}>
                  <button
                    type="button"
                    className={cn(
                      "w-full px-3 py-2 text-left text-xs flex items-start gap-2 transition-colors",
                      active ? "bg-white/[0.06]" : "hover:bg-white/[0.04]",
                    )}
                    onClick={() => {
                      setCurrentUserId(p.user_id);
                      setOpen(false);
                    }}
                  >
                    <Icon
                      className={cn(
                        "w-3.5 h-3.5 mt-0.5 shrink-0",
                        active ? "text-caramel-300" : "text-ink-500",
                      )}
                      strokeWidth={2}
                    />
                    <span className="flex-1 min-w-0">
                      <span
                        className={cn(
                          "block font-medium tracking-tight",
                          active ? "text-ink-900" : "text-ink-800",
                        )}
                      >
                        {p.label}
                      </span>
                      <span className="block text-[11px] text-ink-600 line-clamp-2">
                        {p.notes}
                      </span>
                    </span>
                    {active && (
                      <Icons.success
                        className="w-3.5 h-3.5 text-caramel-300 shrink-0"
                        strokeWidth={2.5}
                      />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
