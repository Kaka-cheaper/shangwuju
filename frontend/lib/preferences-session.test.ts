/**
 * refreshPreferences / resetUserMemory 的 session_id 传参行为（用户偏好面板
 * 全环方案 §1/§2.3/§14.4：闭环唯一必要修法）。
 *
 * 覆盖点：
 * 1. refreshPreferences 不传 override 时用 store 的个人 sessionId，拼进 GET
 *    query string。
 * 2. refreshPreferences 传 sessionIdOverride（房间模式）时用 override，
 *    不用个人 sessionId——房间面板要读房间累积键，不是个人会话键。
 * 3. resetUserMemory 把 session_id 放进 POST body（不是 query），且清空后
 *    用同一个 sessionIdOverride 重新刷新。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./sse", () => ({
  streamSse: vi.fn(async () => {}),
}));

import { useChatStore } from "./store";

describe("refreshPreferences / resetUserMemory session_id 传参", () => {
  let originalFetch: typeof globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          persona: { user_id: "u_dad", label: "新手爸爸", icon: "👨", notes: "", home_location: "", default_distance_max_km: 5, default_budget: 300, default_tags: { physical: [], dietary: [], experience: [], suitable_for_priority: [] } },
          memory: { user_id: "sess", accepted_tags: { counts: {} }, rejected_tags: { counts: {} }, distance_history: [] },
          top_priors: [],
          suggested_distance_max_km: null,
          recent_trips: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    useChatStore.setState({
      sessionId: "sess_personal",
      currentUserId: "u_dad",
      preferences: null,
      toasts: [],
    });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("refreshPreferences 不传 override 用个人 sessionId", async () => {
    await useChatStore.getState().refreshPreferences();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("/preferences/u_dad");
    expect(url).toContain("session_id=sess_personal");
  });

  it("refreshPreferences 传 override（房间会话键）时不用个人 sessionId", async () => {
    await useChatStore.getState().refreshPreferences("collab_room123");
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("session_id=collab_room123");
    expect(url).not.toContain("sess_personal");
  });

  it("resetUserMemory 把 session_id 放进 POST body", async () => {
    await useChatStore.getState().resetUserMemory();
    // 第一次调用是 POST /reset，第二次是 reset 内部触发的 refreshPreferences
    const resetCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes("/reset"),
    );
    expect(resetCall).toBeDefined();
    const [url, options] = resetCall as [string, RequestInit];
    expect(url).not.toContain("session_id="); // 不在 query 里
    expect(options.method).toBe("POST");
    const body = JSON.parse(options.body as string);
    expect(body.session_id).toBe("sess_personal");
  });

  it("resetUserMemory 传 override 时 body 与后续 refresh 都用该 override", async () => {
    await useChatStore.getState().resetUserMemory("collab_roomABC");
    const resetCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes("/reset"),
    );
    const [, options] = resetCall as [string, RequestInit];
    const body = JSON.parse(options.body as string);
    expect(body.session_id).toBe("collab_roomABC");

    const refreshCall = fetchMock.mock.calls.find(
      (c) => !String(c[0]).includes("/reset"),
    );
    expect(String(refreshCall?.[0])).toContain("session_id=collab_roomABC");
  });

  it("ignores stale refreshPreferences responses after persona changes", async () => {
    let resolveFetch!: (value: Response) => void;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );

    const pending = useChatStore.getState().refreshPreferences("collab_room123");
    useChatStore.setState({ currentUserId: "u_biz", preferences: null });

    resolveFetch(
      new Response(
        JSON.stringify({
          persona: { user_id: "u_dad", label: "New dad", icon: "baby", notes: "", home_location: "", default_distance_max_km: 5, default_budget: 300, default_tags: { physical: [], dietary: [], experience: [], suitable_for_priority: [] } },
          memory: { user_id: "sess", accepted_tags: { counts: {} }, rejected_tags: { counts: {} }, distance_history: [] },
          top_priors: [],
          suggested_distance_max_km: null,
          recent_trips: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await pending;

    expect(useChatStore.getState().preferences).toBeNull();
  });
});
