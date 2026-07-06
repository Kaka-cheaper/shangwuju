"use client";

/**
 * 约束流面板：协作模式下展示所有人提出的约束（类似聊天气泡但更紧凑）+
 * 诉求台账面板（状态徽标：生效/被顶替/已满足；节点指向；归名）。
 *
 * ADR-0013 F-4：单人模式的台账数据源是 `useChatStore.demandLedger`（随
 * `/chat/adjust` 的 `agent_narration.demand_ledger` 逐步刷新）。
 * ADR-0013 F-5：房间模式的台账**换成同一个字段**——`collab-store.ts` 在收到
 * `room_state` 快照（`room.demand_ledger` 的 `ledger_for_display` 投影）与
 * 每次房间内 `agent_narration`（走同一套 `handleEvent`）时都写回
 * `useChatStore.demandLedger`，两种模式因此天然复用同一份渲染逻辑——不必
 * 为房间模式单独接一遍台账面板。约束流（自由打字/义务分发广播）与诉求台账
 * （节点级调整历史）是两种独立信号，各自按有无内容独立渲染，不互斥。
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

  // 自动滚动到底部（两个面板共用同一个 ref/effect；不适用的一侧长度恒为 0，
  // 不会互相触发多余的滚动）
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [constraints.length, demandLedger?.length]);

  const showConstraints = collabMode && constraints.length > 0;
  const showLedger = !!demandLedger && demandLedger.length > 0;
  if (!showConstraints && !showLedger) return null;

  const getNickname = (userId: string): string => {
    const member = members.find((m) => m.user_id === userId);
    return member?.nickname || userId;
  };

  return (
    <>
      {showConstraints && (
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
            {!showLedger && <div ref={bottomRef} />}
          </div>
        </div>
      )}

      {/* 诉求台账（ADR-0013 F-4 单人 / F-5 房间共用同一份渲染，见文件顶部 docstring）*/}
      {showLedger && (
        <div className="card p-3 mb-3">
          <h4 className="text-xs font-medium text-ink-500 mb-2 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-ink-400" />
            诉求台账（{demandLedger!.length} 条）
          </h4>
          <div className="max-h-[160px] overflow-y-auto space-y-1.5 scrollbar-thin">
            {demandLedger!.map((entry, i) => (
              <DemandLedgerRow key={`${entry.created_at}-${i}`} entry={entry} itinerary={itinerary} />
            ))}
            <div ref={bottomRef} />
          </div>
        </div>
      )}
    </>
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
        {/* 房间模式归名（ADR-0013 F-5）：entry.nickname 非空时点名"谁提的"；
            单人模式恒为 null，不显示这一段（不是"匿名"，是压根没有"谁"这个概念）*/}
        {entry.nickname && (
          <>
            <span className="font-medium text-ink-700">{entry.nickname}</span>
            <span className="text-ink-400 mx-1">·</span>
          </>
        )}
        <span className="font-medium text-ink-700">{nodeLabel}</span>
        <span className="text-ink-400 mx-1">·</span>
        {dimensionLabel}：{entry.value}
      </span>
    </div>
  );
}
