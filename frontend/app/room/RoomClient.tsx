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

import HomeView from "@/components/HomeView";
import { useCollabStore } from "@/lib/collab-store";

interface RoomClientProps {
  roomIdFromPath?: string;
}

export default function RoomClient({ roomIdFromPath }: RoomClientProps) {
  const searchParams = useSearchParams();
  const queryRoomId = searchParams.get("room_id") || searchParams.get("id") || "";
  const roomId = roomIdFromPath || queryRoomId;
  const nickname = searchParams.get("nickname") || "参与者";
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

  useEffect(() => {
    if (roomId && userId) {
      joinRoom(roomId, userId, nickname);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, userId, nickname]);

  if (!roomId) {
    return (
      <RoomShell>
        <p className="text-red-400 text-sm mb-2">缺少房间 ID</p>
        <p className="text-ink-500 text-xs">请从邀请链接重新进入</p>
      </RoomShell>
    );
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

  return <HomeView />;
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
