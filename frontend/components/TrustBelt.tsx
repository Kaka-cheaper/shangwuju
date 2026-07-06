"use client";

/**
 * TrustBelt —— 信任带（AI 思考流）。
 *
 * 唯一权威规格：`路演PPT/信任带设计终稿.md`（只读）。把原来散在左栏的三个技术
 * 面板（ToolTracePanel「Agent 思考链路」/ ThoughtPanel「Agent 在想什么」/
 * DecisionTraceCard「决策链路」）合成**一条**第一人称思考流："听懂 → 干活 →
 * 自省自愈 → 定稿"。数据判定层在 `lib/trust-belt.ts`（不依赖 React，可独测），
 * 本文件只负责§七"形态与动效"：恒定 3 行高传送带 + 两模式（自动/手动）。
 *
 * Web + 移动端共用同一份组件（§落地清单 5：移动端同款替换）——组件自身从
 * useChatStore 读数据，不吃 props，挂载在哪个容器里都拿到同一份实时数据。
 *
 * 形态（§七，恒定高度，不因内容多少跳动）：
 *   header（26px，常驻标签"AI 幕后"，规划中带脉冲）
 *   + 3 行窗（84px = 28px × 3，恒定）
 *   ≈ 110px 总高
 *
 * 两模式：
 *   - 自动（默认）：底部锚定的传送带，新拍从底部淡入，旧拍上移，到顶经
 *     mask-image 遮罩淡出；队列 + 每拍最小驻留 800ms（可读节奏）。
 *   - 手动：悬停或点击 → 停自动，同一窗口内改为原生滚动，上下看全历史。
 *
 * 降级：prefers-reduced-motion 时关闭传送带，静态显示最新一拍，瞬切无动画。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Bot } from "lucide-react";

import { useChatStore } from "@/lib/store";
import { BEAT_ORDINAL, buildTrustBeltBeats, type TrustBeltBeat } from "@/lib/trust-belt";
import { cn } from "@/lib/utils";

const MIN_DWELL_MS = 800;
const VISIBLE_ROWS = 3;
const ROW_HEIGHT_PX = 28; // 对应 Tailwind h-7

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

  const reducedMotion = usePrefersReducedMotion();

  const [revealedCount, setRevealedCount] = useState(0);
  const [pinnedManual, setPinnedManual] = useState(false);
  const [hovering, setHovering] = useState(false);
  const manual = pinnedManual || hovering;
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // 新一轮重跑：beats 变短（store 清空重来）时同步回退计数，不残留上一轮多出的拍。
  useEffect(() => {
    setRevealedCount((c) => Math.min(c, beats.length));
  }, [beats.length]);

  // 队列 + 每拍最小驻留 800ms；减动效降级时直接显示到最新一拍；悬停/手动锁定时暂停推进。
  useEffect(() => {
    if (reducedMotion) {
      setRevealedCount(beats.length);
      return undefined;
    }
    if (manual) return undefined;
    if (revealedCount >= beats.length) return undefined;
    const timer = setTimeout(() => {
      setRevealedCount((c) => Math.min(c + 1, beats.length));
    }, MIN_DWELL_MS);
    return () => clearTimeout(timer);
  }, [beats.length, revealedCount, manual, reducedMotion]);

  // 进入手动模式时默认滚到底部（看最新内容），随后用户可自由上下滚全历史。
  useEffect(() => {
    if (manual && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [manual]);

  if (beats.length === 0 && !streaming) return null;

  const revealed = beats.slice(0, revealedCount);
  const latest = revealed[revealed.length - 1];

  return (
    <div className="card overflow-hidden border border-amber-400/20 bg-gradient-to-br from-amber-50/60 to-white">
      <TrustBeltHeader
        streaming={streaming}
        manual={manual}
        pinned={pinnedManual}
        onTogglePinned={() => setPinnedManual((v) => !v)}
      />

      {reducedMotion ? (
        // 降级：关传送带，静态显示最新一拍，瞬切无动画。
        <div className="flex h-[84px] items-center px-3">
          {latest ? (
            <BeatLine beat={latest} />
          ) : (
            <span className="text-xs italic text-ink-400">等待 Agent 开始思考……</span>
          )}
        </div>
      ) : (
        <div
          className="relative h-[84px]"
          onMouseEnter={() => setHovering(true)}
          onMouseLeave={() => setHovering(false)}
          style={{
            maskImage:
              "linear-gradient(to bottom, transparent 0, black 14px, black calc(100% - 14px), transparent 100%)",
            WebkitMaskImage:
              "linear-gradient(to bottom, transparent 0, black 14px, black calc(100% - 14px), transparent 100%)",
          }}
        >
          <div
            ref={scrollRef}
            className={cn(manual ? "h-full overflow-y-auto" : "h-full overflow-hidden")}
          >
            {revealed.length === 0 ? (
              <div className="flex h-[84px] items-center px-3 text-xs italic text-ink-400">
                等待 Agent 开始思考……
              </div>
            ) : (
              <div
                className={cn(
                  !manual &&
                    "transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)]",
                )}
                style={{
                  transform: manual
                    ? "translateY(0)"
                    : `translateY(-${Math.max(0, revealed.length - VISIBLE_ROWS) * ROW_HEIGHT_PX}px)`,
                }}
              >
                {revealed.map((beat) => (
                  <div
                    key={beat.id}
                    className="flex h-7 animate-trust-belt-enter items-center px-3"
                  >
                    <BeatLine beat={beat} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function TrustBeltHeader({
  streaming,
  manual,
  pinned,
  onTogglePinned,
}: {
  streaming: boolean;
  manual: boolean;
  pinned: boolean;
  onTogglePinned: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onTogglePinned}
      aria-pressed={pinned}
      title={pinned ? "点击恢复自动播放" : "点击锁定，上下滚动查看全部历史"}
      className="flex h-[26px] w-full items-center gap-1.5 border-b border-black/[0.06] px-3 text-left"
    >
      <Bot className={cn("h-3 w-3 shrink-0", streaming ? "text-brand-600" : "text-ink-500")} strokeWidth={2} />
      <span className="text-xs font-semibold tracking-tight text-ink-700">AI 幕后</span>
      {streaming && !manual && (
        <span
          className="ml-0.5 inline-block h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-brand-500"
          aria-label="正在思考"
        />
      )}
      {manual && (
        <span className="ml-auto text-xs font-medium text-amber-600">查看全部 ↕</span>
      )}
    </button>
  );
}

function BeatLine({ beat }: { beat: TrustBeltBeat }) {
  return (
    <p
      className={cn(
        "truncate text-xs leading-none tracking-tight",
        beat.amber ? "font-semibold text-amber-700" : "text-ink-700",
      )}
      title={beat.text}
    >
      <span className="mr-1 text-ink-400" aria-hidden>
        {BEAT_ORDINAL[beat.kind]}
      </span>
      {beat.text}
    </p>
  );
}
