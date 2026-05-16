"use client";

/**
 * 「我说说哪不对」反馈弹窗（C2）。
 *
 * 用户操作流程：
 * 1. 点击 ItineraryCard 「我说说哪不对」 → open=true
 * 2. textarea 输入反馈（也可不填，refiner 走默认调整）
 * 3. 点提交 → 触发 store.refine(text) → POST /chat/refine
 * 4. 弹窗关闭后 ItineraryCard 重新进入 streaming 态展示 refinement_start/done 事件
 */

import { useEffect, useRef, useState } from "react";

import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

interface RefinementDialogProps {
  open: boolean;
  onClose: () => void;
}

const SUGGESTIONS = [
  "太远了，希望 3 公里以内",
  "想换一家不那么贵的餐厅",
  "孩子太小了，找个室内的活动",
  "时间想再短一点，3 小时以内",
  "想吃辣的，换个菜系",
  "再安静一点，能聊天的地方",
];

const MAX_LEN = 200;

export default function RefinementDialog({
  open,
  onClose,
}: RefinementDialogProps) {
  const refine = useChatStore((s) => s.refine);
  const streaming = useChatStore((s) => s.streaming);

  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 弹窗打开时清空并聚焦；关闭时重置
  useEffect(() => {
    if (open) {
      setText("");
      // 等动画结束再 focus 体感更稳
      const t = setTimeout(() => textareaRef.current?.focus(), 60);
      return () => clearTimeout(t);
    }
  }, [open]);

  // ESC 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const submit = (override?: string) => {
    const value = (override ?? text).trim();
    void refine(value);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center px-4 animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-labelledby="refine-title"
    >
      {/* 遮罩 */}
      <div
        className="absolute inset-0 bg-ink-900/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />

      {/* 主体 */}
      <div className="relative card w-full max-w-md p-5 animate-fade-in-up max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2
              id="refine-title"
              className="text-base font-semibold text-ink-900"
            >
              说说哪不对？
            </h2>
            <p className="mt-1 text-xs text-ink-500 leading-relaxed">
              不喜欢的部分告诉我，Agent 会基于你的反馈调整原计划，
              而不是从零再想一遍。也可以直接提交让我换个组合。
            </p>
          </div>
          <button
            className="btn-ghost shrink-0"
            onClick={onClose}
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        <div className="mt-4">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value.slice(0, MAX_LEN))}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                submit();
              }
            }}
            rows={4}
            placeholder="例：太远了，希望 3 公里以内 / 想换一家不那么贵的"
            className={cn(
              "w-full resize-none rounded-md border border-ink-200 bg-white",
              "px-3 py-2 text-sm text-ink-800 placeholder:text-ink-400",
              "focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500",
            )}
          />
          <div className="mt-1 flex items-center justify-between text-[11px] text-ink-400">
            <span>Ctrl/⌘ + Enter 快速提交</span>
            <span>
              {text.length} / {MAX_LEN}
            </span>
          </div>
        </div>

        <div className="mt-3">
          <div className="text-xs text-ink-500 mb-1.5">常见反馈</div>
          <div className="flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => setText(s)}
                className={cn(
                  "text-xs rounded-full px-2.5 py-1",
                  "bg-ink-100 hover:bg-brand-100 hover:text-brand-700",
                  "text-ink-700 transition-colors",
                )}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-5 flex flex-col-reverse sm:flex-row sm:justify-end gap-2">
          <button className="btn-ghost-bordered" onClick={onClose}>
            取消
          </button>
          <button
            className="btn-primary"
            disabled={streaming}
            onClick={() => submit()}
          >
            {streaming ? "提交中..." : "提交反馈"}
          </button>
        </div>
      </div>
    </div>
  );
}
