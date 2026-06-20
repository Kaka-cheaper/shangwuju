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
  IntentExtraction,
  Itinerary,
  RefinementDonePayload,
  RefinementStartPayload,
  ReplanTriggeredPayload,
  SseEvent,
  StreamErrorPayload,
  ToolCallEndPayload,
  ToolCallStartPayload,
} from "../types";

import { currentArrival, nextArrival } from "./arrival-counter";
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
    lastRefinement: null,
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
      set({ narration: { text: p.text, stage: p.stage } });
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
      set({ streamError: `${p.reason}: ${p.detail}` });
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
