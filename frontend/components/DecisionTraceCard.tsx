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
 *
 * edge_v1（节点+边模型）兼容说明：
 *   - critic_attempts.violation_codes 已从 stages_incomplete 演进为 nodes_incomplete /
 *     invariant_broken / hop_infeasible 等，但本组件只把 code 当作小标签字面渲染，不解析
 *   - 后端 violations 的 field_path 已从 stages[i] 改为 nodes[i] / hops[j]，
 *     但 field_path 在前端从未渲染（仅 codes + feedback_summary 给评委看），故无需改组件
 *   - fallback_chain.from_stage / to_stage 是 fallback 链路名（llm_first → llm_backprompt → ils），
 *     不是 itinerary.stages，与本次重构正交
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
  llm_first: "text-emerald-600 bg-emerald-500/10 border-emerald-400/30",
  llm_backprompt: "text-amber-600 bg-amber-500/10 border-amber-400/30",
  ils: "text-sky-600 bg-sky-500/10 border-sky-400/30",
  rule: "text-ink-600 bg-ink-200/50 border-ink-300/30",
  give_up: "text-rose-600 bg-rose-500/10 border-rose-400/30",
};

export default function DecisionTraceCard({ trace }: DecisionTraceCardProps) {
  // M1：默认展开（评委 demo 5 分钟内看到「LLM-Modulo critic 三层镜像」决策链路）
  const [open, setOpen] = useState(true);

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
    <div className="card mt-4 border border-amber-400/20 bg-gradient-to-br from-amber-50/60 to-white">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex w-full items-center justify-between rounded-lg",
          "px-4 py-3 text-left transition-colors",
          "hover:bg-amber-500/5",
        )}
        aria-expanded={open}
        title="LLM-Modulo 论文范式 · 三层 critic 镜像"
      >
        <div className="flex items-center gap-2">
          <Icons.spark className="h-4 w-4 text-amber-500" />
          <span className="text-sm font-semibold text-amber-800">决策链路</span>
          <span className="text-xs text-amber-600/70">看 Agent 怎么想的</span>
          <span className={cn(
            "rounded-full border px-2 py-0.5 text-xs",
            strategyColor,
          )}>
            {strategyLabel}
          </span>
          <span className="text-xs text-ink-500">· {summaryText}</span>
        </div>
        <span
          className={cn(
            "text-ink-500 transition-transform text-sm leading-none",
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
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-700">
                <Icons.spark className="h-3 w-3" />
                规划思路
              </h4>
              {t.blueprint_rationale && (
                <p className="mb-2 text-sm leading-relaxed text-ink-800">
                  {t.blueprint_rationale}
                </p>
              )}
              {t.weights_explanation && (
                <p className="text-xs text-ink-500">
                  权重：{t.weights_explanation}
                </p>
              )}
            </section>
          )}

          {/* ===== 2. critic 修正历史 ===== */}
          {t.critic_attempts.length > 0 && (
            <section>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-700">
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
                        ? "border-emerald-400/25 bg-emerald-500/5 text-emerald-800"
                        : "border-rose-400/25 bg-rose-500/5 text-rose-800",
                    )}
                  >
                    <div className="mb-1 flex items-center gap-1.5">
                      <span className="font-semibold">第 {a.attempt_n} 次</span>
                      <span className="text-ink-400">·</span>
                      <span>
                        {a.resolved ? "已修正" : "进行中"}
                      </span>
                    </div>
                    {a.violation_codes.length > 0 && (
                      <div className="mb-1 flex flex-wrap gap-1">
                        {a.violation_codes.map((code, codeIdx) => (
                          <span
                            key={`${idx}-${codeIdx}-${code}`}
                            className="rounded-sm bg-ink-200/50 px-1.5 py-0.5 text-xs uppercase text-ink-600"
                          >
                            {code}
                          </span>
                        ))}
                      </div>
                    )}
                    <p className="text-ink-600">{a.feedback_summary}</p>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {/* ===== 3. fallback 链 ===== */}
          {t.fallback_chain.length > 0 && (
            <section>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-700">
                <Icons.pulse className="h-3 w-3" />
                Fallback 链
              </h4>
              <ol className="space-y-1.5">
                {t.fallback_chain.map((h, idx) => (
                  <li
                    key={idx}
                    className="flex items-center gap-2 text-xs text-ink-600"
                  >
                    <span className="rounded-sm bg-ink-200/50 px-1.5 py-0.5 text-xs">
                      {h.from_stage}
                    </span>
                    <span className="text-ink-400">→</span>
                    <span className="rounded-sm bg-amber-500/10 px-1.5 py-0.5 text-xs text-amber-700">
                      {h.to_stage}
                    </span>
                    <span className="text-ink-500">{h.reason}</span>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {/* ===== 4. 备选候选 ===== */}
          {t.alternatives_considered.length > 0 && (
            <section>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-700">
                <Icons.pin className="h-3 w-3" />
                考虑过的备选
              </h4>
              <ol className="space-y-1.5">
                {t.alternatives_considered.map((a, idx) => (
                  <li
                    key={`${a.target_id}-${idx}`}
                    className="flex items-start gap-2 text-xs"
                  >
                    <span className="mt-0.5 rounded-full bg-ink-200/50 px-1.5 py-0 text-xs text-ink-600">
                      #{a.rank}
                    </span>
                    <div className="flex-1">
                      <div className="text-ink-800">
                        {a.target_name}
                        <span className="ml-1 text-ink-500">
                          ({a.target_kind === "poi" ? "活动" : "餐厅"})
                        </span>
                      </div>
                      <div className="text-ink-500">{a.reason_rejected}</div>
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

