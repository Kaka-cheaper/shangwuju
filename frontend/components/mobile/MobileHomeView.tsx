"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRightLeft,
  Bot,
  ChevronDown,
  ChevronRight,
  Compass,
  ArrowRight as ArrowRightIcon,
  Loader2,
  Plus,
  Route,
  ShieldAlert,
  Sparkles,
  Users,
  Wrench,
  X,
} from "lucide-react";

import { Icons, scenarioIcon } from "@/lib/icon-map";
import {
  buildCollabChatStateSnapshot,
  buildCollabPlanningEvents,
  useCollabStore,
} from "@/lib/collab-store";
import { buildConfirmPreviewCopy } from "@/lib/confirm-preview";
import { buildCriticTimeline } from "@/lib/critic-timeline";
import { useBootstrapPlannerMode } from "@/lib/hooks/useBootstrapPlannerMode";
import { useCollabDispatch } from "@/lib/hooks/useCollabDispatch";
import { useConfirmAction } from "@/lib/hooks/useConfirmAction";
import { buildIntentChips } from "@/lib/intent-chips";
import { useChatStore } from "@/lib/store";
import { formatStartTimeLabel } from "@/lib/time-labels";
import type {
  AgentNarrationMessage,
  AlternativeOption,
  DecisionTrace,
  HopMode,
  IntentExtraction,
  Itinerary,
  NodeChip,
  ScheduleEntry,
} from "@/lib/types";
import {
  clearUserIdCookie,
  cn,
  FAILURE_REASON_LABEL,
  generateSessionId,
  PLAN_FALLBACK_STAGE_LABEL,
  TOOL_LABEL,
  upsertSession,
} from "@/lib/utils";

import ChitchatBubble from "../ChitchatBubble";
import CollabBar from "../CollabBar";
import CommandPalette from "../CommandPalette";
import ComparisonView from "../ComparisonView";
import Confetti, { type ConfettiOrigin } from "../Confetti";
import ConstraintFeed from "../ConstraintFeed";
import MapOverlay from "../MapOverlay";
import MockModeBadge from "../MockModeBadge";
import OfflineReadyBadge from "../OfflineReadyBadge";
import PlannerModeBadge from "../PlannerModeBadge";
import PosterGenerator from "../PosterGenerator";
import RefinementDialog from "../RefinementDialog";
import ShareModal from "../ShareModal";
import ShimmerStripe from "../ShimmerStripe";
import ToastStack from "../ToastStack";
import ToolTracePanel from "../ToolTracePanel";
import TrustBelt from "../TrustBelt";
import TtsPlayer from "../TtsPlayer";
import UserSwitcher from "../UserSwitcher";
import VoteButtons from "../VoteButtons";

type SheetKind = "trace" | null;

// B9：预约成功烟花——移动端是单栏居中布局（非桌面两栏），行程卡大致落在
// 屏幕中上部，默认的桌面坐标（70%/38%，两栏布局右侧偏上）在手机上会飞出
// 屏幕外，需要重定位。模块级常量（非内联对象字面量）保证引用稳定，不触发
// Confetti 内部 effect 的多余闭包更新。
const MOBILE_CONFETTI_ORIGIN: ConfettiOrigin = { ox: 50, oy: 30 };

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
  const openCommandPalette = useChatStore((s) => s.openCommandPalette);
  const roomId = useCollabStore((s) => s.roomId);

  const [sheet, setSheet] = useState<SheetKind>(null);
  const [shareModalOpen, setShareModalOpen] = useState(false);
  const [refineOpen, setRefineOpen] = useState(false);
  const [visibleIntent, setVisibleIntent] = useState<IntentExtraction | null>(null);
  const personaResetOnLoadRef = useRef(false);
  const visibleIntentSessionRef = useRef(sessionId);
  const activated = messages.length > 0 || streaming || itinerary != null;
  const canCompare = Boolean(previousItinerary && itinerary && lastRefinement);

  // A9 根治：planner 模式的 cookie/health 校准不再依赖 PlannerModeBadge 是否
  // 挂载——移动端此前压根不挂那个徽章组件，plannerMode 永远停在硬编码的
  // "rule"，实际跑的是降智版规则规划。根组件统一调这个 hook 即可校准。
  useBootstrapPlannerMode();

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
        onOpenCommandPalette={openCommandPalette}
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
        {/* A10：协作状态条（成员/在线/规划触发/连接态）。CollabBar 内部已按
            collabMode 自 return null，非房间态零渲染。-mx-4 抵消 main 的左右
            padding，做到与桌面端一致的"边到边"横条视觉。 */}
        <div className="-mx-4">
          <CollabBar />
        </div>

        {/* A8 根治：SSE 流错误——移动端此前零订阅 streamError，完全静默失败。 */}
        <MobileStreamErrorBanner />

        {/* B3：偏好画像面板——紧凑折叠卡，默认收起，不占初始态视觉焦点。 */}
        <div className="mb-3">
          <MobilePreferencesCard />
        </div>

        {/* C4：评委证据徽章——桌面端默认 hidden md:/lg: 在移动端窄容器里天经
            地义不可见，用 compact prop 摘掉这层限制。flex-wrap 而非固定高度
            一行，窄屏（iPhone SE 等）挤不下时自然换行，不会裁切。 */}
        {!activated && (
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            <PlannerModeBadge />
            <MockModeBadge compact />
            <OfflineReadyBadge compact />
          </div>
        )}

        <MobileScenarioRail compact={activated} />

        <MobileConversation
          visibleIntent={visibleIntent}
          itinerary={itinerary}
        />

        {/* A11：约束流 + 诉求台账（单人/房间共用 useChatStore.demandLedger，
            见 ConstraintFeed.tsx 顶部 docstring）。二者各自按内容是否为空
            独立 return null，不需要额外的显隐判断。 */}
        <div className="mt-3 [&_.card]:mb-0 [&_.card]:rounded-[22px] [&_.card]:border-black/[0.06] [&_.card]:bg-white/[0.82] [&_.card]:shadow-sm [&_.card]:backdrop-blur-xl">
          <ConstraintFeed />
        </div>

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
        onOpenRefine={() => setRefineOpen(true)}
      />
      <MobileComposer />
      <ToastStack />
      <Confetti origin={MOBILE_CONFETTI_ORIGIN} />
      {/* B4：「说说哪不对」反馈弹窗——挂在根组件而非 MobileActionRail 内部：
          MobileActionRail 在 streaming 时会整体切到另一个 return 分支（Agent
          正在思考的进度条），若 RefinementDialog 挂在那个分支之外，用户打开
          弹窗后 streaming 一旦变 true 弹窗会被硬生生卸载。挂在根组件不受
          MobileActionRail 内部分支影响，同 CommandPalette/ShareModal 的既有
          挂载方式一致（本身是 createPortal 渲染的居中弹层，位置不影响布局）。 */}
      <RefinementDialog open={refineOpen} onClose={() => setRefineOpen(false)} />
      {/* B5：会话切换/历史入口——移动端此前开新对话后回不去旧的。CommandPalette
          本身已是响应式居中弹层（fixed inset-0 + max-w-xl w-full px-4），
          直接复用而不是另起一份组件；顶栏的"命令"图标按钮触发它。 */}
      <CommandPalette />

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
  onOpenCommandPalette,
}: {
  onNewSession: () => void;
  onOpenCommandPalette: () => void;
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
          {/* B5：会话切换/历史入口——打开命令面板（场景/模式/用户/历史会话）。 */}
          <button
            type="button"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-black/[0.08] bg-white/[0.68] text-ink-600 shadow-sm backdrop-blur transition hover:border-[#FFD100]/50 hover:bg-white/[0.88] hover:text-ink-900 active:scale-95"
            onClick={onOpenCommandPalette}
            aria-label="打开命令面板（场景 / 历史会话 / 模式 / 用户切换）"
            title="命令面板：场景 / 历史会话 / 模式 / 用户切换"
          >
            <Compass className="h-4 w-4" strokeWidth={2} />
          </button>
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

/**
 * B3：偏好画像面板（累积标签统计 / 清空记忆）——移动端紧凑版。
 *
 * 桌面端 PreferencesPanel.tsx 假设有一块「大头像图 + 文字右对齐」的横向空间
 * （150px 头像图、pr-20 让位），480px 宽的手机容器放不下同一套布局，这里
 * 按移动端已有的圆角卡片语言重写渲染，但读同一份 store 字段/同一套折叠态
 * 持久化 key（shangwuju.preferences.open）——桌面/移动切换时"是否展开"的
 * 记忆是共享的。批 A（confirm/refine 走通）之后这里会开始出现会话级累积
 * 数据（accepted_tags/rejected_tags），这是预期行为，不是这次改动引入的。
 */
function MobilePreferencesCard() {
  const currentUserId = useChatStore((s) => s.currentUserId);
  const preferences = useChatStore((s) => s.preferences);
  const resetUserMemory = useChatStore((s) => s.resetUserMemory);
  const [open, _setOpen] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      _setOpen(window.localStorage.getItem("shangwuju.preferences.open") === "true");
    } catch {
      /* 隐私模式 / 配额异常时忽略 */
    }
  }, []);

  const setOpen = (next: boolean) => {
    _setOpen(next);
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem("shangwuju.preferences.open", next ? "true" : "false");
    } catch {
      /* 隐私模式 / 配额异常时忽略 */
    }
  };

  if (!currentUserId) return null;
  const persona = preferences?.persona;
  const memory = preferences?.memory;
  const acceptedCount = memory
    ? Object.values(memory.accepted_tags.counts).reduce((a, b) => a + b, 0)
    : 0;
  const acceptedTop = memory
    ? Object.entries(memory.accepted_tags.counts).sort((a, b) => b[1] - a[1]).slice(0, 5)
    : [];
  const rejectedTop = memory
    ? Object.entries(memory.rejected_tags.counts)
        .filter(([, n]) => n > 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
    : [];

  return (
    <section className="rounded-[22px] border border-black/[0.06] bg-white/[0.80] px-4 py-3 shadow-sm backdrop-blur-xl">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <div className="min-w-0 text-left">
          <div className="truncate text-base font-semibold tracking-tight text-ink-900">
            {persona?.label ?? "偏好画像"}
          </div>
          <div className="truncate text-xs text-ink-500">
            {persona?.notes ?? "点击查看 Agent 已学到的偏好"}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {acceptedCount > 0 && (
            <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-semibold text-emerald-600">
              已学 {acceptedCount}
            </span>
          )}
          <ChevronDown
            className={cn("h-4 w-4 text-ink-400 transition-transform duration-200", open && "rotate-180")}
            strokeWidth={2.5}
          />
        </div>
      </button>

      {open && persona && (
        <div className="mt-3 space-y-2.5 border-t border-black/[0.06] pt-3 animate-collapse-in">
          {(preferences?.top_priors ?? []).length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-ink-500">常去</span>
              {preferences!.top_priors.map((t) => (
                <span
                  key={t}
                  className="rounded-full border border-[#FFD100]/35 bg-[#FFD100]/[0.08] px-2.5 py-1 text-xs font-medium text-amber-800"
                >
                  {t}
                </span>
              ))}
            </div>
          )}

          {preferences?.suggested_distance_max_km != null && (
            <div className="text-xs text-ink-600">
              建议距离{" "}
              <span className="font-semibold text-ink-900">
                {preferences.suggested_distance_max_km}km
              </span>
            </div>
          )}

          {(acceptedTop.length > 0 || rejectedTop.length > 0) && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-600">
              {acceptedTop.length > 0 && (
                <span>
                  接受{" "}
                  {acceptedTop.map(([t, n], i) => (
                    <span key={t}>
                      {i > 0 && "、"}
                      {t}
                      <span className="text-ink-400">×{n}</span>
                    </span>
                  ))}
                </span>
              )}
              {rejectedTop.length > 0 && (
                <span>
                  拒绝{" "}
                  {rejectedTop.map(([t, n], i) => (
                    <span key={t}>
                      {i > 0 && "、"}
                      {t}
                      <span className="text-ink-400">×{n}</span>
                    </span>
                  ))}
                </span>
              )}
            </div>
          )}

          <button
            type="button"
            onClick={() => void resetUserMemory()}
            className="text-xs text-ink-500 underline decoration-dotted underline-offset-2 transition-colors hover:text-rose-500"
            title="清空当前用户的累积偏好（演示完清场用）"
          >
            清空记忆
          </button>
        </div>
      )}
    </section>
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

/**
 * A8 根治：SSE 流错误展示——移动端此前对 store.streamError 零订阅，流式失败时
 * 用户只看到"卡住"，没有任何提示。照 ChatDock.tsx:453-461 的红色错误条语义
 * 移植，但不套用 Web 那边"仅在 dock 展开态才渲染"的条件（移动端没有可收起的
 * dock 概念）——非空即常驻显示，比桌面端更不容易被漏看。
 * event-handlers.ts 的 stream_error case 现已同时 pushToast（两端共享），本
 * 组件是移动端的常驻兜底，toast 是一次性提示，两者互补不冲突。
 */
function MobileStreamErrorBanner() {
  const streamError = useChatStore((s) => s.streamError);
  if (!streamError) return null;
  return (
    <div className="mt-3 flex items-start gap-2 rounded-2xl border border-rose-500/30 bg-rose-500/10 px-3.5 py-2.5 text-sm text-rose-700 shadow-sm backdrop-blur-xl animate-fade-in">
      <Icons.warn className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
      <span>流出错：{streamError}</span>
    </div>
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

  // A7 根治：chitchat 气泡此前被压平成纯文本（只取 payload.reply_text），
  // 丢了 cta_chips / tone——手机收到"要不要确认预约？"这类气泡时没按钮可点。
  // 保留 kind 区分，chitchat 条目改渲染共享的 <ChitchatBubble>（自带
  // collabMode 分流 + tone 主题 + cta_chips 按钮，同 ChatDock 桌面端一致）。
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
        payload: r.payload,
      })),
    ].sort((a, b) => a.ts - b.ts);
  }, [messages, chitchatReplies]);

  const showIntentSummary = visibleIntent || itinerary;

  if (!timeline.length && !streaming && !showIntentSummary) return null;

  return (
    <section className="mt-3 space-y-2.5">
      {timeline.slice(-6).map((item) =>
        item.kind === "message" ? (
          <MobileBubble key={item.id} role={item.role} text={item.text} />
        ) : (
          <ChitchatBubble key={item.id} payload={item.payload} />
        ),
      )}
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
  const intent = useChatStore((s) => s.intent);
  const narration = useChatStore((s) => s.narration);
  const narrationMessages = useChatStore((s) => s.narrationMessages);
  const memoryPersisted = useChatStore((s) => s.memoryPersisted);
  const lastRefinement = useChatStore((s) => s.lastRefinement);
  const cancelled = useChatStore((s) => s.cancelled);

  if (!itinerary && !streaming) return null;

  if (!itinerary) {
    return (
      <div className="mt-3 space-y-3">
        {/* 信任带（移动端同款）：规划中就该看到"它在想什么"，不必等方案落地。 */}
        <TrustBelt />
        <section className="rounded-[24px] border border-[#FFD100]/[0.42] bg-white/[0.82] px-4 py-4 shadow-sm backdrop-blur-xl">
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
      </div>
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

      {/* B2：Refine 摘要横幅——lastRefinement.changedFields 此前只当布尔闸用
          （只决定要不要显示 ComparisonView），内容本身从没展示过，toast 一闪
          即逝兜不住。 */}
      {lastRefinement && lastRefinement.changedFields.length > 0 && (
        <div className="px-4 pt-1">
          <MobileRefinementBanner
            fields={lastRefinement.changedFields}
            note={lastRefinement.refinerNote}
          />
        </div>
      )}

      {/* B1：narration 暖心文案 + "为你考虑了" chips + 取舍说明——此前只显示
          裸 itinerary.summary，从没消费 narration/narrationMessages，手机用户
          看到的正是被替代掉的旧套话。 */}
      {(narration?.text || intent) && (
        <div className="px-4 pt-2">
          <MobileNarrationBlock
            text={narration?.text}
            stage={narration?.stage ?? "stream"}
            messages={narrationMessages}
            intent={intent}
          />
        </div>
      )}

      {/* B7：「已记住此场景偏好」徽标——纯展示，低成本。 */}
      {memoryPersisted?.success && (
        <div className="px-4 pt-2">
          <MobileMemoryBadge
            socialContext={memoryPersisted.socialContext}
            summaryPreview={memoryPersisted.summaryPreview}
          />
        </div>
      )}

      {/* 信任带（移动端同款）：叙事（上方 narration/memory 徽标）和时间轴
          （下方 <ol>）之间，同 Web 端 ItineraryCard 的插入位置。 */}
      <div className="px-4 pt-3">
        <TrustBelt />
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

      {/* B6：转发文案卡——itinerary.share_message，注意这与海报生成器
          （PosterGenerator，见下方 MobileActionRail 展开菜单）的文案不是
          同一份内容，不能互相替代：前者是 generate_share_message 工具产出，
          后者是 PosterGenerator 自己拼的海报文案。 */}
      {itinerary.share_message && (
        <div className="mx-4 mb-3">
          <MobileShareMessage text={itinerary.share_message} />
        </div>
      )}

      {/* B8：「确认后会发生什么」预告卡——纯派生展示，让用户不点确认也能
          看到一键执行能力（下单后由上面的"已为你预留"订单卡接力）。 */}
      {!hasOrders && !cancelled && (
        <div className="mx-4 mb-3">
          <MobileConfirmPreview itinerary={itinerary} />
        </div>
      )}

      {/* B10：取消方案后的文案提示——此前 cancelled 只用于禁用按钮，没有
          任何文案告知用户"为什么按钮都灰了"。 */}
      {cancelled && !hasOrders && (
        <div className="mx-4 mb-3 rounded-2xl border border-black/[0.06] bg-black/[0.02] px-3.5 py-2.5 text-center text-sm text-ink-500">
          已取消方案，可重新输入或点击场景按钮
        </div>
      )}

    </article>
  );
}

// ============================================================
// B2：Refine 摘要横幅——照 ItineraryCard.tsx 的 RefinementSummaryBanner 移植。
// ============================================================

function MobileRefinementBanner({
  fields,
  note,
}: {
  fields: string[];
  note?: string | null;
}) {
  return (
    <div className="rounded-2xl border border-[#FFD100]/40 bg-[#FFD100]/[0.08] px-3.5 py-2.5 text-sm text-amber-800 shadow-sm backdrop-blur-xl animate-fade-in">
      <div className="mb-1 flex items-center gap-1.5 font-semibold text-amber-700">
        <Sparkles className="h-3.5 w-3.5" strokeWidth={2} />
        <span>已根据反馈调整</span>
      </div>
      <ul className="ml-5 list-disc list-outside space-y-0.5 text-amber-900/90">
        {fields.map((f, i) => (
          <li key={i}>{f}</li>
        ))}
      </ul>
      {note && <div className="mt-1 ml-5 text-amber-700/75">{note}</div>}
    </div>
  );
}

// ============================================================
// B1：Agent 暖心开场白 + intent 命中可视化（narration + "为你考虑了" chips +
// D-7 取舍说明）。照 ItineraryCard.tsx:752-851（NarrationBlock）+ :993-1066
// （buildIntentChips，已抽到 lib/intent-chips.ts）移植，HighlightText 逐字
// 高亮暂不移植（属于 Tier C 视觉打磨 C2，不影响内容完整性）。
// ============================================================

function MobileNarrationBlock({
  text,
  stage,
  messages,
  intent,
}: {
  text?: string;
  stage: "stream" | "confirm";
  messages?: AgentNarrationMessage[] | null;
  intent: IntentExtraction | null;
}) {
  const isConfirm = stage === "confirm";
  const [expanded, setExpanded] = useState(false);
  const hasMessages = !!messages && messages.length > 0;
  const chips = intent ? buildIntentChips(intent) : [];
  if (!text && chips.length === 0) return null;

  return (
    <div
      className={cn(
        "rounded-[22px] border px-4 py-3.5 text-[15px] leading-relaxed shadow-sm backdrop-blur-xl animate-fade-in",
        isConfirm
          ? "border-emerald-400/25 bg-emerald-500/[0.06]"
          : "border-[#FFD100]/[0.32] bg-white/[0.84]",
      )}
    >
      {text && (
        <div className="flex items-start gap-2">
          <Sparkles
            className={cn(
              "mt-0.5 h-4 w-4 shrink-0",
              isConfirm ? "text-emerald-500" : "text-amber-500",
            )}
            strokeWidth={2}
          />
          <p className="whitespace-pre-wrap text-ink-900">{text}</p>
        </div>
      )}

      {chips.length > 0 && (
        <div
          className={cn(
            "flex flex-wrap items-center gap-1.5",
            text && "mt-3 border-t border-black/[0.06] pt-3",
          )}
        >
          <span className="mr-1 inline-flex items-center gap-1 text-xs font-semibold text-ink-500">
            <Sparkles className="h-3.5 w-3.5 text-amber-500" strokeWidth={2.5} />
            为你考虑了
          </span>
          {chips.map((c, i) => {
            const Ico = Icons[c.icon];
            return (
              <span
                key={`${c.label}-${i}`}
                className="inline-flex items-center gap-1 rounded-full border border-[#FFD100]/35 bg-[#FFD100]/[0.10] px-2.5 py-1 text-xs font-semibold tracking-tight text-amber-800"
              >
                <Ico className="h-3.5 w-3.5" strokeWidth={2} />
                {c.label}
              </span>
            );
          })}
        </div>
      )}

      {/* D-7：全部取舍说明——narrate 文字里折叠的"还有 N 处小取舍"在这里展开看全部 */}
      {hasMessages && (
        <div className={cn(text || chips.length > 0 ? "mt-2.5" : "")}>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className={cn(
              "inline-flex items-center gap-1 text-sm font-medium",
              isConfirm ? "text-emerald-600" : "text-amber-700",
            )}
            aria-expanded={expanded}
          >
            <span>{expanded ? "收起取舍说明" : `查看全部取舍说明（${messages!.length}）`}</span>
            <ChevronDown
              className={cn("h-3 w-3 transition-transform duration-200", !expanded && "-rotate-90")}
              strokeWidth={2.5}
            />
          </button>
          {expanded && (
            <ul className="mt-1.5 ml-4 list-disc list-outside space-y-1">
              {messages!.map((m, i) => (
                <li key={`${m.code ?? "advisory"}-${i}`} className="text-sm leading-relaxed text-ink-700">
                  {m.text}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================
// B7：「已记住此场景偏好」徽标——照 ItineraryCard.tsx 的 MemoryPersistedBadge
// 移植（纯展示，低成本）。
// ============================================================

function MobileMemoryBadge({
  socialContext,
  summaryPreview,
}: {
  socialContext: string;
  summaryPreview: string;
}) {
  return (
    <div className="flex items-start gap-2 rounded-2xl border border-emerald-500/25 bg-emerald-500/[0.06] px-3.5 py-2.5 text-sm text-emerald-700/95 shadow-sm backdrop-blur-xl animate-fade-in">
      <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" strokeWidth={2} />
      <div className="min-w-0 flex-1">
        <div className="font-medium tracking-tight text-emerald-600">
          已写入「{socialContext || "本"}」场景的跨 session 召回库
        </div>
        <div className="mt-0.5 line-clamp-1 text-xs text-emerald-700/75">
          {summaryPreview}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// B8：「确认后会发生什么」预告卡——照 ItineraryCard.tsx 的 ConfirmPreviewCard
// 移植，文案派生逻辑复用 lib/confirm-preview.ts（两端同一份判定）。
// ============================================================

function MobileConfirmPreview({ itinerary }: { itinerary: Itinerary }) {
  const intent = useChatStore((s) => s.intent);
  const { restaurantLine, extraLine, memoryLine, extraServices } =
    buildConfirmPreviewCopy(intent, itinerary);

  return (
    <div className="rounded-2xl border border-amber-400/20 bg-amber-500/[0.05] px-3.5 py-3 text-sm leading-relaxed">
      <div className="mb-1.5 flex items-center gap-1.5">
        <Sparkles className="h-3.5 w-3.5 text-amber-500" strokeWidth={2} />
        <span className="font-semibold tracking-tight text-amber-700">
          点击「确认并预约」之后
        </span>
      </div>
      <p className="mb-2 text-amber-900/85">
        {restaurantLine}
        {extraLine}；再为你备好一段可一键复制的转发文案；最后{memoryLine}。
      </p>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-amber-700/80">
        <span className="inline-flex items-center gap-1">
          <span aria-hidden>🪑</span>
          <span>锁餐厅时段</span>
        </span>
        <span className="inline-flex items-center gap-1">
          <span aria-hidden>📝</span>
          <span>备转发文案</span>
        </span>
        {extraServices.length > 0 && (
          <span className="inline-flex items-center gap-1">
            <span aria-hidden>+</span>
            <span>加购{extraServices[0]}</span>
          </span>
        )}
        <span className="inline-flex items-center gap-1">
          <span aria-hidden>🧠</span>
          <span>记本次偏好</span>
        </span>
      </div>
    </div>
  );
}

// ============================================================
// B6：转发文案卡——照 ItineraryCard.tsx 的 ShareMessage 移植（clipboard 写入
// + execCommand 兜底）。与海报生成器（PosterGenerator）的文案是两份独立内容，
// 见调用点注释。
// ============================================================

function MobileShareMessage({ text }: { text: string }) {
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
    <div className="rounded-2xl border border-black/[0.07] bg-[#FFD100]/[0.05] px-3.5 py-3">
      <div className="mb-1.5 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Icons.share className="h-3.5 w-3.5 text-amber-600" strokeWidth={2} />
          <span className="text-sm font-semibold tracking-tight text-ink-800">
            转发文案
          </span>
        </div>
        <button
          type="button"
          onClick={() => void copy()}
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
            copied
              ? "bg-emerald-500 text-white"
              : "border border-black/[0.08] bg-white/70 text-ink-700",
          )}
        >
          {copied ? (
            <>
              <Icons.success className="h-3 w-3" strokeWidth={2.5} />
              <span>已复制</span>
            </>
          ) : (
            <>
              <Icons.copy className="h-3 w-3" strokeWidth={2} />
              <span>复制</span>
            </>
          )}
        </button>
      </div>
      <div className="whitespace-pre-wrap text-sm leading-relaxed tracking-tight text-ink-800">
        {text}
      </div>
    </div>
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

  // A2/A3（ADR-0013 F-4/F-5）：节点行调整入口——具名备选 / 定向调整 chips /
  // 赞踩，此前 NotebookTimelineItem 完全没订阅 nodeActions/lockedNodeId/
  // sendAdjust，移动端拿到的节点是"只能看不能改"的静态卡片。照
  // ItineraryCard.tsx:401-499 + :1204-1260 移植，collabMode 分流手法同
  // ItineraryCard（房间模式走 WS sendAdjust，单人走 HTTP /chat/adjust）。
  const streaming = useChatStore((s) => s.streaming);
  const nodeActions = useChatStore((s) => s.nodeActions);
  const lockedNodeId = useChatStore((s) => s.lockedNodeId);
  const sendAdjust = useChatStore((s) => s.sendAdjust);
  const collabMode = useCollabStore((s) => s.collabMode);
  const sendCollabAdjust = useCollabStore((s) => s.sendAdjust);
  const dispatchAdjust = collabMode ? sendCollabAdjust : sendAdjust;

  const targetId = node?.target_id ?? null;
  const actions = targetId ? nodeActions?.[targetId] : undefined;
  const chips = actions?.chips ?? [];
  const alternatives = (actions?.alternatives ?? []).slice(0, 2);
  const isLocked = targetId != null && lockedNodeId === targetId;
  const canAdjust = targetId != null && !isLocked && lockedNodeId == null && !streaming;

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

        {/* A2：具名备选（换成某个候选） + 定向调整 chips */}
        {targetId && (alternatives.length > 0 || chips.length > 0) && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-[#eadfc9]/50 pt-2.5">
            {alternatives.map((alt) => (
              <MobileAlternativeButton
                key={alt.target_id}
                alt={alt}
                disabled={!canAdjust}
                onClick={() =>
                  dispatchAdjust(targetId, { type: "alternative", target_id: alt.target_id })
                }
              />
            ))}
            {chips.map((chip) => (
              <MobileAdjustChipButton
                key={`${chip.node_id}-${chip.adjustment.dimension}-${chip.adjustment.value}`}
                chip={chip}
                disabled={!canAdjust}
                onClick={() =>
                  dispatchAdjust(targetId, {
                    type: "adjust",
                    adjustment: chip.adjustment,
                    label: chip.label,
                  })
                }
              />
            ))}
          </div>
        )}

        {/* A3：赞/踩——同样是"下方一行"，即便没有 chips/alternatives 也要渲染
            这一行，让协作模式下赞踩仍可见（VoteButtons 自身按 collabMode
            return null，非房间态零渲染）。 */}
        {targetId && (
          <div className="mt-2 flex items-center">
            <VoteButtons stageIndex={index} />
          </div>
        )}

        {/* 换菜进行中：整行 Shimmer（同 ItineraryCard 的锁定态视觉语言）。 */}
        {isLocked && <ShimmerStripe rows={1} className="mt-2" />}
      </div>
    </li>
  );
}

// ============================================================
// A2：节点行调整入口——具名备选按钮 / 定向调整 chip（移动端配色）。
// ============================================================

function MobileAlternativeButton({
  alt,
  disabled,
  onClick,
}: {
  alt: AlternativeOption;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={`换成「${alt.name}」（${alt.category} · ${alt.rating.toFixed(1)} 分 · ${alt.distance_km.toFixed(1)}km）`}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-[#FFD100]/40 bg-[#FFD100]/[0.10] px-2.5 py-1 text-xs font-semibold tracking-tight text-amber-800",
        "transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40",
      )}
    >
      <Sparkles className="h-3 w-3" strokeWidth={2} />
      <span className="max-w-[7.5rem] truncate">换成{alt.name}</span>
    </button>
  );
}

function MobileAdjustChipButton({
  chip,
  disabled,
  onClick,
}: {
  chip: NodeChip;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={chip.label}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-black/[0.08] bg-black/[0.03] px-2.5 py-1 text-xs font-medium tracking-tight text-ink-700",
        "transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40",
      )}
    >
      {chip.label}
    </button>
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
  onOpenRefine,
}: {
  onOpenTrace: () => void;
  onOpenShareModal: () => void;
  onOpenRefine: () => void;
}) {
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);
  const toolCalls = useChatStore((s) => s.toolCalls);
  const replans = useChatStore((s) => s.replans);
  const thoughts = useChatStore((s) => s.thoughts);
  const cancel = useChatStore((s) => s.cancel);
  const pushToast = useChatStore((s) => s.pushToast);
  const collabMode = useCollabStore((s) => s.collabMode);
  const roomId = useCollabStore((s) => s.roomId);
  const createRoom = useCollabStore((s) => s.createRoom);
  const joinRoom = useCollabStore((s) => s.joinRoom);
  const [expanded, setExpanded] = useState(false);
  const [creatingRoom, setCreatingRoom] = useState(false);
  // A6 根治：确认按钮此前无条件调单人 confirm() action，房间参与者能绕过
  // "仅房主可确认"守卫（且完全没走 WS confirm 通道，其它成员看不到）。
  // canConfirm/handleConfirm/confirmLabel 已由共享 hook 统一判定（同
  // ItineraryCard 桌面端confirm 按钮共用同一份逻辑）。必须在下方 early
  // return 之前调用（hooks 规则）。
  const { canConfirm, handleConfirm, confirmLabel, blockedByOwnerGuard } =
    useConfirmAction();

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
          {/* B4：「说说哪不对」反馈弹窗——RefinementDialog 挂在根组件
              MobileHomeView（见该组件 docstring 注释），这里只负责打开它；
              composer 打字是既有等价路径，这里补上和桌面端一致的显式入口。 */}
          <button
            type="button"
            className="flex h-10 items-center justify-center gap-1.5 rounded-full border border-white/[0.74] bg-white/[0.72] px-3 text-sm font-semibold text-ink-700 transition active:scale-[0.98] disabled:text-ink-400"
            disabled={streaming}
            onClick={() => {
              setExpanded(false);
              onOpenRefine();
            }}
          >
            <Wrench className="h-3.5 w-3.5" />
            <span>说说哪不对</span>
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
              disabled={!canConfirm}
              onClick={handleConfirm}
              title={
                blockedByOwnerGuard
                  ? "只有房间发起人可以确认预约"
                  : "确认后 Agent 会做三件事：锁定餐厅时段、整理转发文案、把本次偏好写进长期记忆"
              }
            >
              {confirmLabel}
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

/**
 * N3：iOS Safari 键盘遮挡补偿。`position: fixed; bottom: 0` 元素锚定的是
 * layout viewport，键盘弹出时 Safari 不保证跟随收缩的 visualViewport 走——
 * 输入框会被键盘顶起遮住。用 visualViewport.resize/scroll 算出"键盘吃掉的
 * 高度"，用 translateY 把输入框顶上去（业界标配手法，聊天类 PWA 常见方案）。
 * 非 iOS/无 visualViewport API 的环境下 inset 恒 0，等价于不做任何补偿。
 */
function useKeyboardInset(): number {
  const [inset, setInset] = useState(0);
  useEffect(() => {
    if (typeof window === "undefined" || !window.visualViewport) return;
    const vv = window.visualViewport;
    const update = () => {
      const diff = window.innerHeight - vv.height - vv.offsetTop;
      setInset(Math.max(0, Math.round(diff)));
    };
    update();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  }, []);
  return inset;
}

function MobileComposer() {
  // A5 根治：此前无条件调用单人 sendMessage()，房间里用手机打字消息不广播、
  // 不进约束池。collabMode 分流统一由 useCollabDispatch 实现（同 ChatDock
  // 输入框 / ChitchatBubble chip 共用同一份判断）。
  const { sendUserInput } = useCollabDispatch();
  const streaming = useChatStore((s) => s.streaming);
  const messages = useChatStore((s) => s.messages);
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const keyboardInset = useKeyboardInset();

  const submit = () => {
    const text = draft.trim();
    if (!text || streaming) return;
    sendUserInput(text);
    setDraft("");
    requestAnimationFrame(() => textareaRef.current?.blur());
  };

  return (
    <div
      className="pointer-events-none fixed inset-x-0 bottom-0 z-40 bg-transparent px-4 pb-[calc(12px+env(safe-area-inset-bottom,0px))] pt-3 transition-transform duration-150 ease-out"
      style={keyboardInset > 0 ? { transform: `translateY(-${keyboardInset}px)` } : undefined}
    >
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
  // A1：质检与自愈时间线也算"有值得看的思考内容"——同 ThoughtPanel.tsx 的
  // render-guard 判据（criticCount>0 时不该判定为空态），否则某一轮只触发过
  // critic 自愈、没有普通 agent_thought 时，"Agent在想什么" tab 会误判为空。
  const criticCount = useChatStore((s) => buildCriticTimeline(s.criticReport).length);
  const [activeTab, setActiveTab] = useState<AgentInsightTab>("trace");

  const tabs: Array<{ id: AgentInsightTab; label: string }> = [
    { id: "trace", label: "Agent思考链路" },
    { id: "thought", label: "Agent在想什么" },
    { id: "decision", label: "决策链路" },
  ];

  const hasThoughts =
    thoughts.length > 0 || replans.length > 0 || streaming || criticCount > 0;
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
  // A1：质检与自愈时间线——此前 MobileThoughtTimeline 只读 thoughts/replans，
  // criticReport（critic_violations/critic_fix_attempt/plan_fallback 三事件）
  // 完全没有落地，"系统自愈过程可视化"这个卖点在手机上等于不存在。
  const criticReport = useChatStore((s) => s.criticReport);
  const itinerary = useChatStore((s) => s.itinerary);
  const criticTimeline = useMemo(() => buildCriticTimeline(criticReport), [criticReport]);

  // 单思考面（信任带设计终稿 §修订4）：AI 幕后（TrustBelt）是唯一思考面，
  // 这里不再铺原始 thought.text 列表（那是带权重/冗长 rationale 的未加工
  // 重复面）——重规划事件是结构化的自愈信号（非自由文本 rationale），保留。
  const items = replans.map((replan) => ({
    kind: "replan" as const,
    seq: replan.seq,
    text: `${FAILURE_REASON_LABEL[replan.reason] ?? replan.reason} · ${replan.fromTool}`,
  }));
  const showThinkingPulse = streaming && thoughts.length > 0;

  return (
    <div className="rounded-[22px] border border-black/[0.06] bg-white/[0.82] px-3 py-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between gap-3 px-1">
        <div className="text-sm font-bold tracking-tight text-ink-900">
          Agent 在想什么
        </div>
        <div className="text-xs font-semibold text-ink-500">
          {replans.length > 0 ? `${replans.length} 次重规划` : ""}
        </div>
      </div>

      {showThinkingPulse ? (
        <div className="flex items-center gap-2 rounded-2xl bg-black/[0.025] px-3 py-3 text-sm text-ink-500">
          <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-brand-500" aria-hidden />
          AI 正在思考……
        </div>
      ) : items.length === 0 && streaming ? (
        <div className="flex items-center gap-2 rounded-2xl bg-black/[0.025] px-3 py-3 text-sm text-ink-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          等待 Agent 开始思考……
        </div>
      ) : (
        items.length > 0 && (
          <ol className="space-y-2">
            {items.map((item) => (
              <li
                key={`${item.kind}-${item.seq}`}
                className="rounded-2xl border border-amber-300/35 bg-[#FFD100]/[0.10] px-3 py-2.5 text-sm leading-relaxed text-amber-800"
              >
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-ink-500">重规划</span>
                </div>
                {item.text}
              </li>
            ))}
          </ol>
        )
      )}

      {/* A1：质检与自愈——独立小节，不与上面的自由文本思考叙事混排（同
          ThoughtPanel.tsx 的分工：叙事 vs. 结构化质检结果是两种粒度的信息）。
          措辞口吻＝系统能力的展示，不是错误道歉。 */}
      {criticTimeline.length > 0 && (
        <div className="mt-3 border-t border-black/[0.06] pt-2.5">
          <div className="mb-2 flex items-center gap-1.5 px-1">
            <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-amber-500" strokeWidth={2} />
            <span className="text-sm font-bold tracking-tight text-ink-900">
              质检与自愈
            </span>
            <span className="text-xs font-semibold text-ink-500">{criticTimeline.length}</span>
          </div>
          <ol className="space-y-2">
            {criticTimeline.map((item, idx) => {
              const isFrontier = idx === criticTimeline.length - 1 && streaming && itinerary == null;
              if (item.kind === "violations") {
                return (
                  <MobileViolationRoundItem
                    key={`violations-${item.data.seq}`}
                    data={item.data}
                    isFrontier={isFrontier}
                    hasLaterEvent={idx < criticTimeline.length - 1}
                  />
                );
              }
              if (item.kind === "fix_attempt") {
                return (
                  <MobileFixAttemptItem
                    key={`fix-${item.data.seq}`}
                    data={item.data}
                    isFrontier={isFrontier}
                  />
                );
              }
              return <MobileFallbackHopItem key={`fallback-${item.data.seq}`} data={item.data} />;
            })}
          </ol>
        </div>
      )}
    </div>
  );
}

/** 一轮 critic 违规判定：拦下的问题逐条人话展示 + 是否已被后续事件接住
 * （返工/降级）的状态标记。照 ThoughtPanel.tsx 的 ViolationRoundItem 移植。 */
function MobileViolationRoundItem({
  data,
  isFrontier,
  hasLaterEvent,
}: {
  data: { fixAttempt: number; violations: { message: string; field_path: string }[] };
  isFrontier: boolean;
  hasLaterEvent: boolean;
}) {
  const shown = data.violations.slice(0, 6);
  const overflow = data.violations.length - shown.length;
  // violations=[] 是"这稿压根没生成出方案"（候选为空/蓝图生成失败），不是
  // "零问题"——文案不说"拦下 0 个问题"制造矛盾感（同 ThoughtPanel 注释）。
  const noBlueprintProduced = data.violations.length === 0;
  return (
    <li className="rounded-2xl border border-amber-300/35 bg-[#FFD100]/[0.08] px-3 py-2.5">
      <div className="flex items-center gap-1.5">
        <ShieldAlert className="h-3 w-3 shrink-0 text-amber-600" strokeWidth={2} />
        <span className="text-sm font-semibold text-amber-800">
          {noBlueprintProduced
            ? `第 ${data.fixAttempt} 稿未能生成有效方案`
            : `质检拦下 ${data.violations.length} 个问题（第 ${data.fixAttempt} 稿）`}
        </span>
      </div>
      {shown.length > 0 && (
        <ul className="mt-1.5 ml-[20px] list-disc space-y-1 marker:text-amber-500">
          {shown.map((v, i) => (
            <li key={i} className="text-sm leading-relaxed text-ink-700">
              {v.message}
            </li>
          ))}
          {overflow > 0 && <li className="text-xs text-ink-400">还有 {overflow} 项…</li>}
        </ul>
      )}
      <div className="mt-1.5 ml-[20px]">
        {isFrontier ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700">
            <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} />
            AI 正在修正……
          </span>
        ) : hasLaterEvent ? (
          <span className="text-xs font-medium text-emerald-600">已自动返工</span>
        ) : null}
      </div>
    </li>
  );
}

/** critic backprompt 重做中：正在按质检反馈重写第 N 稿。照 ThoughtPanel.tsx
 * 的 FixAttemptItem 移植——不直接展示后端 feedback_text（常是内部占位文案）。 */
function MobileFixAttemptItem({
  data,
  isFrontier,
}: {
  data: { attempt: number };
  isFrontier: boolean;
}) {
  return (
    <li className="flex items-center gap-1.5 rounded-2xl px-1 py-1 text-sm">
      {isFrontier ? (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-amber-600" strokeWidth={2} />
      ) : (
        <Wrench className="h-3 w-3 shrink-0 text-ink-500" strokeWidth={2} />
      )}
      <span className={isFrontier ? "font-medium text-amber-700" : "text-ink-600"}>
        第 {data.attempt} 稿{isFrontier ? "返工中……" : "已重新生成"}
      </span>
    </li>
  );
}

/** 4 级降级链跳变：LLM 首次规划 → LLM 重新生成 → ILS 算法引擎 → 规则引擎兜底。
 * 照 ThoughtPanel.tsx 的 FallbackHopItem 移植。 */
function MobileFallbackHopItem({
  data,
}: {
  data: { from: string; to: string; reason: string };
}) {
  const fromLabel = PLAN_FALLBACK_STAGE_LABEL[data.from] ?? data.from;
  const toLabel = PLAN_FALLBACK_STAGE_LABEL[data.to] ?? data.to;
  return (
    <li className="rounded-2xl border border-amber-500/30 bg-amber-500/10 px-3 py-2">
      <div className="flex items-center gap-1.5">
        <ArrowRightLeft className="h-3 w-3 shrink-0 text-amber-600" strokeWidth={2} />
        <span className="text-sm font-medium text-amber-700">换算法引擎重排</span>
      </div>
      <div className="mt-0.5 ml-[18px] text-sm text-ink-700">{data.reason}</div>
      <div className="mt-0.5 ml-[18px] text-xs text-ink-500">
        {fromLabel} → {toLabel}
      </div>
    </li>
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

  const visibleEntries = itinerary ? getVisibleEntries(itinerary) : [];
  const activityEntries = visibleEntries.filter(
    (entry) => entry.entry_kind === "node" && entry.title,
  );
  // 单思考面（信任带设计终稿 §修订4）：不再在此铺原始 thought.text
  // （"关键判断"曾直读 thoughts.slice(-4) 逐条展示自由文本 rationale，
  // 与 AI 幕后信任带重复）——AI 幕后是唯一思考面，这里只留结构化的工具证据
  // 与修正链路。
  const finishedTools = toolCalls
    .filter((tool) => tool.endedAtSeq != null || tool.success != null)
    .slice(-6);

  if (
    !itinerary &&
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
  // N6：sheet 打开时背景可滚动穿透——锁定 body 滚动，关闭/卸载时恢复。同一
  // 时刻只会挂载一个 open=true 的 MobileSheet 实例（本文件仅一处调用点），
  // 不需要引用计数，简单还原上一次的 inline overflow 值即可。
  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    const { body } = document;
    const previousOverflow = body.style.overflow;
    body.style.overflow = "hidden";
    return () => {
      body.style.overflow = previousOverflow;
    };
  }, [open]);

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
