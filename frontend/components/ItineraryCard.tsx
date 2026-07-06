"use client";

import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

import { Icons } from "@/lib/icon-map";
import { useCollabStore } from "@/lib/collab-store";
import { useChatStore } from "@/lib/store";
import { useConfirmAction } from "@/lib/hooks/useConfirmAction";
import { buildConfirmPreviewCopy } from "@/lib/confirm-preview";
import { buildIntentChips } from "@/lib/intent-chips";
import type {
  AgentNarrationMessage,
  AlternativeOption,
  HopMode,
  IntentExtraction,
  Itinerary,
  NodeChip,
  ScheduleEntry,
} from "@/lib/types";
import { cn } from "@/lib/utils";

import NumberTicker from "./NumberTicker";
import RefinementDialog from "./RefinementDialog";
import ShimmerStripe from "./ShimmerStripe";
import ComparisonView from "./ComparisonView";
import MapOverlay from "./MapOverlay";
import VoteButtons from "./VoteButtons";

/** 行程卡片：聚焦方案摘要、时间轴、地图、预订结果和主执行动作。 */
export default function ItineraryCard() {
  const itinerary = useChatStore((s) => s.itinerary);
  const intent = useChatStore((s) => s.intent);
  const narration = useChatStore((s) => s.narration);
  const narrationMessages = useChatStore((s) => s.narrationMessages);
  const memoryPersisted = useChatStore((s) => s.memoryPersisted);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);

  // ADR-0013 F-4：节点行调整入口（右侧具名备选 / 下方定向调整 chips）
  const nodeActions = useChatStore((s) => s.nodeActions);
  const lockedNodeId = useChatStore((s) => s.lockedNodeId);
  const sendAdjust = useChatStore((s) => s.sendAdjust);

  // 协作模式
  const collabMode = useCollabStore((s) => s.collabMode);
  const sendCollabAdjust = useCollabStore((s) => s.sendAdjust);
  const lastRefinement = useChatStore((s) => s.lastRefinement);
  const previousItinerary = useChatStore((s) => s.previousItinerary);
  const cancel = useChatStore((s) => s.cancel);

  const [refineOpen, setRefineOpen] = useState(false);

  // 聚光灯：itinerary 从 null/无 → 有时触发一次性脉冲
  // confirm 阶段是接续不重置，spotlight 不应触发（保留 demo UX 连续）
  const [spotlight, setSpotlight] = useState(false);
  const prevHadItinerary = useRef(false);
  const streamPhase = useChatStore((s) => s.streamPhase);
  useEffect(() => {
    const has = !!itinerary;
    if (has && !prevHadItinerary.current && streamPhase !== "confirm") {
      setSpotlight(true);
      const timer = setTimeout(() => setSpotlight(false), 2400);
      prevHadItinerary.current = true;
      return () => clearTimeout(timer);
    }
    if (!has) prevHadItinerary.current = false;
    if (has && !prevHadItinerary.current) prevHadItinerary.current = true;
  }, [itinerary, streamPhase]);

  // ============================================================
  // 时间轴 stagger 动画（R1）：schedule 逐条"长出来"
  //   - itinerary 从 null → 非 null：从 0 开始递增显示
  //   - visibleEntries.length >= 3：间隔 400ms；<= 2：间隔 200ms
  //   - 用户可点「跳过动画」立即显示全部
  //   - animating 期间禁用确认/反馈/取消按钮（防止半成品交互）
  //   - streaming 变 false 时强制兜底（防止 abort 卡住）
  //
  //   schedule 派生视图（edge_v1）已是 nodes+hops 排序展平结果，hidden=true
  //   的条目（in_place hop / virtual hop）由 visibleEntries 在源头过滤，
  //   下游所有 stagger 控制都基于 visibleEntries.length。
  //   兜底：schedule 为空（旧后端 / 异常）时按 nodes 派生（跳过 home）。
  // ============================================================
  const visibleEntries: ScheduleEntry[] = (() => {
    if (!itinerary) return [];
    const sched = itinerary.schedule || [];
    if (sched.length > 0) {
      return sched.filter((e) => !e.hidden);
    }
    // 降级：从 nodes 拼一个最小 schedule（跳过 home）
    return (itinerary.nodes || [])
      .filter((n) => n.target_kind !== "home")
      .map<ScheduleEntry>((n) => ({
        entry_kind: "node",
        ref_id: n.node_id,
        start: n.start_time,
        end: addMinutes(n.start_time, n.duration_min),
        title: n.title,
        minutes: n.duration_min,
        mode: null,
        hidden: false,
      }));
  })();

  const [visibleCount, setVisibleCount] = useState(0);
  const [animating, setAnimating] = useState(false);
  // R1：时间轴 stagger 动画期间也禁用确认按钮——用 extraGate 叠加到共享的
  // useConfirmAction 判定上（房主守卫 + collabMode 分流，见 A6 hook 抽取）。
  // 必须在任何 early return 之前调用（hooks 规则），故放在这里而不是渲染分支处。
  const { canConfirm, handleConfirm, confirmLabel, blockedByOwnerGuard } =
    useConfirmAction(!animating);
  const animTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 跨 itinerary 持久化的「已经跑过 stagger 的总段数」
  // 用途：confirm / refine 后 itinerary 整体替换（含 orders / share_message），但
  // 时间轴本身没变 → 不应再跑一遍 stagger。仅在「段数真的变化」时才重启动画。
  const lastAnimatedTotalRef = useRef<number>(0);

  useEffect(() => {
    if (!itinerary) {
      setVisibleCount(0);
      setAnimating(false);
      lastAnimatedTotalRef.current = 0;
      return;
    }
    const total = visibleEntries.length;
    if (total === 0) {
      setVisibleCount(0);
      setAnimating(false);
      lastAnimatedTotalRef.current = 0;
      return;
    }

    // 段数与上次跑过的 stagger 一致 → 不重启动画（confirm 阶段进来走这条）
    if (total === lastAnimatedTotalRef.current) {
      setVisibleCount(total);
      setAnimating(false);
      return;
    }

    // 重启动画：从 0 开始（首次或 refine 后段数变化）
    setAnimating(true);
    setVisibleCount(0);
    const delay = total <= 2 ? 200 : 400;
    let idx = 0;

    const tick = () => {
      idx += 1;
      setVisibleCount(idx);
      if (idx >= total) {
        setAnimating(false);
        animTimerRef.current = null;
        lastAnimatedTotalRef.current = total;
      } else {
        animTimerRef.current = setTimeout(tick, delay);
      }
    };
    animTimerRef.current = setTimeout(tick, delay);

    return () => {
      if (animTimerRef.current) {
        clearTimeout(animTimerRef.current);
        animTimerRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itinerary]);

  // 跳过动画：清 timer + 立即全显
  const skipAnimation = () => {
    if (animTimerRef.current) {
      clearTimeout(animTimerRef.current);
      animTimerRef.current = null;
    }
    setVisibleCount(visibleEntries.length);
    setAnimating(false);
  };

  // streaming 变 false 时兜底（abort 等异常场景下防止 animating 卡住）
  useEffect(() => {
    if (!streaming && animating) {
      // 给 React 一次重新调度机会：如果是正常完成会被 stagger 自然结束；
      // 如果是 abort，强制兜底
      const timer = setTimeout(() => {
        if (!animTimerRef.current && itinerary) {
          setVisibleCount(visibleEntries.length);
          setAnimating(false);
        }
      }, 100);
      return () => clearTimeout(timer);
    }
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, animating, itinerary]);

  if (!itinerary && !streaming) {
    return (
      <div className="card px-4 py-8 flex flex-col items-center gap-2.5 text-ink-500">
        <div className="w-10 h-10 rounded-full bg-gradient-to-br from-brand-500/15 to-accent-500/15 flex items-center justify-center border border-black/[0.08]">
          <Icons.pin className="w-4 h-4 text-brand-600" strokeWidth={1.5} />
        </div>
        <span className="text-sm text-ink-700">行程将在这里出现</span>
      </div>
    );
  }

  if (!itinerary) {
    return (
      <div className="card px-4 py-5 space-y-3">
        <div className="flex items-center gap-1.5 text-xs text-brand-600">
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
  // R1: animating 期间也禁用按钮（避免用户在动画进行中点确认）
  // canConfirm/handleConfirm 已由 useConfirmAction(!animating) 统一算好（见上）。
  const canAct = !streaming && !hasOrders && !cancelled && !animating;
  // ADR-0013 F-5：房间模式下节点调整走 WS "adjust"（RoomManager.adjust，归名+
  // 串行+锁定广播），单人模式维持原 HTTP `/chat/adjust` SSE（同 ChatDock 的
  // collabMode 分流先例）——两个调用点（具名备选/定向调整 chip）共用这一处分流。
  const dispatchAdjust = collabMode ? sendCollabAdjust : sendAdjust;

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-[30px] border border-black/[0.06] bg-white shadow-[0_28px_72px_-48px_rgba(17,24,39,0.68)] animate-fade-in",
        spotlight && "spotlight-once",
      )}
    >
      {/* streaming 时顶部流动黄光带 */}
      {streaming && (
        <div
          aria-hidden
          className="absolute top-0 left-0 right-0 h-px shimmer-bar z-10"
        />
      )}
      <div
        aria-hidden
        className="absolute right-8 top-0 h-9 w-16 -translate-y-2 rotate-6 rounded-b-xl bg-[#e8d7b5]/70 shadow-sm"
      />
      {/* Header */}
      <div className="relative px-6 pb-4 pt-5">
        <div className="flex items-start justify-between gap-5">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-2xl leading-none" aria-hidden>
                📷
              </span>
              <span className="text-xl font-black tracking-tight text-[#8f4b24]">
                今日行程安排
              </span>
            </div>
            <div className="mt-2 text-3xl font-black leading-tight tracking-tight text-ink-900">
              <HighlightSummary text={itinerary.summary} />
            </div>
          </div>
          <span className="shrink-0 rounded-full border border-white/[0.78] bg-white/75 px-3.5 py-1.5 text-sm font-bold text-[#8f4b24] shadow-sm backdrop-blur-xl">
            约{" "}
            <NumberTicker
              value={totalH}
              format={(v) => v.toFixed(1)}
              className="font-mono mx-0.5"
            />
            小时
          </span>
        </div>
      </div>

      {/* T7/R3: Refine 前后对比视图（仅在有 lastRefinement + previousItinerary 时显示） */}
      {lastRefinement && previousItinerary && itinerary && (
        <ComparisonView
          oldItinerary={previousItinerary}
          newItinerary={itinerary}
        />
      )}

      {/* Refinement summary banner */}
      {lastRefinement && lastRefinement.changedFields.length > 0 && (
        <div className="px-4 pt-3">
          <RefinementSummaryBanner
            fields={lastRefinement.changedFields}
            note={lastRefinement.refinerNote}
          />
        </div>
      )}

      {/* Agent 暖心开场白 + intent 命中可视化 */}
      {(narration?.text || intent) && (
        <div className="px-4 pt-3">
          <NarrationBlock
            text={narration?.text}
            stage={narration?.stage ?? "stream"}
            messages={narrationMessages}
            intent={intent}
          />
        </div>
      )}

      {/* spec algorithm-redesign R5：memory_writer 副作用结果（已记住此次场景偏好） */}
      {memoryPersisted?.success && (
        <div className="px-4 pt-2">
          <MemoryPersistedBadge
            socialContext={memoryPersisted.socialContext}
            summaryPreview={memoryPersisted.summaryPreview}
          />
        </div>
      )}

      {/* R1: 时间轴 stagger 动画期间显示跳过按钮 */}
      {animating && (
        <div className="px-4 pt-2 flex justify-end">
          <button
            type="button"
            onClick={skipAnimation}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-black/[0.08] bg-black/[0.02] hover:bg-black/[0.05] text-xs text-ink-500 hover:text-ink-800 transition-colors"
            title="跳过逐段动画，立即显示完整行程"
          >
            <span>跳过动画</span>
            <span aria-hidden>⏭</span>
          </button>
        </div>
      )}

      {/* Timeline */}
      <ol className="relative px-5 pb-5 pt-3 space-y-3.5">
        {/* 时间轴竖线 */}
        <div
          aria-hidden
          className="absolute left-[42px] top-[36px] bottom-[28px] w-[4px] -translate-x-1/2"
          style={{
            background:
              "linear-gradient(180deg, rgba(16,185,129,0.5) 0%, rgba(255,209,0,0.5) 30%, rgba(245,158,11,0.3) 70%, rgba(239,68,68,0.5) 100%)",
          }}
        />

        {/* 起点绿点：从家出发 */}
        <li className="flex items-center gap-3">
          <div className="flex flex-col items-center min-w-[52px] z-10">
            <div
              className="w-3.5 h-3.5 rounded-full ring-[3px] ring-white"
              style={{
                background: "linear-gradient(135deg, #10b981 0%, #059669 100%)",
                boxShadow: "0 0 0 1px rgba(16,185,129,0.3), 0 0 6px rgba(16,185,129,0.4)",
              }}
            />
          </div>
          <span className="text-base font-semibold text-emerald-600">出发咯 🚀</span>
        </li>

        {(() => {
          // ADR-0013 F-4：room.py 的 vote 协议 stage_index = "mid nodes 顺序"
          // （跳过首尾 home 之后的第几个节点，0-based，见 collab/room.py:850-881
          // `_get_stage_title` docstring）。schedule 派生视图里 home 节点的
          // entry 恒 hidden=True（assemble_blueprint.py::_derive_schedule），
          // 已被 visibleEntries 在源头过滤——故 entry_kind==="node" 的这些条目
          // 天然就是 mid nodes、且顺序一致，用一个跨 map 迭代的计数器即可还原
          // stage_index，不需要另建一套节点定位。
          let midNodeIndex = -1;
          return visibleEntries.map((entry, idx) => {
          // R1: stagger 控制——idx 超出 visibleCount 时不渲染
          if (idx >= visibleCount) return null;

          // 空档检测：与上一条 entry 的 [end, start] 之间若有 ≥ FREE_GAP_THRESHOLD_MIN
          // 分钟的真实空闲，插入一行「自由休息」。gap 用 (本条 start − 上一条 end)
          // 计算——通勤段自己的分钟数已经被通勤行自己的 [start, end] 吃掉，
          // 这里天然不会把通勤时间重复计入休息时长（不是 "下一站 start − 上一站 start"）。
          const prevEntry = idx > 0 ? visibleEntries[idx - 1] : null;
          const gapNode = renderFreeGap(prevEntry, entry, idx);

          // hop 行（细长条）：mode!=="virtual" 才渲染（virtual=in_place 已在
          // visibleEntries 过滤阶段被 hidden=true 屏蔽，此处再保险一道）
          if (entry.entry_kind === "hop") {
            if (!entry.mode || entry.mode === "virtual") return gapNode;
            return (
              <Fragment key={entry.ref_id || `hop-${idx}`}>
                {gapNode}
                <li className="relative flex items-center gap-3 animate-fade-in-up">
                  <div className="min-w-[44px]" aria-hidden />
                  <div
                    className={cn(
                      "flex-1 ml-2 rounded-full border border-[#eadfc9]/70 bg-white/75 px-3 py-1.5 shadow-sm",
                      "text-sm font-semibold text-ink-500 tracking-tight leading-tight",
                    )}
                    title={`${entry.start} → ${entry.end}`}
                  >
                    通勤 {entry.minutes} 分钟（{translateHopMode(entry.mode)}）
                  </div>
                </li>
              </Fragment>
            );
          }

          // node 行（与原 stage 渲染等价 + ADR-0013 F-4 节点行调整入口）
          midNodeIndex += 1;
          const stageIndex = midNodeIndex;
          const targetId = nodeTargetId(itinerary, entry.ref_id);
          const actions = targetId ? nodeActions?.[targetId] : undefined;
          const chips = actions?.chips ?? [];
          const alternatives = (actions?.alternatives ?? []).slice(0, 2);
          const isLocked = targetId != null && lockedNodeId === targetId;
          const canAdjust = targetId != null && !isLocked && lockedNodeId == null && !streaming;

          return (
            <Fragment key={entry.ref_id || `node-${idx}`}>
              {gapNode}
              <li className="relative flex items-start gap-4 animate-fade-in-up">
                {/* 左侧：时间 + 黄点（竖排，黄点居中） */}
                <div className="flex flex-col items-center min-w-[52px] z-10">
                  <div className="text-sm font-bold text-ink-800 mono">{entry.start}</div>
                  {/* 黄色时间点 */}
                  <div
                    className="my-1 w-3 h-3 rounded-full ring-[3px] ring-white"
                    style={{
                      background:
                        "linear-gradient(135deg, #FFD100 0%, #f59e0b 100%)",
                      boxShadow:
                        "0 0 0 1px rgba(0,0,0,0.1), 0 0 8px rgba(255,209,0,0.4)",
                    }}
                  />
                  <div className="text-sm font-semibold text-ink-600 mono">{entry.end}</div>
                </div>
                {/* 右侧内容：用 pt 让标题行对准黄点 */}
                <div
                  className="flex-1 min-w-0 rounded-[26px] border border-white/80 px-5 py-4 shadow-[0_18px_44px_-34px_rgba(17,24,39,0.58),inset_0_1px_0_rgba(255,255,255,0.94)] ring-1 ring-[#FFD100]/10"
                  style={{
                    background:
                      "linear-gradient(135deg, rgba(255,255,255,0.95) 0%, rgba(255,252,237,0.88) 42%, rgba(241,250,245,0.78) 72%, rgba(239,247,255,0.68) 100%)",
                  }}
                >
                  <div className="flex flex-wrap items-start justify-between gap-x-2 gap-y-1">
                    {/* 左：kind chip + 标题 + note */}
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
                      <span className="chip px-2.5 py-1 text-sm font-bold">
                        {nodeKindLabel(itinerary, entry.ref_id)}
                      </span>
                      <span
                        className="text-2xl font-black leading-snug tracking-tight text-ink-900 rounded-sm px-0.5"
                        style={{
                          textDecorationLine: "underline",
                          textDecorationStyle: "wavy",
                          textDecorationColor: "rgba(185, 130, 42, 0.36)",
                          textDecorationThickness: "1.5px",
                          textUnderlineOffset: "7px",
                        }}
                      >
                        {entry.title}
                      </span>
                      {(() => {
                        const note = nodeNote(itinerary, entry.ref_id);
                        return note ? (
                          <span className="ml-2 text-base leading-relaxed text-ink-700">
                            {note}
                          </span>
                        ) : null;
                      })()}
                    </div>

                    {/* 右：具名备选（ADR-0013 决策 4「右侧=具名备选」，≤2 个） */}
                    {alternatives.length > 0 && targetId && (
                      <div className="flex flex-wrap items-center gap-1.5 shrink-0">
                        {alternatives.map((alt) => (
                          <AlternativeButton
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
                  </div>

                  {/* 下方：定向调整 chips + 赞/踩并排（ADR-0013 决策 4「下方=定向
                      调整按钮 + 赞踩并排」；VoteButtons 自身按 collabMode 隐藏，
                      故这一行即便没有 chips 也要渲染，让协作模式下赞踩仍可见） */}
                  {targetId && (
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {chips.map((chip) => (
                        <AdjustChipButton
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
                      <VoteButtons stageIndex={stageIndex} />
                    </div>
                  )}

                  {/* 换菜进行中：整行 Shimmer（ADR-0013 决策 6 锁定态视觉语言复用） */}
                  {isLocked && <ShimmerStripe rows={1} className="mt-2" />}
                </div>
              </li>
            </Fragment>
          );
          });
        })()}

        {/* 终点红点：结束行程 */}
        <li className="flex items-center gap-3">
          <div className="flex flex-col items-center min-w-[52px] z-10">
            <div
              className="w-3.5 h-3.5 rounded-full ring-[3px] ring-white"
              style={{
                background: "linear-gradient(135deg, #ef4444 0%, #dc2626 100%)",
                boxShadow: "0 0 0 1px rgba(239,68,68,0.3), 0 0 6px rgba(239,68,68,0.4)",
              }}
            />
          </div>
          <span className="text-base font-semibold text-red-600">满载而归 ✨</span>
        </li>
      </ol>

      {/* T8/R2: 高德地图标注（配合 R1 stagger 逐段亮起） */}
      <div className="px-4 pb-3">
        <MapOverlay visibleCount={visibleCount} />
      </div>

      {/* 已为你预留：暗色 emerald 玻璃 */}
      {hasOrders && (
        <div className="px-4 pb-3">
          <div className="section-title mb-1.5">已为你预留</div>
          <ul className="space-y-1.5">
            {itinerary.orders.map((o) => (
              <li
                key={o.order_id}
                className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-700 animate-fade-in-up backdrop-blur-sm"
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
                  <span className="text-xs text-emerald-600/80 mono shrink-0">
                    {o.kind}
                  </span>
                </div>
                <div className="mt-1 text-emerald-600/90 ml-5">
                  {o.detail}
                  <span className="text-emerald-500/70 mx-1.5">·</span>
                  <span className="mono text-xs">{o.order_id}</span>
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

      {/* M4：方案就绪后预告「点确认会发生什么」——把评委永远看不到的「确认后一键顺滑执行」
            从「需点击触发」变成「默认可见」。下单前显示，下单后由「已为你预留」订单卡接力 */}
      {!hasOrders && !cancelled && (
        <div className="px-4 pb-3">
          <ConfirmPreviewCard intent={intent} itinerary={itinerary} />
        </div>
      )}

      {/* Action buttons */}
      <div className="px-4 pb-4">
        {!hasOrders && !cancelled && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <button
              className={cn("btn-primary font-bold", streaming && "shimmer-border")}
              disabled={!canConfirm}
              onClick={handleConfirm}
              title={
                blockedByOwnerGuard
                  ? "只有房间发起人可以确认预约"
                  : "确认后 Agent 会做三件事：锁定餐厅时段、整理转发文案、把本次偏好写进长期记忆"
              }
            >
              {streaming ? (
                <>
                  <Icons.thinking className="w-3.5 h-3.5 animate-spin" />
                  <span>{confirmLabel}</span>
                </>
              ) : (
                <>
                  <Icons.success className="w-3.5 h-3.5" strokeWidth={2.25} />
                  <span>{confirmLabel}</span>
                </>
              )}
            </button>
            <button
              className="btn-secondary disabled:opacity-50 disabled:cursor-not-allowed"
              disabled={!canAct}
              onClick={() => setRefineOpen(true)}
              title="基于你的反馈调整原方案，不会从零重新规划"
            >
              <Icons.refine className="w-3.5 h-3.5" strokeWidth={2} />
              <span>说说哪不对</span>
            </button>
            <button
              className="btn-danger-ghost disabled:opacity-50 disabled:cursor-not-allowed"
              disabled={!canAct}
              onClick={cancel}
              title="放弃当前方案 · 不写入长期记忆"
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
      className="rounded-md border border-accent-500/30 px-3 py-2 text-xs text-accent-700 animate-fade-in backdrop-blur-sm"
      style={{
        background:
          "linear-gradient(135deg, rgba(255,209,0,0.10) 0%, rgba(245,158,11,0.06) 100%)",
      }}
    >
      <div className="flex items-center gap-1.5 mb-1 font-medium text-accent-700">
        <Icons.refine className="w-3.5 h-3.5" strokeWidth={2} />
        <span>已根据反馈调整</span>
      </div>
      <ul className="space-y-0.5 text-accent-800 ml-5 list-disc list-outside">
        {fields.map((f, i) => (
          <li key={i}>{f}</li>
        ))}
      </ul>
      {note && <div className="mt-1 ml-5 text-accent-600/80">{note}</div>}
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
      className="rounded-md border border-black/[0.08] p-3 backdrop-blur-sm"
      style={{
        background:
          "linear-gradient(135deg, rgba(255,209,0,0.06) 0%, rgba(245,158,11,0.04) 100%)",
      }}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          <Icons.share
            className="w-3.5 h-3.5 text-brand-600"
            strokeWidth={2}
          />
          <span className="text-xs font-medium text-ink-800 tracking-tight">
            转发文案
          </span>
        </div>
        <button
          onClick={copy}
          className={cn(
            "inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded transition-colors",
            copied
              ? "bg-emerald-500 text-white"
              : "bg-black/[0.04] text-ink-700 border border-black/[0.08] hover:bg-black/[0.06] hover:text-ink-900",
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
      <div className="text-sm leading-relaxed text-ink-800 whitespace-pre-wrap tracking-tight">
        {text}
      </div>
    </div>
  );
}


// ============================================================
// NarrationBlock —— Agent 暖心开场白（导游口播）
// ============================================================

function NarrationBlock({
  text,
  stage,
  messages,
  intent,
}: {
  text?: string;
  stage: "stream" | "confirm";
  /** D-7：narrate 文字里被限额折叠的完整取舍列表（"还有 N 处小取舍"的
   * "点开看全部"落点）。非空时在暖场文案下方挂一个可展开小节；null/[] 不渲染。 */
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
      className="relative overflow-hidden rounded-[18px] px-4 py-3.5 text-base leading-relaxed tracking-tight animate-fade-in backdrop-blur-sm border"
      style={{
        background: isConfirm
          ? "linear-gradient(135deg, rgba(16,185,129,0.08) 0%, rgba(255,209,0,0.04) 100%)"
          : "linear-gradient(135deg, rgba(255,209,0,0.08) 0%, rgba(245,158,11,0.04) 100%)",
        borderColor: isConfirm
          ? "rgba(16,185,129,0.24)"
          : "rgba(255,209,0,0.22)",
        color: "rgb(31 41 55 / 0.92)",
      }}
    >
      {text && (
        <div className="flex items-start gap-2">
          <Icons.spark
            className={cn(
              "w-4 h-4 mt-1 shrink-0",
              isConfirm ? "text-emerald-400" : "text-brand-600",
            )}
            strokeWidth={2}
          />
          <p className="whitespace-pre-wrap text-xl leading-relaxed">
            <HighlightText text={text} />
          </p>
        </div>
      )}

      {chips.length > 0 && (
        <div className={cn("flex flex-wrap items-center gap-2", text && "mt-3 border-t border-[#FFD100]/25 pt-3")}>
          <div className="mr-1 inline-flex items-center gap-1.5 text-base font-semibold text-ink-500">
            <Icons.spark className="w-4 h-4 text-brand-600" strokeWidth={2.5} />
            <span>为你考虑了</span>
          </div>
          {chips.map((c, i) => {
            const Ico = Icons[c.icon];
            return (
              <span
                key={`${c.label}-${i}`}
                className="inline-flex items-center gap-1.5 rounded-full border border-[#FFD100]/35 bg-[#FFD100]/[0.09] px-3 py-1.5 text-base font-semibold tracking-tight text-amber-800 animate-fade-in"
              >
                <Ico className="w-4 h-4" strokeWidth={2} />
                {c.label}
              </span>
            );
          })}
        </div>
      )}

      {/* D-7：全部取舍说明——narrate 文字里折叠的"还有 N 处小取舍"在这里展开看全部 */}
      {hasMessages && (
        <div className={cn("ml-[26px]", text || chips.length > 0 ? "mt-2" : "")}>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className={cn(
              "inline-flex items-center gap-1 text-sm font-medium transition-colors",
              isConfirm ? "text-emerald-600 hover:text-emerald-700" : "text-brand-600 hover:text-brand-700",
            )}
            aria-expanded={expanded}
          >
            <span>{expanded ? "收起取舍说明" : `查看全部取舍说明（${messages!.length}）`}</span>
            <ChevronDown
              className={cn("w-3 h-3 transition-transform duration-200", !expanded && "-rotate-90")}
              strokeWidth={2.5}
            />
          </button>
          {expanded && (
            <ul className="mt-1.5 space-y-1 list-disc list-outside ml-4 animate-collapse-in">
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
// ConfirmPreviewCard —— spec interaction-experience-review M4
// 「点确认后会发生什么」预告，让评委不点确认也能看到一键执行能力。
// ============================================================

function ConfirmPreviewCard({
  intent,
  itinerary,
}: {
  intent: IntentExtraction | null;
  itinerary: Itinerary;
}) {
  // 文案派生逻辑抽到 lib/confirm-preview.ts（B8：移动端 MobileConfirmPreview
  // 复用同一份"多顿饭不能只提第一家 / 人均桌位数 / 加购服务截断"判定）。
  const { restaurantLine, extraLine, memoryLine, extraServices } =
    buildConfirmPreviewCopy(intent, itinerary);

  return (
    <div
      className="rounded-md border border-amber-400/20 bg-amber-500/5 px-3.5 py-3 text-xs leading-relaxed"
    >
      <div className="flex items-center gap-1.5 mb-2">
        <Icons.spark
          className="w-3.5 h-3.5 text-amber-500"
          strokeWidth={2}
        />
        <span className="text-sm font-semibold text-amber-700 tracking-tight">
          点击「确认并预约」之后
        </span>
      </div>

      <p className="text-sm text-amber-900/85 mb-2.5">
        {restaurantLine}{extraLine}；再为你备好一段可一键复制的转发文案；最后{memoryLine}。
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
// MemoryPersistedBadge —— spec algorithm-redesign R5 收尾：
// 让评委一眼看到「Agent 已把这次行程写回用户画像，下次同场景会复用」
// ============================================================

function MemoryPersistedBadge({
  socialContext,
  summaryPreview,
}: {
  socialContext: string;
  summaryPreview: string;
}) {
  return (
    <div
      className="rounded-md border border-emerald-500/24 bg-emerald-500/6 px-3 py-2 text-xs text-emerald-700/95 animate-fade-in backdrop-blur-sm flex items-start gap-2"
      style={{
        background:
          "linear-gradient(135deg, rgba(16,185,129,0.08) 0%, rgba(16,185,129,0.04) 100%)",
      }}
    >
      <Icons.spark
        className="w-3.5 h-3.5 mt-0.5 shrink-0 text-emerald-400"
        strokeWidth={2}
      />
      <div className="flex-1 min-w-0">
        <div className="font-medium text-emerald-600 tracking-tight">
          已写入「{socialContext || "本"}」场景的跨 session 召回库
        </div>
        <div className="text-emerald-700/75 text-xs mt-0.5 line-clamp-1">
          {summaryPreview}
          <span className="text-emerald-500/60 ml-1">·</span>
          <span className="text-emerald-500/60 ml-1">
            user_profile.json 自然语言记忆，与「偏好画像」的 tag 统计互补
          </span>
        </div>
      </div>
    </div>
  );
}


// ============================================================
// edge_v1 schedule 渲染辅助函数
// ============================================================

/** "HH:MM" + minutes → "HH:MM"（只用于 schedule 为空、用 nodes 兜底时拼 end）。 */
function addMinutes(start: string, minutes: number): string {
  const m = /^(\d{1,2}):(\d{2})$/.exec(start);
  if (!m) return start;
  const h = Number(m[1]);
  const mm = Number(m[2]);
  if (Number.isNaN(h) || Number.isNaN(mm)) return start;
  const total = h * 60 + mm + (minutes || 0);
  const wrap = ((total % (24 * 60)) + 24 * 60) % (24 * 60);
  const oh = Math.floor(wrap / 60);
  const om = wrap % 60;
  return `${String(oh).padStart(2, "0")}:${String(om).padStart(2, "0")}`;
}

/** "HH:MM" → 当日分钟数（非法格式返回 NaN，调用方按"不足阈值"处理）。 */
function parseHHMM(t: string): number {
  const m = /^(\d{1,2}):(\d{2})$/.exec(t);
  if (!m) return NaN;
  return Number(m[1]) * 60 + Number(m[2]);
}

/**
 * 空档阈值：assemble_blueprint.py 里非首跳 hop 固定留 5 分钟 buffer_min（结构性
 * 过渡，不算"等待"）；真正的等待（如 not_before_start 把餐厅节点开始时刻顶到
 * 预约时刻）通常是十几到几十分钟。阈值取 10 分钟——是结构性 buffer 的 2 倍，
 * 保证不会给每一段正常通勤都点亮一行「自由休息」，只标出确有实质等待的间隙。
 */
const FREE_GAP_THRESHOLD_MIN = 10;

/**
 * 计算 prev.end → curr.start 之间的空档并渲染「自由休息」行（不足阈值/无上一条时
 * 返回 null）。
 *
 * 口径：gap = 本条 entry.start − 上一条 entry.end。因为 schedule 里通勤本身是独立
 * 的 hop entry（有自己的 [start, end]），这里的「上一条」既可能是 node 也可能是
 * hop —— gap 算的是"上一条结束"到"这一条开始"之间没有被任何 entry 占用的时间，
 * 通勤分钟数已经被通勤行自己的区间吃掉，不会被二次计入「休息」时长。
 */
function renderFreeGap(
  prev: ScheduleEntry | null,
  curr: ScheduleEntry,
  idx: number,
): ReactNode {
  if (!prev) return null;
  const gap = parseHHMM(curr.start) - parseHHMM(prev.end);
  if (!Number.isFinite(gap) || gap < FREE_GAP_THRESHOLD_MIN) return null;
  return (
    <li
      key={`gap-${idx}`}
      className="relative flex items-center gap-3 animate-fade-in-up"
    >
      <div className="min-w-[44px]" aria-hidden />
      <div
        className={cn(
          "flex-1 ml-2 px-3 py-1 border-l-2 border-black/[0.06]",
          "text-sm text-ink-500 tracking-tight leading-tight",
        )}
        title={`${prev.end} → ${curr.start}`}
      >
        自由休息 · {gap} 分钟
      </div>
    </li>
  );
}

/** Hop mode 中文化展示。 */
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

/** node ref_id → ActivityNode.kind（用于 schedule 行展示左侧 chip）。 */
function nodeKindLabel(itinerary: Itinerary, ref_id: string): string {
  const n = itinerary.nodes?.find((x) => x.node_id === ref_id);
  return n?.kind ?? "活动";
}

/** node ref_id → ActivityNode.note（schedule 没存 note，从 nodes 反查）。 */
function nodeNote(itinerary: Itinerary, ref_id: string): string | null {
  const n = itinerary.nodes?.find((x) => x.node_id === ref_id);
  return n?.note ?? null;
}

/**
 * node ref_id（ActivityNode.node_id，如 "n_1"）→ ActivityNode.target_id（POI/
 * Restaurant 实体 id）。ADR-0013 的 node_actions/NodeChip/AlternativeOption
 * 全部按 target_id 分组/寻址（同 resolve_node_swap(target_node_id=...) 口径，
 * 见 schemas/node_chip.py 模块 docstring「为什么是 target_id 不是 node_id」），
 * 与 ScheduleEntry.ref_id 是两套不同的定位轴，这里做一次反查桥接。
 */
function nodeTargetId(itinerary: Itinerary, ref_id: string): string | null {
  const n = itinerary.nodes?.find((x) => x.node_id === ref_id);
  return n?.target_id ?? null;
}

// ============================================================
// ADR-0013 F-4：节点行调整入口——具名备选按钮 / 定向调整 chip
// 药丸视觉语言沿用 intent chips（同一套 pill 配色，见本文件 NarrationBlock）。
// ============================================================

function AlternativeButton({
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
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-sm font-medium tracking-tight border transition-colors",
        "disabled:opacity-40 disabled:cursor-not-allowed",
        "hover:bg-[#FFD100]/16",
      )}
      style={{
        background: "rgba(255, 209, 0, 0.08)",
        borderColor: "rgba(255, 209, 0, 0.22)",
        color: "rgb(146 64 14)",
      }}
    >
      <Icons.spark className="w-3 h-3" strokeWidth={2} />
      <span className="max-w-[9rem] truncate">换成{alt.name}</span>
    </button>
  );
}

function AdjustChipButton({
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
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-sm font-medium tracking-tight border transition-colors",
        "disabled:opacity-40 disabled:cursor-not-allowed",
        "bg-black/[0.03] border-black/[0.08] text-ink-700 hover:bg-black/[0.06] hover:text-ink-900",
      )}
    >
      {chip.label}
    </button>
  );
}


// ============================================================
// HighlightText —— 自动识别关键信息并高亮
// 规则：
//   - 时间（HH:MM 格式）：加粗 + 品牌色
//   - 数字+单位（如 5.7小时、196分钟、3km）：加粗
//   - 地点名（被「」或引号包裹的内容）：黄色背景高亮
//   - 人物关系词（老婆/孩子/宝贝/爸妈/闺蜜/朋友等）：下划线强调
// ============================================================

function HighlightText({ text }: { text: string }) {
  // 正则匹配各类关键信息
  const pattern =
    /(\d{1,2}:\d{2})|(\d+\.?\d*\s*(?:小时|分钟|km|公里|人|岁|h))|([「」""][^「」""]+[「」""])|(\b(?:老婆|老公|孩子|宝贝|宝宝|爸妈|父母|闺蜜|朋友|同事|爱人|妻子|丈夫|女儿|儿子|妈妈|爸爸|奶奶|爷爷|外婆|外公)\b)/g;

  const parts: Array<{ text: string; type: "plain" | "time" | "number" | "place" | "person" }> = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    // 前面的普通文本
    if (match.index > lastIndex) {
      parts.push({ text: text.slice(lastIndex, match.index), type: "plain" });
    }
    // 判断匹配类型
    if (match[1]) {
      parts.push({ text: match[0], type: "time" });
    } else if (match[2]) {
      parts.push({ text: match[0], type: "number" });
    } else if (match[3]) {
      parts.push({ text: match[0], type: "place" });
    } else if (match[4]) {
      parts.push({ text: match[0], type: "person" });
    }
    lastIndex = match.index + match[0].length;
  }
  // 剩余文本
  if (lastIndex < text.length) {
    parts.push({ text: text.slice(lastIndex), type: "plain" });
  }

  // 没有匹配到任何关键词，直接返回原文
  if (parts.length === 0) return <>{text}</>;

  return (
    <>
      {parts.map((part, i) => {
        switch (part.type) {
          case "time":
            return (
              <span key={i} className="font-bold text-brand-700 mono">
                {part.text}
              </span>
            );
          case "number":
            return (
              <span key={i} className="font-bold text-ink-900">
                {part.text}
              </span>
            );
          case "place":
            return (
              <span
                key={i}
                className="font-semibold bg-[#FFD100]/20 px-0.5 rounded"
              >
                {part.text}
              </span>
            );
          case "person":
            return (
              <span
                key={i}
                className="font-semibold underline decoration-[#FFD100] decoration-2 underline-offset-2"
              >
                {part.text}
              </span>
            );
          default:
            return <span key={i}>{part.text}</span>;
        }
      })}
    </>
  );
}

// ============================================================
// HighlightSummary —— 标题行专用高亮
// 规则：
//   - 主要地点名（→ 或 · 分隔的核心实体）：黄色背景高亮
//   - 括号内容（大型主题/健康简餐等）：灰色次要
//   - 「备选 POI：」后面的内容：缩小 + 灰色（次要信息）
// ============================================================

function HighlightSummary({ text }: { text: string }) {
  // 小红书风格一句话标题（无 ·/→ 分隔符、无括号、无「备选 POI」）→ 整句普通渲染。
  // 这类自由口语句（可能含 emoji、+、｜等）若按旧分词逻辑会被整段套上黄色高亮背景，
  // 显得别扭；直接整句朴素显示即可（标题样式由外层 text-2xl font-semibold 给）。
  const hasSeparators = /[→·]|[（(]|备选\s*POI/.test(text);
  if (!hasSeparators) {
    return <>{text}</>;
  }

  // 先拆分「备选 POI」部分（如果有的话，作为次要信息缩小显示）
  const poiSplit = text.split(/[;；]\s*备选\s*POI[：:]/);
  const mainPart = poiSplit[0] || text;
  const poiPart = poiSplit[1] || null;

  // 过滤掉"约X小时"这类括号内容
  const filteredMainPart = mainPart.replace(/[（(]约\s*\d+\.?\d*\s*小时[）)]/g, "").trim();

  // 主体部分：高亮地点名（中文名词，排除连接符号和括号内容）
  // 匹配模式：括号内容变灰，→·等分隔符保留，其余为地点名加亮
  const pattern = /([（(][^）)]+[）)])|([→·])/g;
  const parts: Array<{ text: string; type: "name" | "bracket" | "sep" }> = [];
  let lastIdx = 0;
  let m: RegExpExecArray | null;

  while ((m = pattern.exec(filteredMainPart)) !== null) {
    if (m.index > lastIdx) {
      parts.push({ text: filteredMainPart.slice(lastIdx, m.index), type: "name" });
    }
    if (m[1]) {
      parts.push({ text: m[0], type: "bracket" });
    } else if (m[2]) {
      parts.push({ text: m[0], type: "sep" });
    }
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < filteredMainPart.length) {
    parts.push({ text: filteredMainPart.slice(lastIdx), type: "name" });
  }

  return (
    <>
      {parts.map((p, i) => {
        switch (p.type) {
          case "name":
            // 去掉前后空白后判断是否是实际地点名（长度>1才高亮）
            return p.text.trim().length > 1 ? (
              <span key={i} className="bg-[#FFD100]/20 px-0.5 rounded">
                {p.text}
              </span>
            ) : (
              <span key={i}>{p.text}</span>
            );
          case "bracket":
            return (
              <span key={i} className="text-ink-500 font-normal text-[13px]">
                {p.text}
              </span>
            );
          case "sep":
            return (
              <span key={i} className="text-ink-400 mx-0.5">
                {p.text}
              </span>
            );
          default:
            return <span key={i}>{p.text}</span>;
        }
      })}
      {poiPart && (
        <span className="block mt-0.5 text-[12px] font-normal text-ink-500">
          备选：{poiPart.trim()}
        </span>
      )}
    </>
  );
}
