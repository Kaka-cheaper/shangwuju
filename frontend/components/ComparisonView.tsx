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
 * 数据契约（edge_v1）：
 *   - Itinerary.nodes: ActivityNode[]（含 home 起终点；diff 时过滤）
 *   - 本视图把 nodes 投影到内部 DiffStage 形状（start/end/title/kind），保持原 diff 算法不变。
 *
 * Diff 算法：
 *   按节点索引对齐（不做 LCS / 模糊匹配），逐字段比较 start/end/title/kind。
 *   不同字段加 amber 高亮。新增/删除段加专门的 「新增」/「已移除」 占位。
 */

import { useState } from "react";
import { ChevronDown, GitCompare } from "lucide-react";

import type { Itinerary } from "@/lib/types";
import { cn } from "@/lib/utils";

// ============================================================
// 渲染层 stage 形状（edge_v1 适配，不再依赖已删除的 ItineraryStage 类型）
// ============================================================

interface DiffStage {
  start: string;
  end: string;
  title: string;
  kind: string;
}

function addMinutesHHMM(start: string, minutes: number): string {
  const m = /^(\d{1,2}):(\d{2})$/.exec(start);
  if (!m) return start;
  const total = Number(m[1]) * 60 + Number(m[2]) + (minutes || 0);
  const wrap = ((total % (24 * 60)) + 24 * 60) % (24 * 60);
  return `${String(Math.floor(wrap / 60)).padStart(2, "0")}:${String(wrap % 60).padStart(2, "0")}`;
}

function nodesToDiffStages(itinerary: Itinerary): DiffStage[] {
  return (itinerary.nodes || [])
    .filter((n) => n.target_kind !== "home")
    .map((n) => ({
      start: n.start_time,
      end: addMinutesHHMM(n.start_time, n.duration_min),
      title: n.title,
      kind: n.kind,
    }));
}

// ============================================================
// Diff 算法
// ============================================================

type StageDiffKind = "unchanged" | "modified" | "added" | "removed";

interface StageDiff {
  oldStage: DiffStage | null;
  newStage: DiffStage | null;
  kind: StageDiffKind;
  /** 在 modified 时具体变化的字段名（用于高亮） */
  changedFields: ReadonlyArray<"time" | "title" | "kind">;
}

function diffStages(
  oldStages: ReadonlyArray<DiffStage>,
  newStages: ReadonlyArray<DiffStage>,
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
  variant?: "desktop" | "mobile";
}

export default function ComparisonView({
  oldItinerary,
  newItinerary,
  variant = "desktop",
}: ComparisonViewProps) {
  const [expanded, setExpanded] = useState(true);
  const diffs = diffStages(
    nodesToDiffStages(oldItinerary),
    nodesToDiffStages(newItinerary),
  );
  const changedCount = diffs.filter((d) => d.kind !== "unchanged").length;

  if (variant === "mobile") {
    return (
      <div className="grid grid-cols-2 gap-2.5">
        <MobileCompareColumn label="调整前" side="old" diffs={diffs} />
        <MobileCompareColumn label="调整后" side="new" diffs={diffs} />
      </div>
    );
  }

  return (
    <div className="px-4 pt-3">
      <div className="overflow-hidden rounded-[28px] border border-black/[0.06] bg-white shadow-[0_18px_46px_-38px_rgba(17,24,39,0.55)]">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex w-full items-center gap-2 border-b border-black/[0.05] px-4 py-3 text-left"
          aria-expanded={expanded}
        >
          <GitCompare
            className="h-4 w-4 shrink-0 text-[#d97706]"
            strokeWidth={2}
          />
          <span className="text-base font-black tracking-tight text-ink-900">
            调整对比
          </span>
          <span className="rounded-full bg-[#FFD100]/20 px-2 py-0.5 text-sm font-semibold tabular-nums text-[#9a5b00]">
            {changedCount} 处变化
          </span>
          <span className="ml-auto text-sm font-semibold text-ink-500">
            {expanded ? "收起对比" : "展开对比"}
          </span>
          <ChevronDown
            className={cn(
              "h-4 w-4 shrink-0 text-ink-500 transition-transform duration-200",
              !expanded && "-rotate-90",
            )}
            strokeWidth={2.5}
          />
        </button>

        {expanded && (
          <div className="space-y-3 px-4 py-3 animate-collapse-in">
            <DesktopCompareRow label="调整前" side="old" diffs={diffs} />
            <DesktopCompareRow label="调整后" side="new" diffs={diffs} />
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// 单段渲染
// ============================================================

function DesktopCompareRow({
  label,
  side,
  diffs,
}: {
  label: string;
  side: "old" | "new";
  diffs: StageDiff[];
}) {
  const isAfter = side === "new";

  return (
    <div className="grid grid-cols-[5.5rem_1fr] items-start gap-3">
      <div
        className={cn(
          "pt-2 text-sm font-black tracking-tight",
          isAfter ? "text-[#b45309]" : "text-ink-600",
        )}
      >
        <span className="inline-flex items-center gap-1.5">
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              isAfter ? "bg-[#d97706]" : "bg-ink-400",
            )}
            aria-hidden
          />
          {label}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {diffs.map((diff, index) => (
          <StageRow
            key={`${side}-${index}`}
            stage={side === "old" ? diff.oldStage : diff.newStage}
            diffKind={diff.kind}
            side={side}
            changedFields={diff.changedFields}
          />
        ))}
      </div>
    </div>
  );
}

function MobileCompareColumn({
  label,
  side,
  diffs,
}: {
  label: string;
  side: "old" | "new";
  diffs: StageDiff[];
}) {
  const isAfter = side === "new";

  return (
    <div className="min-w-0">
      <div
        className={cn(
          "mb-2 text-center text-sm font-black tracking-tight",
          isAfter ? "text-[#b45309]" : "text-ink-600",
        )}
      >
        {label}
      </div>
      <div className="space-y-1">
        {diffs.map((diff, index) => (
          <div key={`${side}-${index}`}>
            <MobileStageBlock
              stage={side === "old" ? diff.oldStage : diff.newStage}
              diffKind={diff.kind}
              side={side}
              changedFields={diff.changedFields}
            />
            {index < diffs.length - 1 && (
              <div
                className={cn(
                  "flex h-5 items-center justify-center text-base leading-none",
                  isAfter ? "text-[#d97706]/70" : "text-ink-300",
                )}
                aria-hidden
              >
                ↓
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function MobileStageBlock({
  stage,
  diffKind,
  side,
  changedFields,
}: {
  stage: DiffStage | null;
  diffKind: StageDiffKind;
  side: "old" | "new";
  changedFields: ReadonlyArray<"time" | "title" | "kind">;
}) {
  const isAfter = side === "new";
  const highlight =
    (diffKind === "modified" && side === "new") ||
    (diffKind === "added" && side === "new") ||
    (diffKind === "removed" && side === "old");

  if (!stage) {
    const placeholder =
      diffKind === "added" && side === "old"
        ? "原方案没有这一站"
        : diffKind === "removed" && side === "new"
          ? "调整后已移除"
          : "无";

    return (
      <div className="min-h-[78px] rounded-2xl border border-dashed border-black/[0.08] bg-white/[0.62] px-2.5 py-2.5">
        <div className="text-sm font-medium leading-snug text-ink-400">
          {placeholder}
        </div>
      </div>
    );
  }

  const timeChanged = changedFields.includes("time");
  const titleChanged = changedFields.includes("title");
  const kindChanged = changedFields.includes("kind");
  const toneClass = isAfter
    ? "border-[#FFD100]/45 bg-[#fff9df]/70"
    : "border-black/[0.06] bg-white/[0.72]";

  return (
    <div className={cn("min-h-[78px] min-w-0 rounded-2xl border px-2.5 py-2.5", toneClass)}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className={cn(
            "mono text-[13px] font-semibold leading-none tabular-nums",
            timeChanged && highlight ? "text-[#b45309]" : "text-ink-600",
          )}
        >
          {stage.start}-{stage.end}
        </span>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-xs font-medium",
            kindChanged && highlight
              ? "border-[#FFD100]/50 bg-[#FFD100]/20 text-[#9a5b00]"
              : "border-black/[0.06] bg-white/[0.72] text-ink-600",
          )}
        >
          {stage.kind}
        </span>
      </div>
      <div
        className={cn(
          "mt-1.5 line-clamp-2 break-words text-sm font-semibold leading-snug tracking-tight",
          titleChanged && highlight ? "text-[#9a4a10]" : "text-ink-900",
        )}
      >
        {stage.title}
      </div>
    </div>
  );
}

function StageRow({
  stage,
  diffKind,
  side,
  changedFields,
}: {
  stage: DiffStage | null;
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
          // 去绿归色：调整后新增的行是"新版本"高亮，不是"成功"——改用暖金
          // （accent），与信息栏其余"变更/当前"强调同色系。
          colorClass="bg-accent-500/10 border-accent-500/30 text-accent-600"
        />
      );
    }
    if (diffKind === "removed" && side === "new") {
      return (
        <PlaceholderRow
          label="已移除"
          colorClass="bg-rose-500/10 border-rose-500/30 text-rose-600"
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
        "rounded-2xl border px-3 py-2.5 text-sm transition-colors",
        highlight
          ? "border-[#FFD100]/45 bg-[#fff9df]/70"
          : "border-black/[0.06] bg-black/[0.018]",
      )}
    >
      <div className="flex items-center gap-1.5 mb-0.5">
        <span
          className={cn(
            "mono text-base font-semibold tabular-nums",
            timeChanged ? "text-amber-700 font-semibold" : "text-ink-500",
          )}
        >
          {stage.start}-{stage.end}
        </span>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-xs font-medium",
            kindChanged
              ? "border-amber-500/40 bg-amber-500/10 text-amber-700"
              : "border-black/[0.06] bg-black/[0.03] text-ink-700",
          )}
        >
          {stage.kind}
        </span>
      </div>
      <div
        className={cn(
          "line-clamp-2 text-base font-semibold leading-snug tracking-tight",
          titleChanged ? "text-[#9a4a10]" : "text-ink-900",
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
        "rounded-2xl border px-3 py-2.5 text-sm font-semibold text-center",
        colorClass,
      )}
    >
      {label}
    </div>
  );
}

