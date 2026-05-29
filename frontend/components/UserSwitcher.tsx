"use client";

/**
 * UserSwitcher —— 顶栏用户切换器（黄昏深色主题：玻璃描边）。
 * 配色：用户档案语义采用 caramel 焦糖琥珀色（替代 AI 莓紫，去 AI 味）。
 *
 * z-index 设计（problem.md 问题 23 第二轮修复）：
 *   下拉面板用 React Portal 渲染到 document.body + position: fixed + z-[60]。
 *
 *   为什么必须 Portal：第一轮只用 fixed + z-45 不够，因为：
 *   - <main className="relative-content"> 是 z-1 stacking context（globals.css §relative-content）
 *   - <header className="relative-content sticky z-20"> 也是 z-20 stacking context
 *   - UserSwitcher 是 header 的 DOM 子节点，即使面板用 fixed 仍受困于 header 子树
 *   - main 内部的 ItineraryCard / ToolTracePanel / QuickScenarios 都在 z-1 上下文里
 *     当它们与 header 内 z-45 的元素（fixed 但仍是 header 子树）比较时，
 *     header z-20 vs main z-1 的对比下，main 内部任何元素都可能盖住 header 子树
 *
 *   Portal 把面板物理脱离整个组件树 → 直接挂到 body → z-60 真正全局生效。
 *
 *   层级表（含本次更新）：
 *     z-60 UserSwitcher 下拉（Portal） + Confetti（fixed inset z-60）
 *     z-50 CommandPalette
 *     z-40 ToastStack
 *     z-30 ChatDock / RefinementDialog
 *     z-20 Header
 *     z-1  main / header relative-content
 *     z-0  aurora-bg
 *
 *   按钮位置通过 buttonRef.getBoundingClientRect() 计算，每次开打/视口变化重新对齐。
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

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
  const [mounted, setMounted] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [panelPos, setPanelPos] = useState<{
    top: number;
    right: number;
    maxHeight: number;
  } | null>(null);

  // SSR 时 document 不存在，等 mount 完才能 createPortal
  useEffect(() => {
    setMounted(true);
  }, []);

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
        className="inline-flex items-center gap-1.5 rounded-md border border-black/[0.08] bg-black/[0.02] px-2.5 py-1 text-xs text-ink-700 hover:border-black/[0.12] hover:bg-black/[0.04] hover:text-ink-900 transition-colors tracking-tight backdrop-blur"
        onClick={() => setOpen((v) => !v)}
        title="切换演示用户"
      >
        <CurrentIcon className="w-3.5 h-3.5 text-caramel-300" strokeWidth={2} />
        <span className="max-w-[90px] truncate">{display}</span>
        <span
          className={cn(
            "text-xs text-ink-500 transition-transform",
            open && "rotate-180",
          )}
        >
          ▾
        </span>
      </button>

      {(open && panelPos && mounted
        ? createPortal(
          <div
            ref={panelRef}
            className="fixed rounded-lg border border-black/[0.08] overflow-hidden shadow-elevated backdrop-blur-xl animate-fade-in flex flex-col"
            style={{
              top: `${panelPos.top}px`,
              right: `${panelPos.right}px`,
              width: `${PANEL_WIDTH}px`,
              maxHeight: `${panelPos.maxHeight}px`,
              background: "rgba(255, 255, 255, 0.95)",
              zIndex: 60,
            }}
          >
            <div className="px-3 py-2 text-xs uppercase tracking-wider text-ink-500 border-b border-black/[0.06] shrink-0">
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
                        active ? "bg-black/[0.04]" : "hover:bg-black/[0.03]",
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
                        <span className="block text-xs text-ink-600 line-clamp-2">
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
          </div>,
          document.body,
        )
        : null) as React.ReactNode}
    </div>
  );
}

