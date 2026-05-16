"use client";

/**
 * UserSwitcher —— 顶栏 user 切换器（Phase 0.7）。
 *
 * 设计：
 * - 5 个 mock persona + "demo_user"（默认）
 * - 当前选中 user 显示头像 + label
 * - 点击展开下拉，选中后写 cookie + 刷 preferences
 * - reset 不会清 user（演示连续切 user 体验稳）
 *
 * 与后端契约：
 * - 切换后所有 SSE 请求带 X-User-Id header
 * - main.py 用此 header 选 persona / 累积 memory
 */

import { useEffect, useRef, useState } from "react";

import { useChatStore } from "@/lib/store";

export default function UserSwitcher() {
  const personas = useChatStore((s) => s.personas);
  const personasLoaded = useChatStore((s) => s.personasLoaded);
  const currentUserId = useChatStore((s) => s.currentUserId);
  const setCurrentUserId = useChatStore((s) => s.setCurrentUserId);
  const loadPersonas = useChatStore((s) => s.loadPersonas);

  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!personasLoaded) loadPersonas();
  }, [personasLoaded, loadPersonas]);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const current = personas.find((p) => p.user_id === currentUserId);
  const display = current
    ? `${current.icon} ${current.label}`
    : currentUserId === "demo_user"
      ? "👤 默认"
      : `👤 ${currentUserId ?? "未设置"}`;

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        className="inline-flex items-center gap-1.5 rounded-full border border-ink-200 bg-white px-3 py-1.5 text-xs text-ink-700 hover:bg-ink-50 transition"
        onClick={() => setOpen((v) => !v)}
        title="切换演示用户"
      >
        <span>{display}</span>
        <span className={`text-[10px] text-ink-400 transition ${open ? "rotate-180" : ""}`}>
          ▾
        </span>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-56 rounded-lg border border-ink-200 bg-white shadow-lg z-20">
          <div className="px-3 py-2 text-[11px] text-ink-400 border-b border-ink-100">
            选择演示用户档案
          </div>
          <ul className="py-1 max-h-72 overflow-auto">
            {personas.map((p) => {
              const active = p.user_id === currentUserId;
              return (
                <li key={p.user_id}>
                  <button
                    type="button"
                    className={`w-full px-3 py-2 text-left text-xs flex items-start gap-2 hover:bg-ink-50 transition ${
                      active ? "bg-brand-50" : ""
                    }`}
                    onClick={() => {
                      setCurrentUserId(p.user_id);
                      setOpen(false);
                    }}
                  >
                    <span className="text-base leading-none mt-0.5">{p.icon}</span>
                    <span className="flex-1 min-w-0">
                      <span
                        className={`block font-medium ${active ? "text-brand-700" : "text-ink-800"}`}
                      >
                        {p.label}
                      </span>
                      <span className="block text-[11px] text-ink-500 line-clamp-2">
                        {p.notes}
                      </span>
                    </span>
                    {active && <span className="text-brand-600 text-xs">✓</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
