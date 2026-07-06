"use client";

import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { ArrowLeftRight, ChevronDown, SlidersHorizontal } from "lucide-react";

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
import TrustBelt from "./TrustBelt";
import VoteButtons from "./VoteButtons";

/**
 * 时间轴精修（路演PPT/时间轴精修设计终稿.md §三.1「对齐」）：圆点/连接线要垂直
 * 对齐节点卡的标题行（玻璃标签+店名那一行），而不是整张卡的几何中心——卡片高度
 * 会因操作行/换菜态变化，只有标题行的位置稳定。
 *
 * 推算（近似值，非真机像素测量）：marginTop = 卡片顶部内边距（.node-card 的
 * pt-3.5 = 14px）+ 标题行自身高度的一半（标题行取 label/店名两者较高的一个——
 * 店名 text-[15px] leading-snug ≈ 20.6px 行高，取半 ≈ 10.3px）− 圆点自身半径
 * （9px 圆点，半径 4.5px，因为 marginTop 定的是 marker 容器顶边、marker 容器
 * 里第一个"占文档流"的元素就是圆点本身，时间标签靠 .timeline-time 绝对定位、
 * 不占流）。14 + 10.3 − 4.5 ≈ 19.8px，取 20px。真机字体渲染/行高会有出入，
 * 需要真机核验微调（本批未做真机截图验证，见交付报告「未真机验证项」）。
 */
const TIMELINE_DOT_OFFSET = "1.25rem"; // 20px

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
      <div className="space-y-3">
        {/* 信任带：规划中就该看到"它在想什么"，不必等方案落地——§八布局把
            三面板合成的这条思考流放在"叙事和时间轴之间"，而这里正是还没有
            "叙事"内容的规划中间态，信任带独自撑起这段等待时间的可见性。 */}
        <TrustBelt />
        <div className="card px-4 py-5 space-y-3">
          <div className="flex items-center gap-1.5 text-xs text-accent-600">
            <Icons.thinking
              className="w-3.5 h-3.5 animate-spin"
              strokeWidth={2}
            />
            <span className="tracking-tight">正在为你拼装行程...</span>
          </div>
          <ShimmerStripe rows={4} />
        </div>
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

      {/* 信任带（AI 思考流）：三技术面板（ToolTracePanel/ThoughtPanel/
          DecisionTraceCard）合成后的唯一落点——§八布局"叙事和时间轴之间"，
          绝不进时间轴与地图之间。恒定 3 行高，出稿后仍留在这里（冻结展示
          自愈高潮 + 定稿），不随方案落地而消失。 */}
      <div className="px-4 pt-3">
        <TrustBelt />
      </div>

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

      {/* Timeline：脊柱不再是一整条 absolute 渐变线（时间轴精修终稿§一「克制」要
          杀掉的彩虹渐变），改成每行自带一段暖灰脊柱（.timeline-spine-seg），行与
          行之间用 bottom:-14px 桥接 <ol> 的 space-y-3.5 间隙——这样才能让「停留」
          （实线）和「通勤」（虚线，见下方 hop 分支）分段染色，也让 §三.3 悬停协同
          能只点亮"这一段"而不是整条线。 */}
      <ol className="relative px-5 pb-5 pt-3 space-y-3.5">
        {/* 起点：从家出发（§六「家」bookend——无主活动标签/无操作层，更扁更淡；
            §一「首尾'家'=小 home 图标点，中性灰，不用 🚀/✨」——圆点本身换成
            home 图标点，右侧文案 pill 沿用既有实现，未采用设计稿示例文案，见
            交付报告说明） */}
        <li className="relative flex items-center gap-0">
          {/* self-stretch：覆盖 li 的 items-center，让这一列撑满整行高度，
              脊柱段（absolute top:0/bottom:-14px）才能按"这一行的真实高度"
              桥接到下一行，而不是只按 home 点自己的小高度算；justify-center
              把点重新在撑满后的列里垂直居中，视觉效果和之前一致。 */}
          <div className="relative flex flex-col items-center justify-center min-w-[52px] shrink-0 self-stretch">
            <div aria-hidden className="timeline-spine-seg" />
            <div className="timeline-dot-home relative z-10">
              <Icons.home className="w-2.5 h-2.5 text-ink-400" strokeWidth={2} />
            </div>
          </div>
          <div className="w-4 shrink-0" aria-hidden />
          <div className="inline-flex items-center gap-1.5 rounded-xl border border-black/[0.05] bg-black/[0.02] px-3 py-1.5 text-sm font-medium text-ink-500">
            <Icons.home className="w-3.5 h-3.5 text-ink-400" strokeWidth={2} />
            <span>出发咯</span>
          </div>
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

          // hop 行：时间轴精修终稿§二「通勤挪到脊柱上」——不再是一张独立卡
          // （旧版 rounded-full pill），改成脊柱在这一段变虚线 + 旁边极小灰字。
          // mode!=="virtual" 才渲染（virtual=in_place 已在 visibleEntries 过滤
          // 阶段被 hidden=true 屏蔽，此处再保险一道）
          if (entry.entry_kind === "hop") {
            if (!entry.mode || entry.mode === "virtual") return gapNode;
            return (
              <Fragment key={entry.ref_id || `hop-${idx}`}>
                {gapNode}
                <li
                  className="relative flex items-center gap-0 animate-fade-in-up"
                  title={`${entry.start} → ${entry.end}`}
                >
                  <div className="relative flex flex-col items-center min-w-[52px] shrink-0 self-stretch">
                    <div aria-hidden className="timeline-spine-seg timeline-spine-seg--hop" />
                  </div>
                  <div className="w-4 shrink-0" aria-hidden />
                  <div className="flex-1 min-w-0 py-1.5 text-xs text-ink-400 tracking-tight">
                    <span aria-hidden>{hopIcon(entry.mode)}</span>{" "}
                    {entry.minutes} 分钟
                    {entry.mode === "haversine_estimated" && (
                      <span className="ml-1 text-ink-300">
                        · {translateHopMode(entry.mode)}
                      </span>
                    )}
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
          // 卡片精修终稿§二两行制：内容行 = 玻璃标签 + 店名（唯一视觉焦点）；
          // 时间已被时间轴精修终稿§二挪到脊柱上（不再在卡里重复），note 降级为
          // 店名后缀，随店名一起 truncate（长名 + title 兜底全文，见卡片终稿§七.1）。
          const note = nodeNote(itinerary, entry.ref_id);
          const fullTitle = note ? `${entry.title} · ${note}` : entry.title;

          return (
            <Fragment key={entry.ref_id || `node-${idx}`}>
              {gapNode}
              {/* timeline-row：hover 协同的作用域根（§三.3）——CSS :has() 在这一层
                  判定"卡片被 hover"或"点/时间被 hover"，零 JS 状态（见 globals.css
                  .timeline-row:has(...) 规则），双向、200ms ease、只动
                  transform/opacity/color。 */}
              <li className="relative flex items-start gap-0 animate-fade-in-up timeline-row">
                {/* 左列：脊柱段（桥接到下一行，停留=实线）+ 时间/圆点。
                    §三.1「对齐」：圆点用 TIMELINE_DOT_OFFSET 定位到卡片标题行的
                    高度，时间标签绝对定位在点的上/下方，不占流内空间——这样时间
                    的行高不会把点往下推。self-stretch：li 是 items-start，这一列
                    默认只有 marker 自身高度（远矮于右侧卡片）；撑满整行高度后，
                    脊柱段的 bottom:-14px 才是按"这一行的真实高度"（=卡片高度）
                    去桥接下一行，视觉上才真的是"实线段=停留，和卡片一样高"
                    （终稿§二 ASCII 图示的意思）。marker 自身仍用 marginTop 定位，
                    不受列高度变化影响。 */}
                <div className="relative flex flex-col items-center min-w-[52px] shrink-0 self-stretch">
                  <div aria-hidden className="timeline-spine-seg" />
                  <div
                    className="relative z-10 timeline-marker"
                    style={{ marginTop: TIMELINE_DOT_OFFSET }}
                  >
                    <span className="timeline-time timeline-time--start text-[13px] font-medium tabular-nums text-ink-500">
                      {entry.start}
                    </span>
                    <div
                      className={cn("timeline-dot", isLocked && "timeline-dot--current")}
                      title={isLocked ? "换菜中" : undefined}
                    />
                    <span className="timeline-time timeline-time--end text-[13px] font-medium tabular-nums text-ink-500">
                      {entry.end}
                    </span>
                  </div>
                </div>
                {/* 连接线：点到卡片左缘（§三.2），与点共享同一 marginTop 保证
                    落在同一水平线上；hover 时提亮（globals.css）。 */}
                <div className="w-4 shrink-0" aria-hidden>
                  <div
                    className="timeline-connector"
                    style={{ marginTop: TIMELINE_DOT_OFFSET }}
                  />
                </div>
                {/* 右侧内容：节点卡（路演PPT/卡片精修设计终稿.md §二/§四/§八——两行制 +
                    暖色化用渐变描边/柔光（.node-card，见 globals.css）+ 和信任带同族的
                    16px 圆角/字色阶） */}
                <div className="node-card relative flex-1 min-w-0 px-4 pt-3.5 pb-2.5">
                  {/* 内容行：玻璃标签 + 店名（时间已挪到脊柱上，§二——不再在卡里
                      重复渲染 start–end/分钟，消除"卡片精修批"引入的时间两处重复）。 */}
                  <div className="flex items-center gap-x-2">
                    <span className="node-glass-label shrink-0 px-2 py-[3px] text-[11px] font-semibold tracking-[0.05em] text-amber-700">
                      {nodeKindLabel(itinerary, entry.ref_id)}
                    </span>
                    {isLocked ? (
                      <span
                        className="h-4 w-32 shrink-0 rounded shimmer-skeleton"
                        aria-hidden
                      />
                    ) : (
                      <span
                        className="min-w-0 flex-1 truncate text-[15px] font-semibold leading-snug text-ink-900"
                        title={fullTitle}
                      >
                        {entry.title}
                        {note && (
                          <span className="ml-1.5 font-normal text-ink-500">
                            · {note}
                          </span>
                        )}
                      </span>
                    )}
                  </div>

                  {/* 渐隐分隔线（§四第 4 条：两侧 mask-image 淡出，比实线更精致） */}
                  {targetId && <div className="node-card-divider mt-2.5 mb-1.5" aria-hidden />}

                  {/* 操作行：整行降权（更小/幽灰），三群纯视觉分组不加文字标签（§五）——
                      ②具名备选⇄ / ③定向微调◐（幽灰，与②间一个竖点隔开）/ ④投票恒右对齐。
                      VoteButtons 自身按 collabMode 隐藏，这一行仍渲染让协作模式下赞踩可见。 */}
                  {targetId && (
                    <div
                      className={cn(
                        "flex flex-wrap items-center gap-1.5",
                        isLocked && "pointer-events-none opacity-40",
                      )}
                    >
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
                      {alternatives.length > 0 && chips.length > 0 && (
                        <span className="mx-1 h-3 w-px shrink-0 bg-black/[0.08]" aria-hidden />
                      )}
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
                      <span className="ml-auto flex items-center">
                        <VoteButtons stageIndex={stageIndex} />
                      </span>
                    </div>
                  )}

                  {/* 换菜中（loading）：店名位已经是 shimmer 骨架、操作行已降透明，
                      这里只补一句幽灰小字状态提示——不整卡闪（§六「换菜中」），
                      替掉旧版整行 ShimmerStripe（视觉响度过重，和新版"降权"不符）。 */}
                  {isLocked && (
                    <div className="mt-1.5 flex items-center gap-1 text-xs text-ink-400">
                      <Icons.thinking className="w-3 h-3 animate-spin" strokeWidth={2} />
                      <span>换菜中…</span>
                    </div>
                  )}
                </div>
              </li>
            </Fragment>
          );
          });
        })()}

        {/* 终点：结束行程（同上，家 bookend 同款更淡样式；这是全时间轴最后一行，
            脊柱段用 --tail 变体在自身高度处截止，不再桥接到下一行——下面已经
            没有行了） */}
        <li className="relative flex items-center gap-0">
          <div className="relative flex flex-col items-center justify-center min-w-[52px] shrink-0 self-stretch">
            <div aria-hidden className="timeline-spine-seg timeline-spine-seg--tail" />
            <div className="timeline-dot-home relative z-10">
              <Icons.home className="w-2.5 h-2.5 text-ink-400" strokeWidth={2} />
            </div>
          </div>
          <div className="w-4 shrink-0" aria-hidden />
          <div className="inline-flex items-center gap-1.5 rounded-xl border border-black/[0.05] bg-black/[0.02] px-3 py-1.5 text-sm font-medium text-ink-500">
            <Icons.home className="w-3.5 h-3.5 text-ink-400" strokeWidth={2} />
            <span>满载而归</span>
          </div>
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
          "linear-gradient(135deg, rgba(245,158,11,0.08) 0%, rgba(245,158,11,0.03) 100%)",
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
          "linear-gradient(135deg, rgba(0,0,0,0.02) 0%, rgba(0,0,0,0.008) 100%)",
      }}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          <Icons.share
            className="w-3.5 h-3.5 text-ink-500"
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
          ? "linear-gradient(135deg, rgba(16,185,129,0.08) 0%, rgba(16,185,129,0.03) 100%)"
          : "linear-gradient(135deg, rgba(0,0,0,0.025) 0%, rgba(0,0,0,0.01) 100%)",
        borderColor: isConfirm
          ? "rgba(16,185,129,0.24)"
          : "rgba(0,0,0,0.06)",
        color: "rgb(31 41 55 / 0.92)",
      }}
    >
      {text && (
        <div className="flex items-start gap-2">
          <Icons.spark
            className={cn(
              "w-4 h-4 mt-1 shrink-0",
              isConfirm ? "text-emerald-400" : "text-accent-600",
            )}
            strokeWidth={2}
          />
          <p className="whitespace-pre-wrap text-xl leading-relaxed">
            <HighlightText text={text} />
          </p>
        </div>
      )}

      {chips.length > 0 && (
        <div className={cn("flex flex-wrap items-center gap-2", text && "mt-3 border-t border-black/[0.06] pt-3")}>
          <div className="mr-1 inline-flex items-center gap-1.5 text-base font-semibold text-ink-500">
            <Icons.spark className="w-4 h-4 text-ink-500" strokeWidth={2.5} />
            <span>为你考虑了</span>
          </div>
          {chips.map((c, i) => {
            const Ico = Icons[c.icon];
            return (
              <span
                key={`${c.label}-${i}`}
                className="inline-flex items-center gap-1.5 rounded-full border border-black/[0.08] bg-black/[0.03] px-3 py-1.5 text-base font-semibold tracking-tight text-ink-700 animate-fade-in"
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
              isConfirm ? "text-emerald-600 hover:text-emerald-700" : "text-ink-600 hover:text-ink-800",
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
      className="rounded-md border border-black/[0.06] bg-black/[0.02] px-3.5 py-3 text-xs leading-relaxed"
    >
      <div className="flex items-center gap-1.5 mb-2">
        <Icons.spark
          className="w-3.5 h-3.5 text-ink-500"
          strokeWidth={2}
        />
        <span className="text-sm font-semibold text-ink-700 tracking-tight">
          点击「确认并预约」之后
        </span>
      </div>

      <p className="text-sm text-ink-800 mb-2.5">
        {restaurantLine}{extraLine}；再为你备好一段可一键复制的转发文案；最后{memoryLine}。
      </p>

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-500">
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
      className="relative flex items-center gap-0 animate-fade-in-up"
      title={`${prev.end} → ${curr.start}`}
    >
      {/* 同节点/通勤行共用一套脊柱段桥接（.timeline-spine-seg），不再另用
          border-l-2——避免脊柱旁边多一条平行线，和精修终稿§一「克制」冲突。 */}
      <div className="relative flex flex-col items-center min-w-[52px] shrink-0 self-stretch">
        <div aria-hidden className="timeline-spine-seg" />
      </div>
      <div className="w-4 shrink-0" aria-hidden />
      <div className="flex-1 min-w-0 py-1.5 text-xs text-ink-400 tracking-tight">
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

/**
 * 通勤脊柱标的图标（时间轴精修终稿§二）——按 mode 分辨，不是设计稿示例里
 * 统一的 🚶：taxi/bus 若也套一个步行图标会误导通勤方式。haversine_estimated
 * 视为"步行距离估算"，图标仍用 🚶，另在文字里追加 translateHopMode 的
 * "估算"后缀标出不确定性（承接旧版信息，不因精简丢信息）。
 */
function hopIcon(mode: HopMode): string {
  switch (mode) {
    case "taxi":
      return "🚕";
    case "bus":
      return "🚌";
    case "walking":
    case "haversine_estimated":
    default:
      return "🚶";
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

/**
 * 时间轴精修终稿§四「按钮自适应宽度」：改用 max-width + truncate（CSS 省略号）
 * 替掉旧版「固定切 6 字」的硬截断（`truncateAltName`，按 JS 字符数切）——固定
 * 字符数是个和"这个按钮实际有没有地方显示"无关的武断阈值：6 字对"钱塘汇雪茄厅"
 * 这种名字不够（仍会被切成"钱塘汇雪茄…"），对更短的名字又是多余的一层判断
 * （切与不切全凭字数，不是"真的放不下"）。改用 CSS `max-width` + `truncate`
 * 后，浏览器按实际渲染宽度决定要不要截，短名天然不触发省略号、全显；只有超过
 * ~9 个 CJK 字宽（9em，字号 12.5px 时 ≈112px，落在设计稿"8–10 字宽"区间）才截，
 * 靠 title 兜底全名。
 */
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
        "inline-flex items-center gap-1 px-2 py-1 rounded-md text-[12.5px] font-medium tracking-tight transition-colors",
        "bg-black/[0.03] text-ink-600 hover:bg-black/[0.06] hover:text-ink-800",
        "disabled:opacity-40 disabled:cursor-not-allowed",
      )}
    >
      <ArrowLeftRight className="w-3 h-3 shrink-0" strokeWidth={2} />
      <span className="max-w-[9em] truncate">{alt.name}</span>
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
        "inline-flex items-center gap-1 px-1.5 py-1 rounded-md text-xs font-normal tracking-tight text-ink-400 transition-colors",
        "hover:bg-black/[0.03] hover:text-ink-600",
        "disabled:opacity-40 disabled:cursor-not-allowed",
      )}
    >
      <SlidersHorizontal className="w-3 h-3 shrink-0" strokeWidth={2} />
      {/* 定向微调 label 一般较短（"更近"/"更热闹"），同样给 max-width + truncate
          兜底（时间轴精修终稿§四"定向微调同理"），避免极端长 label 撑爆整行。 */}
      <span className="max-w-[7em] truncate">{chip.label}</span>
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
              <span key={i} className="font-bold text-accent-700 mono">
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
                className="font-semibold bg-accent-500/20 px-0.5 rounded"
              >
                {part.text}
              </span>
            );
          case "person":
            return (
              <span
                key={i}
                className="font-semibold underline decoration-accent-500 decoration-2 underline-offset-2"
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
              <span key={i} className="bg-accent-500/20 px-0.5 rounded">
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
