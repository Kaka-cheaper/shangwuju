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
import { ArrowRight } from "lucide-react";

import { Icons } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { useCollabDispatch } from "@/lib/hooks/useCollabDispatch";
import { cn } from "@/lib/utils";

import ChitchatBubble from "./ChitchatBubble";
import IntentSummary from "./IntentSummary";

type DockMode = "collapsed" | "peek";

// 高度档位（贴底紧凑，无空白 padding）
const HEIGHT_COLLAPSED_BASE = 76; // 仅输入框（无 agent 预览）
const HEIGHT_COLLAPSED_WITH_PREVIEW = 140; // 含箭头 + agent 单行预览 + 输入框
const HEIGHT_PEEK = 760; // 展开态最高高度：历史内容在面板内滚动
const HEIGHT_EXPANDED_MAX = 760;
// snap 与 timeline 阈值同点 → 消除中间空白态：< 阈值吸到 collapsed，>= 阈值展开 timeline
const SNAP_AND_TIMELINE_THRESHOLD = 160;
const SHOW_INTENT_THRESHOLD = 240;

function getExpandedDockHeight() {
  if (typeof window === "undefined") return HEIGHT_PEEK;
  return Math.max(260, Math.min(HEIGHT_EXPANDED_MAX, window.innerHeight - 120));
}

export default function ChatDock({ activated = true }: { activated?: boolean }) {
  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);
  const streamError = useChatStore((s) => s.streamError);
  const intent = useChatStore((s) => s.intent);
  const thoughts = useChatStore((s) => s.thoughts);
  const chitchatReplies = useChatStore((s) => s.chitchatReplies);
  const { sendUserInput } = useCollabDispatch();
  // spec execution-quality-review M3：工具调用 badge 让评委在 dock 收起态也能看到
  // Agent 决策过程的统计（Tool 编排 25% 评分项的 demo 闭环可见性）
  const toolCallsCount = useChatStore((s) => s.toolCalls.length);
  const replansCount = useChatStore((s) => s.replans.length);

  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<DockMode>("collapsed");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const timelineScrollRef = useRef<HTMLDivElement>(null);

  // ============================================================
  // spec algorithm-redesign R6：localStorage 持久化 + Cmd+K 召唤
  // ============================================================
  // 默认 collapsed（避免 SSR hydration mismatch）；初次挂载后从 localStorage 读取
  // 用户在前一次 session 里手动展开过 → 本次启动直接展开
  // 评委教学：Cmd+K（Mac）/ Ctrl+K（Win）随时召唤 ChatDock 大尺寸
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const saved = window.localStorage.getItem("shangwuju.chatdock.expanded");
      if (saved === "true") {
        const fullH = getExpandedDockHeight();
        setManualHeight(fullH);
        setMode("peek");
      }
    } catch {
      // localStorage 不可用（隐私模式 / SSR）→ 静默忽略
    }
  }, []);

  // Cmd+K / Ctrl+K 召唤大尺寸 ChatDock
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        const fullH = getExpandedDockHeight();
        setManualHeight(fullH);
        setMode("peek");
        try {
          window.localStorage.setItem(
            "shangwuju.chatdock.expanded",
            "true",
          );
        } catch {
          // 静默忽略
        }
        // 自动 focus 输入框
        setTimeout(() => textareaRef.current?.focus(), 50);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
      const maxH = getExpandedDockHeight();
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
  const cappedRenderHeight = showTimeline
    ? Math.min(renderHeight, getExpandedDockHeight())
    : renderHeight;

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
        // spec algorithm-redesign R6：同步清除 localStorage 标记
        try {
          if (typeof window !== "undefined") {
            window.localStorage.setItem(
              "shangwuju.chatdock.expanded",
              "false",
            );
          }
        } catch {
          // 静默忽略
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showTimeline]);

  const submit = () => {
    if (!draft.trim()) return;
    // collabMode 分流（WS constraint 广播 vs 单人 HTTP sendMessage）统一在
    // useCollabDispatch 里实现，ChitchatBubble / MobileComposer 共用同一份。
    sendUserInput(draft);
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
      try {
        if (typeof window !== "undefined") {
          window.localStorage.setItem(
            "shangwuju.chatdock.expanded",
            "false",
          );
        }
      } catch {
        // 静默
      }
    } else {
      // 拉到固定最高高度，超出的对话历史在面板内滚动
      const fullH = getExpandedDockHeight();
      setManualHeight(fullH);
      setMode("peek");
      try {
        if (typeof window !== "undefined") {
          window.localStorage.setItem(
            "shangwuju.chatdock.expanded",
            "true",
          );
        }
      } catch {
        // 静默
      }
    }
  };

  return (
    <div
      className={cn(
        activated
          ? "fixed left-0 right-0 bottom-0 z-30"
          : "relative z-30 w-full",
        isDragging
          ? "transition-none"
          : "transition-all duration-1000 ease-[cubic-bezier(0.25,0.1,0.25,1)]",
      )}
      style={activated ? { height: `${cappedRenderHeight}px` } : undefined}
    >
      <div className={cn(
        "mx-auto max-w-7xl px-4 sm:px-6 flex flex-col",
        activated && "h-full",
      )}>
        {/* 输入区：历史按钮 + 右侧列（timeline/collapsed + 输入框） */}
        <div className={cn(
          "flex items-end gap-3 h-full",
          activated ? "pt-1.5 pb-2" : "pt-0",
        )}>
          {/* 右侧列：timeline/collapsed + 输入框，全宽 */}
          <div className="flex-1 flex flex-col min-w-0 min-h-0">
            {/* Timeline / peek 区：仅激活态显示 —— 毛玻璃容器 */}
            {activated && showTimeline && (
              <div
                ref={timelineScrollRef}
                className="relative flex-1 min-h-0 max-h-[620px] overflow-y-scroll overscroll-contain pt-3 pb-2 px-4 animate-fade-in rounded-[28px] bg-white/90 backdrop-blur-xl border border-black/[0.08] shadow-elevated mb-2"
              >
                {/* streaming 时顶部流动黄光带 */}
                {streaming && (
                  <div
                    aria-hidden
                    className="absolute top-0 left-0 right-0 h-px shimmer-bar z-10"
                  />
                )}
                <div className="space-y-3">
                  {/* 空态 */}
                  {timeline.length === 0 && !streaming && (
                    <div className="flex flex-col items-center justify-center text-center gap-2 py-8 text-ink-500">
                      <Icons.spark
                        className="w-5 h-5 text-ink-400"
                        strokeWidth={1.5}
                      />
                      <span className="text-sm">还没有对话</span>
                      <span className="text-xs">
                        说一句你下午想做什么，或点上方的演示场景
                      </span>
                    </div>
                  )}

                  {/* 完整时序 timeline */}
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

                  {/* intent 摘要 */}
                  {showIntent && intent && <IntentSummary intent={intent} />}

                  {/* 单思考面（信任带设计终稿 §修订4）：AI 幕后（TrustBelt）是唯一
                      思考面，这里不再铺 agent_thought 原始 rationale 列表，只留
                      一行"正在思考"脉冲，告诉用户 Agent 仍在工作。 */}
                  {streaming && thoughts.length > 0 && (
                    <div className="flex items-center gap-1.5 px-1 text-sm text-ink-500 animate-fade-in-up">
                      <span
                        className="inline-block h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent-500"
                        aria-hidden
                      />
                      AI 正在思考…
                    </div>
                  )}

                  {streamError && (
                    <div className="flex items-start gap-2 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-600">
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

            {/* 展开/折叠箭头指示器（黄色、加宽） */}
            {activated && (latestChitchat || latestAgent || showTimeline || streaming) && (
              <button
                type="button"
                onClick={onTogglePeek}
                className="w-full flex items-center justify-center py-1 group"
                title={showTimeline ? "收起对话历史" : "展开对话历史"}
                aria-label={showTimeline ? "收起" : "展开"}
              >
                <svg
                  className={cn(
                    "w-14 h-5 text-ink-400 group-hover:text-ink-600 transition-all duration-200",
                    showTimeline && "rotate-180",
                  )}
                  viewBox="0 0 56 20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M10 14L28 6L46 14" />
                </svg>
              </button>
            )}

            {/* collapsed 态：最新 agent 消息单行预览 —— 毛玻璃胶囊 */}
            {activated && !showTimeline && (latestChitchat || latestAgent) && (
              <button
                type="button"
                onClick={onTogglePeek}
                className="w-full pb-2 text-left animate-fade-in group"
                title="点击展开完整对话历史"
              >
                <div className="flex items-center gap-2 text-sm rounded-full bg-white/90 backdrop-blur-xl border border-black/[0.08] shadow-elevated px-3 py-2">
                  <span className="shrink-0 inline-flex items-center gap-1 bg-ink-900 text-white text-[11px] font-semibold px-2.5 py-1 rounded-full">
                    <Icons.spark className="w-3.5 h-3.5" strokeWidth={2} />
                    {totalCount > 0 && (
                      <span className="mono tabular-nums">{totalCount}</span>
                    )}
                  </span>
                  <span className="flex-1 text-ink-700 group-hover:text-ink-900 transition-colors line-clamp-1 tracking-tight">
                    {latestChitchat
                      ? latestChitchat.payload.reply_text
                      : latestAgent?.text}
                  </span>
                </div>
              </button>
            )}

            {/* 输入框：独立悬浮卡片 */}
            <div className={cn(
              "chat-input-breath group/chat-input flex items-end gap-2 rounded-full border border-black/[0.08] bg-black/[0.03]",
              "backdrop-blur-sm shadow-elevated transition-all duration-300 ease-out",
              "hover:border-accent-400/50 hover:bg-white hover:shadow-[0_14px_38px_-22px_rgba(17,24,39,0.45),0_0_0_4px_rgba(245,158,11,0.10)] hover:backdrop-blur-xl",
              "focus-within:border-accent-500/55 focus-within:bg-white focus-within:shadow-[0_14px_38px_-22px_rgba(17,24,39,0.45),0_0_0_4px_rgba(245,158,11,0.12)] focus-within:backdrop-blur-xl",
              "px-4 py-1.5",
            )}>
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
                  ? "Agent 正在规划，稍候~"
                  : userMsgCount === 0
                    ? "想去哪儿、和谁去、有什么小偏好，都可以随口告诉我~"
                    : "哪里安排得不太合适，告诉我，我来帮你改顺~"
              }
              disabled={streaming}
              rows={1}
              className={cn(
                "h-9 flex-1 resize-none bg-transparent border-0",
                "py-1.5 text-base leading-6 text-ink-900 placeholder:text-ink-500 tracking-tight",
                "focus:outline-none",
                "disabled:text-ink-500",
              )}
            />

            <button
              className={cn(
                "mr-[-0.35rem] grid h-9 w-9 min-w-0 shrink-0 place-items-center self-center rounded-full border border-black/[0.06] bg-white/75 p-0 text-[#d97706]",
                "shadow-[0_6px_18px_-12px_rgba(17,24,39,0.35)] transition-all duration-300 ease-out",
                "group-hover/chat-input:border-accent-600/50 group-hover/chat-input:bg-accent-500 group-hover/chat-input:text-white group-hover/chat-input:shadow-[0_10px_24px_-14px_rgba(245,158,11,0.85)]",
                "group-focus-within/chat-input:border-accent-600/50 group-focus-within/chat-input:bg-accent-500 group-focus-within/chat-input:text-white group-focus-within/chat-input:shadow-[0_10px_24px_-14px_rgba(245,158,11,0.85)]",
                "hover:scale-[1.03] active:scale-95 disabled:cursor-not-allowed disabled:opacity-70",
                streaming && "shimmer-border",
              )}
              onClick={submit}
              disabled={streaming || !draft.trim()}
              aria-label={streaming ? "正在规划" : "发送"}
              title={streaming ? "正在规划" : "发送"}
            >
              {streaming ? (
                <Icons.thinking
                  className="h-4 w-4 animate-spin"
                  strokeWidth={2}
                />
              ) : (
                <ArrowRight className="h-5 w-5" strokeWidth={2.75} />
              )}
            </button>
          </div>
          </div>
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
            ? "rounded-br-sm bg-ink-100 text-ink-900 shadow-sm"
            : "bg-white border border-black/[0.06] text-ink-800 rounded-bl-sm shadow-sm",
        )}
      >
        {text}
      </div>
    </div>
  );
}
