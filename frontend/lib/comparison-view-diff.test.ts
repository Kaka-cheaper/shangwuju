import { describe, expect, it } from "vitest";

import {
  diffStages,
  nodesToDiffStages,
  shouldShowComparison,
  type DiffEntry,
} from "@/components/ComparisonView";
import type { ActivityNode, Itinerary } from "./types";

/**
 * ComparisonView 三趟对齐算法回归。
 *
 * 问题命名：结构化短序列 diff，需要 UPDATE 操作（换店=原地改，而非删+加）。
 * 纯 target_id 精确配对是文本级 diff（只有增/删/保留），把换店拆成"删旧店+
 * 加新店"——用户报告的 #100"换两个店画成 4 件事 + 4 个幽灵框"的根。
 *
 * 三趟对齐（GumTree/ChangeDistilling 两阶段匹配的裁剪版）：
 *   Pass 1 精确锚点（target_id 相同）→ unchanged/modified；
 *   Pass 2 角色补配（剩余按 kind 分组、同 kind 按时间贪心配对）→ modified +
 *          entitySwapped（换店认成一次修改）；
 *   Pass 3 剩余 → added / removed。
 *
 * 注意：本文件里多条"换店"用例的期望，相对旧实现是**翻转**的——旧实现判
 * removed+added，新实现判 modified(entitySwapped)，这正是本次要修的行为。
 */

function node(
  overrides: Partial<ActivityNode> & Pick<ActivityNode, "target_id">,
): ActivityNode {
  return {
    node_id: `n_${overrides.target_id}`,
    kind: "用餐",
    target_kind: "restaurant",
    start_time: "12:00",
    duration_min: 60,
    title: overrides.target_id,
    ...overrides,
  };
}

function itineraryWith(nodes: ActivityNode[]): Itinerary {
  return {
    schema_version: "edge_v1",
    summary: "test",
    nodes,
    hops: [],
    schedule: [],
    orders: [],
    total_minutes: 0,
  } as unknown as Itinerary;
}

function diff(oldNodes: ActivityNode[], newNodes: ActivityNode[]): DiffEntry[] {
  return diffStages(
    nodesToDiffStages(itineraryWith(oldNodes)),
    nodesToDiffStages(itineraryWith(newNodes)),
  );
}

function byId(diffs: DiffEntry[]): Record<string, DiffEntry> {
  const out: Record<string, DiffEntry> = {};
  for (const d of diffs) {
    const id = (d.newStage ?? d.oldStage)!.targetId;
    out[id] = d;
  }
  return out;
}

describe("Pass 1 — 精确锚点（target_id 身份配对，与位置无关）", () => {
  it("插入一个节点（不同 kind）：旧[良子健身]、新[管氏翅吧, 良子健身]", () => {
    const liangzi = node({ target_id: "L", title: "良子健身", kind: "主活动" });
    const guanshi = node({ target_id: "G", title: "管氏翅吧", kind: "用餐" });

    const d = byId(diff([liangzi], [guanshi, liangzi]));

    expect(d["L"].kind).toBe("unchanged");
    expect(d["G"].kind).toBe("added");
  });

  it("删除一个节点：旧[A,B]、新[A] → B removed，A 不受影响", () => {
    const d = byId(diff([node({ target_id: "A" }), node({ target_id: "B" })], [node({ target_id: "A" })]));
    expect(d["A"].kind).toBe("unchanged");
    expect(d["B"].kind).toBe("removed");
  });

  it("只改时间（同 target_id）：modified、changedFields 含 time、非换店", () => {
    const before = node({ target_id: "R001", start_time: "12:00", duration_min: 60 });
    const after = node({ target_id: "R001", start_time: "14:00", duration_min: 60 });

    const result = diff([before], [after]);
    expect(result).toHaveLength(1);
    expect(result[0].kind).toBe("modified");
    expect(result[0].entitySwapped).toBe(false);
    expect(result[0].changedFields).toContain("time");
  });

  it("真互换（旧[A,B]→新[B,A] 且时间互换）：两者均因 time 变判 modified，非换店", () => {
    const a1 = node({ target_id: "A", start_time: "12:00" });
    const b1 = node({ target_id: "B", start_time: "14:00" });
    const a2 = node({ target_id: "A", start_time: "14:00" });
    const b2 = node({ target_id: "B", start_time: "12:00" });

    const d = byId(diff([a1, b1], [b2, a2]));
    expect(d["A"].kind).toBe("modified");
    expect(d["B"].kind).toBe("modified");
    expect(d["A"].entitySwapped).toBe(false);
    expect(d["B"].entitySwapped).toBe(false);
  });
});

describe("Pass 2 — 角色补配：换店认成一次修改（本次修复核心）", () => {
  it("换一家店（同 kind，target_id 变）：modified + entitySwapped，而非 removed+added", () => {
    // 旧实现在此判 removed(管氏翅吧)+added(肉串汪)——正是用户看到的车祸。
    const before = node({ target_id: "R001", title: "管氏翅吧", kind: "用餐" });
    const after = node({ target_id: "R099", title: "肉串汪", kind: "用餐" });

    const result = diff([before], [after]);
    expect(result).toHaveLength(1);
    expect(result[0].kind).toBe("modified");
    expect(result[0].entitySwapped).toBe(true);
    expect(result[0].oldStage?.title).toBe("管氏翅吧");
    expect(result[0].newStage?.title).toBe("肉串汪");
    expect(result.some((r) => r.kind === "removed" || r.kind === "added")).toBe(false);
  });

  it("#100 换两个店：旧[良子健身(主活动), 管氏翅吧(用餐)]→新[纯K(主活动), 肉串汪(用餐)] → 2 modified 换店，0 增删", () => {
    const oldNodes = [
      node({ target_id: "L", title: "良子健身", kind: "主活动", start_time: "15:44" }),
      node({ target_id: "G", title: "管氏翅吧", kind: "用餐", start_time: "17:30" }),
    ];
    const newNodes = [
      node({ target_id: "C", title: "纯K", kind: "主活动", start_time: "14:37" }),
      node({ target_id: "R", title: "肉串汪", kind: "用餐", start_time: "17:30" }),
    ];

    const result = diff(oldNodes, newNodes);
    expect(result.filter((r) => r.kind === "modified" && r.entitySwapped)).toHaveLength(2);
    expect(result.filter((r) => r.kind === "added" || r.kind === "removed")).toHaveLength(0);

    // 主活动 与 用餐 各自同 kind 配对，不会跨类错配。
    const d = byId(result);
    expect(d["C"].oldStage?.title).toBe("良子健身");
    expect(d["R"].oldStage?.title).toBe("管氏翅吧");
  });

  it("常见·只换餐店：旧[咖啡,健身,管氏翅吧]→新[咖啡,健身,肉串汪] → 2 unchanged + 1 换店", () => {
    const coffee = node({ target_id: "CF", title: "咖啡", kind: "咖啡", start_time: "09:30" });
    const gym = node({ target_id: "GY", title: "健身", kind: "主活动", start_time: "11:30" });
    const oldMeal = node({ target_id: "M1", title: "管氏翅吧", kind: "用餐", start_time: "17:30" });
    const newMeal = node({ target_id: "M2", title: "肉串汪", kind: "用餐", start_time: "17:30" });

    const result = diff([coffee, gym, oldMeal], [coffee, gym, newMeal]);
    expect(result.filter((r) => r.kind === "unchanged")).toHaveLength(2);
    const swaps = result.filter((r) => r.kind === "modified" && r.entitySwapped);
    expect(swaps).toHaveLength(1);
    expect(swaps[0].oldStage?.title).toBe("管氏翅吧");
    expect(swaps[0].newStage?.title).toBe("肉串汪");
  });

  it("跨 kind 不配对（已知边界）：旧[健身(主活动)]→新[肉串汪(用餐)] → removed + added，不当换店", () => {
    const before = node({ target_id: "GY", title: "健身", kind: "主活动" });
    const after = node({ target_id: "R", title: "肉串汪", kind: "用餐" });

    const d = byId(diff([before], [after]));
    expect(d["GY"].kind).toBe("removed");
    expect(d["R"].kind).toBe("added");
    expect(d["R"].entitySwapped).toBe(false);
  });
});

describe("排序 — 从早到晚，不是新增排前删除甩后", () => {
  it("新增站按时间插到正确位置", () => {
    const dinner = node({ target_id: "D", title: "晚餐", kind: "用餐", start_time: "18:00" });
    const noon = node({ target_id: "N", title: "午间活动", kind: "主活动", start_time: "10:00" });

    // 旧[晚餐]、新[午间活动, 晚餐]：午间活动 added(10:00)、晚餐 unchanged(18:00)。
    const result = diff([dinner], [noon, dinner]);
    expect(result[0].newStage?.start).toBe("10:00"); // 早的排前
    expect(result[1].newStage?.start).toBe("18:00");
  });
});

describe("shouldShowComparison — 有结构延续才挂卡", () => {
  it("换店（有延续）→ 挂", () => {
    const before = itineraryWith([node({ target_id: "R001", title: "管氏翅吧", kind: "用餐" })]);
    const after = itineraryWith([node({ target_id: "R099", title: "肉串汪", kind: "用餐" })]);
    expect(shouldShowComparison(before, after)).toBe(true);
  });

  it("#99 零重合（两活动删到只剩一餐）→ 不挂（continuity=0）", () => {
    const before = itineraryWith([
      node({ target_id: "P1", title: "私人健身管家", kind: "主活动", start_time: "14:04" }),
      node({ target_id: "P2", title: "良子健身", kind: "主活动", start_time: "15:40" }),
    ]);
    const after = itineraryWith([
      node({ target_id: "R", title: "肉串汪", kind: "用餐", start_time: "17:30" }),
    ]);

    // 数据层面：2 removed + 1 added（跨 kind 不配对）。
    const result = diffStages(nodesToDiffStages(before), nodesToDiffStages(after));
    expect(result.filter((r) => r.kind === "removed")).toHaveLength(2);
    expect(result.filter((r) => r.kind === "added")).toHaveLength(1);
    // 判定层面：无结构延续 → 不挂对比卡（由新方案卡兜底）。
    expect(shouldShowComparison(before, after)).toBe(false);
  });

  it("完全没变 → 不挂", () => {
    const same = itineraryWith([node({ target_id: "A" })]);
    expect(shouldShowComparison(same, itineraryWith([node({ target_id: "A" })]))).toBe(false);
  });
});
