"use client";

import { useEffect } from "react";

import { useChatStore } from "@/lib/store";
import { generateSessionId } from "@/lib/utils";

import ChatPanel from "./ChatPanel";
import ItineraryCard from "./ItineraryCard";
import QuickScenarios from "./QuickScenarios";
import ToastStack from "./ToastStack";
import ToolTracePanel from "./ToolTracePanel";

export default function HomeView() {
  const loadScenarios = useChatStore((s) => s.loadScenarios);
  const reset = useChatStore((s) => s.reset);
  const sessionId = useChatStore((s) => s.sessionId);

  useEffect(() => {
    // 客户端 mount 后再生成真实 session_id，避免 SSR/CSR 不一致 hydration 报错
    if (sessionId === "sess_pending") {
      useChatStore.setState({ sessionId: generateSessionId() });
    }
    loadScenarios();
  }, [loadScenarios, sessionId]);

  return (
    <div className="min-h-screen bg-gradient-to-b from-brand-50 via-ink-50 to-ink-50">
      <header className="sticky top-0 z-10 border-b border-ink-200 bg-white/85 backdrop-blur">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 py-3 flex items-center justify-between gap-3">
          <div className="flex items-baseline gap-2 sm:gap-3 min-w-0">
            <h1 className="text-lg sm:text-xl font-bold text-ink-900 shrink-0">
              晌午局
            </h1>
            <span className="hidden md:inline text-sm text-ink-500 truncate">
              一句话搞定下午行程 · 本地半日出行管家
            </span>
          </div>
          <div className="flex items-center gap-2 sm:gap-3 text-xs text-ink-500 shrink-0">
            <span
              className="hidden sm:inline truncate max-w-[180px]"
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

      <main className="mx-auto max-w-7xl px-4 sm:px-6 py-4 sm:py-6">
        <QuickScenarios />

        <div className="mt-4 grid grid-cols-1 lg:grid-cols-12 gap-4">
          <section className="lg:col-span-7">
            <ChatPanel />
          </section>

          <aside className="lg:col-span-5 space-y-4">
            <ItineraryCard />
            <ToolTracePanel />
          </aside>
        </div>
      </main>

      <footer className="mx-auto max-w-7xl px-4 sm:px-6 py-6 text-center text-[11px] sm:text-xs text-ink-400">
        美团 AI Hackathon 06 · 本地探索 · 周末闲时活动规划
      </footer>

      <ToastStack />
    </div>
  );
}
