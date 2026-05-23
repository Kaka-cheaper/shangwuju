/**
 * store 行为单测：refinement_done 处理 / cancel / planner mode 切换。
 *
 * 这些是 C3 / C4 的核心：
 * - refinement_done 事件正确转 toast / lastRefinement
 * - changed_fields 中文显示规则（≤2 条独立 toast / >2 条聚合 toast）
 * - cancel 推 agent 消息 + warn toast
 * - setPlannerMode 改 cookie + 推 toast
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "./store";

beforeEach(() => {
  // 全部测试都跑在 jsdom-less 的 node 环境；mock document.cookie 让 setPlannerModeCookie 不抛
  Object.defineProperty(globalThis, "document", {
    value: { cookie: "" },
    configurable: true,
    writable: true,
  });
  // 重置 store 到 initialState
  useChatStore.setState({
    sessionId: "sess_test",
    scenarios: [],
    scenariosLoaded: false,
    plannerMode: "rule",
    streaming: false,
    streamError: null,
    messages: [],
    intent: null,
    toolCalls: [],
    replans: [],
    thoughts: [],
    itinerary: null,
    cancelled: false,
    lastRefinement: null,
    toasts: [],
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("store · refinement", () => {
  /** 直接调内部 handleEvent 的快捷方式：通过 store action 间接触发更稳，
   * 这里复用 sendMessage 不现实（需要 mock fetch），所以走 setState +
   * 手动派发事件流程。我们通过反射 handleEvent 暴露的 store 副作用断言。
   *
   * 实际上 store 模块没 export handleEvent；改用通过 set 直接构造结果断言
   * 「假如解析过 refinement_done，store 应该是怎样的」也行，但失去解析路径覆盖。
   *
   * 最佳方式：实际派发 SseEvent 给 store 内部 handleEvent。
   * 由于 handleEvent 不是 export，这里采用 sendMessage + mock fetch 路径
   * （正经端到端覆盖参考 sse.test.ts）。
   *
   * 本文件只断言 cancel / setPlannerMode / pushToast 这种纯 action 行为。
   */

  it("cancel：推 agent 消息 + warn toast", () => {
    useChatStore.setState({
      itinerary: {
        schema_version: "edge_v1",
        summary: "x",
        nodes: [],
        hops: [],
        schedule: [],
        orders: [],
        total_minutes: 0,
      },
      streaming: true,
    });
    useChatStore.getState().cancel();
    const s = useChatStore.getState();
    expect(s.streaming).toBe(false);
    expect(s.cancelled).toBe(true);
    expect(s.messages.at(-1)?.text).toContain("已取消当前方案");
    expect(s.toasts.some((t) => t.kind === "warn" && t.text.includes("已取消")))
      .toBe(true);
  });

  it("pushToast：3.5s 后自动消失（warn 是 4.5s）", () => {
    vi.useFakeTimers();
    useChatStore.getState().pushToast({ kind: "info", text: "hi" });
    expect(useChatStore.getState().toasts).toHaveLength(1);
    vi.advanceTimersByTime(3400);
    expect(useChatStore.getState().toasts).toHaveLength(1);
    vi.advanceTimersByTime(200);
    expect(useChatStore.getState().toasts).toHaveLength(0);
  });

  it("warn toast：4.5s 后才消失", () => {
    vi.useFakeTimers();
    useChatStore.getState().pushToast({ kind: "warn", text: "x" });
    vi.advanceTimersByTime(3600);
    expect(useChatStore.getState().toasts).toHaveLength(1);
    vi.advanceTimersByTime(1000);
    expect(useChatStore.getState().toasts).toHaveLength(0);
  });

  it("dismissToast：手动关闭", () => {
    useChatStore.getState().pushToast({ kind: "info", text: "x" });
    const id = useChatStore.getState().toasts[0].id;
    useChatStore.getState().dismissToast(id);
    expect(useChatStore.getState().toasts).toHaveLength(0);
  });

  it("setPlannerMode：写 cookie + 推 toast + 更新 plannerMode", () => {
    useChatStore.getState().setPlannerMode("llm");
    const s = useChatStore.getState();
    expect(s.plannerMode).toBe("llm");
    expect(globalThis.document.cookie).toMatch(/shangwuju_planner_mode=llm/);
    expect(s.toasts.some((t) => t.text.includes("LLM 自主"))).toBe(true);

    useChatStore.getState().setPlannerMode("rule");
    expect(useChatStore.getState().plannerMode).toBe("rule");
    expect(globalThis.document.cookie).toMatch(/shangwuju_planner_mode=rule/);
  });

  it("setPlannerMode silent：不推 toast，仍写 cookie", () => {
    useChatStore.getState().setPlannerMode("llm", { silent: true });
    const s = useChatStore.getState();
    expect(s.plannerMode).toBe("llm");
    expect(globalThis.document.cookie).toMatch(/shangwuju_planner_mode=llm/);
    expect(s.toasts).toHaveLength(0);
  });

  it("setPlannerMode 同 mode：不重复推 toast / 不重写 cookie", () => {
    useChatStore.getState().setPlannerMode("llm");
    expect(useChatStore.getState().toasts).toHaveLength(1);
    useChatStore.getState().setPlannerMode("llm");
    expect(useChatStore.getState().toasts).toHaveLength(1);
  });
});
