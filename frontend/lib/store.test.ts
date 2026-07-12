/**
 * store 行为单测：refinement_done 处理 / cancel / planner mode 切换。
 *
 * 这些是 C3 / C4 的核心：
 * - refinement_done 事件正确转 toast / lastRefinement
 * - changed_fields 中文显示规则（≤2 条独立 toast / >2 条聚合 toast）
 * - cancel 推 agent 消息 + warn toast
 * - setPlannerMode 改 cookie + 推 toast
 *
 * 聊天气泡口播去重（卡片放全文，聊天放交接句）：
 * - sendMessage/confirm/refine 的 onDone 直接调 streamSse 的 handlers.onDone，
 *   mock 掉 "./sse" 模块后即可在不起真实网络请求的前提下驱动真实 store action，
 *   断言三处 onDone 推的交接句文案（而非复刻一份逻辑再测复刻品）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// 测试用假流：streamSse 被 mock 后不发起真实网络请求，也不推任何 onEvent——
// 直接调 handlers.onDone。真实 SSE 流里 awaitingReplan 由 handleEvent 收到
// intent_parsed / refinement_start 时清掉（见 store/event-handlers.ts）；这里
// 没有真流可清，改用一个测试专用开关模拟"本轮是否真重跑了"，由各测试用例
// 在调用 sendMessage 前设置。mockBeforeDone 允许测试在 onDone 触发前再改一次
// store 状态（模拟"流处理到最后一刻把 itinerary/narration 改成了 X"）。
let mockWasReplanTurn = true;
let mockBeforeDone: (() => void) | null = null;
vi.mock("./sse", () => ({
  streamSse: vi.fn(
    async (
      _url: string,
      _body: unknown,
      _signal: AbortSignal,
      handlers: { onDone?: () => void },
    ) => {
      if (mockWasReplanTurn) {
        useChatStore.setState({ awaitingReplan: false });
      }
      mockBeforeDone?.();
      handlers.onDone?.();
    },
  ),
}));

import { useChatStore } from "./store";
import type { DemandLedgerEntry } from "./types";

beforeEach(() => {
  mockWasReplanTurn = true;
  mockBeforeDone = null;
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
    narration: null,
    awaitingReplan: false,
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

  it("setPlannerMode persist:false：更新 mode 但不写 cookie（/health 静默同步用）", () => {
    // 模拟 /health 同步：跟随后端 env 到 llm，但不落 cookie
    // → 让无 cookie 的浏览器每次 mount 都能重新跟随后端，不被旧值锁死
    useChatStore.getState().setPlannerMode("llm", { silent: true, persist: false });
    const s = useChatStore.getState();
    expect(s.plannerMode).toBe("llm");
    expect(globalThis.document.cookie).not.toMatch(/shangwuju_planner_mode/);
    expect(s.toasts).toHaveLength(0);
  });

  it("setPlannerMode 同 mode：不重复推 toast / 不重写 cookie", () => {
    useChatStore.getState().setPlannerMode("llm");
    expect(useChatStore.getState().toasts).toHaveLength(1);
    useChatStore.getState().setPlannerMode("llm");
    expect(useChatStore.getState().toasts).toHaveLength(1);
  });
});

describe("store · 聊天气泡口播去重（卡片放全文，聊天放交接句）", () => {
  const makeItinerary = (summary: string) => ({
    schema_version: "edge_v1" as const,
    summary,
    nodes: [],
    hops: [],
    schedule: [],
    orders: [] as never[],
    total_minutes: 0,
  });

  it("sendMessage onDone：重跑成功后推交接句（非全文），不含口播原文", async () => {
    // 模拟"流已经把 itinerary_ready / agent_narration 都处理完"：预置好
    // itinerary + narration，mock streamSse 只负责触发 onDone。
    useChatStore.setState({
      itinerary: makeItinerary("和室友唱K，2.8小时"),
      narration: { text: "这是一段很长的口播全文，包含时间线与诚实披露……", stage: "stream" },
    });
    await useChatStore.getState().sendMessage("随便找个地方");
    const s = useChatStore.getState();
    const last = s.messages.at(-1);
    expect(last?.role).toBe("agent");
    expect(last?.text).toBe("排好了——和室友唱K，2.8小时。细节和提醒都在方案卡上。");
    // 红线：不能包含口播全文（否则又是卡片/聊天一字不差重复）
    expect(last?.text).not.toContain("很长的口播全文");
  });

  it("sendMessage onDone：itinerary 缺失但 narration 存在 → 兜底句", async () => {
    useChatStore.setState({
      itinerary: null,
      narration: { text: "口播全文", stage: "stream" },
    });
    await useChatStore.getState().sendMessage("随便找个地方");
    expect(useChatStore.getState().messages.at(-1)?.text).toBe(
      "方案排好了，细节在方案卡上。",
    );
  });

  it("sendMessage onDone：chitchat turn（未重跑）不推消息", async () => {
    mockWasReplanTurn = false; // 模拟没收到 intent_parsed / refinement_start
    useChatStore.setState({
      itinerary: makeItinerary("上一轮方案"),
      narration: { text: "上一轮口播", stage: "stream" },
    });
    await useChatStore.getState().sendMessage("你好");
    // chitchat turn 不该补 agent 消息（沿用既有纪律，本次改动不动这条判定逻辑）
    expect(useChatStore.getState().messages.some((m) => m.role === "agent")).toBe(
      false,
    );
  });

  it("confirm onDone：推交接句（凭证/安排在卡片里），不含口播原文", async () => {
    useChatStore.setState({
      itinerary: makeItinerary("和室友唱K，2.8小时"),
      narration: { text: "confirm 阶段的口播全文", stage: "confirm" },
    });
    await useChatStore.getState().confirm();
    const last = useChatStore.getState().messages.at(-1);
    expect(last?.text).toBe("都订好了——和室友唱K，2.8小时。凭证和安排都在卡片里。");
    expect(last?.text).not.toContain("confirm 阶段的口播全文");
  });

  it("confirm onDone：narration.stage 不是 confirm 时不推消息（既有 gate 不变）", async () => {
    useChatStore.setState({
      itinerary: makeItinerary("x"),
      narration: { text: "还是上一轮 stream 阶段的口播", stage: "stream" },
    });
    const before = useChatStore.getState().messages.length;
    await useChatStore.getState().confirm();
    expect(useChatStore.getState().messages.length).toBe(before);
  });

  it("refine onDone：推交接句（变化标在卡上），不含口播原文", async () => {
    // refine() 入口守卫要求已有 itinerary 才会开始；但其同步 set 块会立刻把
    // itinerary 清空等 refinement_done 事件重新填充（真实链路由 handleEvent
    // 完成，mock streamSse 不推 onEvent）——用 mockBeforeDone 模拟"事件已经
    // 把新方案填回 itinerary"这一步，再触发 onDone。
    useChatStore.setState({
      itinerary: makeItinerary("占位，供 refine() 入口守卫通过"),
      narration: null,
    });
    mockBeforeDone = () => {
      useChatStore.setState({
        itinerary: makeItinerary("换了一家人均更低的餐厅"),
        narration: { text: "refine 阶段的口播全文", stage: "stream" },
      });
    };
    await useChatStore.getState().refine("换个便宜点的餐厅");
    const last = useChatStore.getState().messages.at(-1);
    expect(last?.text).toBe("已按你说的调整了——换了一家人均更低的餐厅。变化都标在卡上。");
    expect(last?.text).not.toContain("refine 阶段的口播全文");
  });

  it("refine onDone：itinerary 被清空但 narration 存在 → 兜底句", async () => {
    // refine() 入口要求已有 itinerary 才会开始（否则直接 return，不发起流）。
    // 但流处理中 refine 会先把 itinerary 置 null 等 refinement_done 事件重新
    // 填充（见 store.ts refine() 顶部 set），若事件没如期把 itinerary 填回来
    // （真实链路里的极端情形），onDone 时 itin 仍是 null、narr 仍在——
    // 用 mockBeforeDone 模拟这一时序：onDone 触发前 itinerary 仍未被填回。
    useChatStore.setState({
      itinerary: makeItinerary("占位，供 refine() 入口守卫通过"),
      narration: null,
    });
    mockBeforeDone = () => {
      useChatStore.setState({
        itinerary: null,
        narration: { text: "refine 阶段的口播全文", stage: "stream" },
      });
    };
    await useChatStore.getState().refine("换个便宜点的餐厅");
    expect(useChatStore.getState().messages.at(-1)?.text).toBe(
      "已按你说的调整了，变化都标在卡上。",
    );
  });
});

describe("store · resetUserMemory 清两轨（UI 修复批）", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  const _ledgerFixture: DemandLedgerEntry[] = [
    {
      member_id: null,
      nickname: null,
      node_ref: { kind: "restaurant", target_id: "R1", title: "老地方火锅" },
      dimension: "price",
      value: "cheaper",
      status: "active",
      source_text: "便宜点的",
      created_at: 1,
    },
  ];

  it("成功时 UserMemory 轨（refreshPreferences）与 demandLedger 台账轨一起清空", async () => {
    useChatStore.setState({
      currentUserId: "u_dad",
      demandLedger: _ledgerFixture,
    });
    globalThis.fetch = vi.fn(async (url: string) => {
      if (url.includes("/reset")) {
        return { ok: true, json: async () => ({ status: "ok" }) } as Response;
      }
      // refreshPreferences 的 GET
      return { ok: true, json: async () => ({ persona: null }) } as Response;
    }) as unknown as typeof fetch;

    await useChatStore.getState().resetUserMemory();

    const s = useChatStore.getState();
    expect(s.demandLedger).toEqual([]);
    expect(s.toasts.at(-1)?.text).toBe("已清空学到的记忆");
    expect(s.toasts.at(-1)?.kind).toBe("success");
  });

  it("失败时不清空 demandLedger（半失败比全清空更诚实）", async () => {
    const existingLedger: DemandLedgerEntry[] = _ledgerFixture;
    useChatStore.setState({ currentUserId: "u_dad", demandLedger: existingLedger });
    globalThis.fetch = vi.fn(async () => {
      return { ok: false, status: 500, json: async () => ({}) } as Response;
    }) as unknown as typeof fetch;

    await useChatStore.getState().resetUserMemory();

    const s = useChatStore.getState();
    expect(s.demandLedger).toBe(existingLedger);
    expect(s.toasts.at(-1)?.kind).toBe("warn");
  });
});
