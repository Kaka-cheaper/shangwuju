"use client";

import { useState } from "react";

import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

import RefinementDialog from "./RefinementDialog";

/** 行程卡片：六段时间轴 + 已为你预留清单 + 转发文案 + 三按钮（确认/反馈/取消）。 */
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
      <div className="card px-4 py-6 text-center text-sm text-ink-400">
        🌤 行程将在这里出现
      </div>
    );
  }

  if (!itinerary) {
    return (
      <div className="card px-4 py-6 text-center text-sm text-ink-500 animate-pulse-soft">
        正在为你拼装行程...
      </div>
    );
  }

  const totalH = (itinerary.total_minutes / 60).toFixed(1);
  const hasOrders = itinerary.orders.length > 0;
  const canAct = !streaming && !hasOrders && !cancelled;

  return (
    <div className="card animate-fade-in">
      <div className="px-4 py-3 border-b border-ink-200">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium text-ink-700">行程方案</div>
          <div className="text-xs text-ink-500">总时长 {totalH} 小时</div>
        </div>
        <div className="mt-1 text-base font-semibold text-ink-900">
          {itinerary.summary}
        </div>
      </div>

      {lastRefinement && lastRefinement.changedFields.length > 0 && (
        <div className="px-4 pt-3">
          <RefinementSummaryBanner
            fields={lastRefinement.changedFields}
            note={lastRefinement.refinerNote}
          />
        </div>
      )}

      <ol className="relative px-4 py-4 space-y-3">
        {/* 时间轴竖线 */}
        <div
          aria-hidden
          className="absolute left-[51px] top-6 bottom-6 w-px bg-gradient-to-b from-brand-300 via-brand-200 to-transparent"
        />
        {itinerary.stages.map((stage, idx) => (
          <li
            key={idx}
            className="relative flex items-start gap-3 animate-fade-in-up"
          >
            <div className="flex flex-col items-center min-w-[44px] z-10">
              <div className="text-[11px] text-ink-500">{stage.start}</div>
              <div className="my-1 w-2.5 h-2.5 rounded-full bg-brand-500 ring-2 ring-white shadow-sm"></div>
              <div className="text-[11px] text-ink-400">{stage.end}</div>
            </div>
            <div className="flex-1 pt-0.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="chip">{stage.kind}</span>
                <span className="text-sm font-medium text-ink-800">
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

      {hasOrders && (
        <div className="px-4 pb-3">
          <div className="text-xs font-medium text-ink-700 mb-1.5">
            已为你预留
          </div>
          <ul className="space-y-1.5">
            {itinerary.orders.map((o) => (
              <li
                key={o.order_id}
                className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{o.target_name}</span>
                  <span className="text-[11px] text-emerald-700/80">
                    {o.kind}
                  </span>
                </div>
                <div className="mt-0.5 text-emerald-700/90">
                  {o.detail} · 订单号 {o.order_id}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {itinerary.share_message && (
        <div className="px-4 pb-3">
          <ShareMessage text={itinerary.share_message} />
        </div>
      )}

      <div className="px-4 pb-4">
        {!hasOrders && !cancelled && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <button
              className="btn-primary"
              disabled={!canAct}
              onClick={confirm}
            >
              {streaming ? "执行中..." : "确认并预约"}
            </button>
            <button
              className={cn(
                "btn-secondary",
                "disabled:opacity-50 disabled:cursor-not-allowed",
              )}
              disabled={!canAct}
              onClick={() => setRefineOpen(true)}
            >
              我说说哪不对
            </button>
            <button
              className={cn(
                "btn-ghost-bordered",
                "disabled:opacity-50 disabled:cursor-not-allowed",
              )}
              disabled={!canAct}
              onClick={cancel}
            >
              取消方案
            </button>
          </div>
        )}
        {hasOrders && (
          <div className="text-center text-xs text-emerald-700">
            ✓ 已完成下单与转发文案生成
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
    <div className="rounded-md border border-sky-200 bg-sky-50/70 px-3 py-2 text-xs text-sky-800 animate-fade-in">
      <div className="font-medium mb-1">🪄 已根据你的反馈调整</div>
      <ul className="space-y-0.5 text-sky-900">
        {fields.map((f, i) => (
          <li key={i}>· {f}</li>
        ))}
      </ul>
      {note && <div className="mt-1 text-sky-700/80">{note}</div>}
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
      // 降级：选中文本
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
    <div className="rounded-md border border-brand-200 bg-brand-50/60 p-3">
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs font-medium text-brand-700">📋 转发文案</div>
        <button
          onClick={copy}
          className={cn(
            "text-xs px-2 py-1 rounded-md transition-colors",
            copied
              ? "bg-emerald-600 text-white"
              : "bg-white text-brand-700 border border-brand-300 hover:bg-brand-100",
          )}
        >
          {copied ? "✓ 已复制" : "复制到剪贴板"}
        </button>
      </div>
      <div className="text-xs leading-relaxed text-ink-700 whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
}
