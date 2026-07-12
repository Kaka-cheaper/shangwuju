/**
 * collab-feed.ts 单测——约束流合并 A1 的合并展示流。
 *
 * 覆盖点：
 * 1. 约束流条目（text/vote_dislike）状态恒为 null，展示层不出徽标位。
 * 2. 台账条目状态映射 active/satisfied，superseded 被过滤。
 * 3. 指名换店留痕（alternative_swap）单独映射成"已满足"，与真实约束区分。
 * 4. 台账行 attribution 留空（归名已焊进 ledgerEntryLine 的 text），约束流行
 *    attribution 独立可用——不重复展示同一个昵称。
 * 5. 合并后按 timestamp 升序排列，两种来源交织，互不覆盖对方字段。
 */

import { describe, expect, it } from "vitest";

import type { CollabConstraint } from "./collab-store";
import { mergeCollabFeed } from "./collab-feed";
import type { DemandLedgerEntry, Itinerary } from "./types";

function _constraint(overrides: Partial<CollabConstraint> = {}): CollabConstraint {
  return {
    user_id: "u1",
    nickname: "小明",
    text: "不要辣的",
    source: "text",
    timestamp: 100,
    ...overrides,
  };
}

function _ledgerEntry(overrides: Partial<DemandLedgerEntry> = {}): DemandLedgerEntry {
  return {
    member_id: "u2",
    nickname: "小北",
    node_ref: null,
    dimension: "price",
    value: "cheaper",
    status: "active",
    source_text: "便宜点",
    created_at: 200,
    ...overrides,
  };
}

describe("mergeCollabFeed", () => {
  it("约束流条目（text）status 恒为 null", () => {
    const rows = mergeCollabFeed([_constraint({ source: "text" })], [], null);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe("constraint");
    expect(rows[0].status).toBeNull();
  });

  it("点踩来源约束流条目文案带 👎 前缀，status 仍为 null", () => {
    const rows = mergeCollabFeed(
      [_constraint({ source: "vote_dislike", text: "太远了" })],
      [],
      null,
    );
    expect(rows[0].text).toBe("👎 太远了");
    expect(rows[0].status).toBeNull();
  });

  it("台账条目：active 映射为 active，superseded 被过滤掉不出现在结果里", () => {
    const rows = mergeCollabFeed(
      [],
      [
        _ledgerEntry({ status: "active", created_at: 1 }),
        _ledgerEntry({ status: "superseded", created_at: 2 }),
        _ledgerEntry({ status: "satisfied", created_at: 3 }),
      ],
      null,
    );
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.status)).toEqual(["active", "satisfied"]);
  });

  it("指名换店留痕（alternative_swap）单独映射成已满足，与真实约束区分", () => {
    const rows = mergeCollabFeed(
      [
        _constraint({ source: "text", text: "不要辣的" }),
        _constraint({ source: "alternative_swap", text: "换成了「泡芙工坊」", user_id: "u3", nickname: "阿强" }),
      ],
      [],
      null,
    );
    const swapRow = rows.find((r) => r.text.includes("泡芙工坊"));
    const textRow = rows.find((r) => r.text === "不要辣的");
    expect(swapRow?.status).toBe("satisfied");
    expect(swapRow?.attribution).toBe("阿强");
    expect(textRow?.status).toBeNull();
  });

  it("台账行 attribution 留空——归名已经焊进 ledgerEntryLine 的 text，不重复展示昵称", () => {
    const rows = mergeCollabFeed([], [_ledgerEntry({ nickname: "小北" })], null);
    expect(rows[0].attribution).toBe("");
    expect(rows[0].text).toContain("小北");
    // 只出现一次，不是"小北 · 小北 · ..."这种重复。
    expect(rows[0].text.split("小北")).toHaveLength(2);
  });

  it("约束流行 attribution 独立可用，昵称缺失时兜底 user_id", () => {
    const rows = mergeCollabFeed(
      [_constraint({ nickname: undefined, user_id: "u_fallback" })],
      [],
      null,
    );
    expect(rows[0].attribution).toBe("u_fallback");
  });

  it("合并后按 timestamp 升序交织两种来源，互不覆盖对方字段", () => {
    const rows = mergeCollabFeed(
      [_constraint({ text: "第一句", timestamp: 50 }), _constraint({ text: "第三句", timestamp: 300 })],
      [_ledgerEntry({ created_at: 150, dimension: "dietary", value: "不辣" })],
      null,
    );
    expect(rows.map((r) => r.kind)).toEqual(["constraint", "ledger", "constraint"]);
    expect(rows[0].text).toBe("第一句");
    expect(rows[2].text).toBe("第三句");
    expect(rows[1].kind).toBe("ledger");
  });

  it("ledger 为 null/undefined 时等价于空数组，不抛错", () => {
    expect(mergeCollabFeed([_constraint()], null, null)).toHaveLength(1);
    expect(mergeCollabFeed([_constraint()], undefined, null)).toHaveLength(1);
  });

  it("台账行的店名反查复用既有 itinerary 兜底路径（node_ref.title 缺失时）", () => {
    const itinerary = {
      nodes: [
        { node_id: "n1", kind: "用餐", target_kind: "restaurant", target_id: "R1", start_time: "12:00", duration_min: 90, title: "小湘阁" },
      ],
    } as unknown as Itinerary;
    const rows = mergeCollabFeed(
      [],
      [_ledgerEntry({ node_ref: { kind: "restaurant", target_id: "R1", title: null } })],
      itinerary,
    );
    expect(rows[0].text).toContain("小湘阁");
  });
});
