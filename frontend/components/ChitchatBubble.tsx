"use client";

/**
 * ChitchatBubble —— Phase 0.8 暖心回话气泡。
 *
 * 当 SSE 推 chitchat_reply 时（input_kind ∈ {chitchat / meta / emotional / off_topic / ambiguous}），
 * 由 ChatPanel 在消息流里渲染本组件。
 *
 * 视觉语言（不和现有色板冲突）：
 * - tone=warm        → amber-50 背景 + amber-700 强调
 * - tone=neutral     → sky-50  背景 + sky-700  强调
 * - tone=empathetic  → rose-50  背景 + rose-700  强调
 * - tone=playful     → emerald-50 背景 + emerald-700 强调
 *
 * 引导按钮：点击直接 sendMessage(chip.send) 重入主链路。
 */

import type { ChitchatReplyPayload, ReplyTone } from "@/lib/types";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

interface ToneTheme {
  bg: string;
  border: string;
  accent: string;
  emoji: string;
  label: string;
}

const TONE_THEMES: Record<ReplyTone, ToneTheme> = {
  warm: {
    bg: "bg-amber-50",
    border: "border-amber-200",
    accent: "text-amber-700",
    emoji: "☀️",
    label: "暖心",
  },
  neutral: {
    bg: "bg-sky-50",
    border: "border-sky-200",
    accent: "text-sky-700",
    emoji: "🤖",
    label: "介绍",
  },
  empathetic: {
    bg: "bg-rose-50",
    border: "border-rose-200",
    accent: "text-rose-700",
    emoji: "🫶",
    label: "陪伴",
  },
  playful: {
    bg: "bg-emerald-50",
    border: "border-emerald-200",
    accent: "text-emerald-700",
    emoji: "🌿",
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

  return (
    <div className="flex justify-start animate-fade-in-up">
      <div
        className={cn(
          "max-w-[92%] rounded-2xl border px-3.5 py-3 text-sm leading-relaxed",
          theme.bg,
          theme.border,
        )}
      >
        {/* 头部：表情 + 类别标签 */}
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-xl leading-none">{theme.emoji}</span>
          <span className={cn("text-xs font-medium", theme.accent)}>
            {theme.label}
          </span>
          <span className="text-xs text-ink-400">·</span>
          <span className="text-xs text-ink-500">
            {KIND_LABELS[payload.input_kind] ?? payload.input_kind}
          </span>
        </div>

        {/* 暖心回话文本 */}
        <div className="text-ink-800 whitespace-pre-wrap">{payload.reply_text}</div>

        {/* 引导按钮 chips */}
        {payload.cta_chips.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {payload.cta_chips.map((chip, idx) => (
              <button
                key={`${chip.send}-${idx}`}
                disabled={streaming}
                onClick={() => sendMessage(chip.send)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full",
                  "px-3 py-1.5 text-xs font-medium",
                  "bg-white border border-ink-200 text-ink-700",
                  "hover:border-brand-400 hover:text-brand-700 hover:bg-brand-50",
                  "transition-all duration-200",
                  "active:scale-95",
                  "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:border-ink-200 disabled:hover:bg-white",
                  "shadow-sm",
                )}
                title={chip.send}
              >
                {chip.icon && <span className="text-base leading-none">{chip.icon}</span>}
                <span>{chip.label}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
