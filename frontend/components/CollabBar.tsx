"use client";

/**
 * 协作状态条：显示在顶部，展示房间成员 + 规划状态 + 约束流。
 * 仅在 collabMode=true 时渲染。
 */

import { useCollabStore } from "@/lib/collab-store";
import { cn } from "@/lib/utils";

export default function CollabBar() {
  const collabMode = useCollabStore((s) => s.collabMode);
  const members = useCollabStore((s) => s.members);
  const connected = useCollabStore((s) => s.connected);
  const planningActive = useCollabStore((s) => s.planningActive);
  const planningTrigger = useCollabStore((s) => s.planningTrigger);
  const constraints = useCollabStore((s) => s.constraints);
  const connectionError = useCollabStore((s) => s.connectionError);

  if (!collabMode) return null;

  const lastConstraint = constraints[constraints.length - 1];
  const onlineCount = members.filter((m) => m.online).length;

  return (
    <div
      className={cn(
        "w-full px-4 py-2 border-b border-black/[0.08]",
        "bg-black/[0.02]",
        "flex items-center justify-between gap-3 text-sm",
      )}
    >
      {/* 左侧：成员头像 */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-ink-500">协作中</span>
        <div className="flex -space-x-1">
          {members.map((m) => (
            <div
              key={m.user_id}
              className={cn(
                "w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium border-2 border-white",
                m.online ? "bg-emerald-500/80 text-white" : "bg-ink-200 text-ink-400",
              )}
              title={`${m.nickname}（${m.role === "owner" ? "发起人" : "参与者"}）${m.online ? "" : " · 离线"}`}
            >
              {m.nickname[0]}
            </div>
          ))}
        </div>
        <span
          className="text-xs text-ink-400"
          title="实时统计：房间内在线成员数 · 已收集但还没合并到下次重规划的约束条数"
        >
          房间内 {onlineCount} 人
          {constraints.length > 0 && (
            <span className="ml-1 text-ink-500">
              · {constraints.length} 个约束待合并
            </span>
          )}
        </span>
      </div>

      {/* 中间：状态 */}
      <div className="flex-1 text-center">
        {planningActive ? (
          <span className="text-amber-400 text-xs animate-pulse">
            {planningTrigger === "constraint_added"
              ? `正在根据新约束重新规划…`
              : planningTrigger === "vote_dislike"
                ? "正在根据投票反馈重新规划…"
                : "规划中…"}
          </span>
        ) : lastConstraint ? (
          <span className="text-ink-400 text-xs">
            最新约束：{lastConstraint.nickname || lastConstraint.user_id}说「{lastConstraint.text}」
          </span>
        ) : (
          <span className="text-ink-500 text-xs">
            等待同行人提出偏好…
          </span>
        )}
      </div>

      {/* 右侧：连接状态 */}
      <div className="flex items-center gap-2">
        {connectionError && (
          <span className="text-red-400 text-xs">{connectionError}</span>
        )}
        <div
          className={cn(
            "w-2 h-2 rounded-full",
            connected ? "bg-emerald-400" : "bg-red-400",
          )}
          title={connected ? "已连接" : "未连接"}
        />
      </div>
    </div>
  );
}

