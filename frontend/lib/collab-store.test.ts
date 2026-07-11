import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildCollabChatStateSnapshot,
  buildCollabPlanningEvents,
  handleWsMessage,
  useCollabStore,
} from "./collab-store";
import { useChatStore } from "./store";

beforeEach(() => {
  useChatStore.setState({
    streaming: false,
    streamError: null,
    streamPhase: "idle",
    messages: [],
    intent: null,
    toolCalls: [],
    replans: [],
    thoughts: [],
    itinerary: null,
    previousItinerary: null,
    narration: null,
    cancelled: false,
    lastRefinement: null,
    chitchatReplies: [],
    memoryPersisted: null,
  });

  useCollabStore.setState({
    collabMode: false,
    roomId: null,
    ownerId: null,
    myUserId: null,
    myRole: null,
    members: [],
    constraints: [],
    votes: {},
    lockedStages: [],
    connected: false,
    connectionError: null,
    planningActive: false,
    planningTrigger: null,
    _wsClient: null,
  });
});

describe("collab store helpers", () => {
  it("builds planning events with current trace fields", () => {
    useChatStore.setState({
      intent: { raw_input: "plan", distance_max_km: 5 } as any,
      toolCalls: [
        {
          id: "search-1",
          tool: "search_poi",
          input: { q: "park" },
          startedAtSeq: 1,
          endedAtSeq: 2,
          arrivalIdx: 0,
          durationMs: 120,
          output: { success: true },
          groupId: "fanout-1",
          parallel: true,
        },
      ],
      memoryPersisted: {
        socialContext: "family",
        summaryPreview: "remembered",
        success: true,
        skippedReason: null,
      },
    });

    const events = buildCollabPlanningEvents(useChatStore.getState());
    const start = events.find((ev) => ev.type === "tool_call_start");
    const memory = events.find((ev) => ev.type === "memory_persisted");

    expect(start?.payload as Record<string, unknown>).toMatchObject({
      group_id: "fanout-1",
      parallel: true,
    });
    expect(memory?.payload as Record<string, unknown>).toMatchObject({
      social_context: "family",
      success: true,
    });
  });

  it("builds a chat snapshot for UI-only state", () => {
    const itinerary = {
      schema_version: "edge_v1",
      summary: "current",
      nodes: [],
      hops: [],
      schedule: [],
      orders: [],
      total_minutes: 0,
    };

    useChatStore.setState({
      itinerary: itinerary as any,
      previousItinerary: { ...itinerary, summary: "previous" } as any,
      narration: { text: "done", stage: "confirm" },
      lastRefinement: {
        feedbackText: "change",
        changedFields: ["time"],
      },
      chitchatReplies: [
        {
          id: "c1",
          payload: { kind: "meta", confidence: 1, reply: "hi" } as any,
          receivedAtMs: 1,
        },
      ],
    });

    const snapshot = buildCollabChatStateSnapshot(useChatStore.getState());

    expect(snapshot.itinerary?.summary).toBe("current");
    expect(snapshot.previousItinerary?.summary).toBe("previous");
    expect(snapshot.narration?.stage).toBe("confirm");
    expect(snapshot.lastRefinement?.changedFields).toEqual(["time"]);
    expect(snapshot.chitchatReplies).toHaveLength(1);
  });

  it("does not send confirm for participants", () => {
    const send = vi.fn();
    useCollabStore.setState({
      collabMode: true,
      myRole: "participant",
      _wsClient: {
        send,
        close: vi.fn(),
        isConnected: () => true,
      },
    });

    useCollabStore.getState().sendConfirm();

    expect(send).not.toHaveBeenCalled();
    expect(useCollabStore.getState().connectionError).toContain("发起人");
  });

  // ADR-0013 F-5：房间版节点调整入口——WS "adjust" 消息
  it("sendAdjust sends an adjust WS message with node_id + action", () => {
    const send = vi.fn();
    useCollabStore.setState({
      _wsClient: { send, close: vi.fn(), isConnected: () => true },
    });

    useCollabStore.getState().sendAdjust("R001", { type: "dislike" });

    expect(send).toHaveBeenCalledWith({
      type: "adjust",
      node_id: "R001",
      action: { type: "dislike" },
    });
  });
});

describe("handleWsMessage — F-5 房间生命周期/换菜下行消息", () => {
  const set = useCollabStore.setState;
  const get = useCollabStore.getState;

  it("member_joined appends a new member but does not duplicate an existing one", () => {
    handleWsMessage(set, get, {
      type: "member_joined",
      user_id: "p1",
      nickname: "小明",
      role: "participant",
    });
    expect(get().members).toHaveLength(1);

    // 同一个 user_id 再收到一次 member_joined（防御性：不应出现在真实后端行为里，
    // 但前端不该假设后端绝对不会重复——upsert 语义比"信任上游不重复"更稳）
    handleWsMessage(set, get, {
      type: "member_joined",
      user_id: "p1",
      nickname: "小明",
      role: "participant",
    });
    expect(get().members).toHaveLength(1);
  });

  it("member_reconnected updates the existing member's online/nickname without appending a new row", () => {
    handleWsMessage(set, get, {
      type: "member_joined",
      user_id: "p2",
      nickname: "旧昵称",
      role: "participant",
    });
    handleWsMessage(set, get, { type: "member_left", user_id: "p2" });
    expect(get().members.find((m) => m.user_id === "p2")?.online).toBe(false);

    handleWsMessage(set, get, {
      type: "member_reconnected",
      user_id: "p2",
      nickname: "新昵称",
      role: "participant",
    });

    expect(get().members).toHaveLength(1);
    const member = get().members.find((m) => m.user_id === "p2");
    expect(member?.online).toBe(true);
    expect(member?.nickname).toBe("新昵称");
  });

  it("node_locked/node_unlocked bridge to the main chat store's lockedNodeId", () => {
    handleWsMessage(set, get, { type: "node_locked", node_id: "R001", by_user: "p1", nickname: "小明" });
    expect(useChatStore.getState().lockedNodeId).toBe("R001");

    handleWsMessage(set, get, { type: "node_unlocked", node_id: "R001" });
    expect(useChatStore.getState().lockedNodeId).toBeNull();
  });

  it("room_state hydrates the shared demandLedger field from the room snapshot", () => {
    const ledgerEntry = {
      member_id: "p1",
      nickname: "小明",
      node_ref: { kind: "restaurant" as const, target_id: "R001" },
      dimension: "dietary" as const,
      value: "不辣",
      status: "active" as const,
      source_text: "不辣的",
      created_at: 1,
    };

    handleWsMessage(set, get, {
      type: "room_state",
      owner_id: "owner1",
      members: [],
      constraints: [],
      votes: {},
      locked_stages: [],
      itinerary: null,
      previous_itinerary: null,
      intent: null,
      planning_events: [],
      chat_messages: [],
      chat_state: null,
      planning_active: false,
      demand_ledger: [ledgerEntry],
    });

    expect(useChatStore.getState().demandLedger).toEqual([ledgerEntry]);
  });

  it("room_state hydrates the shared nodeActions field from the room snapshot (late-joiner fix)", () => {
    // 评委体验修复：中途加入的成员在此之前看不到节点调整按钮，因为
    // room_state 从来没把后端新增的顶层 node_actions 字段接进主 store——
    // 本用例钉住这条水合链路，同 demandLedger 上面那条既有先例。
    const nodeActions = {
      R001: {
        chips: [
          {
            node_id: "R001",
            label: "不辣的",
            adjustment: { dimension: "dietary" as const, value: "不辣" },
          },
        ],
        alternatives: [
          {
            kind: "restaurant" as const,
            target_id: "R017",
            name: "本帮小馆",
            rating: 4.5,
            distance_km: 1.2,
            price: 88,
            category: "本帮菜",
          },
        ],
      },
    };

    handleWsMessage(set, get, {
      type: "room_state",
      owner_id: "owner1",
      members: [],
      constraints: [],
      votes: {},
      locked_stages: [],
      itinerary: null,
      previous_itinerary: null,
      intent: null,
      planning_events: [],
      chat_messages: [],
      chat_state: null,
      planning_active: false,
      demand_ledger: [],
      node_actions: nodeActions,
    });

    expect(useChatStore.getState().nodeActions).toEqual(nodeActions);
  });

  // 问题②消息乱序修复：constraint_added 的 messages.createdAt 此前用
  // Date.now()（客户端接收时刻的本地钟），chitchat_reply 的 chitchatReplies.
  // receivedAtMs 用服务器 timestamp_ms（event-handlers.ts）——两把不同的钟
  // 比大小会导致顺序翻转。本用例钉住：即使 chitchat_reply 先到但服务器时间
  // 更晚、constraint_added 后到但服务器时间更早，合并后二者的时间戳仍应
  // 反映真实的服务器时间先后（而不是网络到达顺序）。
  it("constraint_added reuses the server timestamp for messages.createdAt so mixed ordering with chitchat_reply does not reverse", () => {
    // 服务器时间线：constraint（第 1 秒）早于 chitchat_reply（第 2 秒）。
    const constraintServerTs = 1_000; // 秒
    const chitchatServerTsMs = 2_000_000; // 毫秒

    // 网络到达顺序刻意反着来：chitchat_reply 先到，constraint_added 后到——
    // 若 constraint_added 仍用 Date.now() 本地钟，它的 createdAt 必然
    // 大于 chitchat 到达时刻的 receivedAtMs（本地钟单调递增且晚到），
    // 会把"更早发生"的约束排到"更晚发生"的闲聊后面，顺序翻转。
    handleWsMessage(useChatStore.setState as any, useChatStore.getState as any, {
      type: "planning_event",
      event: {
        type: "chitchat_reply",
        seq: 0,
        payload: {
          input_kind: "chitchat",
          confidence: 0.9,
          reply_text: "hi",
          tone: "warm",
          cta_chips: [],
        },
        timestamp_ms: chitchatServerTsMs,
      },
    });

    handleWsMessage(useCollabStore.setState, useCollabStore.getState, {
      type: "constraint_added",
      user_id: "other_user",
      nickname: "小北",
      text: "太远了",
      source: "text",
      timestamp: constraintServerTs,
      is_constraint: true,
    });

    const { messages, chitchatReplies } = useChatStore.getState();
    const constraintMsg = messages.find((m) => m.text.includes("太远了"));
    expect(constraintMsg).toBeDefined();
    expect(constraintMsg!.createdAt).toBe(constraintServerTs * 1000);

    expect(chitchatReplies).toHaveLength(1);
    expect(chitchatReplies[0].receivedAtMs).toBe(chitchatServerTsMs);

    // 核心断言：约束的服务器时间戳早于闲聊的服务器时间戳——合并排序时
    // 约束应排在闲聊之前，与"谁先发生"一致，不受网络到达顺序影响。
    expect(constraintMsg!.createdAt).toBeLessThan(chitchatReplies[0].receivedAtMs);
  });

  it("room_state without node_actions (no plan yet / assembly failed) resets nodeActions to null", () => {
    useChatStore.setState({
      nodeActions: {
        R001: { chips: [], alternatives: [] },
      } as any,
    });

    handleWsMessage(set, get, {
      type: "room_state",
      owner_id: "owner1",
      members: [],
      constraints: [],
      votes: {},
      locked_stages: [],
      itinerary: null,
      previous_itinerary: null,
      intent: null,
      planning_events: [],
      chat_messages: [],
      chat_state: null,
      planning_active: false,
      demand_ledger: [],
    });

    expect(useChatStore.getState().nodeActions).toBeNull();
  });
});
