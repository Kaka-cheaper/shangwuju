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
            <p className="text-red-400 text-sm mb-2">{connectionError}</p>
            <p className="text-ink-500 text-xs">请检查链接是否正确，或刷新重试</p>
          </>
        ) : (
          <>
            <p className="text-ink-400 text-sm mb-2">正在加入房间...</p>
            <p className="text-ink-600 text-xs">房间 ID：{roomId}</p>
          </>
        )}
      </RoomShell>
    );
  }

  return <AdaptiveAppShell />;
}

export function RoomShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[#08080d]">
      <div className="text-center">
        <div className="text-2xl mb-3">☕</div>
        {children}
      </div>
    </div>
  );
}

// ============================================================
// NicknameEntryCard —— ADR-0013 决策 6：进房卡片
// URL 无 nickname（非扫码深链）时，先让用户填昵称再进房，而不是拿「参与者」
// 默认值直接连进去。复用 RoomShell 的暗色壳，卡片本身也走暗色语言（RoomShell
// 背景是 #08080d，仓库里唯一的 .card 全局类是浅色主题，套在这里会跳色）。
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
      <div className="w-[280px] rounded-xl border border-white/10 bg-white/[0.04] backdrop-blur-sm px-5 py-6 text-left">
        <p className="text-ink-100 text-sm font-medium mb-1">加入房间</p>
        <p className="text-ink-500 text-xs mb-4">房间 ID：{roomId}</p>
        <label htmlFor="room-nickname-input" className="block text-ink-400 text-xs mb-1.5">
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
          className="w-full rounded-md border border-white/10 bg-white/5 px-3 py-2 text-sm text-ink-100 placeholder:text-ink-600 focus:outline-none focus:ring-1 focus:ring-accent-500/50 mb-3"
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
