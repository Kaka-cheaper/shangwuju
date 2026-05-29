"use client";

/**
 * 「我说说哪不对」反馈弹窗（B+D 范式：去 ✕ emoji，灰阶克制）。
 */

import { useEffect, useRef, useState } from "react";

import { Icons } from "@/lib/icon-map";
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

  useEffect(() => {
    if (open) {
      setText("");
      const t = setTimeout(() => textareaRef.current?.focus(), 60);
      return () => clearTimeout(t);
    }
  }, [open]);

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
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-md"
        onClick={onClose}
        aria-hidden
      />

      <div className="relative card w-full max-w-md p-5 animate-fade-in-up max-h-[90vh] overflow-y-auto shadow-elevated">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-1.5 mb-0.5">
              <Icons.refine
                className="w-3.5 h-3.5 text-brand-600"
                strokeWidth={2}
              />
              <span className="section-title">反馈调整</span>
            </div>
            <h2
              id="refine-title"
              className="text-[15px] font-semibold text-ink-900 tracking-tight"
            >
              说说哪不对？
            </h2>
            <p className="mt-1.5 text-xs text-ink-500 leading-relaxed">
              不喜欢的部分告诉我，Agent 会基于你的反馈调整原计划，
              而不是从零再想一遍。也可以直接提交让我换个组合。
            </p>
          </div>
          <button
            className="btn-ghost shrink-0"
            onClick={onClose}
            aria-label="关闭"
          >
            <Icons.close className="w-3.5 h-3.5" strokeWidth={2} />
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
              "w-full resize-none rounded-md border bg-black/[0.03]",
              "border-black/[0.08] hover:border-black/[0.12]",
              "px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 tracking-tight",
              "focus:outline-none focus:ring-2 focus:ring-brand-500/30 focus:border-brand-500/40",
              "transition-colors duration-150",
            )}
          />
          <div className="mt-1 flex items-center justify-between text-[11px] text-ink-400">
            <span className="flex items-center gap-1">
              <span className="kbd">⌘</span>
              <span className="kbd">↵</span>
              <span className="ml-1">快速提交</span>
            </span>
            <span className="mono">
              {text.length} / {MAX_LEN}
            </span>
          </div>
        </div>

        <div className="mt-3">
          <div className="section-title mb-1.5">常见反馈</div>
          <div className="flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => setText(s)}
                className={cn(
                  "text-[11px] rounded-md px-2.5 py-1 tracking-tight",
                  "bg-black/[0.03] hover:bg-black/[0.05] text-ink-700 hover:text-ink-900",
                  "border border-black/[0.08] hover:border-black/[0.12]",
                  "transition-colors duration-150",
                )}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-5 flex flex-col-reverse sm:flex-row sm:justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>
            取消
          </button>
          <button
            className={cn("btn-primary", streaming && "shimmer-border")}
            disabled={streaming}
            onClick={() => submit()}
          >
            {streaming ? (
              <>
                <Icons.thinking className="w-3.5 h-3.5 animate-spin" />
                <span>提交中</span>
              </>
            ) : (
              <span>提交反馈</span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
