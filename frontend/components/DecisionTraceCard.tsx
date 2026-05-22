"use client";

/**
 * DecisionTraceCard —— "AI 思考"折叠卡（Step 8 决策可解释性）
 *
 * 渲染后端 Itinerary.decision_trace 三段：
 *   - 蓝图 rationale（LLM 自报"为什么这样规划"）
 *   - critic 修正历史（LLM-Modulo 闭环可见）
 *   - 备选候选（为什么选 X 不选 Y）
 *
 * 设计取向：
 *   - 默认折叠（不打扰主时间轴）
 *   - 数据为空时整个卡片不渲染
 *   - 颜色与 ItineraryCard 主题保持一致（黄昏深色 + brand-orange）
 */

import { useState } from "react";

import { Icons } from "@/lib/icon-map";
import type { DecisionTrace } from "@/lib/types";
import { cn } from "@/lib/utils";

interface DecisionTraceCardProps {
  trace: DecisionTrace | null | undefined;
}

function isTraceEmpty(trace: DecisionTrace | null | undefined): boolean {
  if (!trace) return true;
  return (
    !trace.blueprint_rationale &&
    !trace.weights_explanation &&
    trace.critic_attempts.length === 0 &&
    trace.alternatives_considered.length === 0 &&
    trace.fallback_chain.length === 0
  );
}

const STRATEGY_LABEL: Record<string, string> = {
  llm_first: "LLM 直出",
  llm_backprompt: "LLM 修正后通过",
  ils: "ILS 算法兜底",
  rule: "规则兜底",
  give_up: "保留当前方案",
};

const STRATEGY_COLOR: Record<string, string> = {
  llm_first: "text-emerald-300 bg-emerald-500/10 border-emerald-400/30",
  llm_backprompt: "text-amber-300 bg-amber-500/10 border-amber-400/30",
  ils: "text-sky-300 bg-sky-500/10 border-sky-400/30",
  rule: "text-slate-300 bg-slate-500/10 border-slate-400/30",
  give_up: "text-rose-300 bg-rose-500/10 border-rose-400/30",
};

export default function DecisionTraceCard({ trace }: DecisionTraceCardProps) {
  const [open, setOpen] = useState(false);

  if (isTraceEmpty(trace)) return null;
  // trace 此时一定不是 null
  const t = trace as DecisionTrace;

  const strategyLabel = STRATEGY_LABEL[t.final_strategy] ?? t.final_strategy;
  const strategyColor = STRATEGY_COLOR[t.final_strategy] ?? STRATEGY_COLOR.llm_first;

  const summaryParts: string[] = [];
  if (t.critic_attempts.length > 0) {
    summaryParts.push(`${t.critic_attempts.length} 次 critic 修正`);
  }
  if (t.alternatives_considered.length > 0) {
    summaryParts.push(`${t.alternatives_considered.length} 个备选`);
  }
  if (t.fallback_chain.length > 0) {
    summaryParts.push(`${t.fallback_chain.length} 跳 fallback`);
  }
  const summaryText = summaryParts.length > 0 ? summaryParts.join("，") : "一次通过";

  return (
    <div className="card mt-4 border border-amber-400/20 bg-gradient-to-br from-slate-900/60 to-slate-800/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex w-full items-center justify-between rounded-lg",
          "px-4 py-3 text-left transition-colors",
          "hover:bg-amber-500/5",
        )}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <Icons.spark className="h-4 w-4 text-amber-300" />
          <span className="text-sm font-semibold text-amber-100">AI 思考</span>
          <span className={cn(
            "rounded-full border px-2 py-0.5 text-xs",
            strategyColor,
          )}>
            {strategyLabel}
          </span>
          <span className="text-xs text-slate-400">· {summaryText}</span>
        </div>
        <span
          className={cn(
            "text-slate-400 transition-transform text-sm leading-none",
            open && "rotate-180",
          )}
          aria-hidden
        >
          ▾
        </span>
      </button>

      {open && (
        <div className="space-y-4 border-t border-amber-400/15 px-4 py-4">
          {/* ===== 1. 蓝图 rationale + 权重 ===== */}
          {(t.blueprint_rationale || t.weights_explanation) && (
            <section>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-200">
                <Icons.spark className="h-3 w-3" />
                规划思路
              </h4>
              {t.blueprint_rationale && (
                <p className="mb-2 text-sm leading-relaxed text-slate-200">
                  {t.blueprint_rationale}
                </p>
              )}
              {t.weights_explanation && (
                <p className="text-xs text-slate-400">
                  权重：{t.weights_explanation}
                </p>
              )}
            </section>
          )}

          {/* ===== 2. critic 修正历史 ===== */}
          {t.critic_attempts.length > 0 && (
            <section>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-200">
                <Icons.refine className="h-3 w-3" />
                Critic 修正历史
              </h4>
              <ol className="space-y-2">
                {t.critic_attempts.map((a, idx) => (
                  <li
                    key={idx}
                    className={cn(
                      "rounded-md border px-3 py-2 text-xs",
                      a.resolved
                        ? "border-emerald-400/25 bg-emerald-500/5 text-emerald-100"
                        : "border-rose-400/25 bg-rose-500/5 text-rose-100",
                    )}
                  >
                    <div className="mb-1 flex items-center gap-1.5">
                      <span className="font-semibold">第 {a.attempt_n} 次</span>
                      <span className="text-slate-400">·</span>
                      <span>
                        {a.resolved ? "已修正" : "进行中"}
                      </span>
                    </div>
                    {a.violation_codes.length > 0 && (
                      <div className="mb-1 flex flex-wrap gap-1">
                        {a.violation_codes.map((code) => (
                          <span
                            key={code}
                            className="rounded-sm bg-slate-700/50 px-1.5 py-0.5 text-[10px] uppercase text-slate-300"
                          >
                            {code}
                          </span>
                        ))}
                      </div>
                    )}
                    <p className="text-slate-300">{a.feedback_summary}</p>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {/* ===== 3. fallback 链 ===== */}
          {t.fallback_chain.length > 0 && (
            <section>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-200">
                <Icons.pulse className="h-3 w-3" />
                Fallback 链
              </h4>
              <ol className="space-y-1.5">
                {t.fallback_chain.map((h, idx) => (
                  <li
                    key={idx}
                    className="flex items-center gap-2 text-xs text-slate-300"
                  >
                    <span className="rounded-sm bg-slate-700/50 px-1.5 py-0.5 text-[10px]">
                      {h.from_stage}
                    </span>
                    <span className="text-slate-500">→</span>
                    <span className="rounded-sm bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200">
                      {h.to_stage}
                    </span>
                    <span className="text-slate-400">{h.reason}</span>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {/* ===== 4. 备选候选 ===== */}
          {t.alternatives_considered.length > 0 && (
            <section>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-200">
                <Icons.pin className="h-3 w-3" />
                考虑过的备选
              </h4>
              <ol className="space-y-1.5">
                {t.alternatives_considered.map((a, idx) => (
                  <li
                    key={`${a.target_id}-${idx}`}
                    className="flex items-start gap-2 text-xs"
                  >
                    <span className="mt-0.5 rounded-full bg-slate-700/50 px-1.5 py-0 text-[10px] text-slate-300">
                      #{a.rank}
                    </span>
                    <div className="flex-1">
                      <div className="text-slate-200">
                        {a.target_name}
                        <span className="ml-1 text-slate-500">
                          ({a.target_kind === "poi" ? "活动" : "餐厅"})
                        </span>
                      </div>
                      <div className="text-slate-400">{a.reason_rejected}</div>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
