"use client";

/**
 * 约束流面板：展示所有人提出的约束（类似聊天气泡但更紧凑）。
 * 协作模式下显示在 ToolTracePanel 上方或替代位置。
 */

import { useRef, useEffect } from "react";
import { useCollabStore } from "@/lib/collab-store";
import { cn } from "@/lib/utils";

export default function ConstraintFeed() {
  const collabMode = useCollabStore((s) => s.collabMode);
  const constraints = useCollabStore((s) => s.constraints);
  const members = useCollabStore((s) => s.members);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [constraints.length]);

  if (!collabMode || constraints.length === 0) return null;

  const getNickname = (userId: string): string => {
    const member = members.find((m) => m.user_id === userId);
    return member?.nickname || userId;
  };

  return (
    <div className="card p-3 mb-3">
      <h4 className="text-xs font-medium text-ink-500 mb-2 flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
        约束流（{constraints.length} 条）
      </h4>
      <div className="max-h-[160px] overflow-y-auto space-y-1.5 scrollbar-thin">
        {constraints.map((c, i) => (
          <div
            key={`${c.user_id}-${c.timestamp}-${i}`}
            className={cn(
              "flex items-start gap-2 text-xs",
              c.source === "vote_dislike" && "opacity-70",
            )}
          >
            <span className="shrink-0 font-medium text-ink-600">
              {getNickname(c.user_id)}：
            </span>
            <span className="text-ink-400 break-all">
              {c.source === "vote_dislike" ? `👎 ${c.text}` : c.text}
            </span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
