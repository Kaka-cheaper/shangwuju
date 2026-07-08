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
  // 用户反馈 → 重规划（反馈经 /chat/turn 统一路由触发 refiner）
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

// ADR-0008 B-1 后端枚举 CRITICAL/WARNING→HARD/SOFT 改名,本镜像 2026-07-04
// 才跟上(旧值曾致看板比对空转,后端侧已修过一次同名漂移;保留旧值兼容历史回放)。
export type ViolationSeverity = "hard" | "soft" | "critical" | "warning";

export interface CriticViolation {
  code: ViolationCode;
  severity: ViolationSeverity;
  message: string;
  field_path: string;
  /** B-2 可执行违规字段(critic/_rules/types.py 同名镜像;后端一直在发,
   * 2026-07-04 补齐镜像——看板"质检与自愈"可选消费)。 */
  expected_range?: [number, number] | null;
  node_ref?: string | null;
  hint?: string | null;
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
  /**
   * 信任带（AI 思考流）③拍专用（2026-07-06 新增）：蓝图 `PlanBlueprint.
   * plan_reason` 非空时，`emit_planner` 把它挂在"蓝图 N 个节点：..."这条
   * 既有 AGENT_THOUGHT 事件的兄弟字段上（不新造事件类型）。"无内容不加
   * 字段"——stub / rule / ILS 兜底路径没跑到这次 LLM 调用时缺省，信任带
   * ③拍据此静默跳过，不是渲染空句子。
   */
  plan_reason?: string;
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

// ADR-0011 E-2-c：路由标签闭集 7→6 塌缩后的前端投影（后端 schemas/router.py::
// InputKind 同步改）。meta/emotional 併入 chitchat（语气差异交 tone 字段承载，
// 不再是独立分类）；off_topic 改名 defense；ambiguous 改名 clarify；新增
// confirm（原先塞进 chitchat 的"确认/预约"表态独立成一类）。
export type InputKind =
  | "planning"
  | "chitchat"
  | "confirm"
  | "clarify"
  | "defense";

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

/** D-7（ADR-0010 决策 11 / ADR-0011 决策 5「统一 agent 消息面」）：advisory
 * 结构化条目——narrate_node 把 state.advisories 转成这个形状挂在
 * agent_narration payload 的 messages 兄弟字段。 */
export interface AgentNarrationMessage {
  kind: string; // 目前恒为 "advisory"，预留未来澄清消息复用同一形状
  code: string | null;
  text: string;
}

export interface AgentNarrationPayload {
  /** 暖心开场白文案（80-200 字，2-3 句），替代套话 summary。 */
  text: string;
  /** "stream"=行程刚出炉时；"confirm"=用户确认下单后。 */
  stage: "stream" | "confirm";
  /** D-7：advisory 结构化条目（"无内容不加字段"，可能缺省）。 */
  messages?: AgentNarrationMessage[];
  /** ADR-0013 F-3/F-4：节点调整按钮 + 具名备选，按 ActivityNode.target_id 分组。 */
  node_actions?: NodeActionsMap;
  /**
   * 卡片主角化与事实面板设计终稿§三：节点「真实数据详情」（评分/价钱/距离/
   * 可订余位/标签/营业），按 ActivityNode.target_id 分组——与 node_actions
   * 同一先例（兄弟字段，"无内容不加字段"），schemas/node_detail.py 的
   * NodeDetail 逐节点镜像。
   */
  node_detail?: NodeDetailMap;
  /** ADR-0013 F-4：诉求台账展示投影（仅 /chat/adjust 换菜成功时携带）。 */
  demand_ledger?: DemandLedgerEntry[];
  /**
   * 体感编排批 P1："先出方案，后出文案"。ITINERARY_READY 已由更早的
   * finalize_plan 节点推过（携带规则标题），narrate 节点的叙事 LLM 若换出
   * 一个更精彩的标题，就在这里带上新值（与入参 summary 不同才出现）——
   * 前端据此原地更新已展示的方案卡大标题，不必等一整份新的 itinerary。
   */
  title?: string;
}

// ============================================================
// 节点级调整（schemas/node_adjustment.py · node_chip.py · demand_ledger.py，
// ADR-0013 F-1~F-4）
// ============================================================

/** 定向调整的 6 个受控维度（schemas.node_adjustment.NodeAdjustmentDimension）。 */
export type NodeAdjustmentDimension =
  | "price"
  | "distance"
  | "cuisine_or_type"
  | "dietary"
  | "ambience"
  | "crowd_fit";

/** 一条节点级定向调整——维度 + 取值，按钮展示 / 点击换菜 / 诉求台账三方同一形状。 */
export interface NodeAdjustment {
  dimension: NodeAdjustmentDimension;
  value: string;
}

/** 「定向调整按钮」的下发/回传载荷（schemas.node_chip.NodeChip）。 */
export interface NodeChip {
  node_id: string; // ActivityNode.target_id
  label: string; // ≤8 字
  adjustment: NodeAdjustment;
}

/** 「具名备选」展示要素（agent.planning.planners.node_swap.AlternativeOption）。 */
export interface AlternativeOption {
  kind: "poi" | "restaurant";
  target_id: string;
  name: string;
  rating: number;
  distance_km: number;
  price: number;
  category: string;
}

/** narrate 阶段为一个节点算好的按钮 + 备选（agent.graph.nodes.narrate._build_node_actions）。 */
export interface NodeActionsEntry {
  chips: NodeChip[];
  alternatives: AlternativeOption[];
}

/** `{ActivityNode.target_id: NodeActionsEntry}`——挂 agent_narration payload 兄弟字段。 */
export type NodeActionsMap = Record<string, NodeActionsEntry>;

/**
 * 一个活动节点的真实数据详情（schemas/node_detail.py::NodeDetail 手抄）——
 * ItineraryCard 右栏「安静事实面板」消费。只从真实 Poi/Restaurant 实体字段
 * 派生，绝不由 LLM 生成；字段缺失（后端 `exclude_none` 已省略）时前端该位
 * 不渲染，不补占位符。home 节点不产出本模型。
 */
export interface NodeDetail {
  kind: "poi" | "restaurant";
  /** 实体原始评分（0-5），原样透传——事实面板里唯一暖色触点（⭐深金）。 */
  rating?: number | null;
  /** 人均/门票展示文案，如 "¥75/人"、"¥80–120"、"免费"。 */
  price_text?: string | null;
  /** 距用户家预估直线距离（km），原始数值，前端自行格式化。 */
  distance_km?: number | null;
  /** 可订/余位文案，如 "可订17:30"、"需排队"、"余12"、"约满"——诚实红线：
   * 只报真值，前端照实显、不美化、不补占位。 */
  availability_text?: string | null;
  /** 0-2 个精选标签（餐厅：桌型+描述；POI：适龄+描述），不堆全部原始 tags。 */
  tags: string[];
  /** 营业时段派生文案，如 "营业至21:30"；可选展示位。 */
  open_until_text?: string | null;
  /** ActivityNode.note 缺失时的推荐/评论说明兜底文案。 */
  recommendation_reason?: string | null;
}

/** `{ActivityNode.target_id: NodeDetail}`——挂 agent_narration payload 兄弟字段，
 * 与 NodeActionsMap 同一先例（四条路径全覆盖：/chat/turn、单人换菜、房间换菜、
 * 房间快照，见 backend/collab/room.py::get_state_snapshot / _snapshot_node_detail）。 */
export type NodeDetailMap = Record<string, NodeDetail>;

export type DemandLedgerStatus = "active" | "superseded" | "satisfied";

/** 诉求台账展示投影一条（schemas.demand_ledger.ledger_for_display）。 */
export interface DemandLedgerEntry {
  member_id: string | null;
  nickname: string | null;
  node_ref: { kind: "poi" | "restaurant"; target_id: string } | null;
  dimension: NodeAdjustmentDimension;
  value: string;
  status: DemandLedgerStatus;
  source_text: string;
  created_at: number;
}

// ============================================================
// POST /chat/adjust（ADR-0013 F-4：单人节点调整入口）
// ============================================================

export type AdjustAction =
  | { type: "adjust"; adjustment: NodeAdjustment; label?: string }
  | { type: "alternative"; target_id: string }
  | { type: "dislike" };

export interface ChatAdjustRequest {
  session_id: string;
  node_id: string;
  action: AdjustAction;
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
  is_birthday: boolean;
  is_special_role: boolean;
}

/** 字段出处四值（ADR-0014 决策 1 · G-1）。 */
export type FieldProvenance = "user_stated" | "inferred" | "prior" | "default";

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
  /**
   * 用户明说的人均预算（元，ADR-0014 决策 3 · G-3）。仅当原话明确给出数字才
   * 有值（如"人均 50"→50）；定性表达（"别太贵"）不映射数字，留 null——
   * 系统不编造用户没说的话。
   */
  budget_per_person?: number | null;
  raw_input: string;
  parse_confidence: number;
  ambiguous_fields: string[];
  /**
   * 信任带（AI 思考流）①拍专用一句话（2026-07-06 新增，`schemas/intent.py`
   * `Field(default="")`）：LLM 第一人称"用户……，我理解成……"。恒随
   * `intent.model_dump()` 整体出现在 INTENT_PARSED payload 里（后端 Optional
   * 默认空串，但 model_dump 恒带这个键）——类型上视为 required string，空串
   * 表示"这次没有可展示的理解句"，信任带组件据此静默跳过①拍，不当错误处理。
   */
  understanding: string;
  /**
   * 字段/元素出处标注（ADR-0014 决策 1）。标量字段键=字段名本身（如
   * "distance_max_km"）；列表字段键="字段名:元素值"（如
   * "dietary_constraints:不辣"）。覆盖范围：start_time / start_weekday /
   * duration_hours / distance_max_km / social_context / capacity_requirement /
   * budget_per_person（标量）+ physical_constraints / dietary_constraints /
   * experience_tags / extra_services（列表，元素级）。companions /
   * preferred_poi_types 不在覆盖范围内（见 backend/schemas/intent.py 字段
   * docstring）。Optional，旧数据 / 未跑校正时为 null——前端不应假设它总是存在。
   */
  field_provenance?: Record<string, FieldProvenance> | null;
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
