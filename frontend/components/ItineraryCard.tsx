"use client";

import { useEffect, useRef, useState } from "react";

import { Icons } from "@/lib/icon-map";
import { useCollabStore } from "@/lib/collab-store";
import { useChatStore } from "@/lib/store";
import type { IntentExtraction } from "@/lib/types";
import { cn } from "@/lib/utils";

import NumberTicker from "./NumberTicker";
import RefinementDialog from "./RefinementDialog";
import ShareModal from "./ShareModal";
import ShimmerStripe from "./ShimmerStripe";
import ComparisonView from "./ComparisonView";
import PosterGenerator from "./PosterGenerator";
import TtsPlayer from "./TtsPlayer";
import VoteButtons from "./VoteButtons";

/** 行程卡片：六段时间轴 + 已为你预留 + 转发文案 + 三按钮（黄昏深色主题）。 */
export default function ItineraryCard() {
  const itinerary = useChatStore((s) => s.itinerary);
  const intent = useChatStore((s) => s.intent);
  const narration = useChatStore((s) => s.narration);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);

  // 协作模式
  const collabMode = useCollabStore((s) => s.collabMode);
  const createRoom = useCollabStore((s) => s.createRoom);
  const joinRoom = useCollabStore((s) => s.joinRoom);
  const roomId = useCollabStore((s) => s.roomId);
  const [shareModalOpen, setShareModalOpen] = useState(false);
  const [creatingRoom, setCreatingRoom] = useState(false);
  const lastRefinement = useChatStore((s) => s.lastRefinement);
  const previousItinerary = useChatStore((s) => s.previousItinerary);
  const confirm = useChatStore((s) => s.confirm);
  const cancel = useChatStore((s) => s.cancel);

  const [refineOpen, setRefineOpen] = useState(false);

  // 聚光灯：itinerary 从 null/无 → 有时触发一次性脉冲
  const [spotlight, setSpotlight] = useState(false);
  const prevHadItinerary = useRef(false);
  useEffect(() => {
    const has = !!itinerary;
    if (has && !prevHadItinerary.current) {
      setSpotlight(true);
      const timer = setTimeout(() => setSpotlight(false), 2400);
      prevHadItinerary.current = true;
      return () => clearTimeout(timer);
    }
    if (!has) prevHadItinerary.current = false;
  }, [itinerary]);

  // ============================================================
  // 时间轴 stagger 动画（R1）：stages 逐段"长出来"
  //   - itinerary 从 null → 非 null：从 0 开始递增显示
  //   - stages.length >= 3：间隔 400ms；<= 2：间隔 200ms
  //   - 用户可点「跳过动画」立即显示全部
  //   - animating 期间禁用确认/反馈/取消按钮（防止半成品交互）
  //   - streaming 变 false 时强制兜底（防止 abort 卡住）
  // ============================================================
  const [visibleCount, setVisibleCount] = useState(0);
  const [animating, setAnimating] = useState(false);
  const animTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!itinerary) {
      setVisibleCount(0);
      setAnimating(false);
      return;
    }
    const stages = itinerary.stages;
    if (stages.length === 0) {
      setVisibleCount(0);
      setAnimating(false);
      return;
    }

    // 重启动画：从 0 开始
    setAnimating(true);
    setVisibleCount(0);
    const delay = stages.length <= 2 ? 200 : 400;
    let idx = 0;

    const tick = () => {
      idx += 1;
      setVisibleCount(idx);
      if (idx >= stages.length) {
        setAnimating(false);
        animTimerRef.current = null;
      } else {
        animTimerRef.current = setTimeout(tick, delay);
      }
    };
    animTimerRef.current = setTimeout(tick, delay);

    return () => {
      if (animTimerRef.current) {
        clearTimeout(animTimerRef.current);
        animTimerRef.current = null;
      }
    };
  }, [itinerary]);

  // 跳过动画：清 timer + 立即全显
  const skipAnimation = () => {
    if (animTimerRef.current) {
      clearTimeout(animTimerRef.current);
      animTimerRef.current = null;
    }
    if (itinerary) setVisibleCount(itinerary.stages.length);
    setAnimating(false);
  };

  // streaming 变 false 时兜底（abort 等异常场景下防止 animating 卡住）
  useEffect(() => {
    if (!streaming && animating) {
      // 给 React 一次重新调度机会：如果是正常完成会被 stagger 自然结束；
      // 如果是 abort，强制兜底
      const timer = setTimeout(() => {
        if (!animTimerRef.current && itinerary) {
          setVisibleCount(itinerary.stages.length);
          setAnimating(false);
        }
      }, 100);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [streaming, animating, itinerary]);

  if (!itinerary && !streaming) {
    return (
      <div className="card px-4 py-8 flex flex-col items-center gap-2.5 text-ink-500">
        <div className="w-10 h-10 rounded-full bg-gradient-to-br from-brand-500/15 to-accent-500/15 flex items-center justify-center border border-white/[0.08]">
          <Icons.pin className="w-4 h-4 text-brand-400" strokeWidth={1.5} />
        </div>
        <span className="text-sm text-ink-700">行程将在这里出现</span>
      </div>
    );
  }

  if (!itinerary) {
    return (
      <div className="card px-4 py-5 space-y-3">
        <div className="flex items-center gap-1.5 text-[12px] text-brand-400">
          <Icons.thinking
            className="w-3.5 h-3.5 animate-spin"
            strokeWidth={2}
          />
          <span className="tracking-tight">正在为你拼装行程...</span>
        </div>
        <ShimmerStripe rows={4} />
      </div>
    );
  }

  const totalH = itinerary.total_minutes / 60;
  const hasOrders = itinerary.orders.length > 0;
  // R1: animating 期间也禁用按钮（避免用户在动画进行中点确认）
  const canAct = !streaming && !hasOrders && !cancelled && !animating;

  return (
    <div className={cn("card animate-fade-in", spotlight && "spotlight-once")}>
      {/* Header */}
      <div className="px-4 py-3 border-b border-white/[0.06]">
        <div className="flex items-center justify-between">
          <span className="section-title">行程方案</span>
          <span className="text-[11px] text-ink-500">
            总时长{" "}
            <NumberTicker
              value={totalH}
              format={(v) => v.toFixed(1)}
              className="font-mono text-brand-400 mx-0.5 font-semibold"
            />
            <span className="text-ink-500">小时</span>
          </span>
        </div>
        <div className="mt-0.5 text-[15px] font-semibold text-ink-900 tracking-tight">
          {itinerary.summary}
        </div>
      </div>

      {/* T7/R3: Refine 前后对比视图（仅在有 lastRefinement + previousItinerary 时显示） */}
      {lastRefinement && previousItinerary && itinerary && (
        <ComparisonView
          oldItinerary={previousItinerary}
          newItinerary={itinerary}
        />
      )}

      {/* Refinement summary banner */}
      {lastRefinement && lastRefinement.changedFields.length > 0 && (
        <div className="px-4 pt-3">
          <RefinementSummaryBanner
            fields={lastRefinement.changedFields}
            note={lastRefinement.refinerNote}
          />
        </div>
      )}

      {/* Agent 暖心开场白（导游口播） */}
      {narration?.text && (
        <div className="px-4 pt-3">
          <NarrationBlock text={narration.text} stage={narration.stage} />
        </div>
      )}

      {/* 方案 C：「为你考虑了什么」小标签行（intent 命中可视化） */}
      {intent && <IntentChips intent={intent} />}

      {/* R1: 时间轴 stagger 动画期间显示跳过按钮 */}
      {animating && (
        <div className="px-4 pt-2 flex justify-end">
          <button
            type="button"
            onClick={skipAnimation}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.08] text-[10px] text-ink-500 hover:text-ink-800 transition-colors"
            title="跳过逐段动画，立即显示完整行程"
          >
            <span>跳过动画</span>
            <span aria-hidden>⏭</span>
          </button>
        </div>
      )}

      {/* Timeline */}
      <ol className="relative px-4 py-4 space-y-3.5">
        {/* 时间轴竖线（暖橙→紫渐变） */}
        <div
          aria-hidden
          className="absolute left-[51px] top-6 bottom-6 w-px"
          style={{
            background:
              "linear-gradient(180deg, rgba(251,146,60,0.6) 0%, rgba(236,72,153,0.4) 50%, rgba(139,92,246,0.2) 100%)",
          }}
        />
        {itinerary.stages.map((stage, idx) => {
          // R1: stagger 控制——idx 超出 visibleCount 时不渲染（保留时间轴竖线高度感由间隔自然成）
          if (idx >= visibleCount) return null;
          return (
            <li
              key={idx}
              className="relative flex items-start gap-3 animate-fade-in-up"
            >
              <div className="flex flex-col items-center min-w-[44px] z-10">
                <div className="text-[10px] text-ink-500 mono">{stage.start}</div>
                {/* 暖橙→莓粉时间点 */}
                <div
                  className="my-1 w-2 h-2 rounded-full ring-[3px] ring-[#08080d]"
                  style={{
                    background:
                      "linear-gradient(135deg, #fb923c 0%, #ec4899 100%)",
                    boxShadow:
                      "0 0 0 1px rgba(255,255,255,0.16), 0 0 8px rgba(249,115,22,0.6)",
                  }}
                />
                <div className="text-[10px] text-ink-500 mono">{stage.end}</div>
              </div>
              <div className="flex-1 pt-0.5">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="chip">{stage.kind}</span>
                  <span className="text-sm font-medium text-ink-900 tracking-tight">
                    {stage.title}
                  </span>
                </div>
                {stage.note && (
                  <div className="mt-1 text-xs text-ink-600 leading-relaxed">
                    {stage.note}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {/* 已为你预留：暗色 emerald 玻璃 */}
      {hasOrders && (
        <div className="px-4 pb-3">
          <div className="section-title mb-1.5">已为你预留</div>
          <ul className="space-y-1.5">
            {itinerary.orders.map((o) => (
              <li
                key={o.order_id}
                className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200 animate-fade-in-up backdrop-blur-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <Icons.success
                      className="w-3.5 h-3.5 shrink-0 text-emerald-400"
                      strokeWidth={2}
                    />
                    <span className="font-medium tracking-tight truncate">
                      {o.target_name}
                    </span>
                  </div>
                  <span className="text-[10px] text-emerald-300/80 mono shrink-0">
                    {o.kind}
                  </span>
                </div>
                <div className="mt-1 text-emerald-300/90 ml-5">
                  {o.detail}
                  <span className="text-emerald-500/70 mx-1.5">·</span>
                  <span className="mono text-[11px]">{o.order_id}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 转发文案 */}
      {itinerary.share_message && (
        <div className="px-4 pb-3">
          <ShareMessage text={itinerary.share_message} />
        </div>
      )}

      {/* Action buttons */}
      <div className="px-4 pb-4">
        {!hasOrders && !cancelled && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <button
              className={cn("btn-primary", streaming && "shimmer-border")}
              disabled={!canAct}
              onClick={confirm}
            >
              {streaming ? (
                <>
                  <Icons.thinking className="w-3.5 h-3.5 animate-spin" />
                  <span>执行中</span>
                </>
              ) : (
                <>
                  <Icons.success className="w-3.5 h-3.5" strokeWidth={2.25} />
                  <span>确认并预约</span>
                </>
              )}
            </button>
            <button
              className="btn-secondary disabled:opacity-50 disabled:cursor-not-allowed"
              disabled={!canAct}
              onClick={() => setRefineOpen(true)}
            >
              <Icons.refine className="w-3.5 h-3.5" strokeWidth={2} />
              <span>说说哪不对</span>
            </button>
            <button
              className="btn-danger-ghost disabled:opacity-50 disabled:cursor-not-allowed"
              disabled={!canAct}
              onClick={cancel}
            >
              <Icons.close className="w-3.5 h-3.5" strokeWidth={2.25} />
              <span>取消方案</span>
            </button>
          </div>
        )}
        {/* R6 语音播报 + R5 海报生成：行程的多模态输出（itinerary 存在时即可用） */}
        <TtsPlayer />
        <PosterGenerator />
        {/* 邀请同行人按钮（行程出来后、未下单时显示） */}
        {!hasOrders && !cancelled && itinerary && !collabMode && (
          <button
            className="mt-2 w-full py-2 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/20 hover:border-amber-500/40 text-amber-400 text-sm font-medium transition-all flex items-center justify-center gap-2 disabled:opacity-50"
            disabled={creatingRoom || streaming}
            onClick={async () => {
              setCreatingRoom(true);
              const sessionId = useChatStore.getState().sessionId;
              const userId = useChatStore.getState().currentUserId || "demo_user";
              // 把当前规划过程事件构造为历史（新成员加入时回放 ToolTracePanel）
              const state = useChatStore.getState();
              const planningEvents: Record<string, unknown>[] = [];
              // intent_parsed
              if (state.intent) {
                planningEvents.push({ type: "intent_parsed", seq: 0, payload: state.intent, timestamp_ms: Date.now() });
              }
              // tool_call_start + tool_call_end
              for (const tc of state.toolCalls) {
                planningEvents.push({ type: "tool_call_start", seq: tc.startedAtSeq, payload: { tool: tc.tool, input: tc.input }, timestamp_ms: Date.now() });
                if (tc.endedAtSeq != null) {
                  planningEvents.push({ type: "tool_call_end", seq: tc.endedAtSeq, payload: { tool: tc.tool, output: tc.output || {}, duration_ms: tc.durationMs || 0 }, timestamp_ms: Date.now() });
                }
              }
              // replans
              for (const rp of state.replans) {
                planningEvents.push({ type: "replan_triggered", seq: rp.seq, payload: { reason: rp.reason, from_tool: rp.fromTool }, timestamp_ms: Date.now() });
              }
              // thoughts
              for (const th of state.thoughts) {
                planningEvents.push({ type: "agent_thought", seq: th.seq, payload: { text: th.text }, timestamp_ms: Date.now() });
              }
              const newRoomId = await createRoom(userId, "发起人", sessionId, planningEvents, state.messages as any);
              if (newRoomId) {
                // 自动加入房间
                joinRoom(newRoomId, userId, "发起人");
                setShareModalOpen(true);
              }
              setCreatingRoom(false);
            }}
          >
            {creatingRoom ? "创建中…" : "👥 邀请同行人一起决定"}
          </button>
        )}
        {/* 协作模式下显示分享按钮 */}
        {collabMode && roomId && (
          <button
            className="mt-2 w-full py-1.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.08] text-ink-500 text-xs transition-all flex items-center justify-center gap-1.5"
            onClick={() => setShareModalOpen(true)}
          >
            🔗 分享链接给同行人
          </button>
        )}
        {hasOrders && (
          <div className="flex items-center justify-center gap-1.5 text-xs text-emerald-400">
            <Icons.success className="w-3.5 h-3.5" strokeWidth={2.25} />
            <span>已完成下单与转发文案生成</span>
          </div>
        )}
        {cancelled && !hasOrders && (
          <div className="text-center text-xs text-ink-500">
            已取消方案，可重新输入或点击场景按钮
          </div>
        )}
      </div>

      <RefinementDialog
        open={refineOpen}
        onClose={() => setRefineOpen(false)}
      />
      {roomId && (
        <ShareModal
          open={shareModalOpen}
          onClose={() => setShareModalOpen(false)}
          roomId={roomId}
        />
      )}
    </div>
  );
}

function RefinementSummaryBanner({
  fields,
  note,
}: {
  fields: string[];
  note?: string | null;
}) {
  return (
    <div
      className="rounded-md border border-accent-500/30 px-3 py-2 text-xs text-accent-200 animate-fade-in backdrop-blur-sm"
      style={{
        background:
          "linear-gradient(135deg, rgba(217,70,239,0.12) 0%, rgba(139,92,246,0.08) 100%)",
      }}
    >
      <div className="flex items-center gap-1.5 mb-1 font-medium text-accent-300">
        <Icons.refine className="w-3.5 h-3.5" strokeWidth={2} />
        <span>已根据反馈调整</span>
      </div>
      <ul className="space-y-0.5 text-accent-100 ml-5 list-disc list-outside">
        {fields.map((f, i) => (
          <li key={i}>{f}</li>
        ))}
      </ul>
      {note && <div className="mt-1 ml-5 text-accent-300/80">{note}</div>}
    </div>
  );
}

function ShareMessage({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      } finally {
        document.body.removeChild(ta);
      }
    }
  };

  return (
    <div
      className="rounded-md border border-white/[0.08] p-3 backdrop-blur-sm"
      style={{
        background:
          "linear-gradient(135deg, rgba(251,146,60,0.06) 0%, rgba(236,72,153,0.04) 100%)",
      }}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          <Icons.share
            className="w-3.5 h-3.5 text-brand-400"
            strokeWidth={2}
          />
          <span className="text-[11px] font-medium text-ink-800 tracking-tight">
            转发文案
          </span>
        </div>
        <button
          onClick={copy}
          className={cn(
            "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded transition-colors",
            copied
              ? "bg-emerald-500 text-white"
              : "bg-white/[0.06] text-ink-700 border border-white/[0.08] hover:bg-white/[0.1] hover:text-ink-900",
          )}
        >
          {copied ? (
            <>
              <Icons.success className="w-3 h-3" strokeWidth={2.5} />
              <span>已复制</span>
            </>
          ) : (
            <>
              <Icons.copy className="w-3 h-3" strokeWidth={2} />
              <span>复制</span>
            </>
          )}
        </button>
      </div>
      <div className="text-[13px] leading-relaxed text-ink-800 whitespace-pre-wrap tracking-tight">
        {text}
      </div>
    </div>
  );
}


// ============================================================
// NarrationBlock —— Agent 暖心开场白（导游口播）
// ============================================================

function NarrationBlock({
  text,
  stage,
}: {
  text: string;
  stage: "stream" | "confirm";
}) {
  const isConfirm = stage === "confirm";
  return (
    <div
      className="relative rounded-md px-3.5 py-3 text-[13px] leading-relaxed tracking-tight animate-fade-in backdrop-blur-sm border"
      style={{
        background: isConfirm
          ? "linear-gradient(135deg, rgba(16,185,129,0.10) 0%, rgba(251,146,60,0.06) 100%)"
          : "linear-gradient(135deg, rgba(251,146,60,0.10) 0%, rgba(236,72,153,0.06) 100%)",
        borderColor: isConfirm
          ? "rgba(16,185,129,0.24)"
          : "rgba(251,146,60,0.22)",
        color: "rgb(231 229 228 / 0.92)",
      }}
    >
      <div className="flex items-start gap-2">
        <Icons.spark
          className={cn(
            "w-3.5 h-3.5 mt-0.5 shrink-0",
            isConfirm ? "text-emerald-400" : "text-brand-400",
          )}
          strokeWidth={2}
        />
        <p className="whitespace-pre-wrap">{text}</p>
      </div>
    </div>
  );
}

// ============================================================
// IntentChips —— 「为你考虑了什么」可视化（方案 C）
//   把 intent 命中的 4-6 个关键约束做成小标签，让评委一眼看到 Agent 真在为你考虑
// ============================================================

interface ChipItem {
  icon: keyof typeof Icons;
  label: string;
}

function buildIntentChips(intent: IntentExtraction): ChipItem[] {
  const chips: ChipItem[] = [];

  // 距离
  if (intent.distance_max_km != null) {
    chips.push({
      icon: "pin",
      label: `${intent.distance_max_km % 1 === 0 ? intent.distance_max_km : intent.distance_max_km.toFixed(1)} km 内`,
    });
  }

  // 同行人（家庭/朋友/独处推断）
  if (intent.companions && intent.companions.length > 0) {
    const totalCount = intent.companions.reduce(
      (sum, c) => sum + (c.count ?? 1),
      0,
    );
    const hasChild = intent.companions.some(
      (c) => c.age != null && c.age <= 12,
    );
    const hasElder = intent.companions.some(
      (c) => c.age != null && c.age >= 60,
    );
    let label: string;
    if (hasChild) {
      const child = intent.companions.find((c) => c.age != null && c.age <= 12);
      label = `带 ${child?.age ?? ""} 岁孩子`;
    } else if (hasElder) {
      label = "陪长辈";
    } else if (totalCount > 1) {
      label = `${totalCount} 人同行`;
    } else {
      label = intent.companions[0].role || "同行";
    }
    chips.push({ icon: "user", label });
  } else if (intent.social_context && intent.social_context.includes("独处")) {
    chips.push({ icon: "user", label: "独处时间" });
  }

  // 饮食偏好（合并展示，最多 2 个）
  const dietary = (intent.dietary_constraints || []).slice(0, 2);
  for (const d of dietary) {
    chips.push({ icon: "spark", label: d });
  }

  // 物理约束（无台阶/亲子友好等，最多 1 个，避免太多 chip）
  const physical = (intent.physical_constraints || []).slice(0, 1);
  for (const p of physical) {
    chips.push({ icon: "spark", label: p });
  }

  // 时长（如果用户给了具体范围）
  if (
    intent.duration_hours &&
    Array.isArray(intent.duration_hours) &&
    intent.duration_hours.length === 2
  ) {
    const [lo, hi] = intent.duration_hours;
    if (lo === hi) {
      chips.push({ icon: "thinking", label: `${lo} 小时` });
    } else {
      chips.push({ icon: "thinking", label: `${lo}-${hi} 小时` });
    }
  }

  return chips.slice(0, 6); // 上限 6 个，避免一行挤
}

function IntentChips({ intent }: { intent: IntentExtraction }) {
  const chips = buildIntentChips(intent);
  if (chips.length === 0) return null;

  return (
    <div className="px-4 pt-3">
      <div className="text-[10px] tracking-wider uppercase text-ink-500 mb-1.5 flex items-center gap-1">
        <Icons.spark className="w-3 h-3 text-brand-400" strokeWidth={2.5} />
        <span>为你考虑了</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {chips.map((c, i) => {
          const Ico = Icons[c.icon];
          return (
            <span
              key={`${c.label}-${i}`}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium tracking-tight border animate-fade-in"
              style={{
                background: "rgba(251, 146, 60, 0.08)",
                borderColor: "rgba(251, 146, 60, 0.22)",
                color: "rgb(253 186 116)",
              }}
            >
              <Ico className="w-3 h-3" strokeWidth={2} />
              {c.label}
            </span>
          );
        })}
      </div>
    </div>
  );
}
