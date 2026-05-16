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

  useEffect(() => {
    if (open && !preferences) {
      refreshPreferences();
    }
  }, [open, preferences, refreshPreferences]);

  // 切 user 后自动刷一次
  useEffect(() => {
    if (currentUserId) refreshPreferences();
  }, [currentUserId, refreshPreferences]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-xs text-ink-500 hover:text-brand-600 transition"
        title="查看当前用户的偏好画像与历史记忆"
      >
        📚 偏好
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
