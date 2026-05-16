/**
 * /chat/refine + X-Planner-Mode 端到端联调脚本（W3 C3/C4）。
 *
 * 流程：
 * 1. POST /chat/stream → 获取 session
 * 2. POST /chat/refine（同 session_id + 反馈文本）
 *    - 断言收到 refinement_start → refinement_done(含 changed_fields) → 完整主路径 → done
 *    - 断言后端响应头回显 X-Planner-Mode（验证 header 透传）
 * 3. POST /chat/refine（mode=llm header）→ 后端响应头应回显 llm
 *
 * 用法：
 *   后端先起：cd backend && uv run uvicorn main:app --port 8000
 *   再跑：node frontend/scripts/verify-refine.mjs
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

function findSep(s) {
  const a = s.indexOf("\n\n");
  const b = s.indexOf("\r\n\r\n");
  if (a === -1 && b === -1) return { idx: -1, len: 0 };
  if (a === -1) return { idx: b, len: 4 };
  if (b === -1) return { idx: a, len: 2 };
  if (b <= a) return { idx: b, len: 4 };
  if (a === b + 2) return { idx: b, len: 4 };
  return { idx: a, len: 2 };
}

async function consume(path, body, headers = {}) {
  const counters = { events: 0, types: {} };
  const errors = [];
  let intent = null;
  let itinerary = null;
  let refinement = null;

  const r = await fetch(API_BASE + path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...headers,
    },
    body: JSON.stringify(body),
  });
  const respHeaders = Object.fromEntries(r.headers.entries());
  if (!r.ok) {
    let detail = "";
    try {
      detail = (await r.json()).detail ?? "";
    } catch {
      detail = `HTTP ${r.status}`;
    }
    return { counters, errors: [`HTTP ${r.status}: ${detail}`], respHeaders };
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
        if (etype === "refinement_done") refinement = parsed.payload;
      } catch (e) {
        errors.push(`parse: ${e.message}`);
      }
    }
  }
  return { counters, errors, intent, itinerary, refinement, respHeaders };
}

async function main() {
  const sessionId = `verify_refine_${Date.now()}`;

  // 1. /chat/stream（rule 模式）
  console.log("=== /chat/stream（默认 rule 模式）===");
  const r1 = await consume("/chat/stream", {
    message:
      "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
    session_id: sessionId,
    scenario_id: "S1",
  });
  if (r1.errors.length) {
    console.error("× /chat/stream:", r1.errors);
    process.exit(1);
  }
  console.log(`  ✓ 收 ${r1.counters.events} 条事件`);
  console.log(`  ✓ X-Planner-Mode 响应头：${r1.respHeaders["x-planner-mode"]}`);
  if (r1.respHeaders["x-planner-mode"] !== "rule") {
    console.error("  × 默认应回 rule，实际：", r1.respHeaders["x-planner-mode"]);
    process.exit(1);
  }

  // 2. /chat/refine（同 session）
  console.log("\n=== /chat/refine（feedback=太远了 + X-Planner-Mode=llm）===");
  const r2 = await consume(
    "/chat/refine",
    { session_id: sessionId, feedback_text: "太远了，希望 3 公里以内" },
    { "X-Planner-Mode": "llm" },
  );
  if (r2.errors.length) {
    console.error("× /chat/refine:", r2.errors);
    process.exit(1);
  }
  console.log(`  ✓ 收 ${r2.counters.events} 条事件`);
  console.log(`  ✓ 事件类型：`, r2.counters.types);
  if (!r2.counters.types.refinement_start) {
    console.error("  × 缺 refinement_start");
    process.exit(1);
  }
  if (!r2.counters.types.refinement_done) {
    console.error("  × 缺 refinement_done");
    process.exit(1);
  }
  if (!r2.refinement?.changed_fields?.length) {
    console.error("  × refinement_done.changed_fields 为空");
    process.exit(1);
  }
  console.log(
    `  ✓ changed_fields：${JSON.stringify(r2.refinement.changed_fields)}`,
  );
  if (r2.refinement.refiner_note) {
    console.log(`  ✓ refiner_note：${r2.refinement.refiner_note}`);
  }
  console.log(`  ✓ X-Planner-Mode 响应头：${r2.respHeaders["x-planner-mode"]}`);
  if (r2.respHeaders["x-planner-mode"] !== "llm") {
    console.error("  × header 透传失效，期望 llm，实际：", r2.respHeaders["x-planner-mode"]);
    process.exit(1);
  }
  // refined_intent 应当反映距离调整
  const newDist = r2.refinement?.refined_intent?.distance_max_km;
  if (typeof newDist !== "number") {
    console.error("  × refined_intent.distance_max_km 缺失");
    process.exit(1);
  }
  console.log(
    `  ✓ refined_intent.distance_max_km = ${newDist}（原 5）`,
  );

  // 3. /chat/refine（非法 session_id）
  console.log("\n=== /chat/refine（非法 session_id 应返 422）===");
  const r3 = await consume("/chat/refine", {
    session_id: "no_such_session",
    feedback_text: "x",
  });
  if (r3.errors.length === 0 || !r3.errors[0].includes("422")) {
    console.error("  × 期望 HTTP 422，实际：", r3);
    process.exit(1);
  }
  console.log(`  ✓ ${r3.errors[0]}`);

  console.log("\n✓ /chat/refine + X-Planner-Mode header 透传 全部通过");
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});
