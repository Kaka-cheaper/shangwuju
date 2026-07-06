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
import {
  ArrowRightLeft,
  Brain,
  ChevronDown,
  Loader2,
  ShieldAlert,
  TriangleAlert,
  Wrench,
} from "lucide-react";

import { useChatStore } from "@/lib/store";
import { buildCriticTimeline, criticHeadline } from "@/lib/critic-timeline";
import { cn, FAILURE_REASON_LABEL, PLAN_FALLBACK_STAGE_LABEL } from "@/lib/utils";

// ============================================================
// 时间线 item（thought 或 replan 分隔线）
// ============================================================

type TimelineItem =
  | { kind: "thought"; seq: number; text: string; timestamp_ms: number | null }
  | { kind: "replan"; seq: number; reason: string; fromTool: string };

function buildTimeline(
  thoughts: ReadonlyArray<{ seq: number; text: string; timestamp_ms: number | null }>,
  replans: ReadonlyArray<{ seq: number; reason: string; fromTool: string }>,
): TimelineItem[] {
  const merged: TimelineItem[] = [
    ...thoughts.map(
      (t): TimelineItem => ({
        kind: "thought",
        seq: t.seq,
        text: t.text,
        timestamp_ms: t.timestamp_ms,
      }),
    ),
    ...replans.map(
      (r): TimelineItem => ({
        kind: "replan",
        seq: r.seq,
        reason: r.reason,
        fromTool: r.fromTool,
      }),
    ),
  ];
  // 按 seq 升序（SSE 单次会话内单调递增）
  merged.sort((a, b) => a.seq - b.seq);
  return merged;
}

// ============================================================
// Step 2：质检与自愈——critic_violations / critic_fix_attempt / plan_fallback
// 三个独立子数组（store.criticReport）合并成一条时间线，与上面 thoughts/replans
// 的合并同一手法（按 seq 排序），但视觉上独立成一个小节（见组件主体），不与
// "Agent 在想什么"的自由文本叙事混在一起——两者是不同粒度的信息（叙事 vs.
// 结构化质检结果），分开陈列让评委一眼分清"agent 在说什么"和"质检真的挡了什么"。
//
// buildCriticTimeline / criticHeadline 已抽到 lib/critic-timeline.ts（移动端
// MobileThoughtTimeline 的 A1 复用同一份判定逻辑，见该文件 docstring）。
// ============================================================

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
  const criticReport = useChatStore((s) => s.criticReport);
  // Step 2：itinerary != null 是"这一轮的方案已经定稿"的代理信号——配合 streaming
  // 判定质检自愈时间线上最后一条是否仍是"敞口中"（还没被后续事件接住）。
  const itinerary = useChatStore((s) => s.itinerary);

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

  const criticTimeline = useMemo(
    () => buildCriticTimeline(criticReport),
    [criticReport],
  );
  const criticCount = criticTimeline.length;

  // Step 2：路演体感优先——质检自愈是本次要展示的能力，新一轮一出现就该自动
  // 展开，不能指望评委记得手动点开这个折叠面板。只在「本轮新出现自愈活动」时
  // 触发（criticCount 从 0 变化），不影响没有自愈事件的普通轮次的既有折叠行为。
  useEffect(() => {
    if (criticCount > 0) setExpanded(true);
  }, [criticCount]);

  // R4 #8：thoughts 为空 + 不在 streaming 时不渲染（不显示空面板占位）——
  // Step 2：criticCount > 0 时即便 thoughts 恰好是空的也不能不渲染（理论上不会
  // 发生，但防御性地把它纳入判据，语义上"有值得看的内容就不该 return null"）。
  if (thoughts.length === 0 && !streaming && criticCount === 0) {
    return null;
  }

  const latestThought = thoughts[thoughts.length - 1];
  const latestCritic = criticTimeline[criticTimeline.length - 1];
  // 折叠态摘要行：critic 自愈事件比最新一条 thought 更新时优先展示它——
  // 质检拦下问题/换算法引擎重排是比常规 agent_thought 更值得评委一眼看到的信号。
  const criticIsNewer =
    latestCritic != null &&
    (latestThought == null || latestCritic.data.seq >= latestThought.seq);
  const summary = criticIsNewer
    ? truncate(criticHeadline(latestCritic), 50)
    : latestThought
      ? truncate(latestThought.text, 50)
      : "等待 Agent 开始思考……";
  const replanCount = replans.length;

  return (
    <div className="relative card mt-3 overflow-hidden border border-amber-400/20 bg-gradient-to-br from-amber-50/60 to-white">
      {/* streaming 时顶部流动黄光带 */}
      {streaming && (
        <div
          aria-hidden
          className="absolute top-0 left-0 right-0 h-[2px] shimmer-bar z-10"
        />
      )}
      {/* 折叠头 */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "w-full px-4 py-3 flex items-center gap-2 text-left",
          "hover:bg-black/[0.03] transition-colors duration-150",
          "border-b border-black/[0.06]",
        )}
        aria-expanded={expanded}
      >
        <Brain
          className={cn(
            "w-3.5 h-3.5 shrink-0 transition-colors",
            streaming ? "text-brand-600" : "text-ink-700",
          )}
          strokeWidth={2}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-ink-900 tracking-tight shrink-0">
              Agent 在想什么
            </span>
            <span className="text-xs mono text-ink-500 shrink-0 tabular-nums">
              {thoughts.length}
              {replanCount > 0 && (
                <>
                  <span className="mx-1 text-ink-400">·</span>
                  <span className="text-amber-500">{replanCount} 重规划</span>
                </>
              )}
              {criticCount > 0 && (
                <>
                  <span className="mx-1 text-ink-400">·</span>
                  <span className="text-amber-500">{criticCount} 自愈</span>
                </>
              )}
            </span>
            {/* streaming 时脉冲点 */}
            {streaming && (
              <span
                className="inline-block w-1.5 h-1.5 rounded-full bg-brand-500 animate-pulse shrink-0"
                aria-label="正在思考"
              />
            )}
          </div>
          {/* 折叠态：摘要在第二行 */}
          {!expanded && (
            <span className="block text-xs text-ink-400 truncate mt-0.5">
              {summary}
            </span>
          )}
        </div>
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
        <div className="max-h-[480px] overflow-y-auto animate-collapse-in">
          <ol className="px-3 py-2.5 space-y-2">
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

          {/* Step 2：质检与自愈——独立小节，不与上面的自由文本思考叙事混排。
              措辞口吻＝系统能力的展示，不是错误道歉（"质检拦下 N 个问题，已自动
              返工" 而不是 "出错了/失败了"）。*/}
          {criticTimeline.length > 0 && (
            <div className="px-3 pb-2.5 pt-1 border-t border-black/[0.06]">
              <div className="flex items-center gap-1.5 px-2 py-1.5">
                <ShieldAlert className="w-3.5 h-3.5 text-amber-500 shrink-0" strokeWidth={2} />
                <span className="text-xs font-semibold text-ink-700 tracking-tight">
                  质检与自愈
                </span>
                <span className="text-xs mono text-ink-500 tabular-nums">
                  {criticTimeline.length}
                </span>
              </div>
              <ol className="space-y-1.5">
                {criticTimeline.map((item, idx) => {
                  const isFrontier =
                    idx === criticTimeline.length - 1 && streaming && itinerary == null;
                  if (item.kind === "violations") {
                    return (
                      <ViolationRoundItem
                        key={`violations-${item.data.seq}`}
                        data={item.data}
                        isFrontier={isFrontier}
                        hasLaterEvent={idx < criticTimeline.length - 1}
                      />
                    );
                  }
                  if (item.kind === "fix_attempt") {
                    return (
                      <FixAttemptItem
                        key={`fix-${item.data.seq}`}
                        data={item.data}
                        isFrontier={isFrontier}
                      />
                    );
                  }
                  return (
                    <FallbackHopItem key={`fallback-${item.data.seq}`} data={item.data} />
                  );
                })}
              </ol>
            </div>
          )}
        </div>
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
        <p className="text-xs leading-relaxed text-ink-800 tracking-tight">
          {text}
        </p>
        {relative && (
          <span className="text-xs mono text-ink-500 mt-0.5 inline-block tabular-nums">
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
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium text-amber-600 bg-amber-500/10 border border-amber-500/30">
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

// ============================================================
// Step 2：质检与自愈——3 种条目（违规轮次 / 返工中 / 降级跳变）
// ============================================================

/** 一轮 critic 违规判定：拦下的问题逐条人话展示（violations[].message 本就是
 * 人话，不需要二次翻译）+ 是否已被后续事件接住（返工/降级）的状态标记。 */
function ViolationRoundItem({
  data,
  isFrontier,
  hasLaterEvent,
}: {
  data: { fixAttempt: number; violations: { message: string; field_path: string }[] };
  isFrontier: boolean;
  hasLaterEvent: boolean;
}) {
  const shown = data.violations.slice(0, 6);
  const overflow = data.violations.length - shown.length;
  // 见 criticHeadline 注释：violations=[] 是"这稿压根没生成出方案"（candidate 为空
  // / 蓝图生成失败），不是"零问题"——文案不说"拦下 0 个问题"制造矛盾感。
  const noBlueprintProduced = data.violations.length === 0;
  return (
    <li className="rounded-md border border-amber-400/25 bg-amber-50/60 px-2.5 py-2 animate-fade-in-up">
      <div className="flex items-center gap-1.5">
        <ShieldAlert className="w-3 h-3 text-amber-500 shrink-0" strokeWidth={2} />
        <span className="text-xs font-medium text-amber-700 tracking-tight">
          {noBlueprintProduced
            ? `第 ${data.fixAttempt} 稿未能生成有效方案`
            : `质检拦下 ${data.violations.length} 个问题（第 ${data.fixAttempt} 稿）`}
        </span>
      </div>
      {shown.length > 0 && (
        <ul className="mt-1 ml-[18px] space-y-1 list-disc marker:text-amber-400">
          {shown.map((v, i) => (
            <li key={i} className="text-xs text-ink-700 leading-relaxed">
              {v.message}
              {v.field_path && (
                <span className="block text-xs text-ink-400 mono">{v.field_path}</span>
              )}
            </li>
          ))}
          {overflow > 0 && (
            <li className="text-xs text-ink-400">还有 {overflow} 项…</li>
          )}
        </ul>
      )}
      <div className="mt-1 ml-[18px]">
        {isFrontier ? (
          <span className="inline-flex items-center gap-1 text-xs text-brand-600">
            <Loader2 className="w-3 h-3 animate-spin" strokeWidth={2} />
            AI 正在修正……
          </span>
        ) : hasLaterEvent ? (
          <span className="text-xs text-emerald-500">已自动返工</span>
        ) : null}
      </div>
    </li>
  );
}

/** critic backprompt 重做中：正在按质检反馈重写第 N 稿。不直接展示后端
 * feedback_text（常是「详见上一条 critic_violations」这类内部占位文案，
 * 暴露给用户是实现细节泄漏）——文案自己写，人话、口吻积极。 */
function FixAttemptItem({
  data,
  isFrontier,
}: {
  data: { attempt: number };
  isFrontier: boolean;
}) {
  return (
    <li className="flex items-center gap-1.5 px-2 py-1 text-xs animate-fade-in-up">
      {isFrontier ? (
        <Loader2 className="w-3 h-3 text-brand-600 animate-spin shrink-0" strokeWidth={2} />
      ) : (
        <Wrench className="w-3 h-3 text-ink-500 shrink-0" strokeWidth={2} />
      )}
      <span className={isFrontier ? "text-brand-600 font-medium" : "text-ink-600"}>
        第 {data.attempt} 稿{isFrontier ? "返工中……" : "已重新生成"}
      </span>
    </li>
  );
}

/** 4 级降级链跳变：LLM 首次规划 → LLM 重新生成 → ILS 算法引擎 → 规则引擎兜底。
 * reason 本就是后端写好的人话句子，作为主文案；from/to 阶段标签作为结构化的
 * 辅助信息（同 ToolItem 人话标签 + mono 技术细节的既有双行惯例）。 */
function FallbackHopItem({
  data,
}: {
  data: { from: string; to: string; reason: string };
}) {
  const fromLabel = PLAN_FALLBACK_STAGE_LABEL[data.from] ?? data.from;
  const toLabel = PLAN_FALLBACK_STAGE_LABEL[data.to] ?? data.to;
  return (
    <li className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 animate-fade-in-up">
      <div className="flex items-center gap-1.5">
        <ArrowRightLeft className="w-3 h-3 text-amber-600 shrink-0" strokeWidth={2} />
        <span className="text-xs font-medium text-amber-700">换算法引擎重排</span>
      </div>
      <div className="mt-0.5 ml-[18px] text-xs text-ink-700">{data.reason}</div>
      <div className="mt-0.5 ml-[18px] text-xs text-ink-500 mono">
        {fromLabel} → {toLabel}
      </div>
    </li>
  );
}

