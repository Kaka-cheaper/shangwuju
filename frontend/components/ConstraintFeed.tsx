"use client";

/**
 * 约束流面板：协作模式下展示所有人提出的约束（类似聊天气泡但更紧凑）。
 *
 * ADR-0013 F-4：单人模式升级为「诉求台账」面板——有 demandLedger 内容即显示
 * （状态徽标：生效/被顶替/已满足；节点指向）。协作模式下维持现状不变（房间
 * 约束流；房间侧的记名台账接线是 F-5 范围，本文件不动 collabMode 分支）。
 */

import { useRef, useEffect } from "react";
import { useCollabStore } from "@/lib/collab-store";
import { useChatStore } from "@/lib/store";
import type { DemandLedgerEntry, DemandLedgerStatus, Itinerary } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function ConstraintFeed() {
  const collabMode = useCollabStore((s) => s.collabMode);
  const constraints = useCollabStore((s) => s.constraints);
  const members = useCollabStore((s) => s.members);
  const demandLedger = useChatStore((s) => s.demandLedger);
  const itinerary = useChatStore((s) => s.itinerary);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 自动滚动到底部（两种模式共用同一个 ref/effect；依赖两个长度里非本模式的那个
  // 恒为 0，不会互相触发多余的滚动）
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [constraints.length, demandLedger?.length]);

  if (collabMode) {
    if (constraints.length === 0) return null;

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

  // ---- 单人模式：诉求台账面板（ADR-0013 F-4）----
  if (!demandLedger || demandLedger.length === 0) return null;

  return (
    <div className="card p-3 mb-3">
      <h4 className="text-xs font-medium text-ink-500 mb-2 flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
        诉求台账（{demandLedger.length} 条）
      </h4>
      <div className="max-h-[160px] overflow-y-auto space-y-1.5 scrollbar-thin">
        {demandLedger.map((entry, i) => (
          <DemandLedgerRow key={`${entry.created_at}-${i}`} entry={entry} itinerary={itinerary} />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

const _DIMENSION_LABELS: Record<string, string> = {
  price: "价格",
  distance: "距离",
  cuisine_or_type: "类型",
  dietary: "口味",
  ambience: "氛围",
  crowd_fit: "适配",
};

const _STATUS_THEME: Record<DemandLedgerStatus, { label: string; className: string }> = {
  active: { label: "生效", className: "bg-amber-400/15 text-amber-700 border-amber-400/30" },
  superseded: { label: "被顶替", className: "bg-black/[0.04] text-ink-400 border-black/[0.08] line-through" },
  satisfied: { label: "已满足", className: "bg-emerald-500/12 text-emerald-700 border-emerald-500/30" },
};

function nodeTitleByTargetId(itinerary: Itinerary | null, targetId: string): string {
  const node = itinerary?.nodes?.find((n) => n.target_id === targetId);
  return node?.title ?? targetId;
}

function DemandLedgerRow({
  entry,
  itinerary,
}: {
  entry: DemandLedgerEntry;
  itinerary: Itinerary | null;
}) {
  const theme = _STATUS_THEME[entry.status];
  const nodeLabel = entry.node_ref ? nodeTitleByTargetId(itinerary, entry.node_ref.target_id) : "全局";
  const dimensionLabel = _DIMENSION_LABELS[entry.dimension] ?? entry.dimension;

  return (
    <div className="flex items-start gap-2 text-xs">
      <span
        className={cn(
          "shrink-0 px-1.5 py-0 rounded border text-[11px] leading-[1.4] font-medium",
          theme.className,
        )}
      >
        {theme.label}
      </span>
      <span className="text-ink-600 break-all">
        <span className="font-medium text-ink-700">{nodeLabel}</span>
        <span className="text-ink-400 mx-1">·</span>
        {dimensionLabel}：{entry.value}
      </span>
    </div>
  );
}
