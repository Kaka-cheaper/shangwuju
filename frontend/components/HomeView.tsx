"use client";

import { useEffect, useState } from "react";

import { useChatStore } from "@/lib/store";
import { useCollabStore } from "@/lib/collab-store";
import {
  generateSessionId,
  getUserIdFromCookie,
  upsertSession,
} from "@/lib/utils";
import { cn } from "@/lib/utils";

import ChatDock from "./ChatDock";
import CollabBar from "./CollabBar";
import CommandPalette from "./CommandPalette";
import Confetti from "./Confetti";
import ConstraintFeed from "./ConstraintFeed";
import ItineraryCard from "./ItineraryCard";
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
  const reset = useChatStore((s) => s.reset);
  const startNewSession = useChatStore((s) => s.startNewSession);
  const sessionId = useChatStore((s) => s.sessionId);
  const openCommandPalette = useChatStore((s) => s.openCommandPalette);
  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);

  // 是否已激活（用户发过消息或正在流式中）
  const activated = messages.length > 0 || streaming;

  // 顶栏滚动下沉：scrolled=true 时加深背景 + 暖色发光底线
  const [scrolled, setScrolled] = useState(false);

  // 协作模式
  const collabMode = useCollabStore((s) => s.collabMode);
  const roomId = useCollabStore((s) => s.roomId);
  const createRoom = useCollabStore((s) => s.createRoom);
  const joinRoom = useCollabStore((s) => s.joinRoom);
  const [shareModalOpen, setShareModalOpen] = useState(false);

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

  return (
    <div className="min-h-screen relative">
      {/* 黄昏光斑背景层（fixed，最底层） */}
      <div className="aurora-bg" aria-hidden />

      {/* 顶栏：始终显示 */}
      <header
        className={cn(
          "relative-content sticky top-0 z-20 border-b border-white/[0.06]",
          "bg-[#08080d]/70 backdrop-blur-xl",
          "transition-[background-color,box-shadow] duration-300",
          scrolled && "header-scrolled",
        )}
      >
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
            <MockModeBadge />
            <OfflineReadyBadge />
            <span
              className="hidden lg:inline mono text-[11px] text-ink-400 truncate max-w-[140px]"
              title={`当前会话 ID：${sessionId}`}
            >
              {sessionId}
            </span>
            <button
              className="btn-ghost"
              onClick={startNewSession}
              title="开新会话（保留之前会话历史，可在命令面板切换）"
            >
              + 新会话
            </button>
            <button className="btn-ghost" onClick={reset} title="清空当前会话历史">
              重置
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
          <section className="lg:col-span-1">
            <ConstraintFeed />
            <ToolTracePanel />
            <ThoughtPanel />
          </section>

          <section className="lg:col-span-3">
            <ItineraryCard />
          </section>
        </div>
      </main>

      <footer
        className={cn(
          "relative-content mx-auto max-w-7xl px-4 sm:px-6 pb-2 text-center transition-all duration-1000 ease-[cubic-bezier(0.25,0.1,0.25,1)]",
          !activated && "opacity-0 pointer-events-none",
        )}
      >
        <span className="text-[11px] text-ink-400/70 tracking-tight">
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
