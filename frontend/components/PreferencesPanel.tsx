"use client";

/**
 * PreferencesPanel —— 用户偏好画像（黄昏深色主题 · 焦糖琥珀配色）。
 *
 * 设计意图：
 *   - 偏好画像是「档案 / 笔记」语义，应是温润的纸张感而非 AI 紫
 *   - 用 caramel 焦糖琥珀色（替代莓紫 accent），与主题暖橙脉络一致
 *   - 灵感：Aesop 沙漠米色 / Stripe 旧版焦糖文档 / 中古电影焦糖滤镜
 *
 * 解耦原则（commit "解耦小修"）：
 *   - 颜色 rgba 提为局部常量；主题改时改一处即可
 *   - open/close 状态持久化到 localStorage；评委切回来不用重新点开
 *   - 不依赖父级栅格 / 不假设旁边有谁；可在 aside / 弹窗 / 抽屉任意挂载
 *
 * 三区重构（用户偏好面板全环方案 §3/§B #4/#6，2026-07 批）：
 *   - 「画像」：persona 模板（label/notes/default_tags/default_distance），
 *     随画像切换，不随会话累积变化。
 *   - 「这次对话学到的」：偏好笔记（top_priors 首位 tag 生成一句，无计数）
 *     + 距离笔记 + 去过（recent_trips ≤3 最新在前）+ 诚实空态。同时订阅
 *     `preferences` store 字段（`/preferences/{user_id}?session_id=` 端点，
 *     §14.4 GET 用 query）。**房间模式整区隐藏**（用户拍板 + 方案 §13：
 *     房间累积键是全房间共享，显示"学到的"会有混合口味困惑，房间讲协商
 *     不讲记忆）。
 *   - 「本次调整」：诉求台账收编（`demandLedger` store 字段，`ConstraintFeed.tsx`
 *     对应渲染分支已删除，退回纯房间约束栏）。人话化用 `lib/ledger-copy.ts`
 *     （id→店名、方向词→中文、词典外兜底原词+console.warn）。只显
 *     生效中/已满足，被顶替的条目砍掉（不渲染）；≤5 条 + "还有 N 条"折叠。
 *     房间模式下**照常显示**，甚至是主角（归名台账是房间协商的核心价值）。
 *
 * 硬约束：面板总高有界（外层 max-h + overflow-y-auto），每区超量各自独立
 * 折叠，不挤压下方 QuickScenarios/ChatDock；头像从 150px 缩到 96px 给三区
 * 腾高度。
 *
 * 房间模式清空按钮：整个隐藏（清空一个全房间共享键是影响所有成员的动作，
 * 不该被单个成员静默触发；见方案 §13）。
 */

import { useEffect, useMemo, useState } from "react";

import { useCollabStore } from "@/lib/collab-store";
import { Icons, personaIconFromEmoji } from "@/lib/icon-map";
import { ledgerEntryLine } from "@/lib/ledger-copy";
import { distanceNote, preferenceNote } from "@/lib/preference-notes";
import { useChatStore } from "@/lib/store";
import type { DemandLedgerEntry, Itinerary, Persona, RecentTrip } from "@/lib/types";
import { buildAppPath, cn } from "@/lib/utils";

// ============================================================
// 局部主题常量（避免散落 inline rgba；主题改时改一处即可）
// ============================================================

/** persona icon 渐变背景（焦糖琥珀两层） */
const PERSONA_ICON_GRADIENT =
  "linear-gradient(135deg, rgba(184,137,90,0.18) 0%, rgba(160,106,58,0.14) 100%)";

/** localStorage key（持久化展开态） */
const STORAGE_KEY = "shangwuju.preferences.open";
/** localStorage key（"本次调整"已读游标——展开过一次即消呼吸 dot，见 3.1）。 */
const LEDGER_SEEN_KEY = "shangwuju.preferences.ledgerSeenCount";

/** 每区默认展示条数上限（超出收进"查看全部/更多 N 条"折叠触发器）。 */
const PERSONA_TAGS_PREVIEW_N = 5;
const LEDGER_PREVIEW_N = 5;
const TRIPS_PREVIEW_N = 3;

const avatarMap: Record<string, string> = {
  u_dad: buildAppPath("/avatars/xinshoubaba.png"),
  u_biz: buildAppPath("/avatars/shangwubailing.png"),
  u_grandma: buildAppPath("/avatars/xiaoshunernv.png"),
  u_solo: buildAppPath("/avatars/dujuqingnian.png"),
  u_couple: buildAppPath("/avatars/qinglvdang.png"),
};

export default function PreferencesPanel() {
  const currentUserId = useChatStore((s) => s.currentUserId);
  const preferences = useChatStore((s) => s.preferences);
  const demandLedger = useChatStore((s) => s.demandLedger);
  const itinerary = useChatStore((s) => s.itinerary);
  const refreshPreferences = useChatStore((s) => s.refreshPreferences);
  const resetUserMemory = useChatStore((s) => s.resetUserMemory);
  const collabMode = useCollabStore((s) => s.collabMode);
  const roomId = useCollabStore((s) => s.roomId);

  // 展开态持久化：SSR 期返 false 避免 hydration mismatch；mount 后从 localStorage 恢复
  const [open, _setOpen] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      _setOpen(window.localStorage.getItem(STORAGE_KEY) === "true");
    } catch {
      /* 隐私模式 / 配额异常时忽略 */
    }
  }, []);

  const setOpen = (next: boolean) => {
    _setOpen(next);
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(STORAGE_KEY, next ? "true" : "false");
      } catch {
        /* 隐私模式 / 配额异常时忽略 */
      }
    }
  };

  // 房间模式：台账累积键是 collab_{roomId}（房间会话），不是个人 sessionId
  // （方案 §8/§13 坐实的两处会话身份分叉之一——本次改动不修"画像区显示谁"
  // 这处更深的错位，只保证台账/学到区读对键；房间模式"学到的"整区隐藏，
  // 读不读得到房间累积已不影响展示，但保留正确的 refresh 调用不留技术债）。
  const roomSessionId = collabMode && roomId ? `collab_${roomId}` : undefined;

  // 用户/房间切换时刷一次（store 已自管缓存，open 变化不再触发额外刷新）
  useEffect(() => {
    if (currentUserId) refreshPreferences(roomSessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUserId, roomSessionId, refreshPreferences]);

  const currentAvatar = currentUserId ? avatarMap[currentUserId] : null;

  // 「本次调整」台账：房间/单人共用同一 store 字段（ConstraintFeed.tsx 同源
  // 注释）。只显生效中/已满足，被顶替的条目砍掉（不是"折叠"，是不渲染——
  // 台账面板的价值在这里是"当下有效的诉求一览"，不是审计历史，审计历史
  // 已经由 ConstraintFeed 的房间约束栏承担）。
  const visibleLedger = useMemo(
    () => (demandLedger ?? []).filter((e) => e.status !== "superseded"),
    [demandLedger],
  );

  // Hooks 必须无条件调用（不能塞进下面的 `if (!open)` 分支）——折叠态/展开态
  // 都要跑这个 hook，只是折叠态才消费它的返回值渲染呼吸 dot。
  const hasUnseenLedger = useUnseenLedgerDot(visibleLedger.length);

  if (!open) {
    const persona = preferences?.persona;
    const learnedCount = collabMode
      ? 0
      : preferences?.top_priors?.length ?? 0;
    const PersonaIcon = persona
      ? personaIconFromEmoji(persona.icon, persona.label)
      : Icons.user;
    const subtitle = persona?.notes ?? "点击查看 Agent 已学到的偏好";

    return (
      <div className="w-full flex items-center gap-3 pr-20">
        {/* 左侧：文字部分 + 箭头 */}
        <div className="flex-1 flex items-center justify-end gap-2 px-4 py-3">
          <div className="text-right">
            <div className="text-2xl font-semibold text-ink-900 truncate tracking-tight">
              {persona?.label ?? "偏好画像"}
            </div>
            <div className="mt-0.5 text-sm text-ink-500 truncate leading-relaxed">
              {subtitle}
            </div>
          </div>

          <button
            type="button"
            onClick={() => setOpen(true)}
            title="展开偏好画像"
            className="text-ink-400 hover:text-ink-700 transition-colors p-1"
          >
            <svg
              className="w-3.5 h-3.5 transition-transform hover:translate-y-0.5"
              viewBox="0 0 12 12"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path
                d="M3 4.5L6 7.5L9 4.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>

        {/* 右侧：已学徽标 + 台账呼吸 dot + 头像 */}
        <div className="shrink-0 flex items-center gap-2">
          {learnedCount > 0 && (
            <span className="chip-success text-xs">
              已学 <span className="mono mx-0.5">{learnedCount}</span>
            </span>
          )}
          {hasUnseenLedger && (
            <span
              aria-hidden
              className="h-2 w-2 rounded-full bg-amber-500 animate-pulse"
              title="本次调整有新条目"
            />
          )}
          {currentAvatar ? (
            <img src={currentAvatar} alt={persona?.label ?? "用户"} className="w-24 h-24 rounded-xl object-cover" />
          ) : (
            <div
              className="w-24 h-24 rounded-xl flex items-center justify-center"
              style={{ background: PERSONA_ICON_GRADIENT }}
            >
              <PersonaIcon className="w-10 h-10 text-ink-900" strokeWidth={2} />
            </div>
          )}
        </div>
      </div>
    );
  }

  const persona = preferences?.persona;
  const PersonaIcon = persona
    ? personaIconFromEmoji(persona.icon, persona.label)
    : Icons.user;

  return (
    <div className="w-full flex items-start gap-3 animate-fade-in pr-20">
      {/* 左侧：内容部分，透明背景，外层硬约束总高有界 */}
      <div className="flex-1 max-h-[60vh] overflow-y-auto scrollbar-thin">
        <div className="px-4 pt-2 pb-3">
          {!persona ? (
            <div className="text-ink-500 text-xs">加载中…</div>
          ) : (
            <>
              <PersonaHeader persona={persona} onCollapse={() => setOpen(false)} />

              <PersonaSection persona={persona} />

              {/* 房间模式：「这次对话学到的」整区隐藏（用户拍板 + 方案 §13）。 */}
              {!collabMode && (
                <LearnedSection
                  topPriors={preferences?.top_priors ?? []}
                  suggestedDistanceKm={preferences?.suggested_distance_max_km ?? null}
                  recentTrips={preferences?.recent_trips ?? []}
                />
              )}

              <LedgerSection entries={visibleLedger} itinerary={itinerary} />

              {/* 房间模式：清空按钮隐藏（清空全房间共享键是影响所有成员的
                  动作，不该被单个成员静默触发；方案 §13）。 */}
              {!collabMode && (
                <div className="mt-3 pt-2 border-t border-black/[0.06] flex justify-end">
                  <button
                    type="button"
                    onClick={() => resetUserMemory(roomSessionId)}
                    className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-rose-500 transition-colors"
                    title="清空当前会话累积的学到的记忆（画像/台账不受影响）"
                  >
                    <Icons.trash className="w-3 h-3" strokeWidth={2} />
                    清空学到的记忆
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* 右侧：头像图片，单独展示 */}
      <div className="shrink-0">
        {currentAvatar ? (
          <img src={currentAvatar} alt={persona?.label ?? "用户"} className="w-24 h-24 rounded-xl object-cover" />
        ) : (
          <div
            className="w-24 h-24 rounded-xl flex items-center justify-center"
            style={{ background: PERSONA_ICON_GRADIENT }}
          >
            <PersonaIcon className="w-10 h-10 text-ink-900" strokeWidth={2} />
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// 呼吸 dot 已读判定（3.1）：展开过一次即消——用 localStorage 记"上次展开时
// demand_ledger 长度"，长度不变则视为"看过"。
// ============================================================

function useUnseenLedgerDot(currentLength: number): boolean {
  const [seenCount, setSeenCount] = useState<number | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(LEDGER_SEEN_KEY);
      setSeenCount(raw != null ? Number(raw) : 0);
    } catch {
      setSeenCount(0);
    }
  }, []);

  if (seenCount == null) return false;
  return currentLength > seenCount;
}

/** 展开态挂载时把当前台账长度写进"已读游标"（对应折叠态呼吸 dot 的消散）。 */
function useMarkLedgerSeen(currentLength: number) {
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(LEDGER_SEEN_KEY, String(currentLength));
    } catch {
      /* 忽略 */
    }
  }, [currentLength]);
}

// ============================================================
// 画像头部（label + 收起箭头）
// ============================================================

function PersonaHeader({
  persona,
  onCollapse,
}: {
  persona: Persona;
  onCollapse: () => void;
}) {
  return (
    <div className="text-right">
      <div className="flex items-center justify-end gap-2">
        <h3 className="text-2xl font-semibold text-ink-900 tracking-tight">
          {persona.label}
        </h3>
        <button
          type="button"
          onClick={onCollapse}
          aria-label="收起偏好画像"
          className="text-ink-500 hover:text-caramel-300 transition-colors"
        >
          <svg
            className="w-3.5 h-3.5 transition-transform hover:-translate-y-0.5"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path
              d="M3 7.5L6 4.5L9 7.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
    </div>
  );
}

// ============================================================
// 区一：画像（persona 模板，随画像切换，不随会话累积变化）
// ============================================================

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-3 pt-3 border-t border-black/[0.06] flex items-center justify-end">
      <span className="text-xs font-medium text-ink-500">{children}</span>
    </div>
  );
}

function PersonaSection({ persona }: { persona: Persona }) {
  const templateTags = [
    ...persona.default_tags.physical,
    ...persona.default_tags.dietary,
    ...persona.default_tags.experience,
  ];

  return (
    <>
      <SectionLabel>画像</SectionLabel>
      <div className="mt-2 flex flex-wrap items-center justify-end gap-x-3 gap-y-2">
        {templateTags.length > 0 && (
          <div className="flex flex-wrap items-center justify-end gap-2">
            {templateTags.slice(0, PERSONA_TAGS_PREVIEW_N).map((t) => (
              <span
                key={t}
                className="inline-flex items-center rounded-full border border-black/[0.08] bg-black/[0.03] px-3 py-1 text-xs font-medium leading-none text-ink-700"
              >
                {t}
              </span>
            ))}
          </div>
        )}
        <div className="inline-flex items-center gap-1.5 rounded-full border border-black/[0.07] bg-white/70 px-3 py-1 text-xs text-ink-600">
          距离 {persona.default_distance_max_km}km
        </div>
      </div>
    </>
  );
}

// ============================================================
// 区二：这次对话学到的（偏好笔记 + 距离笔记 + 去过 + 诚实空态）
// 房间模式下由父组件整区不渲染（不在本组件内判 collabMode，保持组件职责
// 单一——"要不要显示这个区"是父组件的编排决策）。
// ============================================================

function LearnedSection({
  topPriors,
  suggestedDistanceKm,
  recentTrips,
}: {
  topPriors: string[];
  suggestedDistanceKm: number | null;
  recentTrips: RecentTrip[];
}) {
  const [tripsExpanded, setTripsExpanded] = useState(false);

  const prefNote = preferenceNote(topPriors);
  const distNote = distanceNote(suggestedDistanceKm);
  const hasTrips = recentTrips.length > 0;
  const visibleTrips = tripsExpanded ? recentTrips : recentTrips.slice(0, TRIPS_PREVIEW_N);
  const hiddenTripsCount = recentTrips.length - visibleTrips.length;

  const isEmpty = !prefNote && !distNote && !hasTrips;

  return (
    <>
      <SectionLabel>这次对话学到的</SectionLabel>
      {isEmpty ? (
        <div className="mt-2 flex justify-end">
          <span className="text-xs text-ink-400">还没学到新偏好，继续聊聊看</span>
        </div>
      ) : (
        <div className="mt-2 space-y-1.5 text-right">
          {prefNote && (
            <div className="text-xs text-ink-600">
              <span className="text-ink-400">偏好 · </span>
              {prefNote}
            </div>
          )}
          {distNote && (
            <div className="text-xs text-ink-600">
              <span className="text-ink-400">距离 · </span>
              {distNote}
            </div>
          )}
          {hasTrips && (
            <div className="space-y-1">
              {visibleTrips.map((trip, i) => (
                <div key={`${trip.timestamp}-${i}`} className="text-xs text-ink-600">
                  <span className="text-ink-400">去过 · </span>
                  {trip.summary}
                </div>
              ))}
              {hiddenTripsCount > 0 && (
                <button
                  type="button"
                  onClick={() => setTripsExpanded(true)}
                  className="text-xs text-caramel-400 hover:text-caramel-300 transition-colors"
                >
                  查看全部 {recentTrips.length} 条 ⌄
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </>
  );
}

// ============================================================
// 区三：本次调整（诉求台账收编）
// ============================================================

const _STATUS_THEME: Record<string, { label: string; className: string }> = {
  active: { label: "生效中", className: "bg-amber-400/15 text-amber-700 border-amber-400/30" },
  satisfied: { label: "已满足", className: "bg-emerald-500/12 text-emerald-700 border-emerald-500/30" },
};

function LedgerSection({
  entries,
  itinerary,
}: {
  entries: DemandLedgerEntry[];
  itinerary: Itinerary | null;
}) {
  const [expanded, setExpanded] = useState(false);
  useMarkLedgerSeen(entries.length);

  if (entries.length === 0) {
    return (
      <>
        <SectionLabel>本次调整</SectionLabel>
        <div className="mt-2 flex justify-end">
          <span className="text-xs text-ink-400">还没有调整过，换个菜试试</span>
        </div>
      </>
    );
  }

  // 最新在前（demandLedger 是追加写入的 append-only 列表）
  const ordered = [...entries].reverse();
  const visible = expanded ? ordered : ordered.slice(0, LEDGER_PREVIEW_N);
  const hiddenCount = ordered.length - visible.length;

  return (
    <>
      <SectionLabel>本次调整</SectionLabel>
      <div className="mt-2 space-y-1.5">
        {visible.map((entry, i) => {
          const theme = _STATUS_THEME[entry.status] ?? _STATUS_THEME.active;
          return (
            <div
              key={`${entry.created_at}-${i}`}
              className="flex items-start justify-end gap-2 text-xs"
            >
              <span className="text-ink-600 text-right break-all">
                {ledgerEntryLine(entry, itinerary)}
              </span>
              <span
                className={cn(
                  "shrink-0 px-1.5 py-0 rounded border text-[11px] leading-[1.4] font-medium",
                  theme.className,
                )}
              >
                {theme.label}
              </span>
            </div>
          );
        })}
        {hiddenCount > 0 && (
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="text-xs text-caramel-400 hover:text-caramel-300 transition-colors"
            >
              查看更多 {hiddenCount} 条 ⌄
            </button>
          </div>
        )}
      </div>
    </>
  );
}
