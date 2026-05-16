"use client";

import { useEffect, useRef, useState } from "react";

import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

import IntentSummary from "./IntentSummary";

/** 聊天主面板：消息流 + 输入框。 */
export default function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);
  const streamError = useChatStore((s) => s.streamError);
  const intent = useChatStore((s) => s.intent);
  const thoughts = useChatStore((s) => s.thoughts);
  const sendMessage = useChatStore((s) => s.sendMessage);

  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  // 新消息或思考时滚到底
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages.length, thoughts.length, streaming]);

  const submit = () => {
    if (!draft.trim()) return;
    sendMessage(draft);
    setDraft("");
  };

  return (
    <div className="card flex flex-col h-[640px]">
      <div className="px-4 py-3 border-b border-ink-200 flex items-center justify-between">
        <div className="text-sm font-medium text-ink-700">对话</div>
        <div
          className={cn(
            "text-xs",
            streaming ? "text-brand-600 animate-pulse-soft" : "text-ink-400",
          )}
        >
          {streaming ? "Agent 正在规划..." : "等待输入"}
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 && !streaming && (
          <div className="h-full flex flex-col items-center justify-center text-center text-ink-400">
            <div className="text-4xl mb-2">☀️</div>
            <div className="text-sm">点上方场景按钮，或输入一句话开始</div>
            <div className="text-xs mt-1">
              比如：今天下午想和老婆孩子出去玩几个小时...
            </div>
          </div>
        )}

        {messages.map((m) => (
          <MessageBubble key={m.id} role={m.role} text={m.text} />
        ))}

        {streaming && intent && (
          <div className="space-y-2">
            <IntentSummary intent={intent} />
          </div>
        )}

        {streaming && thoughts.length > 0 && (
          <div className="space-y-1">
            {thoughts.map((t) => (
              <div
                key={t.seq}
                className="text-xs text-ink-500 italic px-1"
              >
                💭 {t.text}
              </div>
            ))}
          </div>
        )}

        {streamError && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            ⚠ 流出错：{streamError}
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
            placeholder="说一句你下午想做什么...（Enter 发送，Shift+Enter 换行）"
            disabled={streaming}
            rows={2}
            className={cn(
              "flex-1 resize-none rounded-md border border-ink-200 bg-white",
              "px-3 py-2 text-sm text-ink-800 placeholder:text-ink-400",
              "focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500",
              "disabled:bg-ink-100",
            )}
          />
          <button
            className="btn-primary h-[44px]"
            onClick={submit}
            disabled={streaming || !draft.trim()}
          >
            {streaming ? "进行中" : "发送"}
          </button>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ role, text }: { role: "user" | "agent"; text: string }) {
  const isUser = role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed",
          isUser
            ? "bg-brand-600 text-white rounded-br-sm"
            : "bg-ink-100 text-ink-800 rounded-bl-sm",
        )}
      >
        {text}
      </div>
    </div>
  );
}
