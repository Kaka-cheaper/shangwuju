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
 *   - 「本次调整」：诉求台账收编（`demandLedger` store 字段）。人话化用
 *     `lib/ledger-copy.ts`（id→店名、方向词→中文、词典外兜底原词+
 *     console.warn）。只显生效中/已满足，被顶替的条目砍掉（不渲染）；
 *     ≤5 条 + "还有 N 条"折叠。
 *
 *     约束流合并 A1（2026-07-12，用户拍板收尾）：房间模式下「本次调整」台账
 *     整区**隐藏**——同一份台账数据由 `CollabBar.tsx` 顶栏下拉的合并展示流
 *     （`lib/collab-feed.ts::mergeCollabFeed`，带归名+状态）承担，桌面房间态
 *     两处台账重复，删本面板这处（与移动端 `MobilePreferencesCard`、独立
 *     `ConstraintFeed.tsx` 同批收口到 CollabBar 下拉一处）。但**本面板整体
 *     不删**：它是持续存在的"我的偏好画像"面板，「画像」区单人/房间两态都要用；
 *     隐藏的只是房间态的「本次调整」台账区（与「这次对话学到的」同款 !collabMode
 *     门控），不是整个组件。单人模式台账照旧显示，demandLedger 数据管线不动。
 *
 * 三列并排重排（UI 修复批·2026-07，主代理拍板覆盖前一版诊断的"头像移左上"
 * 方案）：头像**保持右侧不动**——它是既有布局的稳定锚点，折叠/展开态位置
 * 一致，不需要为了给内容腾宽度而挪动它，宽横条本身已经够宽。三区改成**三列
 * 并排**填满头像左侧的横向空间（`grid-cols-[repeat(auto-fit,minmax(0,1fr))]`——
 * 用 `auto-fit` 而不是写死 `grid-cols-3`，是因为房间模式下"这次对话学到的"
 * 整列不渲染，此时应自动收成两列平分宽度，不留一条空轨道），每列内部竖排、
 * 左对齐——而不是旧版"内容区当一整栏、三区依次纵向堆叠、每行还各自
 * justify-end 贴右边界"（那是本次修复要根治的"整体贴右、左边一大块空白"的
 * 病灶：内容区够宽但每一行都不用宽度）。三列横向利用宽度，纵向自然更矮，
 * 不必再靠"缩头像腾高度"这种以退为进的办法。
 *   - 空列不占位：某列没有内容时，对应 Section 组件（`LearnedSection`/
 *     `LedgerSection`）直接返回 `null`，grid 自动把宽度让给其余列（css grid
 *     不渲染的轨道不产生视觉留白，不需要额外写"隐藏"逻辑）。
 *   - 有内容时淡入：三列的共用容器 `ColumnShell` 套 `animate-fade-in`（同
 *     文件已有的揭幕动效语汇，不新造一套）——组件从不渲染（null）到渲染
 *     （有内容）那一刻，React 挂载新 DOM 节点，动效随挂载自然触发。
 *   - 台账行状态徽标放行首（先看状态，再看内容）——同上一版诊断的结论，
 *     三列布局下依然成立：徽标在前更符合"一览生效状态"的台账核心价值。
 *
 * 硬约束：面板总高有界（外层 max-h + overflow-y-auto），每区超量各自独立
 * 折叠，不挤压下方 QuickScenarios/ChatDock。
 *
 * 房间模式清空按钮：整个隐藏（清空一个全房间共享键是影响所有成员的动作，
 * 不该被单个成员静默触发；见方案 §13）。
 *
 * 清空作用域全环闭合（UI 修复批）：`resetUserMemory` 现在两轨一起清——
 * UserMemory（标签/行程轨，后端 `reset_memory` 原有职责）+ `demandLedger`
 * 台账轨（新增，见 store.ts::resetUserMemory 内注释）。此前只清前者，
 * "清空学到的记忆"点了常常像没反应——因为「这次对话学到的」区本来就常是
 * 空态（没聊够几轮），真正有内容、用户盯着看效果的往往是"本次调整"台账区，
 * 而台账完全不受这个按钮影响，落差感就是"点了没用"的真机体验根源。两轨
 * 一起清 + 下面的清空反馈动效，才是名副其实的"清空"。
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
  u_dad: buildAppPath("/avatars/xinshoubaba.webp"),
  u_biz: buildAppPath("/avatars/shangwubailing.webp"),
  u_grandma: buildAppPath("/avatars/xiaoshunernv.webp"),
  u_solo: buildAppPath("/avatars/dujuqingnian.webp"),
  u_couple: buildAppPath("/avatars/qinglvdang.webp"),
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

  // 「本次调整」台账：房间/单人共用同一 store 字段。只显生效中/已满足，
  // 被顶替的条目砍掉（不是"折叠"，是不渲染——本面板的台账价值是"当下
  // 有效的诉求一览"，不是审计历史；审计历史/谁提了原话这类"过程"由
  // `CollabBar.tsx` 的合并展示流承担，见约束流合并 A1）。
  const visibleLedger = useMemo(
    () => (demandLedger ?? []).filter((e) => e.status !== "superseded"),
    [demandLedger],
  );

  // Hooks 必须无条件调用（不能塞进下面的 `if (!open)` 分支）——折叠态/展开态
  // 都要跑这个 hook，只是折叠态才消费它的返回值渲染呼吸 dot。
  const hasUnseenLedger = useUnseenLedgerDot(visibleLedger.length);

  if (!open) {
    const persona = preferences?.persona;
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

        {/* 右侧：台账呼吸 dot（低调非计数）+ 头像——"已学 N" 绿色计数 chip
            已删（用户拍板）：折叠态副标题本身已经承担"里面有内容"的引导
            语义，不需要用数字强调；呼吸 dot 保留是因为它是"有新东西"的
            提示，不是计数，两者语义不同，不是同一件事被删了两次。 */}
        <div className="shrink-0 flex items-center gap-2">
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

              {/* 三列并排（主代理拍板，见文件头 docstring "三列并排重排"）：
                  画像 / 这次对话学到的 / 本次调整 填满宽横条，每列竖排左对齐。
                  `items-start` 让高度不同的列各自顶对齐，不被最高列拉伸撑开；
                  房间模式下"这次对话学到的"整区不渲染（grid 自动收窄成两列，
                  不留空轨道）。 */}
              <div className="mt-3 pt-3 border-t border-black/[0.06] grid grid-cols-[repeat(auto-fit,minmax(0,1fr))] gap-x-6 gap-y-3 items-start">
                <PersonaSection persona={persona} />
                {!collabMode && (
                  <LearnedSection
                    topPriors={preferences?.top_priors ?? []}
                    suggestedDistanceKm={preferences?.suggested_distance_max_km ?? null}
                    recentTrips={preferences?.recent_trips ?? []}
                  />
                )}
                {/* 约束流合并 A1 收尾（2026-07-12 用户拍板）：房间模式下「本次调整」
                    整区隐藏——它现在由 CollabBar 顶栏下拉的合并展示流（约束流+台账，
                    带归名/状态）承担，桌面房间态两处台账重复，删这处。单人模式照旧显示
                    （demandLedger 数据管线不动，只是本面板不再在房间态渲染台账区）。 */}
                {!collabMode && (
                  <LedgerSection entries={visibleLedger} itinerary={itinerary} />
                )}
              </div>

              {/* 房间模式：清空按钮隐藏（清空全房间共享键是影响所有成员的
                  动作，不该被单个成员静默触发；方案 §13）。 */}
              {!collabMode && (
                <div className="mt-3 pt-2 border-t border-black/[0.06] flex justify-end">
                  <button
                    type="button"
                    onClick={() => resetUserMemory(roomSessionId)}
                    className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-rose-500 transition-colors"
                    title="清空当前会话累积的学到的记忆和本次调整（画像不受影响）"
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

      {/* 右侧：头像图片，单独展示——保持原位不动（主代理拍板：覆盖前一版
          诊断"头像挪左上"方案，宽横条本身已够宽，不需要靠挪头像腾宽度）。 */}
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
    <div className="flex items-center justify-between gap-2">
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
  );
}

// ============================================================
// 三列共用的列容器：左对齐竖排 + 有内容才淡入（三列并排重排）
// ============================================================

/** 三列各自的小标题——左对齐（三列布局下不再需要"贴右当锚点"，见文件头
 * docstring）。列本身"有没有内容"由各 Section 组件判断，本组件只管标签
 * 长相，不做"是否渲染"的决策（那是调用方的事，保持单一职责）。 */
function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="text-xs font-medium text-ink-500">{children}</div>;
}

/** 三列并排布局的列容器——`animate-fade-in` 是列从"无内容"变"有内容"那一刻
 * 的入场动效（同文件已有揭幕动效语汇），`min-w-0` 防止长文本撑破 grid 轨道。 */
function ColumnShell({ children }: { children: React.ReactNode }) {
  return <div className="min-w-0 animate-fade-in space-y-2">{children}</div>;
}

// ============================================================
// 区一：画像（persona 模板，随画像切换，不随会话累积变化）
// ============================================================

function PersonaSection({ persona }: { persona: Persona }) {
  const templateTags = [
    ...persona.default_tags.physical,
    ...persona.default_tags.dietary,
    ...persona.default_tags.experience,
  ];

  // 画像区理论上恒有内容（persona 模板自带 default_distance_max_km），
  // 不判空——但保持结构对称（同 Learned/Ledger 一样包一层 ColumnShell），
  // 三列渲染逻辑统一，不搞"画像区特殊、另外两区判空"的不一致。
  return (
    <ColumnShell>
      <SectionLabel>画像</SectionLabel>
      <div className="flex flex-wrap items-center gap-2">
        {templateTags.slice(0, PERSONA_TAGS_PREVIEW_N).map((t) => (
          <span
            key={t}
            className="inline-flex items-center rounded-full border border-black/[0.08] bg-black/[0.03] px-3 py-1 text-xs font-medium leading-none text-ink-700"
          >
            {t}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5 rounded-full border border-black/[0.07] bg-white/70 px-3 py-1 text-xs text-ink-600">
          距离 {persona.default_distance_max_km}km
        </span>
      </div>
    </ColumnShell>
  );
}

// ============================================================
// 区二：这次对话学到的（偏好笔记 + 距离笔记 + 去过）
// 房间模式下由父组件整区不渲染（不在本组件内判 collabMode，保持组件职责
// 单一——"要不要显示这个区"是父组件的编排决策）。
//
// 空区不显示（三列并排重排拍板）：此前空态会渲染"还没学到新偏好，继续
// 聊聊看"这句占位——三列布局下，某列没内容时整列不占位（grid 自动把宽度
// 让给其余列），不再渲染空态句子，改为组件直接返回 null。
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

  if (!prefNote && !distNote && !hasTrips) return null;

  return (
    <ColumnShell>
      <SectionLabel>这次对话学到的</SectionLabel>
      <div className="space-y-1.5">
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
    </ColumnShell>
  );
}

// ============================================================
// 区三：本次调整（诉求台账收编）
// ============================================================

// 去绿归色（配色克制设计终稿）：已满足 = 完成的事，隐退成沉静中性灰 + 勾，
// 和「生效中」的琥珀金形成"进行中(暖) vs 已完成(静)"的对比，不再抢注意力。
const _STATUS_THEME: Record<string, { label: string; className: string; showCheck?: boolean }> = {
  active: { label: "生效中", className: "bg-amber-400/15 text-amber-700 border-amber-400/30" },
  satisfied: { label: "已满足", className: "bg-ink-200 text-ink-500 border-ink-300", showCheck: true },
};

/** 空区不显示（三列并排重排拍板）：台账为空时列直接不渲染（`useMarkLedgerSeen`
 * 是"展开过一次即消呼吸 dot"的已读游标写入，必须无条件调用——不能塞进
 * 下面的 `entries.length === 0` 分支，否则空态→有内容那一刻这个 effect
 * 会因为 hook 调用路径变化而表现异常，同文件顶部 `useUnseenLedgerDot` 的
 * 既有注释同一条 hooks 规则）。 */
function LedgerSection({
  entries,
  itinerary,
}: {
  entries: DemandLedgerEntry[];
  itinerary: Itinerary | null;
}) {
  const [expanded, setExpanded] = useState(false);
  useMarkLedgerSeen(entries.length);

  if (entries.length === 0) return null;

  // 最新在前（demandLedger 是追加写入的 append-only 列表）
  const ordered = [...entries].reverse();
  const visible = expanded ? ordered : ordered.slice(0, LEDGER_PREVIEW_N);
  const hiddenCount = ordered.length - visible.length;

  return (
    <ColumnShell>
      <SectionLabel>本次调整</SectionLabel>
      <div className="space-y-1.5">
        {visible.map((entry, i) => {
          const theme = _STATUS_THEME[entry.status] ?? _STATUS_THEME.active;
          return (
            <div
              key={`${entry.created_at}-${i}`}
              className="flex items-start gap-2 text-xs"
            >
              {/* 状态徽标行首（先看状态，再看内容）——同上一版诊断结论，
                  三列布局下依然是台账"一览生效状态"核心价值的正确顺序。 */}
              <span
                className={cn(
                  "shrink-0 inline-flex items-center gap-0.5 px-1.5 py-0 rounded border text-[11px] leading-[1.4] font-medium",
                  theme.className,
                )}
              >
                {theme.showCheck && (
                  <Icons.success className="w-2.5 h-2.5 shrink-0" strokeWidth={2.5} />
                )}
                {theme.label}
              </span>
              <span className="text-ink-600 break-all">
                {ledgerEntryLine(entry, itinerary)}
              </span>
            </div>
          );
        })}
        {hiddenCount > 0 && (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="text-xs text-caramel-400 hover:text-caramel-300 transition-colors"
          >
            查看更多 {hiddenCount} 条 ⌄
          </button>
        )}
      </div>
    </ColumnShell>
  );
}
