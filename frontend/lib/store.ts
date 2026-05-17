/**
 * 会话状态：用户输入 / SSE 流事件 / 当前行程 / 加载态。
 *
 * 为什么用 Zustand：
 * - SSE 事件每 200~500ms 推一条，组件需细粒度订阅，避免整树 rerender
 * - React 19 的 useActionState 不适合长流（不能流式回灌中间状态）
 */

import { create } from "zustand";

import { streamSse } from "./sse";
import type {
  AgentThoughtPayload,
  AgentNarrationPayload,
  ChitchatReplyPayload,
  Itinerary,
  IntentExtraction,
  PlannerMode,
  Persona,
  PersonasResponse,
  RefinementDonePayload,
  RefinementStartPayload,
  ReplanTriggeredPayload,
  Scenario,
  SseEvent,
  StreamErrorPayload,
  ToolCallEndPayload,
  ToolCallStartPayload,
  UserPreferenceView,
} from "./types";
import {
  API_BASE,
  formatStreamError,
  generateSessionId,
  getPlannerModeFromCookie,
  setPlannerModeCookie,
  getUserIdFromCookie,
  setUserIdCookie,
} from "./utils";

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

  // 聊天与中间过程
  messages: ChatMessage[];
  intent: IntentExtraction | null;
  toolCalls: ToolCallRecord[];
  replans: ReplanRecord[];
  thoughts: { seq: number; text: string }[];

  // 输出
  itinerary: Itinerary | null;
  /** Agent 暖心开场白（行程出炉 / confirm 后由后端推送）。 */
  narration: { text: string; stage: "stream" | "confirm" } | null;
  /** 用户已主动取消（和 reset 不同：不清空 trace，仅冻结按钮）。 */
  cancelled: boolean;
  /** 上一次 refinement_done 摘要，用于「我已为你调整」面板。 */
  lastRefinement: RefinementSummary | null;
  /** Phase 0.8：本次会话内收到的所有暖心回话气泡（按时序追加，不清空）。 */
  chitchatReplies: ChitchatReplyRecord[];

  // UI 通知
  toasts: ToastItem[];

  // Cmd+K 命令面板
  commandPaletteOpen: boolean;

  // actions
  loadScenarios: () => Promise<void>;
  sendMessage: (input: string, scenarioId?: string) => Promise<void>;
  confirm: () => Promise<void>;
  refine: (feedbackText: string) => Promise<void>;
  cancel: () => void;
  reset: () => void;
  setPlannerMode: (mode: PlannerMode, options?: { silent?: boolean }) => void;
  setCurrentUserId: (userId: string, options?: { silent?: boolean }) => void;
  loadPersonas: () => Promise<void>;
  refreshPreferences: () => Promise<void>;
  resetUserMemory: () => Promise<void>;
  pushToast: (toast: Omit<ToastItem, "id">) => void;
  dismissToast: (id: string) => void;
  openCommandPalette: () => void;
  closeCommandPalette: () => void;
}

const initialState: Omit<
  ChatState,
  | "loadScenarios"
  | "sendMessage"
  | "confirm"
  | "refine"
  | "cancel"
  | "reset"
  | "setPlannerMode"
  | "setCurrentUserId"
  | "loadPersonas"
  | "refreshPreferences"
  | "resetUserMemory"
  | "pushToast"
  | "dismissToast"
  | "openCommandPalette"
  | "closeCommandPalette"
> = {
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
  messages: [],
  intent: null,
  toolCalls: [],
  replans: [],
  thoughts: [],
  itinerary: null,
  cancelled: false,
  lastRefinement: null,
  chitchatReplies: [],
  toasts: [],
  commandPaletteOpen: false,
  narration: null,
};

let abortController: AbortController | null = null;
// 跨整次会话的全局到达计数（confirm 与 stream 共用），保证 trace 面板稳定排序
let arrivalCounter = 0;

let toastSeq = 0;
function nextToastId(): string {
  toastSeq += 1;
  return `t-${toastSeq}-${Date.now()}`;
}

/** 当前 planner mode 对应的 header；服务端渲染期间返空对象不暴露 cookie。 */
function plannerHeader(mode: PlannerMode): Record<string, string> {
  return { "X-Planner-Mode": mode };
}

/** Phase 0.7：当前 user_id header；与 plannerHeader 合并后透传给 SSE 请求。 */
function userHeader(userId: string | null | undefined): Record<string, string> {
  if (!userId) return {};
  return { "X-User-Id": userId };
}

export const useChatStore = create<ChatState>((set, get) => ({
  ...initialState,

  loadScenarios: async () => {
    if (get().scenariosLoaded) return;
    try {
      const r = await fetch(`${API_BASE}/scenarios`);
      const data = (await r.json()) as { scenarios: Scenario[] };
      set({ scenarios: data.scenarios, scenariosLoaded: true });
    } catch (e) {
      console.warn("[scenarios] 拉取失败：", e);
      set({ scenariosLoaded: true });
    }
  },

  sendMessage: async (input, scenarioId) => {
    if (get().streaming) return;
    const trimmed = input.trim();
    if (!trimmed) return;

    abortController?.abort();
    abortController = new AbortController();

    // 重置中间过程，但保留 messages 历史与 chitchatReplies 气泡
    // （聊天历史不应被新输入清空——只有用户主动 reset 才清）
    set((s) => ({
      streaming: true,
      streamError: null,
      intent: null,
      toolCalls: [],
      replans: [],
      thoughts: [],
      itinerary: null,
      narration: null,
      cancelled: false,
      lastRefinement: null,
      messages: [
        ...s.messages,
        {
          id: `u-${Date.now()}`,
          role: "user",
          text: trimmed,
          createdAt: Date.now(),
        },
      ],
    }));
    arrivalCounter = 0;

    await streamSse(
      `${API_BASE}/chat/turn`,
      {
        message: trimmed,
        session_id: get().sessionId,
        scenario_id: scenarioId,
      },
      abortController.signal,
      {
        onEvent: (ev) => handleEvent(set, get, ev),
        onError: (err) =>
          set({
            streamError: formatStreamError(err.reason, err.detail),
          }),
        onDone: () => {
          // 加一条 agent 总结消息——优先用后端 narration（暖语气），fallback 到 summary
          const itin = get().itinerary;
          const narr = get().narration;
          if (narr?.text || itin) {
            const text = narr?.text || (itin ? `已为你规划：${itin.summary}` : "");
            if (text) {
              set((s) => ({
                messages: [
                  ...s.messages,
                  {
                    id: `a-${Date.now()}`,
                    role: "agent",
                    text,
                    createdAt: Date.now(),
                  },
                ],
              }));
            }
          }
          set({ streaming: false });
        },
      },
      undefined,
      { headers: { ...plannerHeader(get().plannerMode), ...userHeader(get().currentUserId) } },
    );
  },

  confirm: async () => {
    if (get().streaming) return;
    if (!get().itinerary) return;

    abortController?.abort();
    abortController = new AbortController();
    set({ streaming: true, streamError: null });

    await streamSse(
      `${API_BASE}/chat/confirm`,
      { session_id: get().sessionId, decision: "confirm" },
      abortController.signal,
      {
        onEvent: (ev) => handleEvent(set, get, ev),
        onError: (err) =>
          set({
            streamError: formatStreamError(err.reason, err.detail),
          }),
        onDone: () => {
          // confirm 后的暖心收尾文案（"都搞定了"语气，由后端 narrator confirm 阶段生成）
          const narr = get().narration;
          if (narr?.text && narr.stage === "confirm") {
            set((s) => ({
              messages: [
                ...s.messages,
                {
                  id: `a-${Date.now()}`,
                  role: "agent",
                  text: narr.text,
                  createdAt: Date.now(),
                },
              ],
            }));
          }
          set({ streaming: false });
          // Phase 0.7：confirm 后异步刷偏好（让评委看到 accepted_tags 累加）
          get().refreshPreferences().catch(() => {});
        },
      },
      undefined,
      { headers: { ...plannerHeader(get().plannerMode), ...userHeader(get().currentUserId) } },
    );
  },

  refine: async (feedbackText) => {
    if (get().streaming) return;
    if (!get().itinerary) return;

    abortController?.abort();
    abortController = new AbortController();

    // refine 时只清掉 trace / itinerary（保留 intent，新一轮 refinement_done 会覆盖）
    set((s) => ({
      streaming: true,
      streamError: null,
      toolCalls: [],
      replans: [],
      thoughts: [],
      itinerary: null,
      narration: null,
      cancelled: false,
      lastRefinement: null,
      messages: feedbackText.trim()
        ? [
            ...s.messages,
            {
              id: `u-${Date.now()}`,
              role: "user",
              text: `（反馈）${feedbackText.trim()}`,
              createdAt: Date.now(),
            },
          ]
        : s.messages,
    }));
    arrivalCounter = 0;

    await streamSse(
      `${API_BASE}/chat/refine`,
      { session_id: get().sessionId, feedback_text: feedbackText.trim() },
      abortController.signal,
      {
        onEvent: (ev) => handleEvent(set, get, ev),
        onError: (err) =>
          set({
            streamError: formatStreamError(err.reason, err.detail),
          }),
        onDone: () => {
          // refine 后的导游开场白（暖语气，强调"已根据反馈"）
          const itin = get().itinerary;
          const narr = get().narration;
          if (narr?.text || itin) {
            const text = narr?.text
              ? `已根据你的反馈重新规划——${narr.text}`
              : itin
                ? `已根据你的反馈重新规划：${itin.summary}`
                : "";
            if (text) {
              set((s) => ({
                messages: [
                  ...s.messages,
                  {
                    id: `a-${Date.now()}`,
                    role: "agent",
                    text,
                    createdAt: Date.now(),
                  },
                ],
              }));
            }
          }
          set({ streaming: false });
          // Phase 0.7：refine 后刷偏好（rejected_tags 可能 +1）
          get().refreshPreferences().catch(() => {});
        },
      },
      undefined,
      { headers: { ...plannerHeader(get().plannerMode), ...userHeader(get().currentUserId) } },
    );
  },

  cancel: () => {
    abortController?.abort();
    set((s) => ({
      streaming: false,
      cancelled: true,
      messages: [
        ...s.messages,
        {
          id: `a-${Date.now()}`,
          role: "agent",
          text: "已取消当前方案。可以重新点击场景按钮或输入一句话。",
          createdAt: Date.now(),
        },
      ],
    }));
    get().pushToast({ kind: "warn", text: "已取消方案" });
  },

  reset: () => {
    abortController?.abort();
    const cur = get();
    set({
      ...initialState,
      sessionId: generateSessionId(),
      plannerMode: cur.plannerMode,
      // Phase 0.7：reset 不清 user 身份（演示连续切换 user 体验稳）
      currentUserId: cur.currentUserId,
      personas: cur.personas,
      personasLoaded: cur.personasLoaded,
      preferences: cur.preferences,
    });
    // reset 后立刻刷一次场景缓存（loadScenarios 会复用 scenariosLoaded 跳过）
    void get().loadScenarios();
  },

  setPlannerMode: (mode, options) => {
    if (get().plannerMode === mode) return;
    setPlannerModeCookie(mode);
    set({ plannerMode: mode });
    if (options?.silent) return;
    get().pushToast({
      kind: "info",
      text:
        mode === "llm"
          ? "已切换到 LLM 自主决策模式"
          : "已切换到规则化模式（Demo 安全网）",
    });
  },

  setCurrentUserId: (userId, options) => {
    if (get().currentUserId === userId) return;
    setUserIdCookie(userId);
    set({ currentUserId: userId, preferences: null });
    // 切 user 后立即拉新偏好（异步，不阻塞 UI）
    get().refreshPreferences().catch(() => {});
    if (options?.silent) return;
    const persona = get().personas.find((p) => p.user_id === userId);
    get().pushToast({
      kind: "info",
      text: persona ? `已切到「${persona.label}」` : `已切到 ${userId}`,
    });
  },

  loadPersonas: async () => {
    if (get().personasLoaded) return;
    try {
      const resp = await fetch(`${API_BASE}/personas`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = (await resp.json()) as PersonasResponse;
      set({ personas: data.personas, personasLoaded: true });
    } catch (e) {
      get().pushToast({
        kind: "warn",
        text: `加载用户档案失败：${(e as Error).message}`,
      });
    }
  },

  refreshPreferences: async () => {
    const userId = get().currentUserId;
    if (!userId) return;
    try {
      const resp = await fetch(
        `${API_BASE}/preferences/${encodeURIComponent(userId)}`,
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = (await resp.json()) as UserPreferenceView;
      set({ preferences: data });
    } catch {
      // 偏好失败不阻塞主流程
    }
  },

  resetUserMemory: async () => {
    const userId = get().currentUserId;
    if (!userId) return;
    try {
      const resp = await fetch(
        `${API_BASE}/preferences/${encodeURIComponent(userId)}/reset`,
        { method: "POST" },
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await get().refreshPreferences();
      get().pushToast({ kind: "success", text: "已清空当前用户的偏好记忆" });
    } catch (e) {
      get().pushToast({
        kind: "warn",
        text: `清空失败：${(e as Error).message}`,
      });
    }
  },

  pushToast: (toast) => {
    const id = nextToastId();
    set((s) => ({ toasts: [...s.toasts, { ...toast, id }] }));
    // 自动消失
    setTimeout(() => {
      const cur = get().toasts;
      if (cur.some((t) => t.id === id)) {
        set({ toasts: cur.filter((t) => t.id !== id) });
      }
    }, toast.kind === "warn" ? 4500 : 3500);
  },

  dismissToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  openCommandPalette: () => set({ commandPaletteOpen: true }),
  closeCommandPalette: () => set({ commandPaletteOpen: false }),
}));

// ============================================================
// 事件分发
// ============================================================

type Setter = (
  partial:
    | Partial<ChatState>
    | ((state: ChatState) => Partial<ChatState>),
) => void;
type Getter = () => ChatState;

function handleEvent(set: Setter, get: Getter, ev: SseEvent): void {
  switch (ev.type) {
    case "intent_parsed":
      set({ intent: ev.payload as unknown as IntentExtraction });
      break;

    case "tool_call_start": {
      const p = ev.payload as unknown as ToolCallStartPayload;
      const rec: ToolCallRecord = {
        id: `${p.tool}-${arrivalCounter}`,
        tool: p.tool,
        input: p.input,
        startedAtSeq: ev.seq,
        arrivalIdx: arrivalCounter++,
      };
      set((s) => ({ toolCalls: [...s.toolCalls, rec] }));
      break;
    }

    case "tool_call_end": {
      const p = ev.payload as unknown as ToolCallEndPayload;
      set((s) => {
        const arr = [...s.toolCalls];
        // 找最近一个匹配 tool 且未结束的记录
        for (let i = arr.length - 1; i >= 0; i--) {
          if (arr[i].tool === p.tool && arr[i].endedAtSeq == null) {
            arr[i] = {
              ...arr[i],
              endedAtSeq: ev.seq,
              durationMs: p.duration_ms,
              success:
                typeof p.output?.success === "boolean"
                  ? (p.output.success as boolean)
                  : undefined,
              reason: (p.output?.reason as string) ?? null,
              output: p.output,
            };
            break;
          }
        }
        return { toolCalls: arr };
      });
      break;
    }

    case "replan_triggered": {
      const p = ev.payload as unknown as ReplanTriggeredPayload;
      set((s) => ({
        replans: [
          ...s.replans,
          {
            seq: ev.seq,
            arrivalIdx: arrivalCounter++,
            reason: p.reason,
            fromTool: p.from_tool,
          },
        ],
        // 把同名工具最近一次未标记的调用标为「已替换」
        // 不能只看 success（如 check_availability 返回 success=true + available=false 也属于触发重规划）
        toolCalls: (() => {
          const arr = [...s.toolCalls];
          for (let i = arr.length - 1; i >= 0; i--) {
            if (arr[i].tool === p.from_tool && !arr[i].replanned) {
              arr[i] = { ...arr[i], replanned: true };
              break;
            }
          }
          return arr;
        })(),
      }));
      break;
    }

    case "agent_thought": {
      const p = ev.payload as unknown as AgentThoughtPayload;
      set((s) => ({
        thoughts: [...s.thoughts, { seq: ev.seq, text: p.text }],
      }));
      break;
    }

    case "itinerary_ready":
      set({ itinerary: ev.payload as unknown as Itinerary });
      break;

    case "agent_narration": {
      const p = ev.payload as unknown as AgentNarrationPayload;
      set({ narration: { text: p.text, stage: p.stage } });
      break;
    }

    case "refinement_start": {
      const p = ev.payload as unknown as RefinementStartPayload;
      // 只用做轻量提示；refinement_done 才是真正的 changed_fields 来源
      set((s) => ({
        thoughts: [
          ...s.thoughts,
          {
            seq: ev.seq,
            text: p.feedback_text
              ? `开始根据你的反馈调整：「${p.feedback_text}」`
              : "开始重新规划...",
          },
        ],
      }));
      break;
    }

    case "refinement_done": {
      const p = ev.payload as unknown as RefinementDonePayload;
      // 找出最近一条用户反馈消息（用于把 feedbackText 填进 lastRefinement）
      const msgs = get().messages;
      let feedbackText = "";
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        if (m.role === "user" && m.text.startsWith("（反馈）")) {
          feedbackText = m.text.replace(/^（反馈）/, "");
          break;
        }
      }
      // 用合并后的 intent 覆盖意图摘要面板
      set({
        intent: p.refined_intent,
        lastRefinement: {
          feedbackText,
          changedFields: p.changed_fields ?? [],
          refinerNote: p.refiner_note ?? null,
          timestampMs: ev.timestamp_ms ?? Date.now(),
        },
      });
      // 把变更摘要每一条作为一个 toast，让用户立刻看见 Agent 的调整
      const fields = p.changed_fields ?? [];
      if (fields.length === 0) {
        get().pushToast({
          kind: "info",
          text: "没找到可执行的调整，已为你尝试重新组合候选",
        });
      } else if (fields.length <= 2) {
        for (const f of fields) {
          get().pushToast({ kind: "success", text: `Agent 调整：${f}` });
        }
      } else {
        get().pushToast({
          kind: "success",
          text: `Agent 已为你调整 ${fields.length} 项：${fields[0]} 等`,
        });
      }
      break;
    }

    case "stream_error": {
      const p = ev.payload as unknown as StreamErrorPayload;
      set({ streamError: `${p.reason}: ${p.detail}` });
      break;
    }

    case "chitchat_reply": {
      const p = ev.payload as unknown as ChitchatReplyPayload;
      set((s) => ({
        chitchatReplies: [
          ...s.chitchatReplies,
          {
            id: `c-${ev.seq}-${Date.now()}`,
            payload: p,
            receivedAtMs: ev.timestamp_ms ?? Date.now(),
          },
        ],
      }));
      break;
    }

    case "done":
      // onDone 在 streamSse 调用方处理
      break;
  }
}
