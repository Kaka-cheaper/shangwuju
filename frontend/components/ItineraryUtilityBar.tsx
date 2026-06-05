"use client";

import { useState } from "react";

import { Icons } from "@/lib/icon-map";
import {
  buildCollabChatStateSnapshot,
  buildCollabPlanningEvents,
  useCollabStore,
} from "@/lib/collab-store";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

import PosterGenerator from "./PosterGenerator";
import TtsPlayer from "./TtsPlayer";

interface ItineraryUtilityBarProps {
  onOpenShareModal: () => void;
}

export default function ItineraryUtilityBar({
  onOpenShareModal,
}: ItineraryUtilityBarProps) {
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);
  const pushToast = useChatStore((s) => s.pushToast);

  const collabMode = useCollabStore((s) => s.collabMode);
  const roomId = useCollabStore((s) => s.roomId);
  const createRoom = useCollabStore((s) => s.createRoom);
  const joinRoom = useCollabStore((s) => s.joinRoom);
  const [creatingRoom, setCreatingRoom] = useState(false);

  if (!itinerary) return null;

  const hasOrders = itinerary.orders.length > 0;
  const canCreateRoom = !hasOrders && !cancelled && !collabMode;
  const showShareRoom = collabMode && !!roomId;

  const handleCreateRoom = async () => {
    if (creatingRoom || streaming) return;

    setCreatingRoom(true);
    try {
      const state = useChatStore.getState();
      const userId = state.currentUserId || "demo_user";
      const planningEvents = buildCollabPlanningEvents(state);
      const chatState = buildCollabChatStateSnapshot(state);
      const newRoomId = await createRoom(
        userId,
        "发起人",
        state.sessionId,
        planningEvents,
        state.messages as unknown as Record<string, unknown>[],
        chatState,
      );

      if (!newRoomId) {
        pushToast({ kind: "warn", text: "多人房间创建失败" });
        return;
      }

      joinRoom(newRoomId, userId, "发起人");
      onOpenShareModal();
    } finally {
      setCreatingRoom(false);
    }
  };

  return (
    <div className="card px-3 py-3 animate-fade-in">
      <div className="flex flex-col xl:flex-row xl:items-center gap-3">
        <div className="min-w-0 xl:w-36">
          <div className="flex items-center gap-1.5">
            <Icons.wrench className="w-3.5 h-3.5 text-ink-700" strokeWidth={2} />
            <span className="text-sm font-semibold text-ink-900 tracking-tight">方案工具</span>
          </div>
          <div className="mt-0.5 text-xs text-ink-500 truncate">
            {showShareRoom
              ? `协作房间 ${roomId}`
              : hasOrders
                ? "行程已确认"
                : "播报 / 海报 / 协作"}
          </div>
        </div>

        <div className="grid flex-1 grid-cols-1 sm:grid-cols-3 gap-2">
          <TtsPlayer compact />
          <PosterGenerator compact />

          {canCreateRoom && (
            <button
              type="button"
              className={cn(
                "h-10 w-full rounded-full px-3",
                "bg-[#FFD100] hover:bg-[#ffe552]",
                "border border-[#e6bc00]",
                "text-base font-medium text-ink-900",
                "transition-all disabled:cursor-not-allowed disabled:opacity-50",
                "flex items-center justify-center gap-1.5",
              )}
              disabled={creatingRoom || streaming}
              title="创建多人协作房间"
              onClick={() => void handleCreateRoom()}
            >
              {creatingRoom ? (
                <>
                  <Icons.thinking className="h-3.5 w-3.5 animate-spin" />
                  <span>创建中</span>
                </>
              ) : (
                <>
                  <Icons.users className="h-3.5 w-3.5 text-ink-900" />
                  <span>开多人房间</span>
                </>
              )}
            </button>
          )}

          {showShareRoom && (
            <button
              type="button"
              className={cn(
                "h-9 w-full rounded-md px-3",
                "border border-black/[0.08] bg-black/[0.03] hover:bg-black/[0.05]",
                "text-sm font-medium text-ink-700 hover:text-ink-900",
                "transition-all flex items-center justify-center gap-1.5",
              )}
              onClick={onOpenShareModal}
              title="分享协作房间链接"
            >
              <Icons.share className="h-3.5 w-3.5 text-brand-600" />
              <span>分享房间</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
