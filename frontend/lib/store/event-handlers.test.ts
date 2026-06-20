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
    lastRefinement: null,
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
