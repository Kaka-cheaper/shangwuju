"use client";

/**
 * 行程卡片每段的赞/踩按钮。
 * 协作模式下嵌入 ItineraryCard 的每个 stage 右侧。
 */

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
          myVote === "like"
            ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
            : "bg-black/[0.03] text-ink-500 hover:bg-emerald-500/10 hover:text-emerald-400 border border-transparent",
        )}
        title={likeCount > 0 ? `赞：${getVoterNames("like")}` : "赞（保留这段）"}
      >
        👍
        {likeCount > 0 && <span>{likeCount}</span>}
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
        👎
        {dislikeCount > 0 && <span>{dislikeCount}</span>}
      </button>
    </div>
  );
}

