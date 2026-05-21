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
  AgentThought: "agent_thought",
  ItineraryReady: "itinerary_ready",
  // 用户反馈 → 重规划（Phase 0.6 /chat/refine 专用）
  RefinementStart: "refinement_start",
  RefinementDone: "refinement_done",
  // Phase 0.8 输入域路由（非 planning 输入的暖心回话气泡）
  ChitchatReply: "chitchat_reply",
  // 行程出炉时的 Agent 暖心开场白（替代套话 summary）
  AgentNarration: "agent_narration",
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
}

export interface ToolCallEndPayload {
  tool: string;
  output: Record<string, unknown> & { success?: boolean; reason?: FailureReason };
  duration_ms?: number;
}

export interface ReplanTriggeredPayload {
  reason: FailureReason;
  from_tool: string;
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
// 行程（schemas/itinerary.py）
// ============================================================

export interface ItineraryStage {
  kind: string; // 出发 / 主活动 / 转场 / 用餐 / 附加 / 返回
  start: string; // "14:00"
  end: string;
  title: string;
  poi_id?: string | null;
  restaurant_id?: string | null;
  note?: string | null;
}

export interface OrderRecord {
  order_id: string;
  kind: string; // 餐厅预约 / 门票 / 加购服务
  target_id: string;
  target_name: string;
  detail: string;
}

export interface Itinerary {
  summary: string;
  stages: ItineraryStage[];
  orders: OrderRecord[];
  share_message?: string | null;
  total_minutes: number;
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
