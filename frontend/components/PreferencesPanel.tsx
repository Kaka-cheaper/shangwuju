"use client";

/**
 * PreferencesPanel —— 用户偏好面板（Phase 0.7）。
 *
 * 显示当前 user 的：
 * - persona 档案（label / notes）
 * - 历史接受 top tag（memory.accepted_tags）
 * - 历史拒绝 top tag（memory.rejected_tags）
 * - 合并后的 top_priors
 * - 建议默认距离
 *
 * 评分价值：评委看到「Agent 真的在学」——多次 confirm 后 top tag 会变。
 */

import { useEffect, useState } from "react";

import { useChatStore } from "@/lib/store";

export default function PreferencesPanel() {
  const currentUserId = useChatStore((s) => s.currentUserId);
  const preferences = useChatStore((s) => s.preferences);
  const refreshPreferences = useChatStore((s) => s.refreshPreferences);
  const resetUserMemory = useChatStore((s) => s.resetUserMemory);

  const [open, setOpen] = useState(false);

  // 切 user 后自动刷一次（mount 时也拉，确保折叠态预览有数据可显示）
  useEffect(() => {
    if (currentUserId) refreshPreferences();
  }, [currentUserId, refreshPreferences]);

  // 兜底：展开时如果还没数据再拉一次
  useEffect(() => {
    if (open && !preferences) {
      refreshPreferences();
    }
  }, [open, preferences, refreshPreferences]);

  if (!open) {
    // 折叠态预览卡：显示 persona + 已学 top 2 + 接受次数
    // 设计目标：评委一眼看到「Agent 知道我是谁、学了多少」
    const persona = preferences?.persona;
    const top_priors = preferences?.top_priors ?? [];
    const acceptedCount = preferences?.memory
      ? Object.values(preferences.memory.accepted_tags.counts).reduce(
          (a, b) => a + b,
          0,
        )
      : 0;
    const previewTags = top_priors.slice(0, 2);

    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="查看当前用户的偏好画像与历史记忆"
        className={[
          "w-full group flex items-center justify-between gap-3",
          "rounded-xl border border-brand-200 bg-gradient-to-r from-brand-50 to-amber-50",
          "px-3.5 py-2.5 text-left",
          "hover:border-brand-400 hover:shadow-sm transition-all duration-200",
          "active:scale-[0.99]",
        ].join(" ")}
      >
        <div className="flex items-center gap-2.5 min-w-0 flex-1">
          <div className="text-xl shrink-0">{persona?.icon ?? "📚"}</div>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-semibold text-ink-800 truncate">
                {persona?.label ?? "偏好画像"}
              </span>
              {acceptedCount > 0 && (
                <span className="text-[10px] text-emerald-700 bg-emerald-100 px-1.5 py-0.5 rounded-full font-medium shrink-0">
                  已学 {acceptedCount} 次
                </span>
              )}
            </div>
            {previewTags.length > 0 ? (
              <div className="mt-1 flex flex-wrap gap-1 overflow-hidden">
                {previewTags.map((t) => (
                  <span
                    key={t}
                    className="inline-block rounded-full bg-white/80 border border-brand-200 px-1.5 py-0.5 text-[10px] text-brand-700 truncate max-w-[100px]"
                  >
                    {t}
                  </span>
                ))}
                <span className="text-[10px] text-ink-400 self-center">
                  · 共 {top_priors.length} 项
                </span>
              </div>
            ) : (
              <div className="mt-0.5 text-[11px] text-ink-500">
                {persona?.notes ?? "点击查看 Agent 已学到的偏好"}
              </div>
            )}
          </div>
        </div>
        <div className="text-xs text-ink-400 group-hover:text-brand-600 transition shrink-0 flex items-center gap-1">
          <span>展开</span>
          <svg
            className="w-3 h-3 transition-transform group-hover:translate-y-0.5"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M3 4.5L6 7.5L9 4.5" strokeLinecap="round" strokeLinejoin="round" />
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

  return (
    <div className="card p-4 space-y-3 text-xs">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-800">📚 偏好画像</h2>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={resetUserMemory}
            className="text-[11px] text-ink-400 hover:text-brand-600 transition"
            title="清空当前用户的累积偏好（演示完清场用）"
          >
            清空记忆
          </button>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="text-ink-400 hover:text-ink-700 transition"
          >
            ✕
          </button>
        </div>
      </div>

      {!persona ? (
        <div className="text-ink-400">加载中…</div>
      ) : (
        <>
          {/* persona 档案 */}
          <div className="rounded-md bg-ink-50 px-3 py-2">
            <div className="flex items-center gap-2">
              <span className="text-base">{persona.icon}</span>
              <span className="font-medium text-ink-800">{persona.label}</span>
            </div>
            <p className="mt-1 text-[11px] text-ink-500 leading-relaxed">{persona.notes}</p>
          </div>

          {/* 合并后的 top priors */}
          <div>
            <div className="text-[11px] text-ink-400 mb-1">高优先（档案 + 历史）</div>
            {top_priors.length === 0 ? (
              <div className="text-ink-400 text-[11px]">（暂无）</div>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {top_priors.map((t) => (
                  <span
                    key={t}
                    className="rounded-full bg-brand-50 text-brand-700 px-2 py-0.5 text-[11px]"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* 建议距离 */}
          {suggested != null && (
            <div className="text-[11px] text-ink-500">
              建议默认距离：
              <span className="font-medium text-ink-800">{suggested} km</span>
            </div>
          )}

          {/* 接受历史 */}
          <div>
            <div className="text-[11px] text-ink-400 mb-1">最近接受 top 5</div>
            {acceptedTop.length === 0 ? (
              <div className="text-ink-400 text-[11px]">（还没确认过任何方案）</div>
            ) : (
              <ul className="space-y-1">
                {acceptedTop.map(([t, n]) => (
                  <li key={t} className="flex justify-between text-[11px]">
                    <span className="text-ink-700">{t}</span>
                    <span className="text-ink-400">×{n}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* 拒绝历史 */}
          {rejectedTop.length > 0 && (
            <div>
              <div className="text-[11px] text-ink-400 mb-1">最近拒绝 top 5</div>
              <ul className="space-y-1">
                {rejectedTop.map(([t, n]) => (
                  <li key={t} className="flex justify-between text-[11px]">
                    <span className="text-ink-700">{t}</span>
                    <span className="text-ink-400">×{n}</span>
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
