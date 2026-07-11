/**
 * 信任带（AI 思考流）编辑剪辑层单测——见 lib/trust-belt.ts 模块 docstring。
 *
 * 覆盖：
 * - ①②③ 锚点：understanding / 首次 search 工具调用 / plan_reason 各自的出现与缺省。
 * - §三 ④⑤ 分种落词：6 个具名违规码 + 1 个兜底码 都能对上设计文档原文。
 * - ⑥ 换引擎固定句 / ⑦ 定稿固定句 + give_up 诚实改口。
 * - 剪辑规则：3 次同违规合成"发现→压→换引擎"递进；总量 ~7 拍上限。
 * - §五 四种收尾：一次过（跳过④⑤⑥）/ 有自愈（完整七拍）。
 * - buildSearchPreviewChips（2026-07-10 新增）：②拍检索收据芯片数据剪辑。
 */

import { describe, expect, it } from "vitest";

import {
  buildSearchPreviewChips,
  buildTrustBeltBeats,
  type TrustBeltInput,
  type TrustBeltToolCall,
} from "./trust-belt";
import { emptyCriticReport } from "./store/types";
import type { CriticReport } from "./store/types";

function baseInput(overrides: Partial<TrustBeltInput> = {}): TrustBeltInput {
  return {
    understanding: "",
    toolCalls: [],
    thoughts: [],
    criticReport: emptyCriticReport(),
    itineraryReady: false,
    finalStrategy: null,
    ...overrides,
  };
}

function violationRound(
  seq: number,
  fixAttempt: number,
  code: string,
): CriticReport["violationRounds"][number] {
  return {
    seq,
    arrivalIdx: seq,
    fixAttempt,
    violations: [
      { code: code as never, severity: "hard", message: "测试违规", field_path: "" },
    ],
  };
}

function fixAttempt(seq: number, attempt: number): CriticReport["fixAttempts"][number] {
  return { seq, arrivalIdx: seq, attempt, feedbackText: "" };
}

function fallbackHop(seq: number): CriticReport["fallbackHops"][number] {
  return { seq, arrivalIdx: seq, from: "llm_first", to: "ils", reason: "测试降级" };
}

describe("buildTrustBeltBeats — ①②③ 锚点", () => {
  it("understanding 为空时不生成①拍", () => {
    const beats = buildTrustBeltBeats(baseInput());
    expect(beats.some((b) => b.kind === "understanding")).toBe(false);
  });

  it("understanding 非空 → ①拍原样携带该句", () => {
    const beats = buildTrustBeltBeats(
      baseInput({ understanding: "用户想周五晚唱K，我理解成想热闹" }),
    );
    const beat = beats.find((b) => b.kind === "understanding");
    expect(beat?.text).toBe("用户想周五晚唱K，我理解成想热闹");
  });

  it("无 search_pois/search_restaurants 调用时不生成②拍", () => {
    const beats = buildTrustBeltBeats(
      baseInput({ toolCalls: [{ tool: "get_user_profile" }] }),
    );
    expect(beats.some((b) => b.kind === "search")).toBe(false);
  });

  it("命中 search 工具 → ②拍固定句，且多次调用只出现一次", () => {
    const beats = buildTrustBeltBeats(
      baseInput({
        toolCalls: [
          { tool: "search_pois" },
          { tool: "search_restaurants" },
          { tool: "search_pois" },
        ],
      }),
    );
    const searchBeats = beats.filter((b) => b.kind === "search");
    expect(searchBeats).toHaveLength(1);
    expect(searchBeats[0].text).toBe("让我先查询附近的店铺和时间");
  });

  it("thoughts 里没有 planReason → 不生成③拍", () => {
    const beats = buildTrustBeltBeats(
      baseInput({ thoughts: [{ seq: 1, planReason: null }] }),
    );
    expect(beats.some((b) => b.kind === "planning")).toBe(false);
  });

  it("thoughts 里首个携带 planReason 的条目 → ③拍原样携带", () => {
    const beats = buildTrustBeltBeats(
      baseInput({
        thoughts: [
          { seq: 1, planReason: null },
          { seq: 2, planReason: "用户同行年轻人多，所以先用 KTV 带动气氛" },
        ],
      }),
    );
    const beat = beats.find((b) => b.kind === "planning");
    expect(beat?.text).toBe("用户同行年轻人多，所以先用 KTV 带动气氛");
  });
});

describe("buildTrustBeltBeats — §三 ④⑤ 分种落词", () => {
  const cases: Array<[string, string, string]> = [
    ["duration_out_of_range", "方案超出时间限制", "让我压缩一下时间"],
    ["budget_exceeded", "花费超了预算", "让我换些实惠的"],
    ["capacity_requirement_violated", "有的店坐不下这么多人", "让我挑能容下的"],
    ["dietary_violation", "有一站不合忌口", "让我避开这个"],
    ["distance_exceeded", "有的点离得太远", "让我找近一点的"],
    ["opening_hours_violation", "有的店那个点没开门", "让我调下时段"],
    // 兜底（未列类型）
    ["some_unlisted_code", "方案有个地方不太对", "让我调整一下"],
  ];

  it.each(cases)("违规码 %s → ④=%s ⑤=%s", (code, discoverText, fixText) => {
    const criticReport: CriticReport = {
      violationRounds: [violationRound(1, 1, code)],
      fixAttempts: [fixAttempt(2, 2)],
      fallbackHops: [],
    };
    const beats = buildTrustBeltBeats(baseInput({ criticReport }));
    const discover = beats.find((b) => b.kind === "discover");
    const fix = beats.find((b) => b.kind === "fix");
    expect(discover?.text).toBe(discoverText);
    expect(fix?.text).toBe(fixText);
  });

  it("violations=[]（这稿压根没生成出方案）落兜底措辞", () => {
    const criticReport: CriticReport = {
      violationRounds: [
        { seq: 1, arrivalIdx: 1, fixAttempt: 1, violations: [] },
      ],
      fixAttempts: [],
      fallbackHops: [],
    };
    const beats = buildTrustBeltBeats(baseInput({ criticReport }));
    const discover = beats.find((b) => b.kind === "discover");
    expect(discover?.text).toBe("方案有个地方不太对");
  });
});

describe("buildTrustBeltBeats — ⑥⑦固定句 + 收尾分档", () => {
  it("plan_fallback → ⑥固定句", () => {
    const criticReport: CriticReport = {
      violationRounds: [],
      fixAttempts: [],
      fallbackHops: [fallbackHop(1)],
    };
    const beats = buildTrustBeltBeats(baseInput({ criticReport }));
    const beat = beats.find((b) => b.kind === "fallback");
    expect(beat?.text).toBe("还是不行，换成算法引擎");
    expect(beat?.amber).toBe(true);
  });

  it("itinerary 就绪且非 give_up → ⑦规划成功", () => {
    const beats = buildTrustBeltBeats(
      baseInput({ itineraryReady: true, finalStrategy: "llm_first" }),
    );
    const beat = beats[beats.length - 1];
    expect(beat.kind).toBe("done");
    expect(beat.text).toBe("规划成功");
  });

  it("§五『失败保留』：final_strategy=give_up → 诚实改口，不吹规划成功", () => {
    const beats = buildTrustBeltBeats(
      baseInput({ itineraryReady: true, finalStrategy: "give_up" }),
    );
    const beat = beats[beats.length - 1];
    expect(beat.kind).toBe("done");
    expect(beat.text).toBe("试了几版都排不下，先保留这版方案");
  });

  it("§五『一次过』：无 critic 事件时只有①②③⑦，没有④⑤⑥", () => {
    const beats = buildTrustBeltBeats(
      baseInput({
        understanding: "用户想安静待会，我理解成想独处",
        toolCalls: [{ tool: "search_pois" }],
        thoughts: [{ seq: 1, planReason: "用户一个人，所以先找安静的地方" }],
        itineraryReady: true,
        finalStrategy: "llm_first",
      }),
    );
    expect(beats.map((b) => b.kind)).toEqual([
      "understanding",
      "search",
      "planning",
      "done",
    ]);
  });
});

describe("buildTrustBeltBeats — 剪辑规则", () => {
  it("同违规码连续 ≥3 轮 → 合成为首④+末⑤（不逐轮重复）", () => {
    const criticReport: CriticReport = {
      violationRounds: [
        violationRound(1, 1, "duration_out_of_range"),
        violationRound(3, 2, "duration_out_of_range"),
        violationRound(5, 3, "duration_out_of_range"),
      ],
      fixAttempts: [fixAttempt(2, 2), fixAttempt(4, 3), fixAttempt(6, 4)],
      fallbackHops: [fallbackHop(7)],
    };
    const beats = buildTrustBeltBeats(baseInput({ criticReport }));
    const discoverBeats = beats.filter((b) => b.kind === "discover");
    const fixBeats = beats.filter((b) => b.kind === "fix");
    // 递进："发现→压→换引擎"——只保留 1 个④ + 1 个⑤，然后是⑥
    expect(discoverBeats).toHaveLength(1);
    expect(fixBeats).toHaveLength(1);
    expect(beats.some((b) => b.kind === "fallback")).toBe(true);
  });

  it("同违规码仅 1-2 轮时逐轮展示，不触发合并", () => {
    const criticReport: CriticReport = {
      violationRounds: [
        violationRound(1, 1, "distance_exceeded"),
        violationRound(3, 2, "distance_exceeded"),
      ],
      fixAttempts: [fixAttempt(2, 2)],
      fallbackHops: [],
    };
    const beats = buildTrustBeltBeats(baseInput({ criticReport }));
    expect(beats.filter((b) => b.kind === "discover")).toHaveLength(2);
  });

  it("总量 ~7 拍上限：④⑤⑥ 拍数超预算时保留最新的（冻结态停在⑤⑥⑦附近）", () => {
    // 5 轮不同违规码（不触发同码合并），每轮discover+fix，外加最后一个fallback +
    // 完整①②③⑦锚点——总候选拍数远超 7，验证裁剪后总数 <= 7 且末尾仍是⑦。
    const codes = [
      "duration_out_of_range",
      "budget_exceeded",
      "capacity_requirement_violated",
      "dietary_violation",
      "distance_exceeded",
    ];
    const violationRounds = codes.map((code, i) => violationRound(i * 2 + 1, i + 1, code));
    const fixAttempts = codes.map((_, i) => fixAttempt(i * 2 + 2, i + 2));
    const criticReport: CriticReport = {
      violationRounds,
      fixAttempts,
      fallbackHops: [fallbackHop(20)],
    };
    const beats = buildTrustBeltBeats(
      baseInput({
        understanding: "用户……",
        toolCalls: [{ tool: "search_pois" }],
        thoughts: [{ seq: 1, planReason: "用户……，所以先……" }],
        criticReport,
        itineraryReady: true,
        finalStrategy: "ils",
      }),
    );
    expect(beats.length).toBeLessThanOrEqual(7);
    expect(beats[beats.length - 1].kind).toBe("done");
  });
});

describe("buildSearchPreviewChips — ②拍检索收据芯片", () => {
  function poiCall(
    arrivalIdx: number,
    preview: Array<{ name: string; rating: number }>,
    count: number,
  ): TrustBeltToolCall {
    return {
      tool: "search_pois",
      arrivalIdx,
      output: {
        success: true,
        count,
        preview: preview.map((p) => ({ kind: "poi" as const, ...p })),
      },
    };
  }

  function restaurantCall(
    arrivalIdx: number,
    preview: Array<{ name: string; rating: number }>,
    count: number,
  ): TrustBeltToolCall {
    return {
      tool: "search_restaurants",
      arrivalIdx,
      output: {
        success: true,
        count,
        preview: preview.map((p) => ({ kind: "restaurant" as const, ...p })),
      },
    };
  }

  it("零召回（无 search 事件）→ 空芯片 + 0 溢出", () => {
    const result = buildSearchPreviewChips([]);
    expect(result.chips).toEqual([]);
    expect(result.overflowCount).toBe(0);
  });

  it("两类 count 都是 0（有 search 事件但召回为空）→ 空芯片", () => {
    const result = buildSearchPreviewChips([
      poiCall(1, [], 0),
      restaurantCall(2, [], 0),
    ]);
    expect(result.chips).toEqual([]);
    expect(result.overflowCount).toBe(0);
  });

  it("合并排序：餐厅组在前、POI 组在后，各自保持后端已排好的顺序", () => {
    const result = buildSearchPreviewChips([
      poiCall(1, [{ name: "地点甲", rating: 4.5 }], 1),
      restaurantCall(2, [{ name: "餐厅甲", rating: 4.2 }], 1),
    ]);
    expect(result.chips.map((c) => c.kind)).toEqual(["restaurant", "poi"]);
    expect(result.chips.map((c) => c.name)).toEqual(["餐厅甲", "地点甲"]);
  });

  it("每类各自最多展示 3 条（top-3 截取，不越界多取）", () => {
    const fourPois = [
      { name: "地点1", rating: 4.9 },
      { name: "地点2", rating: 4.8 },
      { name: "地点3", rating: 4.7 },
      { name: "地点4", rating: 4.6 },
    ];
    const result = buildSearchPreviewChips([poiCall(1, fourPois, 4)]);
    expect(result.chips).toHaveLength(3);
    expect(result.chips.map((c) => c.name)).toEqual(["地点1", "地点2", "地点3"]);
  });

  it("+N 徽章 = 两类总召回数 − 已展示数", () => {
    const result = buildSearchPreviewChips([
      poiCall(1, [{ name: "地点1", rating: 4.5 }], 5),
      restaurantCall(2, [{ name: "餐厅1", rating: 4.3 }], 4),
    ]);
    // 总召回 5+4=9，展示 1+1=2 → 溢出 7
    expect(result.overflowCount).toBe(7);
  });

  it("总展示数 ≥ 总召回数时不产生负溢出（clamp 到 0）", () => {
    const result = buildSearchPreviewChips([
      poiCall(1, [{ name: "地点1", rating: 4.5 }], 1),
      restaurantCall(2, [{ name: "餐厅1", rating: 4.3 }], 1),
    ]);
    expect(result.overflowCount).toBe(0);
  });

  it("同轮内同 tool 多次调用（反馈重规划再次检索）→ 取最后一组（arrivalIdx 最大）", () => {
    const result = buildSearchPreviewChips([
      poiCall(1, [{ name: "旧地点", rating: 4.9 }], 1),
      poiCall(5, [{ name: "新地点", rating: 4.1 }], 1),
    ]);
    expect(result.chips.map((c) => c.name)).toEqual(["新地点"]);
  });

  it("只有一类召回（如 POI 为空、只有餐厅）→ 只展示存在的那类", () => {
    const result = buildSearchPreviewChips([
      restaurantCall(1, [{ name: "餐厅甲", rating: 4.0 }], 1),
      poiCall(2, [], 0),
    ]);
    expect(result.chips.map((c) => c.kind)).toEqual(["restaurant"]);
  });

  it("非 search 工具调用（如 get_user_profile）不参与芯片提取", () => {
    const result = buildSearchPreviewChips([
      { tool: "get_user_profile", arrivalIdx: 1, output: { success: true, found: true } },
    ]);
    expect(result.chips).toEqual([]);
    expect(result.overflowCount).toBe(0);
  });

  it("ILS/rule 兜底重查（同名 tool 但 output 无 preview）不覆盖 fan-out 收据（⑥换引擎时芯片不塌）", () => {
    // ILS 的 tool_call_end.output 是完整 SearchPoisOutput（candidates，无 preview
    // 无 count）——即便 arrivalIdx 更大，也不该让已展示的检索收据蒸发。
    const result = buildSearchPreviewChips([
      poiCall(1, [{ name: "地点甲", rating: 4.5 }], 6),
      {
        tool: "search_pois",
        arrivalIdx: 9,
        output: { success: true, candidates: [{ id: "P001" }] },
      },
    ]);
    expect(result.chips.map((c) => c.name)).toEqual(["地点甲"]);
    expect(result.overflowCount).toBe(5);
  });

  it("展示名剥尾部分店括号后缀（全角/半角都剥，只剥尾部不动名中括号）", () => {
    const result = buildSearchPreviewChips([
      restaurantCall(
        1,
        [
          { name: "绿茶餐厅(凯德MALL店)", rating: 4.0 },
          { name: "真功夫（合生麒麟新天地）", rating: 3.9 },
          { name: "老王(串都)烧烤", rating: 4.7 },
        ],
        3,
      ),
    ]);
    expect(result.chips.map((c) => c.name)).toEqual([
      "绿茶餐厅",
      "真功夫",
      "老王(串都)烧烤", // 名中括号不是分店后缀，不剥
    ]);
  });

  it("整名都是括号段 → 剥空回退原名（不显示空芯片）", () => {
    const result = buildSearchPreviewChips([
      poiCall(1, [{ name: "（快闪展）", rating: 4.2 }], 1),
    ]);
    expect(result.chips.map((c) => c.name)).toEqual(["（快闪展）"]);
  });

  it("output.preview 非数组（异常线上数据）→ 不消费、不炸", () => {
    const result = buildSearchPreviewChips([
      {
        tool: "search_restaurants",
        arrivalIdx: 1,
        output: { success: true, count: 3, preview: "garbage" },
      },
    ]);
    expect(result.chips).toEqual([]);
    expect(result.overflowCount).toBe(0);
  });
});
