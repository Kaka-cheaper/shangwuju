/**
 * 初始 state（数据字段，无 action）—— 抽出便于 store.ts 主文件聚焦 action 实现。
 */

import { emptyCriticReport } from "./types";
import type { InitialChatState } from "./types";

export const initialState: InitialChatState = {
  // 服务端渲染期间用占位值；客户端 mount 后由 reset/loadScenarios 触发更新
  sessionId: "sess_pending",
  scenarios: [],
  scenariosLoaded: false,
  plannerMode: "rule",
  // Phase 0.7：默认 demo_user，客户端 mount 后由 cookie 改写
  currentUserId: "demo_user",
  personas: [],
  personasLoaded: false,
  preferences: null,
  streaming: false,
  streamError: null,
  streamPhase: "idle",
  awaitingReplan: false,
  messages: [],
  intent: null,
  toolCalls: [],
  replans: [],
  thoughts: [],
  criticReport: emptyCriticReport(),
  itinerary: null,
  previousItinerary: null,
  cancelled: false,
  lastRefinement: null,
  chitchatReplies: [],
  toasts: [],
  narration: null,
  narrationMessages: null,
  swapAlternativesCount: null,
  memoryPersisted: null,
  nodeActions: null,
  nodeDetail: null,
  demandLedger: null,
  lockedNodeId: null,
};
