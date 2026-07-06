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
import { useChatStore, type ChatState } from "./store";
import { nextArrival, resetArrival } from "./store/arrival-counter";
import { handleEvent } from "./store/event-handlers";
import { emptyCriticReport } from "./store/types";
import type { AdjustAction, DemandLedgerEntry, NodeActionsMap, NodeDetailMap, SseEvent } from "./types";
import { API_BASE } from "./utils";

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
  /** ADR-0013 F-5：房间版节点调整入口——WS "adjust" 消息，同 F-4 单人
   * `sendAdjust` 的 action 判别式协议（见 `frontend/components/ItineraryCard.tsx`
   * 的 collabMode 分流：房间模式发这个而非 HTTP `/chat/adjust`）。 */
  sendAdjust: (nodeId: string, action: AdjustAction) => void;
  sendConfirm: () => void;
  createRoom: (
    userId: string,
    nickname: string,
    sessionId?: string,
    planningEvents?: Record<string, unknown>[],
    chatMessages?: Record<string, unknown>[],
    chatState?: CollabChatStateSnapshot,
  ) => Promise<string | null>;
}

export type CollabChatStateSnapshot = Partial<
  Pick<
    ChatState,
    | "streaming"
    | "streamError"
    | "streamPhase"
    | "messages"
    | "intent"
    | "toolCalls"
    | "replans"
    | "thoughts"
    | "itinerary"
    | "previousItinerary"
    | "narration"
    | "narrationMessages"
    | "cancelled"
    | "lastRefinement"
    | "chitchatReplies"
    | "memoryPersisted"
  >
>;

const initialCollabState: Omit<
  CollabState,
  "joinRoom" | "leaveRoom" | "sendConstraint" | "sendVote" | "sendAdjust" | "sendConfirm" | "createRoom"
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

  sendAdjust: (nodeId, action) => {
    const client = get()._wsClient;
    if (client) {
      client.send({ type: "adjust", node_id: nodeId, action });
    }
  },

  sendConfirm: () => {
    const client = get()._wsClient;
    if (get().myRole !== "owner") {
      set({ connectionError: "只有发起人可以确认预约" });
      return;
    }
    if (client) {
      client.send({ type: "confirm" });
    }
  },

  createRoom: async (userId, nickname, sessionId?, planningEvents?, chatMessages?, chatState?) => {
    try {
      const resp = await fetch(`${API_BASE}/room/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          nickname,
          session_id: sessionId || null,
          planning_events: planningEvents || null,
          chat_messages: chatMessages || null,
          chat_state: chatState || null,
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

// 导出供测试直驱（同 buildCollabPlanningEvents/buildCollabChatStateSnapshot 的既有
// 测试性导出先例）——WS 层构造真实 `WebSocket` 在 vitest/node 环境下不可用，
// 单测改为直接调用本函数模拟收到的下行消息。
export function handleWsMessage(set: Setter, get: Getter, msg: WsMessage): void {
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
      const chatState = msg.chat_state as CollabChatStateSnapshot | null;
      if (chatState) {
        hydrateChatStateSnapshot(chatState);
      } else {
        resetArrival();
        useChatStore.setState({
          streaming: Boolean(msg.planning_active),
          streamError: null,
          streamPhase: Boolean(msg.planning_active) ? "stream" : "idle",
          toolCalls: [],
          replans: [],
          thoughts: [],
          // Step 2：下面紧接着会把 planning_events 从头回放一遍（含
          // critic_violations/critic_fix_attempt/plan_fallback）——不清空
          // criticReport 会导致本地残留的上一轮自愈记录被回放内容重复追加。
          criticReport: emptyCriticReport(),
          itinerary: (msg.itinerary as any) || null,
          previousItinerary: (msg.previous_itinerary as any) || null,
          intent: (msg.intent as any) || null,
          narration: null,
          // narrationMessages 绑定 narration 这一版（见 store/types.ts）——没有
          // chat_state 快照可回放时，同 narration 一起清空，不留上个会话的
          // "点开看全部"列表串场到新加入者看到的这个空白房间态。
          narrationMessages: null,
          lastRefinement: null,
          chitchatReplies: [],
          memoryPersisted: null,
        });
      }
      // 回放规划事件历史（让新加入者看到 ToolTracePanel）
      const events = (msg.planning_events as SseEvent[]) || [];
      if (events.length > 0 && !chatState) {
        for (const event of events) {
          dispatchPlanningEvent(event);
          if (event.type === "done") {
            finishCollabStream();
          }
        }
      }
      // 同步对话历史（让新加入者看到 ChatPanel）
      const chatMsgs = (msg.chat_messages as any[]) || [];
      if (chatMsgs.length > 0) {
        useChatStore.setState({ messages: chatMsgs });
      }
      // ADR-0013 F-5：诉求台账数据源永远是房间快照本身（room.demand_ledger 的
      // `ledger_for_display` 投影）——房间是"谁提的"归名记录的唯一真相源，不随
      // `chat_state` 是否存在而分支（`chat_state` 只是单人转房间时的一次性前端
      // 本地态迁移载体，不携带房间侧台账）。喂给 `ConstraintFeed`（读现状对齐：
      // 单人模式已经读 `useChatStore.demandLedger`，房间模式复用同一个字段）。
      useChatStore.setState({ demandLedger: (msg.demand_ledger as DemandLedgerEntry[]) || [] });
      // 评委体验修复（2026-07-03）：node_actions 数据源同上——房间快照本身
      // （`Room.get_state_snapshot()` 有方案时现算的顶层字段，见该方法
      // docstring"评委体验修复"节），不随 `chat_state` 是否存在分支。这治的
      // 正是"中途加入的成员看不到节点调整按钮"这个体验缺口——中途加入者走的
      // 正是这条 room_state 分支，此前无论 chat_state 有没有，这里都没把
      // node_actions 接进主 store，`ItineraryCard` 读 `useChatStore.
      // nodeActions` 自然永远是缺省的 null，直到别人换一次菜（下一次
      // agent_narration 事件）才补上。`CollabChatStateSnapshot` 的 Pick
      // 列表里也确实没有这个字段（同 demandLedger，chat_state 只是单人转
      // 房间时的一次性前端本地态迁移载体，不携带这两个房间侧字段）。
      // 卡片主角化与事实面板设计终稿§三 / 四条路径全覆盖：node_detail 同
      // node_actions 完全同一个数据源的口径——房间快照本身现算的顶层字段
      // （Room.get_state_snapshot()::_snapshot_node_detail），不随 chat_state
      // 是否存在分支。这治的是同一个体验缺口："中途加入的成员看不到事实面板"，
      // 直到别人换一次菜（下一次 agent_narration 事件）才补上。
      useChatStore.setState({
        nodeActions: (msg.node_actions as NodeActionsMap) || null,
        nodeDetail: (msg.node_detail as NodeDetailMap) || null,
      });
      break;
    }

    case "member_joined": {
      const uid = msg.user_id as string;
      set((s) => {
        if (s.members.some((m) => m.user_id === uid)) return {};
        return {
          members: [
            ...s.members,
            {
              user_id: uid,
              nickname: msg.nickname as string,
              role: (msg.role as "owner" | "participant") || "participant",
              online: true,
            },
          ],
        };
      });
      break;
    }

    case "member_reconnected": {
      // 区别于 member_joined（新增一行）——重连是"老朋友回来了"，更新既有行的
      // online/nickname，不追加新行（见 collab/room.py::RoomManager.join
      // docstring："重连刷屏"曾是真实的列表重复 bug）。
      const uid = msg.user_id as string;
      set((s) => ({
        members: s.members.map((m) =>
          m.user_id === uid
            ? { ...m, online: true, nickname: (msg.nickname as string) ?? m.nickname }
            : m,
        ),
      }));
      break;
    }

    case "node_locked": {
      // ADR-0013 F-5：房间版换菜处理期锁定——桥接到主 store 的 lockedNodeId
      // （F-4 单人换菜同一个字段），ItineraryCard 的 Shimmer/禁用逻辑零改动
      // 即可复用（房间模式下该字段由这里驱动，单人模式由 sendAdjust 自己驱动）。
      useChatStore.setState({ lockedNodeId: msg.node_id as string });
      break;
    }

    case "node_unlocked": {
      useChatStore.setState({ lockedNodeId: null });
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
      const trigger = msg.trigger as string;
      set({
        planningActive: true,
        planningTrigger: trigger,
      });
      if (trigger === "confirm") {
        useChatStore.setState({
          streaming: true,
          streamError: null,
          streamPhase: "confirm",
        });
      } else {
        const currentItinerary = useChatStore.getState().itinerary;
        resetArrival();
        // 清空主 store 的中间过程（新一轮规划开始），同时保留旧方案快照供对比视图使用
        useChatStore.setState({
          toolCalls: [],
          replans: [],
          thoughts: [],
          // Step 2：同 toolCalls/thoughts——新一轮规划开始，上一轮的质检自愈记录
          // 不清会串场到这一轮的「质检与自愈」小节里（同 store.ts refine() 的
          // 既有清空手法，房间模式这里是它的对应版本）。
          criticReport: emptyCriticReport(),
          itinerary: null,
          previousItinerary: currentItinerary ? cloneForCollab(currentItinerary) : null,
          narration: null,
          // 同 store.ts refine() 的既有清空手法——新一轮规划开始，上一轮
          // narration 的展开列表不该挂在这一轮还没产出内容的 narration 上。
          narrationMessages: null,
          streaming: true,
          streamError: null,
          streamPhase: "refine",
          cancelled: false,
          lastRefinement: null,
          memoryPersisted: null,
        });
      }
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
          finishCollabStream();
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
 * 直接复用主 store 的 SSE 分发，避免协作通道遗漏新事件类型或 payload 字段。
 */
function dispatchPlanningEvent(event: SseEvent): void {
  handleEvent(
    useChatStore.setState as any,
    useChatStore.getState as any,
    event,
  );
}

function finishCollabStream(): void {
  const state = useChatStore.getState();
  const text =
    state.narration?.text ||
    (state.itinerary ? `已为你规划：${state.itinerary.summary}` : "");

  if (text) {
    useChatStore.setState((s) => ({
      messages: [
        ...s.messages,
        {
          id: `a-${Date.now()}`,
          role: "agent",
          text: state.streamPhase === "refine" ? `已根据反馈重新规划——${text}` : text,
          createdAt: Date.now(),
        },
      ],
    }));
  }

  useChatStore.setState({
    streaming: false,
    streamPhase: "idle",
  });
}

function hydrateChatStateSnapshot(snapshot: CollabChatStateSnapshot): void {
  resetArrival();
  useChatStore.setState({
    streaming: Boolean(snapshot.streaming),
    streamError: snapshot.streamError ?? null,
    streamPhase: snapshot.streamPhase ?? "idle",
    messages: snapshot.messages ?? [],
    intent: snapshot.intent ?? null,
    toolCalls: snapshot.toolCalls ?? [],
    replans: snapshot.replans ?? [],
    thoughts: snapshot.thoughts ?? [],
    itinerary: snapshot.itinerary ?? null,
    previousItinerary: snapshot.previousItinerary ?? null,
    narration: snapshot.narration ?? null,
    narrationMessages: snapshot.narrationMessages ?? null,
    cancelled: Boolean(snapshot.cancelled),
    lastRefinement: snapshot.lastRefinement ?? null,
    chitchatReplies: snapshot.chitchatReplies ?? [],
    memoryPersisted: snapshot.memoryPersisted ?? null,
  });
  primeArrivalCounter(snapshot);
}

export function buildCollabChatStateSnapshot(state: ChatState): CollabChatStateSnapshot {
  return cloneForCollab({
    streaming: false,
    streamError: state.streamError,
    streamPhase: "idle" as const,
    messages: state.messages,
    intent: state.intent,
    toolCalls: state.toolCalls,
    replans: state.replans,
    thoughts: state.thoughts,
    itinerary: state.itinerary,
    previousItinerary: state.previousItinerary,
    narration: state.narration,
    narrationMessages: state.narrationMessages,
    cancelled: state.cancelled,
    lastRefinement: state.lastRefinement,
    chitchatReplies: state.chitchatReplies,
    memoryPersisted: state.memoryPersisted,
  });
}

export function buildCollabPlanningEvents(state: ChatState): Record<string, unknown>[] {
  const events: Record<string, unknown>[] = [];
  const now = Date.now();
  let maxSeq = 0;

  if (state.intent) {
    events.push({ type: "intent_parsed", seq: 0, payload: state.intent, timestamp_ms: now });
  }

  for (const tc of state.toolCalls) {
    maxSeq = Math.max(maxSeq, tc.startedAtSeq, tc.endedAtSeq ?? 0);
    events.push({
      type: "tool_call_start",
      seq: tc.startedAtSeq,
      payload: {
        tool: tc.tool,
        input: tc.input,
        group_id: tc.groupId ?? null,
        parallel: tc.parallel ?? false,
      },
      timestamp_ms: now,
    });
    if (tc.endedAtSeq != null) {
      events.push({
        type: "tool_call_end",
        seq: tc.endedAtSeq,
        payload: {
          tool: tc.tool,
          output: tc.output || {},
          duration_ms: tc.durationMs || 0,
          group_id: tc.groupId ?? null,
          parallel: tc.parallel ?? false,
        },
        timestamp_ms: now,
      });
    }
  }

  for (const rp of state.replans) {
    maxSeq = Math.max(maxSeq, rp.seq);
    events.push({
      type: "replan_triggered",
      seq: rp.seq,
      payload: { reason: rp.reason, from_tool: rp.fromTool },
      timestamp_ms: now,
    });
  }

  for (const th of state.thoughts) {
    maxSeq = Math.max(maxSeq, th.seq);
    events.push({
      type: "agent_thought",
      seq: th.seq,
      payload: { text: th.text },
      timestamp_ms: th.timestamp_ms ?? now,
    });
  }

  if (state.lastRefinement) {
    maxSeq += 1;
    events.push({
      type: "refinement_done",
      seq: maxSeq,
      payload: {
        refined_intent: state.intent,
        changed_fields: state.lastRefinement.changedFields,
        refiner_note: state.lastRefinement.refinerNote ?? null,
      },
      timestamp_ms: state.lastRefinement.timestampMs ?? now,
    });
  }

  if (state.itinerary) {
    maxSeq += 1;
    events.push({ type: "itinerary_ready", seq: maxSeq, payload: state.itinerary, timestamp_ms: now });
  }

  if (state.narration) {
    maxSeq += 1;
    // narrationMessages 是 narration.text 这一版折叠内容的展开详情（D-7），
    // 不是 agent_narration payload 自带字段——重建回放事件时要拼回去，否则
    // 新加入者回放到的这条 narration 会丢失"点开看全部"的数据源。
    const payload =
      state.narrationMessages && state.narrationMessages.length > 0
        ? { ...state.narration, messages: state.narrationMessages }
        : state.narration;
    events.push({ type: "agent_narration", seq: maxSeq, payload, timestamp_ms: now });
  }

  if (state.memoryPersisted) {
    maxSeq += 1;
    events.push({
      type: "memory_persisted",
      seq: maxSeq,
      payload: {
        social_context: state.memoryPersisted.socialContext,
        summary_preview: state.memoryPersisted.summaryPreview,
        success: state.memoryPersisted.success,
        skipped_reason: state.memoryPersisted.skippedReason,
      },
      timestamp_ms: now,
    });
  }

  return cloneForCollab(events).sort((a, b) => Number(a.seq) - Number(b.seq));
}

function cloneForCollab<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function primeArrivalCounter(snapshot: CollabChatStateSnapshot): void {
  const maxArrival = Math.max(
    -1,
    ...(snapshot.toolCalls ?? []).map((tc) => tc.arrivalIdx),
    ...(snapshot.replans ?? []).map((rp) => rp.arrivalIdx),
  );
  for (let i = 0; i <= maxArrival; i += 1) {
    nextArrival();
  }
}
