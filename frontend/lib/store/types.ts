/**
 * store 内部类型定义（不对外导出，外部组件直接用 lib/types.ts 的 SSE schema）。
 *
 * 抽出原因（spec code-modularization-refactor H4）：
 * 让 store.ts 主文件聚焦在 zustand action 实现，类型定义独立可读。
 */

import type {
  AdjustAction,
  AgentNarrationMessage,
  ChitchatReplyPayload,
  CriticViolation,
  DemandLedgerEntry,
  Itinerary,
  IntentExtraction,
  NodeActionsMap,
  NodeDetailMap,
  PlanFallbackStage,
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

/**
 * Step 2：critic 校验 + 自愈闭环三事件（critic_violations / critic_fix_attempt /
 * plan_fallback）落地——路演「系统自愈过程可视化」。
 *
 * 三个子数组各自按到达顺序独立追加，同 toolCalls/replans/thoughts 的既有模式
 * （store 只存原始记录，不做归并；面板层按 arrivalIdx 合并渲染一条时间线）。
 *
 * 生命周期：PER-TURN 数据（同 toolCalls/thoughts，随「清空重跑腾位」的既有清空
 * 手法一起清空）——不是 SESSION_SCOPED（不同于 demandLedger 跨规划事件存活）。
 */
export interface CriticViolationRound {
  seq: number;
  arrivalIdx: number;
  /** 被判定违规的是第几次出的蓝图（emit_critic 读 ctx.last_plan_attempt）。 */
  fixAttempt: number;
  violations: CriticViolation[];
}

export interface CriticFixAttemptRecord {
  seq: number;
  arrivalIdx: number;
  /** 即将展开的这次重写是第几稿（LangGraph planner 节点的 plan_attempt）。 */
  attempt: number;
  /** 后端占位文案（常为「详见上一条 critic_violations」），不作为用户可读文案
   * 直接展示——面板自己写人话（见 ThoughtPanel），此字段仅保留供调试 / 未来消费。 */
  feedbackText: string;
}

export interface PlanFallbackHopRecord {
  seq: number;
  arrivalIdx: number;
  from: PlanFallbackStage;
  to: PlanFallbackStage;
  reason: string;
}

export interface CriticReport {
  violationRounds: CriticViolationRound[];
  fixAttempts: CriticFixAttemptRecord[];
  fallbackHops: PlanFallbackHopRecord[];
}

/** criticReport 的清空态——initial-state / 既有清空手法（clearForReplanIfPending
 * / refine() / confirm() onDone）统一复用，避免任一处漏写字段导致上一轮的
 * 违规记录串场到新一轮。每次调用返回新对象（不共享引用，虽然当前均只读替换）。 */
export function emptyCriticReport(): CriticReport {
  return { violationRounds: [], fixAttempts: [], fallbackHops: [] };
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
   * - "stream"   : 首次规划（点 S1-S8 / 输入框触发的 /chat/turn）
   * - "confirm"  : 用户点「确认并预约」触发的 /chat/confirm（接续之前的链路，
   *               不应让 ToolTracePanel / ItineraryCard 重置成「从头流式显示」状态）
   * - "refine"   : 用户点「说说哪不对」——反馈同样发 /chat/turn，由后端统一
   *               路由判为 feedback 走 refiner（V1 /chat/refine 端点已退役）
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
  thoughts: {
    seq: number;
    text: string;
    timestamp_ms: number | null;
    /** 信任带③拍：见 AgentThoughtPayload.plan_reason 字段注释。 */
    planReason?: string | null;
    /** 信任带⑦拍质检收据：见 AgentThoughtPayload.checks_run 字段注释。 */
    checksRun?: number | null;
  }[];
  /** Step 2：critic 校验 + 自愈闭环（critic_violations/critic_fix_attempt/
   * plan_fallback 三事件落地）——驱动 ThoughtPanel「质检与自愈」小节。 */
  criticReport: CriticReport;

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
  /** 换菜备选收据（2026-07-11）：见 AgentNarrationPayload.swap_alternatives_count
   * 字段注释。绑定"这一版叙事"，同 narrationMessages 的清空语义——每次
   * agent_narration 事件整体替换，缺省清成 null（不是保留上一次换菜的数字）。 */
  swapAlternativesCount: number | null;
  /** D-7（ADR-0010 决策 11 / ADR-0011 决策 5「统一 agent 消息面」）：
   * agent_narration payload 的 messages 兄弟字段——narrate 文字里被限额折叠的
   * 完整结构化告知列表（"还有 N 处小取舍"的"点开看全部"落点）。
   * 生命周期绑定"这一版叙事"，同 narration 本身：每次 agent_narration 事件
   * 整体替换（缺省时清成 null，不是像 nodeActions/demandLedger 那样保留上一
   * 版）——因为 messages 是这一版 narration.text 的详情展开，text 换了而
   * messages 还留着上一版会牛头不对马嘴。 */
  narrationMessages: AgentNarrationMessage[] | null;
  /** ADR-0013 F-3/F-4：节点行的「调整按钮 + 具名备选」，随每次
   * itinerary_ready/换菜成功的 agent_narration 整体刷新（"无内容不加字段"，
   * 缺省时保留上一版直到下一次真正刷新——同 narration 字段的持久语义）。 */
  nodeActions: NodeActionsMap | null;
  /** 卡片主角化与事实面板设计终稿§三：节点「真实数据详情」（评分/价钱/距离/
   * 可订余位/标签/营业），随每次 itinerary_ready/换菜成功的 agent_narration
   * 整体刷新——同 nodeActions 完全同一套生命周期语义（"无内容不加字段"，
   * 缺省时保留上一版直到下一次真正刷新；重跑清空时机也与 nodeActions 一致，
   * 见 event-handlers.ts::clearForReplanIfPending）。 */
  nodeDetail: NodeDetailMap | null;
  /** ADR-0013 F-4：诉求台账展示投影（单人 ConstraintFeed 面板消费）。 */
  demandLedger: DemandLedgerEntry[] | null;
  /** ADR-0013 F-4：正在处理换菜请求的节点 id（ActivityNode.target_id）；
   * 非 null 时该节点整行 Shimmer + 按钮禁用，done/error 时解锁。 */
  lockedNodeId: string | null;
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

  // actions
  loadScenarios: () => Promise<void>;
  sendMessage: (input: string, scenarioId?: string) => Promise<void>;
  sendScenario: (input: string, scenarioId?: string) => Promise<void>;
  confirm: () => Promise<void>;
  refine: (feedbackText: string) => Promise<void>;
  /** ADR-0013 F-4：节点行调整入口——POST /chat/adjust，期间 lockedNodeId=nodeId。 */
  sendAdjust: (nodeId: string, action: AdjustAction) => Promise<void>;
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
}

/** initialState 类型：剔除所有 action（保留纯数据字段）。 */
export type InitialChatState = Omit<
  ChatState,
  | "loadScenarios"
  | "sendMessage"
  | "sendScenario"
  | "confirm"
  | "refine"
  | "sendAdjust"
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
>;

export type Setter = (
  partial:
    | Partial<ChatState>
    | ((state: ChatState) => Partial<ChatState>),
) => void;

export type Getter = () => ChatState;
