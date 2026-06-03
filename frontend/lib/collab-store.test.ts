import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildCollabChatStateSnapshot,
  buildCollabPlanningEvents,
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
});
