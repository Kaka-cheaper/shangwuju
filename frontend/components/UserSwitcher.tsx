"use client";

/**
 * UserSwitcher —— 顶栏用户切换器（黄昏深色主题：玻璃描边）。
 * 配色：用户档案语义采用 caramel 焦糖琥珀色（替代 AI 莓紫，去 AI 味）。
 *
 * z-index 设计（problem.md 问题 23）：
 *   下拉面板用 position: fixed + z-45 渲染——脱离 header (z-20) 的 stacking context，
 *   高于 ChatDock (z-30) 与 ToastStack (z-40)，低于 CommandPalette (z-50)。
 *   按钮位置通过 ref.getBoundingClientRect() 计算，每次开打/视口变化重新对齐。
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { Icons, personaIconFromEmoji } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

const PANEL_WIDTH = 256;
const PANEL_OFFSET_Y = 6; // mt-1.5 ≈ 6px

export default function UserSwitcher() {
  const personas = useChatStore((s) => s.personas);
  const personasLoaded = useChatStore((s) => s.personasLoaded);
  const currentUserId = useChatStore((s) => s.currentUserId);
  const setCurrentUserId = useChatStore((s) => s.setCurrentUserId);
  const loadPersonas = useChatStore((s) => s.loadPersonas);

  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [panelPos, setPanelPos] = useState<{
    top: number;
    right: number;
    maxHeight: number;
  } | null>(null);

  useEffect(() => {
    if (!personasLoaded) loadPersonas();
  }, [personasLoaded, loadPersonas]);

  // 计算面板位置（按钮下方右对齐）+ 可用高度（避免被底部 dock 遮住）
  const updatePosition = () => {
    if (!buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    const top = rect.bottom + PANEL_OFFSET_Y;
    setPanelPos({
      top,
      right: window.innerWidth - rect.right,
      maxHeight: Math.max(160, window.innerHeight - top - 12),
    });
  };

  useLayoutEffect(() => {
    if (!open) return;
    updatePosition();
    const onResize = () => updatePosition();
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onResize, true);
    };
  }, [open]);

  // 点外部关闭：按钮 + 面板都不算外部
  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      const target = e.target as Node;
      const inWrap = wrapRef.current?.contains(target);
      const inPanel = panelRef.current?.contains(target);
      if (!inWrap && !inPanel) setOpen(false);
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onEsc);
    };
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
        ref={buttonRef}
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

      {open && panelPos && (
        <div
          ref={panelRef}
          className="fixed rounded-lg border border-white/[0.08] z-[45] overflow-hidden shadow-elevated backdrop-blur-xl animate-fade-in flex flex-col"
          style={{
            top: `${panelPos.top}px`,
            right: `${panelPos.right}px`,
            width: `${PANEL_WIDTH}px`,
            maxHeight: `${panelPos.maxHeight}px`,
            background: "rgba(20, 20, 23, 0.95)",
          }}
        >
          <div className="px-3 py-2 text-[10px] uppercase tracking-wider text-ink-500 border-b border-white/[0.06] shrink-0">
            选择演示用户档案
          </div>
          <ul className="py-1 flex-1 min-h-0 overflow-auto">
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
