/**
 * SSE 事件分发：每个 SseEventType 对应一个 case。
 *
 * 抽出动机（spec code-modularization-refactor H4）：
 * 让 store.ts 主文件聚焦 zustand action；事件 → state 映射独立可读、可单测。
 *
 * 行为契约：与拆分前的 store.ts:handleEvent 完全一致。
 */

import type {
  AgentNarrationPayload,
  AgentThoughtPayload,
  ChitchatReplyPayload,
  CriticFixAttemptPayload,
  CriticViolationsPayload,
  IntentExtraction,
  Itinerary,
  PlanFallbackPayload,
  RefinementDonePayload,
  RefinementStartPayload,
  ReplanTriggeredPayload,
  SseEvent,
  StreamErrorPayload,
  ToolCallEndPayload,
  ToolCallStartPayload,
} from "../types";

import { currentArrival, nextArrival } from "./arrival-counter";
import { emptyCriticReport } from "./types";
import type { Getter, Setter, ToolCallRecord } from "./types";

/**
 * 惰性清空（Bug 修复）：只有收到「重跑信号」(intent_parsed / refinement_start) 时，
 * 才把主页面清空腾位给新一轮。提问 / 确认 / 预约 / 闲聊只发 chitchat_reply，
 * 收不到这两个信号 → awaitingReplan 一直为 true、主页面纹丝不动。
 * 用 awaitingReplan 保证一轮只清一次（feedback 路径的第一个信号是 refinement_start，
 * 之后的 intent_parsed 不再清，避免抹掉刚 set 的 refined intent）。
 */
function clearForReplanIfPending(set: Setter, get: Getter): void {
  if (!get().awaitingReplan) return;
  set({
    awaitingReplan: false,
    toolCalls: [],
    replans: [],
    thoughts: [],
    intent: null,
    itinerary: null,
    narration: null,
    // messages 是 narration.text 这一版的详情展开（见 store/types.ts 字段
    // docstring）——narration 清空时必须跟着清，否则上一轮的"点开看全部"
    // 列表会挂在这一轮还没产出内容的 narration 上，牛头不对马嘴。
    narrationMessages: null,
    lastRefinement: null,
    // ADR-0013：node_actions 绑定"这一版方案"，换方案即失效——同 itinerary 一起
    // 清空，等新一轮 narrate 产出前不展示指向已作废节点的按钮。demandLedger 是
    // SESSION_SCOPED（诉求跨规划事件存活），不随重跑清空。
    nodeActions: null,
    // Step 2：criticReport 同 toolCalls/thoughts 一样是 PER-TURN 过程数据——
    // 不清会导致上一轮的违规/返工/降级记录串场到这一轮的「质检与自愈」小节里。
    criticReport: emptyCriticReport(),
  });
}

export function handleEvent(set: Setter, get: Getter, ev: SseEvent): void {
  switch (ev.type) {
    case "intent_parsed":
      clearForReplanIfPending(set, get); // 重跑信号：首次到达时清空主页面腾位
      set({ intent: ev.payload as unknown as IntentExtraction });
      break;

    case "tool_call_start": {
      const p = ev.payload as unknown as ToolCallStartPayload;
      const rec: ToolCallRecord = {
        id: `${p.tool}-${currentArrival()}`,
        tool: p.tool,
        input: p.input,
        startedAtSeq: ev.seq,
        arrivalIdx: nextArrival(),
        groupId: p.group_id ?? null,
        parallel: p.parallel ?? false,
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
              // R1：tool_call_end 也带 group_id（与 start 同步），便于失败补偿场景
              groupId: arr[i].groupId ?? p.group_id ?? null,
              parallel: arr[i].parallel ?? p.parallel ?? false,
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
            arrivalIdx: nextArrival(),
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

    // Step 2：critic 校验 + 自愈闭环三事件——原先在 SSE_NOT_CONSUMED_IN_SWITCH
    // 白名单挂了很久（后端一直在发，前端一直静默丢弃），路演拍板做成「系统自愈
    // 过程可视化」后正式接线。三者只落 store（criticReport），不影响任何既有
    // 字段/清空逻辑；面板呈现见 ThoughtPanel「质检与自愈」小节。
    case "critic_violations": {
      const p = ev.payload as unknown as CriticViolationsPayload;
      set((s) => ({
        criticReport: {
          ...s.criticReport,
          violationRounds: [
            ...s.criticReport.violationRounds,
            {
              seq: ev.seq,
              arrivalIdx: nextArrival(),
              fixAttempt: p.fix_attempt,
              violations: p.violations,
            },
          ],
        },
      }));
      break;
    }

    case "critic_fix_attempt": {
      const p = ev.payload as unknown as CriticFixAttemptPayload;
      set((s) => ({
        criticReport: {
          ...s.criticReport,
          fixAttempts: [
            ...s.criticReport.fixAttempts,
            {
              seq: ev.seq,
              arrivalIdx: nextArrival(),
              attempt: p.attempt,
              feedbackText: p.feedback_text,
            },
          ],
        },
      }));
      break;
    }

    case "plan_fallback": {
      const p = ev.payload as unknown as PlanFallbackPayload;
      set((s) => ({
        criticReport: {
          ...s.criticReport,
          fallbackHops: [
            ...s.criticReport.fallbackHops,
            {
              seq: ev.seq,
              arrivalIdx: nextArrival(),
              from: p.from,
              to: p.to,
              reason: p.reason,
            },
          ],
        },
      }));
      break;
    }

    case "agent_thought": {
      const p = ev.payload as unknown as AgentThoughtPayload;
      set((s) => ({
        thoughts: [
          ...s.thoughts,
          { seq: ev.seq, text: p.text, timestamp_ms: ev.timestamp_ms ?? null },
        ],
      }));
      break;
    }

    case "itinerary_ready": {
      // R9：SSE schema 兼容降级。
      // 后端 edge model refactor 后 payload 自带 schema_version="edge_v1"。
      // 若版本字段缺失或不一致（旧后端 / 错误数据），不抛错也不全屏崩，
      // 仅保留 summary + total_minutes 文字提示，让 demo 现场仍能看到结果。
      // 校验放在 store 层而不是组件层 —— 一处兜住，下游所有组件无脑读 itinerary 即可。
      const rawPayload = ev.payload as unknown as Itinerary & {
        schema_version?: string;
      };
      if (rawPayload.schema_version !== "edge_v1") {
        console.warn(
          "[store] itinerary_ready schema_version 不兼容：",
          rawPayload.schema_version,
          "—— 已降级到文字摘要（仅渲染 summary + total_minutes）",
        );
        // fallback 同时带「旧 stages 字段」与「新 nodes/hops/schedule 字段」：
        // - 兼容 Task 11 完成前（types.ts 仍是 stages schema）：stages=[] 让组件不 NPE
        // - 兼容 Task 11 完成后（types.ts 升级为 edge schema）：nodes/hops/schedule=[] 同步生效
        // 强转 `as unknown as Itinerary` 让 schema 演进期不卡 typecheck。
        const fallback = {
          schema_version: "edge_v1" as const,
          summary:
            rawPayload.summary ??
            "（行程数据格式暂不兼容，已为你保留文字摘要，请稍后重试）",
          stages: [],
          nodes: [],
          hops: [],
          schedule: [],
          orders: [],
          share_message: rawPayload.share_message ?? null,
          total_minutes: rawPayload.total_minutes ?? 0,
          decision_trace: null,
        };
        set({ itinerary: fallback as unknown as Itinerary });
        break;
      }
      set({ itinerary: rawPayload });
      break;
    }

    case "agent_narration": {
      const p = ev.payload as unknown as AgentNarrationPayload;
      // ADR-0013 F-3/F-4："无内容不加字段"——node_actions/demand_ledger 缺省时
      // 保留上一版（同 narration 本身"绑定这一版方案"的持久语义），不是清空。
      set((s) => ({
        narration: { text: p.text, stage: p.stage },
        // D-7：messages 绑定"这一版叙事"（同 narration.text 一起整体替换），
        // 不套 node_actions/demand_ledger 的"缺省保留上一版"——上一版 messages
        // 讲的是上一版 text 里折叠的取舍，这一版 text 换了就该跟着换，缺省即
        // 清空为 null（没有可展开的内容），不是沿用旧值。
        narrationMessages: p.messages ?? null,
        nodeActions: p.node_actions ?? s.nodeActions,
        demandLedger: p.demand_ledger ?? s.demandLedger,
        // 体感编排批 P1："从能用到精彩"——ITINERARY_READY 早已推过规则标题
        // （finalize_plan 节点），这里只在 narrate 换出更精彩的 LLM 标题时
        // （payload.title 存在）原地更新已展示的方案卡大标题，不整份替换
        // itinerary（title 缺省 = 本轮没有更好的标题，沿用已展示的版本）。
        itinerary: p.title && s.itinerary
          ? { ...s.itinerary, summary: p.title }
          : s.itinerary,
      }));
      break;
    }

    case "memory_persisted": {
      // spec algorithm-redesign R5 收尾：把 memory_writer 副作用结果落到 store
      // 让 ItineraryCard 显示「✓ 已记住此次「家庭日常」场景偏好，下次会复用」标记
      const p = ev.payload as {
        social_context?: string;
        summary_preview?: string;
        success?: boolean;
        skipped_reason?: string | null;
      };
      set({
        memoryPersisted: {
          socialContext: p.social_context ?? "",
          summaryPreview: p.summary_preview ?? "",
          success: Boolean(p.success),
          skippedReason: p.skipped_reason ?? null,
        },
      });
      break;
    }

    case "refinement_start": {
      clearForReplanIfPending(set, get); // feedback 重跑的第一个信号：清空主页面腾位
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
            timestamp_ms: ev.timestamp_ms ?? null,
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
      const message = `${p.reason}: ${p.detail}`;
      set({ streamError: message });
      // A8 根治：此前只 set streamError，靠各端自己订阅渲染成红色错误条——
      // ChatDock 的错误条挂在 timeline 展开态内部（收起时完全不可见），移动端
      // 此前干脆零订阅（完全静默）。两端共用同一个 handleEvent，这里补一条
      // toast 是"一处修两端受益"的根治版：无论订阅面板是否展开/挂载，用户都
      // 至少能看到一次性提示。不替代各端自己的常驻错误条（ChatDock 的横幅、
      // MobileHomeView 新增的横幅），是双重兜底，不是二选一。
      get().pushToast({ kind: "warn", text: `流出错：${message}` });
      break;
    }

    case "chitchat_reply": {
      const p = ev.payload as unknown as ChitchatReplyPayload;
      // 预约确认气泡（含 action=confirm 的 chip）= 进入终态前奏：收起规划过程展示
      // （意图解析卡 + 思考链路面板），让主页面聚焦「方案 + 确认预约」。
      // 普通闲聊 / 提问 / 确认气泡不清——只有「预约吧」这种带确认按钮的才清。
      const isBookingPrompt = (p.cta_chips ?? []).some(
        (c) => c.action === "confirm",
      );
      set((s) => ({
        chitchatReplies: [
          ...s.chitchatReplies,
          {
            id: `c-${ev.seq}-${Date.now()}`,
            payload: p,
            receivedAtMs: ev.timestamp_ms ?? Date.now(),
          },
        ],
        ...(isBookingPrompt
          ? { intent: null, thoughts: [], toolCalls: [], replans: [] }
          : {}),
      }));
      break;
    }

    case "done":
      // onDone 在 streamSse 调用方处理
      break;
  }
}
