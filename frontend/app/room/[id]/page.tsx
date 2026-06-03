"use client";

/**
 * 协作房间入口页：参与者通过分享链接进入。
 * URL: /room/{roomId}?nickname=老婆
 *
 * 流程：
 * 1. 从 URL 取 roomId
 * 2. 生成或读取 userId（localStorage）
 * 3. 自动建立 WS 连接加入房间
 * 4. 渲染协作版 HomeView（带 CollabBar + VoteButtons + ConstraintFeed）
 */

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { useCollabStore } from "@/lib/collab-store";
import { useChatStore } from "@/lib/store";
import HomeView from "@/components/HomeView";

export default function RoomPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const roomId = params.id as string;
  const nickname = searchParams.get("nickname") || "参与者";
  const queryUserId = searchParams.get("user_id");

  const joinRoom = useCollabStore((s) => s.joinRoom);
  const collabMode = useCollabStore((s) => s.collabMode);
  const connected = useCollabStore((s) => s.connected);
  const connectionError = useCollabStore((s) => s.connectionError);

  const [userId] = useState(() => {
    if (queryUserId?.trim()) return queryUserId.trim();
    // 从 localStorage 读或生成
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

  // 连接中状态
  if (!collabMode || !connected) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#08080d]">
        <div className="text-center">
          <div className="text-2xl mb-3">☕</div>
          {connectionError ? (
            <>
              <p className="text-red-400 text-sm mb-2">{connectionError}</p>
              <p className="text-ink-500 text-xs">请检查链接是否正确，或刷新重试</p>
            </>
          ) : (
            <>
              <p className="text-ink-400 text-sm mb-2">正在加入房间…</p>
              <p className="text-ink-600 text-xs">房间 ID：{roomId}</p>
            </>
          )}
        </div>
      </div>
    );
  }

  // 已连接 → 渲染主页面（HomeView 会根据 collabMode 显示协作 UI）
  return <HomeView />;
}
