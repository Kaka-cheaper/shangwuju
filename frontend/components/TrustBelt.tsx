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
 */

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Bot, MapPin, UtensilsCrossed } from "lucide-react";

import { useChatStore } from "@/lib/store";
import {
  buildSearchPreviewChips,
  buildTrustBeltBeats,
  type SearchPreviewChip,
  type TrustBeltBeat,
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

export default function TrustBelt() {
  const intent = useChatStore((s) => s.intent);
  const toolCalls = useChatStore((s) => s.toolCalls);
  const thoughts = useChatStore((s) => s.thoughts);
  const criticReport = useChatStore((s) => s.criticReport);
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);

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

  if (beats.length === 0 && !streaming) return null;

  const revealed = beats.slice(0, revealedCount);
  const showPending = !reducedMotion && streaming && revealedCount < beats.length;
  const hasRows = revealed.length > 0 || showPending;

  return (
    <div className="card overflow-hidden rounded-[30px] border border-black/[0.06] bg-white">
      <TrustBeltHeader streaming={streaming} />

      <div className="py-2.5 pl-5 pr-3.5">
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
              return (
                <Fragment key={beat.id}>
                  <div
                    className={cn(
                      "relative flex items-stretch gap-2.5",
                      !reducedMotion && "animate-trust-belt-enter",
                    )}
                  >
                    <SequenceMarker accent={beat.amber} isLast={isLastRow} />
                    <BeatLine beat={beat} />
                  </div>
                  {/* ②拍检索收据芯片：**紧跟②拍正文下方**渲染（对抗审查修复
                      2026-07-10：原实现把芯片行放在整个 revealed 列表之后，③④⑤⑦
                      揭示后它会沉到带底部——附件必须钉在它所属的拍下面）。左侧
                      占位列与 SequenceMarker 同宽让芯片行与拍正文左对齐；②拍
                      不是最后一行时补一段连线，别让芯片行打断相邻拍之间的时间轴
                      视觉（芯片是②拍附件，不是新的一拍，不占圆点不占序号）。 */}
                  {withChips && (
                    <div className="relative flex items-stretch gap-2.5">
                      <span aria-hidden className="relative flex w-4 shrink-0 justify-center">
                        {!isLastRow && (
                          <span className="absolute bottom-[-0.25rem] top-[-0.25rem] w-px bg-gradient-to-b from-ink-300/70 via-ink-200/55 to-transparent" />
                        )}
                      </span>
                      <SearchPreviewChipRow
                        chips={searchPreview.chips}
                        overflowCount={searchPreview.overflowCount}
                        instant={reducedMotion || mountedReadyRef.current}
                      />
                    </div>
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
          </div>
        )}
      </div>
    </div>
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
