"use client";

/**
 * ChitchatBubble —— 暖心回话气泡（黄昏深色主题）。
 */

import { Check, Coffee, Heart, MessageCircle, Sparkles } from "lucide-react";

import { useCollabStore } from "@/lib/collab-store";
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
      "linear-gradient(135deg, rgba(255,209,0,0.12) 0%, rgba(245,158,11,0.08) 100%)",
    border: "border-brand-500/30",
    accent: "text-brand-800",
    Icon: Coffee,
    label: "暖心",
  },
  neutral: {
    gradient:
      "linear-gradient(135deg, rgba(0,0,0,0.03) 0%, rgba(0,0,0,0.01) 100%)",
    border: "border-black/[0.08]",
    accent: "text-ink-700",
    Icon: MessageCircle,
    label: "介绍",
  },
  empathetic: {
    gradient:
      "linear-gradient(135deg, rgba(244,63,94,0.12) 0%, rgba(217,70,239,0.08) 100%)",
    border: "border-rose-500/30",
    accent: "text-rose-600",
    Icon: Heart,
    label: "陪伴",
  },
  playful: {
    gradient:
      "linear-gradient(135deg, rgba(16,185,129,0.12) 0%, rgba(20,184,166,0.08) 100%)",
    border: "border-emerald-500/30",
    accent: "text-emerald-600",
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
  const confirm = useChatStore((s) => s.confirm);
  const streaming = useChatStore((s) => s.streaming);
  // 已预约 = 当前方案已带订单（confirm 成功后写入）；用于把确认按钮置成一次性
  const booked = useChatStore((s) => (s.itinerary?.orders?.length ?? 0) > 0);
  const theme = TONE_THEMES[payload.tone] ?? TONE_THEMES.warm;
  const Icon = theme.Icon;

  // ADR-0013 F-4 范围追加（协作模式缺口修复）：气泡 chip 点击原先硬连单人主
  // store，房间里点击会打到单人 /chat/turn 接口而非房间 WS 通道，全无效果。
  // 照 ChatDock.tsx:258-260 先例判 collabMode：协作模式下普通 chip 走
  // sendConstraint（同 ChatDock 输入框一样，本地也要乐观追加一条用户消息——
  // 自己发的不会经 WS constraint_added 广播回显，见 collab-store.ts 对应注释）；
  // 确认 chip 走 collab 的 sendConfirm（内部已自带"只有房间发起人可确认"守卫）。
  const collabMode = useCollabStore((s) => s.collabMode);
  const sendConstraint = useCollabStore((s) => s.sendConstraint);
  const sendCollabConfirm = useCollabStore((s) => s.sendConfirm);

  const handleChipClick = (chip: ChitchatReplyPayload["cta_chips"][number]) => {
    const isConfirm = chip.action === "confirm";
    if (collabMode) {
      if (isConfirm) {
        sendCollabConfirm();
        return;
      }
      sendConstraint(chip.send);
      useChatStore.setState((s) => ({
        messages: [
          ...s.messages,
          {
            id: `u-${Date.now()}`,
            role: "user" as const,
            text: chip.send,
            createdAt: Date.now(),
          },
        ],
      }));
      return;
    }
    if (isConfirm) {
      confirm();
    } else {
      sendMessage(chip.send);
    }
  };

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
          <span className={cn("text-xs font-medium", theme.accent)}>
            {theme.label}
          </span>
          <span className="text-xs text-ink-500">·</span>
          <span className="text-xs text-ink-600">
            {KIND_LABELS[payload.input_kind] ?? payload.input_kind}
          </span>
        </div>

        {/* 暖心回话文本 */}
        <div className="text-ink-900 whitespace-pre-wrap">{payload.reply_text}</div>

        {/* 引导按钮 chips */}
        {payload.cta_chips.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {payload.cta_chips.map((chip, idx) => {
              const isConfirm = chip.action === "confirm";
              const isBooked = isConfirm && booked; // 已预约 → 一次性、置灰
              return (
                <button
                  key={`${chip.send}-${idx}`}
                  disabled={streaming || isBooked}
                  onClick={() => handleChipClick(chip)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md text-xs tracking-tight",
                    "transition-colors duration-150 active:scale-[0.98]",
                    "disabled:cursor-not-allowed",
                    isBooked
                      ? // 已预约：绿色淡底 + 不可点（去掉 hover / active 反馈）
                        "px-3 py-1.5 font-semibold bg-emerald-500/12 border border-emerald-500/30 text-emerald-700 cursor-default active:scale-100"
                      : isConfirm
                        ? // 主行动按钮：实心黄 + lucide Check（精致，不双对钩 / 不塑料）
                          "px-3 py-1.5 font-semibold bg-brand-500 border border-brand-600 text-black shadow-sm hover:bg-brand-400 disabled:opacity-50"
                        : "px-2.5 py-1 font-medium bg-black/[0.04] border border-black/[0.08] text-ink-800 hover:bg-black/[0.06] hover:border-black/[0.15] hover:text-ink-900 disabled:opacity-50",
                  )}
                  title={isBooked ? "已完成预约" : chip.send}
                >
                  {isConfirm ? (
                    <Check className="w-3.5 h-3.5 shrink-0" strokeWidth={2.75} />
                  ) : (
                    chip.icon && (
                      <span className="text-xs leading-none opacity-70">
                        {chip.icon}
                      </span>
                    )
                  )}
                  <span>{isBooked ? "已预约" : chip.label}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

