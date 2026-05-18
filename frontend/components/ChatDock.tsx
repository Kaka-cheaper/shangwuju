"use client";

/**
 * ChatDock —— 单一对话框（连续高度，无 drawer 弹窗）。
 *
 * 设计动机（对应 problem.md 问题 21）：
 *   旧版本 drawer 浮窗与「peek 拖大到 70vh」职能完全重叠：
 *     - 输入框 / 发送按钮被实现两遍
 *     - chitchat / intent / thoughts 渲染两遍
 *     - 用户既能拖大又能点展开 = 两条路径解决同一需求
 *   现在统一为单一 dock，4 段连续高度无缝切换：
 *     96px  折叠态：单行 agent 预览 + 输入框
 *     180px 中等：+ 最近 chitchat / thoughts
 *     340px 大：+ intent 摘要 + 最近 N 条对话
 *     70vh  全开：完整 timeline（messages + chitchatReplies 时序合并）
 *
 *   交互入口：
 *     - 拖动顶部 handle 在任意高度间无缝切换
 *     - 双击 handle 重置到自动尺寸
 *     - 「展开 N」按钮 toggle：折叠 ↔ 70vh
 *     - streaming 自动跳到 peek（让评委看到 agent 中间过程）
 *
 * 视觉范式：玻璃方形浮窗
 *   - bg rgba(20,20,23,0.82) + backdrop-blur(18px) saturate(150%)
 *   - 顶部 1px 暖色发光线（与顶栏 scrolled 状态呼应）
 *   - 圆角仅顶部，底部贴屏；左右留 sm:24px 让浮窗"浮起来"
 *
 * 范式参考：ChatGPT desktop / Cursor / Linear AI / Claude code 都是底栏 sticky chat。
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { Icons } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

import ChitchatBubble from "./ChitchatBubble";
import IntentSummary from "./IntentSummary";

type DockMode = "collapsed" | "peek";

// 高度档位（贴底紧凑，无空白 padding）
const HEIGHT_COLLAPSED_BASE = 76; // 仅输入框（无 agent 预览）
const HEIGHT_COLLAPSED_WITH_PREVIEW = 104; // 含 agent 单行预览
const HEIGHT_PEEK = 360; // 自动展开默认高度
const HEIGHT_FULL_RATIO = 0.7; // viewport * 0.7
// snap 与 timeline 阈值同点 → 消除中间空白态：< 阈值吸到 collapsed，>= 阈值展开 timeline
const SNAP_AND_TIMELINE_THRESHOLD = 160;
const SHOW_INTENT_THRESHOLD = 240;

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
  const timelineScrollRef = useRef<HTMLDivElement>(null);

  // ============================================================
  // 拖动调整高度
  //   - manualHeight=null：跟随 mode 自动高度（112 / 340）
  //   - manualHeight=数字：用户拖过，覆盖自动逻辑
  //   - 拖到 < 130 触发 snap → manualHeight=null + mode=collapsed
  //   - 拖到 > viewport*0.7 截断
  // ============================================================
  const [manualHeight, setManualHeight] = useState<number | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

  // 取最新一条 agent 消息（折叠态显示）
  const latestAgent = [...messages].reverse().find((m) => m.role === "agent");
  // 取最新一条 chitchat 气泡（折叠态/peek 态可见）
  const latestChitchat = chitchatReplies[chitchatReplies.length - 1];
  const userMsgCount = messages.filter((m) => m.role === "user").length;
  const totalCount = messages.length + chitchatReplies.length;

  // collapsed 态目标高度：有预览 → 104，无预览 → 76
  const hasCollapsedPreview = latestChitchat != null || latestAgent != null;
  const collapsedHeight = hasCollapsedPreview
    ? HEIGHT_COLLAPSED_WITH_PREVIEW
    : HEIGHT_COLLAPSED_BASE;

  const onDragStart = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startH =
      manualHeight ?? (mode === "peek" ? HEIGHT_PEEK : collapsedHeight);
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
      const maxH = Math.max(
        260,
        Math.floor(window.innerHeight * HEIGHT_FULL_RATIO),
      );
      const next = Math.min(maxH, Math.max(96, start.startH + delta));
      setManualHeight(next);
    };
    const onUp = () => {
      const cur = manualHeight;
      // snap：拖到 < 阈值 → 释放回 collapsed（吸附到底）
      // 阈值与 timeline 显示阈值同点：拖动接近底部时直接吸附，避免中间空白态
      if (cur != null && cur < SNAP_AND_TIMELINE_THRESHOLD) {
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
        ? HEIGHT_PEEK
        : collapsedHeight;

  // 是否显示 timeline 区域（peek 自动 / manualHeight >= 阈值）
  const showTimeline =
    mode === "peek" ||
    (manualHeight != null && manualHeight >= SNAP_AND_TIMELINE_THRESHOLD);

  // 是否显示 intent 摘要（仅在大尺寸 + 流式或拖大时）
  const showIntent =
    intent != null &&
    (mode === "peek" ||
      (manualHeight != null && manualHeight >= SHOW_INTENT_THRESHOLD));

  // streaming 时自动 peek（让评委看到 agent_thought / chitchat 流）
  // streaming 结束 1.6s 后自动收回 collapsed（让用户先看到 agent 总结消息）
  useEffect(() => {
    if (streaming) {
      setMode((cur) => (cur === "peek" ? "peek" : "peek"));
    } else {
      const timer = setTimeout(() => {
        // 用户手动拖大过 → 不动，尊重用户
        if (manualHeight != null && manualHeight >= SNAP_AND_TIMELINE_THRESHOLD) {
          return;
        }
        setMode("collapsed");
      }, 1600);
      return () => clearTimeout(timer);
    }
    // 仅依赖 streaming（manualHeight 变化不应触发自动收回）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming]);

  // ESC 收回到 collapsed（用户从大尺寸快速收起）
  useEffect(() => {
    if (!showTimeline) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setManualHeight(null);
        setMode("collapsed");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showTimeline]);

  const submit = () => {
    if (!draft.trim()) return;
    sendMessage(draft);
    setDraft("");
  };

  // 时序合并（messages + chitchatReplies）—— peek 拖大态显示
  type TimelineItem =
    | { kind: "msg"; id: string; ts: number; role: "user" | "agent"; text: string }
    | {
        kind: "chitchat";
        id: string;
        ts: number;
        payload: (typeof chitchatReplies)[number]["payload"];
      };

  const timeline = useMemo<TimelineItem[]>(() => {
    const merged: TimelineItem[] = [
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
    ];
    return merged.sort((a, b) => a.ts - b.ts);
  }, [messages, chitchatReplies]);

  // 拖大态：新消息自动滚到底
  useEffect(() => {
    if (!showTimeline || !timelineScrollRef.current) return;
    timelineScrollRef.current.scrollTo({
      top: timelineScrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [showTimeline, timeline.length, thoughts.length, streaming]);

  // 「展开 N」按钮：toggle 70vh ↔ collapsed
  const onTogglePeek = () => {
    if (showTimeline) {
      // 已经在大尺寸 → 收回 collapsed
      setManualHeight(null);
      setMode("collapsed");
    } else {
      // 拉到 70vh
      const fullH = Math.max(
        260,
        Math.floor(window.innerHeight * HEIGHT_FULL_RATIO),
      );
      setManualHeight(fullH);
      setMode("peek");
    }
  };

  return (
    <div
      className={cn(
        "dock-glass fixed left-0 right-0 bottom-0 z-30",
        isDragging
          ? "transition-none"
          : "transition-[height] duration-300 ease-out",
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
        {/* Timeline / peek 区：流式 streaming 自动 / 手动拖大时显示 */}
        {showTimeline && (
          <div
            ref={timelineScrollRef}
            className="flex-1 min-h-0 overflow-y-auto pt-3 pb-2 animate-fade-in"
          >
            <div className="max-w-3xl mx-auto space-y-3">
              {/* 空态 */}
              {timeline.length === 0 && !streaming && (
                <div className="flex flex-col items-center justify-center text-center gap-2 py-8 text-ink-500">
                  <Icons.spark
                    className="w-5 h-5 text-brand-400/60"
                    strokeWidth={1.5}
                  />
                  <span className="text-sm">还没有对话</span>
                  <span className="text-xs">
                    说一句你下午想做什么，或点上方的演示场景
                  </span>
                </div>
              )}

              {/* 完整时序 timeline（messages 与 chitchatReplies 合并） */}
              {timeline.map((item) =>
                item.kind === "msg" ? (
                  <MessageBubble
                    key={item.id}
                    role={item.role}
                    text={item.text}
                  />
                ) : (
                  <ChitchatBubble key={item.id} payload={item.payload} />
                ),
              )}

              {/* intent 摘要（仅在拖到中等以上） */}
              {showIntent && intent && <IntentSummary intent={intent} />}

              {/* agent_thought 打字流 */}
              {thoughts.length > 0 && (
                <div className="space-y-1.5">
                  {thoughts.slice(-5).map((t) => (
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
          </div>
        )}

        {/* collapsed 态：最新 agent 消息单行预览 */}
        {!showTimeline && (latestChitchat || latestAgent) && (
          <button
            type="button"
            onClick={onTogglePeek}
            className="w-full pt-1.5 pb-0.5 text-left animate-fade-in group"
            title="点击展开完整对话历史"
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

        {/* 输入区（始终在底部，紧贴屏幕底） */}
        <div className="pt-1.5 pb-2 flex items-end gap-2">
          {/* 展开按钮：左侧（toggle 70vh ↔ collapsed） */}
          <button
            type="button"
            onClick={onTogglePeek}
            disabled={totalCount === 0 && !streaming}
            className={cn(
              "shrink-0 inline-flex items-center gap-1.5 h-[44px] px-3 rounded-md",
              "border bg-white/[0.04] text-xs",
              showTimeline
                ? "border-brand-400/40 text-brand-300 bg-brand-500/[0.08] hover:bg-brand-500/[0.12]"
                : "border-white/[0.08] text-ink-700 hover:bg-white/[0.08] hover:border-white/[0.16] hover:text-ink-900",
              "transition-colors disabled:opacity-40 disabled:cursor-not-allowed",
              "tracking-tight",
            )}
            title={showTimeline ? "收起对话窗口（ESC）" : "展开完整对话历史"}
            aria-label={showTimeline ? "收起对话" : "展开对话"}
          >
            <Icons.spark
              className={cn(
                "w-3.5 h-3.5 transition-transform",
                showTimeline && "rotate-180",
              )}
              strokeWidth={2}
            />
            <span className="hidden sm:inline">
              {showTimeline ? "收起" : "历史"}
            </span>
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
            rows={showTimeline ? 2 : 1}
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
  );
}

// ============================================================
// MessageBubble：用户 / agent 消息气泡
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
