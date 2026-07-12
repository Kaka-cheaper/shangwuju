"use client";

/**
 * 协作状态条：显示在顶部，展示房间成员 + 规划状态 + 合并展示流。
 * 仅在 collabMode=true 时渲染。
 *
 * 约束流合并 A1（2026-07-12）：此前有三块重复展示——本组件顶栏那行摘要 +
 * 独立的 `ConstraintFeed.tsx`（web/移动端都挂载）+ 移动端 `MobilePreferencesCard`
 * 的房间态「本次调整」台账卡。三处收口成一处：顶栏那行摘要点击展开成
 * 下拉，列全部「约束流 + 台账」合并展示行（`lib/collab-feed.ts::
 * mergeCollabFeed`，每行带归名 + 应用状态）。`ConstraintFeed.tsx` 与
 * `MobilePreferencesCard` 的房间态渲染已删除（demandLedger 数据管线本身
 * 不动，只删这两处纯展示）。
 *
 * 覆盖层交互——Portal + 点击外部关闭，移动端"…"菜单例外：
 * 直接复用 `UserSwitcher.tsx` 已验证过的模式（`wrapRef`/`panelRef` 双 ref
 * 判定"点击是否在外部"，Portal 挂到 document.body 摆脱 `<main
 * className="relative-content">` 的 z-1 stacking context 陷阱——见
 * `UserSwitcher.tsx` 文件头注释对这个陷阱的详细说明，`CollabBar` 与
 * `UserSwitcher` 都是这个陷阱的受害者，同一个病、同一个药）。
 *
 * 但有一处刻意不同于 `UserSwitcher.tsx`：那边的点击外部关闭用整个视口的
 * `fixed inset-0` 透明按钮当"外部点击"捕获层，会连带盖住移动端顶栏"…"
 * 菜单按钮所在的屏幕区域（z-index 更高的捕获层永远先吃到点击，"…"
 * 按钮再也点不到——这正是任务要求"惟独'…'菜单永远可点"要避免的坑，读
 * `UserSwitcher.tsx` 实际渲染的捕获层代码可以直接验证这个风险是真的，不
 * 是假设）。本组件的捕获层改为从**触发行自身的 `getBoundingClientRect().
 * top`** 往下开始（而不是 `inset-0` 从最顶部开始）——`CollabBar` 在两处
 * 挂载点（`HomeView.tsx`/`MobileHomeView.tsx`）都渲染在各自 fixed 顶栏
 * 下方，触发行自身位置天然已经在顶栏区域以下，捕获层从这个位置往下延伸
 * 到视口底部，几何上永远不会覆盖顶栏（包括"…"按钮），不需要额外硬编码
 * 某个页面的顶栏高度数字，也不需要专门为"…"按钮写例外逻辑——两个页面
 * 的顶栏高度不同（移动端 h-16=64px，桌面端 h-14=56px），用触发行自身的
 * 运行时坐标而不是写死的断点高度，两边天然都对。
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown } from "lucide-react";

import { mergeCollabFeed } from "@/lib/collab-feed";
import { useCollabStore } from "@/lib/collab-store";
import { useChatStore } from "@/lib/store";
import { Icons } from "@/lib/icon-map";
import { cn } from "@/lib/utils";

const PANEL_MARGIN = 12;
const PANEL_OFFSET_Y = 6;

export default function CollabBar() {
  const collabMode = useCollabStore((s) => s.collabMode);
  const members = useCollabStore((s) => s.members);
  const connected = useCollabStore((s) => s.connected);
  const planningActive = useCollabStore((s) => s.planningActive);
  const planningTrigger = useCollabStore((s) => s.planningTrigger);
  const constraints = useCollabStore((s) => s.constraints);
  const connectionError = useCollabStore((s) => s.connectionError);
  const demandLedger = useChatStore((s) => s.demandLedger);
  const itinerary = useChatStore((s) => s.itinerary);

  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [panelPos, setPanelPos] = useState<{
    top: number;
    left: number;
    width: number;
    maxHeight: number;
    catcherTop: number;
  } | null>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  const updatePosition = () => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const width = Math.min(420, window.innerWidth - PANEL_MARGIN * 2);
    const left = Math.max(
      PANEL_MARGIN,
      Math.min(rect.left, window.innerWidth - width - PANEL_MARGIN),
    );
    const top = rect.bottom + PANEL_OFFSET_Y;
    setPanelPos({
      top,
      left,
      width,
      maxHeight: Math.max(160, window.innerHeight - top - 12),
      // 捕获层从触发行自身的顶边开始（不是 0）——几何上永远盖不住顶栏，
      // 见本文件头部 docstring"覆盖层交互"节。
      catcherTop: rect.top,
    });
  };

  useLayoutEffect(() => {
    if (!open) return;
    updatePosition();
    const onResize = () => updatePosition();
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onResize, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      const target = e.target as Node;
      const inWrap = wrapRef.current?.contains(target);
      const inPanel = panelRef.current?.contains(target);
      if (!inWrap && !inPanel) setOpen(false);
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onEsc);
    };
  }, []);

  if (!collabMode) return null;

  const lastConstraint = constraints[constraints.length - 1];
  const onlineCount = members.filter((m) => m.online).length;
  const feedRows = mergeCollabFeed(constraints, demandLedger, itinerary);

  return (
    <div ref={wrapRef} className="relative w-full">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={open ? "收起协作动态" : "展开协作动态"}
        className={cn(
          "w-full px-4 py-2 border-b border-black/[0.08]",
          "bg-black/[0.02] hover:bg-black/[0.035] transition-colors",
          "flex items-center justify-between gap-3 text-sm text-left",
        )}
      >
        {/* 左侧：成员头像 */}
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs text-ink-500">协作中</span>
          <div className="flex -space-x-1">
            {members.map((m) => (
              <div
                key={m.user_id}
                className={cn(
                  "w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium border-2 border-white",
                  m.online ? "bg-emerald-500/80 text-white" : "bg-ink-200 text-ink-400",
                )}
                title={`${m.nickname}（${m.role === "owner" ? "发起人" : "参与者"}）${m.online ? "" : " · 离线"}`}
              >
                {m.nickname[0]}
              </div>
            ))}
          </div>
          <span
            className="min-w-0 truncate text-xs text-ink-400"
            title="实时统计：房间内在线成员数 · 已收集但还没合并到下次重规划的约束条数。点击展开查看完整协作动态（约束流+台账合并）"
          >
            房间内 {onlineCount} 人
            {constraints.length > 0 && (
              <span className="ml-1 text-ink-500">
                · {constraints.length} 个约束待合并
              </span>
            )}
          </span>
          <ChevronDown
            className={cn("h-3.5 w-3.5 shrink-0 text-ink-400 transition-transform duration-200", open && "rotate-180")}
            strokeWidth={2.5}
          />
        </div>

        {/* 中间：状态。min-w-0 + block truncate——窄屏(移动端房间)+ 右侧长连接错误
            文案挤压时，这段必须省略号截断，绝不能逐字竖排（真机 bug：断连时
            "等待同行人提出偏好…"整段竖成一列）。 */}
        <div className="flex-1 min-w-0 text-center">
          {planningActive ? (
            <span className="block truncate text-amber-400 text-xs animate-pulse">
              {planningTrigger === "constraint_added"
                ? `正在根据新约束重新规划…`
                : planningTrigger === "vote_dislike"
                  ? "正在根据投票反馈重新规划…"
                  : "规划中…"}
            </span>
          ) : lastConstraint ? (
            <span className="block truncate text-ink-400 text-xs">
              最新约束：{lastConstraint.nickname || lastConstraint.user_id}说「{lastConstraint.text}」
            </span>
          ) : (
            <span className="block truncate text-ink-500 text-xs">
              等待同行人提出偏好…
            </span>
          )}
        </div>

        {/* 右侧：连接状态。shrink-0 保住圆点；错误文案限宽截断，不吞掉中段空间。 */}
        <div className="flex items-center gap-2 shrink-0 min-w-0">
          {connectionError && (
            <span className="truncate max-w-[7rem] text-red-400 text-xs">{connectionError}</span>
          )}
          <div
            className={cn(
              "w-2 h-2 rounded-full",
              connected ? "bg-emerald-400" : "bg-red-400",
            )}
            title={connected ? "已连接" : "未连接"}
          />
        </div>
      </button>

      {(open && mounted && panelPos
        ? createPortal(
            <>
              {/* 点击外部关闭捕获层——`top: panelPos.catcherTop`（触发行
                  自身顶边的运行时坐标）而不是 `inset-0`：这个 `top` 偏移
                  本身就是几何保证——捕获层从这个 y 坐标往下才开始渲染，
                  物理上不可能延伸进顶栏（顶栏在这个坐标以上）的屏幕区域，
                  与 z-index 无关（z-index 只决定同一屏幕区域内谁盖谁，
                  不影响元素渲染的位置范围）。z-45/46 的数值选取只需要盖过
                  普通页面内容与 ToastStack，同时低于 `UserSwitcher.tsx`
                  的 `z-[60]`（若两个下拉极端情况下同时打开，不互相打架）。 */}
              <button
                type="button"
                className="fixed inset-x-0 bottom-0 cursor-default bg-black/[0.06] backdrop-blur-[1px]"
                style={{ top: panelPos.catcherTop, zIndex: 45 }}
                aria-label="收起协作动态"
                onClick={() => setOpen(false)}
              />
              <div
                ref={panelRef}
                className="fixed overflow-hidden rounded-[22px] border border-black/[0.08] bg-white/[0.97] shadow-[0_26px_70px_-36px_rgba(17,24,39,0.82)] backdrop-blur-2xl flex flex-col animate-fade-in"
                style={{
                  top: panelPos.top,
                  left: panelPos.left,
                  width: panelPos.width,
                  maxHeight: panelPos.maxHeight,
                  zIndex: 46,
                }}
              >
                <div className="border-b border-black/[0.06] px-4 py-3 shrink-0">
                  <div className="text-sm font-semibold tracking-tight text-ink-900">
                    协作动态
                  </div>
                  <div className="mt-0.5 text-xs leading-relaxed text-ink-500">
                    谁提了什么、满足没满足，都在这
                  </div>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
                  {feedRows.length === 0 ? (
                    <div className="py-6 text-center text-xs text-ink-400">
                      还没有协作动态，等同行人说点什么
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {feedRows.map((row) => (
                        <div key={row.key} className="flex items-start gap-2 text-xs">
                          {row.status && (
                            <span
                              className={cn(
                                "shrink-0 inline-flex items-center gap-0.5 rounded border px-1.5 py-0 text-[11px] font-medium leading-[1.4]",
                                row.status === "satisfied"
                                  ? "border-ink-300 bg-ink-200 text-ink-500"
                                  : "border-amber-400/30 bg-amber-400/15 text-amber-700",
                              )}
                            >
                              {row.status === "satisfied" && (
                                <Icons.success className="w-2.5 h-2.5 shrink-0" strokeWidth={2.5} />
                              )}
                              {row.status === "satisfied" ? "已满足" : "生效中"}
                            </span>
                          )}
                          <span className="text-ink-600 break-all min-w-0">
                            {row.attribution && (
                              <span className="font-medium text-ink-700">{row.attribution}：</span>
                            )}
                            {row.text}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </>,
            document.body,
          )
        : null) as React.ReactNode}
    </div>
  );
}
