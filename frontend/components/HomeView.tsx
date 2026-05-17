"use client";

import { useEffect } from "react";

import { useChatStore } from "@/lib/store";
import { generateSessionId, getUserIdFromCookie } from "@/lib/utils";

import ChatPanel from "./ChatPanel";
import CommandPalette from "./CommandPalette";
import ItineraryCard from "./ItineraryCard";
import PlannerModeBadge from "./PlannerModeBadge";
import PreferencesPanel from "./PreferencesPanel";
import QuickScenarios from "./QuickScenarios";
import ToastStack from "./ToastStack";
import ToolTracePanel from "./ToolTracePanel";
import UserSwitcher from "./UserSwitcher";

export default function HomeView() {
  const loadScenarios = useChatStore((s) => s.loadScenarios);
  const loadPersonas = useChatStore((s) => s.loadPersonas);
  const refreshPreferences = useChatStore((s) => s.refreshPreferences);
  const reset = useChatStore((s) => s.reset);
  const sessionId = useChatStore((s) => s.sessionId);
  const openCommandPalette = useChatStore((s) => s.openCommandPalette);

  useEffect(() => {
    if (sessionId === "sess_pending") {
      useChatStore.setState({ sessionId: generateSessionId() });
    }
    const persisted = getUserIdFromCookie();
    if (persisted) {
      useChatStore.setState({ currentUserId: persisted });
    }
    loadScenarios();
    loadPersonas();
    refreshPreferences();
  }, [loadScenarios, loadPersonas, refreshPreferences, sessionId]);

  // 全局键盘：Cmd+K / Ctrl+K 打开命令面板
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        openCommandPalette();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [openCommandPalette]);

  return (
    <div className="min-h-screen relative">
      {/* 黄昏光斑背景层（fixed，最底层） */}
      <div className="aurora-bg" aria-hidden />

      {/* 顶栏：玻璃质感 */}
      <header className="relative-content sticky top-0 z-20 border-b border-white/[0.06] bg-[#08080d]/70 backdrop-blur-xl">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 h-14 flex items-center justify-between gap-3">
          {/* 左侧：品牌渐变 mark + breadcrumb */}
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex items-center gap-2.5">
              <div className="brand-mark" aria-hidden />
              <div className="flex flex-col leading-none">
                <h1 className="text-[15px] font-semibold tracking-tight text-ink-900 shrink-0">
                  晌午局
                </h1>
                <span className="hidden sm:inline text-[10px] text-ink-500 mt-0.5 tracking-wide">
                  Shangwu · Local Half-Day Agent
                </span>
              </div>
            </div>
            <span className="hidden md:inline text-ink-300/40">/</span>
            <span className="hidden md:inline text-xs text-ink-500 truncate">
              半日出行管家 · Agent 编排可视化
            </span>
          </div>

          {/* 右侧 */}
          <div className="flex items-center gap-2 sm:gap-3 shrink-0">
            <button
              type="button"
              onClick={openCommandPalette}
              className="hidden sm:inline-flex items-center gap-2 rounded-md border border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.06] hover:border-white/[0.16] px-2.5 py-1 text-xs text-ink-500 hover:text-ink-800 transition-colors backdrop-blur"
              title="打开命令面板（场景 / 模式 / 用户切换）"
            >
              <span>命令</span>
              <span className="kbd">⌘</span>
              <span className="kbd">K</span>
            </button>
            <UserSwitcher />
            <PlannerModeBadge />
            <span
              className="hidden lg:inline mono text-[11px] text-ink-400 truncate max-w-[140px]"
              title={`当前会话 ID：${sessionId}`}
            >
              {sessionId}
            </span>
            <button className="btn-ghost" onClick={reset}>
              重置
            </button>
          </div>
        </div>
      </header>

      <main className="relative-content mx-auto max-w-7xl px-4 sm:px-6 py-4 sm:py-6">
        <QuickScenarios />

        <div className="mt-4 grid grid-cols-1 lg:grid-cols-12 gap-4">
          <section className="lg:col-span-7">
            <ChatPanel />
          </section>

          <aside className="lg:col-span-5 space-y-4">
            <ItineraryCard />
            <PreferencesPanel />
            <ToolTracePanel />
          </aside>
        </div>
      </main>

      <footer className="relative-content mx-auto max-w-7xl px-4 sm:px-6 py-6 text-center">
        <span className="text-[11px] text-ink-400/70 tracking-tight">
          美团 AI Hackathon 06 · 本地探索 · 周末闲时活动规划
        </span>
      </footer>

      <ToastStack />
      <CommandPalette />
    </div>
  );
}
