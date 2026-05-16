/**
 * SSE 客户端：用 fetch + ReadableStream 消费 POST /chat/stream 与 POST /chat/confirm。
 *
 * 浏览器原生 EventSource 只支持 GET，所以这里手写解析器：
 * - 按 SSE 规范以双换行分块
 * - 每块解析 event: 与 data: 字段
 * - data 是 JSON，按 SseEvent 反序列化后回调
 */

import type { SseEvent, SseEventType } from "./types";

export interface SseStreamHandlers {
  onEvent?: (ev: SseEvent) => void;
  onError?: (err: { reason: string; detail?: string }) => void;
  onDone?: () => void;
}

export async function streamSse(
  url: string,
  body: unknown,
  signal: AbortSignal,
  handlers: SseStreamHandlers,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    handlers.onError?.({
      reason: "network",
      detail: e instanceof Error ? e.message : String(e),
    });
    handlers.onDone?.();
    return;
  }

  if (!resp.ok) {
    let detail = "";
    try {
      const data = (await resp.json()) as { detail?: string };
      detail = data?.detail ?? "";
    } catch {
      detail = `HTTP ${resp.status}`;
    }
    handlers.onError?.({ reason: "http", detail: detail || `HTTP ${resp.status}` });
    handlers.onDone?.();
    return;
  }

  const body_ = resp.body;
  if (!body_) {
    handlers.onError?.({ reason: "no_body" });
    handlers.onDone?.();
    return;
  }

  const reader = body_.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // SSE 分隔：\n\n（标准）；服务端可能输出 \r\n\r\n，这里一并兜底
      let sepIdx: number;
      while (
        (sepIdx =
          findBlockSeparator(buf)) !== -1
      ) {
        const block = buf.slice(0, sepIdx);
        buf = buf.slice(sepIdx).replace(/^(\r?\n){2}/, "");
        const parsed = parseBlock(block);
        if (parsed) {
          handlers.onEvent?.(parsed);
          if (parsed.type === ("done" as SseEventType)) {
            handlers.onDone?.();
            return;
          }
        }
      }
    }
    // 流结束但未显式 done
    if (buf.trim()) {
      const parsed = parseBlock(buf);
      if (parsed) handlers.onEvent?.(parsed);
    }
    handlers.onDone?.();
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    handlers.onError?.({
      reason: "stream",
      detail: e instanceof Error ? e.message : String(e),
    });
    handlers.onDone?.();
  }
}

function findBlockSeparator(s: string): number {
  // 优先 \n\n，其次 \r\n\r\n
  const a = s.indexOf("\n\n");
  const b = s.indexOf("\r\n\r\n");
  if (a === -1) return b;
  if (b === -1) return a;
  return Math.min(a, b);
}

function parseBlock(block: string): SseEvent | null {
  let event: string | null = null;
  let data: string | null = null;
  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(":")) continue; // 注释/keepalive
    if (rawLine.startsWith("event:")) {
      event = rawLine.slice("event:".length).trim();
    } else if (rawLine.startsWith("data:")) {
      const part = rawLine.slice("data:".length).trim();
      data = data == null ? part : `${data}\n${part}`;
    }
  }
  if (!event || data == null) return null;
  try {
    return JSON.parse(data) as SseEvent;
  } catch {
    return null;
  }
}
