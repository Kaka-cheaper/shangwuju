"use client";

/**
 * ComparisonView —— 调整对比视图（反馈/换店后，展示"这一轮改了什么"）。
 *
 * 设计动机：
 *   用户给反馈（"换个烧烤"）或换店后，想确认自己那句话精确改了哪一站。
 *   本组件把新方案画成一条时间线，把变化标出来。
 *
 * ── 问题命名 + prior art ──────────────────────────────────────────────
 *   这是"结构化短序列 diff、且需要 UPDATE 操作"的问题。纯文本/序列 diff
 *   （LCS/Myers/git diff）只有 增/删/保留 三种操作，没有"原地改"——两个元素
 *   要么相等要么不等。旧实现按 target_id 精确配对正是这种文本级 diff：换店 =
 *   target_id 变 = 不相等 = 被迫拆成"删旧店 + 加新店"两件事，于是一次"换两个店"
 *   被画成 4 件事 + 4 个幽灵占位框（用户报告的 #100）。
 *
 *   结构化 diff 的成熟范式（ChangeDistilling / Chawathe 1996；GumTree /
 *   Falleri 2014）为此引入 update（原地改）操作，匹配分两阶段：先找完全相同的
 *   做精确锚点，再按相似度补配，相似但不同的一对判成 update（而非删+加）。
 *   本组件的三趟对齐就是这套两阶段匹配在"2–5 个节点 + 稳定实体 id + 粗角色
 *   (kind)"场景下的裁剪版。
 *
 * ── 对齐算法（diffStages，三趟）────────────────────────────────────────
 *   Pass 1 精确锚点：两侧 target_id 相同 → unchanged（时间变了则 modified）。
 *   Pass 2 角色补配：剩余节点按 kind 分组、同 kind 内按时间序贪心配对 →
 *           modified + entitySwapped=true（这就是把"换店"认成一次修改）。
 *   Pass 3 剩余：没配上的旧 → removed，新 → added。
 *   产物按代表时间升序排（新节点用 new.start，removed 用 old.start）。
 *
 * ── 挂不挂卡（shouldShowComparison / 内部 gate）─────────────────────────
 *   只在"有结构延续"时挂：unchanged+modified ≥ 1 且这轮确实改了。全是删+加
 *   （几乎零重合，如 #99 两活动删到只剩一餐）不是"调整"而是"整份重排"——
 *   不挂对比卡，直接由新方案卡兜底（其顶部口播本就说了改啥）。移动端外壳
 *   （MobileInlineCompare）带标题，不能只靠这里 return null，故把判定导出成
 *   shouldShowComparison 供两个父级提前拦。
 *
 * ── 渲染 ───────────────────────────────────────────────────────────────
 *   一条时间线，按时间排。换店显示新店 + 暖金高亮 + 灰字"原：旧店"；仅时间变
 *   显示同店 + 高亮时间 + 灰字"原 旧时段"；新增挂"新增"标；删除用一行紧凑
 *   小字"已精简 X（原 15:44）"，不画大幽灵框（用户反馈：幽灵框看着像 bug）。
 *
 * 数据源：oldItinerary=前端本地在"方案被替换前一刻"抓的快照（不再从后端搬
 *   previous_itinerary，见 collab-store 的对应改动）；newItinerary=当前方案。
 */

import { useState } from "react";
import { ChevronDown, GitCompare } from "lucide-react";

import type { Itinerary } from "@/lib/types";
import { cn } from "@/lib/utils";

// ============================================================
// 渲染层 stage 形状（edge_v1 适配，不再依赖已删除的 ItineraryStage 类型）
// ============================================================

export interface DiffStage {
  /** 身份配对键——ActivityNode.target_id，必填字段（lib/types.ts）。 */
  targetId: string;
  start: string;
  end: string;
  title: string;
  /** 展示角色（主活动/用餐/…）——Pass 2 角色补配按它分组。 */
  kind: string;
}

function addMinutesHHMM(start: string, minutes: number): string {
  const m = /^(\d{1,2}):(\d{2})$/.exec(start);
  if (!m) return start;
  const total = Number(m[1]) * 60 + Number(m[2]) + (minutes || 0);
  const wrap = ((total % (24 * 60)) + 24 * 60) % (24 * 60);
  return `${String(Math.floor(wrap / 60)).padStart(2, "0")}:${String(wrap % 60).padStart(2, "0")}`;
}

function hhmmToMinutes(hhmm: string): number {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm);
  if (!m) return 0;
  return Number(m[1]) * 60 + Number(m[2]);
}

// 导出供测试直驱（同 collab-store.ts::handleWsMessage 的既有测试性导出先例）——
// 这是纯数据转换函数，不依赖 React，直接单测比渲染 + DOM 断言更直接可靠。
export function nodesToDiffStages(itinerary: Itinerary): DiffStage[] {
  return (itinerary.nodes || [])
    .filter((n) => n.target_kind !== "home")
    .map((n) => ({
      targetId: n.target_id,
      start: n.start_time,
      end: addMinutesHHMM(n.start_time, n.duration_min),
      title: n.title,
      kind: n.kind,
    }));
}

// ============================================================
// Diff 算法（三趟对齐）
// ============================================================

export type DiffEntryKind = "unchanged" | "modified" | "added" | "removed";

export interface DiffEntry {
  kind: DiffEntryKind;
  oldStage: DiffStage | null;
  newStage: DiffStage | null;
  /** modified 且换了实体（换店，Pass 2 角色补配得来）→ 渲染显示"原：旧店"。 */
  entitySwapped: boolean;
  /** 字段级变化（用于时间高亮 / 仅时间变时的"原 旧时段"）。 */
  changedFields: ReadonlyArray<"time" | "title" | "kind">;
}

/** 比较两个 stage 的字段，返回变化字段列表。 */
function compareFields(
  oldStage: DiffStage,
  newStage: DiffStage,
): Array<"time" | "title" | "kind"> {
  const changed: Array<"time" | "title" | "kind"> = [];
  if (oldStage.start !== newStage.start || oldStage.end !== newStage.end) {
    changed.push("time");
  }
  if (oldStage.title !== newStage.title) changed.push("title");
  if (oldStage.kind !== newStage.kind) changed.push("kind");
  return changed;
}

function groupByKind(stages: DiffStage[]): Map<string, DiffStage[]> {
  const map = new Map<string, DiffStage[]>();
  for (const s of stages) {
    const arr = map.get(s.kind);
    if (arr) arr.push(s);
    else map.set(s.kind, [s]);
  }
  return map;
}

function repMinutes(entry: DiffEntry): number {
  const s = entry.newStage ?? entry.oldStage;
  return s ? hhmmToMinutes(s.start) : 0;
}

export function diffStages(
  oldStages: ReadonlyArray<DiffStage>,
  newStages: ReadonlyArray<DiffStage>,
): DiffEntry[] {
  const oldById = new Map(oldStages.map((s) => [s.targetId, s]));
  const matchedOld = new Set<string>();
  const matchedNew = new Set<string>();
  const entries: DiffEntry[] = [];

  // Pass 1 — 精确锚点：target_id 相同即同一实体，先钉死（与位置无关，插入/
  // 删除/换序都不会让它误判）。
  for (const ns of newStages) {
    const os = oldById.get(ns.targetId);
    if (!os) continue;
    const changed = compareFields(os, ns);
    entries.push({
      kind: changed.length > 0 ? "modified" : "unchanged",
      oldStage: os,
      newStage: ns,
      entitySwapped: false,
      changedFields: changed,
    });
    matchedOld.add(os.targetId);
    matchedNew.add(ns.targetId);
  }

  // Pass 2 — 角色补配：剩余节点按 kind 分组、同 kind 内按时间序贪心配对，
  // 配上的一对（同角色不同实体）= 换店 = 一次 modified（update），而不是
  // 删+加。这是把 GumTree "相似度补配 → update" 落到我们场景的核心一趟。
  const remOld = oldStages
    .filter((s) => !matchedOld.has(s.targetId))
    .sort((a, b) => hhmmToMinutes(a.start) - hhmmToMinutes(b.start));
  const remNew = newStages
    .filter((s) => !matchedNew.has(s.targetId))
    .sort((a, b) => hhmmToMinutes(a.start) - hhmmToMinutes(b.start));
  const oldByKind = groupByKind(remOld);
  const newByKind = groupByKind(remNew);
  const swappedOld = new Set<string>();
  const swappedNew = new Set<string>();
  for (const [kind, olds] of oldByKind) {
    const news = newByKind.get(kind);
    if (!news) continue;
    const n = Math.min(olds.length, news.length);
    for (let i = 0; i < n; i++) {
      entries.push({
        kind: "modified",
        oldStage: olds[i],
        newStage: news[i],
        entitySwapped: true,
        changedFields: compareFields(olds[i], news[i]),
      });
      swappedOld.add(olds[i].targetId);
      swappedNew.add(news[i].targetId);
    }
  }

  // Pass 3 — 剩余：真增 / 真删（kind 对不上、或同 kind 数量不齐的余量）。
  for (const os of remOld) {
    if (!swappedOld.has(os.targetId)) {
      entries.push({ kind: "removed", oldStage: os, newStage: null, entitySwapped: false, changedFields: [] });
    }
  }
  for (const ns of remNew) {
    if (!swappedNew.has(ns.targetId)) {
      entries.push({ kind: "added", oldStage: null, newStage: ns, entitySwapped: false, changedFields: [] });
    }
  }

  // 按代表时间升序——对比卡是一条从早到晚的时间线，不是"新增排前、删除甩后"。
  entries.sort((a, b) => repMinutes(a) - repMinutes(b));
  return entries;
}

/**
 * 挂不挂对比卡：有结构延续（至少一站被延续或原地换店）且这轮确实改了才挂。
 * 全是删+加（零重合）不是"调整"而是"整份重排"——不挂卡，由新方案卡兜底。
 * 导出供父级（ItineraryCard / MobileHomeView 的 MobileInlineCompare）提前拦：
 * 移动端外壳带"调整对比"标题，不能只靠 ComparisonView 内部 return null。
 */
export function shouldShowComparison(
  oldItinerary: Itinerary,
  newItinerary: Itinerary,
): boolean {
  const diffs = diffStages(
    nodesToDiffStages(oldItinerary),
    nodesToDiffStages(newItinerary),
  );
  const changedCount = diffs.filter((d) => d.kind !== "unchanged").length;
  const continuity = diffs.filter(
    (d) => d.kind === "unchanged" || d.kind === "modified",
  ).length;
  return changedCount > 0 && continuity > 0;
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
  const continuity = diffs.filter(
    (d) => d.kind === "unchanged" || d.kind === "modified",
  ).length;

  // Gate（与 shouldShowComparison 同口径）：无改动或无结构延续 → 不挂卡。
  // 父级正常会用 shouldShowComparison 提前拦住，这里是防御性兜底。
  if (changedCount === 0 || continuity === 0) return null;

  if (variant === "mobile") {
    return <ComparisonTimeline diffs={diffs} />;
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
          <GitCompare className="h-4 w-4 shrink-0 text-[#d97706]" strokeWidth={2} />
          <span className="text-base font-black tracking-tight text-ink-900">
            调整对比
          </span>
          <span className="rounded-full bg-[#FFD100]/20 px-2 py-0.5 text-sm font-semibold tabular-nums text-[#9a5b00]">
            {changedCount} 处调整
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
          <div className="px-4 py-3 animate-collapse-in">
            <ComparisonTimeline diffs={diffs} />
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// 时间线渲染（桌面/移动共用一套）
// ============================================================

function ComparisonTimeline({ diffs }: { diffs: DiffEntry[] }) {
  return (
    <div className="space-y-2">
      {diffs.map((entry, i) => (
        <TimelineRow
          key={`${entry.kind}-${(entry.newStage ?? entry.oldStage)!.targetId}-${i}`}
          entry={entry}
        />
      ))}
    </div>
  );
}

function TimelineRow({ entry }: { entry: DiffEntry }) {
  // 删除：一行紧凑小字，不画大幽灵框。
  if (entry.kind === "removed") {
    const s = entry.oldStage!;
    return (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-2xl border border-dashed border-black/[0.08] bg-black/[0.012] px-3 py-2 text-sm text-ink-400">
        <span className="font-semibold text-ink-400">已精简</span>
        <span className="font-medium text-ink-500 line-through decoration-ink-300">
          {s.title}
        </span>
        <span className="mono text-xs tabular-nums text-ink-400">
          原 {s.start}–{s.end}
        </span>
      </div>
    );
  }

  const s = entry.newStage!;
  const highlighted = entry.kind === "modified" || entry.kind === "added";
  const timeChanged = entry.changedFields.includes("time");

  let sub: string | null = null;
  if (entry.entitySwapped && entry.oldStage) {
    sub = `原：${entry.oldStage.title}`;
  } else if (
    entry.kind === "modified" &&
    timeChanged &&
    entry.oldStage
  ) {
    sub = `原 ${entry.oldStage.start}–${entry.oldStage.end}`;
  }

  return (
    <div
      className={cn(
        "rounded-2xl border px-3 py-2.5",
        highlighted
          ? "border-[#FFD100]/45 bg-[#fff9df]/70"
          : "border-black/[0.06] bg-black/[0.015]",
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className={cn(
            "mono text-sm font-semibold tabular-nums",
            timeChanged ? "text-amber-700" : "text-ink-500",
          )}
        >
          {s.start}–{s.end}
        </span>
        <span className="rounded-full border border-black/[0.06] bg-black/[0.03] px-2 py-0.5 text-xs font-medium text-ink-700">
          {s.kind}
        </span>
        {entry.kind === "added" && (
          <span className="rounded-full border border-accent-500/30 bg-accent-500/10 px-2 py-0.5 text-xs font-semibold text-accent-600">
            新增
          </span>
        )}
        {entry.entitySwapped && (
          <span className="rounded-full border border-[#FFD100]/50 bg-[#FFD100]/20 px-2 py-0.5 text-xs font-semibold text-[#9a5b00]">
            换店
          </span>
        )}
      </div>
      <div className="mt-0.5 line-clamp-2 break-words text-base font-semibold leading-snug tracking-tight text-ink-900">
        {s.title}
      </div>
      {sub && (
        <div className="mt-0.5 text-xs font-medium text-ink-400">{sub}</div>
      )}
    </div>
  );
}
