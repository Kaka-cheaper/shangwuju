/**
 * 惰性清空（Bug 修复）单测：
 * - 非重规划信号（chitchat_reply）不动主页面（方案/intent 保留）。
 * - 重跑信号（intent_parsed / refinement_start）才清空腾位，且一轮只清一次。
 */

import { describe, expect, it } from "vitest";

import { handleEvent } from "./event-handlers";
import type { ChatState, Getter, Setter } from "./types";

function makeStore(initial: Partial<ChatState>) {
  let state = { ...initial } as ChatState;
  const get: Getter = () => state;
  const set: Setter = (partial) => {
    const p = typeof partial === "function" ? partial(state) : partial;
    state = { ...state, ...p };
  };
  return { get, set, getState: () => state };
}

const ITIN = {
  schema_version: "edge_v1",
  summary: "测试方案",
  nodes: [],
  hops: [],
  schedule: [],
  orders: [],
  total_minutes: 0,
} as unknown as ChatState["itinerary"];

function baseState(): Partial<ChatState> {
  return {
    awaitingReplan: true,
    itinerary: ITIN,
    intent: { raw_input: "x" } as unknown as ChatState["intent"],
    toolCalls: [],
    replans: [],
    thoughts: [],
    chitchatReplies: [],
    narration: null,
    narrationMessages: null,
    lastRefinement: null,
    criticReport: { violationRounds: [], fixAttempts: [], fallbackHops: [] },
  };
}

describe("惰性清空", () => {
  it("chitchat_reply 不清空主页面（提问/确认/预约/闲聊场景）", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "chitchat_reply",
      seq: 1,
      payload: { input_kind: "chitchat", reply_text: "hi", tone: "warm", cta_chips: [] },
    } as never);
    // 方案与 intent 纹丝不动；仍挂起等待
    expect(store.getState().itinerary).toBe(ITIN);
    expect(store.getState().intent).not.toBeNull();
    expect(store.getState().awaitingReplan).toBe(true);
    expect(store.getState().chitchatReplies).toHaveLength(1);
  });

  it("intent_parsed 是重跑信号 → 清空主页面腾位", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "intent_parsed",
      seq: 1,
      payload: { raw_input: "new" },
    } as never);
    expect(store.getState().itinerary).toBeNull(); // 旧方案清掉
    expect(store.getState().awaitingReplan).toBe(false);
    // 清空后随即写入新 intent
    expect(store.getState().intent).toMatchObject({ raw_input: "new" });
  });

  it("预约确认气泡（含 confirm chip）→ 收起意图解析 + 思考链路，保留方案卡", () => {
    const store = makeStore({
      ...baseState(),
      thoughts: [{ seq: 1, text: "出 plan", timestamp_ms: null }],
      chitchatReplies: [],
    });
    handleEvent(store.set, store.get, {
      type: "chitchat_reply",
      seq: 1,
      payload: {
        input_kind: "chitchat",
        reply_text: "好的，点确认预约",
        tone: "warm",
        cta_chips: [{ label: "确认预约", send: "确认预约", action: "confirm" }],
      },
    } as never);
    expect(store.getState().intent).toBeNull(); // 意图解析卡收起
    expect(store.getState().thoughts).toHaveLength(0); // 思考链路收起
    expect(store.getState().itinerary).toBe(ITIN); // 方案卡保留
    expect(store.getState().chitchatReplies).toHaveLength(1); // 气泡照常
  });

  it("普通闲聊气泡（无 confirm chip）→ 主页面不动", () => {
    const store = makeStore({
      ...baseState(),
      thoughts: [{ seq: 1, text: "x", timestamp_ms: null }],
      chitchatReplies: [],
    });
    handleEvent(store.set, store.get, {
      type: "chitchat_reply",
      seq: 1,
      payload: {
        input_kind: "chitchat",
        reply_text: "你好呀",
        tone: "warm",
        cta_chips: [],
      },
    } as never);
    expect(store.getState().intent).not.toBeNull(); // 不清
    expect(store.getState().thoughts).toHaveLength(1);
    expect(store.getState().itinerary).toBe(ITIN);
  });

  it("一轮只清一次：refinement_start 清空后，后续 intent_parsed 不再清", () => {
    const store = makeStore(baseState());
    // feedback 路径第一个信号
    handleEvent(store.set, store.get, {
      type: "refinement_start",
      seq: 1,
      payload: { feedback_text: "太远了" },
    } as never);
    expect(store.getState().awaitingReplan).toBe(false);
    // 模拟随后 refinement_done 写入 refined intent
    store.set({ intent: { raw_input: "refined" } } as never);
    // 再来 intent_parsed（重规划刷新）——不该把 refined intent 抹掉
    handleEvent(store.set, store.get, {
      type: "intent_parsed",
      seq: 2,
      payload: { raw_input: "refined" },
    } as never);
    expect(store.getState().intent).toMatchObject({ raw_input: "refined" });
  });
});

describe("agent_narration：体感编排批 P1 标题更新（从能用到精彩）", () => {
  it("payload.title 存在时原地更新 itinerary.summary，其余字段不受影响", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "agent_narration",
      seq: 1,
      payload: {
        text: "暖场文案",
        stage: "stream",
        title: "和室友撸串+唱K，4.5小时",
      },
    } as never);
    const itin = store.getState().itinerary as unknown as { summary: string };
    expect(itin.summary).toBe("和室友撸串+唱K，4.5小时");
    // 其余 itinerary 字段原样保留（不是整份替换）
    expect(itin).toMatchObject({ schema_version: "edge_v1", total_minutes: 0 });
    expect(store.getState().narration).toEqual({ text: "暖场文案", stage: "stream" });
  });

  it("payload.title 缺省时 itinerary 保持不变（沿用 finalize_plan 已推送的规则标题）", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "agent_narration",
      seq: 1,
      payload: { text: "暖场文案", stage: "stream" },
    } as never);
    // 同一个对象引用，没有因为 title 缺省而产生多余的 itinerary 更新
    expect(store.getState().itinerary).toBe(ITIN);
  });

  it("itinerary 尚未就绪（null）时即便带 title 也不报错、不凭空造一个 itinerary", () => {
    const store = makeStore({ ...baseState(), itinerary: null });
    handleEvent(store.set, store.get, {
      type: "agent_narration",
      seq: 1,
      payload: { text: "暖场文案", stage: "stream", title: "标题" },
    } as never);
    expect(store.getState().itinerary).toBeNull();
  });
});

describe("agent_narration.messages（D-7：结构化告知通道——'点开看全部'的落点）", () => {
  it("payload.messages 存在时原样落 narrationMessages", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "agent_narration",
      seq: 1,
      payload: {
        text: "已避开 2 处偏远候选，还有 1 处小取舍",
        stage: "stream",
        messages: [
          { kind: "advisory", code: "distance_relaxed", text: "为满足时长，把最远候选换成了近一点的" },
        ],
      },
    } as never);
    expect(store.getState().narrationMessages).toEqual([
      { kind: "advisory", code: "distance_relaxed", text: "为满足时长，把最远候选换成了近一点的" },
    ]);
  });

  it("payload.messages 缺省时清成 null——不是 node_actions/demand_ledger 那种保留上一版", () => {
    const store = makeStore({
      ...baseState(),
      narrationMessages: [{ kind: "advisory", code: "x", text: "上一版的取舍" }],
    });
    handleEvent(store.set, store.get, {
      type: "agent_narration",
      seq: 1,
      payload: { text: "这一版暖场文案（没有需要折叠的取舍）", stage: "stream" },
    } as never);
    // narration.text 已经换成新版本，旧版本的展开列表不该继续挂着
    expect(store.getState().narration?.text).toBe("这一版暖场文案（没有需要折叠的取舍）");
    expect(store.getState().narrationMessages).toBeNull();
  });

  it("重跑信号（intent_parsed）清空腾位时一并清空 narrationMessages，防上一轮取舍串场", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "agent_narration",
      seq: 1,
      payload: {
        text: "暖场文案",
        stage: "stream",
        messages: [{ kind: "advisory", code: "c1", text: "取舍说明" }],
      },
    } as never);
    expect(store.getState().narrationMessages).toHaveLength(1);
    handleEvent(store.set, store.get, {
      type: "intent_parsed",
      seq: 2,
      payload: { raw_input: "new" },
    } as never);
    expect(store.getState().narrationMessages).toBeNull();
  });

  it("反馈重规划（refinement_start）清空腾位时一并清空 narrationMessages", () => {
    const store = makeStore({
      ...baseState(),
      narrationMessages: [{ kind: "advisory", code: "c1", text: "上一轮的取舍" }],
    });
    handleEvent(store.set, store.get, {
      type: "refinement_start",
      seq: 1,
      payload: { feedback_text: "太远了" },
    } as never);
    expect(store.getState().narrationMessages).toBeNull();
  });
});

describe("agent_narration.swap_alternatives_count（换菜备选收据，2026-07-11）", () => {
  it("payload.swap_alternatives_count 存在时原样落 swapAlternativesCount", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "agent_narration",
      seq: 1,
      payload: {
        text: "按你的要求，把「A」换成了「B」，更安静。",
        stage: "stream",
        swap_alternatives_count: 5,
      },
    } as never);
    expect(store.getState().swapAlternativesCount).toBe(5);
  });

  it("payload.swap_alternatives_count 缺省时清成 null——绑定这一版叙事，不沿用上一次换菜的数字", () => {
    const store = makeStore({ ...baseState(), swapAlternativesCount: 3 });
    handleEvent(store.set, store.get, {
      type: "agent_narration",
      seq: 1,
      payload: { text: "这一版暖场文案（不是换菜结果）", stage: "stream" },
    } as never);
    expect(store.getState().swapAlternativesCount).toBeNull();
  });

  it("重跑信号（intent_parsed）清空腾位时一并清空 swapAlternativesCount", () => {
    const store = makeStore({ ...baseState(), swapAlternativesCount: 4 });
    handleEvent(store.set, store.get, {
      type: "intent_parsed",
      seq: 1,
      payload: { raw_input: "new" },
    } as never);
    expect(store.getState().swapAlternativesCount).toBeNull();
  });
});

describe("critic 校验 + 自愈闭环三事件（Step 2：系统自愈过程可视化）", () => {
  it("critic_violations 追加一个 violationRound，字段原样落 store", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "critic_violations",
      seq: 5,
      payload: {
        fix_attempt: 1,
        violations: [
          {
            code: "duration_out_of_range",
            severity: "critical",
            message: "总时长超出预期范围超过 30 分钟",
            field_path: "nodes[2].duration_minutes",
          },
        ],
      },
    } as never);
    const rounds = store.getState().criticReport.violationRounds;
    expect(rounds).toHaveLength(1);
    expect(rounds[0]).toMatchObject({
      seq: 5,
      fixAttempt: 1,
      violations: [{ message: "总时长超出预期范围超过 30 分钟" }],
    });
    // 不影响其余两个子数组
    expect(store.getState().criticReport.fixAttempts).toHaveLength(0);
    expect(store.getState().criticReport.fallbackHops).toHaveLength(0);
  });

  it("critic_fix_attempt 追加一个 fixAttempt 记录", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "critic_fix_attempt",
      seq: 6,
      payload: { attempt: 2, feedback_text: "（详见上一条 critic_violations）" },
    } as never);
    expect(store.getState().criticReport.fixAttempts).toMatchObject([
      { seq: 6, attempt: 2, feedbackText: "（详见上一条 critic_violations）" },
    ]);
  });

  it("plan_fallback 追加一个 fallbackHop 记录", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "plan_fallback",
      seq: 7,
      payload: { from: "llm_first", to: "ils", reason: "LLM 失败，切换 ILS 算法兜底" },
    } as never);
    expect(store.getState().criticReport.fallbackHops).toMatchObject([
      { seq: 7, from: "llm_first", to: "ils", reason: "LLM 失败，切换 ILS 算法兜底" },
    ]);
  });

  it("三事件依次到达按到达顺序各自累加，互不覆盖", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "critic_violations",
      seq: 1,
      payload: {
        fix_attempt: 1,
        violations: [
          {
            code: "distance_exceeded",
            severity: "critical",
            message: "两个节点间距离超限",
            field_path: "hops[1].distance_km",
          },
        ],
      },
    } as never);
    handleEvent(store.set, store.get, {
      type: "critic_fix_attempt",
      seq: 2,
      payload: { attempt: 2, feedback_text: "x" },
    } as never);
    handleEvent(store.set, store.get, {
      type: "critic_violations",
      seq: 3,
      payload: { fix_attempt: 2, violations: [] },
    } as never);
    handleEvent(store.set, store.get, {
      type: "plan_fallback",
      seq: 4,
      payload: { from: "llm_first", to: "llm_backprompt", reason: "LLM 修正重出" },
    } as never);
    const report = store.getState().criticReport;
    expect(report.violationRounds).toHaveLength(2);
    expect(report.fixAttempts).toHaveLength(1);
    expect(report.fallbackHops).toHaveLength(1);
  });

  it("真实抓包回放（LLM_PROVIDER=stub，S2 撸串场景）：2 次 llm_backprompt 退回 + 1 次 ILS 兜底成功", () => {
    // 2026-07-03 手工冒烟：起 backend（LLM_PROVIDER=stub）+ curl /chat/turn，
    // 完整抓包保存原始 SSE data 行（含本用例节选的 critic 段）。stub 的固定
    // intent JSON 让 blueprint 生成必然失败（backend/agent/planning/blueprint/
    // blueprint_llm.py 要求 payload.nodes，stub JSON 没有）→ planner_node 每次
    // 都把 blueprint=None 写回 → critic_node 走 itinerary is None 分支
    // （backend/agent/graph/nodes/critic.py:36-47）→ violations=[] 但
    // has_critical=True——这正是 criticHeadline 里"未能生成有效方案"分支要处理
    // 的真实场景，不是臆造的边界情况。retry 用尽后 replan_router 切 ILS，
    // ILS 直接给出可行方案成功兜底（backend/agent/graph/nodes/replan.py 的
    // "ils"→"ils" 成功记录）。
    const store = makeStore(baseState());
    const captured: { type: string; seq: number; payload: unknown }[] = [
      { type: "critic_violations", seq: 10, payload: { violations: [], fix_attempt: 1 } },
      { type: "plan_fallback", seq: 12, payload: { from: "llm_first", to: "llm_backprompt", reason: "LLM 修正重出" } },
      { type: "critic_fix_attempt", seq: 14, payload: { attempt: 2, feedback_text: "（详见上一条 critic_violations）" } },
      { type: "critic_violations", seq: 16, payload: { violations: [], fix_attempt: 2 } },
      { type: "plan_fallback", seq: 18, payload: { from: "llm_first", to: "llm_backprompt", reason: "LLM 修正重出" } },
      { type: "critic_fix_attempt", seq: 20, payload: { attempt: 3, feedback_text: "（详见上一条 critic_violations）" } },
      { type: "critic_violations", seq: 22, payload: { violations: [], fix_attempt: 3 } },
      { type: "plan_fallback", seq: 24, payload: { from: "llm_first", to: "ils", reason: "LLM 失败，切换 ILS 算法兜底" } },
      { type: "plan_fallback", seq: 27, payload: { from: "ils", to: "ils", reason: "ILS 算法给出可行方案，成功兜底（不再进一步降级）" } },
    ];
    for (const ev of captured) {
      handleEvent(store.set, store.get, ev as never);
    }
    const report = store.getState().criticReport;
    expect(report.violationRounds).toHaveLength(3);
    expect(report.violationRounds.map((r) => r.fixAttempt)).toEqual([1, 2, 3]);
    // 真实抓包里这三轮的 violations 均为空数组（itinerary=None 分支，不是规则违规）
    expect(report.violationRounds.every((r) => r.violations.length === 0)).toBe(true);
    expect(report.fixAttempts).toHaveLength(2);
    expect(report.fixAttempts.map((f) => f.attempt)).toEqual([2, 3]);
    expect(report.fallbackHops).toHaveLength(4);
    expect(report.fallbackHops.map((h) => `${h.from}->${h.to}`)).toEqual([
      "llm_first->llm_backprompt",
      "llm_first->llm_backprompt",
      "llm_first->ils",
      "ils->ils",
    ]);
  });

  it("重跑信号（intent_parsed）清空腾位时一并清空 criticReport，防上一轮的自愈记录串场", () => {
    const store = makeStore(baseState());
    handleEvent(store.set, store.get, {
      type: "critic_violations",
      seq: 1,
      payload: {
        fix_attempt: 1,
        violations: [
          {
            code: "stages_incomplete",
            severity: "critical",
            message: "行程缺少必要环节",
            field_path: "nodes",
          },
        ],
      },
    } as never);
    expect(store.getState().criticReport.violationRounds).toHaveLength(1);
    // 下一轮开始：intent_parsed 是重跑信号，触发 clearForReplanIfPending
    handleEvent(store.set, store.get, {
      type: "intent_parsed",
      seq: 2,
      payload: { raw_input: "new" },
    } as never);
    const report = store.getState().criticReport;
    expect(report.violationRounds).toHaveLength(0);
    expect(report.fixAttempts).toHaveLength(0);
    expect(report.fallbackHops).toHaveLength(0);
  });
});
