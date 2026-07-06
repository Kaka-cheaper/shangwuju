"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useChatStore } from "@/lib/store";
import { useCollabStore } from "@/lib/collab-store";
import { useBootstrapPlannerMode } from "@/lib/hooks/useBootstrapPlannerMode";
import {
  clearUserIdCookie,
  generateSessionId,
  upsertSession,
} from "@/lib/utils";
import { cn } from "@/lib/utils";

import ChatDock from "./ChatDock";
import CollabBar from "./CollabBar";
import CommandPalette from "./CommandPalette";
import Confetti from "./Confetti";
import ConstraintFeed from "./ConstraintFeed";
import DecisionTraceCard from "./DecisionTraceCard";
import ItineraryCard from "./ItineraryCard";
import ItineraryUtilityBar from "./ItineraryUtilityBar";
import MockModeBadge from "./MockModeBadge";
import OfflineReadyBadge from "./OfflineReadyBadge";
import PlannerModeBadge from "./PlannerModeBadge";
import PreferencesPanel from "./PreferencesPanel";
import QuickScenarios from "./QuickScenarios";
import ShareModal from "./ShareModal";
import ToastStack from "./ToastStack";
import ThoughtPanel from "./ThoughtPanel";
import ToolTracePanel from "./ToolTracePanel";
import UserSwitcher from "./UserSwitcher";

export default function HomeView() {
  const loadScenarios = useChatStore((s) => s.loadScenarios);
  const loadPersonas = useChatStore((s) => s.loadPersonas);
  const refreshPreferences = useChatStore((s) => s.refreshPreferences);
  const startNewSession = useChatStore((s) => s.startNewSession);
  const sessionId = useChatStore((s) => s.sessionId);
  const openCommandPalette = useChatStore((s) => s.openCommandPalette);
  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);

  // 是否已激活（用户发过消息或正在流式中）
  const activated = messages.length > 0 || streaming;

  // 顶栏滚动下沉：scrolled=true 时加深背景 + 暖色发光底线
  const [scrolled, setScrolled] = useState(false);
  const personaResetOnLoadRef = useRef(false);
  const bgRef = useRef<HTMLDivElement | null>(null);

  // 协作模式
  const roomId = useCollabStore((s) => s.roomId);
  const [shareModalOpen, setShareModalOpen] = useState(false);
  const decisionTrace = useChatStore((s) => s.itinerary?.decision_trace);

  // A9 根治：planner 模式的 cookie/health 校准不再依赖 PlannerModeBadge 是否
  // 挂载，根组件统一调用一次（Web/移动端共用同一份实现，见 hook docstring）。
  useBootstrapPlannerMode();

  useEffect(() => {
    if (sessionId === "sess_pending") {
      const newId = generateSessionId();
      useChatStore.setState({ sessionId: newId });
      // 首次进站把当前 session 也注册进 localStorage 列表
      upsertSession({ id: newId, label: "新对话", lastMessageAt: Date.now() });
    } else {
      // 后续刷新：保持当前 session 在 localStorage 里
      upsertSession({ id: sessionId, lastMessageAt: Date.now() });
    }
    if (!personaResetOnLoadRef.current) {
      personaResetOnLoadRef.current = true;
      clearUserIdCookie();
      useChatStore.setState({ currentUserId: "demo_user", preferences: null });
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

  // 滚动下沉：用 rAF 节流，过 12px 阈值切换
  useEffect(() => {
    let rafId = 0;
    const onScroll = () => {
      if (rafId) return;
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        setScrolled(window.scrollY > 12);
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, []);

  const handleBackgroundPointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const bg = bgRef.current;
      if (!bg) return;
      bg.style.setProperty("--grid-x", `${event.clientX}px`);
      bg.style.setProperty("--grid-y", `${event.clientY}px`);
    },
    [],
  );

  return (
    <div
      className="min-h-screen relative"
      onPointerMove={handleBackgroundPointerMove}
    >
      {/* 黄色光晕背景层（fixed，最底层） */}
      <div
        ref={bgRef}
        className="aurora-bg aurora-bg--web-grid"
        aria-hidden
      >
        <span className="aurora-bg__grid aurora-bg__grid--base" />
        <span className="aurora-bg__grid aurora-bg__grid--reveal" />
      </div>

      {/* 顶栏：始终显示 */}
      <header
        className={cn(
          "relative-content sticky top-0 z-20 border-b border-black/[0.06]",
          "bg-white/80 backdrop-blur-xl",
          "transition-[background-color,box-shadow] duration-300",
          scrolled && "header-scrolled",
        )}
      >
        <div className="px-6 sm:px-10 h-14 flex items-center justify-between gap-4">
          {/* 左侧：品牌渐变 mark + breadcrumb */}
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex items-center gap-2.5">
              <div className="w-9 h-9 flex items-center justify-center shrink-0" aria-hidden>
                <svg
                  width="32"
                  height="32"
                  viewBox="0 0 32 32"
                  fill="none"
                >
                  {/* 定位针轮廓（深墨色，高对比） */}
                  <path
                    d="M16 3.5 C 21 3.5, 25 7.3, 25 12.2 C 25 18.5, 16 27.5, 16 27.5 C 16 27.5, 7 18.5, 7 12.2 C 7 7.3, 11 3.5, 16 3.5 Z"
                    fill="none"
                    stroke="#1f2937"
                    strokeWidth="2.2"
                    strokeLinejoin="round"
                  />
                  {/* 针内午后太阳：黄色实心圆 + 半圆地平线意象 */}
                  <circle cx="16" cy="11.8" r="4.2" fill="#FFD100" />
                  {/* 太阳下半被地平线遮，用墨色短线表现地平线 */}
                  <line
                    x1="11.6" y1="13.2" x2="20.4" y2="13.2"
                    stroke="#1f2937"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                  />
                </svg>
              </div>
              <div className="flex flex-col leading-none">
                <h1 className="text-[17px] font-semibold tracking-tight text-ink-900 shrink-0">
                  晌午局
                </h1>
                <span className="hidden sm:inline text-xs text-ink-500 mt-0.5 tracking-wide">
                  Shangwu · Local Half-Day Agent
                </span>
              </div>
            </div>
            <span className="hidden md:inline text-ink-300/40">/</span>
            <span className="hidden md:inline text-sm text-ink-500 truncate">
              半日出行管家 · Agent 编排可视化
            </span>
          </div>

          {/* 右侧 */}
          <div className="flex items-center gap-2 sm:gap-3 shrink-0">
            <button
              type="button"
              onClick={openCommandPalette}
              className="hidden sm:inline-flex items-center gap-2 rounded-full border border-black/[0.08] bg-white/[0.68] hover:bg-white/[0.88] hover:border-[#FFD100]/50 px-3 py-1.5 text-sm font-medium text-ink-500 hover:text-ink-800 transition-colors backdrop-blur"
              title="打开命令面板（场景 / 模式 / 用户切换）"
            >
              <span>命令</span>
              <span className="kbd">⌘</span>
              <span className="kbd">K</span>
            </button>
            <UserSwitcher autoOpenOnMount />
            <PlannerModeBadge />
            <MockModeBadge />
            <OfflineReadyBadge />
            <button
              className="inline-flex items-center rounded-full border border-[#FFD100]/45 bg-[#FFD100]/[0.16] px-3.5 py-1.5 text-sm font-bold text-ink-900 shadow-sm backdrop-blur transition hover:border-[#e6bc00]/60 hover:bg-[#FFD100]/28 active:scale-[0.98]"
              onClick={startNewSession}
              title="开新会话（保留之前会话历史，可在命令面板切换）"
            >
              新会话
            </button>
          </div>
        </div>
      </header>

      {/* 协作状态条 */}
      <CollabBar />

      {/* ============================================================
          初始态：偏好画像 + 演示场景 + 输入栏 垂直居中
          激活态：正常布局（偏好画像 + 演示场景顶部 + 两栏主区）
          ============================================================ */}
      <main
        className={cn(
          "relative-content mx-auto max-w-7xl px-4 sm:px-6 transition-all duration-1000 ease-[cubic-bezier(0.25,0.1,0.25,1)]",
          activated
            ? "py-4 sm:py-6"
            : "min-h-[calc(100vh-56px)] flex flex-col items-center justify-center py-8",
        )}
        style={activated ? { paddingBottom: "calc(112px + env(safe-area-inset-bottom, 0px) + 16px)" } : undefined}
      >
        {/* 偏好画像 + 演示场景容器：初始态居中放大，激活态正常 */}
        <div
          className={cn(
            "w-full transition-all duration-1000 ease-[cubic-bezier(0.25,0.1,0.25,1)]",
            !activated && "max-w-2xl",
          )}
        >
          <PreferencesPanel />

          <div className="mt-4">
            <QuickScenarios enlarged={!activated} />
          </div>
        </div>

        {/* 初始态：ChatDock 跟随内容流（static） */}
        {!activated && (
          <div className="w-full max-w-2xl mt-6">
            <ChatDock activated={false} />
          </div>
        )}

        {/* 激活态：两栏主区 */}
        <div
          className={cn(
            "mt-4 grid grid-cols-1 lg:grid-cols-4 gap-4 transition-all duration-1000 ease-[cubic-bezier(0.25,0.1,0.25,1)]",
            !activated && "opacity-0 pointer-events-none h-0 overflow-hidden mt-0",
          )}
        >
          <section className="order-1 lg:order-2 lg:col-span-3 space-y-3">
            <ItineraryUtilityBar
              onOpenShareModal={() => setShareModalOpen(true)}
            />
            <ItineraryCard />
          </section>

          <section className="order-2 lg:order-1 lg:col-span-1">
            <ConstraintFeed />
            <ToolTracePanel />
            <ThoughtPanel />
            <DecisionTraceCard trace={decisionTrace} />
          </section>
        </div>
      </main>

      <footer
        className={cn(
          "relative-content mx-auto max-w-7xl px-4 sm:px-6 pb-2 text-center transition-all duration-1000 ease-[cubic-bezier(0.25,0.1,0.25,1)]",
          !activated && "opacity-0 pointer-events-none",
        )}
      >
        <span className="text-xs text-ink-400/70 tracking-tight">
          美团 AI Hackathon 06 · 本地探索 · 周末闲时活动规划
        </span>
      </footer>

      <ToastStack />
      <CommandPalette />
      <Confetti />
      {/* 激活态：ChatDock fixed 贴底 */}
      {activated && <ChatDock activated={true} />}
      {roomId && (
        <ShareModal
          open={shareModalOpen}
          onClose={() => setShareModalOpen(false)}
          roomId={roomId}
        />
      )}
    </div>
  );
}

