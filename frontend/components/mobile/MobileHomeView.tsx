"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  ChevronRight,
  ArrowRight as ArrowRightIcon,
  Loader2,
  Plus,
  Route,
  Sparkles,
  Users,
  X,
} from "lucide-react";

import { scenarioIcon } from "@/lib/icon-map";
import {
  buildCollabChatStateSnapshot,
  buildCollabPlanningEvents,
  useCollabStore,
} from "@/lib/collab-store";
import { useChatStore } from "@/lib/store";
import { formatStartTimeLabel } from "@/lib/time-labels";
import type {
  DecisionTrace,
  HopMode,
  IntentExtraction,
  Itinerary,
  ScheduleEntry,
} from "@/lib/types";
import {
  clearUserIdCookie,
  cn,
  FAILURE_REASON_LABEL,
  generateSessionId,
  TOOL_LABEL,
  upsertSession,
} from "@/lib/utils";

import ComparisonView from "../ComparisonView";
import MapOverlay from "../MapOverlay";
import PosterGenerator from "../PosterGenerator";
import ShareModal from "../ShareModal";
import ToastStack from "../ToastStack";
import ToolTracePanel from "../ToolTracePanel";
import TtsPlayer from "../TtsPlayer";
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
  const roomId = useCollabStore((s) => s.roomId);

  const [sheet, setSheet] = useState<SheetKind>(null);
  const [shareModalOpen, setShareModalOpen] = useState(false);
  const [visibleIntent, setVisibleIntent] = useState<IntentExtraction | null>(null);
  const personaResetOnLoadRef = useRef(false);
  const visibleIntentSessionRef = useRef(sessionId);
  const activated = messages.length > 0 || streaming || itinerary != null;
  const canCompare = Boolean(previousItinerary && itinerary && lastRefinement);

  useEffect(() => {
    if (visibleIntentSessionRef.current !== sessionId) {
      visibleIntentSessionRef.current = sessionId;
      setVisibleIntent(null);
    }
  }, [sessionId]);

  useEffect(() => {
    if (intent) setVisibleIntent(intent);
  }, [intent]);

  useEffect(() => {
    if (sessionId === "sess_pending") {
      const newId = generateSessionId();
      useChatStore.setState({ sessionId: newId });
      upsertSession({ id: newId, label: "新对话", lastMessageAt: Date.now() });
    } else {
      upsertSession({ id: sessionId, lastMessageAt: Date.now() });
    }
    if (!personaResetOnLoadRef.current) {
      personaResetOnLoadRef.current = true;
      clearUserIdCookie();
      useChatStore.setState({ currentUserId: "demo_user", preferences: null });
    }
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

        <MobileConversation
          visibleIntent={visibleIntent}
          itinerary={itinerary}
        />

        <MobilePlanCard />

        {itinerary && <MobileInlineMap itinerary={itinerary} />}

        {canCompare && previousItinerary && itinerary && (
          <MobileInlineCompare
            previousItinerary={previousItinerary}
            itinerary={itinerary}
          />
        )}
      </main>

      <MobileActionRail
        onOpenTrace={() => setSheet("trace")}
        onOpenShareModal={() => setShareModalOpen(true)}
      />
      <MobileComposer />
      <ToastStack />

      <MobileSheet
        open={sheet === "trace"}
        title="Agent 思考链路"
        icon={<Bot className="h-4 w-4" />}
        onClose={() => setSheet(null)}
        showHeader={false}
      >
        <MobileAgentInsightTabs
          decisionTrace={itinerary?.decision_trace}
          onClose={() => setSheet(null)}
        />
      </MobileSheet>

      {roomId && (
        <ShareModal
          open={shareModalOpen}
          onClose={() => setShareModalOpen(false)}
          roomId={roomId}
        />
      )}

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

function MobileIntentStrip({ intent }: { intent: IntentExtraction }) {
  const duration = intent.duration_hours || [0, 0];
  const companions = intent.companions || [];
  const companionText = companions.length
    ? companions.map((c) => `${c.role}${c.count > 1 ? `×${c.count}` : ""}`).join("、")
    : "轻松出发";

  return (
    <div className="rounded-[22px] border border-black/[0.06] bg-white/[0.84] px-4 py-3.5 shadow-sm backdrop-blur-xl">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-sm font-semibold text-ink-900">
          <Sparkles className="h-4 w-4 text-amber-500" />
          意图解析
        </div>
        <span className="text-xs font-medium text-ink-500">
          {Math.round((intent.parse_confidence ?? 0.88) * 100)}%
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2.5 min-[380px]:grid-cols-2">
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
    <div className="min-w-0 rounded-2xl bg-black/[0.025] px-3 py-2.5">
      <div className="text-xs font-bold text-ink-500">{label}</div>
      <div className="mt-1 whitespace-normal break-words text-[15px] font-semibold leading-snug text-ink-900">
        {value}
      </div>
    </div>
  );
}

function MobileIntentFallback({ itinerary }: { itinerary: Itinerary }) {
  const visibleEntries = getVisibleEntries(itinerary);
  const activityEntries = visibleEntries.filter(
    (entry) => entry.entry_kind === "node" && entry.title,
  );
  const firstActivity = activityEntries[0];
  const timeText = firstActivity
    ? `${firstActivity.start} 起 · 约 ${(itinerary.total_minutes / 60).toFixed(1)} 小时`
    : `约 ${(itinerary.total_minutes / 60).toFixed(1)} 小时`;

  return (
    <div className="rounded-[22px] border border-black/[0.06] bg-white/[0.84] px-4 py-3.5 shadow-sm backdrop-blur-xl">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-1.5 text-base font-bold tracking-tight text-ink-900">
          <Sparkles className="h-4 w-4 text-amber-500" />
          意图解析
        </div>
        <span className="shrink-0 rounded-full bg-[#FFD100]/[0.16] px-2.5 py-1 text-xs font-semibold text-ink-700">
          已匹配
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2.5 min-[380px]:grid-cols-2">
        <MiniField label="时间" value={timeText} />
        <MiniField label="方案" value={itinerary.summary} />
      </div>
      {activityEntries.length > 0 && (
        <div className="mt-3 border-t border-black/[0.06] pt-3">
          <div className="mb-2 text-sm font-bold text-ink-700">标签</div>
          <div className="flex flex-wrap gap-1.5">
            {activityEntries.slice(0, 4).map((entry) => (
              <span
                key={`fallback-intent-${entry.ref_id}`}
                className="rounded-[10px] border border-[#FFD100]/45 bg-[#FFD100]/[0.10] px-2.5 py-1 text-sm font-medium leading-tight text-ink-800"
              >
                {entry.title}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MobileConversation({
  visibleIntent,
  itinerary,
}: {
  visibleIntent: IntentExtraction | null;
  itinerary: Itinerary | null;
}) {
  const messages = useChatStore((s) => s.messages);
  const chitchatReplies = useChatStore((s) => s.chitchatReplies);
  const streaming = useChatStore((s) => s.streaming);

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

  const showIntentSummary = visibleIntent || itinerary;

  if (!timeline.length && !streaming && !showIntentSummary) return null;

  return (
    <section className="mt-3 space-y-2.5">
      {timeline.slice(-6).map((item) => (
        <MobileBubble key={item.id} role={item.role} text={item.text} />
      ))}
      {visibleIntent ? (
        <MobileIntentStrip intent={visibleIntent} />
      ) : itinerary ? (
        <MobileIntentFallback itinerary={itinerary} />
      ) : null}
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
  const noteItems = visibleEntries
    .map((entry, entryIndex) => ({ entry, entryIndex }))
    .filter(({ entry }) => entry.entry_kind === "node");
  const hasOrders = itinerary.orders.length > 0;

  return (
    <article
      className="relative mt-3 overflow-hidden rounded-[30px] border border-black/[0.06] bg-white shadow-[0_26px_60px_-42px_rgba(17,24,39,0.62)]"
    >
      <div
        aria-hidden
        className="absolute right-6 top-0 h-8 w-14 -translate-y-2 rotate-6 rounded-b-lg bg-[#e8d7b5]/70 shadow-sm"
      />
      <div className="relative px-4 pb-2 pt-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-2xl" aria-hidden>
                📷
              </span>
              <span className="text-lg font-black tracking-tight text-[#8f4b24]">
                今日行程安排
              </span>
            </div>
            <h2 className="mt-2 text-xl font-black leading-snug tracking-tight text-ink-900">
              {itinerary.summary}
            </h2>
          </div>
          <span className="shrink-0 rounded-full bg-white/70 px-2.5 py-1 text-xs font-bold text-[#8f4b24] shadow-sm">
            约 {(itinerary.total_minutes / 60).toFixed(1)} 小时
          </span>
        </div>
      </div>

      <ol className="relative px-3 pb-5 pt-3">
        {noteItems.map(({ entry, entryIndex }, index) => {
          const node = itinerary.nodes.find((n) => n.node_id === entry.ref_id);
          const previous = visibleEntries[entryIndex - 1];
          const hopBefore =
            previous?.entry_kind === "hop" && previous.mode !== "virtual"
              ? previous
              : null;
          return (
            <NotebookTimelineItem
              key={entry.ref_id}
              entry={entry}
              node={node}
              hopBefore={hopBefore}
              index={index}
              total={noteItems.length}
            />
          );
        })}
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

const NOTEBOOK_TONES = [
  {
    dot: "bg-[#3f6fb6]",
    line: "bg-gradient-to-b from-[#3f6fb6] to-[#d9487b]",
    time: "text-[#3f6fb6]",
    chip: "bg-[#eaf1ff] text-[#315a96]",
  },
  {
    dot: "bg-[#d9487b]",
    line: "bg-gradient-to-b from-[#d9487b] to-[#e59f28]",
    time: "text-[#c23b6a]",
    chip: "bg-[#ffeaf2] text-[#a8325d]",
  },
  {
    dot: "bg-[#e59f28]",
    line: "bg-gradient-to-b from-[#e59f28] to-[#4fa565]",
    time: "text-[#b97818]",
    chip: "bg-[#fff2d6] text-[#986415]",
  },
  {
    dot: "bg-[#4fa565]",
    line: "bg-gradient-to-b from-[#4fa565] to-[#7a5cc9]",
    time: "text-[#3d8651]",
    chip: "bg-[#e9f7ed] text-[#347046]",
  },
  {
    dot: "bg-[#7a5cc9]",
    line: "bg-gradient-to-b from-[#7a5cc9] to-[#47627f]",
    time: "text-[#6850aa]",
    chip: "bg-[#f0ecff] text-[#59469a]",
  },
  {
    dot: "bg-[#47627f]",
    line: "bg-gradient-to-b from-[#47627f] to-[#3f6fb6]",
    time: "text-[#40566f]",
    chip: "bg-[#edf2f7] text-[#384b61]",
  },
] as const;

function NotebookTimelineItem({
  entry,
  node,
  hopBefore,
  index,
  total,
}: {
  entry: ScheduleEntry;
  node?: Itinerary["nodes"][number];
  hopBefore: ScheduleEntry | null;
  index: number;
  total: number;
}) {
  const tone = NOTEBOOK_TONES[index % NOTEBOOK_TONES.length];
  const icon = getNotebookIcon(node?.kind, entry.title);
  const tags = buildNotebookTags(node, hopBefore);

  return (
    <li className="relative grid grid-cols-[48px_minmax(0,1fr)] gap-3 pb-6 last:pb-1">
      <div className="relative flex justify-center">
        {index < total - 1 && (
          <span
            aria-hidden
            className={cn(
              "absolute top-10 bottom-[-28px] w-[3px] rounded-full opacity-90",
              tone.line,
            )}
          />
        )}
        <span
          className={cn(
            "relative z-10 grid h-9 w-9 place-items-center rounded-full border-[3px] border-white text-base font-black text-white shadow-[0_8px_18px_-12px_rgba(17,24,39,0.75)]",
            tone.dot,
          )}
        >
          {index + 1}
        </span>
      </div>

      <div
        className="min-w-0 rounded-[22px] border border-white/80 px-3 py-3 shadow-[0_14px_34px_-26px_rgba(17,24,39,0.55),inset_0_1px_0_rgba(255,255,255,0.92)] ring-1 ring-[#FFD100]/10"
        style={{
          background:
            "linear-gradient(135deg, rgba(255,255,255,0.94) 0%, rgba(255,252,237,0.88) 46%, rgba(241,250,245,0.78) 76%, rgba(239,247,255,0.68) 100%)",
        }}
      >
        <div className={cn("flex items-center gap-1.5 text-sm font-black tabular-nums", tone.time)}>
          <span aria-hidden>◷</span>
          <span className="rounded-sm px-0.5">
            {entry.start} - {entry.end}
          </span>
        </div>

        {hopBefore && (
          <div className="mt-2 inline-flex max-w-full items-center gap-1.5 rounded-full border border-[#eadfc9]/70 bg-white/75 px-3 py-1 text-xs font-semibold text-ink-500 shadow-sm">
            <span aria-hidden>{getHopIcon(hopBefore.mode)}</span>
            <span className="truncate">
              通勤 {hopBefore.minutes} 分钟 · {hopBefore.mode ? translateHopMode(hopBefore.mode) : "路上"}
            </span>
          </div>
        )}

        <h3 className="mt-2.5 flex items-start gap-2 text-xl font-black leading-snug tracking-tight text-ink-900">
          <span className="mt-0.5 shrink-0" aria-hidden>
            {icon}
          </span>
          <span
            className="block flex-1 rounded-sm px-0.5"
            style={{
              textDecorationLine: "underline",
              textDecorationStyle: "wavy",
              textDecorationColor: "rgba(185, 130, 42, 0.38)",
              textDecorationThickness: "1.4px",
              textUnderlineOffset: "6px",
            }}
          >
            {entry.title}
          </span>
        </h3>

        {node?.note && (
          <p className="mt-2 border-t border-[#eadfc9]/60 pt-2 text-[15px] leading-relaxed text-ink-700">
            {node.note}
          </p>
        )}

        {tags.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {tags.map((tag) => (
              <span
                key={`${entry.ref_id}-${tag}`}
                className={cn(
                  "rounded-full px-2.5 py-1 text-xs font-bold shadow-sm",
                  tone.chip,
                )}
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    </li>
  );
}

function buildNotebookTags(
  node: Itinerary["nodes"][number] | undefined,
  hopBefore: ScheduleEntry | null,
): string[] {
  const tags = new Set<string>();
  if (node?.kind) tags.add(node.kind);
  if (node?.target_kind === "restaurant") tags.add("美食");
  if (node?.target_kind === "poi") tags.add("活动");
  if (hopBefore?.mode && hopBefore.mode !== "virtual") {
    tags.add(translateHopMode(hopBefore.mode));
  }
  return Array.from(tags).slice(0, 3);
}

function getNotebookIcon(kind: string | undefined, title: string): string {
  const text = `${kind ?? ""} ${title}`;
  if (/餐|饭|烤|茶|食|料理|咖啡/.test(text)) return "🍜";
  if (/音乐|演出|展|剧|看/.test(text)) return "🎵";
  if (/书|图书|阅读/.test(text)) return "📚";
  if (/商场|购物|店|街/.test(text)) return "🛍️";
  if (/公园|漫步|园|湖/.test(text)) return "🌿";
  if (/亲子|孩子|儿童/.test(text)) return "🧸";
  return "📍";
}

function getHopIcon(mode: HopMode | null | undefined): string {
  switch (mode) {
    case "walking":
      return "🚶";
    case "bus":
      return "🚌";
    case "taxi":
      return "🚕";
    default:
      return "🚗";
  }
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

function MobileActionRail({
  onOpenTrace,
  onOpenShareModal,
}: {
  onOpenTrace: () => void;
  onOpenShareModal: () => void;
}) {
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);
  const toolCalls = useChatStore((s) => s.toolCalls);
  const replans = useChatStore((s) => s.replans);
  const thoughts = useChatStore((s) => s.thoughts);
  const confirm = useChatStore((s) => s.confirm);
  const cancel = useChatStore((s) => s.cancel);
  const pushToast = useChatStore((s) => s.pushToast);
  const collabMode = useCollabStore((s) => s.collabMode);
  const roomId = useCollabStore((s) => s.roomId);
  const createRoom = useCollabStore((s) => s.createRoom);
  const joinRoom = useCollabStore((s) => s.joinRoom);
  const [expanded, setExpanded] = useState(false);
  const [creatingRoom, setCreatingRoom] = useState(false);

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
  const canCreateRoom = Boolean(itinerary && !hasOrders && !cancelled && !collabMode);
  const showShareRoom = collabMode && !!roomId;

  const handleCreateRoom = async () => {
    if (creatingRoom || streaming) return;
    if (showShareRoom) {
      onOpenShareModal();
      return;
    }

    setCreatingRoom(true);
    try {
      const state = useChatStore.getState();
      const userId = state.currentUserId || "demo_user";
      const planningEvents = buildCollabPlanningEvents(state);
      const chatState = buildCollabChatStateSnapshot(state);
      const newRoomId = await createRoom(
        userId,
        "发起人",
        state.sessionId,
        planningEvents,
        state.messages as unknown as Record<string, unknown>[],
        chatState,
      );

      if (!newRoomId) {
        pushToast({ kind: "warn", text: "多人房间创建失败" });
        return;
      }

      joinRoom(newRoomId, userId, "发起人");
      onOpenShareModal();
    } finally {
      setCreatingRoom(false);
    }
  };

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(82px+env(safe-area-inset-bottom,0px))] z-40 px-4">
      {expanded && itinerary && !hasOrders && !cancelled && (
        <div className="pointer-events-auto mx-auto mb-2 flex max-w-[480px] justify-end">
          <div className="flex w-[190px] flex-col gap-2 rounded-[24px] border border-white/[0.74] bg-white/[0.72] p-2 shadow-[0_18px_44px_-30px_rgba(17,24,39,0.78)] backdrop-blur-2xl backdrop-saturate-150 animate-drawer-slide-up">
          <TtsPlayer
            compact
            className="!h-10 !rounded-full !text-sm !font-semibold"
          />
          <PosterGenerator
            compact
            className="!h-10 !rounded-full !text-sm !font-semibold"
          />
          <button
            type="button"
            className="flex h-10 items-center justify-center gap-1.5 rounded-full border border-[#e6bc00] bg-[#FFD100] px-3 text-sm font-semibold text-ink-900 transition active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
            disabled={creatingRoom || streaming || (!canCreateRoom && !showShareRoom)}
            onClick={() => void handleCreateRoom()}
          >
            {creatingRoom ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Users className="h-3.5 w-3.5" />
            )}
            <span>{showShareRoom ? "分享房间" : "开多人房间"}</span>
          </button>
          <button
            type="button"
            className="flex h-10 items-center justify-center gap-1.5 rounded-full border border-red-500/15 bg-white/[0.78] px-3 text-sm font-semibold text-red-500 transition active:scale-[0.98] disabled:text-ink-400"
            disabled={streaming}
            onClick={() => {
              setExpanded(false);
              cancel();
            }}
          >
            <X className="h-3.5 w-3.5" />
            <span>取消方案</span>
          </button>
          </div>
        </div>
      )}
      <div className="pointer-events-auto mx-auto flex max-w-[480px] items-center gap-2">
        {(hasTrace || itinerary) && (
          <button
            type="button"
            onClick={onOpenTrace}
            disabled={!hasTrace}
            className="min-h-11 min-w-0 flex-1 rounded-full border border-white/[0.74] bg-white/[0.72] px-2 text-sm font-semibold text-ink-700 shadow-[0_14px_34px_-26px_rgba(17,24,39,0.78)] backdrop-blur-2xl backdrop-saturate-150 transition active:scale-[0.98] disabled:text-ink-400"
          >
            Agent思考链路
          </button>
        )}
        {itinerary && !hasOrders && !cancelled && (
          <>
            <button
              type="button"
              className="min-h-11 min-w-0 flex-1 rounded-full border border-[#e6bc00]/45 bg-[#FFD100] px-2 text-sm font-bold text-ink-900 shadow-[0_14px_34px_-24px_rgba(245,158,11,0.98)] transition active:scale-[0.98] disabled:bg-[#FFD100]/45 disabled:text-ink-500"
              disabled={!canAct}
              onClick={confirm}
            >
              {streaming ? "执行中" : "确认并预约"}
            </button>
            <button
              type="button"
              className={cn(
                "grid h-11 w-11 shrink-0 place-items-center rounded-full border text-sm font-semibold shadow-[0_14px_34px_-26px_rgba(17,24,39,0.78)] backdrop-blur-2xl backdrop-saturate-150 transition active:scale-[0.98] disabled:text-ink-400",
                expanded
                  ? "border-[#e6bc00]/45 bg-[#FFD100] text-ink-900"
                  : "border-white/[0.74] bg-white/[0.72] text-ink-700",
              )}
              disabled={streaming}
              onClick={() => setExpanded((cur) => !cur)}
              aria-expanded={expanded}
              aria-label="展开更多方案工具"
            >
              <Plus
                className={cn(
                  "h-5 w-5 transition-transform duration-200",
                  expanded && "rotate-45",
                )}
              />
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

type AgentInsightTab = "trace" | "thought" | "decision";

function MobileAgentInsightTabs({
  decisionTrace,
  onClose,
}: {
  decisionTrace: DecisionTrace | null | undefined;
  onClose: () => void;
}) {
  const itinerary = useChatStore((s) => s.itinerary);
  const toolCalls = useChatStore((s) => s.toolCalls);
  const thoughts = useChatStore((s) => s.thoughts);
  const replans = useChatStore((s) => s.replans);
  const streaming = useChatStore((s) => s.streaming);
  const [activeTab, setActiveTab] = useState<AgentInsightTab>("trace");

  const tabs: Array<{ id: AgentInsightTab; label: string }> = [
    { id: "trace", label: "Agent思考链路" },
    { id: "thought", label: "Agent在想什么" },
    { id: "decision", label: "决策链路" },
  ];

  const hasThoughts = thoughts.length > 0 || replans.length > 0 || streaming;
  const hasDecisionTrace =
    !isDecisionTraceEmpty(decisionTrace) ||
    Boolean(itinerary || toolCalls.length || thoughts.length || replans.length);

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2">
        <div className="grid min-w-0 flex-1 grid-cols-3 gap-1 rounded-full border border-black/[0.06] bg-black/[0.025] p-1">
          {tabs.map((tab) => {
            const selected = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                className={cn(
                  "min-h-9 rounded-full px-1.5 text-[12px] font-semibold tracking-tight transition active:scale-[0.98]",
                  selected
                    ? "bg-[#FFD100] text-ink-900 shadow-[0_8px_20px_-16px_rgba(245,158,11,0.95)]"
                    : "text-ink-500 hover:bg-white/[0.72] hover:text-ink-800",
                )}
                onClick={() => setActiveTab(tab.id)}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-black/[0.04] text-ink-600 transition hover:bg-black/[0.07] active:scale-95"
          aria-label="关闭"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="[&_.card]:mt-0 [&_.card]:rounded-[22px] [&_.card]:border-black/[0.06] [&_.card]:bg-white/[0.82] [&_.card]:shadow-sm">
        {activeTab === "trace" && <ToolTracePanel />}

        {activeTab === "thought" && (
          hasThoughts ? (
            <MobileThoughtTimeline />
          ) : (
            <MobileInsightEmpty text="这一轮还没有思考过程，发起规划后这里会显示 Agent 的推理节奏。" />
          )
        )}

        {activeTab === "decision" && (
          hasDecisionTrace ? (
            <MobileDecisionTrace trace={decisionTrace} />
          ) : (
            <MobileInsightEmpty text="生成带决策解释的方案后，这里会显示规划思路、修正历史和候选取舍。" />
          )
        )}
      </div>
    </div>
  );
}

function MobileInsightEmpty({ text }: { text: string }) {
  return (
    <div className="rounded-[22px] border border-black/[0.06] bg-white/[0.76] px-4 py-5 text-sm leading-relaxed text-ink-500 shadow-sm">
      {text}
    </div>
  );
}

function MobileThoughtTimeline() {
  const thoughts = useChatStore((s) => s.thoughts);
  const replans = useChatStore((s) => s.replans);
  const streaming = useChatStore((s) => s.streaming);

  const items = [
    ...thoughts.map((thought) => ({
      kind: "thought" as const,
      seq: thought.seq,
      text: thought.text,
      timestampMs: thought.timestamp_ms,
    })),
    ...replans.map((replan) => ({
      kind: "replan" as const,
      seq: replan.seq,
      text: `${FAILURE_REASON_LABEL[replan.reason] ?? replan.reason} · ${replan.fromTool}`,
      timestampMs: null,
    })),
  ].sort((a, b) => a.seq - b.seq);

  return (
    <div className="rounded-[22px] border border-black/[0.06] bg-white/[0.82] px-3 py-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between gap-3 px-1">
        <div className="text-sm font-bold tracking-tight text-ink-900">
          Agent 在想什么
        </div>
        <div className="text-xs font-semibold text-ink-500">
          {thoughts.length} 条思考
          {replans.length > 0 ? ` · ${replans.length} 次重规划` : ""}
        </div>
      </div>

      {items.length === 0 && streaming ? (
        <div className="flex items-center gap-2 rounded-2xl bg-black/[0.025] px-3 py-3 text-sm text-ink-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          等待 Agent 开始思考……
        </div>
      ) : (
        <ol className="space-y-2">
          {items.map((item) => (
            <li
              key={`${item.kind}-${item.seq}`}
              className={cn(
                "rounded-2xl border px-3 py-2.5 text-sm leading-relaxed",
                item.kind === "replan"
                  ? "border-amber-300/35 bg-[#FFD100]/[0.10] text-amber-800"
                  : "border-black/[0.05] bg-black/[0.025] text-ink-700",
              )}
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-ink-500">
                  {item.kind === "replan" ? "重规划" : `思考 ${item.seq}`}
                </span>
                {item.timestampMs != null && (
                  <span className="text-[11px] font-medium text-ink-400">
                    {formatThoughtTime(item.timestampMs)}
                  </span>
                )}
              </div>
              {item.text}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function MobileDecisionTrace({
  trace,
}: {
  trace: DecisionTrace | null | undefined;
}) {
  if (isDecisionTraceEmpty(trace)) {
    return <MobileDecisionFallbackTrace />;
  }

  const t = trace as DecisionTrace;

  return (
    <div className="space-y-3">
      <div className="rounded-[22px] border border-black/[0.06] bg-white/[0.82] px-4 py-3 shadow-sm">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-bold tracking-tight text-ink-900">
            决策链路
          </div>
          <span className="rounded-full border border-[#FFD100]/45 bg-[#FFD100]/[0.16] px-2.5 py-1 text-xs font-semibold text-ink-700">
            {formatStrategy(t.final_strategy)}
          </span>
        </div>
        {(t.blueprint_rationale || t.weights_explanation) && (
          <div className="mt-3 space-y-2 text-sm leading-relaxed text-ink-700">
            {t.blueprint_rationale && (
              <p>{t.blueprint_rationale}</p>
            )}
            {t.weights_explanation && (
              <p className="rounded-2xl bg-black/[0.025] px-3 py-2 text-xs text-ink-500">
                {t.weights_explanation}
              </p>
            )}
          </div>
        )}
      </div>

      {t.critic_attempts.length > 0 && (
        <TraceSection title="Critic 修正">
          {t.critic_attempts.map((attempt) => (
            <div
              key={`critic-${attempt.attempt_n}`}
              className="rounded-2xl border border-black/[0.05] bg-white/[0.72] px-3 py-2.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-ink-800">
                  第 {attempt.attempt_n} 次
                </span>
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 text-xs font-semibold",
                    attempt.resolved
                      ? "bg-emerald-500/10 text-emerald-700"
                      : "bg-red-500/10 text-red-600",
                  )}
                >
                  {attempt.resolved ? "已修正" : "待处理"}
                </span>
              </div>
              {attempt.feedback_summary && (
                <p className="mt-1.5 text-sm leading-relaxed text-ink-600">
                  {attempt.feedback_summary}
                </p>
              )}
              {attempt.violation_codes.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {attempt.violation_codes.map((code) => (
                    <span
                      key={`${attempt.attempt_n}-${code}`}
                      className="rounded-full bg-black/[0.04] px-2 py-0.5 text-[11px] font-medium text-ink-500"
                    >
                      {code}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </TraceSection>
      )}

      {t.alternatives_considered.length > 0 && (
        <TraceSection title="候选取舍">
          {t.alternatives_considered.map((candidate) => (
            <div
              key={`${candidate.target_kind}-${candidate.target_id}-${candidate.rank}`}
              className="rounded-2xl border border-black/[0.05] bg-white/[0.72] px-3 py-2.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-ink-800">
                  {candidate.target_name}
                </span>
                <span className="text-xs font-semibold text-ink-400">
                  #{candidate.rank}
                </span>
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-600">
                {candidate.reason_rejected}
              </p>
            </div>
          ))}
        </TraceSection>
      )}

      {t.fallback_chain.length > 0 && (
        <TraceSection title="Fallback 链路">
          {t.fallback_chain.map((hop, index) => (
            <div
              key={`${hop.from_stage}-${hop.to_stage}-${index}`}
              className="rounded-2xl border border-black/[0.05] bg-white/[0.72] px-3 py-2.5"
            >
              <div className="text-sm font-semibold text-ink-800">
                {formatStrategy(hop.from_stage)} → {formatStrategy(hop.to_stage)}
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-600">
                {hop.reason}
              </p>
            </div>
          ))}
        </TraceSection>
      )}
    </div>
  );
}

function MobileDecisionFallbackTrace() {
  const itinerary = useChatStore((s) => s.itinerary);
  const toolCalls = useChatStore((s) => s.toolCalls);
  const replans = useChatStore((s) => s.replans);
  const thoughts = useChatStore((s) => s.thoughts);

  const visibleEntries = itinerary ? getVisibleEntries(itinerary) : [];
  const activityEntries = visibleEntries.filter(
    (entry) => entry.entry_kind === "node" && entry.title,
  );
  const latestThoughts = thoughts.slice(-4);
  const finishedTools = toolCalls
    .filter((tool) => tool.endedAtSeq != null || tool.success != null)
    .slice(-6);

  if (
    !itinerary &&
    latestThoughts.length === 0 &&
    finishedTools.length === 0 &&
    replans.length === 0
  ) {
    return (
      <MobileInsightEmpty text="生成方案后，这里会把本轮规划依据、工具证据和修正链路整理出来。" />
    );
  }

  return (
    <div className="space-y-3">
      {itinerary && (
        <div className="rounded-[22px] border border-black/[0.06] bg-white/[0.82] px-4 py-3 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm font-bold tracking-tight text-ink-900">
              本轮决策依据
            </div>
            <span className="rounded-full border border-[#FFD100]/45 bg-[#FFD100]/[0.16] px-2.5 py-1 text-xs font-semibold text-ink-700">
              当前方案
            </span>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-ink-700">
            {itinerary.summary}
            {itinerary.total_minutes > 0
              ? `，约 ${(itinerary.total_minutes / 60).toFixed(1)} 小时`
              : ""}
          </p>
          {activityEntries.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {activityEntries.slice(0, 4).map((entry) => (
                <span
                  key={`${entry.entry_kind}-${entry.ref_id}`}
                  className="rounded-full border border-black/[0.06] bg-black/[0.025] px-2.5 py-1 text-xs font-semibold text-ink-600"
                >
                  {entry.start} {entry.title}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {latestThoughts.length > 0 && (
        <TraceSection title="关键判断">
          {latestThoughts.map((thought) => (
            <div
              key={`decision-thought-${thought.seq}`}
              className="rounded-2xl border border-black/[0.05] bg-white/[0.72] px-3 py-2.5 text-sm leading-relaxed text-ink-650"
            >
              {thought.text}
            </div>
          ))}
        </TraceSection>
      )}

      {finishedTools.length > 0 && (
        <TraceSection title="工具证据">
          {finishedTools.map((tool) => (
            <div
              key={`decision-tool-${tool.id}`}
              className="rounded-2xl border border-black/[0.05] bg-white/[0.72] px-3 py-2.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-ink-800">
                  {TOOL_LABEL[tool.tool] ?? tool.tool}
                </span>
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 text-xs font-semibold",
                    tool.success === false
                      ? "bg-red-500/10 text-red-600"
                      : "bg-emerald-500/10 text-emerald-700",
                  )}
                >
                  {tool.success === false ? "未采用" : "已完成"}
                </span>
              </div>
              {tool.durationMs != null && (
                <div className="mt-1 text-xs font-medium text-ink-400">
                  {tool.durationMs}ms
                </div>
              )}
            </div>
          ))}
        </TraceSection>
      )}

      {replans.length > 0 && (
        <TraceSection title="修正链路">
          {replans.map((replan) => (
            <div
              key={`decision-replan-${replan.seq}`}
              className="rounded-2xl border border-amber-300/35 bg-[#FFD100]/[0.10] px-3 py-2.5 text-sm leading-relaxed text-amber-800"
            >
              {FAILURE_REASON_LABEL[replan.reason] ?? replan.reason}
              <span className="text-amber-700/70">
                {" "}
                · 来自 {TOOL_LABEL[replan.fromTool] ?? replan.fromTool}
              </span>
            </div>
          ))}
        </TraceSection>
      )}
    </div>
  );
}

function TraceSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-[22px] border border-black/[0.06] bg-black/[0.018] px-3 py-3">
      <div className="mb-2 px-1 text-xs font-bold tracking-tight text-ink-500">
        {title}
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function isDecisionTraceEmpty(trace: DecisionTrace | null | undefined): boolean {
  if (!trace) return true;
  return (
    !trace.blueprint_rationale &&
    !trace.weights_explanation &&
    (trace.critic_attempts ?? []).length === 0 &&
    (trace.alternatives_considered ?? []).length === 0 &&
    (trace.fallback_chain ?? []).length === 0
  );
}

function formatStrategy(strategy: string): string {
  const labels: Record<string, string> = {
    llm_first: "LLM 直出",
    llm_backprompt: "LLM 修正",
    ils: "ILS 兜底",
    rule: "规则兜底",
    give_up: "保留方案",
  };
  return labels[strategy] ?? strategy;
}

function formatThoughtTime(timestampMs: number): string {
  return new Date(timestampMs).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function MobileSheet({
  open,
  title,
  icon,
  onClose,
  children,
  tall = false,
  showHeader = true,
}: {
  open: boolean;
  title: string;
  icon: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  tall?: boolean;
  showHeader?: boolean;
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
          "absolute inset-x-4 bottom-[calc(18px+env(safe-area-inset-bottom,0px))] mx-auto max-w-[448px] overflow-hidden rounded-[30px] border border-white/[0.78] bg-white/[0.94] shadow-[0_26px_70px_-34px_rgba(17,24,39,0.88)] backdrop-blur-2xl backdrop-saturate-150",
          "animate-drawer-slide-up",
          tall ? "max-h-[84vh]" : "max-h-[72vh]",
        )}
      >
        {showHeader && (
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-black/[0.06] bg-white/[0.86] px-4 py-3 backdrop-blur-xl">
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
        )}
        <div
          className={cn(
            "overflow-y-auto px-4",
            showHeader ? "py-4" : "pt-3 pb-4",
            showHeader
              ? tall
                ? "max-h-[calc(84vh-62px)]"
                : "max-h-[calc(72vh-62px)]"
              : tall
                ? "max-h-[84vh]"
                : "max-h-[72vh]",
          )}
        >
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
