"use client";

/**
 * 行程卡片每段的赞/踩按钮。
 * 协作模式下嵌入 ItineraryCard 的每个 stage 右侧。
 */

import { ThumbsDown, ThumbsUp } from "lucide-react";

import { useCollabStore, type VoteAction } from "@/lib/collab-store";
import { cn } from "@/lib/utils";

interface VoteButtonsProps {
  stageIndex: number;
}

export default function VoteButtons({ stageIndex }: VoteButtonsProps) {
  const collabMode = useCollabStore((s) => s.collabMode);
  const votes = useCollabStore((s) => s.votes);
  const myUserId = useCollabStore((s) => s.myUserId);
  const members = useCollabStore((s) => s.members);
  const sendVote = useCollabStore((s) => s.sendVote);

  if (!collabMode) return null;

  const stageVotes = votes[stageIndex] || {};
  const myVote = myUserId ? stageVotes[myUserId] : null;
  const likeCount = Object.values(stageVotes).filter((v) => v === "like").length;
  const dislikeCount = Object.values(stageVotes).filter((v) => v === "dislike").length;

  // 获取投票者昵称
  const getVoterNames = (action: VoteAction): string => {
    return Object.entries(stageVotes)
      .filter(([, v]) => v === action)
      .map(([uid]) => {
        const member = members.find((m) => m.user_id === uid);
        return member?.nickname || uid;
      })
      .join("、");
  };

  return (
    <div className="flex items-center gap-1 ml-2 shrink-0">
      {/* 赞 */}
      <button
        type="button"
        onClick={() => sendVote(stageIndex, "like")}
        className={cn(
          "flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs transition-all",
          // 去绿归色：赞是积极/正向反馈，和 CTA 一脉相承走暖金（accent），与
          // 下方"踩"的红对称但不再用游离调色板外的 emerald。
          myVote === "like"
            ? "bg-accent-500/20 text-accent-700 border border-accent-500/30"
            : "bg-black/[0.03] text-ink-500 hover:bg-accent-500/10 hover:text-accent-700 border border-transparent",
        )}
        title={likeCount > 0 ? `赞：${getVoterNames("like")}` : "赞（保留这段）"}
      >
        <ThumbsUp className="w-3 h-3" strokeWidth={2} />
        {likeCount > 0 && <span className="font-medium">{likeCount}</span>}
      </button>

      {/* 踩 */}
      <button
        type="button"
        onClick={() => sendVote(stageIndex, "dislike")}
        className={cn(
          "flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs transition-all",
          myVote === "dislike"
            ? "bg-red-500/20 text-red-400 border border-red-500/30"
            : "bg-black/[0.03] text-ink-500 hover:bg-red-500/10 hover:text-red-400 border border-transparent",
        )}
        title={dislikeCount > 0 ? `踩：${getVoterNames("dislike")}` : "踩（换掉这段）"}
      >
        <ThumbsDown className="w-3 h-3" strokeWidth={2} />
        {dislikeCount > 0 && <span className="font-medium">{dislikeCount}</span>}
      </button>
    </div>
  );
}

