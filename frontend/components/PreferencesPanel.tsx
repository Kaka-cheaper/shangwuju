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
 */

import { useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";

import { Icons, personaIconFromEmoji } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import type { Persona } from "@/lib/types";
import { cn } from "@/lib/utils";

// ============================================================
// 局部主题常量（避免散落 inline rgba；主题改时改一处即可）
// ============================================================

/** persona icon 渐变背景（焦糖琥珀两层） */
const PERSONA_ICON_GRADIENT =
  "linear-gradient(135deg, rgba(184,137,90,0.18) 0%, rgba(160,106,58,0.14) 100%)";

/** 折叠态 hover 时浮现的暖光斑（右下角放射） */
const HOVER_GLOW =
  "radial-gradient(circle at 100% 100%, rgba(184,137,90,0.18) 0%, transparent 60%)";

/** localStorage key（持久化展开态） */
const STORAGE_KEY = "shangwuju.preferences.open";

export default function PreferencesPanel() {
  const currentUserId = useChatStore((s) => s.currentUserId);
  const preferences = useChatStore((s) => s.preferences);
  const refreshPreferences = useChatStore((s) => s.refreshPreferences);
  const resetUserMemory = useChatStore((s) => s.resetUserMemory);

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

  /** 包装 setOpen：同步持久化 */
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

  // 用户切换时刷一次（store 已自管缓存，open 变化不再触发额外刷新）
  useEffect(() => {
    if (currentUserId) refreshPreferences();
  }, [currentUserId, refreshPreferences]);

  if (!open) {
    const persona = preferences?.persona;
    const acceptedCount = preferences?.memory
      ? Object.values(preferences.memory.accepted_tags.counts).reduce(
          (a, b) => a + b,
          0,
        )
      : 0;
    const PersonaIcon = persona
      ? personaIconFromEmoji(persona.icon)
      : Icons.user;
    // 副标题：用 persona.notes 摘要，没有时退回静默引导
    const subtitle = persona?.notes ?? "点击查看 Agent 已学到的偏好";

    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="查看当前用户的偏好画像与历史记忆"
        className={cn(
          "w-full group relative flex items-center gap-3.5",
          "rounded-xl border border-black/[0.06] bg-white",
          "px-4 py-3 text-left",
          "hover:border-black/[0.12] hover:shadow-sm",
          "transition-all duration-200",
          "active:scale-[0.99]",
          "overflow-hidden",
        )}
      >
        {/* 大 icon —— 视觉锚点 */}
        <div
          className="relative w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
          style={{ background: "#FFD100" }}
        >
          <PersonaIcon
            className="w-5 h-5 text-ink-900"
            strokeWidth={2}
          />
        </div>

        {/* 主体：两行文字（label + notes 摘要），重点突出 */}
        <div className="relative min-w-0 flex-1">
          <div className="text-base font-semibold text-ink-900 truncate tracking-tight">
            {persona?.label ?? "偏好画像"}
          </div>
          <div className="mt-0.5 text-sm text-ink-500 truncate leading-relaxed">
            {subtitle}
          </div>
        </div>

        {/* 右侧：已学计数（小）+ 展开箭头 */}
        <div className="relative shrink-0 flex items-center gap-2">
          {acceptedCount > 0 && (
            <span className="chip-success text-xs">
              已学 <span className="mono mx-0.5">{acceptedCount}</span>
            </span>
          )}
          <span className="text-ink-400 group-hover:text-ink-700 transition-colors">
            <svg
              className="w-3.5 h-3.5 transition-transform group-hover:translate-y-0.5"
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
          </span>
        </div>
      </button>
    );
  }

  const persona = preferences?.persona;
  const memory = preferences?.memory;
  const top_priors = preferences?.top_priors ?? [];
  const suggested = preferences?.suggested_distance_max_km;

  const acceptedTop = memory
    ? Object.entries(memory.accepted_tags.counts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
    : [];
  const rejectedTop = memory
    ? Object.entries(memory.rejected_tags.counts)
        .sort((a, b) => b[1] - a[1])
        .filter(([, n]) => n > 0)
        .slice(0, 5)
    : [];

  const PersonaIcon = persona
    ? personaIconFromEmoji(persona.icon)
    : Icons.user;

  return (
    <div className="card overflow-hidden animate-fade-in">
      {/* 头部：始终在最上方；展开/折叠态高度一致（min-h-[64px] = 折叠态 icon 40+双行文字+padding）
          整行可点击收起，视觉规格 = 折叠态主标题（text-base semibold）
          直接占满 card 宽度（card overflow-hidden + button w-full + 顶部 rounded-t-2xl 与 card 圆角对齐） */}
      <button
        type="button"
        onClick={() => setOpen(false)}
        aria-label="收起偏好画像"
        className={cn(
          "group w-full px-4 py-3 min-h-[64px]",
          "flex items-center gap-3.5",
          "hover:bg-black/[0.02] transition-colors",
          "text-left",
          "border-b border-black/[0.06]",
        )}
      >
        <div className="min-w-0 flex-1">
          <div className="text-base font-semibold text-ink-900 tracking-tight">
            偏好画像
          </div>
        </div>
        <span className="text-ink-500 group-hover:text-caramel-300 transition-colors shrink-0">
          <svg
            className="w-3.5 h-3.5 transition-transform group-hover:-translate-y-0.5"
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
        </span>
      </button>

      {/* 内容区 */}
      <div className="px-4 pt-3 pb-3">
        {!persona ? (
          <div className="text-ink-500 text-xs">加载中…</div>
        ) : (
          <ContentSections
            persona={persona}
            PersonaIcon={PersonaIcon}
            top_priors={top_priors}
            suggested={suggested}
            acceptedTop={acceptedTop}
            rejectedTop={rejectedTop}
            onResetMemory={resetUserMemory}
          />
        )}
      </div>
    </div>
  );
}

// ============================================================
// 展开态内容区：persona 英雄区 + 标签 + 距离 + 历史 + 清空按钮
// 抽出为独立组件，避免主体 return 嵌套过深
// ============================================================

interface ContentSectionsProps {
  persona: Persona;
  PersonaIcon: LucideIcon;
  top_priors: string[];
  suggested: number | null | undefined;
  acceptedTop: [string, number][];
  rejectedTop: [string, number][];
  onResetMemory: () => void;
}

function ContentSections({
  persona,
  PersonaIcon,
  top_priors,
  suggested,
  acceptedTop,
  rejectedTop,
  onResetMemory,
}: ContentSectionsProps) {
  return (
    <>
      {/* persona 英雄区：icon + label + notes 横排紧凑 */}
      <div className="flex items-center gap-3">
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
          style={{ background: "#FFD100" }}
        >
          <PersonaIcon
            className="w-5 h-5 text-ink-900"
            strokeWidth={2}
          />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-semibold text-ink-900 tracking-tight">
            {persona.label}
          </h3>
          {persona.notes && (
            <p className="text-sm text-ink-600 leading-relaxed mt-1">
              {persona.notes}
            </p>
          )}
        </div>
      </div>

      {/* 偏好标签 + 默认距离：同行紧凑 */}
      <div className="mt-3 pt-3 border-t border-black/[0.06] flex flex-wrap items-center gap-x-4 gap-y-2">
        {top_priors.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm text-ink-500 mr-1">常去</span>
            {top_priors.map((t) => (
              <span key={t} className="chip-warm text-sm">
                {t}
              </span>
            ))}
          </div>
        )}
        {suggested != null && (
          <div className="flex items-baseline gap-1 ml-auto">
            <span className="text-sm text-ink-500">距离</span>
            <span className="text-[14px] font-semibold text-ink-900 mono">
              {suggested}
              <span className="ml-0.5 text-sm font-normal text-ink-500">km</span>
            </span>
          </div>
        )}
      </div>

      {/* 历史记录：接受 / 拒绝 横排紧凑 */}
      {(acceptedTop.length > 0 || rejectedTop.length > 0) && (
        <div className="mt-3 pt-3 border-t border-black/[0.06] flex flex-wrap gap-x-6 gap-y-2">
          {acceptedTop.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-ink-500">接受</span>
              {acceptedTop.map(([t, n]) => (
                <span key={t} className="text-sm text-ink-700">
                  {t}<span className="text-ink-400 mono ml-0.5">×{n}</span>
                </span>
              ))}
            </div>
          )}
          {rejectedTop.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-ink-500">拒绝</span>
              {rejectedTop.map(([t, n]) => (
                <span key={t} className="text-sm text-ink-700">
                  {t}<span className="text-ink-400 mono ml-0.5">×{n}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* footer：清空记忆按钮 */}
      <div className="mt-3 pt-2 border-t border-black/[0.06] flex justify-end">
        <button
          type="button"
          onClick={onResetMemory}
          className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-rose-500 transition-colors"
          title="清空当前用户的累积偏好（演示完清场用）"
        >
          <Icons.trash className="w-3 h-3" strokeWidth={2} />
          清空记忆
        </button>
      </div>
    </>
  );
}


