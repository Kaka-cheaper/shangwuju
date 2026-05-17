"use client";

/**
 * ChatDock —— 底栏玻璃质感方形浮窗（替代左侧固定 ChatPanel 列）。
 *
 * 设计动机（对应 problem.md 问题 15）：
 *   - 左侧 ChatPanel 大部分时间在 idle，与右侧主内容（行程 / 链路 / 偏好）严重不对称
 *   - 历史是 idle 内容（看一遍即过），输入框是 active 入口（评委即兴扔输入）
 *   - 把这两个需求分离：输入框常驻底栏，历史折叠在「展开」按钮里
 *
 * 三态：
 *   - collapsed（默认 96px）：最新 agent 消息预览 + 输入框 + 「展开」按钮
 *   - peek（streaming 自动，340px）：peek 区显 chitchat / 最新 1-2 条消息，输入框仍在
 *   - drawer（点「展开历史」）：滑出 full-screen 浮窗显完整 timeline + intent + thoughts
 *
 * 视觉范式：玻璃方形浮窗
 *   - bg rgba(20,20,23,0.82) + backdrop-blur(18px) saturate(150%)
 *   - 顶部 1px 暖色发光线（与顶栏 scrolled 状态呼应）
 *   - 圆角仅顶部，底部贴屏；左右留 sm:24px 让浮窗"浮起来"
 *
 * 范式参考：ChatGPT desktop / Cursor / Linear AI / Claude code 都是底栏 sticky chat。
 */

import { useEffect, useRef, useState } from "react";

import { Icons } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

import ChitchatBubble from "./ChitchatBubble";
import IntentSummary from "./IntentSummary";

type DockMode = "collapsed" | "peek" | "drawer";

export default function ChatDock() {
  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);
  const streamError = useChatStore((s) => s.streamError);
  const intent = useChatStore((s) => s.intent);
  const thoughts = useChatStore((s) => s.thoughts);
  const chitchatReplies = useChatStore((s) => s.chitchatReplies);
  const sendMessage = useChatStore((s) => s.sendMessage);

  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<DockMode>("collapsed");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // ============================================================
  // 拖动调整高度（A1）
  //   - manualHeight=null：跟随 mode 自动高度（112 / 340）
  //   - manualHeight=数字：用户拖过，覆盖自动逻辑
  //   - 拖到 < 130 触发 snap → manualHeight=null + mode=collapsed
  //   - 拖到 > viewport*0.7 截断
  // ============================================================
  const [manualHeight, setManualHeight] = useState<number | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

  const onDragStart = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startH =
      manualHeight ?? (mode === "peek" ? 340 : 112);
    dragRef.current = { startY: e.clientY, startH };
    setIsDragging(true);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };

  useEffect(() => {
    if (!isDragging) return;
    const onMove = (e: PointerEvent) => {
      const start = dragRef.current;
      if (!start) return;
      const delta = start.startY - e.clientY; // 向上拖 → 增高
      const maxH = Math.max(260, Math.floor(window.innerHeight * 0.7));
      const next = Math.min(maxH, Math.max(96, start.startH + delta));
      setManualHeight(next);
    };
    const onUp = () => {
      const cur = manualHeight;
      // snap：拖到接近 collapsed → 释放回自动模式
      if (cur != null && cur < 130) {
        setManualHeight(null);
        setMode("collapsed");
      }
      setIsDragging(false);
      dragRef.current = null;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [isDragging, manualHeight]);

  // 双击 handle 重置高度（manualHeight=null）
  const onHandleDoubleClick = () => {
    setManualHeight(null);
  };

  // 实际渲染高度
  const renderHeight =
    manualHeight != null
      ? manualHeight
      : mode === "peek"
        ? 340
        : 112;

  // streaming 时自动 peek（让评委看到 agent_thought / chitchat 流）
  // streaming 结束后回到用户最近选的状态（drawer 不动 / 否则 collapsed）
  useEffect(() => {
    if (streaming) {
      setMode((cur) => (cur === "drawer" ? "drawer" : "peek"));
    } else {
      // streaming 结束 1.6s 后自动收起（让用户先看到 agent 总结消息）
      // 但如果用户主动展开过 drawer，保持 drawer 不动
      const timer = setTimeout(() => {
        setMode((cur) => (cur === "drawer" ? "drawer" : "collapsed"));
      }, 1600);
      return () => clearTimeout(timer);
    }
  }, [streaming]);

  // ESC 关闭 drawer
  useEffect(() => {
    if (mode !== "drawer") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMode("collapsed");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode]);

  const submit = () => {
    if (!draft.trim()) return;
    sendMessage(draft);
    setDraft("");
  };

  // 取最新一条 agent 消息（折叠态显示）
  const latestAgent = [...messages].reverse().find((m) => m.role === "agent");
  // 取最新一条 chitchat 气泡（折叠态/peek 态可见）
  const latestChitchat = chitchatReplies[chitchatReplies.length - 1];
  const userMsgCount = messages.filter((m) => m.role === "user").length;
  const totalCount = messages.length + chitchatReplies.length;

  return (
    <>
      {/* Drawer 态：full-screen 浮窗 */}
      {mode === "drawer" && (
        <ChatDrawer onClose={() => setMode("collapsed")} />
      )}

      {/* 底栏（始终存在，drawer 态时隐入 drawer 之下） */}
      <div
        className={cn(
          "dock-glass fixed left-0 right-0 bottom-0 z-30",
          isDragging
            ? "transition-none"
            : "transition-[height,opacity] duration-300 ease-out",
          mode === "drawer" && "opacity-0 pointer-events-none",
        )}
        style={{
          height: `${renderHeight}px`,
        }}
      >
        {/* Drag handle：顶部 8px 横条，向上 / 向下拖改高度 */}
        <div
          className={cn(
            "dock-handle absolute top-0 left-0 right-0 h-2 cursor-ns-resize",
            "flex items-center justify-center group",
            "select-none touch-none z-10",
          )}
          onPointerDown={onDragStart}
          onDoubleClick={onHandleDoubleClick}
          role="separator"
          aria-orientation="horizontal"
          aria-label="拖动调整对话窗口高度（双击重置）"
          title="拖动调整高度 · 双击重置"
        >
          <span
            className={cn(
              "block w-12 h-1 rounded-full",
              "bg-white/[0.08] group-hover:bg-white/[0.18] transition-colors",
              isDragging && "bg-brand-400/60",
            )}
          />
        </div>

        {/* 顶部暖色发光线（streaming 时常显，否则只在 hover 时显） */}
        <div
          aria-hidden
          className={cn(
            "absolute top-0 left-0 right-0 h-px",
            streaming ? "shimmer-bar" : "dock-edge-glow",
          )}
        />

        <div className="mx-auto max-w-7xl h-full px-4 sm:px-6 flex flex-col">
          {/* peek 区：streaming 时显 chitchat + agent_thought 打字流；
              手动拖大时（renderHeight > 180）也显示 */}
          {(mode === "peek" || (manualHeight != null && manualHeight > 180)) && (
            <div className="flex-1 min-h-0 overflow-y-auto pt-3 pb-2 space-y-3 animate-fade-in">
              {/* 手动拖大且无 streaming 内容时：展示最近对话预览 */}
              {!streaming &&
                !latestChitchat &&
                thoughts.length === 0 &&
                manualHeight != null &&
                manualHeight > 180 && (
                  <div className="space-y-2.5">
                    {messages.length === 0 ? (
                      <div className="flex flex-col items-center justify-center text-center gap-2 py-6 text-ink-500">
                        <Icons.spark
                          className="w-5 h-5 text-brand-400/60"
                          strokeWidth={1.5}
                        />
                        <span className="text-sm">还没有对话</span>
                        <span className="text-xs">
                          说一句你下午想做什么，或点上方的演示场景
                        </span>
                      </div>
                    ) : (
                      messages.slice(-6).map((m) => (
                        <div
                          key={m.id}
                          className={cn(
                            "flex animate-fade-in-up",
                            m.role === "user" ? "justify-end" : "justify-start",
                          )}
                        >
                          <div
                            className={cn(
                              "max-w-[80%] rounded-xl px-3 py-2 text-[13px] leading-relaxed tracking-tight",
                              m.role === "user"
                                ? "rounded-br-sm text-white"
                                : "bg-white/[0.04] border border-white/[0.08] text-ink-800 rounded-bl-sm",
                            )}
                            style={
                              m.role === "user"
                                ? {
                                    background:
                                      "linear-gradient(135deg, #f97316 0%, #ec4899 100%)",
                                  }
                                : undefined
                            }
                          >
                            {m.text}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                )}

              {/* 暖心回话气泡（最新一条优先，超过 1 条点展开看全部） */}
              {latestChitchat && (
                <ChitchatBubble payload={latestChitchat.payload} />
              )}

              {/* intent 摘要（streaming 早期） */}
              {intent && <IntentSummary intent={intent} />}

              {/* agent_thought 打字流 */}
              {thoughts.length > 0 && (
                <div className="space-y-1.5">
                  {thoughts.slice(-3).map((t) => (
                    <div
                      key={t.seq}
                      className="flex items-start gap-1.5 text-xs text-ink-500 px-1 italic animate-fade-in-up"
                    >
                      <Icons.thinking
                        className="w-3 h-3 mt-0.5 text-brand-400 shrink-0 animate-spin"
                        strokeWidth={2}
                      />
                      <span>{t.text}</span>
                    </div>
                  ))}
                </div>
              )}

              {streamError && (
                <div className="flex items-start gap-2 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                  <Icons.warn
                    className="w-3.5 h-3.5 mt-0.5 shrink-0"
                    strokeWidth={2}
                  />
                  <span>流出错：{streamError}</span>
                </div>
              )}
            </div>
          )}

          {/* collapsed 态：最新 agent 消息单行预览（仅在小高度时显示） */}
          {mode === "collapsed" &&
            (manualHeight == null || manualHeight <= 180) &&
            (latestChitchat || latestAgent) && (
            <button
              type="button"
              onClick={() => setMode("drawer")}
              className="w-full pt-2.5 pb-1 text-left animate-fade-in group"
              title="点击查看完整对话历史"
            >
              <div className="flex items-start gap-2 text-xs">
                <span className="shrink-0 mt-0.5 text-[10px] text-ink-500 tracking-wider uppercase">
                  Agent
                </span>
                <span className="flex-1 text-ink-700 group-hover:text-ink-900 transition-colors line-clamp-1 tracking-tight">
                  {latestChitchat
                    ? latestChitchat.payload.reply_text
                    : latestAgent?.text}
                </span>
                <Icons.copy
                  className="w-3 h-3 mt-0.5 text-ink-500 group-hover:text-ink-700 shrink-0 hidden sm:inline"
                  strokeWidth={2}
                />
              </div>
            </button>
          )}

          {/* 输入区（始终在底部） */}
          <div className="pt-2 pb-3 flex items-end gap-2">
            {/* 历史按钮：左侧 */}
            <button
              type="button"
              onClick={() => setMode(mode === "drawer" ? "collapsed" : "drawer")}
              disabled={totalCount === 0}
              className={cn(
                "shrink-0 inline-flex items-center gap-1.5 h-[44px] px-3 rounded-md",
                "border border-white/[0.08] bg-white/[0.04] text-xs text-ink-700",
                "hover:bg-white/[0.08] hover:border-white/[0.16] hover:text-ink-900",
                "transition-colors disabled:opacity-40 disabled:cursor-not-allowed",
                "tracking-tight",
              )}
              title="展开完整对话历史"
            >
              <Icons.spark className="w-3.5 h-3.5" strokeWidth={2} />
              <span className="hidden sm:inline">历史</span>
              {totalCount > 0 && (
                <span className="mono text-[10px] text-ink-500 tabular-nums">
                  {totalCount}
                </span>
              )}
            </button>

            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder={
                streaming
                  ? "Agent 正在规划，稍候..."
                  : userMsgCount === 0
                    ? "说一句你下午想做什么... (Enter 发送 · Shift+Enter 换行)"
                    : "继续对话或反馈... (Enter 发送)"
              }
              disabled={streaming}
              rows={mode === "peek" ? 2 : 1}
              className={cn(
                "flex-1 resize-none rounded-md border bg-white/[0.04]",
                "border-white/[0.08] hover:border-white/[0.16]",
                "px-3 py-2.5 text-sm text-ink-900 placeholder:text-ink-500 tracking-tight",
                "focus:outline-none focus:ring-2 focus:ring-brand-500/30 focus:border-brand-500/40",
                "transition-[border-color,box-shadow] duration-150",
                "disabled:bg-white/[0.02] disabled:text-ink-500",
              )}
            />

            <button
              className={cn(
                "btn-primary h-[44px] min-w-[80px] shrink-0",
                streaming && "shimmer-border",
              )}
              onClick={submit}
              disabled={streaming || !draft.trim()}
            >
              {streaming ? (
                <>
                  <Icons.thinking
                    className="w-3.5 h-3.5 animate-spin"
                    strokeWidth={2}
                  />
                  <span className="hidden sm:inline">规划中</span>
                </>
              ) : (
                <span>发送</span>
              )}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// ============================================================
// Drawer 态：full-screen 浮窗，渲染完整 timeline + intent + thoughts
// ============================================================

function ChatDrawer({ onClose }: { onClose: () => void }) {
  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);
  const streamError = useChatStore((s) => s.streamError);
  const intent = useChatStore((s) => s.intent);
  const thoughts = useChatStore((s) => s.thoughts);
  const chitchatReplies = useChatStore((s) => s.chitchatReplies);
  const sendMessage = useChatStore((s) => s.sendMessage);

  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  type TimelineItem =
    | { kind: "msg"; id: string; ts: number; role: "user" | "agent"; text: string }
    | {
        kind: "chitchat";
        id: string;
        ts: number;
        payload: (typeof chitchatReplies)[number]["payload"];
      };

  const timeline: TimelineItem[] = [
    ...messages.map(
      (m): TimelineItem => ({
        kind: "msg",
        id: m.id,
        ts: m.createdAt,
        role: m.role,
        text: m.text,
      }),
    ),
    ...chitchatReplies.map(
      (r): TimelineItem => ({
        kind: "chitchat",
        id: r.id,
        ts: r.receivedAtMs,
        payload: r.payload,
      }),
    ),
  ].sort((a, b) => a.ts - b.ts);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [timeline.length, thoughts.length, streaming]);

  const submit = () => {
    if (!draft.trim()) return;
    sendMessage(draft);
    setDraft("");
  };

  return (
    <div
      className="fixed inset-0 z-[35] flex items-end justify-center animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-label="完整对话历史"
    >
      {/* 背景遮罩 */}
      <button
        type="button"
        aria-label="关闭历史"
        onClick={onClose}
        className="absolute inset-0 bg-black/55 backdrop-blur-sm"
      />

      {/* 浮窗 */}
      <div
        className={cn(
          "relative w-full max-w-3xl mx-4 mb-2 sm:mb-4",
          "rounded-xl border border-white/[0.08] overflow-hidden",
          "shadow-elevated animate-drawer-slide-up",
          "flex flex-col",
        )}
        style={{
          background: "rgba(20, 20, 23, 0.92)",
          backdropFilter: "blur(20px) saturate(150%)",
          WebkitBackdropFilter: "blur(20px) saturate(150%)",
          maxHeight: "min(80vh, 720px)",
        }}
      >
        {streaming && (
          <div
            aria-hidden
            className="absolute top-0 left-0 right-0 h-px shimmer-bar"
          />
        )}

        {/* Header */}
        <div className="px-5 py-3 border-b border-white/[0.06] flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <Icons.spark
              className={cn(
                "w-3.5 h-3.5 transition-colors",
                streaming ? "text-brand-400" : "text-ink-700",
              )}
              strokeWidth={2}
            />
            <span className="text-[12px] font-medium text-ink-900 tracking-tight">
              对话历史
            </span>
            {streaming && (
              <span className="text-[11px] text-brand-400 ml-1">
                · 规划中
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-ink-500 hover:text-ink-900 transition-colors"
            aria-label="关闭"
            title="ESC 关闭"
          >
            <Icons.close className="w-4 h-4" strokeWidth={2} />
          </button>
        </div>

        {/* 滚动主体 */}
        <div
          ref={scrollRef}
          className="flex-1 min-h-0 overflow-y-auto px-5 py-4 space-y-4"
        >
          {timeline.length === 0 && !streaming && (
            <div className="h-full flex flex-col items-center justify-center text-center gap-2.5 py-12">
              <div className="w-12 h-12 rounded-full bg-gradient-to-br from-brand-500/20 to-accent-500/20 flex items-center justify-center border border-brand-500/30">
                <Icons.spark
                  className="w-5 h-5 text-brand-400"
                  strokeWidth={1.5}
                />
              </div>
              <div className="text-sm text-ink-700">
                还没有对话，先关掉窗口扔一句给我吧
              </div>
            </div>
          )}

          {timeline.map((item) =>
            item.kind === "msg" ? (
              <MessageBubble key={item.id} role={item.role} text={item.text} />
            ) : (
              <ChitchatBubble key={item.id} payload={item.payload} />
            ),
          )}

          {streaming && intent && <IntentSummary intent={intent} />}

          {streaming && thoughts.length > 0 && (
            <div className="space-y-1.5">
              {thoughts.map((t) => (
                <div
                  key={t.seq}
                  className="flex items-start gap-1.5 text-xs text-ink-500 px-1 italic animate-fade-in-up"
                >
                  <Icons.thinking
                    className="w-3 h-3 mt-0.5 text-brand-400 shrink-0 animate-spin"
                    strokeWidth={2}
                  />
                  <span>{t.text}</span>
                </div>
              ))}
            </div>
          )}

          {streamError && (
            <div className="flex items-start gap-2 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
              <Icons.warn
                className="w-3.5 h-3.5 mt-0.5 shrink-0"
                strokeWidth={2}
              />
              <span>流出错：{streamError}</span>
            </div>
          )}
        </div>

        {/* 底部输入：drawer 内也可继续输入 */}
        <div className="border-t border-white/[0.06] px-5 py-3 shrink-0">
          <div className="flex items-end gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder={
                streaming
                  ? "Agent 正在规划，稍候..."
                  : "继续对话或反馈... (Enter 发送)"
              }
              disabled={streaming}
              rows={2}
              className={cn(
                "flex-1 resize-none rounded-md border bg-white/[0.04]",
                "border-white/[0.08] hover:border-white/[0.16]",
                "px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 tracking-tight",
                "focus:outline-none focus:ring-2 focus:ring-brand-500/30 focus:border-brand-500/40",
                "transition-colors duration-150",
                "disabled:bg-white/[0.02] disabled:text-ink-500",
              )}
            />
            <button
              className={cn(
                "btn-primary h-[44px] min-w-[88px]",
                streaming && "shimmer-border",
              )}
              onClick={submit}
              disabled={streaming || !draft.trim()}
            >
              {streaming ? (
                <>
                  <Icons.thinking
                    className="w-3.5 h-3.5 animate-spin"
                    strokeWidth={2}
                  />
                  <span>规划中</span>
                </>
              ) : (
                <span>发送</span>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// MessageBubble：与 ChatPanel 一致，方便后期把 ChatPanel 整体退役
// ============================================================

function MessageBubble({
  role,
  text,
}: {
  role: "user" | "agent";
  text: string;
}) {
  const isUser = role === "user";
  return (
    <div
      className={cn(
        "flex animate-fade-in-up",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed tracking-tight",
          isUser
            ? "rounded-br-sm text-white shadow-glow"
            : "bg-white/[0.04] border border-white/[0.08] text-ink-800 rounded-bl-sm backdrop-blur-sm",
        )}
        style={
          isUser
            ? {
                background: "linear-gradient(135deg, #f97316 0%, #ec4899 100%)",
              }
            : undefined
        }
      >
        {text}
      </div>
    </div>
  );
}
