"use client";

/**
 * TrustBelt —— 信任带（AI 思考流）。
 *
 * 唯一权威规格：`路演PPT/信任带设计终稿.md`（只读）。把原来散在左栏的三个技术
 * 面板（ToolTracePanel「Agent 思考链路」/ ThoughtPanel「Agent 在想什么」/
 * DecisionTraceCard「决策链路」）合成**一条**第一人称思考流："听懂 → 干活 →
 * 自省自愈 → 定稿"。数据判定层在 `lib/trust-belt.ts`（不依赖 React，可独测），
 * 本文件只负责§七"形态与动效"（见下方"修订"）。
 *
 * Web + 移动端共用同一份组件（§落地清单 5：移动端同款替换）——组件自身从
 * useChatStore 读数据，不吃 props，挂载在哪个容器里都拿到同一份实时数据。
 *
 * 修订（真机反馈后，`信任带设计终稿.md` 文末"修订"节 1-3，覆盖原 §七）：
 *   1. 圆圈序号 = 该拍在本轮实际序列里的位置（index+1），不再用固定角色号
 *      （①..⑦ 只是设计文档里的角色代号）。
 *   2. 长出全部拍：废除 3 行窗 + 传送带滚动。所有 revealed 拍顺序全展示，
 *      带高度随拍数自然长；保留每拍 800ms 逐条 revealed 的节奏 + 逐拍
 *      fade-in（animate-trust-belt-enter）。①拍永远可见。
 *      reduced-motion 降级＝全拍瞬显（无动画，非只显示最新一拍）。
 *   3. 删除「查看全部」+ 手动滚动模式：全拍可见后无需滚动。header 只留
 *      "AI 幕后" + 规划中脉冲。
 *
 * 修订（2026-07-10，②拍检索收据芯片）：见下方 SearchPreviewChipRow 及其调用处
 * 注释——芯片挂在②拍正文下方，不是新的一拍，七拍剪辑纪律不变。
 *
 * 修订（2026-07-11，五收据体系——见路演PPT/信任带设计终稿.md 同日修订）：
 * 收据是拍下挂的附件，不是新拍（同芯片行的既有纪律），全部中性墨色 + Lucide
 * 单线图标（禁 emoji）：
 *   - 画像收据（①拍下）：ProfileFieldsReceiptRow，虚线边框——与②拍芯片的
 *     实线边框区分"出处"（实线=查到的，虚线=记得的）。
 *   - 放宽重搜提示（②拍芯片行尾）：RelaxedSearchNoticeRow，琥珀教义不挪用，
 *     文字承担诚实。
 *   - 质检收据（⑦拍下）：ChecksRunReceiptRow，v1 不可点开（对齐芯片 v1 不可
 *     点击拍板）。
 *   - 记忆收据（⑦拍后独立行，晚到淡入）：MemoryReceiptRow——MEMORY_PERSISTED
 *     事件在 /chat/confirm 才推（见 store/event-handlers.ts），到达时间天然
 *     晚于 itinerary_ready/⑦拍，不需要额外延迟逻辑，只需不随 revealedCount
 *     判定门槛（它不是拍，没有 800ms 节奏）。
 *
 * 修订（2026-07-11，定稿后折叠脊柱——见路演PPT/信任带设计终稿.md 同日修订
 * "折叠脊柱"节）：**直播态不变**（本节不改上面两次修订确立的逐拍淡入全展示）。
 * ⑦拍 reveal 完成 + 停约 1 秒 → 收拢为一行横向脊柱（每节=原拍图标缩影+关键
 * 数字，见 `lib/trust-belt.ts::buildSpineNodes`），点击整体展开/再点收起。
 * 状态机见下方 `SpinePhase` 类型定义处的完整状态转移说明。**这不是推翻修订2
 * ——修订2 反对的是直播中途滚动让①拍不可见，保护的是直播态；折叠只发生在
 * 定稿之后，保护的是交付物台面主权，证据以脊柱形态常驻，不是被隐藏。**
 *
 * 修订（本批，⑦拍引擎自证收据——见路演PPT/信任带设计终稿.md 同批修订）：
 * ILS/rule 兜底成功从不回 critic（`build.py::_route_after_ils` 硬编码直通
 * finalize，防 ILS 死循环），换引擎成功收尾的局因此永远拿不到质检收据——
 * `EngineSelfCertificationReceiptRow`（`buildEngineSelfCertificationReceipt`
 * 判定，见该函数 docstring）在这种局的⑦拍下顶上"算法引擎按硬约束求解通过"，
 * 与质检收据互斥、同一槽位，视觉同族（ShieldCheck + 中性墨色，不挪用琥珀）。
 */

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  Bookmark,
  Bot,
  CheckCircle2,
  MapPin,
  MessageCircle,
  RefreshCw,
  Route,
  Search,
  ShieldCheck,
  TriangleAlert,
  User,
  UtensilsCrossed,
  type LucideIcon,
} from "lucide-react";

import { useChatStore } from "@/lib/store";
import {
  buildChecksRunReceipt,
  buildEngineSelfCertificationReceipt,
  buildProfileFieldsReceipt,
  buildRelaxedSearchNotice,
  buildSearchPreviewChips,
  buildSpineNodes,
  buildTrustBeltBeats,
  type ProfileFieldReceipt,
  type RelaxedSearchNotice,
  type SearchPreviewChip,
  type TrustBeltBeat,
  type TrustBeltBeatKind,
  type TrustBeltSpineNode,
} from "@/lib/trust-belt";
import { cn } from "@/lib/utils";

const MIN_DWELL_MS = 800;

// 序号按实际步数递增（修订1）：MAX_BEATS（lib/trust-belt.ts）上限为 7，
// 圆圈数字够用；超出防御性退化为阿拉伯数字。
const CIRCLED_ORDINALS = ["①", "②", "③", "④", "⑤", "⑥", "⑦"];

function ordinalFor(index: number): string {
  return CIRCLED_ORDINALS[index] ?? String(index + 1);
}

function usePrefersReducedMotion(): boolean {
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

// ============================================================
// 折叠脊柱状态机（2026-07-11）
//
// 状态：
//   live       —— 直播态：逐拍淡入全展示（修订2 既有行为，原样保留）。
//   spine      —— 脊柱态：收拢成一行横向图标链。
//   expanded   —— 用户点击脊柱后再次展开的完整带（视觉同 live，但不再走
//                 逐拍揭示节奏——方案早就定了，展开就该整条一次性可见）。
//
// 转移：
//   live → spine   ⑦拍 reveal 完成（revealedCount>=beats.length && itinerary
//                  就绪）+ 停留 ~1 秒 + 非 give_up/降级收尾 + 当前未悬停。
//   spine → expanded  点击脊柱任意处。
//   expanded → spine  再次点击。
//   任意态 → live   store 重置开始新一轮规划（itinerary 从非 null 变回 null，
//                  或 beats 整体清空重来）——见下方 useEffect。
//
// 例外（任务规格「折叠脊柱」原文）：
//   - give_up / 降级收尾（finalStrategy==="give_up"）不自动折叠——失败保留
//     的诚实证据应该一直摊在台面上，不该被"看起来完成了"的折叠态误导。
//   - 用户正悬停/滚动带上时推迟折叠——不能在用户还在看的时候把内容收走。
// ============================================================

// "collapsing" 是 live→spine 之间的短暂过渡态（非独立可长期停留的状态）：
// 渲染上与 spine 共用同一份脊柱视图（见下方 `isSpine` 判定），只是多给
// SpineRow 自己的 opacity+scale 进场动效（`animate-trust-belt-enter`，
// 300-400ms，只动 transform/opacity——任务规格「折叠脊柱」原文的动效硬约束）
// 留够播放时间再真正定格，而不是直接以终态出现。reduced-motion 时跳过这个
// 过渡态直接落地 spine（瞬切，见下方状态机 useEffect）。
type SpinePhase = "live" | "collapsing" | "spine" | "expanded";

const SPINE_COLLAPSE_DELAY_MS = 1000;
const SPINE_COLLAPSE_TRANSITION_MS = 350;

/** 拍种 → 脊柱图标（中性墨色图标，amber 由外层容器上色，不靠图标本身换色）。 */
const SPINE_ICON_BY_KIND: Record<TrustBeltBeatKind, LucideIcon> = {
  understanding: MessageCircle,
  search: Search,
  planning: Route,
  discover: TriangleAlert,
  fix: TriangleAlert,
  fallback: RefreshCw,
  done: CheckCircle2,
};

export default function TrustBelt() {
  const intent = useChatStore((s) => s.intent);
  const toolCalls = useChatStore((s) => s.toolCalls);
  const thoughts = useChatStore((s) => s.thoughts);
  const criticReport = useChatStore((s) => s.criticReport);
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);
  const memoryPersisted = useChatStore((s) => s.memoryPersisted);

  const beats = useMemo(
    () =>
      buildTrustBeltBeats({
        understanding: intent?.understanding ?? "",
        toolCalls,
        thoughts,
        criticReport,
        itineraryReady: itinerary != null,
        finalStrategy: itinerary?.decision_trace?.final_strategy ?? null,
      }),
    [intent, toolCalls, thoughts, criticReport, itinerary],
  );

  // ②拍检索收据芯片（2026-07-10）：纯数据剪辑在 lib/trust-belt.ts，这里只取用。
  const searchPreview = useMemo(() => buildSearchPreviewChips(toolCalls), [toolCalls]);

  // 五收据体系（2026-07-11）：画像/放宽重搜/质检三个收据的数据剪辑同样在
  // lib/trust-belt.ts；记忆收据数据源是 store.memoryPersisted 本身（无需剪辑，
  // 事件到达即是最终形态）。
  const profileFields = useMemo(() => buildProfileFieldsReceipt(toolCalls), [toolCalls]);
  const relaxedNotice = useMemo(() => buildRelaxedSearchNotice(toolCalls), [toolCalls]);
  const checksRun = useMemo(() => buildChecksRunReceipt(thoughts), [thoughts]);
  // 引擎自证收据（本批新增）：ILS/rule 兜底成功从不回 critic，checks_run 永
  // 不产生——换引擎成功收尾的局用这条收据顶上（与质检收据互斥，同一槽位）。
  const engineSelfCertified = useMemo(
    () =>
      buildEngineSelfCertificationReceipt({
        fallbackHops: criticReport.fallbackHops,
        checksRun,
        itineraryReady: itinerary != null,
        finalStrategy: itinerary?.decision_trace?.final_strategy ?? null,
      }),
    [criticReport.fallbackHops, checksRun, itinerary],
  );

  const reducedMotion = usePrefersReducedMotion();

  // 修复"规划完成后从头到尾又演一遍":TrustBelt 在 ItineraryCard 的"规划中
  // （itinerary 为 null）"与"方案就绪"两个分支各挂一个实例，itinerary null→
  // 非 null 切分支会卸载旧实例、挂载全新实例，revealedCount 若从 useState(0)
  // 起就被重置回 0 → 全弧从头逐拍重演。根治：挂载时方案已就绪（itinerary!=null）
  // 说明这是"就绪分支"的实例，直接初始化为全拍已显——逐拍揭示只是直播规划期
  // 的动效，方案定了就该显定稿全弧、不该再演一遍。规划中分支挂载时 itinerary
  // 为 null → 初始化 0，正常逐拍长出。
  const [revealedCount, setRevealedCount] = useState(() =>
    itinerary != null ? beats.length : 0,
  );

  // 芯片进场效果同样要遵守"挂载时方案已就绪＝瞬显不重演"（任务规格 §二"两个既有
  // 行为必须保持"之一）：这个 ref 只在组件挂载的第一刻求值一次，记录"这个实例
  // 是不是从就绪态诞生的"——不同于 `reducedMotion`（会话中途开关都要响应），
  // 这是"这个实例的出身"，故意不放进依赖数组、不随后续 itinerary 变化更新。
  const mountedReadyRef = useRef(itinerary != null);

  // 新一轮重跑：beats 变短（store 清空重来）时同步回退计数，不残留上一轮多出的拍。
  useEffect(() => {
    setRevealedCount((c) => Math.min(c, beats.length));
  }, [beats.length]);

  // 队列 + 每拍最小驻留 800ms；减动效降级时直接显示全部拍（瞬显，无逐条动画）。
  useEffect(() => {
    if (reducedMotion) {
      setRevealedCount(beats.length);
      return undefined;
    }
    if (revealedCount >= beats.length) return undefined;
    const timer = setTimeout(() => {
      setRevealedCount((c) => Math.min(c + 1, beats.length));
    }, MIN_DWELL_MS);
    return () => clearTimeout(timer);
  }, [beats.length, revealedCount, reducedMotion]);

  const itineraryReady = itinerary != null;
  const finalStrategy = itinerary?.decision_trace?.final_strategy ?? null;
  // §五"失败保留"收尾 / give_up 降级：不自动折叠（任务规格"折叠脊柱"例外）——
  // 失败保留的诚实证据该一直摊在台面上，不该被"看起来完成了"的折叠态误导。
  const isGiveUpEnding = finalStrategy === "give_up";
  const fullyRevealed = itineraryReady && revealedCount >= beats.length && beats.length > 0;

  const [phase, setPhase] = useState<SpinePhase>("live");
  const [hovering, setHovering] = useState(false);
  const beltRootRef = useRef<HTMLDivElement>(null);

  // live → spine：⑦拍 reveal 完成 + 停约 1 秒 + 非 give_up 收尾 + 当前未悬停。
  // 减动效降级时跳过"collapsing"过渡态直接落地 spine（瞬切，不播 300-400ms
  // 收拢动效）——折叠是"形态变化"本身不受 reduced-motion 影响（该发生仍发生），
  // 受影响的只是"怎么过渡"。
  useEffect(() => {
    if (phase !== "live") return undefined;
    if (!fullyRevealed || isGiveUpEnding || hovering) return undefined;
    const timer = setTimeout(() => {
      setPhase(reducedMotion ? "spine" : "collapsing");
    }, SPINE_COLLAPSE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [phase, fullyRevealed, isGiveUpEnding, hovering, reducedMotion]);

  // collapsing → spine：过渡动效播完（300-400ms）后卸载完整带只留脊柱。
  // 若用户在收拢过程中途悬停（"推迟折叠"覆盖这个过渡窗口，不只覆盖 live 态的
  // 1 秒等待）→ 中止收拢、退回 live，避免"鼠标刚移上去内容就在眼前收走"。
  useEffect(() => {
    if (phase !== "collapsing") return undefined;
    if (hovering) {
      setPhase("live");
      return undefined;
    }
    const timer = setTimeout(() => {
      setPhase("spine");
    }, SPINE_COLLAPSE_TRANSITION_MS);
    return () => clearTimeout(timer);
  }, [phase, hovering]);

  // 新一轮规划开始（store 重置）→ 回直播态：itinerary 从"已就绪"变回"未就绪"
  // 是重跑信号最直接的前端可观察结果（clearForReplanIfPending 把 itinerary
  // 置 null），据此把折叠/展开态都重置回 live，让新一轮从头逐拍演出。
  const prevItineraryReadyRef = useRef(itineraryReady);
  useEffect(() => {
    if (prevItineraryReadyRef.current && !itineraryReady) {
      setPhase("live");
    }
    prevItineraryReadyRef.current = itineraryReady;
  }, [itineraryReady]);

  const spineNodes = useMemo(
    () =>
      buildSpineNodes(beats, {
        searchTotalCount: searchPreview.chips.length + searchPreview.overflowCount,
        midNodeCount: itinerary?.nodes?.filter((n) => n.target_kind !== "home").length ?? 0,
        checksRun,
      }),
    [beats, searchPreview.chips.length, searchPreview.overflowCount, itinerary, checksRun],
  );

  if (beats.length === 0 && !streaming) return null;

  const revealed = beats.slice(0, revealedCount);
  const showPending = !reducedMotion && streaming && revealedCount < beats.length;
  const hasRows = revealed.length > 0 || showPending;
  // "collapsing" 与 "spine" 共用同一份渲染（脊柱视图）——过渡态只是给
  // SpineRow 自己的进场动效（opacity+scale，300-400ms，见 SpineRow 的
  // animate-trust-belt-enter）留够播放时间，不需要额外渲染一份"正在收拢中"
  // 的完整带叠在上面（两套 DOM 互相叠加的 grid crossfade 复杂度/高度对齐
  // 风险，与这里"只是让脊柱有个像样的入场"的实际诉求不成比例）。
  const isSpine = phase === "spine" || phase === "collapsing";

  return (
    <div
      ref={beltRootRef}
      className="card overflow-hidden rounded-[30px] border border-black/[0.06] bg-white"
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
    >
      <TrustBeltHeader streaming={streaming} />

      {isSpine ? (
        <SpineRow
          nodes={spineNodes}
          reducedMotion={reducedMotion}
          onExpand={() => setPhase("expanded")}
          memorySuccess={memoryPersisted?.success ?? false}
        />
      ) : (
      <div className="py-2.5 pl-5 pr-3.5">
        {phase === "expanded" && (
          <div className="mb-1.5 flex justify-end">
            <button
              type="button"
              onClick={() => setPhase("spine")}
              className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs text-ink-400 transition-colors hover:bg-black/[0.03] hover:text-ink-700"
              title="收起为脊柱视图"
            >
              收起
            </button>
          </div>
        )}
        {!hasRows ? (
          <div className="flex h-10 items-center gap-2 text-base font-semibold leading-snug text-ink-900">
            <span
              aria-hidden
              className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-[#FFD100]/35 border-t-[#f59e0b]"
            />
            <span>AI 正在思考中，马上就好~</span>
          </div>
        ) : (
          <div className="space-y-1">
            {revealed.map((beat, index) => {
              const isLastRow = index === revealed.length - 1 && !showPending;
              const withChips = beat.kind === "search" && searchPreview.chips.length > 0;
              const withProfileReceipt = beat.kind === "understanding" && profileFields.length > 0;
              const withRelaxedNotice = beat.kind === "search" && relaxedNotice != null;
              const withChecksReceipt = beat.kind === "done" && checksRun != null;
              // 引擎自证收据（本批新增）：与质检收据互斥，同一⑦拍槽位——
              // engineSelfCertified 已经把"有质检收据时不成立"这条纳入判定
              // （见 buildEngineSelfCertificationReceipt），这里不需要再重复
              // 排除 withChecksReceipt，但写出来便于读者一眼确认互斥关系。
              const withEngineSelfCertification =
                beat.kind === "done" && !withChecksReceipt && engineSelfCertified;
              // 附件行是否需要接一段连线（同②拍芯片行的既有纪律：非最后一行时
              // 补连线，别让附件打断相邻拍之间的时间轴视觉）——一拍可能挂
              // 多个附件（如②拍同时有芯片+放宽提示），除最后一个附件外都要连线。
              const attachmentCount =
                (withChips ? 1 : 0) +
                (withRelaxedNotice ? 1 : 0) +
                (withProfileReceipt ? 1 : 0) +
                (withChecksReceipt ? 1 : 0) +
                (withEngineSelfCertification ? 1 : 0);
              let attachmentsRendered = 0;
              const isLastAttachment = () => {
                attachmentsRendered += 1;
                return attachmentsRendered >= attachmentCount;
              };
              return (
                <Fragment key={beat.id}>
                  <div
                    className={cn(
                      "relative flex items-stretch gap-2.5",
                      !reducedMotion && "animate-trust-belt-enter",
                    )}
                  >
                    <SequenceMarker accent={beat.amber} isLast={isLastRow && attachmentCount === 0} />
                    <BeatLine beat={beat} />
                  </div>
                  {/* ①拍画像收据（2026-07-11）：紧跟①拍正文下方——同②拍芯片
                      "附件钉在它所属的拍下面"的既有纪律，不是新的一拍。虚线
                      边框（与②拍芯片的实线边框区分出处：实线=查到的，虚线=
                      记得的）。 */}
                  {withProfileReceipt && (
                    <AttachmentRow isLastRow={isLastRow && isLastAttachment()}>
                      <ProfileFieldsReceiptRow fields={profileFields} />
                    </AttachmentRow>
                  )}
                  {/* ②拍检索收据芯片：**紧跟②拍正文下方**渲染（对抗审查修复
                      2026-07-10：原实现把芯片行放在整个 revealed 列表之后，③④⑤⑦
                      揭示后它会沉到带底部——附件必须钉在它所属的拍下面）。左侧
                      占位列与 SequenceMarker 同宽让芯片行与拍正文左对齐；②拍
                      不是最后一行时补一段连线，别让芯片行打断相邻拍之间的时间轴
                      视觉（芯片是②拍附件，不是新的一拍，不占圆点不占序号）。 */}
                  {withChips && (
                    <AttachmentRow isLastRow={isLastRow && isLastAttachment()}>
                      <SearchPreviewChipRow
                        chips={searchPreview.chips}
                        overflowCount={searchPreview.overflowCount}
                        instant={reducedMotion || mountedReadyRef.current}
                      />
                    </AttachmentRow>
                  )}
                  {/* 放宽重搜提示（2026-07-11）：②拍芯片行尾追加一行——守琥珀
                      教义（④⑤⑥自愈拍专属），这里全程中性墨色，文字本身承担
                      诚实（"安静"不够，放宽后补到 N 家）。 */}
                  {withRelaxedNotice && relaxedNotice && (
                    <AttachmentRow isLastRow={isLastRow && isLastAttachment()}>
                      <RelaxedSearchNoticeRow notice={relaxedNotice} />
                    </AttachmentRow>
                  )}
                  {/* ⑦拍质检收据（2026-07-11）：紧跟⑦拍正文下方，v1 不可点开
                      （对齐芯片 v1 不可点击拍板）。 */}
                  {withChecksReceipt && checksRun != null && (
                    <AttachmentRow isLastRow={isLastRow && isLastAttachment()}>
                      <ChecksRunReceiptRow count={checksRun} />
                    </AttachmentRow>
                  )}
                  {/* ⑦拍引擎自证收据（本批新增）：ILS/rule 兜底成功从不回
                      critic，checks_run 永不产生——换引擎成功收尾的局用这条
                      顶上，与质检收据互斥、同一槽位（见
                      buildEngineSelfCertificationReceipt docstring）。视觉
                      与质检收据同族（ShieldCheck 图标、中性墨色），不挪用
                      琥珀（琥珀仍是④⑤⑥自愈拍专属）。 */}
                  {withEngineSelfCertification && (
                    <AttachmentRow isLastRow={isLastRow && isLastAttachment()}>
                      <EngineSelfCertificationReceiptRow />
                    </AttachmentRow>
                  )}
                </Fragment>
              );
            })}
            {showPending && (
              <div className="relative flex min-h-6 items-center gap-2.5">
                <PendingMarker />
                <span className="h-2.5 w-24 rounded-full bg-ink-100/80 animate-pulse" />
              </div>
            )}
            {/* 记忆收据（2026-07-11）：⑦拍后独立行，晚到淡入——MEMORY_PERSISTED
                只在 /chat/confirm 才推（见 store/event-handlers.ts），到达时间
                天然晚于 itinerary_ready/⑦拍，不吃 revealedCount 的 800ms 节奏
                门槛（它不是拍）。success=false 不显示（任务规格原文）。 */}
            {memoryPersisted?.success && (
              <div className="relative flex items-stretch gap-2.5 animate-trust-belt-enter">
                <span aria-hidden className="relative flex w-4 shrink-0 justify-center" />
                <MemoryReceiptRow
                  socialContext={memoryPersisted.socialContext}
                  summaryPreview={memoryPersisted.summaryPreview}
                />
              </div>
            )}
          </div>
        )}
      </div>
      )}
    </div>
  );
}

/** 附件行外壳：左侧占位列与 SequenceMarker 同宽对齐正文，非最后一行时补连线
 * （同②拍芯片行既有的连线手法，抽成共享外壳供①②⑦三处收据复用）。 */
function AttachmentRow({
  isLastRow,
  children,
}: {
  isLastRow: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="relative flex items-stretch gap-2.5">
      <span aria-hidden className="relative flex w-4 shrink-0 justify-center">
        {!isLastRow && (
          <span className="absolute bottom-[-0.25rem] top-[-0.25rem] w-px bg-gradient-to-b from-ink-300/70 via-ink-200/55 to-transparent" />
        )}
      </span>
      {children}
    </div>
  );
}

/**
 * 脊柱行（2026-07-11）：定稿后收拢态——一行横向图标链，点击任意处整体展开
 * 回完整带。琥珀节保持琥珀（对象恒存，见任务规格）；记忆收据晚到时落在
 * 脊柱尾部淡入（`memorySuccess` 为 true 时追加一个 Bookmark 尾节点）。
 * 高度收拢/该行本身的进场都只动 transform/opacity（`animate-trust-belt-enter`
 * 复用既有关键帧），reduced-motion 时瞬切不做动画。
 */
function SpineRow({
  nodes,
  reducedMotion,
  onExpand,
  memorySuccess,
}: {
  nodes: TrustBeltSpineNode[];
  reducedMotion: boolean;
  onExpand: () => void;
  memorySuccess: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onExpand}
      title="点击展开完整思考过程"
      className={cn(
        "flex w-full items-center gap-2 overflow-x-auto px-4 py-2.5 text-left",
        "transition-colors duration-200 hover:bg-black/[0.015]",
        !reducedMotion && "animate-trust-belt-enter",
      )}
    >
      {nodes.map((node, index) => (
        <Fragment key={node.id}>
          {index > 0 && <span aria-hidden className="h-px w-3 shrink-0 bg-ink-200/70" />}
          <SpineNodeChip node={node} />
        </Fragment>
      ))}
      {memorySuccess && (
        <>
          {nodes.length > 0 && <span aria-hidden className="h-px w-3 shrink-0 bg-ink-200/70" />}
          <span
            className={cn(
              "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-black/[0.06] bg-white",
              !reducedMotion && "animate-trust-belt-enter",
            )}
          >
            <Bookmark className="h-3 w-3 text-ink-400" strokeWidth={2} aria-hidden />
          </span>
        </>
      )}
    </button>
  );
}

function SpineNodeChip({ node }: { node: TrustBeltSpineNode }) {
  const Icon = SPINE_ICON_BY_KIND[node.kind];
  return (
    <span
      className={cn(
        "inline-flex h-6 shrink-0 items-center gap-1 rounded-full border px-2",
        node.amber
          ? "border-[#d89a00]/25 bg-[#fff5bf]/55 text-[#9a5b00]"
          : "border-black/[0.06] bg-white text-ink-500",
      )}
    >
      <Icon className="h-3 w-3 shrink-0" strokeWidth={2} aria-hidden />
      {node.count != null && (
        <span className="text-xs font-semibold tabular-nums">{node.count}</span>
      )}
    </span>
  );
}

function TrustBeltHeader({ streaming }: { streaming: boolean }) {
  return (
    <div className="flex h-11 w-full items-center gap-2 border-b border-black/[0.06] px-4">
      <Bot className={cn("h-5 w-5 shrink-0", streaming ? "text-accent-600" : "text-ink-600")} strokeWidth={2} />
      <span className="text-lg font-black tracking-tight text-ink-900">AI 幕后</span>
      {streaming && (
        <span
          className="ml-0.5 inline-block h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent-500"
          aria-label="正在思考"
        />
      )}
    </div>
  );
}

function SequenceMarker({ accent, isLast }: { accent: boolean; isLast: boolean }) {
  const background = accent
    ? "radial-gradient(circle, rgba(180,83,9,0.98) 0%, rgba(217,119,6,0.84) 38%, rgba(217,119,6,0.34) 70%, rgba(217,119,6,0.08) 100%)"
    : "radial-gradient(circle, rgba(15,23,42,0.96) 0%, rgba(15,23,42,0.82) 34%, rgba(15,23,42,0.34) 68%, rgba(15,23,42,0.08) 100%)";

  return (
    <span
      aria-hidden
      className="relative flex w-4 shrink-0 justify-center self-stretch pt-[0.48rem]"
    >
      {!isLast && (
        <span className="absolute bottom-[-0.25rem] top-[1rem] w-px bg-gradient-to-b from-ink-300/70 via-ink-200/55 to-transparent" />
      )}
      <span
        className="relative z-10 h-2.5 w-2.5 rounded-full border border-white shadow-[0_0_0_3px_rgba(15,23,42,0.05)]"
        style={{ background }}
      />
    </span>
  );
}

function PendingMarker() {
  return (
    <span
      aria-hidden
      className="relative flex w-4 shrink-0 justify-center"
    >
      <span className="h-3 w-3 animate-spin rounded-full border border-ink-300/70 border-t-ink-900" />
    </span>
  );
}

function BeatLine({ beat }: { beat: TrustBeltBeat }) {
  return (
    <p
      className={cn(
        "min-w-0 flex-1 pb-0.5 text-base leading-snug tracking-tight",
        beat.amber ? "font-bold text-[#b45309]" : "text-ink-700",
      )}
    >
      {beat.text}
    </p>
  );
}

// ============================================================
// ②拍检索收据芯片（2026-07-10）：真实召回候选的小药丸，让评委看见"它真查到了
// 什么"。全程中性墨色——琥珀色是④⑤⑥自愈拍专属重音，这里不用。芯片不可点击
// （v1 拍板），不加 hover 手型/态。
// ============================================================

const CHIP_STAGGER_START_MS = 300;
const CHIP_STAGGER_STEP_MS = 70;

function SearchPreviewChipRow({
  chips,
  overflowCount,
  instant,
}: {
  chips: SearchPreviewChip[];
  overflowCount: number;
  instant: boolean;
}) {
  return (
    <div className="min-w-0 flex-1 pb-1.5 pt-0.5">
      <div className="flex flex-wrap gap-1.5">
        {chips.map((chip, index) => (
          <SearchPreviewChipPill
            key={`${chip.kind}-${chip.name}-${index}`}
            chip={chip}
            delayMs={instant ? 0 : CHIP_STAGGER_START_MS + index * CHIP_STAGGER_STEP_MS}
            instant={instant}
          />
        ))}
        {overflowCount > 0 && (
          <OverflowBadge
            count={overflowCount}
            delayMs={instant ? 0 : CHIP_STAGGER_START_MS + chips.length * CHIP_STAGGER_STEP_MS}
            instant={instant}
          />
        )}
      </div>
    </div>
  );
}

function SearchPreviewChipPill({
  chip,
  delayMs,
  instant,
}: {
  chip: SearchPreviewChip;
  delayMs: number;
  instant: boolean;
}) {
  const Icon = chip.kind === "restaurant" ? UtensilsCrossed : MapPin;
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded-full border border-black/[0.06] bg-white px-2",
        !instant && "animate-trust-belt-chip-enter",
      )}
      style={instant ? undefined : { animationDelay: `${delayMs}ms` }}
    >
      <Icon className="h-3 w-3 shrink-0 text-ink-400" strokeWidth={2} aria-hidden />
      {/* 长名截断：max-width ~7-8em（任务规格），em 相对 text-xs 自身字号，
          比固定 rem 更贴合"这段文字能装几个字"的直觉。 */}
      <span className="min-w-0 max-w-[7.5em] truncate text-xs font-medium text-ink-700">
        {chip.name}
      </span>
      <span className="shrink-0 text-xs tabular-nums text-ink-400">{chip.rating.toFixed(1)}</span>
    </span>
  );
}

function OverflowBadge({
  count,
  delayMs,
  instant,
}: {
  count: number;
  delayMs: number;
  instant: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center rounded-full bg-ink-50 px-2 text-xs text-ink-500",
        !instant && "animate-trust-belt-chip-enter",
      )}
      style={instant ? undefined : { animationDelay: `${delayMs}ms` }}
    >
      +{count}
    </span>
  );
}

// ============================================================
// ①拍画像收据（2026-07-11）：本局真被 field_provenance 标为 prior 的画像
// 字段——虚线边框（与②拍芯片的实线边框区分出处：实线=查到的，虚线=记得的）。
// 全程中性墨色，禁 emoji。
// ============================================================

function ProfileFieldsReceiptRow({ fields }: { fields: ProfileFieldReceipt[] }) {
  return (
    <div className="min-w-0 flex-1 pb-1.5 pt-0.5">
      <div className="flex flex-wrap gap-1.5">
        {fields.map((f) => (
          <span
            key={f.field}
            className="inline-flex h-6 items-center gap-1 rounded-full border border-dashed border-black/[0.14] bg-white px-2"
          >
            <User className="h-3 w-3 shrink-0 text-ink-400" strokeWidth={2} aria-hidden />
            <span className="text-xs font-medium text-ink-700">
              {f.label}：{f.tags.join("、")}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

// ============================================================
// 放宽重搜提示（2026-07-11）：②拍芯片行尾追加一行——琥珀色仍是④⑤⑥自愈拍
// 专属重音，这里全程中性墨色，文字本身承担诚实。
// ============================================================

function RelaxedSearchNoticeRow({ notice }: { notice: RelaxedSearchNotice }) {
  return (
    <div className="min-w-0 flex-1 pb-1.5 pt-0.5">
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-500">
        <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-ink-400" strokeWidth={2} aria-hidden />
        <span>
          「{notice.tag}」不够，放宽后补到 {notice.count} 家
        </span>
      </span>
    </div>
  );
}

// ============================================================
// ⑦拍质检收据（2026-07-11）：v1 不可点开（对齐芯片 v1 不可点击拍板）。
// ============================================================

function ChecksRunReceiptRow({ count }: { count: number }) {
  return (
    <div className="min-w-0 flex-1 pb-1.5 pt-0.5">
      <span className="inline-flex h-6 items-center gap-1 rounded-full border border-black/[0.06] bg-white px-2 text-xs font-medium text-ink-500">
        <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-ink-400" strokeWidth={2} aria-hidden />
        <span>{count} 项体检 ✓</span>
      </span>
    </div>
  );
}

// ============================================================
// ⑦拍引擎自证收据（本批新增）：与质检收据互斥同槽位，视觉同族（ShieldCheck
// 图标、中性墨色），文案逐字锁定——见 buildEngineSelfCertificationReceipt
// docstring 的判定条件与理由。
// ============================================================

function EngineSelfCertificationReceiptRow() {
  return (
    <div className="min-w-0 flex-1 pb-1.5 pt-0.5">
      <span className="inline-flex h-6 items-center gap-1 rounded-full border border-black/[0.06] bg-white px-2 text-xs font-medium text-ink-500">
        <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-ink-400" strokeWidth={2} aria-hidden />
        <span>算法引擎按硬约束求解通过</span>
      </span>
    </div>
  );
}

// ============================================================
// 记忆收据（2026-07-11）：⑦拍后独立行，晚到淡入（MEMORY_PERSISTED 只在
// /chat/confirm 才推，到达时间天然晚于⑦拍）。
// ============================================================

function MemoryReceiptRow({
  socialContext,
  summaryPreview,
}: {
  socialContext: string;
  summaryPreview: string;
}) {
  return (
    <div className="min-w-0 flex-1 pb-0.5 pt-0.5">
      <span className="inline-flex items-start gap-1.5 text-xs font-medium text-ink-500">
        <Bookmark className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-400" strokeWidth={2} aria-hidden />
        <span className="min-w-0">
          记住了：{socialContext ? `「${socialContext}」场景` : ""}
          {summaryPreview ? ` · ${summaryPreview}` : ""}
        </span>
      </span>
    </div>
  );
}
