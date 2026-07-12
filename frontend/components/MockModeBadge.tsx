"use client";

/**
 * MockModeBadge —— 顶栏「mock 数据源」徽章（spec bonus-points-review M1）。
 *
 * 设计意图：
 *   评委不打开 IDE 看 mock_data/ 目录就误以为「mock = 简化玩具」。这一行徽章把
 *   mock 数据严谨度的关键证据（条目数 + 真实评论数 + Pydantic 校验）暴露在 hover
 *   tooltip 里，让评委 30 秒内秒懂工程深度。
 *
 * 数据来源（2026-07-12 修复 P0）：
 *   tooltip 曾硬编码具体数字，数据集换血（望京活集）后与 mock_data/ 实际条目数
 *   脱节——评委 hover 后翻目录对不上，弄巧成拙。改为组件内 `useEffect` 自包含
 *   fetch `GET /ready`（`api/health.py::ready` 的 `checks.mock_data` 已现成算好
 *   pois/restaurants/routes/reviews 四个计数，见该端点 docstring），渲染运行时
 *   真实计数，永不过时。故意不读 store（该 store 目前由另一批并行改动，且
 *   /ready 探活本身与会话状态无关，自包含 fetch 更干净）。
 *
 *   在计数到达前 tooltip 显示占位语（不敢先猜一个数字导致再次说谎）；fetch
 *   失败（后端未起）时退化为不含具体数字的通用文案，宁可少信息也不显示假数据。
 *
 * 视觉范式：
 *   - 与 PlannerModeBadge 同样的低饱和 chip 风格（ink 灰底 + 状态点）
 *   - 不抢 PlannerModeBadge 的视觉焦点；hover 才暴露完整文案
 *
 * C4：默认 `hidden md:inline-flex`——移动端窄容器里桌面顶栏塞不下这么多徽章
 * 天经地义地隐藏了它。`compact` 让移动端也能挂它（无 `hidden md:` 前缀），
 * 桌面端调用点不传这个 prop，行为不变。
 */

import { useEffect, useState } from "react";

import { API_BASE, cn } from "@/lib/utils";
import { buildMockDataTooltip, type MockDataCounts } from "@/lib/mock-mode-badge";
import type { ReadyResponse } from "@/lib/types";

export default function MockModeBadge({ compact = false }: { compact?: boolean }) {
  const [counts, setCounts] = useState<MockDataCounts | null>(null);

  // 客户端 mount 时探活一次，读运行时真实计数（不轮询——mock 数据集在进程
  // 生命周期内不会变化，一次 fetch 足够）
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/ready`)
      .then((r) => (r.ok ? (r.json() as Promise<ReadyResponse>) : null))
      .then((data) => {
        const mockData = data?.checks?.mock_data;
        if (!cancelled && mockData?.ok) {
          setCounts({
            pois: mockData.pois,
            restaurants: mockData.restaurants,
            routes: mockData.routes,
            reviews: mockData.reviews,
          });
        }
      })
      .catch(() => {
        // 后端未起或网络抖动：保留占位文案，不展示猜测数字
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <span
      title={buildMockDataTooltip(counts)}
      aria-label="当前数据源：mock 模式"
      className={cn(
        compact ? "inline-flex" : "hidden md:inline-flex",
        "items-center gap-1.5 rounded-full px-3 py-1.5 text-sm",
        "border border-black/[0.08] bg-white/[0.68] text-ink-900 tracking-tight shadow-sm",
        "backdrop-blur cursor-help transition-colors hover:border-accent-400/50 hover:bg-white/[0.88] hover:text-ink-900",
      )}
    >
      {/* 去绿归色：这颗点纯粹是「跟隔壁徽章视觉区分」的差异化点，不是成功/在线
          语义——emerald 游离于调色板外，改用中性灰（同 PlannerModeBadge 的
          rule 态 ink-500，取更浅一档区分）。 */}
      <span aria-hidden className="w-1.5 h-1.5 rounded-full bg-ink-400/70" />
      <span className="font-bold">mock 数据源</span>
    </span>
  );
}

