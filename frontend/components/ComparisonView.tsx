"use client";

/**
 * ComparisonView —— Refine 前后对比视图（spec R3）。
 *
 * 设计动机：
 *   评委想看到 Agent 根据用户反馈做了什么调整。
 *   ItineraryCard 已通过 RefinementSummaryBanner 展示「已调整字段」摘要，
 *   但缺乏新旧方案的视觉并排对比——本组件填这个空白。
 *
 * 触发条件（同时满足）：
 *   - store.itinerary 非空（当前方案已就绪）
 *   - store.previousItinerary 非空（上一次有方案，refine 前已快照）
 *   - store.lastRefinement 非空（这是 refine 后的结果，不是 fresh 重新规划）
 *
 * 位置：ItineraryCard 内部 header 下方、narration 之前。
 * 默认状态：展开（refine 后立刻展示对比给用户看）。
 *
 * 数据契约：
 *   - Itinerary.stages: ItineraryStage[]
 *   - ItineraryStage: { kind, start, end, title, poi_id?, restaurant_id?, note? }
 *
 * Diff 算法：
 *   按 stage 索引对齐（不做 LCS / 模糊匹配），逐字段比较 start/end/title/kind。
 *   不同字段加 amber 高亮。新增/删除段加专门的 「新增」/「已移除」 占位。
 */

import { useState } from "react";
import { ChevronDown, GitCompare } from "lucide-react";

import type { Itinerary, ItineraryStage } from "@/lib/types";
import { cn } from "@/lib/utils";

// ============================================================
// Diff 算法
// ============================================================

type StageDiffKind = "unchanged" | "modified" | "added" | "removed";

interface StageDiff {
  oldStage: ItineraryStage | null;
  newStage: ItineraryStage | null;
  kind: StageDiffKind;
  /** 在 modified 时具体变化的字段名（用于高亮） */
  changedFields: ReadonlyArray<"time" | "title" | "kind">;
}

function diffStages(
  oldStages: ReadonlyArray<ItineraryStage>,
  newStages: ReadonlyArray<ItineraryStage>,
): StageDiff[] {
  const maxLen = Math.max(oldStages.length, newStages.length);
  const diffs: StageDiff[] = [];
  for (let i = 0; i < maxLen; i++) {
    const oldStage = oldStages[i] ?? null;
    const newStage = newStages[i] ?? null;
    if (!oldStage && newStage) {
      diffs.push({ oldStage: null, newStage, kind: "added", changedFields: [] });
      continue;
    }
    if (oldStage && !newStage) {
      diffs.push({ oldStage, newStage: null, kind: "removed", changedFields: [] });
      continue;
    }
    if (!oldStage || !newStage) continue; // TS 缩窄

    const changed: Array<"time" | "title" | "kind"> = [];
    if (oldStage.start !== newStage.start || oldStage.end !== newStage.end) {
      changed.push("time");
    }
    if (oldStage.title !== newStage.title) changed.push("title");
    if (oldStage.kind !== newStage.kind) changed.push("kind");

    diffs.push({
      oldStage,
      newStage,
      kind: changed.length > 0 ? "modified" : "unchanged",
      changedFields: changed,
    });
  }
  return diffs;
}

// ============================================================
// 主组件
// ============================================================

interface ComparisonViewProps {
  oldItinerary: Itinerary;
  newItinerary: Itinerary;
}

export default function ComparisonView({
  oldItinerary,
  newItinerary,
}: ComparisonViewProps) {
  const [expanded, setExpanded] = useState(true);
  const diffs = diffStages(oldItinerary.stages, newItinerary.stages);
  const changedCount = diffs.filter((d) => d.kind !== "unchanged").length;

  return (
    <div
      className={cn(
        "px-4 py-3 border-b border-white/[0.06]",
        "bg-gradient-to-r from-accent-500/5 to-brand-500/5",
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 text-left"
        aria-expanded={expanded}
      >
        <GitCompare
          className="w-3.5 h-3.5 text-accent-400 shrink-0"
          strokeWidth={2}
        />
        <span className="text-[12px] font-medium text-ink-900 tracking-tight">
          调整对比
        </span>
        <span className="text-[10px] mono text-ink-500 tabular-nums">
          {changedCount} 处变化
        </span>
        <span className="ml-auto text-[10px] text-ink-500">
          {expanded ? "收起对比" : "展开对比"}
        </span>
        <ChevronDown
          className={cn(
            "w-3.5 h-3.5 text-ink-500 shrink-0 transition-transform duration-200",
            !expanded && "-rotate-90",
          )}
          strokeWidth={2.5}
        />
      </button>

      {expanded && (
        <div className="mt-2.5 grid grid-cols-2 gap-3 animate-collapse-in">
          {/* 旧方案列 */}
          <div>
            <div className="text-[10px] tracking-wider uppercase text-ink-500 mb-1.5 flex items-center gap-1">
              <span className="inline-block w-1 h-1 rounded-full bg-ink-500/60" />
              <span>调整前</span>
            </div>
            <ul className="space-y-1.5">
              {diffs.map((d, idx) => (
                <li key={`old-${idx}`}>
                  <StageRow
                    stage={d.oldStage}
                    diffKind={d.kind}
                    side="old"
                    changedFields={d.changedFields}
                  />
                </li>
              ))}
            </ul>
          </div>

          {/* 新方案列 */}
          <div>
            <div className="text-[10px] tracking-wider uppercase text-accent-300 mb-1.5 flex items-center gap-1">
              <span className="inline-block w-1 h-1 rounded-full bg-accent-400" />
              <span>调整后</span>
            </div>
            <ul className="space-y-1.5">
              {diffs.map((d, idx) => (
                <li key={`new-${idx}`}>
                  <StageRow
                    stage={d.newStage}
                    diffKind={d.kind}
                    side="new"
                    changedFields={d.changedFields}
                  />
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// 单段渲染
// ============================================================

function StageRow({
  stage,
  diffKind,
  side,
  changedFields,
}: {
  stage: ItineraryStage | null;
  diffKind: StageDiffKind;
  side: "old" | "new";
  changedFields: ReadonlyArray<"time" | "title" | "kind">;
}) {
  // 占位标记
  if (!stage) {
    if (diffKind === "added" && side === "old") {
      return (
        <PlaceholderRow
          label="新增"
          colorClass="bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
        />
      );
    }
    if (diffKind === "removed" && side === "new") {
      return (
        <PlaceholderRow
          label="已移除"
          colorClass="bg-rose-500/10 border-rose-500/30 text-rose-300"
        />
      );
    }
    return null; // 不应到这（防御）
  }

  // 是否高亮整行（modified 在新侧 + added 在新侧 + removed 在旧侧）
  const highlight =
    (diffKind === "modified" && side === "new") ||
    (diffKind === "added" && side === "new") ||
    (diffKind === "removed" && side === "old");

  const timeChanged = changedFields.includes("time");
  const titleChanged = changedFields.includes("title");
  const kindChanged = changedFields.includes("kind");

  return (
    <div
      className={cn(
        "rounded-md border px-2 py-1.5 text-[11px] transition-colors",
        highlight
          ? "border-amber-500/30 bg-amber-500/5"
          : "border-white/[0.06] bg-white/[0.02]",
      )}
    >
      <div className="flex items-center gap-1.5 mb-0.5">
        <span
          className={cn(
            "mono text-[10px] tabular-nums",
            timeChanged ? "text-amber-300 font-semibold" : "text-ink-500",
          )}
        >
          {stage.start}-{stage.end}
        </span>
        <span
          className={cn(
            "px-1 py-0 rounded text-[9px] border",
            kindChanged
              ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
              : "border-white/[0.06] bg-white/[0.04] text-ink-700",
          )}
        >
          {stage.kind}
        </span>
      </div>
      <div
        className={cn(
          "text-[12px] tracking-tight",
          titleChanged ? "text-amber-200 font-medium" : "text-ink-800",
        )}
      >
        {stage.title}
      </div>
    </div>
  );
}

function PlaceholderRow({
  label,
  colorClass,
}: {
  label: string;
  colorClass: string;
}) {
  return (
    <div
      className={cn(
        "rounded-md border px-2 py-1.5 text-[11px] italic text-center",
        colorClass,
      )}
    >
      {label}
    </div>
  );
}
