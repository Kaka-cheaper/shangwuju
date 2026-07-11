"use client";

/**
 * UserSwitcher —— 顶栏用户切换器（黄昏深色主题：玻璃描边）。
 * 配色：用户档案语义采用 caramel 焦糖琥珀色（替代 AI 莓紫，去 AI 味）。
 *
 * z-index 设计（problem.md 问题 23 第二轮修复）：
 *   下拉面板用 React Portal 渲染到 document.body + position: fixed + z-[60]。
 *
 *   为什么必须 Portal：第一轮只用 fixed + z-45 不够，因为：
 *   - <main className="relative-content"> 是 z-1 stacking context（globals.css §relative-content）
 *   - <header className="relative-content sticky z-20"> 也是 z-20 stacking context
 *   - UserSwitcher 是 header 的 DOM 子节点，即使面板用 fixed 仍受困于 header 子树
 *   - main 内部的 ItineraryCard / ToolTracePanel / QuickScenarios 都在 z-1 上下文里
 *     当它们与 header 内 z-45 的元素（fixed 但仍是 header 子树）比较时，
 *     header z-20 vs main z-1 的对比下，main 内部任何元素都可能盖住 header 子树
 *
 *   Portal 把面板物理脱离整个组件树 → 直接挂到 body → z-60 真正全局生效。
 *
 *   层级表（含本次更新）：
 *     z-60 UserSwitcher 下拉（Portal） + Confetti（fixed inset z-60）
 *     z-40 ToastStack
 *     z-30 ChatDock
 *     z-20 Header
 *     z-1  main / header relative-content
 *     z-0  aurora-bg
 *
 *   按钮位置通过 buttonRef.getBoundingClientRect() 计算，每次开打/视口变化重新对齐。
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useCollabStore } from "@/lib/collab-store";
import { Icons, personaIconFromEmoji } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { buildAppPath, cn } from "@/lib/utils";

const PANEL_WIDTH = 360;
const PANEL_OFFSET_Y = 6; // mt-1.5 ≈ 6px
const PANEL_MARGIN = 12;
const ONBOARDING_SESSION_KEY = "shangwuju.persona.onboarded";

type WizardStep = "choose" | "refine" | "confirm" | "detail";
type PanelPresentation = "dropdown" | "modal";

interface MatchOption {
  userId: string;
  title: string;
  subtitle: string;
  traits: string[];
  summary: string;
  outingTitle: string;
  outingSubtitle: string;
}

const MATCH_OPTIONS: MatchOption[] = [
  {
    userId: "u_dad",
    title: "带孩子轻松玩",
    subtitle: "近一点、少折腾、孩子能玩",
    traits: ["孩子能玩久一点", "室内备份", "少折腾", "亲子餐友好"],
    summary: "亲子友好、低强度、餐食稳妥",
    outingTitle: "带孩子一起",
    outingSubtitle: "想轻松一点，孩子也能玩得住",
  },
  {
    userId: "u_biz",
    title: "接待客户同事",
    subtitle: "体面、方便、核心商圈",
    traits: ["体面一点", "交通方便", "核心商圈", "有包间"],
    summary: "商务体面、交通便利、预算更宽",
    outingTitle: "客户或同事",
    outingSubtitle: "希望安排得体面、顺路、好沟通",
  },
  {
    userId: "u_grandma",
    title: "陪父母长辈",
    subtitle: "少走路、可休息、餐食稳",
    traits: ["少走路", "无台阶", "软烂菜", "可休息"],
    summary: "近距离、低强度、照顾长辈",
    outingTitle: "陪父母长辈",
    outingSubtitle: "少走一点，吃得稳，也方便休息",
  },
  {
    userId: "u_solo",
    title: "一个人放空",
    subtitle: "安静、室内、单人友好",
    traits: ["安静一点", "室内放空", "单人友好", "不赶时间"],
    summary: "独处舒缓、安静聊天、节奏松",
    outingTitle: "自己出去",
    outingSubtitle: "想一个人放空，节奏不用太赶",
  },
  {
    userId: "u_couple",
    title: "和对象约会",
    subtitle: "聊天、拍照、有氛围",
    traits: ["适合聊天", "看展", "适合拍照", "有氛围"],
    summary: "亲密情侣、看展拍照、安静聊天",
    outingTitle: "和伴侣一起",
    outingSubtitle: "适合聊天、拍照，氛围要舒服",
  },
];

const PERSONA_AVATARS: Record<string, string> = {
  u_dad: buildAppPath("/avatars/xinshoubaba.png"),
  u_biz: buildAppPath("/avatars/shangwubailing.png"),
  u_grandma: buildAppPath("/avatars/xiaoshunernv.png"),
  u_solo: buildAppPath("/avatars/dujuqingnian.png"),
  u_couple: buildAppPath("/avatars/qinglvdang.png"),
};

function personaAvatarSrc(userId: string | null | undefined): string | null {
  return userId ? (PERSONA_AVATARS[userId] ?? null) : null;
}

export default function UserSwitcher({
  autoOpenOnMount = false,
}: {
  autoOpenOnMount?: boolean;
}) {
  const personas = useChatStore((s) => s.personas);
  const personasLoaded = useChatStore((s) => s.personasLoaded);
  const currentUserId = useChatStore((s) => s.currentUserId);
  const setCurrentUserId = useChatStore((s) => s.setCurrentUserId);
  const loadPersonas = useChatStore((s) => s.loadPersonas);
  const preferences = useChatStore((s) => s.preferences);
  const resetUserMemory = useChatStore((s) => s.resetUserMemory);
  // 闭环审计 P1（用户偏好面板全环方案任务书裁决）：房间内切画像是 no-op
  // 假控件——`setCurrentUserId` 改的是 `useChatStore.currentUserId`，房间
  // 成员身份是 `collab-store.myUserId`，两者不同步（§8 坐实的既有错位），
  // 房间里点"切画像"不会真的换成员身份，还会误导性地刷新偏好面板显示
  // 别人（或模板 demo_user）的画像。房间模式下整个入口隐藏，而不是禁用/
  // 只读展示——禁用态仍会让用户以为"点了会生效只是暂时关闭"，隐藏才诚实。
  const collabMode = useCollabStore((s) => s.collabMode);

  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [step, setStep] = useState<WizardStep>("choose");
  const [presentation, setPresentation] =
    useState<PanelPresentation>("dropdown");
  const [blocking, setBlocking] = useState(false);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [selectedTraits, setSelectedTraits] = useState<string[]>([]);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [panelPos, setPanelPos] = useState<{
    top: number;
    left: number;
    width: number;
    maxHeight: number;
  } | null>(null);

  // SSR 时 document 不存在，等 mount 完才能 createPortal
  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!personasLoaded) loadPersonas();
  }, [personasLoaded, loadPersonas]);

  // 计算面板位置（按钮下方右对齐）+ 可用高度（避免被底部 dock 遮住）
  const updatePosition = () => {
    if (!buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    const width = Math.min(PANEL_WIDTH, window.innerWidth - PANEL_MARGIN * 2);
    const maxLeft = window.innerWidth - width - PANEL_MARGIN;
    const left = Math.min(
      Math.max(PANEL_MARGIN, rect.right - width),
      Math.max(PANEL_MARGIN, maxLeft),
    );
    const top = rect.bottom + PANEL_OFFSET_Y;
    setPanelPos({
      top,
      left,
      width,
      maxHeight: Math.max(160, window.innerHeight - top - 12),
    });
  };

  useLayoutEffect(() => {
    if (!open || presentation === "modal") return;
    updatePosition();
    const onResize = () => updatePosition();
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onResize, true);
    };
  }, [open, presentation]);

  // 点外部关闭：按钮 + 面板都不算外部
  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (blocking) return;
      const target = e.target as Node;
      const inWrap = wrapRef.current?.contains(target);
      const inPanel = panelRef.current?.contains(target);
      if (!inWrap && !inPanel) setOpen(false);
    }
    function onEsc(e: KeyboardEvent) {
      if (blocking) return;
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onEsc);
    };
  }, [blocking]);

  const current = personas.find((p) => p.user_id === currentUserId);
  const CurrentIcon = current
    ? personaIconFromEmoji(current.icon, current.label)
    : Icons.user;
  const currentOption =
    MATCH_OPTIONS.find((option) => option.userId === currentUserId) ?? null;
  const currentAvatar = personaAvatarSrc(currentUserId);
  const display =
    current?.label ??
    (currentUserId === "demo_user" ? "选择画像" : currentUserId ?? "未设置");
  const selectedOption =
    MATCH_OPTIONS.find((option) => option.userId === selectedUserId) ??
    currentOption ??
    MATCH_OPTIONS[0];
  const selectedAvatar = personaAvatarSrc(selectedOption.userId);
  const selectedPersona = personas.find((p) => p.user_id === selectedOption.userId);
  const SelectedIcon = selectedPersona
    ? personaIconFromEmoji(selectedPersona.icon, selectedPersona.label)
    : Icons.user;
  const selectedLabel = selectedPersona?.label ?? selectedOption.title;
  const isCurrentDetail = step === "detail" && selectedOption.userId === currentUserId;
  const detailPersona = isCurrentDetail ? preferences?.persona : null;
  const detailNotes = detailPersona?.notes ?? selectedOption.summary;
  const detailPriors =
    isCurrentDetail && (preferences?.top_priors ?? []).length > 0
      ? preferences!.top_priors
      : selectedOption.traits;
  const detailMemory = isCurrentDetail ? preferences?.memory : null;
  const acceptedCount = detailMemory
    ? Object.values(detailMemory.accepted_tags.counts).reduce((a, b) => a + b, 0)
    : 0;
  const acceptedTop = detailMemory
    ? Object.entries(detailMemory.accepted_tags.counts).sort((a, b) => b[1] - a[1]).slice(0, 5)
    : [];
  const rejectedTop = detailMemory
    ? Object.entries(detailMemory.rejected_tags.counts)
        .filter(([, n]) => n > 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
    : [];
  const panelTitle =
    step === "detail"
      ? selectedLabel
      : blocking
        ? "先聊聊这次怎么出门"
        : "重新匹配这次出门";
  const panelSubtitle =
    step === "detail"
      ? "这里是当前画像的偏好标签，也可以随时重新选择。"
      : blocking
        ? "先选和谁一起，再挑几个在意的点，我来帮你匹配合适画像。"
        : "这次情况变了的话，重新选一遍就好。";

  const openPanel = (
    nextPresentation: PanelPresentation = "dropdown",
    nextBlocking = false,
    nextStep: WizardStep = "choose",
  ) => {
    setStep(nextStep);
    setPresentation(nextPresentation);
    setBlocking(nextBlocking);
    setSelectedUserId(
      MATCH_OPTIONS.some((option) => option.userId === currentUserId)
        ? currentUserId
        : null,
    );
    setSelectedTraits([]);
    setOpen(true);
  };

  const toggleOpen = () => {
    if (open) {
      if (blocking) return;
      setOpen(false);
    } else {
      openPanel(
        "dropdown",
        false,
        currentOption ? "detail" : "choose",
      );
    }
  };

  useEffect(() => {
    // 房间模式：不强弹"选画像"引导——整个切换器都要隐藏（见上方 collabMode
    // docstring），强弹的阻断式 onboarding 模态在房间里更是无意义的打扰。
    if (!mounted || !autoOpenOnMount || collabMode) return;

    if (currentOption) {
      if (blocking && presentation === "modal" && step === "choose") {
        setBlocking(false);
        setOpen(false);
      }
      return;
    }

    if (open) return;
    openPanel("modal", true, "choose");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpenOnMount, blocking, collabMode, currentOption, mounted, open, presentation, step]);

  const chooseOption = (option: MatchOption) => {
    setSelectedUserId(option.userId);
    setSelectedTraits([]);
    setStep("refine");
  };

  const toggleTrait = (trait: string) => {
    setSelectedTraits((prev) =>
      prev.includes(trait)
        ? prev.filter((item) => item !== trait)
        : [...prev, trait],
    );
  };

  const confirmMatch = () => {
    setCurrentUserId(selectedOption.userId);
    try {
      window.sessionStorage.setItem(ONBOARDING_SESSION_KEY, "true");
    } catch {
      // 忽略隐私模式 / 存储不可用。
    }
    setBlocking(false);
    setOpen(false);
  };

  const restartMatch = () => {
    setSelectedUserId(currentOption?.userId ?? null);
    setSelectedTraits([]);
    setStep("choose");
  };

  // 房间模式：整个切换器隐藏（见顶部 collabMode docstring）——所有 hooks
  // 已在上方无条件跑完，这里只是渲染早退，不违反 Hooks 规则。
  if (collabMode) return null;

  return (
    <div ref={wrapRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        className="inline-flex items-center gap-2 rounded-full border border-black/[0.08] bg-white/[0.68] py-1 pl-3 pr-1 text-sm font-bold text-ink-900 shadow-sm backdrop-blur transition-colors hover:border-accent-400/50 hover:bg-white/[0.86]"
        onClick={toggleOpen}
        title={currentOption ? "查看人物画像" : "选择人物画像"}
      >
        <span className="max-w-[88px] truncate tracking-tight">{display}</span>
        {currentAvatar ? (
          <img
            src={currentAvatar}
            alt={display}
            className="h-8 w-8 rounded-full border border-white/80 object-cover shadow-[0_8px_20px_-14px_rgba(17,24,39,0.75)]"
          />
        ) : (
          <span className="grid h-8 w-8 place-items-center rounded-full border border-caramel-300/45 bg-caramel-100">
            <CurrentIcon className="h-4 w-4 text-caramel-300" strokeWidth={2.15} />
          </span>
        )}
      </button>

      {(open && mounted && (presentation === "modal" || panelPos)
        ? createPortal(
          <>
            {blocking ? (
              <div
                className="fixed inset-0 bg-white/[0.35] backdrop-blur-md"
                style={{ zIndex: 59 }}
                aria-hidden
              />
            ) : (
              <button
                type="button"
                className="fixed inset-0 cursor-default bg-white/[0.18] backdrop-blur-sm"
                style={{ zIndex: 59 }}
                aria-label="关闭档案匹配"
                onClick={() => setOpen(false)}
              />
            )}
            <div
              ref={panelRef}
              className="fixed overflow-hidden rounded-[30px] border border-white/[0.78] shadow-[0_26px_70px_-36px_rgba(17,24,39,0.82)] backdrop-blur-2xl backdrop-saturate-150 animate-fade-in flex flex-col"
              style={
                presentation === "modal"
                  ? {
                      top: "50%",
                      left: "50%",
                      width: "min(420px, calc(100vw - 24px))",
                      maxHeight: "calc(100vh - 48px)",
                      transform: "translate(-50%, -50%)",
                      background: "rgba(255, 255, 255, 0.95)",
                      zIndex: 60,
                    }
                  : {
                      top: `${panelPos!.top}px`,
                      left: `${panelPos!.left}px`,
                      width: `${panelPos!.width}px`,
                      maxHeight: `${panelPos!.maxHeight}px`,
                      background: "rgba(255, 255, 255, 0.95)",
                      zIndex: 60,
                    }
              }
            >
            <div className="border-b border-black/[0.06] px-4 py-3 shrink-0">
              <div className="text-sm font-semibold tracking-tight text-ink-900">
                {panelTitle}
              </div>
              <div className="mt-0.5 text-xs leading-relaxed text-ink-500">
                {panelSubtitle}
              </div>
            </div>
            <div className="flex-1 min-h-0 overflow-auto px-3 py-3">
              {step === "choose" && (
                <div className="space-y-3">
                  <div>
                    <div className="text-xs font-semibold text-ink-500">
                      这次主要是谁一起出门？
                    </div>
                    <div className="mt-2.5 grid gap-2.5">
                      {MATCH_OPTIONS.map((option) => {
                        const active = option.userId === selectedUserId;
                        return (
                          <button
                            key={option.userId}
                            type="button"
                            className={cn(
                              "w-full rounded-full border px-4 py-3 text-left shadow-sm backdrop-blur-xl transition active:scale-[0.99]",
                              active
                                ? "border-accent-500/70 bg-accent-500/[0.16] shadow-[0_14px_34px_-26px_rgba(245,158,11,0.9)]"
                                : "border-white/[0.78] bg-white/[0.74] hover:border-accent-400/50 hover:bg-white/[0.88]",
                            )}
                            onClick={() => chooseOption(option)}
                          >
                            <span className="flex items-center gap-3">
                              <span
                                className={cn(
                                  "grid h-6 w-6 shrink-0 place-items-center rounded-full border transition-colors",
                                  active
                                    ? "border-accent-500 bg-accent-500"
                                    : "border-black/[0.14] bg-white/[0.75]",
                                )}
                              >
                                {active && (
                                  <span className="h-2 w-2 rounded-full bg-ink-900" />
                                )}
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="block text-sm font-semibold tracking-tight text-ink-900">
                                  {option.outingTitle}
                                </span>
                                <span className="mt-0.5 block text-xs leading-relaxed text-ink-500">
                                  {option.outingSubtitle}
                                </span>
                              </span>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}

              {step === "refine" && (
                <div className="space-y-3">
                  <div className="rounded-[28px] border border-accent-500/45 bg-accent-500/[0.10] px-5 py-4 shadow-[0_14px_34px_-28px_rgba(245,158,11,0.9)] backdrop-blur-xl">
                    <div className="text-xs font-semibold text-ink-500">
                      已选择
                    </div>
                    <div className="mt-1 text-sm font-semibold tracking-tight text-ink-900">
                      {selectedOption.outingTitle}
                    </div>
                    <div className="mt-1 text-xs leading-relaxed text-ink-600">
                      {selectedOption.outingSubtitle}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-ink-500">
                      这次希望怎么样？
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {selectedOption.traits.map((trait) => {
                        const active = selectedTraits.includes(trait);
                        return (
                          <button
                            key={trait}
                            type="button"
                            className={cn(
                              "rounded-full border px-4 py-2 text-sm font-semibold shadow-sm backdrop-blur-xl transition active:scale-[0.98]",
                              active
                                ? "border-accent-500/70 bg-accent-500/25 text-ink-900 shadow-[0_12px_26px_-22px_rgba(245,158,11,0.9)]"
                                : "border-white/[0.78] bg-white/[0.76] text-ink-600 hover:border-accent-400/50 hover:bg-white/[0.90]",
                            )}
                            onClick={() => toggleTrait(trait)}
                          >
                            {trait}
                          </button>
                        );
                      })}
                    </div>
                    <p className="mt-2 text-xs leading-relaxed text-ink-500">
                      点几个最在意的点，我会根据它们匹配一个合适的画像。
                    </p>
                  </div>
                  <div className="grid grid-cols-[0.9fr_1.1fr] gap-2.5">
                    <button
                      type="button"
                      className="rounded-full border border-white/[0.78] bg-white/[0.76] px-4 py-3 text-sm font-bold text-ink-600 shadow-sm backdrop-blur-xl transition hover:border-accent-400/50 hover:bg-white/[0.92] hover:text-ink-900 active:scale-[0.99]"
                      onClick={() => setStep("choose")}
                    >
                      换同行对象
                    </button>
                    <button
                      type="button"
                      className="rounded-full border border-[#e6bc00]/45 bg-[#FFD100] px-4 py-3 text-sm font-bold text-ink-900 shadow-[0_14px_34px_-24px_rgba(245,158,11,0.98)] transition active:scale-[0.99]"
                      onClick={() => setStep("confirm")}
                    >
                      生成匹配画像
                    </button>
                  </div>
                </div>
              )}

              {step === "confirm" && (
                <div className="space-y-3">
                  <div className="rounded-[28px] border border-white/[0.78] bg-white/[0.72] px-5 py-5 shadow-sm backdrop-blur-xl">
                    <div className="flex flex-col items-center text-center">
                      {selectedAvatar ? (
                        <img
                          src={selectedAvatar}
                          alt={selectedLabel}
                          className="h-24 w-24 rounded-full border-2 border-white object-cover shadow-[0_18px_38px_-24px_rgba(17,24,39,0.75)]"
                        />
                      ) : (
                        <span className="grid h-24 w-24 place-items-center rounded-full border border-caramel-300/45 bg-caramel-100">
                          <SelectedIcon
                            className="h-8 w-8 text-caramel-300"
                            strokeWidth={2.25}
                          />
                        </span>
                      )}
                      <div className="mt-3 text-sm font-semibold tracking-tight text-ink-900">
                        今天先按「{selectedLabel}」来安排
                      </div>
                    </div>
                    <div className="mt-3 rounded-full border border-white/[0.76] bg-white/[0.76] px-4 py-2 text-center text-sm font-semibold leading-relaxed text-ink-600 shadow-sm">
                      {selectedTraits.length > 0
                        ? selectedTraits.join(" / ")
                        : selectedOption.summary}
                    </div>
                    <div className="mt-2 rounded-full border border-white/[0.72] bg-white/[0.86] px-4 py-2.5 text-center text-xs font-medium leading-relaxed text-ink-500">
                      确认后，我就按这个状态来理解你的后续安排。
                    </div>
                  </div>
                  <div className="grid grid-cols-[0.85fr_1.15fr] gap-2.5">
                    <button
                      type="button"
                      className="rounded-full border border-white/[0.78] bg-white/[0.76] px-4 py-3 text-sm font-bold text-ink-600 shadow-sm backdrop-blur-xl transition hover:border-accent-400/50 hover:bg-white/[0.92] hover:text-ink-900 active:scale-[0.99]"
                      onClick={() => setStep("refine")}
                    >
                      返回调整
                    </button>
                    <button
                      type="button"
                      className="rounded-full border border-[#e6bc00]/45 bg-[#FFD100] px-4 py-3 text-sm font-bold text-ink-900 shadow-[0_14px_34px_-24px_rgba(245,158,11,0.98)] transition active:scale-[0.99]"
                      onClick={confirmMatch}
                    >
                      就用这个
                    </button>
                  </div>
                </div>
              )}

              {step === "detail" && (
                <div className="space-y-3">
                  <div className="rounded-2xl border border-black/[0.06] bg-white/[0.86] px-4 py-4 text-center shadow-sm">
                    {selectedAvatar ? (
                      <img
                        src={selectedAvatar}
                        alt={selectedLabel}
                        className="mx-auto h-32 w-32 rounded-full border-2 border-white object-cover shadow-[0_20px_42px_-26px_rgba(17,24,39,0.82)]"
                      />
                    ) : (
                      <span className="mx-auto grid h-32 w-32 place-items-center rounded-full border border-caramel-300/45 bg-white/[0.62]">
                        <SelectedIcon
                          className="h-10 w-10 text-caramel-300"
                          strokeWidth={2.25}
                        />
                      </span>
                    )}
                    <div className="mt-3 text-base font-bold tracking-tight text-ink-900">
                      {selectedLabel}
                    </div>
                    <div className="mt-1 text-xs leading-relaxed text-ink-600">
                      {detailNotes}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-ink-500">
                      常用偏好标签
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {detailPriors.map((trait) => (
                        <span
                          key={trait}
                          className="rounded-full border border-black/[0.08] bg-black/[0.03] px-3 py-1.5 text-xs font-medium text-ink-700"
                        >
                          {trait}
                        </span>
                      ))}
                    </div>
                  </div>
                  {isCurrentDetail && preferences?.suggested_distance_max_km != null && (
                    <div className="rounded-2xl border border-black/[0.06] bg-black/[0.02] px-3 py-2 text-xs font-medium text-ink-600">
                      建议距离{" "}
                      <span className="font-bold text-ink-900">
                        {preferences.suggested_distance_max_km} km
                      </span>
                    </div>
                  )}
                  {isCurrentDetail && (acceptedTop.length > 0 || rejectedTop.length > 0) && (
                    <div className="rounded-2xl border border-black/[0.06] bg-white/[0.78] px-3 py-2.5 text-xs leading-relaxed text-ink-600">
                      {acceptedTop.length > 0 && (
                        <div>
                          <span className="font-semibold text-ink-900">已学到 </span>
                          {acceptedTop.map(([t, n], i) => (
                            <span key={t}>
                              {i > 0 && "、"}
                              {t}
                              <span className="text-ink-400">×{n}</span>
                            </span>
                          ))}
                        </div>
                      )}
                      {rejectedTop.length > 0 && (
                        <div className="mt-1">
                          <span className="font-semibold text-ink-900">少安排 </span>
                          {rejectedTop.map(([t, n], i) => (
                            <span key={t}>
                              {i > 0 && "、"}
                              {t}
                              <span className="text-ink-400">×{n}</span>
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  <button
                    type="button"
                    className="w-full rounded-xl border border-black/[0.08] bg-white/[0.76] px-3 py-2.5 text-sm font-semibold text-ink-700 transition hover:border-accent-400/50 hover:text-ink-900 active:scale-[0.99]"
                    onClick={restartMatch}
                  >
                    更换人物画像
                  </button>
                  {isCurrentDetail && acceptedCount > 0 && (
                    <button
                      type="button"
                      onClick={() => void resetUserMemory()}
                      className="w-full text-xs font-medium text-ink-500 underline decoration-dotted underline-offset-4 transition-colors hover:text-rose-500"
                    >
                      清空记忆
                    </button>
                  )}
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

