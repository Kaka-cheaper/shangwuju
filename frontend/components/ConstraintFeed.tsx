"use client";

/**
 * 约束流面板：协作模式下展示所有人提出的约束（类似聊天气泡但更紧凑）。
 *
 * 诉求台账渲染分支已收编进 `PreferencesPanel.tsx`「本次调整」区（用户偏好
 * 面板全环方案 §3.5/§B #4）——本组件退回**纯房间约束栏**，只渲染
 * `useCollabStore.constraints`（自由打字/点踩广播），不再消费
 * `useChatStore.demandLedger`。台账（节点级定向调整历史）与约束流（自由
 * 文字广播）本就是两种独立信号，拆开渲染后各自的组件职责更单一：
 * 「学到的偏好」与「本次调整」都是"记忆/协商透明面"的一部分，天然属于
 * 偏好面板；约束流是"房间实时协作气泡流"，留在这里。
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

  const showConstraints = collabMode && constraints.length > 0;
  if (!showConstraints) return null;

  const getNickname = (userId: string): string => {
    const member = members.find((m) => m.user_id === userId);
    return member?.nickname || userId;
  };

  return (
    <div className="card p-3 mb-3">
      <h4 className="text-xs font-medium text-ink-500 mb-2 flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-ink-400" />
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
