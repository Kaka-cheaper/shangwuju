/**
 * 协作状态 Zustand store：房间成员 / 约束池 / 投票 / WS 连接管理。
 *
 * 与主 store (lib/store.ts) 的关系：
 * - collab-store 管理"房间级"状态（成员、约束、投票、WS 连接）
 * - 主 store 管理"规划级"状态（intent、toolCalls、itinerary）
 * - WS 下行的 planning_event 会被转发给主 store 的 handleEvent
 * - 两个 store 通过 collabMode 标志协调
 */

import { create } from "zustand";
import { createWsClient, type WsClient, type WsMessage } from "./ws";
import { useChatStore } from "./store";
import type { SseEvent } from "./types";

// ============================================================
// 类型
// ============================================================

export interface CollabMember {
  user_id: string;
  nickname: string;
  role: "owner" | "participant";
  online: boolean;
}

export interface CollabConstraint {
  user_id: string;
  nickname?: string;
  text: string;
  source: "text" | "vote_dislike";
  timestamp: number;
}

export type VoteAction = "like" | "dislike";

export interface CollabState {
  // 房间状态
  collabMode: boolean;
  roomId: string | null;
  ownerId: string | null;
  myUserId: string | null;
  myRole: "owner" | "participant" | null;
  members: CollabMember[];
  constraints: CollabConstraint[];
  votes: Record<number, Record<string, VoteAction>>; // stageIndex → {userId: action}
  lockedStages: number[];

  // 连接状态
  connected: boolean;
  connectionError: string | null;
  planningActive: boolean;
  planningTrigger: string | null;

  // WS 客户端引用
  _wsClient: WsClient | null;

  // Actions
  joinRoom: (roomId: string, userId: string, nickname: string) => void;
  leaveRoom: () => void;
  sendConstraint: (text: string) => void;
  sendVote: (stageIndex: number, action: VoteAction) => void;
  sendConfirm: () => void;
  createRoom: (userId: string, nickname: string, sessionId?: string, planningEvents?: Record<string, unknown>[], chatMessages?: Record<string, unknown>[]) => Promise<string | null>;
}

const initialCollabState: Omit<
  CollabState,
  "joinRoom" | "leaveRoom" | "sendConstraint" | "sendVote" | "sendConfirm" | "createRoom"
> = {
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
};

// ============================================================
// Store
// ============================================================

export const useCollabStore = create<CollabState>((set, get) => ({
  ...initialCollabState,

  joinRoom: (roomId, userId, nickname) => {
    // 如果已连接，先断开
    const existing = get()._wsClient;
    if (existing) {
      existing.close();
    }

    const client = createWsClient({
      roomId,
      userId,
      nickname,
      onMessage: (msg) => handleWsMessage(set, get, msg),
      onOpen: () => set({ connected: true, connectionError: null }),
      onClose: (reason) => set({ connected: false, connectionError: reason }),
      onError: (err) => set({ connectionError: err }),
    });

    set({
      collabMode: true,
      roomId,
      myUserId: userId,
      _wsClient: client,
    });
  },

  leaveRoom: () => {
    const client = get()._wsClient;
    if (client) {
      client.close();
    }
    set({ ...initialCollabState });
  },

  sendConstraint: (text) => {
    const client = get()._wsClient;
    if (client && text.trim()) {
      client.send({ type: "constraint", text: text.trim() });
    }
  },

  sendVote: (stageIndex, action) => {
    const client = get()._wsClient;
    if (client) {
      client.send({ type: "vote", stage_index: stageIndex, action });
    }
  },

  sendConfirm: () => {
    const client = get()._wsClient;
    if (client) {
      client.send({ type: "confirm" });
    }
  },

  createRoom: async (userId, nickname, sessionId?, planningEvents?, chatMessages?) => {
    try {
      const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
      const resp = await fetch(`${API_BASE}/room/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          nickname,
          session_id: sessionId || null,
          planning_events: planningEvents || null,
          chat_messages: chatMessages || null,
        }),
      });
      if (!resp.ok) return null;
      const data = await resp.json();
      return data.room_id as string;
    } catch {
      return null;
    }
  },
}));

// ============================================================
// WS 消息处理
// ============================================================

type Setter = (partial: Partial<CollabState> | ((s: CollabState) => Partial<CollabState>)) => void;
type Getter = () => CollabState;

function handleWsMessage(set: Setter, get: Getter, msg: WsMessage): void {
  const type = msg.type;

  switch (type) {
    case "room_state": {
      const members = (msg.members as CollabMember[]) || [];
      const myUserId = get().myUserId;
      const myMember = members.find((m) => m.user_id === myUserId);
      set({
        ownerId: msg.owner_id as string,
        members,
        constraints: (msg.constraints as CollabConstraint[]) || [],
        votes: (msg.votes as Record<number, Record<string, VoteAction>>) || {},
        lockedStages: (msg.locked_stages as number[]) || [],
        myRole: myMember?.role || "participant",
      });
      // 如果有行程，同步到主 store
      if (msg.itinerary) {
        useChatStore.setState({ itinerary: msg.itinerary as any });
      }
      if (msg.intent) {
        useChatStore.setState({ intent: msg.intent as any });
      }
      // 回放规划事件历史（让新加入者看到 ToolTracePanel）
      const events = (msg.planning_events as SseEvent[]) || [];
      if (events.length > 0) {
        // 清空主 store 的中间过程再回放
        useChatStore.setState({ toolCalls: [], replans: [], thoughts: [] });
        for (const event of events) {
          dispatchPlanningEvent(event);
        }
      }
      // 同步对话历史（让新加入者看到 ChatPanel）
      const chatMsgs = (msg.chat_messages as any[]) || [];
      if (chatMsgs.length > 0) {
        useChatStore.setState({ messages: chatMsgs });
      }
      break;
    }

    case "member_joined": {
      set((s) => ({
        members: [
          ...s.members,
          {
            user_id: msg.user_id as string,
            nickname: msg.nickname as string,
            role: (msg.role as "owner" | "participant") || "participant",
            online: true,
          },
        ],
      }));
      break;
    }

    case "member_left": {
      set((s) => ({
        members: s.members.map((m) =>
          m.user_id === msg.user_id ? { ...m, online: false } : m
        ),
      }));
      break;
    }

    case "constraint_added": {
      const constraintUserId = msg.user_id as string;
      const constraintNickname = msg.nickname as string;
      const constraintText = msg.text as string;
      set((s) => ({
        constraints: [
          ...s.constraints,
          {
            user_id: constraintUserId,
            nickname: constraintNickname,
            text: constraintText,
            source: (msg.source as "text" | "vote_dislike") || "text",
            timestamp: (msg.timestamp as number) || Date.now() / 1000,
          },
        ],
      }));
      // 同步到主 store 的 messages（让所有窗口的 ChatPanel 显示这条约束）
      // 只有非自己发的才追加（自己发的在 ChatDock.submit 里已经追加了）
      const myId = get().myUserId;
      if (constraintUserId !== myId) {
        useChatStore.setState((s: any) => ({
          messages: [
            ...s.messages,
            {
              id: `collab-${Date.now()}`,
              role: "user",
              text: `${constraintNickname}：${constraintText}`,
              createdAt: Date.now(),
            },
          ],
        }));
      }
      break;
    }

    case "vote_updated": {
      const stageIndex = msg.stage_index as number;
      const votes = msg.votes as Record<string, VoteAction>;
      const lockedStages = (msg.locked_stages as number[]) || [];
      set((s) => ({
        votes: { ...s.votes, [stageIndex]: votes },
        lockedStages,
      }));
      break;
    }

    case "planning_started": {
      set({
        planningActive: true,
        planningTrigger: msg.trigger as string,
      });
      // 清空主 store 的中间过程（新一轮规划开始）
      useChatStore.setState({
        toolCalls: [],
        replans: [],
        thoughts: [],
        itinerary: null,
        narration: null,
        streaming: true,
      });
      break;
    }

    case "planning_aborted": {
      set({ planningActive: false, planningTrigger: null });
      break;
    }

    case "planning_event": {
      // 把内部 event 转发给主 store 的 handleEvent 逻辑
      const event = msg.event as SseEvent;
      if (event) {
        // 直接触发主 store 的事件处理
        dispatchPlanningEvent(event);
        // 如果是 done，标记规划结束
        if (event.type === "done") {
          set({ planningActive: false });
          useChatStore.setState({ streaming: false });
        }
      }
      break;
    }

    case "confirmed": {
      // 确认结果同步
      if (msg.itinerary) {
        useChatStore.setState({ itinerary: msg.itinerary as any });
      }
      break;
    }

    case "error": {
      // 服务端错误消息
      set({ connectionError: msg.message as string });
      break;
    }
  }
}

/**
 * 把 WS 下行的 planning_event 转发给主 store 的事件处理逻辑。
 * 复用 store.ts 里的 handleEvent 函数（通过直接操作 useChatStore.setState）。
 */
function dispatchPlanningEvent(event: SseEvent): void {
  const store = useChatStore;
  const state = store.getState();

  switch (event.type) {
    case "intent_parsed":
      store.setState({ intent: event.payload as any });
      break;

    case "tool_call_start": {
      const p = event.payload as any;
      const toolCalls = state.toolCalls;
      const arrivalIdx = toolCalls.length;
      store.setState({
        toolCalls: [
          ...toolCalls,
          {
            id: `${p.tool}-${arrivalIdx}`,
            tool: p.tool,
            input: p.input || {},
            startedAtSeq: event.seq,
            arrivalIdx,
          },
        ],
      });
      break;
    }

    case "tool_call_end": {
      const p = event.payload as any;
      const arr = [...state.toolCalls];
      for (let i = arr.length - 1; i >= 0; i--) {
        if (arr[i].tool === p.tool && arr[i].endedAtSeq == null) {
          arr[i] = {
            ...arr[i],
            endedAtSeq: event.seq,
            durationMs: p.duration_ms,
            success: p.output?.success,
            reason: p.output?.reason ?? null,
            output: p.output,
          };
          break;
        }
      }
      store.setState({ toolCalls: arr });
      break;
    }

    case "replan_triggered": {
      const p = event.payload as any;
      store.setState((s: any) => ({
        replans: [
          ...s.replans,
          { seq: event.seq, arrivalIdx: s.replans.length, reason: p.reason, fromTool: p.from_tool },
        ],
      }));
      break;
    }

    case "agent_thought": {
      const p = event.payload as any;
      store.setState((s: any) => ({
        thoughts: [...s.thoughts, { seq: event.seq, text: p.text, user_text: p.user_text, timestamp_ms: event.timestamp_ms ?? null }],
      }));
      break;
    }

    case "itinerary_ready":
      store.setState({ itinerary: event.payload as any });
      break;

    case "agent_narration": {
      const p = event.payload as any;
      store.setState({ narration: { text: p.text, stage: p.stage } });
      break;
    }

    case "stream_error": {
      const p = event.payload as any;
      store.setState({ streamError: `${p.reason}: ${p.detail || ""}` });
      break;
    }
  }
}
