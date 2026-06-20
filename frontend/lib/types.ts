/**
 * 类型契约：手抄自 backend/schemas/{sse,itinerary,intent}.py
 *
 * 同步纪律（参见 backend/api_contract.md）：
 * - 后端字段变动 → 先改 schemas/，再改本文件，再 grep 组件代码。
 * - 不得发明字段；§5.7 IntentExtraction 是 D-SoT。
 */

// ============================================================
// SSE 事件（schemas/sse.py）
// ============================================================

export const SseEventType = {
  IntentParsed: "intent_parsed",
  ToolCallStart: "tool_call_start",
  ToolCallEnd: "tool_call_end",
  ReplanTriggered: "replan_triggered",
  // Step 2：critic 闭环明细
  CriticViolations: "critic_violations",
  CriticFixAttempt: "critic_fix_attempt",
  PlanFallback: "plan_fallback",
  AgentThought: "agent_thought",
  ItineraryReady: "itinerary_ready",
  // 用户反馈 → 重规划（Phase 0.6 /chat/refine 专用）
  RefinementStart: "refinement_start",
  RefinementDone: "refinement_done",
  // Phase 0.8 输入域路由（非 planning 输入的暖心回话气泡）
  ChitchatReply: "chitchat_reply",
  // 行程出炉时的 Agent 暖心开场白（替代套话 summary）
  AgentNarration: "agent_narration",
  // spec algorithm-redesign R5 收尾：memory 副作用结果（前端可显示「已记住」标记）
  MemoryPersisted: "memory_persisted",
  StreamError: "stream_error",
  Done: "done",
} as const;

export type SseEventType = (typeof SseEventType)[keyof typeof SseEventType];

export interface SseEvent<P = Record<string, unknown>> {
  type: SseEventType;
  seq: number;
  payload: P;
  timestamp_ms?: number | null;
}

// ===== payload 形态（与 sse.py docstring 对齐） =====

export interface ToolCallStartPayload {
  tool: string;
  input: Record<string, unknown>;
  /** spec innovation-review R1：fan-out 并行组 ID（前端识别同组 worker 并横向并列展示）。 */
  group_id?: string | null;
  /** 是否并发执行。 */
  parallel?: boolean;
}

export interface ToolCallEndPayload {
  tool: string;
  output: Record<string, unknown> & { success?: boolean; reason?: FailureReason };
  duration_ms?: number;
  /** spec innovation-review R1：fan-out 并行组 ID（与 ToolCallStartPayload 同源）。 */
  group_id?: string | null;
  parallel?: boolean;
}

export interface ReplanTriggeredPayload {
  reason: FailureReason;
  from_tool: string;
}

// ===== Step 2：critic 闭环明细（来自后端 LangGraph 主架构） =====

export type ViolationCode =
  | "duration_out_of_range"
  | "distance_exceeded"
  | "stages_incomplete"
  | "restaurant_full_unresolved"
  | "timeline_inconsistent"
  | "social_context_mismatch"
  | "dietary_violation"
  | "commute_infeasible";

export type ViolationSeverity = "critical" | "warning";

export interface CriticViolation {
  code: ViolationCode;
  severity: ViolationSeverity;
  message: string;
  field_path: string;
}

export interface CriticViolationsPayload {
  violations: CriticViolation[];
  fix_attempt: number;
}

export interface CriticFixAttemptPayload {
  attempt: number;
  feedback_text: string;
}

/** 4 级 fallback 链：每跳一级推一条 */
export type PlanFallbackStage =
  | "llm_first"
  | "llm_backprompt"
  | "ils"
  | "rule"
  | "error"
  | "give_up";

export interface PlanFallbackPayload {
  from: PlanFallbackStage;
  to: PlanFallbackStage;
  reason: string;
}

export interface AgentThoughtPayload {
  text: string;
}

export interface RefinementStartPayload {
  feedback_text: string;
}

export interface RefinementDonePayload {
  refined_intent: IntentExtraction;
  changed_fields: string[];
  refiner_note?: string | null;
}

// ============================================================
// 输入域路由（schemas/router.py，Phase 0.8）
// ============================================================

export type InputKind =
  | "planning"
  | "chitchat"
  | "meta"
  | "emotional"
  | "off_topic"
  | "ambiguous";

export type ReplyTone = "warm" | "neutral" | "empathetic" | "playful";

export interface CtaChip {
  label: string;
  send: string;
  icon?: string | null;
  /** 可选前端动作：默认发送 send 文案（走对话）；"confirm"=点击触发真预约 /chat/confirm。 */
  action?: string | null;
}

export interface RouterDecision {
  input_kind: InputKind;
  confidence: number;
  reply_text: string;
  tone: ReplyTone;
  cta_chips: CtaChip[];
  rationale?: string | null;
}

export type ChitchatReplyPayload = RouterDecision;

export interface AgentNarrationPayload {
  /** 暖心开场白文案（80-200 字，2-3 句），替代套话 summary。 */
  text: string;
  /** "stream"=行程刚出炉时；"confirm"=用户确认下单后。 */
  stage: "stream" | "confirm";
}

export interface StreamErrorPayload {
  reason: string;
  detail: string;
}

// ============================================================
// 失败原因（schemas/errors.py）
// ============================================================

export type FailureReason =
  | "restaurant_full"
  | "ticket_sold_out"
  | "distance_exceeded"
  | "duration_exceeded"
  | "not_found"
  | "empty_candidates"
  | "invalid_input"
  | "upstream_failure";

// ============================================================
// 行程（schemas/itinerary.py，edge_v1）
// ============================================================

/** ActivityNode 的 target 类型；home 为隐式起终点节点。 */
export type NodeTargetKind = "poi" | "restaurant" | "home";

/** Hop 的运输模式；virtual 表示同地复用（in_place hop）。 */
export type HopMode =
  | "walking"
  | "taxi"
  | "bus"
  | "haversine_estimated"
  | "virtual";

/** Hop 的路径来源标记，用于前端不同视觉/语义分支。 */
export type HopPathType = "real_route" | "estimated" | "in_place";

/**
 * 活动节点（ActivityNode）—— 边模型的"顶点"。
 * 字段名保持后端 snake_case（不转 camel）。
 */
export interface ActivityNode {
  node_id: string;
  kind: string; // 主活动 / 用餐 / 附加 / home 等
  target_kind: NodeTargetKind;
  target_id: string; // poi_id / restaurant_id / "home"
  start_time: string; // "14:15"
  duration_min: number; // home 节点为 0
  title: string;
  note?: string | null;
  lat?: number | null;
  lng?: number | null;
  address?: string | null;
}

/**
 * 通勤段（Hop）—— 边模型的"边"。
 * minutes：实际通勤分钟（in_place hop minutes=0）。
 * buffer_min：节点之间的缓冲（首跳 0，后续 5）。
 */
export interface Hop {
  hop_id: string;
  from_node_id: string;
  to_node_id: string;
  start_time: string;
  minutes: number;
  mode: HopMode;
  path_type: HopPathType;
  buffer_min: number;
}

/**
 * 派生时间轴（ScheduleEntry）—— assemble 阶段按 start 排序展平的视图。
 * 前端 ItineraryCard 直接遍历这个数组渲染（hidden=true 跳过）。
 *  - entry_kind="node" → ref_id 指向 ActivityNode.node_id
 *  - entry_kind="hop"  → ref_id 指向 Hop.hop_id；mode 必填
 */
export interface ScheduleEntry {
  entry_kind: "node" | "hop";
  ref_id: string;
  start: string;
  end: string;
  title: string;
  minutes: number;
  mode?: HopMode | null;
  hidden: boolean;
}

export interface OrderRecord {
  order_id: string;
  kind: string; // 餐厅预约 / 门票 / 加购服务
  /** edge_v1 新增：用于 confirm 流找下单目标节点。 */
  target_kind: "poi" | "restaurant";
  target_id: string;
  target_name: string;
  detail: string;
}

/**
 * 行程根对象（edge_v1）。
 *
 * schema_version 字段：
 *  - 后端固定输出 "edge_v1"；前端默认按此渲染。
 *  - Task 13 会在 store 层加降级判断：非 edge_v1 时仅渲染 summary + total_minutes。
 *    这里保留 string 兜底类型，避免降级路径触发 TS 错误。
 */
export interface Itinerary {
  schema_version: "edge_v1" | string;
  summary: string;
  nodes: ActivityNode[];
  hops: Hop[];
  schedule: ScheduleEntry[];
  orders: OrderRecord[];
  share_message?: string | null;
  total_minutes: number;
  /** Step 4+8：决策可解释性元数据；is_empty() 时前端隐藏卡片 */
  decision_trace?: DecisionTrace | null;
}

// ============================================================
// 决策可解释性（schemas/decision_trace.py，Step 4+8）
// ============================================================

export interface CriticAttempt {
  attempt_n: number;
  violation_codes: string[];
  feedback_summary: string;
  resolved: boolean;
}

export interface AlternativeCandidate {
  target_kind: string; // poi / restaurant
  target_id: string;
  target_name: string;
  utility_score?: number | null;
  rank: number;
  reason_rejected: string;
}

export interface FallbackHop {
  from_stage: string;
  to_stage: string;
  reason: string;
}

export interface DecisionTrace {
  blueprint_rationale: string;
  weights_explanation: string;
  critic_attempts: CriticAttempt[];
  alternatives_considered: AlternativeCandidate[];
  fallback_chain: FallbackHop[];
  final_strategy: string;
}

// ============================================================
// 意图抽取（schemas/intent.py，§5.7 D-SoT）
// ============================================================

export type SocialContext =
  | "家庭日常"
  | "老人伴助"
  | "闺蜜聊天"
  | "朋友热闹"
  | "情侣亲密"
  | "商务接待"
  | "同学重聚"
  | "独处放空"
  | "纪念日仪式感";

export interface Companion {
  role: string;
  age?: number | null;
  count: number;
  gender_mix?: string | null;
  is_birthday: boolean;
  is_special_role: boolean;
}

export interface IntentExtraction {
  start_time: string;
  start_weekday?: string | null;
  duration_hours: [number, number];
  distance_max_km: number;
  companions: Companion[];
  physical_constraints: string[];
  dietary_constraints: string[];
  experience_tags: string[];
  social_context: SocialContext;
  capacity_requirement?: number | null;
  extra_services: string[];
  preferred_poi_types: string[];
  raw_input: string;
  parse_confidence: number;
  ambiguous_fields: string[];
}

// ============================================================
// 演示场景（GET /scenarios）
// ============================================================

export interface Scenario {
  id: string;
  title: string;
  input: string;
  icon: string;
}

export interface ScenariosResponse {
  scenarios: Scenario[];
}

// ============================================================
// 请求体（POST /chat/stream / /chat/confirm）
// ============================================================

export interface ChatStreamRequest {
  message: string;
  session_id: string;
  scenario_id?: string;
}

export interface ChatConfirmRequest {
  session_id: string;
  decision: "confirm" | "reject" | "modify";
  modifications?: Record<string, unknown> | null;
}

// ============================================================
// 拒绝 + 反馈重规划（schemas/refine.py，POST /chat/refine）
// ============================================================

export interface ChatRefineRequest {
  session_id: string;
  feedback_text: string;
}

// ============================================================
// 双范式 planner（schemas/planner_mode.py）
// ============================================================

export type PlannerMode = "rule" | "llm";

export interface HealthResponse {
  status: string;
  version: string;
  llm_provider: string;
  planner_mode?: PlannerMode;
  /**
   * 当前是否启用真 planner 链路。"1" 表示已配置 LLM 凭证；"0" 表示走 stub。
   * （main.py:_use_real_planner 解析结果）
   */
  planner_real?: string;
}

// ============================================================
// Persona + Memory（schemas/persona.py，Phase 0.7）
// ============================================================

export interface PersonaDefaultTags {
  physical: string[];
  dietary: string[];
  experience: string[];
  suitable_for_priority: SocialContext[];
}

export interface Persona {
  user_id: string;
  label: string;
  icon: string;
  notes: string;
  home_location: string;
  default_distance_max_km: number;
  default_budget: number;
  default_tags: PersonaDefaultTags;
}

export interface UserMemory {
  user_id: string;
  accepted_tags: { counts: Record<string, number> };
  rejected_tags: { counts: Record<string, number> };
  distance_history: number[];
  last_updated_ms?: number | null;
}

export interface UserPreferenceView {
  persona: Persona;
  memory: UserMemory;
  top_priors: string[];
  suggested_distance_max_km?: number | null;
}

export interface PersonasResponse {
  personas: Persona[];
}

// 在 ChatStreamRequest / ChatConfirmRequest 之外，仍透传 X-User-Id header
// 旧字段不变，仅在 sse.ts 加 user_id header；store 层管理 currentUserId
