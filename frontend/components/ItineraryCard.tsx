"use client";

import { useState } from "react";

import { Icons } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

import NumberTicker from "./NumberTicker";
import RefinementDialog from "./RefinementDialog";

/** 行程卡片：六段时间轴 + 已为你预留 + 转发文案 + 三按钮（B+D+C 范式）。 */
export default function ItineraryCard() {
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);
  const lastRefinement = useChatStore((s) => s.lastRefinement);
  const confirm = useChatStore((s) => s.confirm);
  const cancel = useChatStore((s) => s.cancel);

  const [refineOpen, setRefineOpen] = useState(false);

  if (!itinerary && !streaming) {
    return (
      <div className="card px-4 py-8 flex flex-col items-center gap-2 text-ink-400">
        <Icons.pin className="w-5 h-5 text-ink-300" strokeWidth={1.5} />
        <span className="text-sm">行程将在这里出现</span>
      </div>
    );
  }

  if (!itinerary) {
    return (
      <div className="card px-4 py-8 flex flex-col items-center gap-2 text-ink-500">
        <Icons.thinking
          className="w-5 h-5 text-accent-500 animate-spin"
          strokeWidth={2}
        />
        <span className="text-sm animate-pulse-soft">正在为你拼装行程...</span>
      </div>
    );
  }

  const totalH = itinerary.total_minutes / 60;
  const hasOrders = itinerary.orders.length > 0;
  const canAct = !streaming && !hasOrders && !cancelled;

  return (
    <div className="card animate-fade-in">
      {/* Header */}
      <div className="px-4 py-3 border-b border-ink-200">
        <div className="flex items-center justify-between">
          <span className="section-title">行程方案</span>
          <span className="text-[11px] text-ink-500">
            总时长{" "}
            <NumberTicker
              value={totalH}
              format={(v) => v.toFixed(1)}
              className="font-mono text-ink-800 mx-0.5"
            />
            <span className="text-ink-400">小时</span>
          </span>
        </div>
        <div className="mt-0.5 text-[15px] font-semibold text-ink-900 tracking-tight">
          {itinerary.summary}
        </div>
      </div>

      {/* Refinement summary banner */}
      {lastRefinement && lastRefinement.changedFields.length > 0 && (
        <div className="px-4 pt-3">
          <RefinementSummaryBanner
            fields={lastRefinement.changedFields}
            note={lastRefinement.refinerNote}
          />
        </div>
      )}

      {/* Timeline */}
      <ol className="relative px-4 py-4 space-y-3.5">
        {/* 时间轴竖线（accent，淡） */}
        <div
          aria-hidden
          className="absolute left-[51px] top-6 bottom-6 w-px bg-gradient-to-b from-accent-300 via-ink-200 to-transparent"
        />
        {itinerary.stages.map((stage, idx) => (
          <li
            key={idx}
            className="relative flex items-start gap-3 animate-fade-in-up"
          >
            <div className="flex flex-col items-center min-w-[44px] z-10">
              <div className="text-[10px] text-ink-500 mono">{stage.start}</div>
              <div className="my-1 w-2 h-2 rounded-full bg-ink-900 ring-[3px] ring-white shadow-[0_0_0_1px_rgb(228_228_231)]" />
              <div className="text-[10px] text-ink-400 mono">{stage.end}</div>
            </div>
            <div className="flex-1 pt-0.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="chip">{stage.kind}</span>
                <span className="text-sm font-medium text-ink-900 tracking-tight">
                  {stage.title}
                </span>
              </div>
              {stage.note && (
                <div className="mt-1 text-xs text-ink-500 leading-relaxed">
                  {stage.note}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>

      {/* 已为你预留 */}
      {hasOrders && (
        <div className="px-4 pb-3">
          <div className="section-title mb-1.5">已为你预留</div>
          <ul className="space-y-1.5">
            {itinerary.orders.map((o) => (
              <li
                key={o.order_id}
                className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 animate-fade-in-up"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <Icons.success
                      className="w-3.5 h-3.5 shrink-0"
                      strokeWidth={2}
                    />
                    <span className="font-medium tracking-tight truncate">
                      {o.target_name}
                    </span>
                  </div>
                  <span className="text-[10px] text-emerald-700/80 mono shrink-0">
                    {o.kind}
                  </span>
                </div>
                <div className="mt-1 text-emerald-700/90 ml-5">
                  {o.detail}
                  <span className="text-emerald-600/70 mx-1.5">·</span>
                  <span className="mono text-[11px]">{o.order_id}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 转发文案 */}
      {itinerary.share_message && (
        <div className="px-4 pb-3">
          <ShareMessage text={itinerary.share_message} />
        </div>
      )}

      {/* Action buttons */}
      <div className="px-4 pb-4">
        {!hasOrders && !cancelled && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <button
              className={cn("btn-primary", streaming && "shimmer-border")}
              disabled={!canAct}
              onClick={confirm}
            >
              {streaming ? (
                <>
                  <Icons.thinking className="w-3.5 h-3.5 animate-spin" />
                  <span>执行中</span>
                </>
              ) : (
                <>
                  <Icons.success className="w-3.5 h-3.5" strokeWidth={2.25} />
                  <span>确认并预约</span>
                </>
              )}
            </button>
            <button
              className="btn-secondary disabled:opacity-50 disabled:cursor-not-allowed"
              disabled={!canAct}
              onClick={() => setRefineOpen(true)}
            >
              <Icons.refine className="w-3.5 h-3.5" strokeWidth={2} />
              <span>说说哪不对</span>
            </button>
            <button
              className="btn-danger-ghost disabled:opacity-50 disabled:cursor-not-allowed"
              disabled={!canAct}
              onClick={cancel}
            >
              <Icons.close className="w-3.5 h-3.5" strokeWidth={2.25} />
              <span>取消方案</span>
            </button>
          </div>
        )}
        {hasOrders && (
          <div className="flex items-center justify-center gap-1.5 text-xs text-emerald-700">
            <Icons.success className="w-3.5 h-3.5" strokeWidth={2.25} />
            <span>已完成下单与转发文案生成</span>
          </div>
        )}
        {cancelled && !hasOrders && (
          <div className="text-center text-xs text-ink-500">
            已取消方案，可重新输入或点击场景按钮
          </div>
        )}
      </div>

      <RefinementDialog
        open={refineOpen}
        onClose={() => setRefineOpen(false)}
      />
    </div>
  );
}

function RefinementSummaryBanner({
  fields,
  note,
}: {
  fields: string[];
  note?: string | null;
}) {
  return (
    <div className="rounded-md border border-accent-200 bg-accent-50/60 px-3 py-2 text-xs text-accent-800 animate-fade-in">
      <div className="flex items-center gap-1.5 mb-1 font-medium">
        <Icons.refine className="w-3.5 h-3.5" strokeWidth={2} />
        <span>已根据反馈调整</span>
      </div>
      <ul className="space-y-0.5 text-accent-900 ml-5 list-disc list-outside">
        {fields.map((f, i) => (
          <li key={i}>{f}</li>
        ))}
      </ul>
      {note && <div className="mt-1 ml-5 text-accent-700/80">{note}</div>}
    </div>
  );
}

function ShareMessage({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      } finally {
        document.body.removeChild(ta);
      }
    }
  };

  return (
    <div className="rounded-md border border-ink-200 bg-ink-50/60 p-3">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          <Icons.share
            className="w-3.5 h-3.5 text-ink-500"
            strokeWidth={2}
          />
          <span className="text-[11px] font-medium text-ink-700 tracking-tight">
            转发文案
          </span>
        </div>
        <button
          onClick={copy}
          className={cn(
            "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded transition-colors",
            copied
              ? "bg-emerald-600 text-white"
              : "bg-white text-ink-700 border border-ink-200 hover:border-ink-300 hover:bg-ink-50",
          )}
        >
          {copied ? (
            <>
              <Icons.success className="w-3 h-3" strokeWidth={2.5} />
              <span>已复制</span>
            </>
          ) : (
            <>
              <Icons.copy className="w-3 h-3" strokeWidth={2} />
              <span>复制</span>
            </>
          )}
        </button>
      </div>
      <div className="text-[13px] leading-relaxed text-ink-700 whitespace-pre-wrap tracking-tight">
        {text}
      </div>
    </div>
  );
}
