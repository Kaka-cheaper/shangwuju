"use client";

/**
 * ToolTracePanel —— B Chain-of-Thought 范式重写。
 *
 * 设计灵感：assistant-ui Chain-of-Thought + react-o11y trace inspector
 *
 * 把扁平 Tool 调用列表改成 hierarchical 折叠 trace：
 *   ▼ 用户画像  · 1 调用 · 80ms · 成功
 *   ▼ 候选发现  · 4 调用 · 350ms · 含异常重规划
 *      └ search_pois ✓
 *      └ search_restaurants ✓
 *      └ check_restaurant_availability ⚠ 已替换
 *      └ ⤷ 触发重规划：餐厅已满
 *      └ check_restaurant_availability ✓ (改约时段)
 *   ▼ 执行下单  · 2 调用 · 400ms · 成功
 *
 * Epic 头默认收起；只展开当前正在跑或刚出错的那个。
 */

import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  CornerDownRight,
  Loader2,
  Play,
  Sparkles,
  TriangleAlert,
} from "lucide-react";

import { Icons } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn, FAILURE_REASON_LABEL, TOOL_LABEL } from "@/lib/utils";

// ============================================================
// Tool → Epic 分组映射
// ============================================================

type EpicId =
  | "profile"
  | "discovery"
  | "routing"
  | "execution"
  | "share"
  | "other";

const EPIC_OF_TOOL: Record<string, EpicId> = {
  get_user_profile: "profile",
  search_pois: "discovery",
  search_restaurants: "discovery",
  check_restaurant_availability: "discovery",
  estimate_route_time: "routing",
  reserve_restaurant: "execution",
  buy_ticket: "execution",
  order_extra_service: "execution",
  generate_share_message: "share",
};

interface EpicMeta {
  id: EpicId;
  label: string;
  hint: string;
  order: number;
}

const EPICS: Record<EpicId, EpicMeta> = {
  profile: { id: "profile", label: "用户画像", hint: "读取硬编码偏好", order: 0 },
  discovery: {
    id: "discovery",
    label: "候选发现",
    hint: "查询 POI / 餐厅 / 座位",
    order: 1,
  },
  routing: { id: "routing", label: "路线估算", hint: "计算通勤耗时", order: 2 },
  execution: {
    id: "execution",
    label: "执行下单",
    hint: "餐厅预约 / 购票 / 加购",
    order: 3,
  },
  share: { id: "share", label: "文案生成", hint: "口语化转发文案", order: 4 },
  other: { id: "other", label: "其他", hint: "未分组的 Tool", order: 5 },
};

// 类型来自 store，但本地用别名让代码可读
type ToolCall = ReturnType<typeof useChatStore.getState>["toolCalls"][number];
type Replan = ReturnType<typeof useChatStore.getState>["replans"][number];

// 时间线项目（ToolCall 或 Replan）
type Item =
  | { kind: "tool"; idx: number; tool: ToolCall }
  | { kind: "replan"; idx: number; reason: string; fromTool: string };

// ============================================================
// 主组件
// ============================================================

export default function ToolTracePanel() {
  const toolCalls = useChatStore((s) => s.toolCalls);
  const replans = useChatStore((s) => s.replans);
  const streaming = useChatStore((s) => s.streaming);

  // 1. 按 arrivalIdx 合并 toolCalls + replans 到时间线
  const timeline: Item[] = useMemo(
    () =>
      [
        ...toolCalls.map(
          (tc): Item => ({ kind: "tool" as const, idx: tc.arrivalIdx, tool: tc }),
        ),
        ...replans.map(
          (r): Item => ({
            kind: "replan" as const,
            idx: r.arrivalIdx,
            reason: r.reason,
            fromTool: r.fromTool,
          }),
        ),
      ].sort((a, b) => a.idx - b.idx),
    [toolCalls, replans],
  );

  // 2. 把时间线划进 epic 桶
  const buckets = useMemo(() => bucketByEpic(timeline), [timeline]);

  // 3. 折叠状态
  const [collapsed, setCollapsed] = useState<Set<EpicId>>(new Set());
  // 当 streaming 重新开始时（新一轮），重置折叠状态让所有 epic 可见
  useEffect(() => {
    if (streaming) setCollapsed(new Set());
  }, [streaming]);

  // 早返必须放在所有 hook 后面，否则违反 Rules of Hooks
  if (!toolCalls.length && !replans.length && !streaming) {
    return null;
  }

  const toggle = (id: EpicId) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const totalCalls = toolCalls.length;
  const totalReplans = replans.length;

  return (
    <div className="card relative overflow-hidden">
      {streaming && (
        <div
          aria-hidden
          className="absolute top-0 left-0 right-0 h-px shimmer-bar"
        />
      )}
      <div className="px-4 py-3 border-b border-ink-200 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Sparkles
            className={cn(
              "w-3.5 h-3.5 transition-colors",
              streaming ? "text-accent-500" : "text-ink-700",
            )}
            strokeWidth={2}
          />
          <span className="text-[12px] font-medium text-ink-800 tracking-tight">
            Agent 思考链路
          </span>
        </div>
        <div className="text-[11px] text-ink-400 mono">
          {totalCalls} 调用
          {totalReplans > 0 && (
            <>
              <span className="mx-1.5 text-ink-300">·</span>
              <span className="text-amber-600">{totalReplans} 重规划</span>
            </>
          )}
        </div>
      </div>

      <div className="px-3 py-2.5 space-y-1.5">
        {buckets.length === 0 && streaming && (
          <div className="px-2 py-1.5 flex items-center gap-1.5 text-xs text-ink-400 italic">
            <Loader2 className="w-3 h-3 animate-spin" strokeWidth={2} />
            <span>等待 Agent 调用 Tool...</span>
          </div>
        )}

        {buckets.map((bucket) => (
          <EpicBlock
            key={bucket.epic.id}
            bucket={bucket}
            collapsed={collapsed.has(bucket.epic.id)}
            onToggle={() => toggle(bucket.epic.id)}
          />
        ))}
      </div>
    </div>
  );
}

// ============================================================
// Epic 块（折叠头 + 子项列表）
// ============================================================

interface Bucket {
  epic: EpicMeta;
  items: Item[];
  // 聚合统计
  totalDurationMs: number;
  callCount: number;
  hasReplan: boolean;
  hasInProgress: boolean;
  hasFail: boolean;
}

function EpicBlock({
  bucket,
  collapsed,
  onToggle,
}: {
  bucket: Bucket;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const { epic, items, totalDurationMs, callCount, hasReplan, hasInProgress, hasFail } = bucket;

  // 头部状态色
  const headerAccent = hasInProgress
    ? "text-accent-700"
    : hasFail
      ? "text-rose-700"
      : hasReplan
        ? "text-amber-700"
        : "text-ink-700";

  return (
    <div className="rounded-md">
      <button
        type="button"
        onClick={onToggle}
        className={cn(
          "w-full flex items-center gap-2 rounded-md px-2 py-1.5",
          "hover:bg-ink-50 transition-colors duration-150",
          "text-left",
        )}
      >
        <ChevronDown
          className={cn(
            "w-3 h-3 text-ink-400 shrink-0 transition-transform duration-200",
            collapsed && "-rotate-90",
          )}
          strokeWidth={2.5}
        />
        <span className={cn("text-[12px] font-medium tracking-tight", headerAccent)}>
          {epic.label}
        </span>
        <span className="text-[10px] text-ink-400 truncate flex-1 min-w-0">
          {epic.hint}
        </span>
        <span className="flex items-center gap-1.5 shrink-0">
          {hasInProgress && (
            <Loader2
              className="w-3 h-3 text-accent-500 animate-spin"
              strokeWidth={2}
            />
          )}
          {hasReplan && !hasInProgress && (
            <TriangleAlert
              className="w-3 h-3 text-amber-600"
              strokeWidth={2}
            />
          )}
          <span className="text-[10px] text-ink-400 mono tabular-nums">
            {callCount}× · {totalDurationMs}ms
          </span>
        </span>
      </button>

      {!collapsed && (
        <ol className="ml-3 pl-2 border-l border-ink-200 space-y-1 py-1 animate-collapse-in overflow-hidden">
          {items.map((it, idx) => {
            const localIdx = idx + 1;
            if (it.kind === "replan") {
              return (
                <li
                  key={`replan-${it.idx}`}
                  className="flex items-start gap-1.5 px-2 py-1.5 rounded text-[11px] text-amber-800"
                >
                  <CornerDownRight
                    className="w-3 h-3 text-amber-500 mt-0.5 shrink-0"
                    strokeWidth={2}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1">
                      <TriangleAlert
                        className="w-3 h-3 text-amber-600 shrink-0"
                        strokeWidth={2}
                      />
                      <span className="font-medium">触发异常重规划</span>
                    </div>
                    <div className="mt-0.5 text-amber-700/90">
                      {FAILURE_REASON_LABEL[it.reason] ?? it.reason}
                      <span className="mx-1 text-amber-400">·</span>
                      来自 <span className="mono text-[10px]">{it.fromTool}</span>
                    </div>
                  </div>
                </li>
              );
            }
            return (
              <ToolItem key={it.tool.id} index={localIdx} call={it.tool} />
            );
          })}
        </ol>
      )}
    </div>
  );
}

// ============================================================
// 单个 Tool 调用（折叠 trace 内的叶子节点）
// ============================================================

function ToolItem({ index, call }: { index: number; call: ToolCall }) {
  const inProgress = call.endedAtSeq == null;
  const isFail = call.success === false;
  const replaced = call.replanned === true;

  const StatusIcon = inProgress
    ? Loader2
    : replaced
      ? Icons.refine
      : isFail
        ? Icons.fail
        : Icons.success;

  const iconClass = inProgress
    ? "text-accent-500 animate-spin"
    : replaced
      ? "text-ink-400"
      : isFail
        ? "text-rose-500"
        : "text-emerald-500";

  const textClass = replaced ? "text-ink-400" : "text-ink-800";

  return (
    <li
      className={cn(
        "px-2 py-1.5 rounded transition-colors animate-fade-in-up",
        replaced && "opacity-60",
      )}
    >
      <div className="flex items-start gap-1.5">
        <span className="text-[10px] text-ink-300 mono w-3 text-right shrink-0 mt-0.5 tabular-nums">
          {index}
        </span>
        <StatusIcon
          className={cn("w-3 h-3 mt-0.5 shrink-0", iconClass)}
          strokeWidth={2}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <div className="flex items-baseline gap-1.5 min-w-0">
              <span className={cn("text-[12px] font-medium tracking-tight", textClass)}>
                {TOOL_LABEL[call.tool] ?? call.tool}
              </span>
              <span className="text-[10px] text-ink-400 mono truncate">
                {call.tool}
              </span>
            </div>
            <span className="text-[10px] text-ink-400 mono shrink-0 tabular-nums">
              {call.durationMs != null ? `${call.durationMs}ms` : "..."}
            </span>
          </div>

          {Object.keys(call.input).length > 0 && (
            <div className="mt-0.5 text-[10px] text-ink-500 mono break-all line-clamp-1">
              {compactInput(call.input)}
            </div>
          )}

          {isFail && call.reason && (
            <div className="mt-0.5 text-[10px] text-rose-600">
              {FAILURE_REASON_LABEL[call.reason] ?? call.reason}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

// ============================================================
// 工具函数
// ============================================================

function compactInput(input: Record<string, unknown>): string {
  // 超长 JSON 截短到关键字段
  const keys = Object.keys(input);
  if (keys.length === 0) return "";
  const parts = keys.slice(0, 3).map((k) => {
    const v = input[k];
    let str: string;
    if (Array.isArray(v)) {
      str = v.length > 0 ? `[${v.length}]` : "[]";
    } else if (v && typeof v === "object") {
      str = "{...}";
    } else {
      str = JSON.stringify(v);
    }
    return `${k}=${str}`;
  });
  if (keys.length > 3) parts.push(`+${keys.length - 3}`);
  return parts.join(" ");
}

function bucketByEpic(timeline: Item[]): Bucket[] {
  // 1. 第一遍：tool item 直接进 epic；replan item 暂存，第二遍归到其 fromTool 的 epic
  const epicOrder: EpicId[] = [];
  const map = new Map<EpicId, Item[]>();

  for (const it of timeline) {
    let eid: EpicId;
    if (it.kind === "tool") {
      eid = EPIC_OF_TOOL[it.tool.tool] ?? "other";
    } else {
      eid = EPIC_OF_TOOL[it.fromTool] ?? "other";
    }
    if (!map.has(eid)) {
      epicOrder.push(eid);
      map.set(eid, []);
    }
    map.get(eid)!.push(it);
  }

  // 2. 计算每个 bucket 的统计
  return epicOrder.map((eid) => {
    const items = map.get(eid)!;
    let total = 0;
    let count = 0;
    let hasReplan = false;
    let hasInProgress = false;
    let hasFail = false;
    for (const it of items) {
      if (it.kind === "tool") {
        count += 1;
        if (it.tool.durationMs != null) total += it.tool.durationMs;
        if (it.tool.endedAtSeq == null) hasInProgress = true;
        if (it.tool.success === false) hasFail = true;
      } else {
        hasReplan = true;
      }
    }
    return {
      epic: EPICS[eid],
      items,
      totalDurationMs: total,
      callCount: count,
      hasReplan,
      hasInProgress,
      hasFail,
    };
  });
}
