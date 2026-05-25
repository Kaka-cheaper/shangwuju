/**
 * arrival counter：跨整次会话的全局到达计数（confirm 与 stream 共用）。
 *
 * 为什么不能用 toolCall.startedAtSeq 排序？
 *   confirm 流的 seq 从 0 重新开始，不能与 stream 流的 seq 一起单调排序。
 *   引入独立 counter 让 ToolTracePanel 可以稳定按到达时间排序。
 *
 * sendMessage / refine 触发新轮次时调 reset() 归零；confirm 不归零（接续）。
 */

let counter = 0;

export function nextArrival(): number {
  return counter++;
}

export function resetArrival(): void {
  counter = 0;
}

export function currentArrival(): number {
  return counter;
}
