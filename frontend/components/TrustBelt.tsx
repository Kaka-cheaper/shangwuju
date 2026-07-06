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
 */

import { useEffect, useMemo, useState } from "react";
import { Bot } from "lucide-react";

import { useChatStore } from "@/lib/store";
import { buildTrustBeltBeats, type TrustBeltBeat } from "@/lib/trust-belt";
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

  const reducedMotion = usePrefersReducedMotion();

  const [revealedCount, setRevealedCount] = useState(0);

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

  return (
    <div className="card overflow-hidden border border-amber-400/20 bg-gradient-to-br from-amber-50/60 to-white">
      <TrustBeltHeader streaming={streaming} />

      <div className="px-3 py-2">
        {revealed.length === 0 ? (
          <div className="flex h-7 items-center text-xs italic text-ink-400">
            等待 Agent 开始思考……
          </div>
        ) : (
          <div className="space-y-1.5">
            {revealed.map((beat, index) => (
              <div
                key={beat.id}
                className={cn(
                  "flex items-start",
                  !reducedMotion && "animate-trust-belt-enter",
                )}
              >
                <BeatLine beat={beat} ordinal={ordinalFor(index)} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TrustBeltHeader({ streaming }: { streaming: boolean }) {
  return (
    <div className="flex h-[26px] w-full items-center gap-1.5 border-b border-black/[0.06] px-3">
      <Bot className={cn("h-3 w-3 shrink-0", streaming ? "text-brand-600" : "text-ink-500")} strokeWidth={2} />
      <span className="text-xs font-semibold tracking-tight text-ink-700">AI 幕后</span>
      {streaming && (
        <span
          className="ml-0.5 inline-block h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-brand-500"
          aria-label="正在思考"
        />
      )}
    </div>
  );
}

function BeatLine({ beat, ordinal }: { beat: TrustBeltBeat; ordinal: string }) {
  return (
    <p
      className={cn(
        "text-xs leading-relaxed tracking-tight",
        beat.amber ? "font-semibold text-amber-700" : "text-ink-700",
      )}
    >
      <span className="mr-1 text-ink-400" aria-hidden>
        {ordinal}
      </span>
      {beat.text}
    </p>
  );
}
