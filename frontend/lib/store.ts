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
  Itinerary,
  IntentExtraction,
  ReplanTriggeredPayload,
  Scenario,
  SseEvent,
  StreamErrorPayload,
  ToolCallEndPayload,
  ToolCallStartPayload,
} from "./types";
import { API_BASE, generateSessionId } from "./utils";

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

export interface ChatState {
  // 会话
  sessionId: string;
  scenarios: Scenario[];
  scenariosLoaded: boolean;

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

  // actions
  loadScenarios: () => Promise<void>;
  sendMessage: (input: string, scenarioId?: string) => Promise<void>;
  confirm: () => Promise<void>;
  reset: () => void;
}

const initialState: Omit<
  ChatState,
  "loadScenarios" | "sendMessage" | "confirm" | "reset"
> = {
  // 服务端渲染期间用占位值；客户端 mount 后由 reset/loadScenarios 触发更新
  sessionId: "sess_pending",
  scenarios: [],
  scenariosLoaded: false,
  streaming: false,
  streamError: null,
  messages: [],
  intent: null,
  toolCalls: [],
  replans: [],
  thoughts: [],
  itinerary: null,
};

let abortController: AbortController | null = null;
// 跨整次会话的全局到达计数（confirm 与 stream 共用），保证 trace 面板稳定排序
let arrivalCounter = 0;

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

    // 重置中间过程，但保留 messages 历史
    set((s) => ({
      streaming: true,
      streamError: null,
      intent: null,
      toolCalls: [],
      replans: [],
      thoughts: [],
      itinerary: null,
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
      `${API_BASE}/chat/stream`,
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
            streamError: `${err.reason}${err.detail ? `: ${err.detail}` : ""}`,
          }),
        onDone: () => {
          // 加一条 agent 总结消息（基于 itinerary）
          const itin = get().itinerary;
          if (itin) {
            set((s) => ({
              messages: [
                ...s.messages,
                {
                  id: `a-${Date.now()}`,
                  role: "agent",
                  text: `已为你规划：${itin.summary}`,
                  createdAt: Date.now(),
                },
              ],
            }));
          }
          set({ streaming: false });
        },
      },
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
            streamError: `${err.reason}${err.detail ? `: ${err.detail}` : ""}`,
          }),
        onDone: () => set({ streaming: false }),
      },
    );
  },

  reset: () => {
    abortController?.abort();
    set({ ...initialState, sessionId: generateSessionId() });
    // reset 后立刻刷一次场景缓存（loadScenarios 会复用 scenariosLoaded 跳过）
    void get().loadScenarios();
  },
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

    case "stream_error": {
      const p = ev.payload as unknown as StreamErrorPayload;
      set({ streamError: `${p.reason}: ${p.detail}` });
      break;
    }

    case "done":
      // onDone 在 streamSse 调用方处理
      break;
  }
}
