"use client";

/**
 * ThoughtPanel —— Agent 思考过程可视化（语义级决策面板）。
 *
 * 设计动机（对应 R4 / spec frontend-experience-innovation §4）：
 *   现有 ToolTracePanel 展示 *技术级* Tool 调用链路（按 Epic 分组）；
 *   本组件展示 *语义级* 决策过程——把 agent_thought 事件流做成
 *   类似 ChatGPT o1 的折叠思考过程面板：
 *     折叠态：脉冲点 + 最新一句摘要 + 总条数 badge
 *     展开态：完整时间线（thoughts + replan 分隔线 + 相对时间戳）
 *
 * 与 ToolTracePanel 的分工：
 *   - ToolTracePanel：show "Agent 调了什么"（动作）
 *   - ThoughtPanel：show "Agent 在想什么"（理由）
 *   两者并列同级，独立折叠，互不影响。
 *
 * 不渲染条件（return null）：
 *   - thoughts.length === 0 且 !streaming：本会话没思考事件
 *
 * 依赖契约：
 *   - store.thoughts: { seq, text, timestamp_ms }[]（含 R4 新增的 timestamp_ms）
 *   - store.replans: { seq, reason, fromTool }[]
 *   - SSE event seq 在单次会话内单调递增（参考 schemas/sse.py）
 */

import { useEffect, useMemo, useState } from "react";
import { Brain, ChevronDown, Loader2, TriangleAlert } from "lucide-react";

import { useChatStore } from "@/lib/store";
import { cn, FAILURE_REASON_LABEL } from "@/lib/utils";

// ============================================================
// 时间线 item（thought 或 replan 分隔线）
// ============================================================

type TimelineItem =
  | {
      kind: "thought";
      seq: number;
      text: string; // 用户向显示文案（已经从 user_text || text 解出）
      timestamp_ms: number | null;
    }
  | { kind: "replan"; seq: number; reason: string; fromTool: string };

/**
 * 选取展示文本：优先 user_text，其次回退到 text。
 * user_text === null 表示后端显式隐藏该 thought，前端不展示。
 */
function pickDisplayText(t: {
  text: string;
  user_text: string | null | undefined;
}): string | null {
  // null 显式隐藏；undefined 表示后端未注入（兼容旧路径，回退 text）
  if (t.user_text === null) return null;
  if (t.user_text !== undefined && t.user_text !== "") return t.user_text;
  return t.text;
}

function buildTimeline(
  thoughts: ReadonlyArray<{
    seq: number;
    text: string;
    user_text: string | null | undefined;
    timestamp_ms: number | null;
  }>,
  replans: ReadonlyArray<{ seq: number; reason: string; fromTool: string }>,
): TimelineItem[] {
  const merged: TimelineItem[] = [];
  for (const t of thoughts) {
    const display = pickDisplayText(t);
    if (display === null) continue; // 后端隐藏
    merged.push({
      kind: "thought",
      seq: t.seq,
      text: display,
      timestamp_ms: t.timestamp_ms,
    });
  }
  for (const r of replans) {
    merged.push({
      kind: "replan",
      seq: r.seq,
      reason: r.reason,
      fromTool: r.fromTool,
    });
  }
  // 按 seq 升序（SSE 单次会话内单调递增）
  merged.sort((a, b) => a.seq - b.seq);
  return merged;
}

// ============================================================
// 相对时间戳格式化（"3 秒前 / 1 分钟前 / 刚刚"）
// ============================================================

function formatRelativeTime(timestamp_ms: number | null, now: number): string {
  if (timestamp_ms == null) return "";
  const diff = Math.max(0, now - timestamp_ms);
  if (diff < 5_000) return "刚刚";
  if (diff < 60_000) return `${Math.floor(diff / 1000)} 秒前`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  return new Date(timestamp_ms).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max)}…`;
}

// ============================================================
// 主组件
// ============================================================

export default function ThoughtPanel() {
  const thoughts = useChatStore((s) => s.thoughts);
  const replans = useChatStore((s) => s.replans);
  const streaming = useChatStore((s) => s.streaming);

  const [expanded, setExpanded] = useState(false);

  // 相对时间戳每 10 秒更新一次（避免 1 秒一次的高频 rerender）
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    if (!expanded) return; // 折叠时不需要更新
    const timer = setInterval(() => setNow(Date.now()), 10_000);
    return () => clearInterval(timer);
  }, [expanded]);

  const timeline = useMemo(
    () => buildTimeline(thoughts, replans),
    [thoughts, replans],
  );

  // 仅可见 thought（user_text 非 null）参与摘要 + 总条数计算
  const visibleThoughts = useMemo(
    () => timeline.filter((t) => t.kind === "thought"),
    [timeline],
  );

  // R4 #8：可见 thoughts 为空 + 不在 streaming 时不渲染（不显示空面板占位）
  if (visibleThoughts.length === 0 && !streaming) {
    return null;
  }

  const latestVisible = visibleThoughts[visibleThoughts.length - 1] as
    | (TimelineItem & { kind: "thought" })
    | undefined;
  const summary = latestVisible
    ? truncate(latestVisible.text, 50)
    : "等待 Agent 开始思考……";
  const replanCount = replans.length;

  return (
    <div className="card mt-3 overflow-hidden">
      {/* 折叠头 */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "w-full px-4 py-3 flex items-center gap-2 text-left",
          "hover:bg-white/[0.04] transition-colors duration-150",
          "border-b border-white/[0.06]",
        )}
        aria-expanded={expanded}
      >
        <Brain
          className={cn(
            "w-3.5 h-3.5 shrink-0 transition-colors",
            streaming ? "text-brand-400" : "text-ink-700",
          )}
          strokeWidth={2}
        />
        <span className="text-[12px] font-medium text-ink-900 tracking-tight shrink-0">
          Agent 在想什么
        </span>
        <span className="text-[10px] mono text-ink-500 shrink-0 tabular-nums">
          {visibleThoughts.length}
          {replanCount > 0 && (
            <>
              <span className="mx-1 text-ink-400">·</span>
              <span className="text-amber-400">{replanCount} 重规划</span>
            </>
          )}
        </span>
        {/* streaming 时脉冲点 */}
        {streaming && (
          <span
            className="inline-block w-1.5 h-1.5 rounded-full bg-brand-400 animate-pulse shrink-0"
            aria-label="正在思考"
          />
        )}
        {/* 折叠态：摘要 */}
        {!expanded && (
          <span className="text-[11px] text-ink-500 truncate flex-1 min-w-0 italic">
            {summary}
          </span>
        )}
        <ChevronDown
          className={cn(
            "w-3.5 h-3.5 text-ink-500 shrink-0 ml-auto transition-transform duration-200",
            !expanded && "-rotate-90",
          )}
          strokeWidth={2.5}
        />
      </button>

      {/* 展开态：完整时间线 */}
      {expanded && (
        <ol className="px-3 py-2.5 space-y-2 max-h-[360px] overflow-y-auto animate-collapse-in">
          {timeline.length === 0 && streaming && (
            <li className="px-2 py-1.5 flex items-center gap-1.5 text-xs text-ink-500 italic">
              <Loader2 className="w-3 h-3 animate-spin" strokeWidth={2} />
              <span>等待 Agent 开始思考……</span>
            </li>
          )}
          {timeline.map((item, idx) => {
            const isLast = idx === timeline.length - 1;
            if (item.kind === "replan") {
              return (
                <ReplanDivider
                  key={`replan-${item.seq}`}
                  reason={item.reason}
                  fromTool={item.fromTool}
                />
              );
            }
            return (
              <ThoughtItem
                key={`thought-${item.seq}`}
                text={item.text}
                timestamp_ms={item.timestamp_ms}
                now={now}
                isLatest={isLast && streaming}
              />
            );
          })}
        </ol>
      )}
    </div>
  );
}

// ============================================================
// 单条思考条目
// ============================================================

function ThoughtItem({
  text,
  timestamp_ms,
  now,
  isLatest,
}: {
  text: string;
  timestamp_ms: number | null;
  now: number;
  isLatest: boolean;
}) {
  const relative = formatRelativeTime(timestamp_ms, now);
  return (
    <li className="px-2 py-1.5 rounded animate-fade-in-up flex items-start gap-2">
      <span
        className={cn(
          "mt-1.5 inline-block w-1 h-1 rounded-full shrink-0",
          isLatest ? "bg-brand-400 animate-pulse" : "bg-ink-500/60",
        )}
        aria-hidden
      />
      <div className="flex-1 min-w-0">
        <p className="text-[12px] leading-relaxed text-ink-800 tracking-tight">
          {text}
        </p>
        {relative && (
          <span className="text-[10px] mono text-ink-500 mt-0.5 inline-block tabular-nums">
            {relative}
          </span>
        )}
      </div>
    </li>
  );
}

// ============================================================
// Replan 分隔线（区分前后两轮思考）
// ============================================================

function ReplanDivider({ reason, fromTool }: { reason: string; fromTool: string }) {
  const reasonLabel = FAILURE_REASON_LABEL[reason] ?? reason;
  return (
    <li className="my-2 flex items-center gap-2 px-2">
      <span className="h-px flex-1 bg-amber-400/30" aria-hidden />
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium text-amber-300 bg-amber-500/10 border border-amber-500/30">
        <TriangleAlert className="w-3 h-3" strokeWidth={2} />
        <span>重新规划</span>
        <span className="mx-1 text-amber-500/60">·</span>
        <span>{reasonLabel}</span>
        <span className="text-amber-500/60 ml-1">
          来自 <span className="mono">{fromTool}</span>
        </span>
      </span>
      <span className="h-px flex-1 bg-amber-400/30" aria-hidden />
    </li>
  );
}
