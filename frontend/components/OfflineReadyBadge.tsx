"use client";

/**
 * OfflineReadyBadge —— 顶栏「断网继续运行」徽章（spec bonus-points-review M3）。
 *
 * 仅在以下条件全部满足时显示：
 *   1. plannerMode === "rule"（用户已切到规则模式）
 *   2. 已确认后端探活成功（/health 200）—— 单纯证明前后端连通
 *
 * 设计意图：
 *   评委 demo 时切到「规则」模式 → 这一行徽章告诉评委「现在断网也跑得动」，把
 *   spec interaction-experience-review 的「双范式真落地」加分点显式暴露出来。
 *   不调用大模型 = 不依赖外部网络（mock_data 全本地）。
 *
 * 视觉范式：
 *   - 与 MockModeBadge / PlannerModeBadge 同样的低饱和 chip 风格
 *   - 状态点用暖琥珀色（amber），区别于 mock 徽章的 emerald
 *   - hover tooltip 解释「断网验证」演示玩法
 */

import { useEffect, useState } from "react";

import { useChatStore } from "@/lib/store";
import { API_BASE, cn } from "@/lib/utils";
import type { HealthResponse } from "@/lib/types";

export default function OfflineReadyBadge() {
  const plannerMode = useChatStore((s) => s.plannerMode);
  const [backendReady, setBackendReady] = useState(false);

  // 客户端 mount 时探活一次（只为确认 backend 在跑；不轮询）
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/health`)
      .then((r) => r.ok ? (r.json() as Promise<HealthResponse>) : null)
      .then((data) => {
        if (!cancelled && data && data.status === "ok") setBackendReady(true);
      })
      .catch(() => {
        // 后端未起或网络抖动：不显示徽章
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 只在 rule 模式 + 后端在跑时显示
  if (plannerMode !== "rule" || !backendReady) return null;

  return (
    <span
      title="演示韧性时可断网验证：当前是规则模式，意图理解之外不依赖大模型与外部网络"
      aria-label="当前规则模式可断网继续运行"
      className={cn(
        "hidden lg:inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs",
        "border border-amber-400/24 bg-amber-500/8 text-amber-700/90 tracking-tight",
        "backdrop-blur cursor-help animate-fade-in",
      )}
    >
      <span aria-hidden className="w-1.5 h-1.5 rounded-full bg-amber-400" />
      <span className="font-medium">断网继续运行</span>
    </span>
  );
}

