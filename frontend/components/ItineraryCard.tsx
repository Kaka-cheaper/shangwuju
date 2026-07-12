"use client";

import {
  Fragment,
  type CSSProperties,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { ArrowLeftRight, Clock, Info, type LucideIcon, SlidersHorizontal } from "lucide-react";

import { Icons } from "@/lib/icon-map";
import { useCollabStore } from "@/lib/collab-store";
import { useChatStore } from "@/lib/store";
import { useConfirmAction } from "@/lib/hooks/useConfirmAction";
import { buildConfirmPreviewCopy } from "@/lib/confirm-preview";
import type {
  ActivityNode,
  AlternativeOption,
  HopMode,
  IntentExtraction,
  Itinerary,
  NodeChip,
  NodeDetail,
  NodeDetailMap,
  ScheduleEntry,
} from "@/lib/types";
import { cn, primaryStoreName } from "@/lib/utils";

import NodeFactPanel, { NodeHeadline } from "./NodeFactPanel";
import NumberTicker from "./NumberTicker";
import PosterGenerator from "./PosterGenerator";
import ShimmerStripe from "./ShimmerStripe";
import ComparisonView from "./ComparisonView";
import MapOverlay from "./MapOverlay";
import TrustBelt from "./TrustBelt";
import TtsPlayer from "./TtsPlayer";
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

/**
 * 揭幕入场要在浏览器 paint 之前就把 .reveal-* 类挂上（否则会先闪一帧"静止态卡片"
 * 再从 opacity:0 掀起，出现弹跳/闪烁）。useLayoutEffect 在 DOM 变更后、paint 前
 * 同步执行 → 无闪帧。SSR 不跑布局，退回 useEffect 避免服务端告警（本组件的方案卡
 * 只在用户交互后出现，SSR 首屏 showCard 恒为 false，不会命中揭幕分支）。
 */
const useIsoLayoutEffect =
  typeof window !== "undefined" ? useLayoutEffect : useEffect;

/** prefers-reduced-motion：整段揭幕降级为即时静止态（同 TrustBelt 的读法）。 */
function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return reduced;
}

/** 行程卡片：聚焦方案摘要、时间轴、地图、预订结果和主执行动作。 */
export default function ItineraryCard() {
  const itinerary = useChatStore((s) => s.itinerary);
  const intent = useChatStore((s) => s.intent);
  const narration = useChatStore((s) => s.narration);
  // 换菜备选收据（2026-07-11）：不进信任带（adjust 流不喂带是既定设计），
  // 挂在换菜结果这条叙事下方（见下方 swapAlternativesCount 渲染处注释）。
  const swapAlternativesCount = useChatStore((s) => s.swapAlternativesCount);
  const memoryPersisted = useChatStore((s) => s.memoryPersisted);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);

  // ADR-0013 F-4：节点行调整入口（右侧具名备选 / 下方定向调整 chips）
  const nodeActions = useChatStore((s) => s.nodeActions);
  // 节点卡 headline（评分+人均）与事实行（距离/可订/营业至+tag）的数据源，
  // 镜像 nodeActions 的读法（同一个 agent_narration payload 兄弟字段）。
  const nodeDetail = useChatStore((s) => s.nodeDetail);
  const lockedNodeId = useChatStore((s) => s.lockedNodeId);
  const sendAdjust = useChatStore((s) => s.sendAdjust);

  // 协作模式
  const collabMode = useCollabStore((s) => s.collabMode);
  const sendCollabAdjust = useCollabStore((s) => s.sendAdjust);
  const lastRefinement = useChatStore((s) => s.lastRefinement);
  const previousItinerary = useChatStore((s) => s.previousItinerary);
  const cancel = useChatStore((s) => s.cancel);

  // ============================================================
  // 压住 → 就绪后揭幕（本批核心，见交付报告「压住→就绪后揭幕」）
  //
  // 真实流式链路：itinerary_ready 先到（规则标题占位）→ agent_narration 最后
  // 才把 LLM 标题原地换进 itinerary.summary + 推出口播 narration.text → done
  // 令 streaming 翻 false。若一边流一边揭，标题会先露"规则套话"再被换掉、口播
  // 也会中途冒出——凌乱。正解：AI 幕后思考流（TrustBelt）照常直播当观赏性等待，
  // 把方案卡整张压住不上屏，直到 streaming 翻 false（= 总结/narration 已就绪、
  // 整条流结束这个干净信号）再统一放这套自上而下的揭幕。
  //
  // showCard：是否把方案卡上屏（vs. 压住时的"正在拼装"骨架）。
  //   - itinerary 且 !streaming：流已结束 → 上屏（首轮 done / 稳定态 / 换菜态）
  //   - itinerary 且 streaming 但已上过屏（planVisible 锁存）：确认预约进行中
  //     接续展示，不重新压住、不重放揭幕
  //   - itinerary 且 streaming 且没上过屏：首轮/反馈重规划进行中 → 压住
  const [spotlight, setSpotlight] = useState(false);
  const [planVisible, setPlanVisible] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const [landed, setLanded] = useState(false);
  const reducedMotion = useReducedMotion();
  const showCard = !!itinerary && (planVisible || !streaming);

  // planVisible 锁存：一旦上过屏就记住（confirm 期间 streaming 又变 true 也不再
  // 压住）；itinerary 被清空（新一轮 / 反馈重规划）时复位，等下一次 done 再揭。
  useEffect(() => {
    if (showCard) setPlanVisible(true);
    else if (!itinerary) setPlanVisible(false);
  }, [showCard, itinerary]);

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
    useConfirmAction(!animating && !revealing);
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

  // 跳过动画：清 timer + 立即全显 + 收束揭幕（把三幕直接落到静止态）
  const skipAnimation = () => {
    if (animTimerRef.current) {
      clearTimeout(animTimerRef.current);
      animTimerRef.current = null;
    }
    setVisibleCount(visibleEntries.length);
    setAnimating(false);
    setRevealing(false);
    setLanded(false);
    setSpotlight(false);
  };

  // ============================================================
  // 揭幕编排：showCard 首次 false→true（首轮 done / 反馈重规划 done）时，
  // 自上而下放三幕（幕①标题掀开 + 幕③节点弹性过冲 + 幕④投影落定），落定后归静。
  //   - confirm 期间 showCard 恒 true（planVisible 锁存），不会触发（不重放）
  //   - useLayoutEffect：paint 前挂类，避免"先闪静止态再掀起"
  //   - reducedMotion：只上屏、不放动画（即时静止态）
  // 时序（照原型 v4 的 playReveal 节奏，去掉未落地的脊柱画出幕②后压缩）：
  //   t0 纸抬升(0.7s)+标题掀开(0.12→0.9s)+节点逐行弹起 → t1.8s 投影落桌 →
  //   t2.6s 撤动画类归静。到场柔光(spotlight)≈2.4s 一次性。
  // ============================================================
  const prevShowCardRef = useRef(false);
  useIsoLayoutEffect(() => {
    const wasShown = prevShowCardRef.current;
    prevShowCardRef.current = showCard;

    if (!showCard) {
      // 压回骨架态：清净揭幕态，等下一次 done 重新揭
      setRevealing(false);
      setLanded(false);
      setSpotlight(false);
      return;
    }
    if (wasShown) return; // 已在屏上（confirm / 换菜接续）→ 不重放揭幕

    // 首次上屏：先把时间轴强制全显（越过旧的逐条 stagger）再叠三幕
    if (animTimerRef.current) {
      clearTimeout(animTimerRef.current);
      animTimerRef.current = null;
    }
    setVisibleCount(visibleEntries.length);
    setAnimating(false);
    lastAnimatedTotalRef.current = visibleEntries.length;

    if (reducedMotion) {
      setRevealing(false);
      setLanded(false);
      setSpotlight(false);
      return;
    }

    setSpotlight(true);
    setRevealing(true);
    setLanded(false);
    const tLand = setTimeout(() => setLanded(true), 1800); // ④ 投影落桌
    const tEnd = setTimeout(() => {
      setRevealing(false); // 归静：撤动画类（落桌深影 = 静止态，无跳变）
      setLanded(false);
    }, 2600);
    const tGlow = setTimeout(() => setSpotlight(false), 2400);
    return () => {
      clearTimeout(tLand);
      clearTimeout(tEnd);
      clearTimeout(tGlow);
    };
    // reducedMotion / visibleEntries 有意不入依赖：只在 showCard 翻转时编排一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCard]);

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
      <div className="space-y-3">
        {/* 信任带（单一稳定实例，2026-07-06 收口）：固定挂在方案卡容器
            上方，空闲/规划中/就绪三态共享同一个挂载位置——见下方另外两个
            分支同款结构。空闲态没有任何 beats 可显示，TrustBelt 内部会
            自己判定返回 null，这里不需要额外按 streaming/itinerary 条件
            隐藏它，放在上方也不会在空闲时露出。 */}
        <TrustBelt />
        <div className="card px-4 py-8 flex flex-col items-center gap-2.5 text-ink-500">
          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-brand-500/15 to-accent-500/15 flex items-center justify-center border border-black/[0.08]">
            <Icons.pin className="w-4 h-4 text-brand-600" strokeWidth={1.5} />
          </div>
          <span className="text-sm text-ink-700">行程将在这里出现</span>
        </div>
      </div>
    );
  }

  // 压住态（!showCard）：itinerary 可能已到（itinerary_ready），但总结/narration
  // 还没就绪、流没结束 → 整张方案卡压住不上屏，只让 AI 幕后思考流（TrustBelt）
  // 直播 + "正在拼装"骨架顶着，直到 done 才统一揭幕（见上方 showCard 说明）。
  if (!showCard) {
    return (
      <div className="space-y-3">
        {/* 信任带：规划中就该看到"它在想什么"，不必等方案落地。同一个
            挂载位置（卡片容器上方）在三个分支间保持结构一致——itinerary
            从 null→非 null 切到"就绪"分支时，React 按元素类型+位置比对，
            这个 <TrustBelt/> 不会被卸载重挂，只有它下面的卡片内容会替换。 */}
        <TrustBelt />
        <div className="card space-y-5 rounded-[30px] px-5 py-5">
          <div className="flex items-center gap-2 text-base font-semibold leading-snug text-ink-900">
            <span
              aria-hidden
              className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-[#FFD100]/35 border-t-[#f59e0b]"
            />
            <span className="tracking-tight">我正在把路线和时间排整齐，马上端上行程~</span>
          </div>
          <ShimmerStripe rows={4} className="space-y-3.5 [&>*]:h-3.5 [&>*]:rounded-full" />
        </div>
      </div>
    );
  }

  const totalH = itinerary.total_minutes / 60;
  const hasOrders = itinerary.orders.length > 0;
  const timelineNodeCount = visibleEntries.filter((entry) => entry.entry_kind === "node").length;
  // R1: animating / 揭幕(revealing) 期间禁用按钮（避免用户在动画进行中点确认）。
  // canConfirm/handleConfirm 已由 useConfirmAction(!animating && !revealing) 统一算好。
  const canAct =
    !streaming && !hasOrders && !cancelled && !animating && !revealing;
  // ADR-0013 F-5：房间模式下节点调整走 WS "adjust"（RoomManager.adjust，归名+
  // 串行+锁定广播），单人模式维持原 HTTP `/chat/adjust` SSE（同 ChatDock 的
  // collabMode 分流先例）——两个调用点（具名备选/定向调整 chip）共用这一处分流。
  const dispatchAdjust = collabMode ? sendCollabAdjust : sendAdjust;

  // 幕③ 逐行 stagger：给时间轴每一 <li>（含首尾"家" + 节点/通勤/自由休息行）
  // 按自上而下的 DOM 顺序注入递增 animationDelay，配 .reveal-row 的 back.out
  // 弹性过冲。计数器每次 render 归零、随 JSX 构建顺序自增（纯本地、确定性）；
  // 非 revealing 态返回空 props（静止态无入场动画，避免归静/换菜时误触发闪动）。
  let revealRowSeq = 0;
  const rowRevealProps = (): {
    className: string;
    style: CSSProperties | undefined;
  } => {
    if (!revealing) return { className: "", style: undefined };
    const style: CSSProperties = {
      animationDelay: `${(0.42 + revealRowSeq * 0.08).toFixed(3)}s`,
    };
    revealRowSeq += 1;
    return { className: "reveal-row", style };
  };

  return (
    <div className="space-y-3">
      {/* 信任带（单一稳定实例，2026-07-06 收口——见上方空闲/规划中两个分支
          同款注释）：固定挂在方案卡容器上方，itinerary 从 null→非 null 切到
          这个分支时，外层 <div className="space-y-3"> 与这个 <TrustBelt/>
          在三个分支间类型、位置完全一致，React 不会卸载重挂它——只有它
          下面这块方案卡内容会替换。层级意图：[AI 幕后·思考过程] 在上、
          [方案卡·交付物] 在下——过程先于成品，信任带在卡外也不会跟方案卡
          抢焦点（方案卡本身的"主角化"视觉处理另案处理，本批不动卡头）。 */}
      <TrustBelt />
      {/* 方案卡主角化（⑤ 抬升 + 发丝暖描边/顶缘微光 + 到场柔光，见设计终稿§二
          改版）：relative 包裹层承载"到场柔光"——方案落定那一刻（spotlight 2.4s
          一次性态）从卡顶后方绽放一团暖光晕再消失；卡片本体换 .itinerary-hero
          （暖调精修抬升 + 顶缘暖金发丝 rim-light）成为悬浮的交付物主角。替掉旧的
          黄光环 spotlight-once（硬黄环/色块那套"捞"的做法）——高级感来自材质与
          光、不来自铺黄。 */}
      <div className="relative">
        {spotlight && <div className="itinerary-arrival-glow" aria-hidden />}
        {/* 揭幕（幕④投影落定）：revealing 时挂 .reveal-card（抬升浅软影 + sheetLift），
            landed 时叠 .reveal-card--land 过渡到落桌深影；归静后撤类回到 .itinerary-hero
            静止态（深影一致，无跳变）。不再用 animate-fade-in——入场由三幕接管。 */}
        <div
          className={cn(
            "itinerary-hero relative overflow-hidden rounded-[30px] border border-black/[0.06] bg-white",
            revealing && "reveal-card",
            revealing && landed && "reveal-card--land",
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
              <Icons.camera className="h-5 w-5 shrink-0 text-[#8f4b24]" strokeWidth={1.8} aria-hidden />
              <span className="text-lg font-black tracking-tight text-[#8f4b24]">
                今日行程安排
              </span>
            </div>
            {/* 幕① 标题遮罩掀开：.reveal-title 是 overflow:hidden 的裁剪框，
                .reveal-title-inner 从框下方掀上来露出（文字全程锐利，不模糊/淡入）。
                只在 revealing 时裁剪+动画，静止态是普通 block，不改多行标题排版。 */}
            <div className="mt-2 text-3xl font-black leading-tight tracking-tight text-ink-900">
              <span className={cn("reveal-title", revealing && "reveal-title--anim")}>
                <span className="reveal-title-inner">
                  <HighlightSummary text={itinerary.summary} />
                </span>
              </span>
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

      {/* Agent 暖心开场白——唯一的叙事声音（2026-07-06 收口：删掉与这段正文
          重复的「为你考虑了」intent chips 与「查看全部取舍说明」折叠，见
          下方 NarrationBlock docstring）。
          2026-07-12 收口：删掉「已根据反馈调整」RefinementSummaryBanner——它
          的 changedFields 芯片与①调整对比重复、refinerNote 与③口播开头重复
          （一屏三个声音说同一件"改了什么"）；口播直接借用它那张卡的规整外壳
          （白底/边框/圆角/阴影，不带 header），一举把"漂亮的壳"留下、"重复的
          内容"删掉。 */}
      {narration?.text && (
        <div className="px-4 pt-3">
          <NarrationBlock text={narration.text} stage={narration?.stage ?? "stream"} />
        </div>
      )}

      {/* 换菜备选收据（2026-07-11，见路演PPT/信任带设计终稿.md 同日修订「五
          收据」换菜备选行）：**不进信任带**——adjust 流不喂带是既定设计（单
          思考面，修订4）。挂在换菜结果这条叙事正下方——narration 此刻正是
          "把「X」换成了「Y」"这句话，备选数字紧贴它才读得通；数字来自
          swap_alternatives_count（服务端现算，见 AgentNarrationPayload 字段
          注释），不是"有 narration 就显示"，只有真是换菜这条叙事才会带这个
          字段（"无内容不加字段"）。 */}
      {narration?.text && swapAlternativesCount != null && swapAlternativesCount > 0 && (
        <div className="px-4 pt-1.5">
          <SwapAlternativesNotice count={swapAlternativesCount} />
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

      {/* R1: 时间轴 stagger / 揭幕三幕进行中显示跳过按钮 */}
      {(animating || revealing) && (
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
      <ol
        className="timeline-rail relative px-5 pb-5 pt-3 space-y-3.5"
        style={
          {
            "--timeline-rail": timelineRailGradient(timelineNodeCount),
          } as CSSProperties
        }
      >
        {/* 起点：从家出发（§六「家」bookend——无主活动标签/无操作层，更扁更淡；
            §一「首尾'家'=小 home 图标点，中性灰，不用 🚀/✨」——圆点本身换成
            home 图标点。节点卡+行程轨-对比.html 改版：右侧文案由"药丸框"改
            成贴内容列左边距的纯文字（同节点卡内容行 px-4 对齐，见下方
            timecol 布局注释），文案沿用既有实现，未采用设计稿示例"从家出发"
            文案，见交付报告说明。timecol 列（"出发"小字）是本批新增的纯布局
            列，复用真实 .timeline-spine-seg/.timeline-dot-home 类不变。 */}
        {(() => {
          const rr = rowRevealProps();
          const firstNodeTone = visibleEntries.some((item) => item.entry_kind === "node")
            ? stageTone(0)
            : HOME_STAGE_TONE;
          return (
        <li className={cn("relative flex items-center gap-0", rr.className)} style={rr.style}>
          {/* 时间/通勤列（timecol）：家 bookend 无起止时刻，只放一个极小的
              "出发" 标签，右对齐、贴脊柱——和下方节点行/通勤行共用同一条
              时间列，让整条时间轴左侧的"时间语义列"贯穿始终。 */}
          <div className="w-6 shrink-0" aria-hidden />
          {/* self-stretch：覆盖 li 的 items-center，让这一列撑满整行高度，
              脊柱段（absolute top:0/bottom:-14px）才能按"这一行的真实高度"
              桥接到下一行，而不是只按 home 点自己的小高度算；justify-center
              把点重新在撑满后的列里垂直居中，视觉效果和之前一致。 */}
          <div
            className="relative flex flex-col items-center justify-center min-w-[52px] shrink-0 self-stretch"
            style={
              {
                "--timeline-line": stageLineGradient(HOME_STAGE_TONE, firstNodeTone),
                "--timeline-line-top": "42px",
              } as CSSProperties
            }
          >
            <div aria-hidden className="timeline-spine-seg" />
            <div className="timeline-dot-home relative z-10">
              <Icons.home className="h-5 w-5 text-white" strokeWidth={2.35} />
            </div>
          </div>
          <div className="w-4 shrink-0" aria-hidden />
          {/* 内容列：纯文字，pl-4 对齐节点卡的内边距（.node-card px-4），
              让起终点/自由休息这些过渡文字和卡内「用餐/主
              活动」标签左边对齐（不再贴脊柱右边）。 */}
          <div className="flex-1 min-w-0 pl-4 text-base font-semibold text-ink-600">
            从望京出发
          </div>
        </li>
          );
        })()}

        {(() => {
          // ADR-0013 F-4：room.py 的 vote 协议 stage_index = "mid nodes 顺序"
          // （跳过首尾 home 之后的第几个节点，0-based，见 collab/room.py:850-881
          // `_get_stage_title` docstring）。schedule 派生视图里 home 节点的
          // entry 恒 hidden=True（assemble_blueprint.py::_derive_schedule），
          // 已被 visibleEntries 在源头过滤——故 entry_kind==="node" 的这些条目
          // 天然就是 mid nodes、且顺序一致，用一个跨 map 迭代的计数器即可还原
          // stage_index，不需要另建一套节点定位。
          let midNodeIndex = -1;
          const midNodeTotal = visibleEntries.filter((item) => item.entry_kind === "node").length;
          return visibleEntries.map((entry, idx) => {
          // R1: stagger 控制——idx 超出 visibleCount 时不渲染
          if (idx >= visibleCount) return null;

          // 空档检测：与上一条 entry 的 [end, start] 之间若有 ≥ FREE_GAP_THRESHOLD_MIN
          // 分钟的真实空闲，插入一行「自由休息」。gap 用 (本条 start − 上一条 end)
          // 计算——通勤段自己的分钟数已经被通勤行自己的 [start, end] 吃掉，
          // 这里天然不会把通勤时间重复计入休息时长（不是 "下一站 start − 上一站 start"）。
          const prevEntry = idx > 0 ? visibleEntries[idx - 1] : null;
          // hop 行：通勤是两段行程之间的关系，不再吸附到下一张行程卡顶部。
          // 统一渲染为独立胶囊行：从起点/上一站出发 → 通勤 → 下一站/回家。
          if (entry.entry_kind === "hop") {
            const gapNode = renderFreeGap(prevEntry, entry, idx, rowRevealProps);
            if (!entry.mode || entry.mode === "virtual") return gapNode;
            const nextEntry = visibleEntries[idx + 1] ?? null;
            const HopIcon = hopIconComponent(entry.mode);
            const hopReveal = rowRevealProps();
            const fromTone = midNodeIndex >= 0 ? stageTone(midNodeIndex) : HOME_STAGE_TONE;
            const toTone =
              nextEntry?.entry_kind === "node"
                ? stageTone(midNodeIndex + 1)
                : HOME_STAGE_TONE;
            return (
              <Fragment key={entry.ref_id || `hop-${idx}`}>
                {gapNode}
                <li
                  className={cn("relative flex items-center gap-0", hopReveal.className)}
                  style={hopReveal.style}
                  title={`${entry.start} → ${entry.end}`}
                  >
                  <div className="w-6 shrink-0" aria-hidden />
                  <div
                    className="relative flex flex-col items-center min-w-[52px] shrink-0 self-stretch"
                    style={
                      {
                        "--timeline-line": stageLineGradient(fromTone, toTone),
                      } as CSSProperties
                    }
                  >
                    <div aria-hidden className="timeline-spine-seg" />
                  </div>
                  <div className="w-4 shrink-0" aria-hidden />
                  <div className="flex-1 min-w-0 py-1.5">
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-[#d89a00]/25 bg-[#fff5bf]/55 px-3.5 py-1.5 text-sm font-bold text-[#9a5b00] shadow-sm backdrop-blur">
                      <HopIcon className="h-3.5 w-3.5 shrink-0 text-[#9a5b00]" strokeWidth={1.9} />
                      <span>
                        通勤 {entry.minutes} 分钟 · {translateHopMode(entry.mode)}
                      </span>
                    </span>
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
          // node_detail：r1 headline（评分+人均）与 r2 事实行（距离/可订/营业至+
          // tag）的共同数据源，两处各自按字段缺失独立不渲染（NodeFactPanel.tsx）。
          const detail = targetId ? nodeDetail?.[targetId] : undefined;
          const isLocked = targetId != null && lockedNodeId === targetId;
          const canAdjust = targetId != null && !isLocked && lockedNodeId == null && !streaming;
          // 店名：唯一视觉焦点，不再硬截单行（无 truncate），title 兜全文
          // （含 note，换菜中悬停也能看到完整"店名 · 理由"）。note 是后端
          // ActivityNode.note 透传的选点/预约说明，必须作为独立第三行展示。
          const note = nodeDisplayNote(itinerary, entry.ref_id, detail);
          const fullTitle = note ? `${entry.title} · ${note}` : entry.title;
          const nodeReveal = rowRevealProps();
          const tone = stageTone(stageIndex);
          const nextTone = stageIndex + 1 < midNodeTotal ? stageTone(stageIndex + 1) : HOME_STAGE_TONE;
          const prevTone = stageIndex > 0 ? stageTone(stageIndex - 1) : HOME_STAGE_TONE;
          // 幕③：gap（自由休息）行也走同一套逐行 stagger（在它关联的通勤/节点行
          // 之前渲染，计数器顺序天然正确）。这里顺手把 gap 的脊柱染成"上一节点→当前
          // 节点"的同族渐变，避免自由休息行把时间轴切成灰色断层。
          const gapNode = renderFreeGap(
            prevEntry,
            entry,
            idx,
            rowRevealProps,
            stageLineGradient(prevTone, tone),
          );

          return (
            <Fragment key={entry.ref_id || `node-${idx}`}>
              {gapNode}
              {/* timeline-row：hover 协同的作用域根（§三.3）——CSS :has() 在这一层
                  判定"卡片被 hover"或"点/时间被 hover"，零 JS 状态（见 globals.css
                  .timeline-row:has(...) 规则），双向、200ms ease、只动
                  transform/opacity/color。幕③ 揭幕时 .reveal-row 弹性入场（入场由三幕
                  接管，不再用 animate-fade-in-up，避免归静时误触发二次淡入）。 */}
              <li
                className={cn("relative flex items-start gap-0 timeline-row", nodeReveal.className)}
                style={nodeReveal.style}
              >
                {/* 时间列（timecol）：起止时刻，挪到脊柱左侧独立一列、右对齐
                    （节点卡+行程轨-对比.html 改版——原先时间绝对定位"压"在圆点
                    所在列的正上/下方，现在和圆点分成左右两列，不再共享同一条
                    视觉基线，也不会把圆点所在列的宽度撑给时间文字用）。
                    -9px/+12px 偏移是照视觉稿手工量的相对值换算：视觉稿
                    dot-wrap margin-top:19px 时 t-start top:10/t-end top:31，
                    即 dot 位置 ∓9/+12，这里换算成我们自己的 TIMELINE_DOT_OFFSET
                    （20px）基准延用同一相对关系，不是另起一套独立数字——真机
                    未校验，见交付报告「未真机验证项」。 */}
                <div className="w-6 shrink-0" aria-hidden />
                {/* 脊柱+点列（railcol）：桥接到下一行，停留=实线。self-stretch：
                    li 是 items-start，这一列默认只有 marker 自身高度（远矮于
                    右侧卡片）；撑满整行高度后，脊柱段的 bottom:-14px 才是按
                    "这一行的真实高度"（=卡片高度）去桥接下一行，视觉上才真的是
                    "实线段=停留，和卡片一样高"（终稿§二 ASCII 图示的意思）。
                    timeline-marker 类沿用不新造——时间挪去左列后，这里单纯
                    复用它触发 globals.css 既有的悬停协同规则
                    （.timeline-row:has(.timeline-marker:hover) .node-card），
                    命中范围从"仅点周围"扩大到"整条 railcol"，属于更宽松的
                    hover 靶区，不是新视觉。 */}
                <div
                  className={cn(
                    "relative flex flex-col items-center min-w-[52px] shrink-0 self-stretch timeline-marker timeline-marker--node",
                    stageIndex === 0 && "timeline-marker--first",
                  )}
                  style={
                    {
                      "--timeline-line": stageLineGradient(tone, nextTone),
                      "--timeline-line-top": `${TIMELINE_DOT_OFFSET + 42}px`,
                    } as CSSProperties
                  }
                >
                  <div aria-hidden className="timeline-spine-seg" />
                  <div
                    className={cn("relative z-10 timeline-dot timeline-dot--indexed", isLocked && "timeline-dot--current")}
                    style={{
                      marginTop: TIMELINE_DOT_OFFSET,
                      background: tone.gradient,
                    }}
                    title={isLocked ? "换菜中" : undefined}
                  >
                    <span>{stageIndex + 1}</span>
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
                {/* 右侧内容：节点卡（节点卡+行程轨-对比.html ①单人卡布局 + 暖色化用
                    渐变描边/柔光（.node-card，见 globals.css）+ 和信任带同族的 16px
                    圆角/字色阶）。原先"左栏内容 + 右栏 140px 事实面板"两栏布局已废弃
                    ——右栏把卡片撑高、右侧死白，被用户否掉；改成视觉稿的单栏四行制：
                    r1 店名行（左标签+店名，右上角 headline）→ r2 事实行（距离·可订·
                    营业至+tag）→ r3 理由行（note）→ 分隔线 → r4 操作行。 */}
                <div className="node-card relative flex-1 min-w-0 px-5 pt-4 pb-3.5">
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <span
                      className="inline-flex items-center gap-2 text-lg font-black tabular-nums"
                      style={{ color: tone.color }}
                    >
                      <Clock className="h-5 w-5 shrink-0" strokeWidth={2} />
                      <span>{entry.start} - {entry.end}</span>
                    </span>
                  </div>
                  {/* r1：类型标签 + 店名 + 事实信息同排，右上角 headline（评分★+
                      人均，NodeHeadline）。利用桌面端横向空间，把距离/余位/
                      营业和标签收到名称后面，不再让中间大面积留白。 */}
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-2">
                      <span
                        className="node-glass-label shrink-0 border px-3 py-1 text-sm font-bold tracking-tight"
                        style={{
                          color: tone.color,
                          backgroundColor: tone.softBg,
                          borderColor: tone.border,
                        }}
                      >
                        {nodeKindLabel(itinerary, entry.ref_id)}
                      </span>
                      {isLocked ? (
                        <span
                          className="h-7 w-48 shrink-0 rounded shimmer-skeleton"
                          aria-hidden
                        />
                      ) : (
                        <span
                          className="node-title-wavy min-w-0 max-w-full text-2xl font-black leading-snug text-ink-900"
                          title={fullTitle}
                        >
                          {entry.title}
                        </span>
                      )}
                      {!isLocked && (
                        <NodeFactPanel
                          detail={detail}
                          className="mt-0 max-w-full"
                          tone={tone}
                          size="large"
                        />
                      )}
                    </div>
                    {!isLocked && <NodeHeadline detail={detail} size="large" />}
                  </div>

                  {/* r2：事实行——距离·可订·营业至 + tag，紧贴店名下方一行；
                      评分/人均已被 r1 headline 拿走，这里不再重复（诚实红线：
                      字段缺失就不渲染，不补占位）。 */}

                  {/* r3：评论/理由行——优先用后端 ActivityNode.note；若规则规划
                      路径没有 note，则退到 node_detail.recommendation_reason，
                      保证每个活动的推荐说明不因布局重构丢失。 */}
                  {note && !isLocked && (
                    <div className="mt-2 max-w-4xl text-base font-medium leading-relaxed text-ink-500">
                      {note}
                    </div>
                  )}

                  {/* 渐隐分隔线（两侧 mask-image 淡出，比实线更精致） */}
                  {targetId && <div className="node-card-divider mt-2.5 mb-1.5" aria-hidden />}

                  {/* r4：操作行——整行降权（更小/幽灰），三群纯视觉分组不加文字标签：
                      ②具名备选⇄ / ③定向微调◐（幽灰，与②间一个竖点隔开）/ ④投票。
                      单人态（collabMode=false）：VoteButtons 自身按 collabMode 返回
                      null，②③左对齐、右侧自然留白；多人房间态：VoteButtons 渲染
                      赞踩，ml-auto 把它推到行尾靠右——同一份 JSX 兼顾两态，赞踩
                      只在 collabMode 出现，不把这个空 ml-auto span 当单人态布局锚。 */}
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
                      这里只补一句幽灰小字状态提示——不整卡闪（替掉旧版整行
                      ShimmerStripe，视觉响度过重，和新版"降权"不符）。 */}
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
            没有行了）。幕③ 最后一行，收在整段 stagger 末尾。 */}
        {(() => {
          const rr = rowRevealProps();
          const midNodeTotal = visibleEntries.filter((item) => item.entry_kind === "node").length;
          const lastNodeTone = midNodeTotal > 0 ? stageTone(midNodeTotal - 1) : HOME_STAGE_TONE;
          return (
        <li className={cn("relative flex items-center gap-0", rr.className)} style={rr.style}>
          <div className="w-6 shrink-0" aria-hidden />
          <div
            className="relative flex flex-col items-center justify-center min-w-[52px] shrink-0 self-stretch"
            style={
              {
                "--timeline-line": stageLineGradient(lastNodeTone, HOME_STAGE_TONE),
              } as CSSProperties
            }
          >
            <div aria-hidden className="timeline-spine-seg timeline-spine-seg--tail" />
            <div className="timeline-dot-home relative z-10">
              <Icons.home className="h-5 w-5 text-white" strokeWidth={2.35} />
            </div>
          </div>
          <div className="w-4 shrink-0" aria-hidden />
          <div className="flex-1 min-w-0 pl-4 text-base font-semibold text-ink-600">
            结束行程，返回望京
          </div>
        </li>
          );
        })()}
      </ol>

      {/* T8/R2: 高德地图标注（配合 R1 stagger 逐段亮起） */}
      <div className="px-6 pb-4 md:px-8">
        <MapOverlay visibleCount={visibleCount} />
      </div>

      {/* 已为你预留：预约卡重设计（UI 修复批·纯前端）——多卡横向排一行，
          暖金描边+中性文字（去掉游离于调色板外的 emerald 绿），字段全走
          前端 join：时段/花费从时间线本就在渲染的 nodes/node_detail 反查，
          不是新增数据源。见下方 OrderCards 组件 docstring。 */}
      {hasOrders && (
        <div className="px-4 pb-3">
          <div className="section-title mb-1.5">已为你预留</div>
          <OrderCards orders={itinerary.orders} nodes={itinerary.nodes} nodeDetail={nodeDetail} />
        </div>
      )}

      {/* 转发文案 + 常驻工具行——"带走你的安排"分享簇（UI 修复批拍板方案A）：
          海报/TTS 解除与 hasOrders 的连坐，归入这个簇，确认前后都能用。
          见下方 ShareCluster 组件 docstring。 */}
      {(itinerary.share_message || (hasOrders && !cancelled)) && (
        <div className="px-4 pb-3">
          <ShareCluster shareMessage={itinerary.share_message} showTools={hasOrders && !cancelled} />
        </div>
      )}

      <div className="h-4" aria-hidden />
        </div>
      </div>
      {!hasOrders && !cancelled && (
        <PlanActionBar
          intent={intent}
          itinerary={itinerary}
          canAct={canAct}
          canConfirm={canConfirm}
          confirmLabel={confirmLabel}
          streaming={streaming}
          blockedByOwnerGuard={blockedByOwnerGuard}
          onCancel={cancel}
          onConfirm={handleConfirm}
        />
      )}
    </div>
  );
}

// ============================================================
// OrderCards —— 预约卡重设计（UI 修复批·纯前端，零后端）
//
// 【这是什么问题】此前订单卡（问题诊断 `confirm-card-fix/设计.md` 问题1-3）
// 有三处毛病：① 泄漏内部编号（R-WJR060-xxx，mock 订单号=实体id+哈希摘要，
// 从未设计给用户看，`schemas/itinerary.py::OrderRecord.order_id` docstring
// 自己的示例格式都是另一种，说明这本来就是内部可追溯 id 不是用户凭证号）；
// ② 竖排堆叠、要素不全（`o.detail` 是后端拼好的纯字符串，餐厅没花费、
// 门票没时段——曾诊断为"必须补后端"）；③ emerald 绿完全游离于项目暖色
// 调色板（`路演PPT/配色克制设计终稿.md` 只定义 brand 黄/accent 深金/ink
// 中性三类）。
//
// 【字段前端 join 复核，推翻"必须补后端"结论】时段和花费其实**同一个组件
// 树内、时间轴本来就在渲染**：`itinerary.nodes`（同 target_id 节点的
// `start_time`+`duration_min`）与 `nodeDetail[target_id].price_text`
// （时间线 headline 已经在用的同一个字段，见本文件 `nodeDetail` 消费处）
// ——`ItineraryCard` 组件树里两者都已经是 hook 订阅的 store 字段，订单卡
// 按 `order.target_id` 一 join 就有，不需要新增后端字段/新接口。这是本批
// 唯一定性为"纯前端"的原因：数据已经在场，只是订单卡自己没读。
//
// 【四个改动落地对照】
// 1. 删内部编号——`order_id` 只做 React key，不渲染到卡面。
// 2. 多卡横向排一行——`grid-cols-2`（桌面≥2 张订单双列，≤1 张/移动端单列，
//    诊断稿"横向卡"指卡片内部布局改一行放完标题+金额，"多卡横向排布"是
//    本次用户明确澄清的补充：多个订单卡并排，不是纵向堆叠）。
// 3. 配色贴合整体——暖金描边 + 中性文字，圆角 16px（同节点卡/信任带）。
// 4. （海报按钮解连坐见 `ShareCluster`，不在本组件范围。）
// ============================================================

/** 单张订单卡的四要素：名称/时段/人数(或张数)/花费——全部前端 join 得出。 */
function OrderCard({
  order,
  nodes,
  nodeDetail,
}: {
  order: Itinerary["orders"][number];
  nodes: ActivityNode[];
  nodeDetail: NodeDetailMap | null;
}) {
  const node = nodes.find((n) => n.target_id === order.target_id);
  const timeRange = node
    ? `${node.start_time}-${addMinutes(node.start_time, node.duration_min)}`
    : null;
  const priceText = nodeDetail?.[order.target_id]?.price_text ?? null;
  // 人数/张数：order.detail 是后端已拼好的字符串（如"17:30 预订 2 人"/
  // "2 张 / 总价 278 元"），本次只从中摘出"数量"这个量词短语，不吞掉整句、
  // 也不新增字段——摘取失败（格式意外）时整句 detail 兜底展示，不留空。
  const countMatch = /(\d+\s*(?:人|张|份))/.exec(order.detail);
  const countText = countMatch?.[1] ?? order.detail;

  return (
    <li
      className="relative overflow-hidden rounded-2xl border border-[#e6bc00]/25 bg-white px-3.5 py-2.5 animate-fade-in-up"
      style={{
        boxShadow: "0 1px 2px rgba(0,0,0,.03), 0 6px 20px rgba(180,140,0,.05)",
      }}
    >
      {/* 左侧暖金竖线——替代绿色圆点图标，"这是一张确定性凭证"的锚点，
          不引入新色相（同 `卡片精修设计终稿.md` §四"化用技法"）。 */}
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
            <div className="mono text-lg font-bold leading-none text-ink-900">{priceText}</div>
            <div className="mt-1 text-[11px] text-ink-400">预估花费</div>
          </div>
        )}
      </div>
    </li>
  );
}

/** 订单卡列表——多卡横向排一行（用户澄清：并排，不是竖排堆叠）。 */
function OrderCards({
  orders,
  nodes,
  nodeDetail,
}: {
  orders: Itinerary["orders"];
  nodes: ActivityNode[];
  nodeDetail: NodeDetailMap | null;
}) {
  return (
    <ul className={cn("grid gap-2", orders.length > 1 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1")}>
      {orders.map((o) => (
        <OrderCard key={o.order_id} order={o} nodes={nodes} nodeDetail={nodeDetail} />
      ))}
    </ul>
  );
}

// ============================================================
// ShareCluster —— "带走你的安排"分享簇（UI 修复批拍板方案A）
//
// 海报/TTS 与"是否已下单"无关（`PosterGenerator`/`TtsPlayer` 组件自身没有
// 任何 hasOrders 相关守卫，通读两个组件全文件确认），此前却被打包进
// `PlanActionBar`、随 `!hasOrders` 整块消失——一个变量身兼"该不该允许再次
// 确认"和"该不该显示整个工具行"两个不同粒度的门控，语义无关的功能被连坐
// 隐藏（诊断稿问题4根因）。这里把它们从 `PlanActionBar` 摘出来，归入订单卡
// 下方"带走你的安排"分享簇，和转发文案放一起——确认前后都能点，不再受
// `hasOrders` 影响。
//
// `showTools`：只在"已下单 && 未取消"时显示海报/TTS 工具行（转发文案则由
// 调用方按 `shareMessage` 是否非空独立控制）——确认前用户仍可通过
// `PlanActionBar` 里的"查看预约说明"预告了解海报，但海报/TTS 本身作为
// 独立工具挂在这里，只在方案已经落地（下单）后才在这个"带走你的安排"
// 簇里出现，避免方案还在草稿阶段就展示"带走"这类收尾语义的工具行。
// ============================================================

function ShareCluster({
  shareMessage,
  showTools,
}: {
  shareMessage?: string | null;
  showTools: boolean;
}) {
  return (
    <div className="space-y-2">
      {shareMessage && <ShareMessage text={shareMessage} />}
      {showTools && (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5">
            <Icons.share className="h-3.5 w-3.5 text-ink-500" strokeWidth={2} />
            <span className="text-xs font-medium tracking-tight text-ink-800">
              带走你的安排
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <TtsPlayer
              compact
              className="h-10 rounded-full border border-ink-300 bg-white px-3 text-sm font-semibold text-ink-700 transition hover:border-[#e6bc00] hover:bg-white"
            />
            <PosterGenerator
              compact
              className="h-10 rounded-full border border-ink-300 bg-white px-3 text-sm font-semibold text-ink-700 transition hover:border-[#e6bc00] hover:bg-white"
            />
          </div>
        </div>
      )}
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
              ? "bg-accent-500 text-white"
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
//
// 2026-07-06 收口（方案卡去冗余）：原来这里叠了三份声音——叙事正文 + 「为你
// 考虑了」intent chips（原 buildIntentChips）+「查看全部取舍说明」折叠
// （narrationMessages）。三者说的是同一件事（Agent 权衡了哪些约束），叙事
// 正文已经把取舍写进一句人话里，chips/折叠列表只是把同一份信息又摆了两遍，
// 一屏三个声音反而稀释了正文——删掉后者两个，只留 text 这一个唯一声音。
// chips 逻辑当时保留在 lib/intent-chips.ts 供"以后恢复"，但两端（桌面/移动）
// 一直没有再消费过——2026-07-12 代码卫生清理确认零引用后连同该文件一起删除；
// 如需恢复，从 git 历史找回即可，不必长期挂一份没人调用的代码。
// AgentNarrationMessage 类型仍在 lib/types.ts，未受影响。
// ============================================================

function NarrationBlock({
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
      className="animate-fade-in rounded-[28px] border border-black/[0.06] bg-white px-4 py-3.5 leading-relaxed tracking-tight shadow-[0_18px_46px_-38px_rgba(17,24,39,0.55)]"
      style={{ color: "rgb(31 41 55 / 0.92)" }}
    >
      {/* 外壳借用原「已根据反馈调整」卡的规整样式（白底/边框/圆角/阴影，无
          header）；确认态与 stream 态的区分收到 spark 图标深浅一层，不再靠
          背景色分家（用户拍板 2026-07-12：只要那张卡的外壳）。 */}
      <div className="flex items-start gap-2">
        <Icons.spark
          className={cn(
            "w-4 h-4 mt-1 shrink-0",
            isConfirm ? "text-accent-700" : "text-accent-600",
          )}
          strokeWidth={2}
        />
        <p className="whitespace-pre-wrap text-lg leading-relaxed">
          <HighlightText text={text} />
        </p>
      </div>
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
      className="rounded-[24px] border border-white/[0.76] bg-white px-5 py-4 text-sm leading-relaxed shadow-[inset_0_1px_0_rgba(255,255,255,0.88)]"
    >
      <p className="mb-3 text-base leading-relaxed text-ink-800">
        {restaurantLine}{extraLine}；再为你备好一段可一键复制的转发文案；最后{memoryLine}。
      </p>

      <div
        className={cn(
          "grid gap-3 text-sm font-semibold text-ink-600",
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

function PlanActionBar({
  intent,
  itinerary,
  canAct,
  canConfirm,
  confirmLabel,
  streaming,
  blockedByOwnerGuard,
  onCancel,
  onConfirm,
}: {
  intent: IntentExtraction | null;
  itinerary: Itinerary;
  canAct: boolean;
  canConfirm: boolean;
  confirmLabel: string;
  streaming: boolean;
  blockedByOwnerGuard: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [previewOpen, setPreviewOpen] = useState(false);

  return (
    <div className="relative z-[120] mt-3 w-full isolate">
      <div className="w-full">
        <div className="relative pointer-events-auto">
          {previewOpen && (
            <div className="absolute bottom-[calc(100%+14px)] right-0 z-[140] w-full max-w-[560px] overflow-hidden rounded-[30px] border border-white/[0.86] bg-white p-2 shadow-[0_28px_80px_-44px_rgba(17,24,39,0.78)] ring-1 ring-black/[0.035] animate-fade-in-up">
              <div className="flex items-center justify-between gap-3 px-3 py-2">
                <div className="flex items-center gap-2 text-base font-black tracking-tight text-ink-900">
                  <Info className="h-5 w-5 text-[#9a5b00]" strokeWidth={2.5} />
                  点击「确认并预约」之后
                </div>
                <button
                  type="button"
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-black/[0.06] bg-white/[0.72] text-ink-500 transition hover:bg-white hover:text-ink-900 active:scale-95"
                  onClick={() => setPreviewOpen(false)}
                  aria-label="关闭预约说明"
                >
                  <Icons.close className="h-4 w-4" strokeWidth={2.2} />
                </button>
              </div>
              <ConfirmPreviewCard intent={intent} itinerary={itinerary} />
            </div>
          )}

          {/* 海报/TTS 解连坐（UI 修复批拍板方案A）：这两个是"和是否下单无关"
              的常驻工具，此前打包在这个 grid 里，随 hasOrders 一起从 DOM 消失
              （父组件 `!hasOrders && !cancelled` 整块门控 PlanActionBar）。现在
              只保留"确认这个动作本身"的两个按钮（取消/确认），海报/TTS 挪到
              `ShareCluster`（"带走你的安排"分享簇），确认前后都能点，不再被
              `hasOrders` 连坐隐藏。 */}
          <div className="grid grid-cols-2 items-center gap-3">
            <button
              type="button"
              className={cn(
                "inline-flex h-12 w-full items-center justify-center gap-2 rounded-full border-2 px-4 text-lg font-bold tracking-tight shadow-[0_18px_42px_-30px_rgba(17,24,39,0.72)] backdrop-blur-2xl transition",
                "border-red-400/75 bg-white/[0.82] text-ink-600",
                "hover:border-red-500 hover:bg-white hover:text-red-600",
                "disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-red-400/75 disabled:hover:bg-white/[0.82] disabled:hover:text-ink-600",
              )}
              disabled={!canAct}
              onClick={onCancel}
              title="放弃当前方案 · 不写入长期记忆"
            >
              <Icons.close className="h-4 w-4" strokeWidth={2.4} />
              <span>取消方案</span>
            </button>
            <div
              className={cn(
                "flex h-12 w-full items-center overflow-hidden rounded-full border border-[#e6bc00]/45 bg-[#FFD100] text-ink-950 shadow-[0_18px_42px_-28px_rgba(245,158,11,0.9)] transition",
                streaming && "shimmer-border",
                !canConfirm && "opacity-60",
              )}
            >
              <button
                type="button"
                className="flex h-full min-w-0 flex-1 items-center justify-center gap-2 px-4 text-lg font-black tracking-tight disabled:cursor-not-allowed"
                disabled={!canConfirm}
                onClick={onConfirm}
                title={
                  blockedByOwnerGuard
                    ? "只有房间发起人可以确认预约"
                    : "确认后 Agent 会锁定餐厅时段、整理转发文案、写入本次偏好"
                }
              >
                {streaming ? (
                  <Icons.thinking className="h-4 w-4 animate-spin" />
                ) : (
                  <Icons.success className="h-4 w-4" strokeWidth={2.4} />
                )}
                <span className="truncate">{confirmLabel}</span>
              </button>
              <button
                type="button"
                className="mr-3 grid h-9 w-9 shrink-0 place-items-center bg-transparent text-[#8f4b00] transition hover:scale-105 hover:text-ink-950 active:scale-95"
                onClick={() => setPreviewOpen((open) => !open)}
                aria-label={previewOpen ? "收起预约说明" : "查看预约说明"}
                aria-expanded={previewOpen}
                title="查看确认后会发生什么"
              >
                <Info className="h-6 w-6" strokeWidth={2.5} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}


// ============================================================
// SwapAlternativesNotice —— 换菜备选收据（2026-07-11）：
// 挂在换菜结果叙事下方的一行小字，让用户知道"还有其它同类可换"，不进信任带。
// 全程中性墨色，禁 emoji（同任务规格「五收据」纪律）。
// ============================================================

function SwapAlternativesNotice({ count }: { count: number }) {
  return (
    <div className="flex items-center gap-1.5 text-xs font-medium text-ink-500">
      <ArrowLeftRight className="h-3.5 w-3.5 shrink-0 text-ink-400" strokeWidth={2} aria-hidden />
      <span>同类替补 {count} 家</span>
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
      // 去绿归色：这是"已写入记忆库"的成功/确认通知，和 CTA 一脉相承走暖金
      // （accent），不再用游离于调色板外的 emerald 绿。
      className="rounded-md border border-accent-500/24 bg-accent-500/6 px-3 py-2 text-xs text-accent-700/95 animate-fade-in backdrop-blur-sm flex items-start gap-2"
      style={{
        background:
          "linear-gradient(135deg, rgba(245,158,11,0.08) 0%, rgba(245,158,11,0.04) 100%)",
      }}
    >
      <Icons.spark
        className="w-3.5 h-3.5 mt-0.5 shrink-0 text-accent-500"
        strokeWidth={2}
      />
      <div className="flex-1 min-w-0">
        <div className="font-medium text-accent-700 tracking-tight">
          已写入「{socialContext || "本"}」场景的跨 session 召回库
        </div>
        <div className="text-accent-700/75 text-xs mt-0.5 line-clamp-1">
          {summaryPreview}
          <span className="text-accent-500/60 ml-1">·</span>
          <span className="text-accent-500/60 ml-1">
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

type StageTone = {
  gradient: string;
  color: string;
  softBg: string;
  border: string;
};

const HOME_STAGE_TONE: StageTone = {
  gradient: "linear-gradient(135deg, #ffd100 0%, #eab308 100%)",
  color: "#eab308",
  softBg: "rgba(255, 209, 0, 0.14)",
  border: "rgba(234, 179, 8, 0.3)",
};

function stageTone(index: number): StageTone {
  const tones: StageTone[] = [
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

function stageLineGradient(current: StageTone, next: StageTone | null): string {
  if (!next) {
    return `linear-gradient(to bottom, ${current.color} 0%, ${current.color} 68%, rgba(216, 210, 196, 0) 100%)`;
  }
  return `linear-gradient(to bottom, ${current.color} 0%, ${next.color} 100%)`;
}

function timelineRailGradient(nodeCount: number): string {
  const tones = [
    HOME_STAGE_TONE,
    ...Array.from({ length: Math.max(0, nodeCount) }, (_, index) => stageTone(index)),
    HOME_STAGE_TONE,
  ];
  const last = tones.length - 1;
  const stops = tones
    .map((tone, index) => `${tone.color} ${(index / last) * 100}%`)
    .join(", ");
  return `linear-gradient(to bottom, ${stops})`;
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
  // 幕③ 逐行 stagger 的 props 生成器（自上而下递增 delay）；只有真的渲染 gap 行
  // 时才调用它消费一个计数槽，返回 null 时不消费（保持与相邻行的顺序一致）。
  rowReveal: () => { className: string; style: CSSProperties | undefined },
  lineGradient?: string,
): ReactNode {
  if (!prev) return null;
  const gap = parseHHMM(curr.start) - parseHHMM(prev.end);
  if (!Number.isFinite(gap) || gap < FREE_GAP_THRESHOLD_MIN) return null;
  const rr = rowReveal();
  return (
    <li
      key={`gap-${idx}`}
      className={cn("relative flex items-center gap-0", rr.className)}
      style={rr.style}
      title={`${prev.end} → ${curr.start}`}
    >
      {/* 时间列留空（自由休息没有独立的起止钟点展示位，时长已经在内容里写出
          "N 分钟"）——占位保持四列对齐，不留空白会让下面这行内容跟节点/通勤
          行错位。 */}
      <div className="w-6 shrink-0" aria-hidden />
      {/* 同节点/通勤行共用一套脊柱段桥接（.timeline-spine-seg），不再另用
          border-l-2——避免脊柱旁边多一条平行线，和精修终稿§一「克制」冲突。 */}
      <div
        className="relative flex flex-col items-center min-w-[52px] shrink-0 self-stretch"
        style={
          lineGradient
            ? ({ "--timeline-line": lineGradient } as CSSProperties)
            : undefined
        }
      >
        <div aria-hidden className="timeline-spine-seg" />
      </div>
      <div className="w-4 shrink-0" aria-hidden />
      {/* pl-4 对齐节点卡内边距（.node-card px-4），和起终点过渡
          文字同一套左缩进口径（节点卡+行程轨-对比.html「过渡文字对齐」）。 */}
      <div className="flex-1 min-w-0 py-1.5 pl-4 text-xs text-ink-400 tracking-tight">
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
 * 通勤脊柱标的图标（时间轴精修终稿§二 + 节点卡+行程轨-对比.html 改版：
 * emoji 🚶🚕🚌 换线性 lucide 图标，去塑料感）——按 mode 分辨，不是设计稿示例里
 * 统一的一个图标：taxi/bus 若也套一个步行图标会误导通勤方式。
 * haversine_estimated 视为"步行距离估算"，图标仍用步行（Icons.footprints），
 * 另在文字里追加 translateHopMode 的"估算"后缀标出不确定性（承接旧版信息，
 * 不因换图标丢信息）。
 */
function hopIconComponent(mode: HopMode): LucideIcon {
  switch (mode) {
    case "taxi":
      return Icons.taxi;
    case "bus":
      return Icons.bus;
    case "walking":
    case "haversine_estimated":
    default:
      return Icons.footprints;
  }
}

/** node ref_id → ActivityNode.kind（用于 schedule 行展示左侧 chip）。 */
function nodeKindLabel(itinerary: Itinerary, ref_id: string): string {
  const n = itinerary.nodes?.find((x) => x.node_id === ref_id);
  return n?.kind ?? "活动";
}

/**
 * node ref_id → ActivityNode.note（schedule 没存 note，从 nodes 反查）。
 *
 * 主口径是 ScheduleEntry.ref_id === ActivityNode.node_id；同时兼容历史/异常数据
 * 把 target_id 塞进 ref_id 的情况，避免行程卡第三行说明被静默丢掉。
 */
function nodeDisplayNote(
  itinerary: Itinerary,
  ref_id: string,
  detail?: NodeDetail | null,
): string | null {
  const n = itinerary.nodes?.find(
    (x) => x.node_id === ref_id || x.target_id === ref_id,
  );
  const note = n?.note?.trim();
  if (note) return note;
  const fallback = detail?.recommendation_reason?.trim();
  return fallback ? fallback : null;
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
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-semibold tracking-tight",
        "border-black/[0.06] bg-white/[0.72] text-ink-700 shadow-[0_8px_18px_-16px_rgba(15,23,42,0.45)] backdrop-blur",
        "transition-[background-color,border-color,box-shadow,color,transform] duration-200",
        "hover:-translate-y-0.5 hover:border-accent-400/35 hover:bg-white/[0.92] hover:text-ink-900 hover:shadow-[0_14px_24px_-18px_rgba(146,101,0,0.45)]",
        "active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:translate-y-0 disabled:hover:border-black/[0.06] disabled:hover:bg-white/[0.72]",
      )}
    >
      <ArrowLeftRight className="h-4 w-4 shrink-0 text-ink-500" strokeWidth={2} />
      <span className="max-w-[12em] truncate">{primaryStoreName(alt.name)}</span>
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
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-semibold tracking-tight",
        "border-transparent bg-white/[0.42] text-ink-500 backdrop-blur",
        "transition-[background-color,border-color,color,transform] duration-200",
        "hover:-translate-y-0.5 hover:border-black/[0.06] hover:bg-white/[0.78] hover:text-ink-800",
        "active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:translate-y-0 disabled:hover:border-transparent disabled:hover:bg-white/[0.42]",
      )}
    >
      <SlidersHorizontal className="h-4 w-4 shrink-0 text-ink-400" strokeWidth={2} />
      {/* 定向微调 label 一般较短（"更近"/"更热闹"），同样给 max-width + truncate
          兜底（时间轴精修终稿§四"定向微调同理"），避免极端长 label 撑爆整行。 */}
      <span className="max-w-[8em] truncate">{chip.label}</span>
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
