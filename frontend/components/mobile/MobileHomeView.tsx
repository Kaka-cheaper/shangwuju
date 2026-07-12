"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  ArrowLeftRight,
  Ellipsis,
  ArrowRight as ArrowRightIcon,
  Info,
  Loader2,
  type LucideIcon,
  Plus,
  Route,
  SlidersHorizontal,
  Sparkles,
  Users,
  X,
} from "lucide-react";

import { Icons, scenarioIcon } from "@/lib/icon-map";
import {
  buildCollabChatStateSnapshot,
  buildCollabPlanningEvents,
  useCollabStore,
} from "@/lib/collab-store";
import { buildConfirmPreviewCopy } from "@/lib/confirm-preview";
import { useBootstrapPlannerMode } from "@/lib/hooks/useBootstrapPlannerMode";
import { useCollabDispatch } from "@/lib/hooks/useCollabDispatch";
import { useConfirmAction } from "@/lib/hooks/useConfirmAction";
import { useChatStore } from "@/lib/store";
import type {
  AlternativeOption,
  HopMode,
  Itinerary,
  NodeChip,
  NodeDetail,
  NodeDetailMap,
  ScheduleEntry,
  ChitchatReplyPayload,
} from "@/lib/types";
import {
  clearUserIdCookie,
  cn,
  generateSessionId,
  primaryStoreName,
  upsertSession,
} from "@/lib/utils";

import CollabBar from "../CollabBar";
import ComparisonView, { shouldShowComparison } from "../ComparisonView";
import Confetti, { type ConfettiOrigin } from "../Confetti";
import MapOverlay from "../MapOverlay";
import MockModeBadge from "../MockModeBadge";
import NodeFactPanel, { NodeHeadline } from "../NodeFactPanel";
import OfflineReadyBadge from "../OfflineReadyBadge";
import PlannerModeBadge from "../PlannerModeBadge";
import PosterGenerator from "../PosterGenerator";
import ShareModal from "../ShareModal";
import ToastStack from "../ToastStack";
import TrustBelt from "../TrustBelt";
import TtsPlayer from "../TtsPlayer";
import UserSwitcher from "../UserSwitcher";
import VoteButtons from "../VoteButtons";

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
  const previousItinerary = useChatStore((s) => s.previousItinerary);
  const lastRefinement = useChatStore((s) => s.lastRefinement);
  const startNewSession = useChatStore((s) => s.startNewSession);
  const roomId = useCollabStore((s) => s.roomId);

  const [shareModalOpen, setShareModalOpen] = useState(false);
  const personaResetOnLoadRef = useRef(false);
  const activated = messages.length > 0 || streaming || itinerary != null;
  const canCompare = Boolean(
    previousItinerary &&
      itinerary &&
      lastRefinement &&
      shouldShowComparison(previousItinerary, itinerary),
  );

  // A9 根治：planner 模式的 cookie/health 校准不再依赖 PlannerModeBadge 是否
  // 挂载——移动端此前压根不挂那个徽章组件，plannerMode 永远停在硬编码的
  // "rule"，实际跑的是降智版规则规划。根组件统一调这个 hook 即可校准。
  useBootstrapPlannerMode();

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
        onOpenShareModal={() => setShareModalOpen(true)}
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
        {/* A10：协作状态条（成员/在线/规划触发/连接态）+ 约束流合并 A1
            （2026-07-12）——房间约束流+诉求台账收编进 CollabBar 顶栏那行
            摘要的下拉展开（`lib/collab-feed.ts::mergeCollabFeed`），独立的
            `ConstraintFeed` 约束栏与 `MobilePreferencesCard` 房间态「本次
            调整」台账卡均已删除（demandLedger 数据管线本身不动，只删这两处
            纯展示，避免"同一份协作信息挂三处"）。CollabBar 内部已按
            collabMode 自 return null，非房间态零渲染。-mx-4 抵消 main 的左右
            padding，做到与桌面端一致的"边到边"横条视觉。 */}
        <div className="-mx-4">
          <CollabBar />
        </div>

        {/* A8 根治：SSE 流错误——移动端此前零订阅 streamError，完全静默失败。 */}
        <MobileStreamErrorBanner />

        {/* C4：评委证据徽章——桌面端默认 hidden md:/lg: 在移动端窄容器里天经
            地义不可见，用 compact prop 摘掉这层限制。flex-wrap 而非固定高度
            一行，窄屏（iPhone SE 等）挤不下时自然换行，不会裁切。 */}
        <MobileScenarioRail compact={activated} />

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

      <MobileActionRail />
      <MobileComposer />
      <ToastStack />
      <Confetti origin={MOBILE_CONFETTI_ORIGIN} />

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
  onOpenShareModal,
}: {
  onNewSession: () => void;
  onOpenShareModal: () => void;
}) {
  // 2026-07-06 收口：「开多人房间」从「方案工具」抽屉摘到顶栏持久位置——
  // 这是协作/邀请动作、且不依赖已有方案（可以先开房再一起规划），同桌面端
  // HomeView 头部同款处理（Figma/Notion 的 Share 常驻右上角，不是"对着
  // 某份文档才出现"的工具）。原按钮逻辑照 ItineraryUtilityBar.tsx /
  // MobileActionRail 既有 handleCreateRoom 搬来，去掉了 !itinerary 这层
  // 依赖——buildCollabPlanningEvents/buildCollabChatStateSnapshot 本就按
  // state 里各字段是否存在分别兜底，itinerary 为 null 时一样能开出空房间。
  const streaming = useChatStore((s) => s.streaming);
  const pushToast = useChatStore((s) => s.pushToast);
  const collabMode = useCollabStore((s) => s.collabMode);
  const roomId = useCollabStore((s) => s.roomId);
  const createRoom = useCollabStore((s) => s.createRoom);
  const joinRoom = useCollabStore((s) => s.joinRoom);
  const [creatingRoom, setCreatingRoom] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const showShareRoom = collabMode && !!roomId;

  const handleRoomAction = async () => {
    if (creatingRoom || streaming) return;
    if (showShareRoom) {
      onOpenShareModal();
      setMenuOpen(false);
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
      setMenuOpen(false);
    } finally {
      setCreatingRoom(false);
    }
  };

  const handleNewSession = () => {
    setMenuOpen(false);
    onNewSession();
  };

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

        <div className="relative flex shrink-0 items-center gap-1.5">
          <UserSwitcher autoOpenOnMount />
          <button
            type="button"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-black/[0.08] bg-white/[0.68] text-ink-600 shadow-sm backdrop-blur transition hover:border-accent-400/50 hover:bg-white/[0.88] hover:text-ink-900 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label={menuOpen ? "收起更多操作" : "展开更多操作"}
            aria-expanded={menuOpen}
            title="更多操作"
          >
            <Ellipsis className="h-5 w-5" strokeWidth={2.3} />
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-[calc(100%+10px)] z-50 w-[218px] rounded-[26px] border border-black/[0.07] bg-white/[0.92] p-2.5 shadow-[0_24px_70px_-42px_rgba(17,24,39,0.68)] backdrop-blur-2xl animate-fade-in">
              <div className="space-y-2">
                <button
                  type="button"
                  className="flex h-11 w-full items-center justify-center gap-2 rounded-full border border-[#FFD100]/45 bg-[#FFD100]/90 px-4 text-sm font-black tracking-tight text-ink-950 shadow-sm shadow-[#FFD100]/20 transition hover:bg-[#FFD100] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
                  onClick={() => void handleRoomAction()}
                  disabled={creatingRoom || streaming}
                >
                  {creatingRoom ? (
                    <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
                  ) : (
                    <Users className="h-4 w-4" strokeWidth={2.2} />
                  )}
                  <span>{showShareRoom ? "分享多人房间" : "建多人房间"}</span>
                </button>
                <button
                  type="button"
                  className="flex h-11 w-full items-center justify-center gap-2 rounded-full border border-black/[0.08] bg-white px-4 text-sm font-black tracking-tight text-ink-900 shadow-sm transition hover:bg-black/[0.03] active:scale-[0.98]"
                  onClick={handleNewSession}
                >
                  <ArrowRightIcon className="h-4 w-4" strokeWidth={2.2} />
                  <span>开启新对话</span>
                </button>
                <div className="h-px bg-black/[0.06]" />
                <div className="flex flex-wrap items-center gap-1.5">
                  <PlannerModeBadge />
                  <MockModeBadge compact />
                  <OfflineReadyBadge compact />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

// B3 的 `MobilePreferencesCard`（房间态「本次调整」台账折叠卡）已随约束流
// 合并 A1（2026-07-12）删除——它在删除前已经是 100% 房间专属组件
// （`if (!currentUserId || !collabMode) return null;` 是函数体第一行，
// 单人模式恒不渲染；"画像"/"这次对话学到的"两区也早已 `!collabMode` 门控，
// 房间态下实际只剩"本次调整"这一区在渲染），该区展示职责已经收编进
// `CollabBar.tsx` 顶栏摘要的下拉展开（`lib/collab-feed.ts::
// mergeCollabFeed` 把这里的 demandLedger 与约束流合并成一条流），不是
// "留一个空壳组件"——整份删除，避免留下恒定死代码。demandLedger 数据
// 管线本身（store 字段、后端广播、refiner 消费）不受影响。

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
        "relative overflow-hidden rounded-[28px] border border-[#FFD100]/[0.34] bg-white/[0.9]",
        "shadow-[0_24px_64px_-46px_rgba(17,24,39,0.72),inset_0_1px_0_rgba(255,255,255,0.92)] backdrop-blur-2xl",
        compact ? "px-3.5 py-3.5" : "px-4 py-4",
      )}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute -right-10 -top-12 h-32 w-32 rounded-full bg-[#FFD100]/[0.13] blur-2xl"
      />
      <span
        aria-hidden
        className="pointer-events-none absolute left-8 top-0 h-px w-32 bg-gradient-to-r from-transparent via-[#FFD100]/[0.55] to-transparent"
      />
      <div className="relative z-10 mb-3 flex items-end justify-between gap-3">
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
          "relative z-10 flex snap-x gap-2 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
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
                "snap-start rounded-[22px] border border-[#FFD100]/[0.36] bg-gradient-to-br from-white via-white to-[#fff8d8]/70 text-center",
                "shadow-[0_14px_30px_-24px_rgba(17,24,39,0.72),inset_0_1px_0_rgba(255,255,255,0.96)] transition active:scale-[0.98]",
                "disabled:cursor-not-allowed disabled:opacity-55",
                compact
                  ? "flex min-h-[62px] min-w-[116px] flex-col items-center justify-center gap-1.5 px-2.5 py-2"
                  : "flex min-h-[96px] flex-col items-center justify-center gap-3 px-3.5 py-3",
              )}
              title={scenario.input}
            >
              <ScenarioIcon
                className={cn("text-amber-600", compact ? "h-[18px] w-[18px]" : "h-6 w-6")}
                strokeWidth={2}
              />
              <span className={cn("block font-semibold leading-tight tracking-tight text-ink-900", compact ? "text-sm" : "text-base")}>
                {scenario.title}
              </span>
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

function MobileConversation() {
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

  if (!timeline.length && !streaming) return null;

  return (
    <section className="mt-3 space-y-2.5">
      {timeline.slice(-6).map((item) =>
        item.kind === "message" ? (
          <MobileBubble key={item.id} role={item.role} text={item.text} />
        ) : (
          <MobileChitchatBubble key={item.id} payload={item.payload} />
        ),
      )}
    </section>
  );
}

const MOBILE_KIND_LABELS: Record<ChitchatReplyPayload["input_kind"], string> = {
  planning: "规划",
  chitchat: "闲聊",
  confirm: "确认",
  clarify: "澄清",
  defense: "婉拒",
};

function MobileChitchatBubble({ payload }: { payload: ChitchatReplyPayload }) {
  const streaming = useChatStore((s) => s.streaming);
  const booked = useChatStore((s) => (s.itinerary?.orders?.length ?? 0) > 0);
  const { sendUserInput } = useCollabDispatch();
  const { handleConfirm } = useConfirmAction();

  const handleChipClick = (chip: ChitchatReplyPayload["cta_chips"][number]) => {
    if (chip.action === "confirm") {
      handleConfirm();
      return;
    }
    sendUserInput(chip.send);
  };

  return (
    <div className="flex justify-start animate-fade-in-up">
      <div className="max-w-[86%] rounded-3xl rounded-bl-lg border border-black/[0.06] bg-white/[0.86] px-4 py-2.5 text-[15px] leading-relaxed tracking-tight text-ink-800 shadow-sm backdrop-blur-xl">
        <div className="mb-1.5 inline-flex items-center gap-1.5 text-xs font-medium text-ink-500">
          <Sparkles className="h-3.5 w-3.5 text-amber-500" strokeWidth={2.2} />
          <span>{MOBILE_KIND_LABELS[payload.input_kind] ?? payload.input_kind}</span>
        </div>
        <div className="whitespace-pre-wrap text-ink-900">{payload.reply_text}</div>
        {payload.cta_chips.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {payload.cta_chips.map((chip, idx) => {
              const isConfirm = chip.action === "confirm";
              const isBooked = isConfirm && booked;
              return (
                <button
                  key={`${chip.send}-${idx}`}
                  disabled={streaming || isBooked}
                  onClick={() => handleChipClick(chip)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold tracking-tight transition duration-150 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55",
                    isBooked
                      ? // 已预约：暖金淡底（去绿归色，与桌面端 ChitchatBubble 同语义）
                        "border-accent-500/30 bg-accent-500/12 text-accent-700 active:scale-100"
                      : isConfirm
                        ? "border-[#F6C400] bg-[#FFD100] text-ink-950 shadow-sm shadow-[#FFD100]/20 hover:bg-[#FFE15A]"
                        : "border-black/[0.08] bg-black/[0.035] text-ink-700 hover:border-black/[0.14] hover:bg-black/[0.055] hover:text-ink-900",
                  )}
                  title={isBooked ? "已完成预约" : chip.send}
                >
                  {!isConfirm && chip.icon ? (
                    <span className="text-xs leading-none opacity-70">{chip.icon}</span>
                  ) : null}
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
  const narration = useChatStore((s) => s.narration);
  const memoryPersisted = useChatStore((s) => s.memoryPersisted);
  const lastRefinement = useChatStore((s) => s.lastRefinement);
  const cancelled = useChatStore((s) => s.cancelled);
  // 预约卡重设计（UI 修复批）：时段/花费前端 join 需要 nodeDetail（同 Web 端
  // ItineraryCard 的读法，见 NodeDetail.price_text）。
  const nodeDetail = useChatStore((s) => s.nodeDetail);

  if (!itinerary && !streaming) return null;

  if (!itinerary) {
    return (
      <div className="mt-3 space-y-3">
        {/* 信任带（移动端同款）：规划中就该看到"它在想什么"，不必等方案落地。 */}
        <TrustBelt />
        <section className="rounded-[30px] border border-black/[0.06] bg-white px-4 py-4 shadow-[0_1px_3px_rgba(0,0,0,0.04),0_4px_16px_-4px_rgba(0,0,0,0.04)]">
          <div className="flex items-center gap-2 text-base font-semibold leading-snug text-ink-900">
            <span
              aria-hidden
              className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-[#FFD100]/35 border-t-[#f59e0b]"
            />
            <span>正在拼装行程方案~</span>
          </div>
          <div className="mt-4 space-y-3.5">
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
  // 端点钟点（方案A·钟点前置）：首个可见 entry 的 start = 离开望京的时刻，末个
  // entry 的 end = 回到望京的时刻——首/末段通勤 hop 自带 [start,end]，无 hop 时
  // 降级到首/末节点的起止钟点，让时间轴首尾也落在同一条"钟点前置"读法上。
  const dayStart = visibleEntries[0]?.start ?? null;
  const dayEnd =
    visibleEntries.length > 0 ? visibleEntries[visibleEntries.length - 1].end : null;

  return (
    <div className="mt-3 space-y-3">
      {/* 信任带（单一稳定实例，2026-07-06 收口，同 Web 端 ItineraryCard 的
          结构）：固定挂在方案卡容器上方，规划中（上方 !itinerary 分支）与
          就绪两态共享同一个挂载位置——itinerary 从 null→非 null 切分支时，
          外层 <div className="mt-3 space-y-3"> 与这个 <TrustBelt/> 类型、
          位置一致，React 不会卸载重挂它。层级意图同 Web 端：[AI 幕后] 在
          上、[方案卡] 在下——本批不动卡头视觉。 */}
      <TrustBelt />
      {/* 方案卡主角化（⑤ 静态部分：暖调精修抬升 + 顶缘暖金发丝 rim-light，见
          globals.css .itinerary-hero）。移动端无 spotlight 一次性到场态，故"到场
          柔光绽放"暂缓（记档待补），先给持续的主角身份，与 Web 端观感一致。 */}
      <article className="itinerary-hero relative overflow-hidden rounded-[30px] border border-black/[0.06] bg-white">
      <div
        aria-hidden
        className="absolute right-6 top-0 h-8 w-14 -translate-y-2 rotate-6 rounded-b-lg bg-[#e8d7b5]/70 shadow-sm"
      />
      <div className="relative px-4 pb-2 pt-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Icons.camera className="h-[18px] w-[18px] shrink-0 text-[#8f4b24]" strokeWidth={1.8} aria-hidden />
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

      {/* 2026-07-12 收口（同桌面端）：删「已根据反馈调整」横幅——芯片与①对比
          重复、note 与③口播开头重复；口播直接借它那张卡的规整外壳（白底/边框/
          圆角/阴影，无 header）。 */}

      {/* B1：narration 暖心文案——此前只显示裸 itinerary.summary，从没消费
          narration，手机用户看到的正是被替代掉的旧套话。2026-07-06 收口：
          "为你考虑了" chips 与取舍说明折叠已删（与叙事正文重复的第二/三份
          声音，同 Web 端 ItineraryCard 同批处理），只留 narration.text 这一份。 */}
      {narration?.text && (
        <div className="px-4 pt-2">
          <MobileNarrationBlock text={narration.text} stage={narration?.stage ?? "stream"} />
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

      {/* 信任带已上移到方案卡容器上方（本函数顶部单一稳定实例，2026-07-06
          收口）——此处原有的卡内 <TrustBelt/> 已删除，避免移动端渲染两个信任带
          （上移时的漏删，深审补掉）。 */}
      <ol className="relative px-3 pb-5 pt-3">
        {/* 家（首尾 bookend，卡片精修设计终稿.md §六）+ 时间轴脊柱（时间轴精修
            设计终稿.md §一/§二）：起点 bookend 自己的"到第一站"连接线携带
            出发通勤（若有），每个节点携带"到下一站"的连接线（最后一个节点
            接到收尾 bookend），通勤统一挪到脊柱上,不再有独立通勤卡。 */}
        <MobileWebBookend
          kind="start"
          nodeCount={noteItems.length}
          time={dayStart}
          hop={
            noteItems[0]
              ? hopEntryAt(visibleEntries, noteItems[0].entryIndex - 1)
              : null
          }
        />
        {noteItems.map(({ entry, entryIndex }, index) => {
          const node = itinerary.nodes.find((n) => n.node_id === entry.ref_id);
          const hopAfter = hopEntryAt(visibleEntries, entryIndex + 1);
          return (
            <MobileWebTimelineItem
              key={entry.ref_id}
              entry={entry}
              node={node}
              hopAfter={hopAfter}
              index={index}
              total={noteItems.length}
            />
          );
        })}
        <MobileWebBookend kind="end" nodeCount={noteItems.length} time={dayEnd} />
      </ol>

      {/* 预约卡重设计（UI 修复批·纯前端）：同 Web 端 ItineraryCard 的
          MobileOrderCards——删内部编号、多卡横向排一行、暖金描边+中性文字，
          时段/花费按 target_id 前端 join（itinerary.nodes + nodeDetail）。 */}
      {hasOrders && (
        <div className="mx-4 mb-3">
          <div className="mb-1.5 text-sm font-semibold text-ink-900">
            已为你预留
          </div>
          <MobileOrderCards orders={itinerary.orders} nodes={itinerary.nodes} nodeDetail={nodeDetail} />
        </div>
      )}

      {/* B6：转发文案卡——itinerary.share_message，注意这与海报生成器
          （PosterGenerator，见下方 MobileActionRail 展开菜单）的文案不是
          同一份内容，不能互相替代：前者是 generate_share_message 工具产出，
          后者是 PosterGenerator 自己拼的海报文案。
          移动端海报/TTS 归属（用户拍板，覆盖桌面端"分享簇"方案）：屏窄，
          "+"抽屉收纳比常驻分享簇更合适——继续留在 MobileActionRail 的
          expanded 抽屉里，不挪到这里；本批只解除抽屉的 hasOrders 连坐
          （见 MobileActionRail 内注释），确保确认后海报/TTS 仍可达。 */}
      {itinerary.share_message && (
        <div className="mx-4 mb-3">
          <MobileShareMessage text={itinerary.share_message} />
        </div>
      )}

      {/* B8：「确认后会发生什么」预告卡——纯派生展示，让用户不点确认也能
          看到一键执行能力（下单后由上面的"已为你预留"订单卡接力）。 */}
      {/* B10：取消方案后的文案提示——此前 cancelled 只用于禁用按钮，没有
          任何文案告知用户"为什么按钮都灰了"。 */}
      {cancelled && !hasOrders && (
        <div className="mx-4 mb-3 rounded-2xl border border-black/[0.06] bg-black/[0.02] px-3.5 py-2.5 text-center text-sm text-ink-500">
          已取消方案，可重新输入或点击场景按钮
        </div>
      )}

      </article>
    </div>
  );
}

// ============================================================
// ============================================================
// B1：Agent 暖心开场白 + intent 命中可视化（narration + "为你考虑了" chips +
// D-7 取舍说明）。照 ItineraryCard.tsx:752-851（NarrationBlock）+ :993-1066
// （buildIntentChips，此前抽到 lib/intent-chips.ts；该文件已随另一批改动
// 删除，本行注释不再指向死链接）移植，HighlightText 逐字高亮暂不移植
// （属于 Tier C 视觉打磨 C2，不影响内容完整性）。
// ============================================================

function MobileNarrationBlock({
  text,
  stage,
}: {
  text?: string;
  stage: "stream" | "confirm";
}) {
  const isConfirm = stage === "confirm";
  if (!text) return null;

  return (
    <div
      className={cn(
        // 外壳借用原「已根据反馈调整」卡的规整样式（白底/边框/圆角/阴影，无
        // header），与桌面端 NarrationBlock 一致；确认态与 stream 态的区分收到
        // spark 图标深浅一层，不再靠背景色分家（用户拍板 2026-07-12）。
        "rounded-[22px] border border-black/[0.06] bg-white px-4 py-3.5 text-[15px] leading-relaxed shadow-sm backdrop-blur-xl animate-fade-in",
      )}
    >
      <div className="flex items-start gap-2">
        <Sparkles
          className={cn(
            "mt-0.5 h-4 w-4 shrink-0",
            isConfirm ? "text-accent-600" : "text-ink-500",
          )}
          strokeWidth={2}
        />
        <p className="whitespace-pre-wrap text-ink-900">{text}</p>
      </div>
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
    // 去绿归色：同桌面端 MemoryPersistedBadge——"已写入记忆库"是成功/确认通知，
    // 走暖金（accent），不用游离调色板外的 emerald。
    <div className="flex items-start gap-2 rounded-2xl border border-accent-500/25 bg-accent-500/[0.06] px-3.5 py-2.5 text-sm text-accent-700/95 shadow-sm backdrop-blur-xl animate-fade-in">
      <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent-500" strokeWidth={2} />
      <div className="min-w-0 flex-1">
        <div className="font-medium tracking-tight text-accent-600">
          已写入「{socialContext || "本"}」场景的跨 session 召回库
        </div>
        <div className="mt-0.5 line-clamp-1 text-xs text-accent-700/75">
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
    <div className="rounded-[24px] border border-white/[0.76] bg-white px-4 py-3 text-sm leading-relaxed shadow-[inset_0_1px_0_rgba(255,255,255,0.88)]">
      <p className="mb-3 text-base leading-relaxed text-ink-800">
        {restaurantLine}
        {extraLine}；再为你备好一段可一键复制的转发文案；最后{memoryLine}。
      </p>
      <div
        className={cn(
          "grid gap-2 text-xs font-semibold text-ink-600",
          extraServices.length > 0 ? "grid-cols-4" : "grid-cols-3",
        )}
      >
        <span className="inline-flex items-center justify-center gap-1 text-center">
          <span aria-hidden>🪑</span>
          <span>锁餐厅时段</span>
        </span>
        <span className="inline-flex items-center justify-center gap-1 text-center">
          <span aria-hidden>📝</span>
          <span>备转发文案</span>
        </span>
        {extraServices.length > 0 && (
          <span className="inline-flex items-center justify-center gap-1 text-center">
            <span aria-hidden>+</span>
            <span>加购{extraServices[0]}</span>
          </span>
        )}
        <span className="inline-flex items-center justify-center gap-1 text-center">
          <span aria-hidden>🧠</span>
          <span>记本次偏好</span>
        </span>
      </div>
    </div>
  );
}

// ============================================================
// MobileOrderCards —— 预约卡重设计（UI 修复批·纯前端，零后端）
// 照 ItineraryCard.tsx 的 OrderCards/OrderCard 移植，字段前端 join 的理由与
// 桌面端完全一致（见该文件 OrderCards docstring）：时段/花费本来就在同一个
// 组件树内可拿（itinerary.nodes + nodeDetail），不需要新增后端字段。
// ============================================================

function MobileOrderCard({
  order,
  nodes,
  nodeDetail,
}: {
  order: Itinerary["orders"][number];
  nodes: Itinerary["nodes"];
  nodeDetail: NodeDetailMap | null;
}) {
  const node = nodes.find((n) => n.target_id === order.target_id);
  const timeRange = node
    ? `${node.start_time}-${addMinutes(node.start_time, node.duration_min)}`
    : null;
  const priceText = nodeDetail?.[order.target_id]?.price_text ?? null;
  const countMatch = /(\d+\s*(?:人|张|份))/.exec(order.detail);
  const countText = countMatch?.[1] ?? order.detail;

  return (
    <li
      className="relative overflow-hidden rounded-2xl border border-[#e6bc00]/25 bg-white px-3.5 py-2.5"
      style={{
        boxShadow: "0 1px 2px rgba(0,0,0,.03), 0 6px 20px rgba(180,140,0,.05)",
      }}
    >
      <span
        aria-hidden
        className="absolute inset-y-2 left-0 w-[3px] rounded-full"
        style={{ background: "rgba(255,201,0,.35)" }}
      />
      <div className="flex items-start justify-between gap-3 pl-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[15px] font-semibold tracking-tight text-ink-900">
            <Icons.success className="h-3.5 w-3.5 shrink-0 text-[#9a5b00]" strokeWidth={2.2} />
            <span className="truncate">{order.target_name}</span>
          </div>
          <div className="mt-0.5 text-xs text-ink-500">
            {order.kind}
            {timeRange && <span> · {timeRange}</span>}
            {countText && <span> · {countText}</span>}
          </div>
        </div>
        {priceText && (
          <div className="shrink-0 text-right">
            <div className="mono text-base font-bold leading-none text-ink-900">{priceText}</div>
            <div className="mt-1 text-[11px] text-ink-400">预估花费</div>
          </div>
        )}
      </div>
    </li>
  );
}

function MobileOrderCards({
  orders,
  nodes,
  nodeDetail,
}: {
  orders: Itinerary["orders"];
  nodes: Itinerary["nodes"];
  nodeDetail: NodeDetailMap | null;
}) {
  return (
    <ul className="grid grid-cols-1 gap-2">
      {orders.map((o) => (
        <MobileOrderCard key={o.order_id} order={o} nodes={nodes} nodeDetail={nodeDetail} />
      ))}
    </ul>
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
    <div className="rounded-2xl border border-black/[0.07] bg-black/[0.02] px-3.5 py-3">
      <div className="mb-1.5 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Icons.share className="h-3.5 w-3.5 text-ink-500" strokeWidth={2} />
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
              ? "bg-accent-500 text-white"
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

// ============================================================
// 时间轴精修（路演PPT/时间轴精修设计终稿.md）+ 节点卡精修（路演PPT/卡片
// 精修设计终稿.md）落地到移动端——原 NOTEBOOK_TONES 彩虹脊柱（每个节点一个
// 色相，圆点/连线/时间/tag 全部跟着换色）整个删掉：时间轴克制要求脊柱
// "单色暖灰、圆点扁平"，不再有"每一站一个颜色"的信息维度。节点卡的两行制
// （内容行 = 玻璃标签 + 店名，操作行 = 具名备选/定向微调/投票三群纯视觉
// 分组）与 Web 端 ItineraryCard.tsx 共用 .node-card/.node-glass-label/
// .node-card-divider（globals.css 里 a2ad544 已落地、本批不碰）。
// ============================================================

/** 单色暖灰脊柱色（时间轴精修设计终稿.md §一），圆点/连接线/家 bookend 统一用它。 */
type MobileStageTone = {
  gradient: string;
  color: string;
  softBg: string;
  border: string;
};

const MOBILE_HOME_STAGE_TONE: MobileStageTone = {
  gradient: "linear-gradient(135deg, #ffd100 0%, #eab308 100%)",
  color: "#eab308",
  softBg: "rgba(255, 209, 0, 0.14)",
  border: "rgba(234, 179, 8, 0.3)",
};

function mobileStageTone(index: number): MobileStageTone {
  const tones: MobileStageTone[] = [
    {
      gradient: "linear-gradient(135deg, #3f6fb7 0%, #2f5f9f 100%)",
      color: "#3f6fb7",
      softBg: "rgba(63, 111, 183, 0.11)",
      border: "rgba(63, 111, 183, 0.28)",
    },
    {
      gradient: "linear-gradient(135deg, #d93b76 0%, #bd2d66 100%)",
      color: "#bd2d66",
      softBg: "rgba(217, 59, 118, 0.11)",
      border: "rgba(189, 45, 102, 0.27)",
    },
    {
      gradient: "linear-gradient(135deg, #e49a2f 0%, #d97706 100%)",
      color: "#c46705",
      softBg: "rgba(228, 154, 47, 0.13)",
      border: "rgba(217, 119, 6, 0.28)",
    },
    {
      gradient: "linear-gradient(135deg, #37a46f 0%, #23835b 100%)",
      color: "#23835b",
      softBg: "rgba(55, 164, 111, 0.11)",
      border: "rgba(35, 131, 91, 0.26)",
    },
    {
      gradient: "linear-gradient(135deg, #7c5cc7 0%, #5f45a8 100%)",
      color: "#5f45a8",
      softBg: "rgba(124, 92, 199, 0.11)",
      border: "rgba(95, 69, 168, 0.26)",
    },
  ];
  return tones[index % tones.length];
}

function mobileStageLineGradient(current: MobileStageTone, next: MobileStageTone | null): string {
  if (!next) {
    return `linear-gradient(to bottom, ${current.color} 0%, ${current.color} 72%, rgba(216, 210, 196, 0) 100%)`;
  }
  return `linear-gradient(to bottom, ${current.color} 0%, ${next.color} 100%)`;
}

const SPINE_COLOR = "#D8D2C4";

/**
 * 按 visibleEntries 的绝对下标取一条真实通勤（hop）行——virtual（原地/同址
 * 复用）不算通勤，返回 null（脊柱画实线，不出现灰字）。用于把"通勤"从
 * 独立卡挪到脊柱上（时间轴精修设计终稿.md §二）。
 */
function hopEntryAt(entries: ScheduleEntry[], idx: number): ScheduleEntry | null {
  const e = entries[idx];
  return e && e.entry_kind === "hop" && e.mode && e.mode !== "virtual" ? e : null;
}

/**
 * 脊柱连接线（点到下一个点/收尾 bookend）+ 通勤图标标——单色暖灰，有通勤则
 * 虚线 + 小灰字，无通勤则实线（时间轴精修设计终稿.md §一/§二）。绝对定位
 * 依赖调用方的 gutter 容器是 `relative`、固定宽度 `w-[60px]`（见 NotebookBookend/
 * NotebookTimelineItem 左侧列，节点卡+行程轨-对比.html 改版：60px = 时间列
 * 40px + 4px 间距 + 9px 圆点，圆点中心落在 x=49px）且作为 grid 项默认 stretch
 * 到与右侧内容同高（同 Web ItineraryCard 既有写法）；`top` 由调用方传入——
 * 节点行 gutter（时间+点两列）和 bookend gutter（只一个小图标）自身内容高度
 * 不同，共用一个硬编码 top 会在矮的 bookend 里露出一截空白。offset 按"点变
 * 小之后"的新几何手工估算，未经真机/浏览器像素级校验（见交付报告）。
 *
 * 通勤图标+时长横排在 x=49px 圆点【左侧】（right-4=16px，紧贴圆点左边，落在
 * 时间列的视觉车道里）——同 Web 端"通勤挪到脊柱左列"的改版意图，不再贴在圆
 * 点正下方。
 */
function SpineConnector({ hop, top }: { hop: ScheduleEntry | null; top: string }) {
  const HopIcon = hop ? getHopIconComponent(hop.mode) : null;
  return (
    <>
      <span
        aria-hidden
        className={cn(
          "absolute left-[49px] bottom-[-28px] w-0 border-l-[1.5px]",
          top,
          hop ? "border-dashed" : "border-solid",
        )}
        style={{ borderColor: SPINE_COLOR }}
      />
      {hop && HopIcon && (
        <span className="absolute right-4 bottom-[-20px] flex items-center gap-0.5 whitespace-nowrap text-[10px] font-medium text-ink-400">
          <HopIcon className="h-3 w-3 shrink-0" strokeWidth={1.8} />
          {hop.minutes}分钟
        </span>
      )}
    </>
  );
}

/**
 * 家（首尾 bookend，卡片精修设计终稿.md §六）——无主活动标签、无操作层，只
 * 一行文案 + 小 home 图标点；同 Web ItineraryCard 硬编码起止行为一致（文案
 * 沿用既有"出发咯"/"满载而归"，不是数据驱动的 schedule 条目——home 节点在
 * getVisibleEntries 里恒被过滤/隐藏，见该函数注释）。图标用中性灰（不用 Web
 * 旧版的 emerald/red），呼应时间轴精修设计稿"美团黄只在当前节点点一下，其余
 * 脊柱全程暖灰"的克制基调（当前 Web 尚未跟进这处，移动端直接对齐设计稿终态）。
 */
function MobileWebHopPill({ hop }: { hop: ScheduleEntry | null }) {
  if (!hop || !hop.mode || hop.mode === "virtual") return null;
  const HopIcon = getHopIconComponent(hop.mode);
  return (
    <div className="mt-3">
      <span className="inline-flex items-center gap-1.5 rounded-full border border-[#d89a00]/25 bg-[#fff5bf]/70 px-3 py-1.5 text-[13px] font-bold text-[#9a5b00] shadow-sm backdrop-blur">
        <HopIcon className="h-3.5 w-3.5 shrink-0 text-[#9a5b00]" strokeWidth={1.9} />
        <span>通勤 {hop.minutes} 分钟 · {translateHopMode(hop.mode)}</span>
      </span>
    </div>
  );
}

function MobileWebBookend({
  kind,
  hop,
  nodeCount,
  time,
}: {
  kind: "start" | "end";
  hop?: ScheduleEntry | null;
  nodeCount: number;
  time?: string | null;
}) {
  const isStart = kind === "start";
  const firstTone = nodeCount > 0 ? mobileStageTone(0) : MOBILE_HOME_STAGE_TONE;

  return (
    <li className="relative grid grid-cols-[54px_minmax(0,1fr)] gap-2 pb-6">
      <div
        className="relative flex min-h-[48px] items-start justify-center self-stretch pt-1"
        style={
          isStart && nodeCount > 0
            ? ({
                "--timeline-line": mobileStageLineGradient(MOBILE_HOME_STAGE_TONE, firstTone),
                "--timeline-line-top": "42px",
              } as CSSProperties)
            : undefined
        }
      >
        {isStart && nodeCount > 0 && <div aria-hidden className="timeline-spine-seg" />}
        <div className="timeline-dot-home relative z-10">
          <Icons.home className="h-5 w-5 text-white" strokeWidth={2.35} />
        </div>
      </div>
      <div className="min-w-0 pt-2">
        {/* 钟点前置（方案A）：家 bookend 也带钟点，和节点卡同款 clock 图标，但
            用中性灰（HOME 语义，不抢主活动），让左侧时间尺首尾闭合——15:27 出发
            → … → 19:10 到家，把"一个下午"从头框到尾。 */}
        <div className="flex items-center gap-1.5 text-base font-black tracking-tight text-ink-700">
          {time && (
            <span className="inline-flex items-center gap-1 tabular-nums text-ink-500">
              <Icons.clock className="h-[15px] w-[15px] shrink-0" strokeWidth={2} />
              {time}
            </span>
          )}
          <span>{isStart ? "从望京出发" : "回到望京"}</span>
        </div>
        {isStart && <MobileWebHopPill hop={hop ?? null} />}
      </div>
    </li>
  );
}

function mobileNodeDisplayNote(
  node: Itinerary["nodes"][number] | undefined,
  detail?: NodeDetail | null,
): string | null {
  const note = node?.note?.trim();
  if (note) return note;
  const fallback = detail?.recommendation_reason?.trim();
  return fallback ? fallback : null;
}

function MobileWebTimelineItem({
  entry,
  node,
  hopAfter,
  index,
  total,
}: {
  entry: ScheduleEntry;
  node?: Itinerary["nodes"][number];
  hopAfter: ScheduleEntry | null;
  index: number;
  total: number;
}) {
  const streaming = useChatStore((s) => s.streaming);
  const nodeActions = useChatStore((s) => s.nodeActions);
  const nodeDetail = useChatStore((s) => s.nodeDetail);
  const lockedNodeId = useChatStore((s) => s.lockedNodeId);
  const sendAdjust = useChatStore((s) => s.sendAdjust);
  const collabMode = useCollabStore((s) => s.collabMode);
  const sendCollabAdjust = useCollabStore((s) => s.sendAdjust);
  const dispatchAdjust = collabMode ? sendCollabAdjust : sendAdjust;

  const targetId = node?.target_id ?? null;
  const actions = targetId ? nodeActions?.[targetId] : undefined;
  const chips = actions?.chips ?? [];
  const alternatives = (actions?.alternatives ?? []).slice(0, 2);
  const detail = targetId ? nodeDetail?.[targetId] : undefined;
  const isLocked = targetId != null && lockedNodeId === targetId;
  const canAdjust = targetId != null && !isLocked && lockedNodeId == null && !streaming;
  const kindLabel = node?.kind || "活动";
  const note = mobileNodeDisplayNote(node, detail);
  const fullTitle = note ? `${entry.title} · ${note}` : entry.title;
  const hasOperationRow = alternatives.length > 0 || chips.length > 0 || collabMode;
  const tone = mobileStageTone(index);
  const nextTone = index + 1 < total ? mobileStageTone(index + 1) : MOBILE_HOME_STAGE_TONE;

  return (
    <li className="relative grid grid-cols-[54px_minmax(0,1fr)] gap-2 pb-7">
      <div
        className="relative flex min-h-full items-start justify-center self-stretch pt-5"
        style={
          {
            "--timeline-line": mobileStageLineGradient(tone, nextTone),
            "--timeline-line-top": "62px",
          } as CSSProperties
        }
      >
        <div aria-hidden className="timeline-spine-seg" />
        <div
          className={cn("timeline-dot timeline-dot--indexed relative z-10", isLocked && "timeline-dot--current")}
          style={{ background: tone.gradient }}
          title={isLocked ? "换菜中" : undefined}
        >
          <span>{index + 1}</span>
        </div>
      </div>

      <div className="min-w-0">
        <div className="node-card relative min-w-0 px-4 py-3.5 transition-transform active:scale-[0.99]">
          <div className="mb-2 flex items-start justify-between gap-2">
            <span
              className="inline-flex min-w-0 items-center gap-1.5 text-[17px] font-black leading-none tabular-nums"
              style={{ color: tone.color }}
            >
              <Icons.clock className="h-4 w-4 shrink-0" strokeWidth={2} />
              <span>{entry.start} - {entry.end}</span>
            </span>
            {!isLocked && <NodeHeadline detail={detail} className="pt-0" />}
          </div>

          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5">
            <span
              className="node-glass-label shrink-0 border px-2.5 py-1 text-sm font-bold tracking-tight"
              style={{
                color: tone.color,
                backgroundColor: tone.softBg,
                borderColor: tone.border,
              }}
            >
              {kindLabel}
            </span>
            {isLocked ? (
              <span className="h-6 w-36 shrink-0 rounded shimmer-skeleton" aria-hidden />
            ) : (
              <span
                className="node-title-wavy min-w-0 max-w-full text-[19px] font-black leading-tight text-ink-900"
                title={fullTitle}
              >
                {entry.title}
              </span>
            )}
          </div>

          {!isLocked && (
            <NodeFactPanel
              detail={detail}
              className="mt-2"
              tone={tone}
              size="large"
            />
          )}

          {note && !isLocked && (
            <div className="mt-2 text-[15px] font-medium leading-relaxed text-ink-500">
              {note}
            </div>
          )}

          {targetId && hasOperationRow && <div className="node-card-divider mt-3 mb-2.5" aria-hidden />}

          {targetId && hasOperationRow && (
            <div className={cn("flex flex-col gap-2", isLocked && "pointer-events-none opacity-40")}>
              {alternatives.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
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
                </div>
              )}
              {(chips.length > 0 || collabMode) && (
                <div className="flex flex-wrap items-center gap-2">
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
                  <span className="ml-auto flex items-center">
                    <VoteButtons stageIndex={index} />
                  </span>
                </div>
              )}
            </div>
          )}

          {isLocked && (
            <div className="mt-2 flex items-center gap-1 text-xs text-ink-400">
              <Icons.thinking className="h-3 w-3 animate-spin" strokeWidth={2} />
              <span>换菜中…</span>
            </div>
          )}
        </div>
        <MobileWebHopPill hop={hopAfter} />
      </div>
    </li>
  );
}

function NotebookBookend({ kind, hop }: { kind: "start" | "end"; hop?: ScheduleEntry | null }) {
  const isStart = kind === "start";
  return (
    <li className="relative grid grid-cols-[60px_minmax(0,1fr)] gap-3 pb-6">
      {/* 左侧 gutter：60px 固定宽，和 NotebookTimelineItem 的节点行共用同一条
          x=49px 圆点车道（节点卡+行程轨-对比.html「左行程轨同款」——时间/通勤
          挪到脊柱左列这套改法，移动端和 Web 端用同一个几何基准）。家 bookend
          没有起止钟点，时间列这里只放"出发/到家"极小标签。 */}
      <div className="relative flex items-start gap-1 pt-1">
        <div className="w-10 shrink-0 pt-1 text-right text-[10px] font-medium text-ink-400">
          {isStart ? "出发" : "到家"}
        </div>
        {/* w-[10px] + justify-center：18px 图标比 9px 圆点宽，用固定 10px 车道
            + 溢出居中，让图标的几何中心也落在 x=49px（40 timecol + 4 gap + 5），
            和 SpineConnector 的竖线基准（left-[49px]）对上。 */}
        <div className="flex w-[10px] shrink-0 justify-center">
          <span
            aria-hidden
            className="relative z-10 mt-0.5 grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full bg-black/[0.04]"
          >
            <Icons.home className="h-[9px] w-[9px] text-ink-400" strokeWidth={2.5} />
          </span>
        </div>
        {isStart && <SpineConnector hop={hop ?? null} top="top-6" />}
      </div>
      <div className="flex min-h-[18px] items-center text-sm font-medium text-ink-500">
        {isStart ? "出发咯" : "满载而归"}
      </div>
    </li>
  );
}

function NotebookTimelineItem({
  entry,
  node,
  hopAfter,
  index,
}: {
  entry: ScheduleEntry;
  node?: Itinerary["nodes"][number];
  hopAfter: ScheduleEntry | null;
  index: number;
}) {
  // A2/A3（ADR-0013 F-4/F-5）：节点行调整入口——具名备选 / 定向调整 chips /
  // 赞踩，此前 NotebookTimelineItem 完全没订阅 nodeActions/lockedNodeId/
  // sendAdjust，移动端拿到的节点是"只能看不能改"的静态卡片。照
  // ItineraryCard.tsx:401-499 + :1204-1260 移植，collabMode 分流手法同
  // ItineraryCard（房间模式走 WS sendAdjust，单人走 HTTP /chat/adjust）。
  const streaming = useChatStore((s) => s.streaming);
  const nodeActions = useChatStore((s) => s.nodeActions);
  // 卡片主角化与事实面板设计终稿§四"移动端镜像"：右栏收窄成店名下一行事实
  // chips，不强上两栏——数据源同 Web 端一样镜像 nodeActions 的读法。
  const nodeDetail = useChatStore((s) => s.nodeDetail);
  const lockedNodeId = useChatStore((s) => s.lockedNodeId);
  const sendAdjust = useChatStore((s) => s.sendAdjust);
  const collabMode = useCollabStore((s) => s.collabMode);
  const sendCollabAdjust = useCollabStore((s) => s.sendAdjust);
  const dispatchAdjust = collabMode ? sendCollabAdjust : sendAdjust;

  const targetId = node?.target_id ?? null;
  const actions = targetId ? nodeActions?.[targetId] : undefined;
  const chips = actions?.chips ?? [];
  const alternatives = (actions?.alternatives ?? []).slice(0, 2);
  const detail = targetId ? nodeDetail?.[targetId] : undefined;
  const isLocked = targetId != null && lockedNodeId === targetId;
  const canAdjust = targetId != null && !isLocked && lockedNodeId == null && !streaming;
  const kindLabel = node?.kind || "活动";
  const note = mobileNodeDisplayNote(node, detail);
  const fullTitle = note ? `${entry.title} · ${note}` : entry.title;
  const hasOperationRow = alternatives.length > 0 || chips.length > 0 || collabMode;

  return (
    <li className="relative grid grid-cols-[60px_minmax(0,1fr)] gap-3 pb-6">
      {/* 左侧 gutter（节点卡+行程轨-对比.html 改版）：时间挪到脊柱左侧独立
          一列（timecol，40px 右对齐），不再堆在圆点正上/正下方；圆点车道
          （9px）紧贴其后，几何中心落在 x=49px（40 timecol + 4 gap + 4.5）——
          单色暖灰扁平点（无光泽、无编号，时间轴精修设计终稿.md §一）+ 到下一
          个点（或收尾 bookend）的连接线，通勤图标+时长横排在同一条 x=49px
          圆点左侧（SpineConnector 内部实现）。 */}
      <div className="relative flex items-start gap-1 pt-1">
        <div className="flex w-10 shrink-0 flex-col items-end gap-1 text-right">
          <span className="text-[12px] font-medium leading-none tabular-nums text-ink-500">
            {entry.start}
          </span>
          <span className="text-[12px] font-medium leading-none tabular-nums text-ink-500">
            {entry.end}
          </span>
        </div>
        <span
          aria-hidden
          className="mt-0.5 h-[9px] w-[9px] shrink-0 rounded-full border-2 border-white"
          style={{ background: SPINE_COLOR }}
        />
        {/* 联动·连接（时间轴精修设计终稿.md §三.2）：点到卡片左缘一条极短
            极淡的连接线，把"这个点属于这张卡"钉死。悬停协同（§三.3）在触屏
            上意义不大，改用 .node-card 上既有的 active: 轻反馈顶替，此处只
            做静态对齐 + 连接，不做双向高亮联动。top 值随"时间挪去左列"重新
            估算（原来的点在两行时间文字之下，现在时间和点同一行、点更靠
            上），未经真机像素校验。 */}
        <span
          aria-hidden
          className="absolute left-full top-[11px] h-px w-3 -translate-y-1/2"
          style={{ background: `${SPINE_COLOR}b3` }}
        />
        <SpineConnector hop={hopAfter} top="top-8" />
      </div>

      {/* 右侧：节点卡（卡片精修设计终稿.md §二/§四/§八——复用已提交的
          .node-card/.node-glass-label/.node-card-divider，不新增/不改
          globals.css）。active: 轻按反馈顶替 Web 端的 hover 抬升（触屏无
          hover）。 */}
      <div className="node-card relative min-w-0 px-3.5 pt-3 pb-2.5 transition-transform active:scale-[0.99]">
        {/* 店名行：左标签+店名，右上角 headline（评分★+人均，NodeHeadline）
            ——同 Web 端节点卡+行程轨-对比.html 改版，原先"店名下一行事实
            chips 里混着评分/人均"已拆开，headline 单独挂右上角。 */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-1 items-start gap-x-2">
            <span className="node-glass-label shrink-0 px-2 py-[3px] text-[11px] font-semibold tracking-[0.05em] text-amber-700">
              {kindLabel}
            </span>
            {isLocked ? (
              <span className="h-4 w-28 shrink-0 rounded shimmer-skeleton" aria-hidden />
            ) : (
              <span
                className="min-w-0 flex-1 text-[15px] font-semibold leading-snug text-ink-900"
                title={fullTitle}
              >
                {entry.title}
              </span>
            )}
          </div>
          {!isLocked && <NodeHeadline detail={detail} />}
        </div>

        {/* 事实行（设计终稿§四"移动端镜像"：窄屏仍是店名下一行横排，不强上
            两栏）——距离·可订·营业至+tag，评分/人均已被上面 headline 拿走，
            这里不再重复；随店名一起在换菜中隐藏。 */}
        {!isLocked && <NodeFactPanel detail={detail} className="mt-1.5" />}

        {/* 理由行：选店理由（note），从"店名后缀"升级成事实行下方独立一行
            （照视觉稿）；理由原文一字不改。 */}
        {note && !isLocked && (
          <div className="mt-1.5 text-[13px] leading-relaxed text-ink-500">{note}</div>
        )}

        {targetId && hasOperationRow && <div className="node-card-divider mt-2.5 mb-2" aria-hidden />}

        {/* 操作行：整行降权，三群纯视觉分组不加文字标签（卡片精修设计终稿.md
            §五）——窄屏换行：②具名备选占一行、③定向微调+④投票占第二行
            （同文档 §九）。 */}
        {targetId && hasOperationRow && (
          <div className={cn("flex flex-col gap-1.5", isLocked && "pointer-events-none opacity-40")}>
            {alternatives.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
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
              </div>
            )}
            {(chips.length > 0 || collabMode) && (
              <div className="flex flex-wrap items-center gap-1.5">
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
                <span className="ml-auto flex items-center">
                  <VoteButtons stageIndex={index} />
                </span>
              </div>
            )}
          </div>
        )}

        {/* 换菜中（loading）：店名位已是 shimmer 骨架、操作行已降透明，这里
            只补一句幽灰小字状态提示——不整卡闪（卡片精修设计终稿.md §六
            「换菜中」，替掉旧版整行 ShimmerStripe，和降权后的操作行响度
            一致）。 */}
        {isLocked && (
          <div className="mt-1.5 flex items-center gap-1 text-xs text-ink-400">
            <Icons.thinking className="h-3 w-3 animate-spin" strokeWidth={2} />
            <span>换菜中…</span>
          </div>
        )}
      </div>
    </li>
  );
}

// ============================================================
// A2：节点行调整入口——具名备选按钮 / 定向调整 chip（移动端配色）。
// 时间轴精修设计终稿.md §四「切换活动按钮按内容自适应」：按钮宽度贴合
// 名字（max-width + truncate），短名全显，只在真的超长时才截并 title 出
// 全名——span 是 inline-flex 按钮的 flex 子项，会被 CSS 隐式 blockify，
// max-width/truncate 因此在不加 display:block 的情况下就能生效。窄屏
// 备选名截更短（卡片精修设计终稿.md §九），故 max-width 比桌面端更紧。
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
        "flex min-h-[40px] w-full items-center gap-1.5 rounded-full bg-black/[0.03] px-3.5 py-1 text-[12.5px] font-medium tracking-tight text-ink-600",
        "transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40",
      )}
    >
      <ArrowLeftRight className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
      {/* 全宽行 + flex-1：换菜候选店名用满可用宽度（≤2 条纵向铺开），不再被
          max-w-[6rem] 早早截成"朴家一头猪(合…"，只有极长名才 truncate 兜底。 */}
      <span className="min-w-0 flex-1 truncate text-left">{primaryStoreName(alt.name)}</span>
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
        "inline-flex min-h-[40px] items-center gap-1 rounded-full px-2 py-1 text-xs font-normal tracking-tight text-ink-400",
        "transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40",
      )}
    >
      <SlidersHorizontal className="h-3 w-3 shrink-0" strokeWidth={2} />
      <span className="max-w-[5rem] truncate">{chip.label}</span>
    </button>
  );
}

/** 通勤方式 → lucide 线性图标（emoji 🚶🚌🚕🚗 换掉，去塑料感，同 Web 端 ItineraryCard.tsx 的 hopIconComponent）。 */
function getHopIconComponent(mode: HopMode | null | undefined): LucideIcon {
  switch (mode) {
    case "bus":
      return Icons.bus;
    case "taxi":
      return Icons.taxi;
    case "walking":
    default:
      return Icons.footprints;
  }
}

function MobileInlineMap({ itinerary }: { itinerary: Itinerary }) {
  return (
    <section className="mt-3">
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
    <section className="mt-3 overflow-hidden rounded-[30px] border border-black/[0.06] bg-white shadow-[0_18px_46px_-36px_rgba(17,24,39,0.68)]">
      <div className="flex items-center gap-2 border-b border-black/[0.05] px-4 py-3.5">
        <Sparkles className="h-4 w-4 text-ink-600" strokeWidth={2} />
        <h2 className="text-lg font-black tracking-tight text-ink-900">
          调整对比
        </h2>
      </div>
      <div className="px-3 py-3">
        <ComparisonView
          oldItinerary={previousItinerary}
          newItinerary={itinerary}
          variant="mobile"
        />
      </div>
    </section>
  );
}

function MobileActionRail() {
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);
  const cancel = useChatStore((s) => s.cancel);
  const [expanded, setExpanded] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  // A6 根治：确认按钮此前无条件调单人 confirm() action，房间参与者能绕过
  // "仅房主可确认"守卫（且完全没走 WS confirm 通道，其它成员看不到）。
  // canConfirm/handleConfirm/confirmLabel 已由共享 hook 统一判定（同
  // ItineraryCard 桌面端confirm 按钮共用同一份逻辑）。必须在下方 early
  // return 之前调用（hooks 规则）。
  const { canConfirm, handleConfirm, confirmLabel, blockedByOwnerGuard } =
    useConfirmAction();

  if (!itinerary && !streaming) return null;

  // 规划中：底部不再另起一张「Agent 正在思考」进度卡——与上方「AI 幕后」信任带
  // （AI 正在思考中 + 拼装骨架）语义重复（用户 2026-07-12 拍板）。规划态的进度
  // 表达统一收口到「AI 幕后」+ 输入框占位文案，底部固定层此时留空。
  if (streaming) return null;

  const hasOrders = itinerary?.orders.length ?? 0;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(82px+env(safe-area-inset-bottom,0px))] z-40 px-4">
      {/* 展开抽屉：语音播报 / 一键生成海报——2026-07-06 收口：「开多人房间」
          已挪到顶栏持久位置（MobileTopBar），「说说哪不对」已删（反馈走下方
          聊天框即可）；移动端清理批：「取消方案」已从这个抽屉摘出、放到下方
          底栏与「确认并预约」并排常驻（确认左/取消右），抽屉里现在只留这两个
          次级工具，同桌面端 ItineraryCard「安静工具行」一个意思，只是移动端
          沿用既有的收纳抽屉承载而不是常驻一行（屏窄，收纳比常驻更合适）。 */}
      {previewOpen && itinerary && !hasOrders && !cancelled && (
        <div className="pointer-events-auto mx-auto mb-2 max-w-[480px] animate-drawer-slide-up overflow-hidden rounded-[28px] border border-white/[0.86] bg-white p-2 shadow-[0_24px_70px_-42px_rgba(17,24,39,0.82)] ring-1 ring-black/[0.035]">
          <div className="flex items-center justify-between gap-3 px-3 py-2">
            <div className="flex items-center gap-2 text-base font-black tracking-tight text-ink-900">
              <Info className="h-5 w-5 text-[#9a5b00]" strokeWidth={2.5} />
              点击「确认并预约」之后
            </div>
            <button
              type="button"
              className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-black/[0.06] bg-white/[0.72] text-ink-500 transition active:scale-95"
              onClick={() => setPreviewOpen(false)}
              aria-label="关闭预约说明"
            >
              <X className="h-4 w-4" strokeWidth={2.2} />
            </button>
          </div>
          <MobileConfirmPreview itinerary={itinerary} />
        </div>
      )}
      {/* 抽屉本身解连坐（UI 修复批 + 用户移动端纠正）：海报/TTS 与"是否已
          下单"无关（两个组件自身都没有 hasOrders 相关守卫），此前整个抽屉
          随 hasOrders 一起消失，确认后海报/TTS 彻底够不着。现在抽屉容器
          不再依赖 hasOrders——只要展开过、有方案、没取消就渲染。移动端清理批：
          「取消方案」已摘出这个抽屉，挪到下方底栏与确认并排常驻，抽屉现在
          只装语音播报 + 海报两个次级工具。 */}
      {expanded && itinerary && !cancelled && (
        <div className="pointer-events-auto mx-auto mb-2 flex max-w-[480px] justify-end">
          <div className="flex w-[190px] flex-col gap-2 rounded-[24px] border border-white/[0.74] bg-white/[0.72] p-2 shadow-[0_18px_44px_-30px_rgba(17,24,39,0.78)] backdrop-blur-2xl backdrop-saturate-150 animate-drawer-slide-up">
          <TtsPlayer
            compact
            className="!h-10 !rounded-full !text-sm !font-semibold"
          />
          <PosterGenerator
            compact
            variant="mobile"
            className="!h-10 !rounded-full !text-sm !font-semibold"
          />
          </div>
        </div>
      )}
      {/* 底栏重排（移动端清理批，用户拍板）：确认并预约在左，取消方案在右，
          两者并排常驻——不再共用「+」抽屉承载取消。「Agent思考链路」触发
          按钮随三个遗留思考面板一起删除（单思考面已收口到「AI 幕后」信任
          带，这里不再需要入口）。 */}
      <div className="pointer-events-auto mx-auto flex max-w-[480px] items-center gap-2">
        {itinerary && !hasOrders && !cancelled && (
          <div
            className={cn(
              "flex min-h-11 min-w-0 flex-1 overflow-hidden rounded-full border border-[#e6bc00]/45 bg-[#FFD100] text-ink-900 shadow-[0_14px_34px_-24px_rgba(245,158,11,0.98)] transition",
              !canConfirm && "opacity-60",
            )}
          >
            <button
              type="button"
              className="min-w-0 flex-1 px-2 text-sm font-bold transition active:scale-[0.98] disabled:cursor-not-allowed disabled:text-ink-500"
              disabled={!canConfirm}
              onClick={handleConfirm}
              title={
                blockedByOwnerGuard
                  ? "只有房间发起人可以确认预约"
                  : "确认后 Agent 会做三件事：锁定餐厅时段、整理转发文案、把本次偏好写进长期记忆"
              }
            >
              <span className="truncate">{confirmLabel}</span>
            </button>
            {/* ⓘ「预约说明」只在能确认时给（房主 / 单人）。非房主等待态本就
                点不了确认，这个图标既无用、又和"等待发起人确认"文字挤重叠
                （真机 bug）——等待态直接不渲染它。 */}
            {!blockedByOwnerGuard && (
              <button
                type="button"
                className="grid h-11 w-11 shrink-0 place-items-center bg-transparent text-[#8f4b00] transition active:scale-95"
                onClick={() => setPreviewOpen((open) => !open)}
                aria-label={previewOpen ? "收起预约说明" : "查看预约说明"}
                aria-expanded={previewOpen}
                title="查看确认后会发生什么"
              >
                <Info className="h-5 w-5" strokeWidth={2.5} />
              </button>
            )}
          </div>
        )}
        {itinerary && !hasOrders && !cancelled && (
          <button
            type="button"
            className="min-h-11 min-w-0 flex-1 rounded-full border border-red-500/15 bg-white/[0.72] px-2 text-sm font-semibold text-red-500 shadow-[0_14px_34px_-26px_rgba(17,24,39,0.78)] backdrop-blur-2xl backdrop-saturate-150 transition active:scale-[0.98] disabled:text-ink-400"
            disabled={streaming}
            onClick={() => {
              setExpanded(false);
              setPreviewOpen(false);
              cancel();
            }}
          >
            取消方案
          </button>
        )}
        {/* "+"展开抽屉触发按钮——解连坐（UI 修复批 + 用户移动端纠正）：此前
            和上面的确认/取消按钮组共用同一个 `!hasOrders` 门控 fragment，
            确认后整个 fragment（含这个触发按钮）一起从 DOM 消失，导致抽屉
            里的海报/TTS 确认后再也打不开、够不着（诊断稿问题4同款"一个变量
            身兼两个不同粒度门控"病灶，移动端版本）。这里只解开触发按钮自己
            的门控（不再依赖 hasOrders）——抽屉触发按钮和海报/TTS 的可达性
            与「是否已下单」解绑，确认后仍可达；取消方案按钮本身仍按业务规则
            只在 !hasOrders 时渲染（不能取消一个已下单的方案）。 */}
        {itinerary && !cancelled && (
          <button
            type="button"
            className={cn(
              "grid h-11 w-11 shrink-0 place-items-center rounded-full border text-sm font-semibold shadow-[0_14px_34px_-26px_rgba(17,24,39,0.78)] backdrop-blur-2xl backdrop-saturate-150 transition active:scale-[0.98] disabled:text-ink-400",
              expanded
                ? "border-accent-600/45 bg-accent-500 text-white"
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
            // 去绿归色：起止点不是"成功"语义，本就该同 MobileWebBookend 的
            // 中性暖灰基调（该函数当前是未被调用的旧实现，颜色仍按去绿归色
            // 统一改，避免留一份调色板外的孤立配色）。
            tone === "start"
              ? "bg-ink-500 ring-1 ring-ink-500/18"
              : "bg-red-500 ring-1 ring-red-500/18",
          )}
        />
      </span>
      <span
        className={cn(
          "text-base font-semibold tracking-tight",
          tone === "start" ? "text-ink-700" : "text-red-600",
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
            "group/mobile-composer flex items-end gap-2 rounded-full border border-black/[0.08] px-4 py-2",
            "bg-white/[0.56] shadow-[0_20px_48px_-30px_rgba(17,24,39,0.72),inset_0_1px_0_rgba(255,255,255,0.78)]",
            "backdrop-blur-2xl backdrop-saturate-150 transition-all duration-300 ease-out",
            "hover:border-accent-400/50 hover:bg-white/[0.82] hover:shadow-[0_20px_50px_-30px_rgba(17,24,39,0.64),0_0_0_4px_rgba(245,158,11,0.08),inset_0_1px_0_rgba(255,255,255,0.92)]",
            "focus-within:border-accent-500/55 focus-within:bg-white/[0.90] focus-within:shadow-[0_22px_55px_-32px_rgba(17,24,39,0.68),0_0_0_4px_rgba(245,158,11,0.10),inset_0_1px_0_rgba(255,255,255,0.96)]",
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
              "mb-0.5 mr-[-0.25rem] grid h-10 w-10 shrink-0 place-items-center rounded-full border border-accent-600/40 bg-accent-500 p-0 text-white",
              "shadow-[0_10px_26px_-16px_rgba(245,158,11,0.95)] transition-all duration-300 ease-out",
              "group-hover/mobile-composer:scale-[1.02] group-hover/mobile-composer:bg-accent-400 group-hover/mobile-composer:shadow-[0_12px_28px_-15px_rgba(245,158,11,0.98)]",
              "group-focus-within/mobile-composer:scale-[1.02] group-focus-within/mobile-composer:bg-accent-400 group-focus-within/mobile-composer:shadow-[0_12px_28px_-15px_rgba(245,158,11,0.98)]",
              "active:scale-95 disabled:border-accent-500/[0.30] disabled:bg-accent-500/[0.44] disabled:text-ink-500 disabled:shadow-[inset_0_1px_0_rgba(255,255,255,0.72)]",
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
