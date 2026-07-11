/**
 * MockModeBadge 数据层单测——见 lib/mock-mode-badge.ts 模块 docstring。
 *
 * 核心断言（2026-07-12 P0 修复的回归护栏）：
 * - 渲染 `/ready` 真实计数时，tooltip 里出现的是新数字（望京活集：95/120/215/430），
 *   且**绝不含旧硬编码数字**（48/45/241/174——曾经写死在组件里、数据集换血后
 *   过时的那组值）。
 * - counts 为 null（尚未 fetch 到 / fetch 失败）时退化为不含任何具体数字的
 *   通用文案，不能编造数字。
 * - 单个维度缺失（如后端暂缺 routes/reviews 计数）时优雅跳过，不拼出
 *   "undefined" 之类的坏文案。
 */

import { describe, expect, it } from "vitest";

import { buildMockDataTooltip } from "./mock-mode-badge";

const STALE_HARDCODED_NUMBERS = ["48", "45", "241", "174"];

describe("buildMockDataTooltip", () => {
  it("渲染当前真实计数（望京活集 95/120/215/430）", () => {
    const tooltip = buildMockDataTooltip({
      pois: 95,
      restaurants: 120,
      routes: 215,
      reviews: 430,
    });
    expect(tooltip).toContain("95 个活动地点");
    expect(tooltip).toContain("120 家餐厅");
    expect(tooltip).toContain("215 条路线");
    expect(tooltip).toContain("430 条真实评论");
  });

  it("不包含任何已过时的旧硬编码数字", () => {
    const tooltip = buildMockDataTooltip({
      pois: 95,
      restaurants: 120,
      routes: 215,
      reviews: 430,
    });
    for (const stale of STALE_HARDCODED_NUMBERS) {
      expect(tooltip).not.toContain(stale);
    }
  });

  it("counts 为 null 时退化为不含具体数字的通用文案", () => {
    const tooltip = buildMockDataTooltip(null);
    expect(tooltip).not.toMatch(/\d/);
    expect(tooltip).toContain("mock 数据集");
  });

  it("单个维度缺失时优雅跳过，不拼出 undefined", () => {
    const tooltip = buildMockDataTooltip({ pois: 95, restaurants: 120 });
    expect(tooltip).toContain("95 个活动地点");
    expect(tooltip).toContain("120 家餐厅");
    expect(tooltip).not.toContain("undefined");
    expect(tooltip).not.toContain("条路线");
    expect(tooltip).not.toContain("条真实评论");
  });

  it("全部维度缺失时等价于 null（不误判为有数据）", () => {
    const tooltip = buildMockDataTooltip({});
    expect(tooltip).toBe(buildMockDataTooltip(null));
  });
});
