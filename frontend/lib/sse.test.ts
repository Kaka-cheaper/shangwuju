/**
 * SSE 解析器鲁棒性单测。
 *
 * 覆盖：
 * - 粘围栏：一个 chunk 内多事件块
 * - 长 token：data 跨多个 chunk
 * - CRLF / LF 双格式
 * - data 多行 join
 * - 注释行（: keepalive）跳过
 * - 非法 JSON 安全降级
 * - HTTP 错误 / no_body / network 错误
 * - 首字节超时 / 空闲超时
 * - 主动 abort
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import {
  findBlockSeparator,
  parseBlock,
  streamSse,
  type SseStreamError,
} from "./sse";

// ============================================================
// 纯函数：findBlockSeparator
// ============================================================

describe("findBlockSeparator", () => {
  it("空串返 -1", () => {
    expect(findBlockSeparator("")).toEqual({ idx: -1, len: 0 });
  });

  it("纯 LF 分隔", () => {
    const s = "event: x\ndata: y\n\nevent: z\ndata: w\n\n";
    const r = findBlockSeparator(s);
    expect(r.idx).toBe(s.indexOf("\n\n"));
    expect(r.len).toBe(2);
  });

  it("纯 CRLF 分隔", () => {
    const s = "event: x\r\ndata: y\r\n\r\nevent: z\r\ndata: w\r\n\r\n";
    const r = findBlockSeparator(s);
    expect(r.idx).toBe(s.indexOf("\r\n\r\n"));
    expect(r.len).toBe(4);
  });

  it("CRLF 混 LF 优先 CRLF（即同位置返 4 字节）", () => {
    // sse-starlette 默认输出 \r\n\r\n；naive 实现会把 \r[\n\n] 当成 LF 分隔切坏
    const s = "event: x\r\ndata: y\r\n\r\n";
    const r = findBlockSeparator(s);
    expect(r.len).toBe(4);
    expect(s.slice(0, r.idx)).toBe("event: x\r\ndata: y");
  });
});

// ============================================================
// 纯函数：parseBlock
// ============================================================

describe("parseBlock", () => {
  it("正常 LF 块", () => {
    const r = parseBlock(
      'event: intent_parsed\ndata: {"type":"intent_parsed","seq":0,"payload":{}}',
    );
    expect(r?.type).toBe("intent_parsed");
    expect(r?.seq).toBe(0);
  });

  it("正常 CRLF 块", () => {
    const r = parseBlock(
      'event: tool_call_start\r\ndata: {"type":"tool_call_start","seq":1,"payload":{"tool":"x","input":{}}}',
    );
    expect(r?.type).toBe("tool_call_start");
  });

  it("data 多行 join 后能解析", () => {
    const r = parseBlock(
      "event: intent_parsed\n" +
        'data: {"type":"intent_parsed",\n' +
        'data: "seq":0,\n' +
        'data: "payload":{}}',
    );
    expect(r?.type).toBe("intent_parsed");
  });

  it("含 : 开头注释行不影响", () => {
    const r = parseBlock(
      ":keepalive\nevent: done\ndata: {\"type\":\"done\",\"seq\":99,\"payload\":{}}",
    );
    expect(r?.type).toBe("done");
  });

  it("id: 开头行被忽略（SSE id 字段）", () => {
    const r = parseBlock(
      'id: 5\nevent: agent_thought\ndata: {"type":"agent_thought","seq":5,"payload":{"text":"x"}}',
    );
    expect(r?.type).toBe("agent_thought");
  });

  it("缺 event 字段 → null", () => {
    expect(parseBlock('data: {"type":"x","seq":0,"payload":{}}')).toBeNull();
  });

  it("缺 data 字段 → null", () => {
    expect(parseBlock("event: foo")).toBeNull();
  });

  it("非法 JSON → null（不抛）", () => {
    expect(parseBlock("event: x\ndata: {not json}")).toBeNull();
  });

  it("data 含转义换行不破坏（JSON 编码内的 \\n 与块分隔无关）", () => {
    const r = parseBlock(
      'event: agent_thought\ndata: {"type":"agent_thought","seq":1,"payload":{"text":"行1\\n行2"}}',
    );
    expect(r?.type).toBe("agent_thought");
    expect((r?.payload as { text: string }).text).toBe("行1\n行2");
  });
});

// ============================================================
// streamSse 鲁棒性（mock fetch）
// ============================================================

/** 用 ReadableStream 构造一个发送指定字节序列的 mock body。 */
function bodyFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    async pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(enc.encode(chunks[i++]));
    },
  });
}

/** 类似 bodyFromChunks，但每 chunk 之间 sleep 让事件循环喘口气。 */
function bodyFromDelayedChunks(
  chunks: string[],
  delayMs: number,
): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    async pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      await new Promise((r) => setTimeout(r, delayMs));
      controller.enqueue(enc.encode(chunks[i++]));
    },
  });
}

/** 永远不发送数据的 body，用于测试首字节超时。 */
function neverEndingBody(): ReadableStream<Uint8Array> {
  return new ReadableStream({
    pull() {
      /* noop, never enqueue */
    },
  });
}

function makeOkResp(body: ReadableStream<Uint8Array>): Response {
  // node 18+ 支持 Response 直接接受 ReadableStream
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function makeHandlers() {
  const events: unknown[] = [];
  const errors: SseStreamError[] = [];
  let doneCalls = 0;
  return {
    handlers: {
      onEvent: (ev: unknown) => events.push(ev),
      onError: (e: SseStreamError) => errors.push(e),
      onDone: () => doneCalls++,
    },
    events,
    errors,
    get doneCalls() {
      return doneCalls;
    },
  };
}

describe("streamSse 鲁棒性", () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("粘围栏：一个 chunk 多个事件块（LF）", async () => {
    const ev1 = 'event: e1\ndata: {"type":"e1","seq":0,"payload":{}}\n\n';
    const ev2 = 'event: e2\ndata: {"type":"e2","seq":1,"payload":{}}\n\n';
    const ev3 = 'event: done\ndata: {"type":"done","seq":2,"payload":{}}\n\n';
    globalThis.fetch = vi.fn(async () =>
      makeOkResp(bodyFromChunks([ev1 + ev2 + ev3])),
    ) as never;

    const h = makeHandlers();
    await streamSse("http://t", {}, new AbortController().signal, h.handlers);

    expect(h.events).toHaveLength(3);
    expect((h.events[0] as { type: string }).type).toBe("e1");
    expect((h.events[2] as { type: string }).type).toBe("done");
    expect(h.errors).toHaveLength(0);
    expect(h.doneCalls).toBe(1);
  });

  it("粘围栏：sse-starlette 风格 CRLF", async () => {
    // 真后端的实际格式
    const block =
      "id: 0\r\nevent: e1\r\ndata: {\"type\":\"e1\",\"seq\":0,\"payload\":{}}\r\n\r\n" +
      "id: 1\r\nevent: done\r\ndata: {\"type\":\"done\",\"seq\":1,\"payload\":{}}\r\n\r\n";
    globalThis.fetch = vi.fn(async () =>
      makeOkResp(bodyFromChunks([block])),
    ) as never;

    const h = makeHandlers();
    await streamSse("http://t", {}, new AbortController().signal, h.handlers);

    expect(h.events).toHaveLength(2);
    expect((h.events[1] as { type: string }).type).toBe("done");
  });

  it("长 token：data 字段被切到两个 chunk", async () => {
    // 这里把 SseEvent JSON 故意切在 payload 中间字节
    const full =
      'event: itinerary_ready\r\ndata: {"type":"itinerary_ready","seq":13,"payload":{"summary":"AAAAAAAAAAAAAAA","stages":[],"orders":[],"total_minutes":300}}\r\n\r\n';
    const half = Math.floor(full.length / 2);
    globalThis.fetch = vi.fn(async () =>
      makeOkResp(bodyFromChunks([full.slice(0, half), full.slice(half)])),
    ) as never;

    const h = makeHandlers();
    await streamSse("http://t", {}, new AbortController().signal, h.handlers);

    expect(h.events).toHaveLength(1);
    expect((h.events[0] as { type: string }).type).toBe("itinerary_ready");
  });

  it("分隔符跨 chunk 边界（\\r\\n\\r\\n 被切成两半）", async () => {
    const block =
      "event: e1\r\ndata: {\"type\":\"e1\",\"seq\":0,\"payload\":{}}\r\n\r\nevent: done\r\ndata: {\"type\":\"done\",\"seq\":1,\"payload\":{}}\r\n\r\n";
    // 切在 \r\n[break]\r\n
    const cut = block.indexOf("\r\n\r\n") + 2;
    globalThis.fetch = vi.fn(async () =>
      makeOkResp(bodyFromChunks([block.slice(0, cut), block.slice(cut)])),
    ) as never;

    const h = makeHandlers();
    await streamSse("http://t", {}, new AbortController().signal, h.handlers);

    expect(h.events).toHaveLength(2);
    expect(h.errors).toHaveLength(0);
  });

  it("HTTP 500 → onError(http) + onDone", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "boom" }), { status: 500 }),
    ) as never;

    const h = makeHandlers();
    await streamSse("http://t", {}, new AbortController().signal, h.handlers);

    expect(h.errors).toHaveLength(1);
    expect(h.errors[0].reason).toBe("http");
    expect(h.errors[0].detail).toBe("boom");
    expect(h.doneCalls).toBe(1);
  });

  it("HTTP 422 但 body 不是 JSON", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response("plain text", { status: 422 }),
    ) as never;

    const h = makeHandlers();
    await streamSse("http://t", {}, new AbortController().signal, h.handlers);

    expect(h.errors[0].reason).toBe("http");
    expect(h.errors[0].detail).toBe("HTTP 422");
  });

  it("network 错误（fetch 直接 throw）", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    }) as never;

    const h = makeHandlers();
    await streamSse("http://t", {}, new AbortController().signal, h.handlers);

    expect(h.errors[0].reason).toBe("network");
    expect(h.errors[0].detail).toContain("ECONNREFUSED");
  });

  it("首字节超时", async () => {
    globalThis.fetch = vi.fn(async () =>
      makeOkResp(neverEndingBody()),
    ) as never;

    const h = makeHandlers();
    await streamSse(
      "http://t",
      {},
      new AbortController().signal,
      h.handlers,
      { firstEventTimeoutMs: 80, idleTimeoutMs: 1000 },
    );

    expect(h.errors[0]?.reason).toBe("timeout_first_event");
    expect(h.doneCalls).toBe(1);
  });

  it("空闲超时：第一条事件后久不送下一条", async () => {
    const ev = 'event: e1\ndata: {"type":"e1","seq":0,"payload":{}}\n\n';
    globalThis.fetch = vi.fn(async () =>
      makeOkResp(bodyFromDelayedChunks([ev, ev, ev], 200)),
    ) as never;

    const h = makeHandlers();
    await streamSse(
      "http://t",
      {},
      new AbortController().signal,
      h.handlers,
      { firstEventTimeoutMs: 1000, idleTimeoutMs: 80 },
    );

    expect(h.events.length).toBeGreaterThanOrEqual(1);
    expect(h.errors[0]?.reason).toBe("idle_timeout");
  });

  it("主动 abort：静默退出，不报 error", async () => {
    const ev = 'event: e1\ndata: {"type":"e1","seq":0,"payload":{}}\n\n';
    globalThis.fetch = vi.fn(async () =>
      makeOkResp(bodyFromDelayedChunks([ev, ev, ev], 100)),
    ) as never;

    const ac = new AbortController();
    const h = makeHandlers();
    const p = streamSse("http://t", {}, ac.signal, h.handlers);

    setTimeout(() => ac.abort(), 50);
    await p;

    // abort 路径既不该 onError，也应触发 onDone
    expect(h.errors).toHaveLength(0);
    expect(h.doneCalls).toBe(1);
  });
});
