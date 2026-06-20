/**
 * store 内部类型定义（不对外导出，外部组件直接用 lib/types.ts 的 SSE schema）。
 *
 * 抽出原因（spec code-modularization-refactor H4）：
 * 让 store.ts 主文件聚焦在 zustand action 实现，类型定义独立可读。
 */

import type {
  ChitchatReplyPayload,
  Itinerary,
  IntentExtraction,
  PlannerMode,
  Persona,
  Scenario,
  UserPreferenceView,
} from "../types";

export type ChatRole = "user" | "agent";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  createdAt: number;
}

/** Tool 调用一次的可视化记录（驱动「调用链路」面板）。 */
export interface ToolCallRecord {
  id: string; // 唯一：tool + 起始 seq
  tool: string;
  input: Record<string, unknown>;
  startedAtSeq: number;
  /** 到达顺序（跨 stream 与 confirm 流递增，用于稳定排序）。 */
  arrivalIdx: number;
  endedAtSeq?: number;
  durationMs?: number;
  success?: boolean;
  reason?: string | null;
  output?: Record<string, unknown>;
  // 是否在异常重规划之前 → UI 上灰显
  replanned?: boolean;
  /** spec innovation-review R1：fan-out 并行组 ID，同 group_id 表示并发执行；
   * 前端可基于此横向并列展示，让评委看到「并发」而非「按完成顺序串行到达的伪串行」。 */
  groupId?: string | null;
  /** 是否并发执行（与 groupId 一起用，便于 UI 区分串行 / 并行）。 */
  parallel?: boolean;
}

export interface ReplanRecord {
  seq: number;
  arrivalIdx: number;
  reason: string;
  fromTool: string;
}

/** Toast 通知项（短暂显示后自动消失）。 */
export interface ToastItem {
  id: string;
  kind: "info" | "success" | "warn";
  text: string;
}

/** 上一次 refine 的结果（驱动「Agent 已为你调整」面板）。 */
export interface RefinementSummary {
  feedbackText: string;
  changedFields: string[];
  refinerNote?: string | null;
  /** 服务端 timestamp_ms（来自 refinement_done 事件）。 */
  timestampMs?: number;
}

/** Phase 0.8：暖心回话气泡（chitchat / meta / emotional / off_topic / ambiguous）。 */
export interface ChitchatReplyRecord {
  id: string;
  payload: ChitchatReplyPayload;
  receivedAtMs: number;
}

export interface ChatState {
  // 会话
  sessionId: string;
  scenarios: Scenario[];
  scenariosLoaded: boolean;

  // 规划模式
  plannerMode: PlannerMode;

  // Phase 0.7：用户身份
  currentUserId: string | null;
  personas: Persona[];
  personasLoaded: boolean;
  preferences: UserPreferenceView | null;

  // 流式状态
  streaming: boolean;
  streamError: string | null;
  /**
   * 当前流式阶段：让 UI 知道这是首次规划 / 用户确认 / 反馈重规划。
   * - "idle"     : 没有进行中的流
   * - "stream"   : 首次规划（点 S1-S8 / 输入框 / Cmd+K 触发的 /chat/stream）
   * - "confirm"  : 用户点「确认并预约」触发的 /chat/confirm（接续之前的链路，
   *               不应让 ToolTracePanel / ItineraryCard 重置成「从头流式显示」状态）
   * - "refine"   : 用户点「说说哪不对」触发的 /chat/refine
   */
  streamPhase: "idle" | "stream" | "confirm" | "refine";
  /**
   * 惰性清空闸（Bug 修复：非重规划输入不该清空主页面）。
   * sendMessage 时设 true 但**不清空**；只有收到「重跑信号」(intent_parsed /
   * refinement_start) 时，若仍为 true 才清空 toolCalls/thoughts/itinerary 并置 false。
   * 提问 / 确认 / 预约 / 闲聊（只发 chitchat_reply）永远收不到重跑信号 → 主页面纹丝不动。
   */
  awaitingReplan: boolean;

  // 聊天与中间过程
  messages: ChatMessage[];
  intent: IntentExtraction | null;
  toolCalls: ToolCallRecord[];
  replans: ReplanRecord[];
  thoughts: { seq: number; text: string; timestamp_ms: number | null }[];

  // 输出
  itinerary: Itinerary | null;
  /**
   * 上一次的 itinerary 快照（refine/feedback 前保存）。
   * 用于「Refine 前后对比视图」（spec R3）。
   * - null 表示首次规划或会话重置后
   * - 非 null 表示上一次有方案，本次拿到新方案后可对比展示
   */
  previousItinerary: Itinerary | null;
  /** Agent 暖心开场白（行程出炉 / confirm 后由后端推送）。 */
  narration: { text: string; stage: "stream" | "confirm" } | null;
  /** 用户已主动取消（和 reset 不同：不清空 trace，仅冻结按钮）。 */
  cancelled: boolean;
  /** 上一次 refinement_done 摘要，用于「我已为你调整」面板。 */
  lastRefinement: RefinementSummary | null;
  /** Phase 0.8：本次会话内收到的所有暖心回话气泡（按时序追加，不清空）。 */
  chitchatReplies: ChitchatReplyRecord[];
  /** spec algorithm-redesign R5：narrate 末尾 memory_writer 副作用结果（前端可显示「已记住」标记）。 */
  memoryPersisted: {
    socialContext: string;
    summaryPreview: string;
    success: boolean;
    skippedReason: string | null;
  } | null;

  // UI 通知
  toasts: ToastItem[];

  // Cmd+K 命令面板
  commandPaletteOpen: boolean;

  // actions
  loadScenarios: () => Promise<void>;
  sendMessage: (input: string, scenarioId?: string) => Promise<void>;
  sendScenario: (input: string, scenarioId?: string) => Promise<void>;
  confirm: () => Promise<void>;
  refine: (feedbackText: string) => Promise<void>;
  cancel: () => void;
  reset: () => void;
  startNewSession: () => void;
  switchSession: (sessionId: string) => void;
  setPlannerMode: (mode: PlannerMode, options?: { silent?: boolean; persist?: boolean }) => void;
  setCurrentUserId: (userId: string, options?: { silent?: boolean }) => void;
  loadPersonas: () => Promise<void>;
  refreshPreferences: () => Promise<void>;
  resetUserMemory: () => Promise<void>;
  pushToast: (toast: Omit<ToastItem, "id">) => void;
  dismissToast: (id: string) => void;
  openCommandPalette: () => void;
  closeCommandPalette: () => void;
}

/** initialState 类型：剔除所有 action（保留纯数据字段）。 */
export type InitialChatState = Omit<
  ChatState,
  | "loadScenarios"
  | "sendMessage"
  | "sendScenario"
  | "confirm"
  | "refine"
  | "cancel"
  | "reset"
  | "startNewSession"
  | "switchSession"
  | "setPlannerMode"
  | "setCurrentUserId"
  | "loadPersonas"
  | "refreshPreferences"
  | "resetUserMemory"
  | "pushToast"
  | "dismissToast"
  | "openCommandPalette"
  | "closeCommandPalette"
>;

export type Setter = (
  partial:
    | Partial<ChatState>
    | ((state: ChatState) => Partial<ChatState>),
) => void;

export type Getter = () => ChatState;
