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
import { useCollabStore } from "@/lib/collab-store";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

import ChitchatBubble from "./ChitchatBubble";
import IntentSummary from "./IntentSummary";

type DockMode = "collapsed" | "peek";

// 高度档位（贴底紧凑，无空白 padding）
const HEIGHT_COLLAPSED_BASE = 76; // 仅输入框（无 agent 预览）
const HEIGHT_COLLAPSED_WITH_PREVIEW = 140; // 含箭头 + agent 单行预览 + 输入框
const HEIGHT_PEEK = 360; // 自动展开默认高度
const HEIGHT_FULL_RATIO = 0.7; // viewport * 0.7
// snap 与 timeline 阈值同点 → 消除中间空白态：< 阈值吸到 collapsed，>= 阈值展开 timeline
const SNAP_AND_TIMELINE_THRESHOLD = 160;
const SHOW_INTENT_THRESHOLD = 240;

export default function ChatDock({ activated = true }: { activated?: boolean }) {
  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);
  const streamError = useChatStore((s) => s.streamError);
  const intent = useChatStore((s) => s.intent);
  const thoughts = useChatStore((s) => s.thoughts);
  const chitchatReplies = useChatStore((s) => s.chitchatReplies);
  const sendMessage = useChatStore((s) => s.sendMessage);
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
        const fullH = Math.max(
          260,
          Math.floor(window.innerHeight * HEIGHT_FULL_RATIO),
        );
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
        const fullH = Math.max(
          260,
          Math.floor(window.innerHeight * HEIGHT_FULL_RATIO),
        );
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
    // 协作模式下走 WS 通道（所有人同步看到）
    const { collabMode, sendConstraint } = useCollabStore.getState();
    if (collabMode) {
      sendConstraint(draft.trim());
      // 本地也追加一条用户消息到 messages（WS 广播会同步给其他人）
      useChatStore.setState((s) => ({
        messages: [
          ...s.messages,
          {
            id: `u-${Date.now()}`,
            role: "user" as const,
            text: draft.trim(),
            createdAt: Date.now(),
          },
        ],
      }));
    } else {
      sendMessage(draft);
    }
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
      // 拉到 70vh
      const fullH = Math.max(
        260,
        Math.floor(window.innerHeight * HEIGHT_FULL_RATIO),
      );
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
      style={activated ? { height: `${renderHeight}px` } : undefined}
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
                className="relative flex-1 min-h-0 overflow-y-auto pt-3 pb-2 px-4 animate-fade-in rounded-2xl bg-black/[0.03] backdrop-blur-sm border border-black/[0.06] shadow-elevated mb-2"
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
                        className="w-5 h-5 text-brand-600/60"
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

                  {/* agent_thought 打字流 */}
                  {thoughts.length > 0 && (
                    <div className="space-y-1.5">
                      {thoughts.slice(-5).map((t, idx, arr) => {
                        const isLatest = idx === arr.length - 1;
                        const inProgress = streaming && isLatest;
                        return (
                          <div
                            key={t.seq}
                            className="flex items-start gap-1.5 text-sm text-ink-500 px-1 animate-fade-in-up"
                          >
                            {inProgress ? (
                              <Icons.thinking
                                className="w-3 h-3 mt-0.5 text-brand-600 shrink-0 animate-spin"
                                strokeWidth={2}
                              />
                            ) : (
                              <span
                                className="w-3 h-3 mt-0.5 shrink-0 flex items-center justify-center"
                                aria-hidden
                              >
                                <span className="w-1 h-1 rounded-full bg-ink-500/60" />
                              </span>
                            )}
                            <span>{t.text}</span>
                          </div>
                        );
                      })}
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
                    "w-14 h-5 text-[#FFD100] group-hover:text-[#e6bc00] transition-all duration-200",
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
                <div className="flex items-center gap-2 text-sm rounded-full bg-black/[0.03] backdrop-blur-sm border border-black/[0.06] shadow-elevated px-2.5 py-1.5">
                  <span className="shrink-0 inline-flex items-center gap-1 bg-[#FFD100] text-ink-900 text-[11px] font-semibold px-2.5 py-1 rounded-full">
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
              "flex items-end gap-2 rounded-full border border-black/[0.08] bg-black/[0.03]",
              "backdrop-blur-sm shadow-elevated",
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
                  ? "Agent 正在规划，稍候..."
                  : userMsgCount === 0
                    ? "说一句你下午想做什么... (Enter 发送 · Shift+Enter 换行)"
                    : "继续对话或反馈... (Enter 发送)"
              }
              disabled={streaming}
              rows={1}
              className={cn(
                "flex-1 resize-none bg-transparent border-0",
                "py-2 text-sm text-ink-900 placeholder:text-ink-500 tracking-tight",
                "focus:outline-none",
                "disabled:text-ink-500",
              )}
            />

            <button
              className={cn(
                "btn-primary h-[36px] min-w-[72px] shrink-0 rounded-full",
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
            ? "rounded-br-sm text-ink-900 shadow-glow"
            : "bg-white border border-black/[0.06] text-ink-800 rounded-bl-sm shadow-sm",
        )}
        style={
          isUser
            ? {
                background: "#FFD100",
              }
            : undefined
        }
      >
        {text}
      </div>
    </div>
  );
}
