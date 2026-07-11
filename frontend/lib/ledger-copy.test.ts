/**
 * ledger-copy.ts 单测——诉求台账人话化（用户偏好面板全环方案 §3/§9 系列）。
 *
 * 覆盖点：
 * 1. 方向词维度（price/distance）命中词表 → 人话短语（不漏出英文原词）。
 * 2. 词典外方向词组合 → 兜底原样打印 + console.warn（不崩、不瞎猜）。
 * 3. 目标值维度（dietary/ambience/crowd_fit/cuisine_or_type）→ 直接展示原值。
 * 4. id→店名映射：itinerary 里能找到就显示 title，找不到兜底原始 target_id。
 * 5. ledgerEntryLine 拼装完整语句（含/不含 nickname、含/不含 node_ref）。
 */

import { describe, expect, it, vi } from "vitest";

import { ledgerEntryLine, ledgerValuePhrase, nodeNameByTargetId } from "./ledger-copy";
import type { DemandLedgerEntry, Itinerary } from "./types";

function _entry(overrides: Partial<DemandLedgerEntry> = {}): DemandLedgerEntry {
  return {
    member_id: null,
    nickname: null,
    node_ref: null,
    dimension: "price",
    value: "cheaper",
    status: "active",
    source_text: "便宜点",
    created_at: 1,
    ...overrides,
  };
}

describe("ledgerValuePhrase", () => {
  it("price/cheaper 命中词表", () => {
    expect(ledgerValuePhrase(_entry({ dimension: "price", value: "cheaper" }))).toBe("更便宜");
  });

  it("price/pricier 命中词表", () => {
    expect(ledgerValuePhrase(_entry({ dimension: "price", value: "pricier" }))).toBe("更高档");
  });

  it("distance/closer 命中词表", () => {
    expect(ledgerValuePhrase(_entry({ dimension: "distance", value: "closer" }))).toBe("更近");
  });

  it("distance/farther 命中词表", () => {
    expect(ledgerValuePhrase(_entry({ dimension: "distance", value: "farther" }))).toBe("更远");
  });

  it("目标值维度（dietary）直接展示原值，不查方向词表", () => {
    expect(ledgerValuePhrase(_entry({ dimension: "dietary", value: "不辣" }))).toContain("不辣");
  });

  it("词典外方向词组合兜底原样打印 + console.warn", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const phrase = ledgerValuePhrase(_entry({ dimension: "price", value: "unknown_direction" }));
    expect(phrase).toContain("unknown_direction");
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });
});

describe("nodeNameByTargetId", () => {
  const itinerary = {
    nodes: [
      { node_id: "n1", kind: "主活动", target_kind: "poi", target_id: "P1", start_time: "10:00", duration_min: 60, title: "望京公园" },
    ],
  } as unknown as Itinerary;

  it("找得到节点时返回 title", () => {
    expect(nodeNameByTargetId(itinerary, "P1")).toBe("望京公园");
  });

  it("找不到时兜底原始 target_id（节点已被换掉/itinerary 未加载）", () => {
    expect(nodeNameByTargetId(itinerary, "P999")).toBe("P999");
    expect(nodeNameByTargetId(null, "P999")).toBe("P999");
  });
});

describe("ledgerEntryLine", () => {
  const itinerary = {
    nodes: [
      { node_id: "n1", kind: "用餐", target_kind: "restaurant", target_id: "R1", start_time: "12:00", duration_min: 90, title: "小湘阁" },
    ],
  } as unknown as Itinerary;

  it("全局诉求（无 node_ref）显示「全局」", () => {
    const line = ledgerEntryLine(_entry({ node_ref: null }), itinerary);
    expect(line).toContain("全局");
  });

  it("有 node_ref 时显示店名而非原始 id", () => {
    const line = ledgerEntryLine(
      _entry({ node_ref: { kind: "restaurant", target_id: "R1" } }),
      itinerary,
    );
    expect(line).toContain("小湘阁");
    expect(line).not.toContain("R1");
  });

  it("房间模式 nickname 非空时点名前缀；单人模式恒为 null 不显示这段", () => {
    const named = ledgerEntryLine(_entry({ nickname: "小明" }), itinerary);
    expect(named).toContain("小明");
    const anon = ledgerEntryLine(_entry({ nickname: null }), itinerary);
    expect(anon).not.toContain("小明");
  });
});
