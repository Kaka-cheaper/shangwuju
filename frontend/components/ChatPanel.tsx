"use client";

import { useEffect, useRef, useState } from "react";

import { Icons } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

import ChitchatBubble from "./ChitchatBubble";
import IntentSummary from "./IntentSummary";

/** 聊天主面板：消息流 + 输入框（B+D 范式，去 emoji + 单色高亮）。 */
export default function ChatPanel() {
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
  }, [messages.length, thoughts.length, chitchatReplies.length, streaming]);

  const submit = () => {
    if (!draft.trim()) return;
    sendMessage(draft);
    setDraft("");
  };

  return (
    <div className="card flex flex-col h-[520px] sm:h-[640px] relative overflow-hidden">
      {streaming && (
        <div
          aria-hidden
          className="absolute top-0 left-0 right-0 h-px shimmer-bar z-10"
        />
      )}
      <div className="px-4 py-3 border-b border-ink-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="section-title">对话</span>
        </div>
        {streaming ? (
          <div className="flex items-center gap-1.5 text-[11px] text-accent-700">
            <Icons.thinking className="w-3 h-3 animate-spin" strokeWidth={2.5} />
            <span>Agent 正在规划</span>
          </div>
        ) : (
          <span className="text-[11px] text-ink-400">等待输入</span>
        )}
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {timeline.length === 0 && !streaming && (
          <div className="h-full flex flex-col items-center justify-center text-center text-ink-400 gap-2">
            <Icons.spark className="w-7 h-7 text-ink-300" strokeWidth={1.5} />
            <div className="text-sm text-ink-500">
              点上方场景按钮，或输入一句话开始
            </div>
            <div className="text-xs text-ink-400">
              比如：今天下午想和老婆孩子出去玩几个小时...
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

        {streaming && intent && (
          <div className="space-y-2">
            <IntentSummary intent={intent} />
          </div>
        )}

        {streaming && thoughts.length > 0 && (
          <div className="space-y-1.5">
            {thoughts.map((t) => (
              <div
                key={t.seq}
                className="flex items-start gap-1.5 text-xs text-ink-500 px-1 italic"
              >
                <Icons.thinking
                  className="w-3 h-3 mt-0.5 text-ink-400 shrink-0 animate-spin"
                  strokeWidth={2}
                />
                <span>{t.text}</span>
              </div>
            ))}
          </div>
        )}

        {streamError && (
          <div className="flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
            <Icons.warn className="w-3.5 h-3.5 mt-0.5 shrink-0" strokeWidth={2} />
            <span>流出错：{streamError}</span>
          </div>
        )}
      </div>

      <div className="border-t border-ink-200 p-3">
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
            placeholder="说一句你下午想做什么... (Enter 发送 · Shift+Enter 换行)"
            disabled={streaming}
            rows={2}
            className={cn(
              "flex-1 resize-none rounded-md border border-ink-200 bg-white",
              "px-3 py-2 text-sm text-ink-800 placeholder:text-ink-400 tracking-tight",
              "focus:outline-none focus:ring-2 focus:ring-accent-500/30 focus:border-accent-500",
              "transition-colors duration-150",
              "disabled:bg-ink-50 disabled:text-ink-500",
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
                <Icons.thinking className="w-3.5 h-3.5 animate-spin" />
                <span>规划中</span>
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

function MessageBubble({ role, text }: { role: "user" | "agent"; text: string }) {
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
            ? "bg-ink-900 text-white rounded-br-sm"
            : "bg-white border border-ink-200 text-ink-800 rounded-bl-sm",
        )}
      >
        {text}
      </div>
    </div>
  );
}
