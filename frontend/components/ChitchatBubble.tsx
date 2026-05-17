"use client";

/**
 * ChitchatBubble —— 暖心回话气泡（B+D 范式：去 emoji，灰阶克制）。
 *
 * tone 用 ink + accent 灰阶配色，不再把 emoji 当 chrome。
 * cta_chips 仍可能含 emoji（来自后端），保留显示但限制大小。
 */

import { Coffee, Heart, MessageCircle, Sparkles } from "lucide-react";

import { useChatStore } from "@/lib/store";
import type { ChitchatReplyPayload, ReplyTone } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ToneTheme {
  bg: string;
  border: string;
  accent: string;
  Icon: typeof Sparkles;
  label: string;
}

const TONE_THEMES: Record<ReplyTone, ToneTheme> = {
  warm: {
    bg: "bg-amber-50/60",
    border: "border-amber-200",
    accent: "text-amber-700",
    Icon: Coffee,
    label: "暖心",
  },
  neutral: {
    bg: "bg-ink-50",
    border: "border-ink-200",
    accent: "text-ink-700",
    Icon: MessageCircle,
    label: "介绍",
  },
  empathetic: {
    bg: "bg-rose-50/60",
    border: "border-rose-200",
    accent: "text-rose-700",
    Icon: Heart,
    label: "陪伴",
  },
  playful: {
    bg: "bg-emerald-50/60",
    border: "border-emerald-200",
    accent: "text-emerald-700",
    Icon: Sparkles,
    label: "玩笑",
  },
};

const KIND_LABELS: Record<ChitchatReplyPayload["input_kind"], string> = {
  planning: "规划",
  chitchat: "闲聊",
  meta: "问能力",
  emotional: "情绪",
  off_topic: "无关",
  ambiguous: "模糊",
};

export default function ChitchatBubble({ payload }: { payload: ChitchatReplyPayload }) {
  const sendMessage = useChatStore((s) => s.sendMessage);
  const streaming = useChatStore((s) => s.streaming);
  const theme = TONE_THEMES[payload.tone] ?? TONE_THEMES.warm;
  const Icon = theme.Icon;

  return (
    <div className="flex justify-start animate-fade-in-up">
      <div
        className={cn(
          "max-w-[92%] rounded-2xl border px-3.5 py-3 text-sm leading-relaxed tracking-tight",
          theme.bg,
          theme.border,
        )}
      >
        {/* 头部：图标 + tone label + kind */}
        <div className="flex items-center gap-1.5 mb-1.5">
          <Icon
            className={cn("w-3.5 h-3.5 shrink-0", theme.accent)}
            strokeWidth={2}
          />
          <span className={cn("text-[11px] font-medium", theme.accent)}>
            {theme.label}
          </span>
          <span className="text-[10px] text-ink-300">·</span>
          <span className="text-[11px] text-ink-500">
            {KIND_LABELS[payload.input_kind] ?? payload.input_kind}
          </span>
        </div>

        {/* 暖心回话文本 */}
        <div className="text-ink-800 whitespace-pre-wrap">{payload.reply_text}</div>

        {/* 引导按钮 chips */}
        {payload.cta_chips.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {payload.cta_chips.map((chip, idx) => (
              <button
                key={`${chip.send}-${idx}`}
                disabled={streaming}
                onClick={() => sendMessage(chip.send)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md",
                  "px-2.5 py-1 text-[11px] font-medium tracking-tight",
                  "bg-white border border-ink-200 text-ink-700",
                  "hover:border-ink-300 hover:bg-ink-50",
                  "transition-colors duration-150",
                  "active:scale-[0.98]",
                  "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:border-ink-200 disabled:hover:bg-white",
                )}
                title={chip.send}
              >
                {chip.icon && (
                  <span className="text-[11px] leading-none opacity-70">
                    {chip.icon}
                  </span>
                )}
                <span>{chip.label}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
