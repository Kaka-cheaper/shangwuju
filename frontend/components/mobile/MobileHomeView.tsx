"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  ChevronRight,
  ClipboardList,
  ArrowRight as ArrowRightIcon,
  Loader2,
  Route,
  Sparkles,
  X,
} from "lucide-react";

import { scenarioIcon } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { formatStartTimeLabel } from "@/lib/time-labels";
import type { HopMode, Itinerary, ScheduleEntry } from "@/lib/types";
import {
  clearUserIdCookie,
  cn,
  generateSessionId,
  upsertSession,
} from "@/lib/utils";

import ComparisonView from "../ComparisonView";
import IntentSummary from "../IntentSummary";
import MapOverlay from "../MapOverlay";
import ToastStack from "../ToastStack";
import ToolTracePanel from "../ToolTracePanel";
import UserSwitcher from "../UserSwitcher";

type SheetKind = "trace" | null;

export default function MobileHomeView() {
  const loadScenarios = useChatStore((s) => s.loadScenarios);
  const loadPersonas = useChatStore((s) => s.loadPersonas);
  const refreshPreferences = useChatStore((s) => s.refreshPreferences);
  const sessionId = useChatStore((s) => s.sessionId);
  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);
  const itinerary = useChatStore((s) => s.itinerary);
  const intent = useChatStore((s) => s.intent);
  const previousItinerary = useChatStore((s) => s.previousItinerary);
  const lastRefinement = useChatStore((s) => s.lastRefinement);
  const startNewSession = useChatStore((s) => s.startNewSession);

  const [sheet, setSheet] = useState<SheetKind>(null);
  const activated = messages.length > 0 || streaming || itinerary != null;
  const canCompare = Boolean(previousItinerary && itinerary && lastRefinement);

  useEffect(() => {
    if (sessionId === "sess_pending") {
      const newId = generateSessionId();
      useChatStore.setState({ sessionId: newId });
      upsertSession({ id: newId, label: "新对话", lastMessageAt: Date.now() });
    } else {
      upsertSession({ id: sessionId, lastMessageAt: Date.now() });
    }
    clearUserIdCookie();
    useChatStore.setState({ currentUserId: "demo_user", preferences: null });
    loadScenarios();
    loadPersonas();
    refreshPreferences();
  }, [loadScenarios, loadPersonas, refreshPreferences, sessionId]);

  return (
    <div className="min-h-screen bg-[#fffdf6] text-ink-900">
      <div className="aurora-bg" aria-hidden />

      <MobileTopBar
        onNewSession={startNewSession}
      />

      <main
        className={cn(
          "relative-content mx-auto flex min-h-screen w-full max-w-[480px] flex-col px-4 pt-[76px]",
          streaming
            ? "pb-[calc(236px+env(safe-area-inset-bottom,0px))]"
            : itinerary
              ? "pb-[calc(176px+env(safe-area-inset-bottom,0px))]"
              : "pb-[calc(112px+env(safe-area-inset-bottom,0px))]",
        )}
      >
        <MobileScenarioRail compact={activated} />

        {intent && (
          <section className="mt-3">
            <MobileIntentStrip />
          </section>
        )}

        <MobileConversation />

        <MobilePlanCard />

        {itinerary && <MobileInlineMap itinerary={itinerary} />}

        {canCompare && previousItinerary && itinerary && (
          <MobileInlineCompare
            previousItinerary={previousItinerary}
            itinerary={itinerary}
          />
        )}
      </main>

      <MobileActionRail onOpenTrace={() => setSheet("trace")} />
      <MobileComposer />
      <ToastStack />

      <MobileSheet
        open={sheet === "trace"}
        title="Agent 思考链路"
        icon={<Bot className="h-4 w-4" />}
        onClose={() => setSheet(null)}
      >
        <ToolTracePanel />
      </MobileSheet>

    </div>
  );
}

function MobileTopBar({
  onNewSession,
}: {
  onNewSession: () => void;
}) {
  return (
    <header className="fixed inset-x-0 top-0 z-40 border-b border-black/[0.06] bg-white/[0.88] backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[480px] items-center justify-between gap-3 px-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center" aria-hidden>
            <svg
              width="32"
              height="32"
              viewBox="0 0 32 32"
              fill="none"
              className="drop-shadow-[0_8px_14px_rgba(245,158,11,0.14)]"
            >
              <path
                d="M16 3.5 C21 3.5 25 7.3 25 12.2 C25 18.5 16 27.5 16 27.5 C16 27.5 7 18.5 7 12.2 C7 7.3 11 3.5 16 3.5 Z"
                fill="rgba(255,255,255,0.72)"
                stroke="#1f2937"
                strokeWidth="2.2"
                strokeLinejoin="round"
              />
              <circle cx="16" cy="11.8" r="4.2" fill="#FFD100" />
              <line
                x1="11.6"
                y1="13.2"
                x2="20.4"
                y2="13.2"
                stroke="#1f2937"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </div>
          <div className="min-w-0 leading-tight">
            <div className="text-lg font-semibold tracking-tight text-ink-900">
              晌午局
            </div>
            <div className="truncate text-xs font-medium text-ink-500">
              半日出行管家
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <UserSwitcher autoOpenOnMount />
          <button
            type="button"
            className="h-9 rounded-full border border-[#FFD100]/55 bg-[#FFD100]/[0.18] px-3.5 text-sm font-bold tracking-tight text-ink-900 shadow-sm backdrop-blur transition hover:bg-[#FFD100]/[0.28] active:scale-95"
            onClick={onNewSession}
            aria-label="新对话"
            title="新对话"
          >
            新对话
          </button>
        </div>
      </div>
    </header>
  );
}

function MobileScenarioRail({ compact }: { compact: boolean }) {
  const scenarios = useChatStore((s) => s.scenarios);
  const streaming = useChatStore((s) => s.streaming);
  const sendScenario = useChatStore((s) => s.sendScenario);

  if (!scenarios.length) {
    return (
      <div className="rounded-2xl border border-black/[0.06] bg-white/[0.82] px-4 py-3 text-sm text-ink-500 shadow-sm backdrop-blur-xl">
        正在拉取演示场景...
      </div>
    );
  }

  return (
    <section
      className={cn(
        "rounded-[24px] border border-[#FFD100]/[0.45] bg-white/[0.78] shadow-[0_18px_45px_-34px_rgba(17,24,39,0.55)] backdrop-blur-xl",
        compact ? "px-3 py-3" : "px-4 py-4",
      )}
    >
      <div className="mb-3 flex items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold tracking-tight text-ink-900">
            {compact ? "快速试试" : "今天想安排点什么？"}
          </h2>
          {!compact && (
            <p className="mt-1 text-sm leading-relaxed text-ink-500">
              点一个场景，或直接在底部随口说需求。
            </p>
          )}
        </div>
      </div>

      <div
        className={cn(
          "flex snap-x gap-2 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
          !compact && "grid grid-cols-2 overflow-visible",
        )}
      >
        {scenarios.map((scenario) => {
          const ScenarioIcon = scenarioIcon(scenario.id);
          return (
            <button
              key={scenario.id}
              type="button"
              disabled={streaming}
              onClick={() => sendScenario(scenario.input, scenario.id)}
              className={cn(
                "snap-start rounded-2xl border border-[#FFD100]/[0.55] bg-white/[0.86] text-left shadow-sm transition active:scale-[0.98]",
                "disabled:cursor-not-allowed disabled:opacity-55",
                compact
                  ? "flex min-w-[132px] items-center gap-2 px-3 py-2.5"
                  : "min-h-[88px] px-3.5 py-3",
              )}
              title={scenario.input}
            >
              <ScenarioIcon
                className={cn("text-amber-600", compact ? "h-4 w-4" : "h-5 w-5")}
                strokeWidth={2}
              />
              <span className={cn("block font-semibold tracking-tight text-ink-900", compact ? "text-sm" : "mt-2 text-base")}>
                {scenario.title}
              </span>
              {!compact && (
                <span className="mt-1 block line-clamp-1 text-xs text-ink-500">
                  {scenario.input}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}

function MobileIntentStrip() {
  const intent = useChatStore((s) => s.intent);
  if (!intent) return null;

  const duration = intent.duration_hours || [0, 0];
  const companions = intent.companions || [];
  const companionText = companions.length
    ? companions.map((c) => `${c.role}${c.count > 1 ? `×${c.count}` : ""}`).join("、")
    : "轻松出发";

  return (
    <div className="rounded-[22px] border border-black/[0.06] bg-white/[0.84] px-4 py-3 shadow-sm backdrop-blur-xl">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-sm font-semibold text-ink-900">
          <Sparkles className="h-4 w-4 text-amber-500" />
          需求摘要
        </div>
        <span className="text-xs font-medium text-ink-500">
          {Math.round((intent.parse_confidence ?? 0.88) * 100)}%
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <MiniField label="时间" value={`${formatStartTimeLabel(intent.start_time)} · ${duration[0]}-${duration[1]} 小时`} />
        <MiniField label="距离" value={`${intent.distance_max_km} km`} />
        <MiniField label="同行" value={companionText} />
        <MiniField label="场景" value={intent.social_context} />
      </div>
    </div>
  );
}

function MiniField({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-black/[0.025] px-3 py-2">
      <div className="text-xs font-semibold text-ink-500">{label}</div>
      <div className="mt-0.5 truncate text-sm font-semibold text-ink-900">
        {value}
      </div>
    </div>
  );
}

function MobileConversation() {
  const messages = useChatStore((s) => s.messages);
  const chitchatReplies = useChatStore((s) => s.chitchatReplies);
  const streaming = useChatStore((s) => s.streaming);
  const intent = useChatStore((s) => s.intent);

  const timeline = useMemo(() => {
    return [
      ...messages.map((m) => ({
        kind: "message" as const,
        id: m.id,
        ts: m.createdAt,
        role: m.role,
        text: m.text,
      })),
      ...chitchatReplies.map((r) => ({
        kind: "chitchat" as const,
        id: r.id,
        ts: r.receivedAtMs,
        role: "agent" as const,
        text: r.payload.reply_text,
      })),
    ].sort((a, b) => a.ts - b.ts);
  }, [messages, chitchatReplies]);

  if (!timeline.length && !streaming && !intent) return null;

  return (
    <section className="mt-3 space-y-2.5">
      {timeline.slice(-6).map((item) => (
        <MobileBubble key={item.id} role={item.role} text={item.text} />
      ))}
      {intent && <IntentSummary intent={intent} />}
      {streaming && (
        <div className="inline-flex items-center gap-2 rounded-full border border-[#FFD100]/[0.45] bg-white/[0.84] px-3 py-2 text-sm font-medium text-amber-700 shadow-sm backdrop-blur-xl">
          <Loader2 className="h-4 w-4 animate-spin" />
          Agent 正在规划，稍候~
        </div>
      )}
    </section>
  );
}

function MobileBubble({
  role,
  text,
}: {
  role: "user" | "agent";
  text: string;
}) {
  const isUser = role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[86%] rounded-3xl px-4 py-2.5 text-[15px] leading-relaxed tracking-tight shadow-sm",
          isUser
            ? "rounded-br-lg bg-[#FFD100] text-ink-900"
            : "rounded-bl-lg border border-black/[0.06] bg-white/[0.86] text-ink-800 backdrop-blur-xl",
        )}
      >
        {text}
      </div>
    </div>
  );
}

function MobilePlanCard() {
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);

  if (!itinerary && !streaming) return null;

  if (!itinerary) {
    return (
      <section className="mt-3 rounded-[24px] border border-[#FFD100]/[0.42] bg-white/[0.82] px-4 py-4 shadow-sm backdrop-blur-xl">
        <div className="flex items-center gap-2 text-sm font-semibold text-amber-700">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在拼装行程方案~
        </div>
        <div className="mt-4 space-y-2.5">
          <div className="h-4 rounded-full shimmer-skeleton" />
          <div className="h-4 w-4/5 rounded-full shimmer-skeleton" />
          <div className="h-20 rounded-2xl shimmer-skeleton" />
        </div>
      </section>
    );
  }

  const visibleEntries = getVisibleEntries(itinerary);
  const hasOrders = itinerary.orders.length > 0;

  return (
    <article className="mt-3 overflow-hidden rounded-[28px] border border-black/[0.06] bg-white/[0.88] shadow-[0_22px_55px_-38px_rgba(17,24,39,0.7)] backdrop-blur-xl">
      <div className="border-b border-black/[0.06] px-4 py-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <ClipboardList className="h-4 w-4 text-ink-700" />
            <span className="text-base font-semibold tracking-tight text-ink-900">
              行程方案
            </span>
          </div>
          <span className="rounded-full bg-black/[0.035] px-2.5 py-1 text-xs font-semibold text-ink-600">
            约 {(itinerary.total_minutes / 60).toFixed(1)} 小时
          </span>
        </div>
        <h2 className="mt-2 text-xl font-semibold leading-snug tracking-tight text-ink-900">
          {itinerary.summary}
        </h2>
      </div>

      <ol className="relative space-y-0 px-4 py-5">
        <div
          aria-hidden
          className="absolute bottom-9 left-[39px] top-9 w-[2px] rounded-full bg-[linear-gradient(180deg,#12b981_0%,#a6d96a_28%,#f2c94c_52%,#f2a65a_74%,#ef5a5a_100%)] opacity-90 shadow-[0_0_0_1px_rgba(255,255,255,0.72)]"
        />
        <TimelineEndpoint label="出发" tone="start" />
        {visibleEntries.map((entry) =>
          entry.entry_kind === "hop" ? (
            <TimelineHop key={entry.ref_id} entry={entry} />
          ) : (
            <TimelineNode
              key={entry.ref_id}
              entry={entry}
              itinerary={itinerary}
            />
          ),
        )}
        <TimelineEndpoint label="满载而归" tone="end" />
      </ol>

      {hasOrders && (
        <div className="mx-4 mb-3 rounded-2xl border border-emerald-500/20 bg-emerald-500/[0.08] px-3 py-2.5">
          <div className="text-sm font-semibold text-emerald-700">
            已为你预留
          </div>
          <div className="mt-1 space-y-1">
            {itinerary.orders.map((order) => (
              <div key={order.order_id} className="text-sm text-emerald-800/85">
                {order.target_name} · {order.detail}
              </div>
            ))}
          </div>
        </div>
      )}

    </article>
  );
}

function MobileInlineMap({ itinerary }: { itinerary: Itinerary }) {
  return (
    <section className="mt-3">
      <div className="mb-2 flex items-center justify-between gap-3 px-1">
        <div className="flex items-center gap-2">
          <Route className="h-4 w-4 text-amber-700" />
          <h2 className="text-base font-semibold tracking-tight text-ink-900">
            地图路线
          </h2>
        </div>
      </div>
      <div className="[&_.card]:mt-0 [&_.card]:overflow-hidden [&_.card]:rounded-[28px] [&_.card]:border-black/[0.06] [&_.card]:bg-white/[0.88] [&_.card]:shadow-[0_18px_46px_-36px_rgba(17,24,39,0.68)] [&_.card]:backdrop-blur-xl">
        <MapOverlay visibleCount={itinerary.schedule?.length || itinerary.nodes.length} />
      </div>
    </section>
  );
}

function MobileInlineCompare({
  previousItinerary,
  itinerary,
}: {
  previousItinerary: Itinerary;
  itinerary: Itinerary;
}) {
  return (
    <section className="mt-3 overflow-hidden rounded-[28px] border border-[#FFD100]/[0.30] bg-white/[0.88] shadow-[0_18px_46px_-36px_rgba(17,24,39,0.68)] backdrop-blur-xl">
      <div className="flex items-center gap-2 border-b border-black/[0.06] px-4 py-3.5">
        <Sparkles className="h-4 w-4 text-amber-600" />
        <h2 className="text-base font-semibold tracking-tight text-ink-900">
          调整对比
        </h2>
      </div>
      <div className="overflow-x-auto px-3 py-3">
        <div className="min-w-[520px]">
          <ComparisonView oldItinerary={previousItinerary} newItinerary={itinerary} />
        </div>
      </div>
    </section>
  );
}

function MobileActionRail({ onOpenTrace }: { onOpenTrace: () => void }) {
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);
  const toolCalls = useChatStore((s) => s.toolCalls);
  const replans = useChatStore((s) => s.replans);
  const thoughts = useChatStore((s) => s.thoughts);
  const confirm = useChatStore((s) => s.confirm);
  const cancel = useChatStore((s) => s.cancel);

  const hasTrace = Boolean(toolCalls.length || replans.length || thoughts.length);
  if (!itinerary && !streaming && !hasTrace) return null;

  const latestThought = thoughts[thoughts.length - 1]?.text;
  const doneCount = toolCalls.filter((t) => t.endedAtSeq != null).length;

  if (streaming) {
    return (
      <div className="pointer-events-none fixed inset-x-0 bottom-[calc(82px+env(safe-area-inset-bottom,0px))] z-40 px-4">
        <button
          type="button"
          onClick={onOpenTrace}
          className={cn(
            "pointer-events-auto mx-auto block w-full max-w-[480px] rounded-[24px] border border-[#FFD100]/[0.46] px-4 py-3 text-left",
            "bg-white/[0.70] shadow-[0_18px_48px_-30px_rgba(17,24,39,0.82),0_0_34px_-22px_rgba(255,209,0,0.80)] backdrop-blur-2xl backdrop-saturate-150",
            "transition-all duration-300 ease-out animate-drawer-slide-up active:scale-[0.99]",
          )}
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2.5">
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#FFD100]/22 text-amber-700">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
              <div className="min-w-0">
                <div className="text-sm font-semibold tracking-tight text-ink-900">
                  Agent 正在思考
                </div>
                <div className="mt-0.5 text-xs font-medium text-ink-500">
                  {doneCount}/{toolCalls.length || 1} 调用
                  {replans.length > 0 ? ` · ${replans.length} 次重规划` : ""}
                </div>
              </div>
            </div>
            <ChevronRight className="h-4 w-4 shrink-0 text-ink-400" />
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[#FFD100]/18">
            <div className="h-full w-1/2 rounded-full bg-gradient-to-r from-[#FFD100]/40 via-[#FFD100] to-[#FFD100]/40 animate-shimmer-x" />
          </div>
          <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-ink-600">
            {latestThought ?? "正在拆解需求、查找候选并拼装行程~"}
          </p>
        </button>
      </div>
    );
  }

  const hasOrders = itinerary?.orders.length ?? 0;
  const canAct = !streaming && !cancelled && !hasOrders;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(82px+env(safe-area-inset-bottom,0px))] z-40 px-4">
      <div className="pointer-events-auto mx-auto grid max-w-[480px] grid-cols-3 items-center gap-2 rounded-full border border-white/[0.74] bg-white/[0.58] p-1.5 shadow-[0_18px_44px_-30px_rgba(17,24,39,0.78)] backdrop-blur-2xl backdrop-saturate-150">
        {(hasTrace || itinerary) && (
          <button
            type="button"
            onClick={onOpenTrace}
            disabled={!hasTrace}
            className="min-h-10 min-w-0 rounded-full bg-white/[0.72] px-2 text-sm font-semibold text-ink-700 shadow-sm transition active:scale-[0.98] disabled:text-ink-400"
          >
            Agent思考链路
          </button>
        )}
        {itinerary && !hasOrders && !cancelled && (
          <>
            <button
              type="button"
              className="min-h-10 min-w-0 rounded-full bg-[#FFD100] px-2 text-sm font-bold text-ink-900 shadow-[0_10px_26px_-18px_rgba(245,158,11,0.95)] transition active:scale-[0.98] disabled:bg-[#FFD100]/45 disabled:text-ink-500"
              disabled={!canAct}
              onClick={confirm}
            >
              {streaming ? "执行中" : "确认并预约"}
            </button>
            <button
              type="button"
              className="min-h-10 min-w-0 rounded-full bg-white/[0.72] px-2 text-sm font-semibold text-red-500 shadow-sm transition active:scale-[0.98] disabled:text-ink-400"
              disabled={streaming}
              onClick={cancel}
            >
              取消方案
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function TimelineEndpoint({
  label,
  tone,
}: {
  label: string;
  tone: "start" | "end";
}) {
  return (
    <li className="relative flex min-h-12 items-center py-2 pl-16">
      <span className="absolute left-0 top-1/2 z-10 grid h-12 w-12 -translate-y-1/2 place-items-center">
        <span
          className={cn(
            "h-[22px] w-[22px] rounded-full border-[4px] border-white shadow-[0_8px_18px_-13px_rgba(17,24,39,0.75)]",
            tone === "start"
              ? "bg-emerald-500 ring-1 ring-emerald-500/18"
              : "bg-red-500 ring-1 ring-red-500/18",
          )}
        />
      </span>
      <span
        className={cn(
          "text-base font-semibold tracking-tight",
          tone === "start" ? "text-emerald-700" : "text-red-600",
        )}
      >
        {label}
      </span>
    </li>
  );
}

function TimelineHop({ entry }: { entry: ScheduleEntry }) {
  if (!entry.mode || entry.mode === "virtual") return null;
  return (
    <li className="relative py-2.5 pl-16">
      <span className="absolute left-0 top-1/2 z-10 grid h-12 w-12 -translate-y-1/2 place-items-center">
        <span className="h-[10px] w-[10px] rounded-full bg-[#F6C945] shadow-[0_0_0_4px_rgba(255,255,255,0.96),0_7px_14px_-10px_rgba(17,24,39,0.72)] ring-1 ring-[#d9a900]/25" />
      </span>
      <div className="rounded-xl border border-black/[0.05] bg-black/[0.02] px-3 py-1.5 text-sm text-ink-500">
        通勤 {entry.minutes} 分钟（{translateHopMode(entry.mode)}）
      </div>
    </li>
  );
}

function TimelineNode({
  entry,
  itinerary,
}: {
  entry: ScheduleEntry;
  itinerary: Itinerary;
}) {
  const node = itinerary.nodes.find((n) => n.node_id === entry.ref_id);
  return (
    <li className="relative py-3 pl-16">
      <span className="absolute left-0 top-[25px] z-10 grid h-12 w-12 -translate-y-1/2 place-items-center">
        <span className="h-[16px] w-[16px] rounded-full bg-[#F6C945] shadow-[0_0_0_4px_rgba(255,255,255,0.96),0_8px_16px_-11px_rgba(17,24,39,0.76)] ring-1 ring-[#d9a900]/25" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-bold tabular-nums text-ink-800">
            {entry.start}
          </span>
          <span className="rounded-md border border-[#FFD100]/[0.35] bg-[#FFD100]/[0.14] px-1.5 py-0.5 text-xs font-semibold text-ink-700">
            {node?.kind ?? "活动"}
          </span>
        </div>
        <div className="mt-1 text-lg font-semibold leading-snug tracking-tight text-ink-900">
          {entry.title}
        </div>
        {node?.note && (
          <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-ink-500">
            {node.note}
          </p>
        )}
      </div>
    </li>
  );
}

function MobileComposer() {
  const sendMessage = useChatStore((s) => s.sendMessage);
  const streaming = useChatStore((s) => s.streaming);
  const messages = useChatStore((s) => s.messages);
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const submit = () => {
    const text = draft.trim();
    if (!text || streaming) return;
    sendMessage(text);
    setDraft("");
    requestAnimationFrame(() => textareaRef.current?.blur());
  };

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 bg-transparent px-4 pb-[calc(12px+env(safe-area-inset-bottom,0px))] pt-3">
      <div className="mx-auto max-w-[480px]">
        <div
          className={cn(
            "pointer-events-auto",
            "group/mobile-composer flex items-end gap-2 rounded-full border border-[#FFD100]/[0.36] px-4 py-2",
            "bg-white/[0.56] shadow-[0_20px_48px_-30px_rgba(17,24,39,0.72),0_0_28px_-18px_rgba(255,209,0,0.72),inset_0_1px_0_rgba(255,255,255,0.78)]",
            "backdrop-blur-2xl backdrop-saturate-150 transition-all duration-300 ease-out",
            "hover:border-[#FFD100]/[0.72] hover:bg-white/[0.82] hover:shadow-[0_20px_50px_-30px_rgba(17,24,39,0.64),0_0_0_4px_rgba(255,209,0,0.10),inset_0_1px_0_rgba(255,255,255,0.92)]",
            "focus-within:border-[#FFD100]/[0.78] focus-within:bg-white/[0.90] focus-within:shadow-[0_22px_55px_-32px_rgba(17,24,39,0.68),0_0_0_4px_rgba(255,209,0,0.14),inset_0_1px_0_rgba(255,255,255,0.96)]",
          )}
        >
          <textarea
            ref={textareaRef}
            rows={1}
            value={draft}
            disabled={streaming}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={
              streaming
                ? "Agent 正在规划，稍候~"
                : messages.length === 0
                  ? "想去哪儿、和谁去，都可以随口说~"
                  : "哪里安排得不合适，告诉我来改~"
            }
            className="max-h-24 min-h-10 flex-1 resize-none bg-transparent py-2 text-base leading-6 text-ink-900 placeholder:text-ink-500 focus:outline-none disabled:text-ink-500"
          />
          <button
            type="button"
            onClick={submit}
            disabled={streaming || !draft.trim()}
            className={cn(
              "mb-0.5 mr-[-0.25rem] grid h-10 w-10 shrink-0 place-items-center rounded-full border border-[#e6bc00]/40 bg-[#FFD100] p-0 text-ink-900",
              "shadow-[0_10px_26px_-16px_rgba(245,158,11,0.95)] transition-all duration-300 ease-out",
              "group-hover/mobile-composer:scale-[1.02] group-hover/mobile-composer:bg-[#ffdb2e] group-hover/mobile-composer:shadow-[0_12px_28px_-15px_rgba(245,158,11,0.98)]",
              "group-focus-within/mobile-composer:scale-[1.02] group-focus-within/mobile-composer:bg-[#ffdb2e] group-focus-within/mobile-composer:shadow-[0_12px_28px_-15px_rgba(245,158,11,0.98)]",
              "active:scale-95 disabled:border-[#FFD100]/[0.30] disabled:bg-[#FFD100]/[0.44] disabled:text-ink-500 disabled:shadow-[inset_0_1px_0_rgba(255,255,255,0.72)]",
            )}
            aria-label="发送"
          >
            {streaming ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <ArrowRightIcon className="h-5 w-5" strokeWidth={2.75} />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

function MobileSheet({
  open,
  title,
  icon,
  onClose,
  children,
  tall = false,
}: {
  open: boolean;
  title: string;
  icon: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  tall?: boolean;
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50">
      <button
        type="button"
        className="absolute inset-0 bg-black/[0.24] backdrop-blur-[2px]"
        onClick={onClose}
        aria-label="关闭弹层"
      />
      <section
        className={cn(
          "absolute inset-x-0 bottom-0 mx-auto max-w-[480px] rounded-t-[30px] border border-black/[0.06] bg-white/[0.96] shadow-2xl backdrop-blur-2xl",
          "animate-drawer-slide-up",
          tall ? "max-h-[88vh]" : "max-h-[72vh]",
        )}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-black/[0.06] bg-white/90 px-4 py-3 backdrop-blur-xl">
          <div className="flex items-center gap-2 text-base font-semibold tracking-tight text-ink-900">
            <span className="grid h-8 w-8 place-items-center rounded-full bg-[#FFD100]/20 text-amber-700">
              {icon}
            </span>
            {title}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-9 w-9 place-items-center rounded-full bg-black/[0.04] text-ink-600"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-[calc(88vh-62px)] overflow-y-auto px-4 py-4">
          {children}
        </div>
      </section>
    </div>
  );
}

function getVisibleEntries(itinerary: Itinerary): ScheduleEntry[] {
  if (itinerary.schedule?.length) {
    return itinerary.schedule.filter((entry) => !entry.hidden);
  }
  return itinerary.nodes
    .filter((node) => node.target_kind !== "home")
    .map((node) => ({
      entry_kind: "node" as const,
      ref_id: node.node_id,
      start: node.start_time,
      end: addMinutes(node.start_time, node.duration_min),
      title: node.title,
      minutes: node.duration_min,
      mode: null,
      hidden: false,
    }));
}

function addMinutes(start: string, minutes: number): string {
  const m = /^(\d{1,2}):(\d{2})$/.exec(start);
  if (!m) return start;
  const total = Number(m[1]) * 60 + Number(m[2]) + (minutes || 0);
  const wrap = ((total % (24 * 60)) + 24 * 60) % (24 * 60);
  return `${String(Math.floor(wrap / 60)).padStart(2, "0")}:${String(wrap % 60).padStart(2, "0")}`;
}

function translateHopMode(mode: HopMode): string {
  switch (mode) {
    case "walking":
      return "步行";
    case "taxi":
      return "打车";
    case "bus":
      return "公交";
    case "haversine_estimated":
      return "估算";
    case "virtual":
      return "原地";
    default:
      return mode;
  }
}
