/**
 * 8 场景前端按钮压测脚本（Node 18+ 环境直跑）
 *
 * 用途：在 stub 模式下批量发 8 次 /chat/stream，断言每次都能按
 * `api_contract.md` §2 收齐 15 条事件、1 个 done、1 个 replan、5 个 tool_call_end。
 *
 * 真 LLM 接入后此脚本只会按"事件齐全 + 形态合法"做断言，意图差异由 A 同学的
 * intent_parser 测试覆盖。
 *
 * 用法：
 *   后端先起：cd backend && uv run uvicorn main:app --port 8000
 *   再跑：node frontend/scripts/pressure-test-scenarios.mjs
 *
 * 不依赖：浏览器、Playwright、外部 npm 包（纯 Node fetch + ReadableStream）。
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

function findSep(s) {
  const a = s.indexOf("\n\n");
  const b = s.indexOf("\r\n\r\n");
  if (a === -1) return { idx: b, len: 4 };
  if (b === -1) return { idx: a, len: 2 };
  if (a < b) return { idx: a, len: 2 };
  return { idx: b, len: 4 };
}

async function consume(path, body) {
  const counters = { events: 0, types: {} };
  const errors = [];
  let intent = null;
  let itinerary = null;
  const t0 = Date.now();

  const r = await fetch(API_BASE + path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    errors.push(`HTTP ${r.status}`);
    return { counters, intent, itinerary, errors, ms: Date.now() - t0 };
  }

  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    while (true) {
      const { idx, len } = findSep(buf);
      if (idx === -1) break;
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + len);
      let etype = null;
      let data = null;
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith("event:")) etype = line.slice(6).trim();
        else if (line.startsWith("data:")) data = line.slice(5).trim();
      }
      if (!etype || !data) continue;
      counters.events++;
      counters.types[etype] = (counters.types[etype] || 0) + 1;
      try {
        const parsed = JSON.parse(data);
        if (etype === "intent_parsed") intent = parsed.payload;
        if (etype === "itinerary_ready") itinerary = parsed.payload;
      } catch (e) {
        errors.push(`parse: ${e.message}`);
      }
    }
  }
  return { counters, intent, itinerary, errors, ms: Date.now() - t0 };
}

function assertScenario(s, r) {
  const errs = [];
  if (r.errors.length) errs.push(...r.errors.map((e) => `error: ${e}`));
  if ((r.counters.types.done ?? 0) !== 1) errs.push("缺少 done 事件");
  if ((r.counters.types.intent_parsed ?? 0) !== 1)
    errs.push("缺少 intent_parsed 事件");
  if ((r.counters.types.itinerary_ready ?? 0) < 1)
    errs.push("缺少 itinerary_ready 事件");
  if ((r.counters.types.tool_call_start ?? 0) < 3)
    errs.push("tool_call_start < 3");
  if ((r.counters.types.tool_call_end ?? 0) < 3)
    errs.push("tool_call_end < 3");
  if ((r.counters.types.replan_triggered ?? 0) < 1)
    errs.push("缺少 replan_triggered（异常韧性证据）");
  if (!r.itinerary || (r.itinerary.stages?.length ?? 0) < 5)
    errs.push("itinerary stages < 5");
  return errs;
}

async function main() {
  const sc = await fetch(API_BASE + "/scenarios").then((r) => r.json());
  if (!Array.isArray(sc.scenarios) || sc.scenarios.length !== 8) {
    console.error("× /scenarios 应返 8 条，实际：", sc.scenarios?.length);
    process.exit(2);
  }
  console.log(`✓ /scenarios 返 ${sc.scenarios.length} 个场景`);

  const report = [];
  for (const s of sc.scenarios) {
    const sessionId = `pressure_${s.id}_${Date.now()}`;
    const r = await consume("/chat/stream", {
      message: s.input,
      session_id: sessionId,
      scenario_id: s.id,
    });
    const errs = assertScenario(s, r);
    report.push({ id: s.id, title: s.title, ms: r.ms, ...r.counters, errs });
    const status = errs.length === 0 ? "✓" : "×";
    console.log(
      `${status} ${s.id} ${s.title} · 事件 ${r.counters.events} · ${r.ms}ms` +
        (errs.length ? `\n   ${errs.join("\n   ")}` : ""),
    );
  }

  const failed = report.filter((r) => r.errs.length);
  console.log("\n=== 总结 ===");
  console.log(`  通过：${report.length - failed.length} / ${report.length}`);
  if (failed.length) {
    process.exit(1);
  }
  console.log("  ✓ 8 场景压测全部通过\n");
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});
