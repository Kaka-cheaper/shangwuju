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

import { Icons, personaIconFromEmoji } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
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
    const top_priors = preferences?.top_priors ?? [];
    const acceptedCount = preferences?.memory
      ? Object.values(preferences.memory.accepted_tags.counts).reduce(
          (a, b) => a + b,
          0,
        )
      : 0;
    const previewTags = top_priors.slice(0, 2);
    const PersonaIcon = persona
      ? personaIconFromEmoji(persona.icon)
      : Icons.user;

    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="查看当前用户的偏好画像与历史记忆"
        className={cn(
          "w-full group relative flex items-center justify-between gap-3",
          "rounded-lg border border-white/[0.08] bg-white/[0.04]",
          "px-3.5 py-2.5 text-left",
          "hover:border-caramel-400/40 hover:bg-white/[0.06]",
          "hover:shadow-glow-caramel transition-all duration-200",
          "active:scale-[0.99]",
          "backdrop-blur-sm overflow-hidden",
        )}
      >
        {/* hover 时浮现暖焦糖光斑（替代原紫粉） */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-300"
          style={{ background: HOVER_GLOW }}
        />
        <div className="relative flex items-center gap-2.5 min-w-0 flex-1">
          <div
            className="w-8 h-8 rounded-md flex items-center justify-center shrink-0 border border-white/[0.08]"
            style={{ background: PERSONA_ICON_GRADIENT }}
          >
            <PersonaIcon
              className="w-4 h-4 text-caramel-300"
              strokeWidth={1.75}
            />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-medium text-ink-900 truncate tracking-tight">
                {persona?.label ?? "偏好画像"}
              </span>
              {acceptedCount > 0 && (
                <span className="chip-success shrink-0 text-[10px]">
                  已学 <span className="mono mx-0.5">{acceptedCount}</span> 次
                </span>
              )}
            </div>
            {previewTags.length > 0 ? (
              <div className="mt-1 flex flex-wrap gap-1 overflow-hidden">
                {previewTags.map((t) => (
                  <span
                    key={t}
                    className="chip-warm text-[10px] truncate max-w-[100px]"
                  >
                    {t}
                  </span>
                ))}
                {top_priors.length > 2 && (
                  <span className="text-[10px] text-ink-500 self-center">
                    · 共 {top_priors.length} 项
                  </span>
                )}
              </div>
            ) : (
              <div className="mt-0.5 text-[11px] text-ink-500">
                {persona?.notes ?? "点击查看 Agent 已学到的偏好"}
              </div>
            )}
          </div>
        </div>
        <div className="relative text-[11px] text-ink-500 group-hover:text-caramel-300 transition-colors shrink-0 flex items-center gap-1">
          <span>展开</span>
          <svg
            className="w-3 h-3 transition-transform group-hover:translate-y-0.5"
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
    <div className="card p-4 space-y-3 text-xs animate-fade-in">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Icons.user
            className="w-3.5 h-3.5 text-caramel-300"
            strokeWidth={2}
          />
          <h2 className="text-[12px] font-medium text-ink-900 tracking-tight">
            偏好画像
          </h2>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={resetUserMemory}
            className="inline-flex items-center gap-1 text-[11px] text-ink-500 hover:text-rose-400 transition-colors"
            title="清空当前用户的累积偏好（演示完清场用）"
          >
            <Icons.trash className="w-3 h-3" strokeWidth={2} />
            清空记忆
          </button>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="text-ink-500 hover:text-ink-900 transition-colors"
            aria-label="关闭"
          >
            <Icons.close className="w-3.5 h-3.5" strokeWidth={2} />
          </button>
        </div>
      </div>

      {!persona ? (
        <div className="text-ink-500">加载中…</div>
      ) : (
        <>
          {/* persona 档案：玻璃半透 + 焦糖暖底 */}
          <div className="rounded-md bg-white/[0.04] border border-white/[0.08] px-3 py-2.5 backdrop-blur-sm">
            <div className="flex items-center gap-2">
              <div
                className="w-7 h-7 rounded-md flex items-center justify-center shrink-0 border border-white/[0.08]"
                style={{ background: PERSONA_ICON_GRADIENT }}
              >
                <PersonaIcon
                  className="w-3.5 h-3.5 text-caramel-300"
                  strokeWidth={1.75}
                />
              </div>
              <span className="font-medium text-ink-900 tracking-tight">
                {persona.label}
              </span>
            </div>
            <p className="mt-1.5 text-[11px] text-ink-600 leading-relaxed">
              {persona.notes}
            </p>
          </div>

          <div>
            <div className="section-title mb-1">高优先（档案 + 历史）</div>
            {top_priors.length === 0 ? (
              <div className="text-ink-500 text-[11px]">（暂无）</div>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {top_priors.map((t) => (
                  <span key={t} className="chip-warm">
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>

          {suggested != null && (
            <div className="text-[11px] text-ink-600">
              建议默认距离：
              <span className="font-medium text-ink-900 mono ml-0.5">
                {suggested} km
              </span>
            </div>
          )}

          <div>
            <div className="section-title mb-1">最近接受 top 5</div>
            {acceptedTop.length === 0 ? (
              <div className="text-ink-500 text-[11px]">
                （还没确认过任何方案）
              </div>
            ) : (
              <ul className="space-y-1">
                {acceptedTop.map(([t, n]) => (
                  <li
                    key={t}
                    className="flex justify-between items-center text-[11px]"
                  >
                    <span className="text-ink-800">{t}</span>
                    <span className="text-ink-500 mono">×{n}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {rejectedTop.length > 0 && (
            <div>
              <div className="section-title mb-1">最近拒绝 top 5</div>
              <ul className="space-y-1">
                {rejectedTop.map(([t, n]) => (
                  <li
                    key={t}
                    className="flex justify-between items-center text-[11px]"
                  >
                    <span className="text-ink-800">{t}</span>
                    <span className="text-ink-500 mono">×{n}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}
