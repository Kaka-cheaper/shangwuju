/**
 * Critic 时间线 —— critic_violations / critic_fix_attempt / plan_fallback
 * 三个独立子数组（store.criticReport）合并成一条按 seq 排序的时间线。
 *
 * 抽出动机：原是 ThoughtPanel.tsx 的私有实现（Step 2「质检与自愈」小节）。
 * 移动端 MobileThoughtTimeline（A1：质检与自愈时间线）需要完全同一套合并 +
 * 文案生成逻辑——这是"系统自愈过程可视化"这个卖点在两端必须逐字一致的地方，
 * 不是各写一份"看起来差不多"的版本。
 *
 * 类型推导手法沿用 ThoughtPanel 原实现：不直接 import CriticReport（store 内部
 * 类型，未列入 store.ts 的公开 re-export 列表），而是用 ReturnType 从
 * useChatStore.getState() 结构化推导——保持 lib/store/types.ts 对外部组件的
 * "非公开 API 表面"边界不变。
 */

import type { useChatStore } from "./store";

export type CriticReportShape = ReturnType<typeof useChatStore.getState>["criticReport"];
export type CriticViolationRound = CriticReportShape["violationRounds"][number];
export type CriticFixAttempt = CriticReportShape["fixAttempts"][number];
export type PlanFallbackHop = CriticReportShape["fallbackHops"][number];

export type CriticTimelineItem =
  | { kind: "violations"; data: CriticViolationRound }
  | { kind: "fix_attempt"; data: CriticFixAttempt }
  | { kind: "fallback"; data: PlanFallbackHop };

export function buildCriticTimeline(report: CriticReportShape): CriticTimelineItem[] {
  const merged: CriticTimelineItem[] = [
    ...report.violationRounds.map((d): CriticTimelineItem => ({ kind: "violations", data: d })),
    ...report.fixAttempts.map((d): CriticTimelineItem => ({ kind: "fix_attempt", data: d })),
    ...report.fallbackHops.map((d): CriticTimelineItem => ({ kind: "fallback", data: d })),
  ];
  merged.sort((a, b) => a.data.seq - b.data.seq);
  return merged;
}

/** 折叠态摘要行文案——人话、系统能力展示口吻（不是错误道歉）。 */
export function criticHeadline(item: CriticTimelineItem): string {
  if (item.kind === "violations") {
    const n = item.data.violations.length;
    // critic_node 的 itinerary=None 早返回分支（候选为空/蓝图生成失败）会推一条
    // violations=[] 但仍 has_critical=True 的 critic_violations（读码 + 真实
    // stub 冒烟验证核实：backend/agent/graph/nodes/critic.py 的 itinerary is None
    // 分支）——这不是"零问题"，是"这稿压根没生成出方案"，文案分开写。
    return n > 0
      ? `质检拦下 ${n} 个问题（第 ${item.data.fixAttempt} 稿），已自动返工`
      : `第 ${item.data.fixAttempt} 稿未能生成有效方案，正在重新规划`;
  }
  if (item.kind === "fix_attempt") {
    return `第 ${item.data.attempt} 稿返工中……`;
  }
  return `换算法引擎重排：${item.data.reason}`;
}
