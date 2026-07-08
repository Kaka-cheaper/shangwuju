"use client";

/**
 * Planner Mode 切换器 chip（W3 C4）。
 *
 * 设计原则：
 * - 「不要做太显眼，但要可点」：放在顶栏右侧、低饱和度 ink 配色、点击才显眼
 * - 单击循环 rule → llm → rule
 * - cookie 持久化（store.setPlannerMode 内）
 * - hover 展示规则范式 / LLM 范式各自的一句话说明
 *
 * 首屏校准（cookie 优先于 /health）已抽到 `useBootstrapPlannerMode()`（不依赖
 * 本组件是否挂载，Web/移动端根组件各调一次）——本组件只负责展示 store 当前值
 * + 点击循环切换，不再自己跑校准（移动端此前不挂这个徽章，校准从未触发过，
 * 见该 hook 的 docstring）。
 */

import { useChatStore } from "@/lib/store";
import type { PlannerMode } from "@/lib/types";
import { cn } from "@/lib/utils";

const MODE_LABEL: Record<PlannerMode, string> = {
  rule: "规则",
  llm: "LLM",
};

const MODE_TITLE: Record<PlannerMode, string> = {
  rule: "不调用大模型的纯算法路径，毫秒级出方案，断网也能跑（模式可随时切换 · 大模型不可用时自动回到规则路径）",
  llm: "让大模型自己拿主意，看它怎么权衡你的多个偏好（模式可随时切换 · 大模型不可用时自动回到规则路径）",
};

const MODE_DOT: Record<PlannerMode, string> = {
  rule: "bg-ink-500",
  llm: "bg-gradient-to-br from-accent-400 to-accent-600",
};

export default function PlannerModeBadge() {
  const mode = useChatStore((s) => s.plannerMode);
  const streaming = useChatStore((s) => s.streaming);
  const setPlannerMode = useChatStore((s) => s.setPlannerMode);

  const next: PlannerMode = mode === "rule" ? "llm" : "rule";
  const handleClick = () => {
    if (streaming) return;
    setPlannerMode(next);
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={streaming}
      title={MODE_TITLE[mode] + `（点击切换为 ${MODE_LABEL[next]}）`}
      aria-label={`当前 planner 模式：${MODE_LABEL[mode]}，点击切换为 ${MODE_LABEL[next]}`}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm",
        "border border-black/[0.08] bg-white/[0.68] text-ink-900 tracking-tight shadow-sm",
        "hover:border-accent-400/50 hover:bg-white/[0.88] hover:text-ink-900",
        "transition-colors backdrop-blur",
        "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-white/[0.68] disabled:hover:text-ink-900 disabled:hover:border-black/[0.08]",
      )}
    >
      <span
        aria-hidden
        className={cn("w-1.5 h-1.5 rounded-full", MODE_DOT[mode])}
      />
      <span className="font-bold">{MODE_LABEL[mode]}</span>
    </button>
  );
}

