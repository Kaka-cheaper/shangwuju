/**
 * SSE 客户端：用 fetch + ReadableStream 消费 POST /chat/stream 与 POST /chat/confirm。
 *
 * 浏览器原生 EventSource 只支持 GET，所以这里手写解析器：
 * - 按 SSE 规范以双换行（\n\n / \r\n\r\n）分块
 * - 每块解析 event: 与 data: 字段（data 多行自动 join）
 * - data 是 JSON，按 SseEvent 反序列化后回调
 *
 * 鲁棒性能力：
 * - 粘围栏：一个 chunk 内多个事件块 → while 循环切分
 * - 长 token 跨 chunk：buf 累积到下次 read
 * - HTTP 错误：解析后端 detail 字段
 * - 首字节超时（默认 8s）：判断后端 / 代理是否健康
 * - 空闲超时（默认 30s）：长时间无新事件视为断流
 * - 中途异常：onError 后调 onDone 让 store 复位 streaming 标志
 */

import type { SseEvent, SseEventType } from "./types";

export interface SseStreamHandlers {
  /** 收到一条事件（已 JSON 反序列化为 SseEvent）。 */
  onEvent?: (ev: SseEvent) => void;
  /** 网络层 / 协议层错误。可能在 onDone 前调用。 */
  onError?: (err: SseStreamError) => void;
  /** 流结束（无论成功 done 还是异常）。store 据此复位 streaming 标志。 */
  onDone?: () => void;
}

export interface SseStreamError {
  reason:
    | "network"
    | "http"
    | "no_body"
    | "stream"
    | "timeout_first_event"
    | "idle_timeout"
    | "parse";
  detail?: string;
}

export interface SseStreamOptions {
  /** 首字节超时（ms）：发出请求后多久内必须收到第一条事件。默认 8000。 */
  firstEventTimeoutMs?: number;
  /** 空闲超时（ms）：相邻两条事件的最大间隔。默认 30000。 */
  idleTimeoutMs?: number;
}

export interface SseRequestOptions {
  /** 额外请求头（如 X-Planner-Mode）。Content-Type / Accept 会自动设置。 */
  headers?: Record<string, string>;
}

const DEFAULT_FIRST_EVENT_TIMEOUT = 8000;
const DEFAULT_IDLE_TIMEOUT = 30000;

export async function streamSse(
  url: string,
  body: unknown,
  signal: AbortSignal,
  handlers: SseStreamHandlers,
  options: SseStreamOptions = {},
  request: SseRequestOptions = {},
): Promise<void> {
  const firstTimeoutMs =
    options.firstEventTimeoutMs ?? DEFAULT_FIRST_EVENT_TIMEOUT;
  const idleTimeoutMs = options.idleTimeoutMs ?? DEFAULT_IDLE_TIMEOUT;

  let resp: Response;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(request.headers ?? {}),
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
      // body 不是 JSON，吃掉
    }
    handlers.onError?.({
      reason: "http",
      detail: detail || `HTTP ${resp.status}`,
    });
    handlers.onDone?.();
    return;
  }

  const respBody = resp.body;
  if (!respBody) {
    handlers.onError?.({ reason: "no_body" });
    handlers.onDone?.();
    return;
  }

  // ---- 超时看门狗 ----
  // 用 AbortController 链：内部 controller 触发时取消 reader.read()
  const watchdog = new AbortController();
  const onParentAbort = () => watchdog.abort();
  signal.addEventListener("abort", onParentAbort, { once: true });

  let timeoutHandle: ReturnType<typeof setTimeout> | null = null;
  let timedOutReason: "timeout_first_event" | "idle_timeout" | null = null;

  const armTimeout = (ms: number, reason: typeof timedOutReason) => {
    if (timeoutHandle) clearTimeout(timeoutHandle);
    timeoutHandle = setTimeout(() => {
      timedOutReason = reason;
      watchdog.abort();
    }, ms);
  };
  armTimeout(firstTimeoutMs, "timeout_first_event");

  const reader = respBody.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let receivedFirst = false;

  try {
    while (true) {
      // race：reader.read() vs watchdog.abort()
      const readPromise = reader.read();
      const abortPromise: Promise<never> = new Promise((_, reject) => {
        if (watchdog.signal.aborted) reject(new Error("__watchdog_aborted__"));
        else
          watchdog.signal.addEventListener(
            "abort",
            () => reject(new Error("__watchdog_aborted__")),
            { once: true },
          );
      });
      const { value, done } = (await Promise.race([
        readPromise,
        abortPromise,
      ])) as ReadableStreamReadResult<Uint8Array>;

      if (done) break;
      buf += decoder.decode(value, { stream: true });

      while (true) {
        const sep = findBlockSeparator(buf);
        if (sep.idx === -1) break;
        const block = buf.slice(0, sep.idx);
        buf = buf.slice(sep.idx + sep.len);
        const parsed = parseBlock(block);
        if (!parsed) continue;

        if (!receivedFirst) {
          receivedFirst = true;
        }
        // 任何事件到达后，重置看门狗为「空闲超时」
        armTimeout(idleTimeoutMs, "idle_timeout");

        handlers.onEvent?.(parsed);
        if (parsed.type === ("done" as SseEventType)) {
          if (timeoutHandle) clearTimeout(timeoutHandle);
          handlers.onDone?.();
          return;
        }
      }
    }

    // 流自然结束但没显式 done（健康但非预期）
    if (buf.trim()) {
      const parsed = parseBlock(buf);
      if (parsed) handlers.onEvent?.(parsed);
    }
    if (timeoutHandle) clearTimeout(timeoutHandle);
    handlers.onDone?.();
  } catch (e) {
    if (timeoutHandle) clearTimeout(timeoutHandle);
    if ((e as Error).name === "AbortError") return;

    if (timedOutReason) {
      handlers.onError?.({ reason: timedOutReason });
    } else if ((e as Error).message === "__watchdog_aborted__") {
      // signal 触发的 abort（用户主动取消），静默退出
      handlers.onDone?.();
      return;
    } else {
      handlers.onError?.({
        reason: "stream",
        detail: e instanceof Error ? e.message : String(e),
      });
    }
    handlers.onDone?.();
  } finally {
    signal.removeEventListener("abort", onParentAbort);
    try {
      // 主动释放 reader，否则连接会被挂起
      reader.releaseLock();
    } catch {
      // 已释放
    }
  }
}

/** 找下一个事件块的分隔符位置和长度。 */
export function findBlockSeparator(s: string): { idx: number; len: number } {
  // 标准分隔符是 \n\n（LF）或 \r\n\r\n（CRLF）。
  // 取较早出现的那个；同位置时优先 4 字节版本（更长 → 切得更干净）。
  const a = s.indexOf("\n\n");
  const b = s.indexOf("\r\n\r\n");
  if (a === -1 && b === -1) return { idx: -1, len: 0 };
  if (a === -1) return { idx: b, len: 4 };
  if (b === -1) return { idx: a, len: 2 };
  if (b <= a) return { idx: b, len: 4 };
  // 注意：\r\n\r\n 内部嵌套 \n\n 会让 a 指向 b+2 位置（不是真分隔）
  // 比如 "...\r\n\r\n..."：a = indexOf("\n\n") = b+2（即 \r[\n\r\n]）
  // 这种情况要走 b（4 字节版）才能正确切到块尾
  if (a === b + 2) return { idx: b, len: 4 };
  return { idx: a, len: 2 };
}

/** 解析单个 SSE 块。失败返 null。 */
export function parseBlock(block: string): SseEvent | null {
  let event: string | null = null;
  let data: string | null = null;
  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(":")) continue; // 注释 / keepalive
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
