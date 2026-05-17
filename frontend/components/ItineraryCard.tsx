"use client";

import { useEffect, useRef, useState } from "react";

import { Icons } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

import NumberTicker from "./NumberTicker";
import RefinementDialog from "./RefinementDialog";
import ShimmerStripe from "./ShimmerStripe";

/** 行程卡片：六段时间轴 + 已为你预留 + 转发文案 + 三按钮（黄昏深色主题）。 */
export default function ItineraryCard() {
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);
  const lastRefinement = useChatStore((s) => s.lastRefinement);
  const confirm = useChatStore((s) => s.confirm);
  const cancel = useChatStore((s) => s.cancel);

  const [refineOpen, setRefineOpen] = useState(false);

  // 聚光灯：itinerary 从 null/无 → 有时触发一次性脉冲
  const [spotlight, setSpotlight] = useState(false);
  const prevHadItinerary = useRef(false);
  useEffect(() => {
    const has = !!itinerary;
    if (has && !prevHadItinerary.current) {
      setSpotlight(true);
      const timer = setTimeout(() => setSpotlight(false), 2400);
      prevHadItinerary.current = true;
      return () => clearTimeout(timer);
    }
    if (!has) prevHadItinerary.current = false;
  }, [itinerary]);

  if (!itinerary && !streaming) {
    return (
      <div className="card px-4 py-8 flex flex-col items-center gap-2.5 text-ink-500">
        <div className="w-10 h-10 rounded-full bg-gradient-to-br from-brand-500/15 to-accent-500/15 flex items-center justify-center border border-white/[0.08]">
          <Icons.pin className="w-4 h-4 text-brand-400" strokeWidth={1.5} />
        </div>
        <span className="text-sm text-ink-700">行程将在这里出现</span>
      </div>
    );
  }

  if (!itinerary) {
    return (
      <div className="card px-4 py-5 space-y-3">
        <div className="flex items-center gap-1.5 text-[12px] text-brand-400">
          <Icons.thinking
            className="w-3.5 h-3.5 animate-spin"
            strokeWidth={2}
          />
          <span className="tracking-tight">正在为你拼装行程...</span>
        </div>
        <ShimmerStripe rows={4} />
      </div>
    );
  }

  const totalH = itinerary.total_minutes / 60;
  const hasOrders = itinerary.orders.length > 0;
  const canAct = !streaming && !hasOrders && !cancelled;

  return (
    <div className={cn("card animate-fade-in", spotlight && "spotlight-once")}>
      {/* Header */}
      <div className="px-4 py-3 border-b border-white/[0.06]">
        <div className="flex items-center justify-between">
          <span className="section-title">行程方案</span>
          <span className="text-[11px] text-ink-500">
            总时长{" "}
            <NumberTicker
              value={totalH}
              format={(v) => v.toFixed(1)}
              className="font-mono text-brand-400 mx-0.5 font-semibold"
            />
            <span className="text-ink-500">小时</span>
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
        {/* 时间轴竖线（暖橙→紫渐变） */}
        <div
          aria-hidden
          className="absolute left-[51px] top-6 bottom-6 w-px"
          style={{
            background:
              "linear-gradient(180deg, rgba(251,146,60,0.6) 0%, rgba(236,72,153,0.4) 50%, rgba(139,92,246,0.2) 100%)",
          }}
        />
        {itinerary.stages.map((stage, idx) => (
          <li
            key={idx}
            className="relative flex items-start gap-3 animate-fade-in-up"
          >
            <div className="flex flex-col items-center min-w-[44px] z-10">
              <div className="text-[10px] text-ink-500 mono">{stage.start}</div>
              {/* 暖橙→莓粉时间点 */}
              <div
                className="my-1 w-2 h-2 rounded-full ring-[3px] ring-[#08080d]"
                style={{
                  background:
                    "linear-gradient(135deg, #fb923c 0%, #ec4899 100%)",
                  boxShadow:
                    "0 0 0 1px rgba(255,255,255,0.16), 0 0 8px rgba(249,115,22,0.6)",
                }}
              />
              <div className="text-[10px] text-ink-500 mono">{stage.end}</div>
            </div>
            <div className="flex-1 pt-0.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="chip">{stage.kind}</span>
                <span className="text-sm font-medium text-ink-900 tracking-tight">
                  {stage.title}
                </span>
              </div>
              {stage.note && (
                <div className="mt-1 text-xs text-ink-600 leading-relaxed">
                  {stage.note}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>

      {/* 已为你预留：暗色 emerald 玻璃 */}
      {hasOrders && (
        <div className="px-4 pb-3">
          <div className="section-title mb-1.5">已为你预留</div>
          <ul className="space-y-1.5">
            {itinerary.orders.map((o) => (
              <li
                key={o.order_id}
                className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200 animate-fade-in-up backdrop-blur-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <Icons.success
                      className="w-3.5 h-3.5 shrink-0 text-emerald-400"
                      strokeWidth={2}
                    />
                    <span className="font-medium tracking-tight truncate">
                      {o.target_name}
                    </span>
                  </div>
                  <span className="text-[10px] text-emerald-300/80 mono shrink-0">
                    {o.kind}
                  </span>
                </div>
                <div className="mt-1 text-emerald-300/90 ml-5">
                  {o.detail}
                  <span className="text-emerald-500/70 mx-1.5">·</span>
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
          <div className="flex items-center justify-center gap-1.5 text-xs text-emerald-400">
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
    <div
      className="rounded-md border border-accent-500/30 px-3 py-2 text-xs text-accent-200 animate-fade-in backdrop-blur-sm"
      style={{
        background:
          "linear-gradient(135deg, rgba(217,70,239,0.12) 0%, rgba(139,92,246,0.08) 100%)",
      }}
    >
      <div className="flex items-center gap-1.5 mb-1 font-medium text-accent-300">
        <Icons.refine className="w-3.5 h-3.5" strokeWidth={2} />
        <span>已根据反馈调整</span>
      </div>
      <ul className="space-y-0.5 text-accent-100 ml-5 list-disc list-outside">
        {fields.map((f, i) => (
          <li key={i}>{f}</li>
        ))}
      </ul>
      {note && <div className="mt-1 ml-5 text-accent-300/80">{note}</div>}
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
    <div
      className="rounded-md border border-white/[0.08] p-3 backdrop-blur-sm"
      style={{
        background:
          "linear-gradient(135deg, rgba(251,146,60,0.06) 0%, rgba(236,72,153,0.04) 100%)",
      }}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          <Icons.share
            className="w-3.5 h-3.5 text-brand-400"
            strokeWidth={2}
          />
          <span className="text-[11px] font-medium text-ink-800 tracking-tight">
            转发文案
          </span>
        </div>
        <button
          onClick={copy}
          className={cn(
            "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded transition-colors",
            copied
              ? "bg-emerald-500 text-white"
              : "bg-white/[0.06] text-ink-700 border border-white/[0.08] hover:bg-white/[0.1] hover:text-ink-900",
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
      <div className="text-[13px] leading-relaxed text-ink-800 whitespace-pre-wrap tracking-tight">
        {text}
      </div>
    </div>
  );
}
