import { describe, expect, it } from "vitest";

import { diffStages, nodesToDiffStages, type DiffStage } from "@/components/ComparisonView";
import type { ActivityNode, Itinerary } from "./types";

/**
 * ComparisonView diffStages 身份对齐算法回归（forge round1/round2/round3.md
 * DP1/T2/T3 收敛结论）。
 *
 * 根因：旧实现按数组下标逐位比较（zip 对齐），插入/删除会让下标错位——
 * "良子健身"被插入的"管氏翅吧"往后挤了一位，位置比较器认不出它是同一个
 * 实体，误判成"整体替换"。新实现按 ActivityNode.target_id 身份配对，
 * 覆盖 forge 材料枚举的全部边界：插入/删除/替换/时移/真互换/同名不同店。
 *
 * 不设独立 moved 类（round3.md T2 拍板结论）：同身份且四字段全同，即便
 * 相对顺序变了，仍归为 unchanged——因为本业务时间轴下"纯移动不改时间"
 * 几乎不发生，真移动几乎总伴随 start/end 变化，天然落入 modified。
 */

function node(overrides: Partial<ActivityNode> & Pick<ActivityNode, "target_id">): ActivityNode {
  return {
    node_id: `n_${overrides.target_id}`,
    kind: "餐饮",
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

function diff(oldNodes: ActivityNode[], newNodes: ActivityNode[]) {
  return diffStages(
    nodesToDiffStages(itineraryWith(oldNodes)),
    nodesToDiffStages(itineraryWith(newNodes)),
  );
}

function kindsByTargetId(diffs: ReturnType<typeof diff>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const d of diffs) {
    const id = (d.newStage ?? d.oldStage) as DiffStage;
    out[id.targetId] = d.kind;
  }
  return out;
}

describe("diffStages — 身份对齐（target_id），非数组下标对齐", () => {
  it("插入一个节点：本例用户场景——旧[良子健身]、新[管氏翅吧, 良子健身]", () => {
    // 这是用户拍定的验收场景：插入一顿饭（管氏翅吧）不应把良子健身误判成
    // "被换成了管氏翅吧"——良子健身内容没变，只是被插入的节点挤到了后面。
    const liangzi = node({ target_id: "L_良子健身", title: "良子健身" });
    const guanshi = node({ target_id: "G_管氏翅吧", title: "管氏翅吧" });

    const result = diff([liangzi], [guanshi, liangzi]);
    const kinds = kindsByTargetId(result);

    expect(kinds["G_管氏翅吧"]).toBe("added");
    expect(kinds["L_良子健身"]).toBe("unchanged");
    // 不应出现任何 removed——旧方案里唯一的实体（良子健身）在新方案里还在。
    expect(result.some((d) => d.kind === "removed")).toBe(false);
  });

  it("删除一个节点：旧[A,B]、新[A] → B 判 removed，A 不受影响", () => {
    const a = node({ target_id: "A" });
    const b = node({ target_id: "B" });

    const result = diff([a, b], [a]);
    const kinds = kindsByTargetId(result);

    expect(kinds["A"]).toBe("unchanged");
    expect(kinds["B"]).toBe("removed");
  });

  it("整表替换（换场景，target_id 全变）：全部 removed + 全部 added", () => {
    const oldA = node({ target_id: "OLD_A" });
    const oldB = node({ target_id: "OLD_B" });
    const newA = node({ target_id: "NEW_A" });
    const newB = node({ target_id: "NEW_B" });

    const result = diff([oldA, oldB], [newA, newB]);

    expect(result.filter((d) => d.kind === "removed")).toHaveLength(2);
    expect(result.filter((d) => d.kind === "added")).toHaveLength(2);
    expect(result.some((d) => d.kind === "unchanged" || d.kind === "modified")).toBe(false);
  });

  it("只改时间（同 target_id）：判 modified，changedFields 含 time", () => {
    const before = node({ target_id: "R001", start_time: "12:00", duration_min: 60 });
    const after = node({ target_id: "R001", start_time: "14:00", duration_min: 60 });

    const result = diff([before], [after]);

    expect(result).toHaveLength(1);
    expect(result[0].kind).toBe("modified");
    expect(result[0].changedFields).toContain("time");
    expect(result[0].changedFields).not.toContain("title");
  });

  it("新增+删除同时发生（旧[A,B]→新[A,C]，B删C增）：A 不受干扰判 unchanged", () => {
    const a = node({ target_id: "A" });
    const b = node({ target_id: "B" });
    const c = node({ target_id: "C" });

    const result = diff([a, b], [a, c]);
    const kinds = kindsByTargetId(result);

    expect(kinds["A"]).toBe("unchanged");
    expect(kinds["B"]).toBe("removed");
    expect(kinds["C"]).toBe("added");
  });

  it("真互换（旧[A,B]→新[B,A]，且时间跟着互换）：两者均因 time 变化判 modified", () => {
    // 本业务时间轴下，两个节点真的交换先后时，start/end 几乎总跟着变
    // （否则两个节点同时段重叠没有意义）——round3.md T2 结论：这类真移动
    // 天然落入 modified 的 time 高亮，不需要独立的 moved 视觉类。
    const a1 = node({ target_id: "A", start_time: "12:00" });
    const b1 = node({ target_id: "B", start_time: "14:00" });
    const a2 = node({ target_id: "A", start_time: "14:00" });
    const b2 = node({ target_id: "B", start_time: "12:00" });

    const result = diff([a1, b1], [b2, a2]);
    const kinds = kindsByTargetId(result);

    expect(kinds["A"]).toBe("modified");
    expect(kinds["B"]).toBe("modified");
    expect(result.every((d) => d.changedFields.includes("time"))).toBe(true);
  });

  it("同名不同店（target_id 不同，title 相同）：判 removed 一个 + added 一个，不误判 unchanged", () => {
    const branch1 = node({ target_id: "STORE_001", title: "同名连锁店" });
    const branch2 = node({ target_id: "STORE_002", title: "同名连锁店" });

    const result = diff([branch1], [branch2]);
    const kinds = kindsByTargetId(result);

    // 即使 title 字符串相同，target_id 不同就是不同实体——正确判断是换了
    // 一家店（removed+added），不能因为标题相同就当作 unchanged。
    expect(kinds["STORE_001"]).toBe("removed");
    expect(kinds["STORE_002"]).toBe("added");
  });

  it("换菜后 target_id 变化（graph_adjust.py 坐实的换菜语义）：判 removed+added，而非 modified", () => {
    // 换菜前后 target_id 必变（graph_adjust.py:422 注释）——身份配对下无法
    // 区分"换菜替换"与"删A插B"，两者 target_id 变化模式相同，这是已知的
    // 前端侧固有局限（round2.md T3③），不是本次要修的范围。这里锁死的是
    // "至少不会因为位置凑巧对齐就误判成 modified"这一底线正确性。
    const before = node({ target_id: "R001", title: "老店" });
    const after = node({ target_id: "R099", title: "新换的店" });

    const result = diff([before], [after]);
    const kinds = kindsByTargetId(result);

    expect(kinds["R001"]).toBe("removed");
    expect(kinds["R099"]).toBe("added");
    expect(result.some((d) => d.kind === "modified")).toBe(false);
  });
});
