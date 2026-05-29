"use client";

/**
 * Planner Mode 切换器 chip（W3 C4）。
 *
 * 设计原则：
 * - 「不要做太显眼，但要可点」：放在顶栏右侧、低饱和度 ink 配色、点击才显眼
 * - 单击循环 rule → llm → rule
 * - cookie 持久化（store.setPlannerMode 内）
 * - hover 展示规则范式 / LLM 范式各自的一句话说明
 * - 首屏挂载时拉 /health 校准（环境变量级 mode）
 */

import { useEffect } from "react";

import { useChatStore } from "@/lib/store";
import type { HealthResponse, PlannerMode } from "@/lib/types";
import { API_BASE, cn, getPlannerModeFromCookie } from "@/lib/utils";

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
  llm: "bg-gradient-to-br from-brand-400 to-accent-500",
};

export default function PlannerModeBadge() {
  const mode = useChatStore((s) => s.plannerMode);
  const streaming = useChatStore((s) => s.streaming);
  const setPlannerMode = useChatStore((s) => s.setPlannerMode);

  // 客户端 mount：cookie > /health 给一个初始值（静默不弹 toast）
  useEffect(() => {
    const fromCookie = getPlannerModeFromCookie();
    if (fromCookie) {
      setPlannerMode(fromCookie, { silent: true });
      return;
    }
    let cancelled = false;
    fetch(`${API_BASE}/health`)
      .then((r) => r.json() as Promise<HealthResponse>)
      .then((data) => {
        if (cancelled) return;
        if (data.planner_mode === "llm" || data.planner_mode === "rule") {
          // 仅在 cookie 缺省时跟随后端 env
          setPlannerMode(data.planner_mode, { silent: true });
        }
      })
      .catch(() => {
        // /health 拉不到时保持 default rule，不打扰
      });
    return () => {
      cancelled = true;
    };
    // 仅在 mount 时执行一次：初始化阶段不依赖 mode 自身
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        "inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px]",
        "border border-black/[0.08] bg-black/[0.02] text-ink-700 tracking-tight",
        "hover:border-black/[0.12] hover:bg-black/[0.04] hover:text-ink-900",
        "transition-colors backdrop-blur",
        "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-black/[0.02] disabled:hover:text-ink-700 disabled:hover:border-black/[0.08]",
      )}
    >
      <span
        aria-hidden
        className={cn("w-1.5 h-1.5 rounded-full", MODE_DOT[mode])}
      />
      <span className="font-medium">{MODE_LABEL[mode]}</span>
    </button>
  );
}
