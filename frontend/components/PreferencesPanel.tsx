"use client";

/**
 * PreferencesPanel —— 用户偏好面板（B+D 范式：去 emoji，灰阶克制）。
 */

import { useEffect, useState } from "react";

import { Icons, personaIconFromEmoji } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export default function PreferencesPanel() {
  const currentUserId = useChatStore((s) => s.currentUserId);
  const preferences = useChatStore((s) => s.preferences);
  const refreshPreferences = useChatStore((s) => s.refreshPreferences);
  const resetUserMemory = useChatStore((s) => s.resetUserMemory);

  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (currentUserId) refreshPreferences();
  }, [currentUserId, refreshPreferences]);

  useEffect(() => {
    if (open && !preferences) {
      refreshPreferences();
    }
  }, [open, preferences, refreshPreferences]);

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
          "w-full group flex items-center justify-between gap-3",
          "rounded-lg border border-ink-200 bg-white",
          "px-3.5 py-2.5 text-left",
          "hover:border-ink-300 hover:bg-ink-50/40 transition-colors duration-150",
          "active:bg-ink-100",
        )}
      >
        <div className="flex items-center gap-2.5 min-w-0 flex-1">
          <div className="w-8 h-8 rounded-md bg-ink-100 flex items-center justify-center shrink-0">
            <PersonaIcon
              className="w-4 h-4 text-ink-700"
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
                  <span key={t} className="chip-accent text-[10px] truncate max-w-[100px]">
                    {t}
                  </span>
                ))}
                {top_priors.length > 2 && (
                  <span className="text-[10px] text-ink-400 self-center">
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
        <div className="text-[11px] text-ink-400 group-hover:text-ink-600 transition-colors shrink-0 flex items-center gap-1">
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
          <Icons.user className="w-3.5 h-3.5 text-ink-700" strokeWidth={2} />
          <h2 className="text-[12px] font-medium text-ink-800 tracking-tight">
            偏好画像
          </h2>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={resetUserMemory}
            className="inline-flex items-center gap-1 text-[11px] text-ink-400 hover:text-rose-600 transition-colors"
            title="清空当前用户的累积偏好（演示完清场用）"
          >
            <Icons.trash className="w-3 h-3" strokeWidth={2} />
            清空记忆
          </button>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="text-ink-400 hover:text-ink-700 transition-colors"
            aria-label="关闭"
          >
            <Icons.close className="w-3.5 h-3.5" strokeWidth={2} />
          </button>
        </div>
      </div>

      {!persona ? (
        <div className="text-ink-400">加载中…</div>
      ) : (
        <>
          {/* persona 档案 */}
          <div className="rounded-md bg-ink-50 border border-ink-200/60 px-3 py-2.5">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-md bg-white border border-ink-200 flex items-center justify-center shrink-0">
                <PersonaIcon className="w-3.5 h-3.5 text-ink-700" strokeWidth={1.75} />
              </div>
              <span className="font-medium text-ink-900 tracking-tight">
                {persona.label}
              </span>
            </div>
            <p className="mt-1.5 text-[11px] text-ink-500 leading-relaxed">
              {persona.notes}
            </p>
          </div>

          {/* 合并后的 top priors */}
          <div>
            <div className="section-title mb-1">高优先（档案 + 历史）</div>
            {top_priors.length === 0 ? (
              <div className="text-ink-400 text-[11px]">（暂无）</div>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {top_priors.map((t) => (
                  <span key={t} className="chip-accent">
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>

          {suggested != null && (
            <div className="text-[11px] text-ink-500">
              建议默认距离：
              <span className="font-medium text-ink-800 mono ml-0.5">
                {suggested} km
              </span>
            </div>
          )}

          <div>
            <div className="section-title mb-1">最近接受 top 5</div>
            {acceptedTop.length === 0 ? (
              <div className="text-ink-400 text-[11px]">
                （还没确认过任何方案）
              </div>
            ) : (
              <ul className="space-y-1">
                {acceptedTop.map(([t, n]) => (
                  <li
                    key={t}
                    className="flex justify-between items-center text-[11px]"
                  >
                    <span className="text-ink-700">{t}</span>
                    <span className="text-ink-400 mono">×{n}</span>
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
                    <span className="text-ink-700">{t}</span>
                    <span className="text-ink-400 mono">×{n}</span>
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
