"use client";

/**
 * CommandPalette —— Cmd+K 命令面板（D IDE 范式）。
 *
 * 视觉灵感：Vercel / Linear / GitHub Cmd+K 模板。
 *
 * 提供：
 *   - 8 个演示场景（搜索 / 上下方向键导航 / Enter 提交）
 *   - 切换 Planner Mode（rule ↔ llm）
 *   - 切换 User（personas 列表）
 *   - 取消方案 / 重置会话
 *
 * 全部入口集中在一处，主屏视觉负担降低，符合「不要做太显眼，但要可点」的纪律。
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { Icons, personaIconFromEmoji, scenarioIcon } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import type { LucideIcon } from "lucide-react";
import { Compass, MessageSquarePlus, RefreshCw, ToggleRight, Users2, X } from "lucide-react";

import { cn, loadSessions, sessionLabelFromId, type SessionRecord } from "@/lib/utils";

interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon: LucideIcon;
  keywords: string;
  perform: () => void;
}

export default function CommandPalette() {
  const open = useChatStore((s) => s.commandPaletteOpen);
  const close = useChatStore((s) => s.closeCommandPalette);
  const scenarios = useChatStore((s) => s.scenarios);
  const personas = useChatStore((s) => s.personas);
  const plannerMode = useChatStore((s) => s.plannerMode);
  const currentUserId = useChatStore((s) => s.currentUserId);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const setPlannerMode = useChatStore((s) => s.setPlannerMode);
  const setCurrentUserId = useChatStore((s) => s.setCurrentUserId);
  const cancel = useChatStore((s) => s.cancel);
  const reset = useChatStore((s) => s.reset);
  const startNewSession = useChatStore((s) => s.startNewSession);
  const switchSession = useChatStore((s) => s.switchSession);
  const currentSessionId = useChatStore((s) => s.sessionId);
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);

  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [recentSessions, setRecentSessions] = useState<SessionRecord[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  // 打开命令面板时刷一次 session 列表（localStorage）
  useEffect(() => {
    if (open) setRecentSessions(loadSessions());
  }, [open]);

  const allCommands: Command[] = useMemo(() => {
    const cmds: Command[] = [];

    // 场景
    for (const s of scenarios) {
      cmds.push({
        id: `scenario-${s.id}`,
        label: `${s.id} · ${s.title}`,
        hint: s.input,
        group: "演示场景",
        icon: scenarioIcon(s.id),
        keywords: `${s.id} ${s.title} ${s.input}`,
        perform: () => {
          if (streaming) return;
          sendMessage(s.input, s.id);
          close();
        },
      });
    }

    // 切 user
    for (const p of personas) {
      cmds.push({
        id: `user-${p.user_id}`,
        label: `切到「${p.label}」`,
        hint: p.notes,
        group: "切换用户",
        icon: personaIconFromEmoji(p.icon),
        keywords: `user persona ${p.user_id} ${p.label} ${p.notes}`,
        perform: () => {
          setCurrentUserId(p.user_id);
          close();
        },
      });
    }

    // 切 planner
    cmds.push(
      {
        id: "mode-rule",
        label: "切到规则模式（Demo 安全网）",
        hint: "Tool 调用顺序固定，结果稳定",
        group: "Planner 模式",
        icon: ToggleRight,
        keywords: "planner mode rule 规则 default",
        perform: () => {
          if (plannerMode !== "rule") setPlannerMode("rule");
          close();
        },
      },
      {
        id: "mode-llm",
        label: "切到 LLM 自主决策模式",
        hint: "评分加分 · 失败自动 fallback",
        group: "Planner 模式",
        icon: ToggleRight,
        keywords: "planner mode llm 自主 function calling",
        perform: () => {
          if (plannerMode !== "llm") setPlannerMode("llm");
          close();
        },
      },
    );

    // 会话操作
    if (itinerary) {
      cmds.push({
        id: "cancel-plan",
        label: "取消当前方案",
        hint: "不再展示行程，可重新输入",
        group: "会话操作",
        icon: X,
        keywords: "cancel 取消 abort discard",
        perform: () => {
          cancel();
          close();
        },
      });
    }
    cmds.push({
      id: "reset-session",
      label: "重置会话",
      hint: "清空当前对话与行程，新建 session_id",
      group: "会话操作",
      icon: RefreshCw,
      keywords: "reset 重置 clear 清空 new",
      perform: () => {
        reset();
        close();
      },
    });

    // 会话切换 + 新建
    cmds.push({
      id: "session-new",
      label: "+ 开新会话",
      hint: "保留之前会话历史，新开一个独立上下文",
      group: "会话切换",
      icon: MessageSquarePlus,
      keywords: "new session 新会话 开新 chat thread",
      perform: () => {
        startNewSession();
        close();
      },
    });
    for (const s of recentSessions) {
      if (s.id === currentSessionId) continue; // 当前会话不放在切换列表
      cmds.push({
        id: `session-${s.id}`,
        label: s.label || sessionLabelFromId(s.id),
        hint: s.lastSummary || `创建于 ${new Date(s.createdAt).toLocaleString()}`,
        group: "会话切换",
        icon: Compass,
        keywords: `session 会话 切换 ${s.label} ${s.id} ${s.lastSummary ?? ""}`,
        perform: () => {
          switchSession(s.id);
          close();
        },
      });
    }

    return cmds;
  }, [
    scenarios,
    personas,
    plannerMode,
    streaming,
    itinerary,
    sendMessage,
    setCurrentUserId,
    setPlannerMode,
    cancel,
    reset,
    close,
    recentSessions,
    currentSessionId,
    startNewSession,
    switchSession,
  ]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allCommands;
    return allCommands.filter((c) =>
      c.keywords.toLowerCase().includes(q) ||
      c.label.toLowerCase().includes(q),
    );
  }, [allCommands, query]);

  // 按 group 排序但保持原顺序
  const grouped = useMemo(() => {
    const order: string[] = [];
    const map = new Map<string, Command[]>();
    for (const c of filtered) {
      if (!map.has(c.group)) {
        order.push(c.group);
        map.set(c.group, []);
      }
      map.get(c.group)!.push(c);
    }
    return order.map((g) => ({ group: g, items: map.get(g)! }));
  }, [filtered]);

  // 把分组数组扁平化用于 active 索引
  const flatItems = useMemo(
    () => grouped.flatMap((g) => g.items),
    [grouped],
  );

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    const t = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(t);
  }, [open]);

  // active 边界保护
  useEffect(() => {
    if (active >= flatItems.length) setActive(0);
  }, [flatItems.length, active]);

  // 键盘
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => (i + 1) % Math.max(1, flatItems.length));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(
          (i) => (i - 1 + flatItems.length) % Math.max(1, flatItems.length),
        );
      } else if (e.key === "Enter") {
        e.preventDefault();
        flatItems[active]?.perform();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, flatItems, active, close]);

  if (!open) return null;

  let runningIdx = -1;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[8vh] sm:pt-[16vh] animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-label="命令面板"
    >
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-md"
        onClick={close}
        aria-hidden
      />

      <div
        className="relative w-full max-w-xl border border-black/[0.08] rounded-lg overflow-hidden animate-fade-in-up backdrop-blur-xl"
        style={{
          background: "rgba(255, 255, 255, 0.95)",
          boxShadow:
            "0 0 0 1px rgba(0,0,0,0.04), 0 24px 48px -12px rgba(0,0,0,0.25)",
        }}
      >
        {/* 搜索框 */}
        <div className="flex items-center gap-2 px-3.5 py-3 border-b border-black/[0.06]">
          <Compass className="w-4 h-4 text-brand-600" strokeWidth={2} />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            placeholder="搜索场景 / 切换用户 / 切换模式..."
            className="flex-1 bg-transparent border-0 outline-none text-sm text-ink-900 placeholder:text-ink-500 tracking-tight"
          />
          <span className="kbd">ESC</span>
        </div>

        {/* 列表 */}
        <div className="max-h-[420px] overflow-y-auto py-1">
          {flatItems.length === 0 && (
            <div className="px-4 py-10 text-center text-sm text-ink-500">
              没有匹配结果
            </div>
          )}
          {grouped.map((g) => (
            <div key={g.group} className="py-1">
              <div className="px-3.5 py-1 section-title">{g.group}</div>
              <ul>
                {g.items.map((cmd) => {
                  runningIdx += 1;
                  const isActive = runningIdx === active;
                  const Icon = cmd.icon;
                  const idx = runningIdx;
                  return (
                    <li key={cmd.id}>
                      <button
                        type="button"
                        onMouseEnter={() => setActive(idx)}
                        onClick={() => cmd.perform()}
                        className={cn(
                          "w-full flex items-center gap-2.5 px-3.5 py-2 text-left transition-colors",
                          isActive
                            ? "bg-black/[0.05]"
                            : "hover:bg-black/[0.03]",
                        )}
                      >
                        <Icon
                          className={cn(
                            "w-3.5 h-3.5 shrink-0",
                            isActive ? "text-brand-600" : "text-ink-500",
                          )}
                          strokeWidth={2}
                        />
                        <div className="flex-1 min-w-0">
                          <div
                            className={cn(
                              "text-[13px] tracking-tight truncate",
                              isActive
                                ? "text-ink-900 font-medium"
                                : "text-ink-800",
                            )}
                          >
                            {cmd.label}
                          </div>
                          {cmd.hint && (
                            <div className="text-[11px] text-ink-500 truncate">
                              {cmd.hint}
                            </div>
                          )}
                        </div>
                        {isActive && (
                          <span className="text-[10px] text-brand-600 mono">
                            ↵
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>

        {/* 底部 hint */}
        <div className="px-3.5 py-2 border-t border-black/[0.06] bg-black/[0.02] flex items-center justify-between text-[11px] text-ink-500">
          <div className="flex items-center gap-1.5">
            <span className="kbd">↑</span>
            <span className="kbd">↓</span>
            <span className="ml-1">导航</span>
            <span className="mx-2">·</span>
            <span className="kbd">↵</span>
            <span className="ml-1">执行</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Users2 className="w-3 h-3" strokeWidth={2} />
            <span>{flatItems.length} 项</span>
          </div>
        </div>
      </div>
    </div>
  );
}
