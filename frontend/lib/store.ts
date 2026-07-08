/**
 * 会话状态：用户输入 / SSE 流事件 / 当前行程 / 加载态。
 *
 * 为什么用 Zustand：
 * - SSE 事件每 200~500ms 推一条，组件需细粒度订阅，避免整树 rerender
 * - React 19 的 useActionState 不适合长流（不能流式回灌中间状态）
 *
 * 文件结构（spec code-modularization-refactor H4）：
 * - store/types.ts            类型定义（ChatState / ToolCallRecord 等）
 * - store/initial-state.ts    initialState（纯数据字段）
 * - store/event-handlers.ts   handleEvent SSE 分发大 switch
 * - store/arrival-counter.ts  跨流到达计数（confirm / stream 共用）
 * - store.ts（本文件）         create() + 9 个 action + re-export 类型
 */

import { create } from "zustand";

import { streamSse } from "./sse";
import type { AdjustAction, PersonasResponse, UserPreferenceView } from "./types";
import {
  API_BASE,
  formatStreamError,
  generateSessionId,
  setPlannerModeCookie,
  setUserIdCookie,
  upsertSession,
  sessionLabelFromText,
} from "./utils";
import { resetArrival } from "./store/arrival-counter";
import { handleEvent } from "./store/event-handlers";
import { initialState } from "./store/initial-state";
import { emptyCriticReport } from "./store/types";
import type {
  ChatState,
  Getter,
  Setter,
} from "./store/types";

// 公开类型给现有引用方（保持 `import { ChatMessage, useChatStore } from "@/lib/store"` 不变）
export type {
  ChatMessage,
  ChatRole,
  ChatState,
  ChitchatReplyRecord,
  RefinementSummary,
  ReplanRecord,
  ToastItem,
  ToolCallRecord,
} from "./store/types";

let abortController: AbortController | null = null;
// ADR-0013 F-4：节点调整走独立的 abort 生命周期，不与主规划/确认流共用同一个
// controller——节点行的换菜是轻量局部操作，不该被"取消当前主流程"的既有逻辑
// 意外牵连，也不该反过来打断一个真正在跑的主流程。
let adjustAbortController: AbortController | null = null;

let toastSeq = 0;
function nextToastId(): string {
  toastSeq += 1;
  return `t-${toastSeq}-${Date.now()}`;
}

/** 当前 planner mode 对应的 header；服务端渲染期间返空对象不暴露 cookie。 */
function plannerHeader(mode: ChatState["plannerMode"]): Record<string, string> {
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
      const data = (await r.json()) as { scenarios: ChatState["scenarios"] };
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

    // T2/R3: 在清空 itinerary 前保存快照（用于 ComparisonView）
    // 不预判 fresh vs feedback——总是保存，UI 层结合 lastRefinement 判定是否展示对比
    const currentItinerary = get().itinerary;
    const previousSnapshot = currentItinerary
      ? structuredClone(currentItinerary)
      : null;

    // 惰性清空（Bug 修复）：**发送时不清空主页面**——这次输入可能只是提问 / 确认 /
    // 预约 / 闲聊（不重跑）。只在收到「重跑信号」(intent_parsed / refinement_start) 时，
    // 由 event-handlers 清空 toolCalls/thoughts/itinerary。这里只挂起 awaitingReplan。
    // 仍保留 messages 历史与 chitchatReplies 气泡。
    set((s) => ({
      streaming: true,
      streamError: null,
      streamPhase: "stream",
      awaitingReplan: true,
      previousItinerary: previousSnapshot,
      cancelled: false,
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
    resetArrival();

    // 持久化 session（首条消息时设 label = 用户输入摘要）
    upsertSession({
      id: get().sessionId,
      label: sessionLabelFromText(trimmed),
      lastMessageAt: Date.now(),
    });

    await streamSse(
      `${API_BASE}/chat/turn`,
      {
        message: trimmed,
        session_id: get().sessionId,
        scenario_id: scenarioId,
      },
      abortController.signal,
      {
        onEvent: (ev) => handleEvent(set as Setter, get as Getter, ev),
        onError: (err) =>
          set({
            streamError: formatStreamError(err.reason, err.detail),
          }),
        onDone: () => {
          // 只有本轮真重跑了（收到过 intent_parsed / refinement_start → awaitingReplan 被清）
          // 才补一条 agent 总结消息。chitchat turn（提问/确认/预约/闲聊）保留着上一轮的
          // narration，不能再 push 一遍——否则旧方案文案会重复出现（惰性清空的连带 bug）。
          const wasReplan = !get().awaitingReplan;
          if (wasReplan) {
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
                upsertSession({
                  id: get().sessionId,
                  lastMessageAt: Date.now(),
                  lastSummary: text.slice(0, 80),
                });
              }
            }
          }
          set({ streaming: false, streamPhase: "idle", awaitingReplan: false });
        },
      },
      undefined,
      { headers: { ...plannerHeader(get().plannerMode), ...userHeader(get().currentUserId) } },
    );
  },

  sendScenario: async (input, scenarioId) => {
    // 演示场景按钮 = 开启一次「全新探索」。若当前会话已有内容（之前点过别的场景
    // 或发过消息），先静默开一个新 session 再发送——避免不同场景的方案堆在同一对话框里
    // （评委依次点 商务/朋友/撸串 时，每个场景应是干净的独立上下文，不互相串台）。
    if (get().streaming) return;
    const hasHistory = get().messages.length > 0 || get().itinerary != null;
    if (hasHistory) {
      abortController?.abort();
      const cur = get();
      const newId = generateSessionId();
      set({
        ...initialState,
        sessionId: newId,
        plannerMode: cur.plannerMode,
        currentUserId: cur.currentUserId,
        personas: cur.personas,
        personasLoaded: cur.personasLoaded,
        preferences: cur.preferences,
        scenarios: cur.scenarios,
        scenariosLoaded: cur.scenariosLoaded,
      });
      upsertSession({
        id: newId,
        label: sessionLabelFromText(input),
        createdAt: Date.now(),
        lastMessageAt: Date.now(),
      });
    }
    await get().sendMessage(input, scenarioId);
  },

  confirm: async () => {
    if (get().streaming) return;
    const itin = get().itinerary;
    if (!itin) return;
    // 幂等守卫：已经预约过（itinerary 带订单）就不再重复 confirm——防按钮多点导致重复下单
    // + 重复 push 收尾文案。前端按钮也会 disabled，这里是双保险（ItineraryCard 按钮等其它入口）。
    if ((itin.orders?.length ?? 0) > 0) return;

    abortController?.abort();
    abortController = new AbortController();
    // confirm **进行中**不重置：接续展示规划链路 + 追加 confirm 执行动画
    // （reserve_restaurant / buy_ticket / order_extra_service / generate_share_message）。
    // confirm **完成后**（onDone）才清掉规划过程展示——它已是终态，只留「已预约」方案卡 + 暖心收尾。
    set({ streaming: true, streamError: null, streamPhase: "confirm" });

    await streamSse(
      `${API_BASE}/chat/confirm`,
      { session_id: get().sessionId, decision: "confirm" },
      abortController.signal,
      {
        onEvent: (ev) => handleEvent(set as Setter, get as Getter, ev),
        onError: (err) =>
          set({
            streamError: formatStreamError(err.reason, err.detail),
          }),
        onDone: () => {
          // confirm 是终态：意图解析 / 思考链路 / 执行动画都让位给「已预约」结果。
          // 清掉规划过程展示，保留方案卡（itinerary 已带订单）+ 暖心收尾文案（"都搞定了"）。
          const narr = get().narration;
          set((s) => ({
            streaming: false,
            streamPhase: "idle",
            intent: null,
            thoughts: [],
            toolCalls: [],
            replans: [],
            lastRefinement: null,
            // Step 2：criticReport 同 toolCalls/thoughts 一样是规划过程的痕迹——
            // confirm 终态让位给「已预约」结果时一并清掉，不留上一轮的自愈记录。
            criticReport: emptyCriticReport(),
            messages:
              narr?.text && narr.stage === "confirm"
                ? [
                    ...s.messages,
                    {
                      id: `a-${Date.now()}`,
                      role: "agent",
                      text: narr.text,
                      createdAt: Date.now(),
                    },
                  ]
                : s.messages,
          }));
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

    // T2/R3: 保存快照供 ComparisonView 使用
    const currentItinerary = get().itinerary;
    const previousSnapshot = currentItinerary
      ? structuredClone(currentItinerary)
      : null;

    // refine 时只清掉 trace / itinerary（保留 intent，新一轮 refinement_done 会覆盖）
    set((s) => ({
      streaming: true,
      streamError: null,
      streamPhase: "refine",
      toolCalls: [],
      replans: [],
      thoughts: [],
      itinerary: null,
      previousItinerary: previousSnapshot,
      narration: null,
      // narrationMessages 绑定 narration.text 这一版（见 store/types.ts），
      // 同一起清，防上一版的"点开看全部"列表串场到这一轮反馈重规划。
      narrationMessages: null,
      cancelled: false,
      lastRefinement: null,
      // Step 2：refine() 走自己的同步清空（不经 clearForReplanIfPending——本
      // action 从不置 awaitingReplan=true），若漏清 criticReport 会导致上一轮
      // 的违规/返工/降级记录串场到这一轮反馈重规划的「质检与自愈」小节里。
      criticReport: emptyCriticReport(),
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
    resetArrival();

    await streamSse(
      `${API_BASE}/chat/turn`,
      // 块C-2（spec planning-pipeline-consolidation R4）：反馈统一走 V3 /chat/turn。
      // V3 router 据 message + checkpointer 跨 turn 恢复的 itinerary 判定为 feedback，
      // 触发 refiner 节点闭环（与首轮规划同路线）。V1 /chat/refine 端点已退役删除。
      {
        message: feedbackText.trim(),
        session_id: get().sessionId,
      },
      abortController.signal,
      {
        onEvent: (ev) => handleEvent(set as Setter, get as Getter, ev),
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
          set({ streaming: false, streamPhase: "idle" });
          // Phase 0.7：refine 后刷偏好（rejected_tags 可能 +1）
          get().refreshPreferences().catch(() => {});
        },
      },
      undefined,
      { headers: { ...plannerHeader(get().plannerMode), ...userHeader(get().currentUserId) } },
    );
  },

  sendAdjust: async (nodeId, action: AdjustAction) => {
    // 全局 streaming（主规划/确认流）进行中，或已有另一个节点在处理换菜 →
    // 不重入（ADR-0013 F-4：lockedNodeId 全局只允许一个节点同时在途）。
    if (get().streaming || get().lockedNodeId) return;

    adjustAbortController?.abort();
    adjustAbortController = new AbortController();
    set({ lockedNodeId: nodeId, streamError: null });

    await streamSse(
      `${API_BASE}/chat/adjust`,
      { session_id: get().sessionId, node_id: nodeId, action },
      adjustAbortController.signal,
      {
        onEvent: (ev) => handleEvent(set as Setter, get as Getter, ev),
        onError: (err) =>
          set({ streamError: formatStreamError(err.reason, err.detail) }),
        onDone: () => {
          set({ lockedNodeId: null });
        },
      },
    );
  },

  cancel: () => {
    abortController?.abort();
    set((s) => ({
      streaming: false,
      streamPhase: "idle",
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

  startNewSession: () => {
    abortController?.abort();
    const cur = get();
    const newId = generateSessionId();
    set({
      ...initialState,
      sessionId: newId,
      plannerMode: cur.plannerMode,
      currentUserId: cur.currentUserId,
      personas: cur.personas,
      personasLoaded: cur.personasLoaded,
      preferences: cur.preferences,
    });
    upsertSession({
      id: newId,
      label: "新对话",
      createdAt: Date.now(),
      lastMessageAt: Date.now(),
    });
    get().pushToast({ kind: "info", text: "已开新会话" });
    void get().loadScenarios();
  },

  switchSession: (sessionId) => {
    if (!sessionId || sessionId === get().sessionId) return;
    abortController?.abort();
    const cur = get();
    // 切换到目标 session：清前端中间过程，但保留 user 身份与场景缓存
    // 后端跨轮上下文 = LangGraph checkpointer，按 thread_id(=session_id) 隔离；
    // 当前实现里前端不从后端拉历史
    // （v3 可加 GET /sessions/:id/messages 拉取，本次先实现切 id）
    set({
      ...initialState,
      sessionId,
      plannerMode: cur.plannerMode,
      currentUserId: cur.currentUserId,
      personas: cur.personas,
      personasLoaded: cur.personasLoaded,
      preferences: cur.preferences,
    });
    upsertSession({ id: sessionId, lastMessageAt: Date.now() });
    get().pushToast({ kind: "info", text: "已切换会话" });
  },

  setPlannerMode: (mode, options) => {
    if (get().plannerMode === mode) return;
    // persist 默认 true：用户显式点击切换才写 sticky cookie；
    // /health 静默同步传 persist:false——不写 cookie，让无 cookie 的浏览器每次
    // mount 都跟随后端 env（修顶栏默认值与后端不一致 + 旧 cookie 永久锁死的 bug）。
    if (options?.persist !== false) {
      setPlannerModeCookie(mode);
    }
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
    setTimeout(
      () => {
        const cur = get().toasts;
        if (cur.some((t) => t.id === id)) {
          set({ toasts: cur.filter((t) => t.id !== id) });
        }
      },
      toast.kind === "warn" ? 4500 : 3500,
    );
  },

  dismissToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
