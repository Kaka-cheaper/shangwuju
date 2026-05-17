"use client";

/**
 * ChitchatBubble —— 暖心回话气泡（黄昏深色主题）。
 */

import { Coffee, Heart, MessageCircle, Sparkles } from "lucide-react";

import { useChatStore } from "@/lib/store";
import type { ChitchatReplyPayload, ReplyTone } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ToneTheme {
  /** 卡片渐变背景 */
  gradient: string;
  /** 边框色 */
  border: string;
  /** 文字 accent 色 */
  accent: string;
  Icon: typeof Sparkles;
  label: string;
}

const TONE_THEMES: Record<ReplyTone, ToneTheme> = {
  warm: {
    gradient:
      "linear-gradient(135deg, rgba(251,146,60,0.18) 0%, rgba(236,72,153,0.10) 100%)",
    border: "border-brand-500/30",
    accent: "text-brand-300",
    Icon: Coffee,
    label: "暖心",
  },
  neutral: {
    gradient:
      "linear-gradient(135deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.02) 100%)",
    border: "border-white/[0.1]",
    accent: "text-ink-700",
    Icon: MessageCircle,
    label: "介绍",
  },
  empathetic: {
    gradient:
      "linear-gradient(135deg, rgba(244,63,94,0.18) 0%, rgba(217,70,239,0.10) 100%)",
    border: "border-rose-500/30",
    accent: "text-rose-300",
    Icon: Heart,
    label: "陪伴",
  },
  playful: {
    gradient:
      "linear-gradient(135deg, rgba(16,185,129,0.18) 0%, rgba(20,184,166,0.10) 100%)",
    border: "border-emerald-500/30",
    accent: "text-emerald-300",
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
          "max-w-[92%] rounded-2xl border px-3.5 py-3 text-sm leading-relaxed tracking-tight backdrop-blur-sm",
          theme.border,
        )}
        style={{ background: theme.gradient }}
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
          <span className="text-[10px] text-ink-500">·</span>
          <span className="text-[11px] text-ink-600">
            {KIND_LABELS[payload.input_kind] ?? payload.input_kind}
          </span>
        </div>

        {/* 暖心回话文本 */}
        <div className="text-ink-900 whitespace-pre-wrap">{payload.reply_text}</div>

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
                  "bg-white/[0.06] border border-white/[0.1] text-ink-800",
                  "hover:bg-white/[0.1] hover:border-white/[0.2] hover:text-ink-900",
                  "transition-colors duration-150",
                  "active:scale-[0.98]",
                  "disabled:opacity-50 disabled:cursor-not-allowed",
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
