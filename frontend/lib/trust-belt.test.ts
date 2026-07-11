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
 * - buildProfileFieldsReceipt（2026-07-11 新增）：①拍画像收据数据剪辑。
 */

import { describe, expect, it } from "vitest";

import {
  buildChecksRunReceipt,
  buildProfileFieldsReceipt,
  buildRelaxedSearchNotice,
  buildSearchPreviewChips,
  buildSpineNodes,
  buildTrustBeltBeats,
  type TrustBeltBeat,
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
    // 四条不变式批 I3·C5b：显式要吃饭但这版没排上（explicit_dining_missing HARD）
    ["explicit_dining_missing", "你说了要吃饭，这版还没排上", "让我补一顿进去"],
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

describe("buildTrustBeltBeats — 止损三修（真机症状回归，2026-07-11）", () => {
  // 真机症状：琥珀行渲染成「还是不行换引擎→让我压缩时间→方案超出限制→
  // 还是不行换引擎」——因果倒序 + 连续⑥一字不差重复。三处根因各自单测覆盖。

  it("止损修 1：预算裁剪不再从中间切断④⑤配对——按轮整体保留/丢弃", () => {
    // 构造：3 轮不同违规码（各自 discover+fix，不触发同码合并）+ 1 个 fallback，
    // 外加完整①②③锚点。总候选拍数 = 3(锚点) + 3*2(轮) + 1(fallback) + 1(⑦) = 11，
    // 预算 = 7 - 3(锚点) - 1(⑦) = 3 拍——只够放 1 轮多一点。旧实现对拍摊平后
    // slice(-3) 会切出 [fix(轮3), fallback] 只有 2 个真实拍再垫不满，或在不同
    // 轮数下切出"孤儿 fix"；新实现必须保证：留下的每一个 fix 拍，它配对的
    // discover 拍也必须留下（不允许孤儿 fix / 孤儿产生的因果倒序）。
    const codes = ["duration_out_of_range", "budget_exceeded", "distance_exceeded"];
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
      }),
    );
    // 断言：每个留下的 fix 拍，紧邻它前面必须是同一轮的 discover 拍（seq 相邻，
    // fix.seq = discover.seq + 1，本 fixture 的构造方式），不存在孤儿 fix。
    const kinds = beats.map((b) => b.kind);
    for (let i = 0; i < beats.length; i += 1) {
      if (kinds[i] === "fix") {
        expect(kinds[i - 1]).toBe("discover");
      }
    }
  });

  it("止损修 1：裁剪后不出现『fallback 排在某轮 discover 之前，而该轮 fix 却排在 fallback 之后』的倒序", () => {
    // 更直接地复现真机症状本身：构造 2 轮违规 + 1 个 fallback，预算恰好只够
    // 2 个 HealUnit（该 fixture 下 = 1 轮 discover+fix 或 discover+fallback），
    // 验证裁剪后序列里 discover 一定在其配对 fix 之前，fallback 不会插在
    // 一对④⑤中间。
    const criticReport: CriticReport = {
      violationRounds: [
        violationRound(1, 1, "duration_out_of_range"),
        violationRound(3, 2, "budget_exceeded"),
      ],
      fixAttempts: [fixAttempt(2, 2), fixAttempt(4, 3)],
      fallbackHops: [fallbackHop(5)],
    };
    const beats = buildTrustBeltBeats(
      baseInput({
        understanding: "u",
        toolCalls: [{ tool: "search_pois" }],
        thoughts: [{ seq: 1, planReason: "p" }],
        criticReport,
      }),
    );
    const kinds = beats.map((b) => b.kind);
    // discover 必须先于同轮的 fix（用 seq 校验配对而非位置假设）
    const discoverSeqs = beats.filter((b) => b.kind === "discover").map((b) => b.seq);
    const fixSeqs = beats.filter((b) => b.kind === "fix").map((b) => b.seq);
    // 若某个 fix 存在，比它 seq 小 1 的 discover 也必须存在于 beats 里
    for (const fixSeq of fixSeqs) {
      expect(discoverSeqs).toContain(fixSeq - 1);
    }
    void kinds;
  });

  it("止损修 2：连续 2 次换引擎不再一字不差重复——第二次换成措辞变体", () => {
    const criticReport: CriticReport = {
      violationRounds: [],
      fixAttempts: [],
      fallbackHops: [fallbackHop(1), fallbackHop(3)],
    };
    const beats = buildTrustBeltBeats(baseInput({ criticReport }));
    const fallbackTexts = beats.filter((b) => b.kind === "fallback").map((b) => b.text);
    expect(fallbackTexts).toHaveLength(2);
    expect(fallbackTexts[0]).not.toBe(fallbackTexts[1]);
    // 两句都不能是空、且都保持第一人称思考腔（不做字面校验，只验证非空）
    expect(fallbackTexts[0].length).toBeGreaterThan(0);
    expect(fallbackTexts[1].length).toBeGreaterThan(0);
  });

  it("止损修 2：3 次连续换引擎——变体池用完后停在最后一种（不循环回第一句、不报错）", () => {
    const criticReport: CriticReport = {
      violationRounds: [],
      fixAttempts: [],
      fallbackHops: [fallbackHop(1), fallbackHop(3), fallbackHop(5), fallbackHop(7)],
    };
    const beats = buildTrustBeltBeats(baseInput({ criticReport }));
    const fallbackTexts = beats
      .filter((b) => b.kind === "fallback")
      .map((b) => b.text);
    // 4 次 fallback 但只有 3 种变体——第 3、4 次应相同（停在最后一种）
    expect(fallbackTexts.length).toBeGreaterThan(0);
    const lastTwo = fallbackTexts.slice(-2);
    if (fallbackTexts.length >= 4) {
      expect(lastTwo[0]).toBe(lastTwo[1]);
    }
  });

  it("止损修 3：fallback 之后到达的 fix_attempt 不再误挂回切换前的旧违规轮", () => {
    // 复现根因：违规轮①（duration_out_of_range）先发现，紧接着换引擎（fallback），
    // 新引擎自己又报了一次同码违规，随后才姗姗来迟一条 fix_attempt——这条
    // fix_attempt 在真实事件里对应的是**新引擎那一轮**，不该被吞回旧引擎那轮
    // （旧实现里 current 跨 fallback 不重置，会把它错挂给第一条 discover）。
    const criticReport: CriticReport = {
      violationRounds: [
        violationRound(1, 1, "duration_out_of_range"), // 旧引擎发现，未等到 fix 就换引擎
        violationRound(5, 2, "duration_out_of_range"), // 新引擎重新报同一类问题
      ],
      fixAttempts: [fixAttempt(6, 3)], // 只有新引擎这一轮等到了 fix_attempt
      fallbackHops: [fallbackHop(3)], // 换引擎发生在旧发现之后、新发现之前
    };
    const beats = buildTrustBeltBeats(baseInput({ criticReport }));
    // 断言核心不变量：任意一个 fix 拍，紧邻它前面的必须是 discover 拍——
    // 不允许 fix 排在 fallback 之后却和一个更早（fallback 之前）的 discover
    // 组成非因果配对（即 fix.seq 必须与紧邻 discover.seq 相邻，不能隔着 fallback）。
    const idx = beats.findIndex((b) => b.kind === "fix");
    expect(idx).toBeGreaterThan(0);
    expect(beats[idx - 1].kind).toBe("discover");
    // 且这个 discover 必须是新引擎那一轮（seq=5），不是旧引擎那一轮（seq=1）——
    // 若错挂回旧轮，beats[idx-1] 会是 seq=1 的 discover，而 fix.seq=6 与它不相邻。
    expect(beats[idx - 1].seq).toBe(5);
  });
});

describe("buildTrustBeltBeats — 琥珀误分类修复（2026-07-11 同批，真机三连 bug）", () => {
  // 真机症状：一局"换引擎"里琥珀句连出三条、中间零④⑤对、最后一句逐字重复
  // 两遍。根因：emit_replan_router 对每次 replan 决策（含 llm_backprompt，
  // LLM 自己重试、根本没换引擎）都发 PLAN_FALLBACK，旧实现不读 `.to`，一律
  // 当⑥渲染。本组断言：llm_backprompt / give_up / ils→ils 自环三种"非真实
  // 引擎切换"均不生成⑥拍、不拆散④⑤配对；只有 from!==to 且 to∈{ils,rule}
  // 的真实切换才生成⑥拍。

  it("to===llm_backprompt：不生成⑥拍，且不打断同一轮的④⑤配对", () => {
    const criticReport: CriticReport = {
      violationRounds: [violationRound(1, 1, "duration_out_of_range")],
      fixAttempts: [fixAttempt(3, 2)],
      fallbackHops: [
        { seq: 2, arrivalIdx: 2, from: "llm_first", to: "llm_backprompt", reason: "LLM 修正重出" },
      ],
    };
    const beats = buildTrustBeltBeats(baseInput({ criticReport }));
    expect(beats.some((b) => b.kind === "fallback")).toBe(false);
    // fix 紧邻同一轮 discover（未被 llm_backprompt 打断配对）
    const idx = beats.findIndex((b) => b.kind === "fix");
    expect(idx).toBeGreaterThan(0);
    expect(beats[idx - 1].kind).toBe("discover");
    expect(beats[idx - 1].seq).toBe(1);
  });

  it("to===give_up：不生成⑥拍（收尾交给⑦拍诚实分支，不重复宣布一次引擎切换）", () => {
    const criticReport: CriticReport = {
      violationRounds: [],
      fixAttempts: [],
      fallbackHops: [
        { seq: 1, arrivalIdx: 1, from: "rule", to: "give_up", reason: "rule planner 也未能产出方案" },
      ],
    };
    const beats = buildTrustBeltBeats(
      baseInput({ criticReport, itineraryReady: true, finalStrategy: "give_up" }),
    );
    expect(beats.some((b) => b.kind === "fallback")).toBe(false);
    // ⑦拍诚实改口独立承担收尾叙事
    const done = beats.find((b) => b.kind === "done");
    expect(done?.text).toBe("试了几版都排不下，先保留这版方案");
  });

  it("from===to===ils（ILS 成功自环）：不生成第二条⑥拍——不是又切了一次引擎", () => {
    const criticReport: CriticReport = {
      violationRounds: [],
      fixAttempts: [],
      fallbackHops: [
        { seq: 1, arrivalIdx: 1, from: "llm_first", to: "ils", reason: "LLM 失败，切换 ILS 算法兜底" },
        { seq: 2, arrivalIdx: 2, from: "ils", to: "ils", reason: "ILS 算法给出可行方案，成功兜底" },
      ],
    };
    const beats = buildTrustBeltBeats(baseInput({ criticReport }));
    const fallbackBeats = beats.filter((b) => b.kind === "fallback");
    // 只有第一跳（llm_first→ils，真实切换）产生⑥拍；自环那一跳不产生第二条
    expect(fallbackBeats).toHaveLength(1);
  });

  it("真实抓包回放（S2 撸串场景，同 event-handlers.test.ts 的抓包 fixture）：" +
    "修前会连出 4 条⑥且因果错位，修后只在真实 llm→ils 切换处出 1 条⑥，" +
    "叙事呈现④→⑤→⑥→⑦", () => {
    // 复用 lib/store/event-handlers.test.ts「真实抓包回放（LLM_PROVIDER=stub，
    // S2 撸串场景）」同一份真机 SSE 序列：3 轮 itinerary=None 空 violations
    // （critic 的"这稿压根没生成出方案"分支）+ 2 次 llm_backprompt 退回 + 1 次
    // 真实切 ILS + 1 次 ILS 成功自环。
    const criticReport: CriticReport = {
      violationRounds: [
        // 真实抓包里这三轮 violations=[]（itinerary=None 分支，不是规则违规），
        // 不用 violationRound() 助手（它总塞一条 code）——照真实 fixture 形状。
        { seq: 10, arrivalIdx: 1, fixAttempt: 1, violations: [] },
        { seq: 16, arrivalIdx: 2, fixAttempt: 2, violations: [] },
        { seq: 22, arrivalIdx: 3, fixAttempt: 3, violations: [] },
      ],
      fixAttempts: [fixAttempt(14, 2), fixAttempt(20, 3)],
      fallbackHops: [
        { seq: 12, arrivalIdx: 1, from: "llm_first", to: "llm_backprompt", reason: "LLM 修正重出" },
        { seq: 18, arrivalIdx: 2, from: "llm_first", to: "llm_backprompt", reason: "LLM 修正重出" },
        { seq: 24, arrivalIdx: 3, from: "llm_first", to: "ils", reason: "LLM 失败，切换 ILS 算法兜底" },
        { seq: 27, arrivalIdx: 4, from: "ils", to: "ils", reason: "ILS 算法给出可行方案，成功兜底" },
      ],
    };
    const beats = buildTrustBeltBeats(
      baseInput({ criticReport, itineraryReady: true, finalStrategy: "ils" }),
    );

    // 核心断言 1：⑥拍只出现 1 次（真实的 llm_first→ils 切换），不是修前的
    // "4 条 plan_fallback 全部渲染成⑥"（那会连出 3 条⑥ + 1 条自环⑥ = 4 条）。
    const fallbackBeats = beats.filter((b) => b.kind === "fallback");
    expect(fallbackBeats).toHaveLength(1);

    // 核心断言 2：因果顺序正确——discover 在 fix 之前，fix 在 fallback 之前，
    // fallback 在 done 之前（④→⑤→⑥→⑦，不是修前截图那种"⑥→⑤→④→⑥"倒序）。
    const kinds = beats.map((b) => b.kind);
    const discoverIdx = kinds.indexOf("discover");
    const fixIdx = kinds.indexOf("fix");
    const fallbackIdx = kinds.indexOf("fallback");
    const doneIdx = kinds.indexOf("done");
    expect(discoverIdx).toBeGreaterThanOrEqual(0);
    expect(fixIdx).toBeGreaterThan(discoverIdx);
    expect(fallbackIdx).toBeGreaterThan(fixIdx);
    expect(doneIdx).toBeGreaterThan(fallbackIdx);

    // 核心断言 3：⑦拍是"规划成功"（真实切换到 ILS 且成功，不是 give_up 诚实改口）
    expect(beats[doneIdx].text).toBe("规划成功");
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

describe("buildRelaxedSearchNotice — ②拍芯片行尾放宽重搜提示", () => {
  function poiCallWithRelax(
    arrivalIdx: number,
    relaxedTags: string[],
    count: number,
  ): TrustBeltToolCall {
    return {
      tool: "search_pois",
      arrivalIdx,
      output: {
        success: true,
        count,
        preview: [{ kind: "poi", name: "占位", rating: 4.0 }],
        relaxed_tags: relaxedTags,
      },
    };
  }

  function restaurantCallWithRelax(
    arrivalIdx: number,
    relaxedTags: string[],
    count: number,
  ): TrustBeltToolCall {
    return {
      tool: "search_restaurants",
      arrivalIdx,
      output: {
        success: true,
        count,
        preview: [{ kind: "restaurant", name: "占位", rating: 4.0 }],
        relaxed_tags: relaxedTags,
      },
    };
  }

  it("无放宽（relaxed_tags 缺省或空）→ null", () => {
    expect(buildRelaxedSearchNotice([])).toBeNull();
    expect(
      buildRelaxedSearchNotice([poiCallWithRelax(1, [], 5)]),
    ).toBeNull();
  });

  it("有放宽 → 取第一个被丢的 tag + 放宽后的真实候选数", () => {
    const result = buildRelaxedSearchNotice([
      poiCallWithRelax(1, ["安静聊天", "拍照友好"], 12),
    ]);
    expect(result).toEqual({ tag: "安静聊天", count: 12 });
  });

  it("poi/restaurant 都放宽时只展示一条（poi 优先），不堆两行", () => {
    const result = buildRelaxedSearchNotice([
      poiCallWithRelax(1, ["安静聊天"], 12),
      restaurantCallWithRelax(2, ["不辣"], 8),
    ]);
    expect(result).toEqual({ tag: "安静聊天", count: 12 });
  });

  it("只有 restaurant 放宽（poi 未放宽）→ 展示 restaurant 那条", () => {
    const result = buildRelaxedSearchNotice([
      poiCallWithRelax(1, [], 5),
      restaurantCallWithRelax(2, ["不辣"], 8),
    ]);
    expect(result).toEqual({ tag: "不辣", count: 8 });
  });

  it("ILS/rule 兜底重查（同名 tool 但 output 无 preview）不误判为本轮放宽", () => {
    const result = buildRelaxedSearchNotice([
      poiCallWithRelax(1, [], 5),
      {
        tool: "search_pois",
        arrivalIdx: 9,
        output: { success: true, candidates: [{ id: "P001" }], relaxed_tags: ["安静聊天"] },
      },
    ]);
    expect(result).toBeNull();
  });

  it("同轮内同 tool 多次调用 → 取最后一组（arrivalIdx 最大）判定放宽", () => {
    const result = buildRelaxedSearchNotice([
      poiCallWithRelax(1, ["安静聊天"], 3),
      poiCallWithRelax(5, [], 20), // 反馈重规划后不再需要放宽
    ]);
    expect(result).toBeNull();
  });
});

describe("buildProfileFieldsReceipt — ①拍画像收据", () => {
  function profileCall(
    arrivalIdx: number,
    profileFields: Array<{ field: string; label: string; tags: string[] }>,
  ): TrustBeltToolCall {
    return {
      tool: "get_user_profile",
      arrivalIdx,
      output: { success: true, found: true, profile_fields: profileFields },
    };
  }

  it("无 get_user_profile 调用 → 空数组", () => {
    expect(buildProfileFieldsReceipt([])).toEqual([]);
  });

  it("有调用但无 profile_fields 键（这局没有字段真被画像先验改写）→ 空数组", () => {
    const result = buildProfileFieldsReceipt([
      { tool: "get_user_profile", arrivalIdx: 1, output: { success: true, found: true } },
    ]);
    expect(result).toEqual([]);
  });

  it("有 profile_fields → 原样透传（后端已判定好，前端不重新判定）", () => {
    const result = buildProfileFieldsReceipt([
      profileCall(1, [{ field: "dietary_constraints", label: "饮食偏好", tags: ["日料"] }]),
    ]);
    expect(result).toEqual([
      { field: "dietary_constraints", label: "饮食偏好", tags: ["日料"] },
    ]);
  });

  it("同轮内多次 get_user_profile 调用（反馈重规划）→ 取最后一组（arrivalIdx 最大）", () => {
    const result = buildProfileFieldsReceipt([
      profileCall(1, [{ field: "dietary_constraints", label: "饮食偏好", tags: ["旧值"] }]),
      profileCall(5, [{ field: "experience_tags", label: "体验偏好", tags: ["新值"] }]),
    ]);
    expect(result).toEqual([
      { field: "experience_tags", label: "体验偏好", tags: ["新值"] },
    ]);
  });

  it("非 get_user_profile 工具调用不参与画像收据提取", () => {
    const result = buildProfileFieldsReceipt([
      { tool: "search_pois", arrivalIdx: 1, output: { success: true, count: 3 } },
    ]);
    expect(result).toEqual([]);
  });

  it("output.profile_fields 非数组（异常线上数据）→ 不消费、不炸", () => {
    const result = buildProfileFieldsReceipt([
      { tool: "get_user_profile", arrivalIdx: 1, output: { success: true, profile_fields: "garbage" } },
    ]);
    expect(result).toEqual([]);
  });

  it("profile_fields 空数组 → 视同无收据（不展示空行）", () => {
    const result = buildProfileFieldsReceipt([profileCall(1, [])]);
    expect(result).toEqual([]);
  });
});

describe("buildChecksRunReceipt — ⑦拍质检收据", () => {
  it("无携带 checksRun 的条目 → null", () => {
    expect(buildChecksRunReceipt([])).toBeNull();
    expect(buildChecksRunReceipt([{ checksRun: null }, { checksRun: undefined }])).toBeNull();
  });

  it("有携带 checksRun 的条目 → 原样透传（后端已从 REGISTRY 现场数好）", () => {
    const result = buildChecksRunReceipt([
      { checksRun: null },
      { checksRun: 18 },
      { checksRun: null },
    ]);
    expect(result).toBe(18);
  });

  it("取首个携带该字段的条目（同③拍 plan_reason 的既有取法）", () => {
    const result = buildChecksRunReceipt([{ checksRun: 18 }, { checksRun: 99 }]);
    expect(result).toBe(18);
  });
});

describe("buildSpineNodes — 折叠脊柱节点投影", () => {
  function beat(kind: TrustBeltBeat["kind"], amber = false): TrustBeltBeat {
    return { id: `${kind}-1`, kind, text: "占位", seq: 0, amber };
  }

  it("②检索拍：有真实召回总数时携带数字", () => {
    const nodes = buildSpineNodes([beat("search")], {
      searchTotalCount: 50,
      midNodeCount: 0,
      checksRun: null,
    });
    expect(nodes[0]).toEqual({ id: "search-1", kind: "search", count: 50, amber: false });
  });

  it("②检索拍：召回总数为 0 时不编数字（count=null，只显示图标）", () => {
    const nodes = buildSpineNodes([beat("search")], {
      searchTotalCount: 0,
      midNodeCount: 0,
      checksRun: null,
    });
    expect(nodes[0].count).toBeNull();
  });

  it("③规划拍：携带最终方案活动节点数", () => {
    const nodes = buildSpineNodes([beat("planning")], {
      searchTotalCount: 0,
      midNodeCount: 3,
      checksRun: null,
    });
    expect(nodes[0].count).toBe(3);
  });

  it("⑦定稿拍：携带质检收据数字（无则 null）", () => {
    const withChecks = buildSpineNodes([beat("done")], {
      searchTotalCount: 0,
      midNodeCount: 0,
      checksRun: 18,
    });
    expect(withChecks[0].count).toBe(18);

    const withoutChecks = buildSpineNodes([beat("done")], {
      searchTotalCount: 0,
      midNodeCount: 0,
      checksRun: null,
    });
    expect(withoutChecks[0].count).toBeNull();
  });

  it("④⑤⑥自愈拍：琥珀节保持琥珀（对象恒存）", () => {
    const nodes = buildSpineNodes(
      [beat("discover", true), beat("fix", true), beat("fallback", true)],
      { searchTotalCount: 0, midNodeCount: 0, checksRun: null },
    );
    expect(nodes.every((n) => n.amber)).toBe(true);
    expect(nodes.every((n) => n.count === null)).toBe(true);
  });

  it("①理解拍：无可靠数字来源，count 恒 null（不臆造）", () => {
    const nodes = buildSpineNodes([beat("understanding")], {
      searchTotalCount: 99,
      midNodeCount: 99,
      checksRun: 99,
    });
    expect(nodes[0].count).toBeNull();
  });

  it("同一 kind 出现多次（如④⑤各命中 2 轮）逐条投影，不合并", () => {
    const nodes = buildSpineNodes(
      [beat("discover", true), beat("fix", true), beat("discover", true), beat("fix", true)],
      { searchTotalCount: 0, midNodeCount: 0, checksRun: null },
    );
    expect(nodes).toHaveLength(4);
  });
});
