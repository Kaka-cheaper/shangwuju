"use client";

/**
 * 协作房间客户端入口。
 *
 * 支持两种 URL：
 * - /room?room_id={roomId}&nickname=老婆      GitHub Pages 静态导出可用
 * - /room/{roomId}?nickname=老婆             本地 / Node 部署兼容入口
 */

import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";

import AdaptiveAppShell from "@/components/AdaptiveAppShell";
import { useCollabStore } from "@/lib/collab-store";

interface RoomClientProps {
  roomIdFromPath?: string;
}

export default function RoomClient({ roomIdFromPath }: RoomClientProps) {
  const searchParams = useSearchParams();
  const queryRoomId = searchParams.get("room_id") || searchParams.get("id") || "";
  const roomId = roomIdFromPath || queryRoomId;
  // 深链昵称（扫码邀请场景）：URL 带 nickname 时保持原「无摩擦直接进房」行为。
  const nicknameFromUrl = (searchParams.get("nickname") || "").trim();
  const queryUserId = searchParams.get("user_id");

  const joinRoom = useCollabStore((s) => s.joinRoom);
  const collabMode = useCollabStore((s) => s.collabMode);
  const connected = useCollabStore((s) => s.connected);
  const connectionError = useCollabStore((s) => s.connectionError);
  const [userId] = useState(() => {
    if (queryUserId?.trim()) return queryUserId.trim();
    if (typeof window === "undefined") return "anon";
    const stored = localStorage.getItem("collab_user_id");
    if (stored) return stored;
    const newId = `u_${Date.now().toString(36)}`;
    localStorage.setItem("collab_user_id", newId);
    return newId;
  });

  // ADR-0013 决策 6：URL 无 nickname 时不再默认「参与者」直接进房——先在进房卡片
  // 收集昵称，用户提交后才落到这里触发 joinRoom。手动提交的昵称只在本次会话内存活
  // （不持久化，房散即毁，呼应决策 6「身份归属房间」的方向）。
  const [manualNickname, setManualNickname] = useState<string | null>(null);
  const effectiveNickname = nicknameFromUrl || manualNickname || "";

  useEffect(() => {
    if (roomId && userId && effectiveNickname) {
      joinRoom(roomId, userId, effectiveNickname);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, userId, effectiveNickname]);

  if (!roomId) {
    return (
      <RoomShell>
        <p className="text-red-400 text-sm mb-2">缺少房间 ID</p>
        <p className="text-ink-500 text-xs">请从邀请链接重新进入</p>
      </RoomShell>
    );
  }

  // 没有深链昵称、也还没提交进房卡片 → 先收集昵称，不自动 joinRoom。
  if (!nicknameFromUrl && !manualNickname) {
    return <NicknameEntryCard roomId={roomId} onSubmit={setManualNickname} />;
  }

  if (!collabMode || !connected) {
    return (
      <RoomShell>
        {connectionError ? (
          <>
            <p className="text-red-500 text-sm font-medium mb-2">{connectionError}</p>
            <p className="text-ink-500 text-xs">请检查链接是否正确，或刷新重试</p>
          </>
        ) : (
          <>
            <p className="text-ink-700 text-sm mb-2">正在加入房间...</p>
            <p className="text-ink-400 text-xs">房间 ID：{roomId}</p>
          </>
        )}
      </RoomShell>
    );
  }

  return <AdaptiveAppShell />;
}

export function RoomShell({ children }: { children: ReactNode }) {
  return (
    <div
      className="min-h-screen flex items-center justify-center px-4"
      style={{
        // 与主 app 一致的暖色亮调（白底 + 暖黄光晕），不再是全 app 唯一的黑屏
        // ——评委扫码进房间视觉连贯，还是那个暖暖的晌午局（2026-07-12 重主题）。
        background:
          "radial-gradient(circle at 28% 18%, rgb(255 209 0 / 0.20), transparent 55%), radial-gradient(circle at 78% 88%, rgb(245 158 11 / 0.15), transparent 60%), #fffdf7",
      }}
    >
      <div className="text-center">
        <div className="text-3xl mb-3">☕</div>
        {children}
      </div>
    </div>
  );
}

// ============================================================
// NicknameEntryCard —— ADR-0013 决策 6：进房卡片
// URL 无 nickname（非扫码深链）时，先让用户填昵称再进房，而不是拿「参与者」
// 默认值直接连进去。卡片走与主 app 一致的暖色亮调（亮玻璃卡 + 焦糖描边 +
// 金色 btn-primary），配合 RoomShell 的暖色背景（2026-07-12 从黑屏重主题）。
// ============================================================

function NicknameEntryCard({
  roomId,
  onSubmit,
}: {
  roomId: string;
  onSubmit: (nickname: string) => void;
}) {
  const [value, setValue] = useState("");
  const trimmed = value.trim();
  const valid = trimmed.length >= 1 && trimmed.length <= 12;

  const submit = () => {
    if (!valid) return;
    onSubmit(trimmed);
  };

  return (
    <RoomShell>
      <div className="w-full max-w-[320px] rounded-[20px] border border-black/[0.08] bg-white/90 backdrop-blur-xl px-5 py-6 text-left shadow-[0_24px_60px_-40px_rgba(17,24,39,0.4)]">
        <p className="text-ink-900 text-base font-bold tracking-tight mb-1">加入房间</p>
        <p className="text-ink-400 text-xs mb-4">房间 ID：{roomId}</p>
        <label htmlFor="room-nickname-input" className="block text-ink-500 text-xs mb-1.5">
          你的昵称
        </label>
        <input
          id="room-nickname-input"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          maxLength={12}
          placeholder="1-12 字，例如「老婆」"
          className="w-full rounded-lg border border-black/[0.1] bg-white px-3 py-2.5 text-sm text-ink-900 placeholder:text-ink-400 focus:outline-none focus:ring-2 focus:ring-accent-400/40 mb-3"
        />
        <button
          type="button"
          disabled={!valid}
          onClick={submit}
          className="btn-primary w-full disabled:opacity-40 disabled:cursor-not-allowed"
        >
          进入房间
        </button>
      </div>
    </RoomShell>
  );
}
