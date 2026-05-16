"use client";

import { useChatStore } from "@/lib/store";
import { cn, FAILURE_REASON_LABEL, TOOL_LABEL } from "@/lib/utils";

/** Tool 调用链路可视化：评委加分项。
 *
 * 展示：每个 Tool 调用的耗时、输入摘要、成功/失败、被替换状态、异常重规划事件。
 */
export default function ToolTracePanel() {
  const toolCalls = useChatStore((s) => s.toolCalls);
  const replans = useChatStore((s) => s.replans);
  const streaming = useChatStore((s) => s.streaming);

  if (!toolCalls.length && !replans.length && !streaming) {
    return null;
  }

  // 按 arrivalIdx 合并 toolCalls 与 replans，跨 stream/confirm 流稳定排序
  type Item =
    | {
        kind: "tool";
        idx: number;
        tool: ReturnType<() => typeof toolCalls>[number];
      }
    | {
        kind: "replan";
        idx: number;
        reason: string;
        fromTool: string;
      };

  const items: Item[] = [
    ...toolCalls.map((tc) => ({
      kind: "tool" as const,
      idx: tc.arrivalIdx,
      tool: tc,
    })),
    ...replans.map((r) => ({
      kind: "replan" as const,
      idx: r.arrivalIdx,
      reason: r.reason,
      fromTool: r.fromTool,
    })),
  ].sort((a, b) => a.idx - b.idx);

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-ink-200 flex items-center justify-between">
        <div className="text-sm font-medium text-ink-700">Tool 调用链路</div>
        <div className="text-xs text-ink-400">
          {toolCalls.length} 次调用 · {replans.length} 次重规划
        </div>
      </div>

      <ol className="px-4 py-3 space-y-2">
        {items.map((it, idx) => {
          if (it.kind === "replan") {
            return (
              <li
                key={`replan-${it.idx}`}
                className={cn(
                  "rounded-md border border-amber-300 bg-amber-50",
                  "px-3 py-2 text-xs text-amber-800",
                )}
              >
                <div className="font-medium">⚡ 触发异常重规划</div>
                <div className="mt-0.5">
                  原因：{FAILURE_REASON_LABEL[it.reason] ?? it.reason}
                  <span className="mx-1 text-amber-500">·</span>
                  来自：{TOOL_LABEL[it.fromTool] ?? it.fromTool}
                </div>
              </li>
            );
          }
          return (
            <ToolCallItem key={it.tool.id} index={idx + 1} call={it.tool} />
          );
        })}

        {streaming && items.length === 0 && (
          <li className="text-xs text-ink-400 italic">等待 Agent 调用 Tool...</li>
        )}
      </ol>
    </div>
  );
}

function ToolCallItem({
  index,
  call,
}: {
  index: number;
  call: ReturnType<typeof useChatStore.getState>["toolCalls"][number];
}) {
  const inProgress = call.endedAtSeq == null;
  const isOk = call.success === true;
  const isFail = call.success === false;
  const replaced = call.replanned === true;

  const accent = inProgress
    ? "border-brand-300 bg-brand-50/40"
    : replaced
      ? "border-ink-200 bg-ink-50 opacity-70"
      : isFail
        ? "border-red-200 bg-red-50"
        : "border-emerald-200 bg-emerald-50/60";

  const statusBadge = inProgress ? (
    <span className="chip text-brand-700 bg-brand-100 animate-pulse-soft">
      调用中
    </span>
  ) : replaced ? (
    <span className="chip text-ink-500 bg-ink-200">已替换</span>
  ) : isFail ? (
    <span className="chip text-red-700 bg-red-100">失败</span>
  ) : (
    <span className="chip text-emerald-700 bg-emerald-100">成功</span>
  );

  return (
    <li className={cn("rounded-md border px-3 py-2 transition-colors", accent)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium text-ink-800">
          <span className="text-ink-400 text-xs w-5 text-right">#{index}</span>
          <span>{TOOL_LABEL[call.tool] ?? call.tool}</span>
          <span className="text-[11px] text-ink-400 font-normal">
            {call.tool}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {call.durationMs != null && (
            <span className="text-[11px] text-ink-500">
              {call.durationMs}ms
            </span>
          )}
          {statusBadge}
        </div>
      </div>

      {Object.keys(call.input).length > 0 && (
        <div className="mt-1 text-[11px] text-ink-500 font-mono break-all line-clamp-2">
          input: {JSON.stringify(call.input)}
        </div>
      )}

      {isFail && call.reason && (
        <div className="mt-1 text-xs text-red-700">
          原因：{FAILURE_REASON_LABEL[call.reason] ?? call.reason}
        </div>
      )}
    </li>
  );
}
