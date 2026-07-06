"use client";

/**
 * useBootstrapPlannerMode —— Planner 模式启动校准，不依赖 UI 挂载。
 *
 * 抽出动机：校准逻辑原本埋在 PlannerModeBadge.tsx 的 useEffect 里（cookie
 * 优先于 /health）——校准这件事因此被绑定到"这个徽章组件有没有被挂载"这个
 * 无关的 UI 决策上。移动端根组件（MobileHomeView）此前不挂 PlannerModeBadge，
 * 于是校准从未跑过，plannerMode 永远停在 initial-state.ts 硬编码的 "rule"，
 * 即使后端 env 配的是 llm、即使用户之前在桌面端切换并写过 cookie，移动端也
 * 感知不到——静默跑的是降智版规则规划。
 *
 * 修复：把校准逻辑抽成一个不依赖任何组件是否渲染的 hook，Web（HomeView）和
 * 移动端（MobileHomeView）各在根组件调用一次即可；PlannerModeBadge 之后只
 * 负责展示当前 store 值 + 点击循环切换，不再自己跑校准（避免两处都跑一遍
 * /health，也避免"badge 没挂载 = 校准没跑"这种耦合再次出现）。
 *
 * 行为契约（与抽出前 PlannerModeBadge.tsx:44-68 逐字等价）：
 *   1. cookie 存在（用户曾显式选择）→ setPlannerMode(fromCookie, {silent:true})，直接返回
 *   2. cookie 不存在 → fetch /health，命中 rule/llm 才 setPlannerMode(..., {silent:true, persist:false})
 *   3. /health 失败 → 静默保持 initialState 的默认值，不打扰
 *   仅在 mount 时执行一次（依赖数组为空，同原实现）。
 */

import { useEffect } from "react";

import { useChatStore } from "../store";
import type { HealthResponse } from "../types";
import { API_BASE, getPlannerModeFromCookie } from "../utils";

export function useBootstrapPlannerMode(): void {
  const setPlannerMode = useChatStore((s) => s.setPlannerMode);

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
          // 仅在 cookie 缺省时跟随后端 env；persist:false 不写 cookie
          setPlannerMode(data.planner_mode, { silent: true, persist: false });
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
}
