"use client";

/**
 * PosterGenerator —— 一键生成行程海报（spec R5）。
 *
 * 设计变更（2026-05-21 重做）：
 *   - 库：html2canvas → modern-screenshot（渲染质量更高，中文字体不变形）
 *   - 风格：「小红书」暖色卡片（白底 / 渐变 chip / 圆角 / 大字标题）
 *   - 呈现：去掉全屏 Modal，改「就地内联预览」——
 *     生成完直接在 ShareMessage 下方展开 section，配收起按钮
 *
 * 工作流：
 *   1. 点击「生成海报」
 *   2. 渲染 PosterTemplate 到隐藏 DOM（off-screen）
 *   3. modern-screenshot 截图 → blob → Object URL
 *   4. 在 ShareMessage 下方就地展开预览卡片 + 「保存」/「重做」/「收起」按钮
 *
 * 状态：
 *   - generating：生成中
 *   - previewUrl：预览图就绪
 *   - 失败 → toast + 复制文字版兜底
 */

import { forwardRef, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Image as ImageIcon,
  Download,
  X,
  Loader2,
  RefreshCw,
  Copy,
  Check,
} from "lucide-react";

import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import type { Itinerary } from "@/lib/types";

// ============================================================
// 海报渲染超时
// ============================================================

const POSTER_RENDER_TIMEOUT_MS = 5000;

// ============================================================
// edge_v1 适配：把 nodes（过滤 home）压成原 stage 形状给海报渲染层用
// ============================================================

interface PosterStage {
  start: string;
  end: string;
  title: string;
  kind: string;
  note?: string | null;
}

function addMinutesHHMM(start: string, minutes: number): string {
  const m = /^(\d{1,2}):(\d{2})$/.exec(start);
  if (!m) return start;
  const total = Number(m[1]) * 60 + Number(m[2]) + (minutes || 0);
  const wrap = ((total % (24 * 60)) + 24 * 60) % (24 * 60);
  return `${String(Math.floor(wrap / 60)).padStart(2, "0")}:${String(wrap % 60).padStart(2, "0")}`;
}

function nodesToStages(itinerary: Itinerary): PosterStage[] {
  return (itinerary.nodes || [])
    .filter((n) => n.target_kind !== "home")
    .map((n) => ({
      start: n.start_time,
      end: addMinutesHHMM(n.start_time, n.duration_min),
      title: n.title,
      kind: n.kind,
      note: n.note ?? null,
    }));
}

// ============================================================
// 文字版降级（与 ShareMessage 复制按钮等价的兜底）
// ============================================================

function buildTextFallback(itinerary: Itinerary): string {
  const lines: string[] = [];
  lines.push(`【晌午局】${itinerary.summary || "本次行程"}`);
  lines.push(`总时长 ${(itinerary.total_minutes / 60).toFixed(1)} 小时`);
  lines.push("");
  for (const stage of nodesToStages(itinerary)) {
    lines.push(`${stage.start}-${stage.end}  ${stage.title}（${stage.kind}）`);
  }
  return lines.join("\n");
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } finally {
      document.body.removeChild(ta);
    }
    return ok;
  }
}

// ============================================================
// 主组件：触发按钮 + 海报预览
// ============================================================

interface PosterGeneratorProps {
  compact?: boolean;
  className?: string;
  variant?: "desktop" | "mobile";
}

export default function PosterGenerator({
  compact = false,
  className,
  variant = "desktop",
}: PosterGeneratorProps) {
  const itinerary = useChatStore((s) => s.itinerary);
  const pushToast = useChatStore((s) => s.pushToast);

  const [generating, setGenerating] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewBlob, setPreviewBlob] = useState<Blob | null>(null);
  const templateRef = useRef<HTMLDivElement>(null);

  // 用 ref 跟踪当前 url 让 cleanup 取最新值
  const previewUrlRef = useRef<string | null>(null);
  useEffect(() => {
    previewUrlRef.current = previewUrl;
  }, [previewUrl]);

  // 卸载时释放 Object URL
  useEffect(() => {
    return () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    };
  }, []);

  // itinerary 变化时重置预览（refine 后旧海报失效）
  useEffect(() => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    setPreviewUrl(null);
    setPreviewBlob(null);
  }, [itinerary]);

  if (!itinerary) return null;

  const generate = async () => {
    if (generating || !templateRef.current) return;
    setGenerating(true);

    try {
      // 动态 import modern-screenshot（避免 SSR + 减小首屏 bundle）
      const { domToBlob } = await import("modern-screenshot");

      const blob = await Promise.race([
        domToBlob(templateRef.current, {
          scale: 2, // 2x 输出
          quality: 0.95,
          backgroundColor: "#fffaf2",
        }),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error("render_timeout")), POSTER_RENDER_TIMEOUT_MS),
        ),
      ]);

      if (!blob) throw new Error("blob_null");

      // 释放旧 url
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);

      const url = URL.createObjectURL(blob);
      setPreviewUrl(url);
      setPreviewBlob(blob);
    } catch (e) {
      const reason = e instanceof Error ? e.message : "unknown";
      const isTimeout = reason === "render_timeout";
      pushToast({
        kind: "warn",
        text: isTimeout ? "海报生成超时，已复制文字版" : "海报生成失败，已复制文字版",
      });
      const fallback = buildTextFallback(itinerary);
      const ok = await copyToClipboard(fallback);
      if (!ok) {
        pushToast({ kind: "warn", text: "复制也失败了…可以手动选择文字" });
      }
    } finally {
      setGenerating(false);
    }
  };

  const download = () => {
    if (!previewBlob) return;
    const a = document.createElement("a");
    const date = new Date();
    const ts = `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, "0")}${String(date.getDate()).padStart(2, "0")}`;
    a.download = `晌午局-行程-${ts}.png`;
    a.href = URL.createObjectURL(previewBlob);
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    pushToast({ kind: "success", text: "海报已保存到本地" });
  };

  const closePreview = () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    setPreviewUrl(null);
    setPreviewBlob(null);
  };

  return (
    <>
      {/* 触发按钮（独立挂载于 ItineraryCard 操作按钮区） */}
      <button
        type="button"
        onClick={generate}
        disabled={generating}
        className={cn(
          compact ? "h-10 w-full rounded-full px-3" : "mt-2 w-full py-1.5 rounded-full",
          "bg-white hover:bg-black/[0.03]",
          "border border-ink-300",
          "text-ink-700 text-base",
          "transition-all flex items-center justify-center gap-1.5",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          className,
        )}
        title="把行程渲染成竖版海报图，可保存转发到微信群"
      >
        {generating ? (
          <>
            <Loader2 className="w-3.5 h-3.5 animate-spin" strokeWidth={2} />
            <span>生成海报中…</span>
          </>
        ) : (
          <>
            <ImageIcon
              className="w-3.5 h-3.5 text-ink-700"
              strokeWidth={2}
            />
            <span>一键生成海报</span>
          </>
        )}
      </button>

      {/* 隐藏海报模板（off-screen 截图源） */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          left: "-9999px",
          top: 0,
          pointerEvents: "none",
        }}
      >
        <PosterTemplate ref={templateRef} itinerary={itinerary} />
      </div>

      {/* 双栏 Modal 预览（Canva 风：左海报 + 右操作）—— Portal 到 body 避免父级 stacking context 限制 */}
      {previewUrl && typeof document !== "undefined" && createPortal(
        <PreviewModal
          imageUrl={previewUrl}
          itinerary={itinerary}
          onDownload={download}
          onRegenerate={() => {
            closePreview();
            void generate();
          }}
          onClose={closePreview}
          pushToast={pushToast}
          mobile={variant === "mobile"}
        />,
        document.body,
      )}
    </>
  );
}

// ============================================================
// 双栏 Modal 预览（Canva 风）
//   左栏：海报缩略图（自适应 Modal 高度）
//   右栏：标题 + 三按钮（保存 / 重做 / 复制文字）
// ============================================================

function PreviewModal({
  imageUrl,
  itinerary,
  onDownload,
  onRegenerate,
  onClose,
  pushToast,
  mobile,
}: {
  imageUrl: string;
  itinerary: Itinerary;
  onDownload: () => void;
  onRegenerate: () => void;
  onClose: () => void;
  pushToast: (t: { kind: "info" | "success" | "warn"; text: string }) => void;
  mobile: boolean;
}) {
  const [textCopied, setTextCopied] = useState(false);

  // ESC 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 锁定背景滚动
  useEffect(() => {
    const original = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = original;
    };
  }, []);

  const copyText = async () => {
    const ok = await copyToClipboard(buildTextFallback(itinerary));
    if (ok) {
      setTextCopied(true);
      pushToast({ kind: "success", text: "文字版已复制到剪贴板" });
      setTimeout(() => setTextCopied(false), 1800);
    } else {
      pushToast({ kind: "warn", text: "复制失败" });
    }
  };

  if (mobile) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-end justify-center bg-black/[0.42] p-3 backdrop-blur-sm animate-fade-in"
        onClick={onClose}
        role="dialog"
        aria-modal="true"
        aria-label="行程海报预览"
      >
        <div
          className="relative flex max-h-[88dvh] w-full max-w-[480px] flex-col overflow-hidden rounded-[32px] border border-white/[0.86] bg-white shadow-[0_28px_80px_-40px_rgba(17,24,39,0.88)] animate-drawer-slide-up"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between gap-3 border-b border-black/[0.05] px-4 py-3.5">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-[#d97706]">
                <ImageIcon className="h-3.5 w-3.5" strokeWidth={2} />
                <span>海报已生成</span>
              </div>
              <h3 className="mt-0.5 text-lg font-black tracking-tight text-ink-900">
                转发给同行人
              </h3>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-black/[0.06] bg-black/[0.03] text-ink-500 transition active:scale-95"
              aria-label="关闭预览"
            >
              <X className="h-4 w-4" strokeWidth={2.25} />
            </button>
          </div>

          <div className="min-h-0 overflow-y-auto px-4 pb-4 pt-3">
            <div className="rounded-[28px] border border-[#FFD100]/25 bg-gradient-to-br from-[#fffaf2] via-white to-[#fff7d6] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.9)]">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={imageUrl}
                alt="行程海报"
                className="mx-auto max-h-[46dvh] w-auto max-w-full rounded-[20px] shadow-[0_22px_48px_-30px_rgba(17,24,39,0.86)]"
              />
            </div>

            <p className="mt-3 text-sm leading-relaxed text-ink-500">
              保存图片后可以直接转发给同行人。也可以改用文字版，一键复制到剪贴板。
            </p>

            <button
              type="button"
              onClick={onDownload}
              className="mt-4 flex h-12 w-full items-center justify-center gap-2 rounded-full bg-[#FFD100] px-4 text-base font-black text-ink-950 shadow-[0_16px_34px_-24px_rgba(245,158,11,0.95)] transition active:scale-[0.98]"
            >
              <Download className="h-[18px] w-[18px]" strokeWidth={2.25} />
              <span>保存到本地</span>
            </button>

            <div className="mt-2 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={onRegenerate}
                className="flex h-11 items-center justify-center gap-1.5 rounded-full border border-black/[0.08] bg-white/[0.78] px-3 text-sm font-semibold text-ink-700 transition active:scale-[0.98]"
              >
                <RefreshCw className="h-3.5 w-3.5" strokeWidth={2} />
                <span>重做</span>
              </button>
              <button
                type="button"
                onClick={copyText}
                className={cn(
                  "flex h-11 items-center justify-center gap-1.5 rounded-full border px-3 text-sm font-semibold transition active:scale-[0.98]",
                  textCopied
                    ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-600"
                    : "border-black/[0.08] bg-white/[0.78] text-ink-700",
                )}
              >
                {textCopied ? (
                  <>
                    <Check className="h-3.5 w-3.5" strokeWidth={2.5} />
                    <span>已复制</span>
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" strokeWidth={2} />
                    <span>文字版</span>
                  </>
                )}
              </button>
            </div>

            <div className="mt-3 rounded-2xl border border-black/[0.04] bg-black/[0.025] px-3 py-2 text-xs leading-relaxed text-ink-500">
              移动端也可以长按海报图片保存。
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center p-3 sm:items-center sm:p-6 animate-fade-in"
      style={{ background: "rgba(0,0,0,0.78)", backdropFilter: "blur(12px)" }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="行程海报预览"
    >
      <div
        className={cn(
          "relative bg-white rounded-[28px] sm:rounded-2xl border border-black/[0.08]",
          "shadow-2xl shadow-black/50",
          "w-full max-w-[430px] sm:max-w-3xl",
          "flex flex-col sm:flex-row",
          "max-h-[88vh] sm:max-h-[92vh]",
          "overflow-hidden",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 关闭按钮 */}
        <button
          type="button"
          onClick={onClose}
          className={cn(
            "absolute top-3 right-3 z-10",
            "w-10 h-10 sm:w-7 sm:h-7 rounded-full",
            "bg-black/[0.04] hover:bg-black/[0.06]",
            "border border-black/[0.08]",
            "text-ink-500 hover:text-ink-900",
            "flex items-center justify-center transition-colors",
          )}
          aria-label="关闭预览"
        >
          <X className="w-4 h-4 sm:w-3.5 sm:h-3.5" strokeWidth={2.25} />
        </button>

        {/* 左栏：海报展示 */}
        <div
          className={cn(
            "flex-shrink-0 flex items-center justify-center",
            "p-4 pt-5 sm:p-8",
            "bg-ink-50",
            "sm:w-[320px]",
          )}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt="行程海报"
            className={cn(
              "max-h-[42vh] sm:max-h-[78vh] w-auto rounded-[18px] sm:rounded-lg",
              "shadow-2xl shadow-black/40",
              "ring-1 ring-white/[0.08]",
              "block",
            )}
            style={{ maxWidth: "100%" }}
          />
        </div>

        {/* 右栏：标题 + 操作 */}
        <div className="flex-1 p-4 sm:p-6 flex flex-col gap-3 sm:gap-4 min-w-0 overflow-y-auto">
          <div>
            <div className="text-xs tracking-wider uppercase text-accent-600 mb-1 flex items-center gap-1">
              <ImageIcon className="w-3 h-3" strokeWidth={2} />
              <span>海报已生成</span>
            </div>
            <h3 className="text-base sm:text-lg font-semibold text-ink-900 tracking-tight leading-snug">
              转发给同行人
            </h3>
            <p className="mt-1.5 text-xs text-ink-500 leading-relaxed">
              微信群里转发一张图，比一段文字更直观。点
              <span className="text-accent-600 mx-0.5">保存</span>
              下载到本地后即可分享。
            </p>
          </div>

          {/* 主操作 */}
          <button
            type="button"
            onClick={onDownload}
            className={cn(
              "w-full py-3 rounded-full font-semibold text-sm",
              "bg-gradient-to-r from-accent-500 to-accent-600 text-white",
              "hover:from-accent-400 hover:to-accent-500",
              "shadow-lg shadow-accent-500/20",
              "transition-all flex items-center justify-center gap-2",
            )}
          >
            <Download className="w-4 h-4" strokeWidth={2.25} />
            <span>保存到本地</span>
          </button>

          {/* 副操作 */}
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={onRegenerate}
              className={cn(
                "py-2.5 rounded-full text-xs font-semibold",
                "bg-black/[0.03] hover:bg-black/[0.05]",
                "border border-black/[0.08] hover:border-black/[0.12]",
                "text-ink-700 hover:text-ink-900",
                "transition-colors flex items-center justify-center gap-1.5",
              )}
            >
              <RefreshCw className="w-3 h-3" strokeWidth={2} />
              <span>重做</span>
            </button>
            <button
              type="button"
              onClick={copyText}
              className={cn(
                "py-2.5 rounded-full text-xs font-semibold",
                textCopied
                  ? "bg-emerald-500/15 border border-emerald-500/30 text-emerald-600"
                  : "bg-black/[0.03] hover:bg-black/[0.05] border border-black/[0.08] hover:border-black/[0.12] text-ink-700 hover:text-ink-900",
                "transition-colors flex items-center justify-center gap-1.5",
              )}
            >
              {textCopied ? (
                <>
                  <Check className="w-3 h-3" strokeWidth={2.5} />
                  <span>文字已复制</span>
                </>
              ) : (
                <>
                  <Copy className="w-3 h-3" strokeWidth={2} />
                  <span>改用文字版</span>
                </>
              )}
            </button>
          </div>

          {/* 小提示 */}
          <div
            className={cn(
              "mt-auto rounded-2xl px-3 py-2 text-xs leading-relaxed",
              "bg-black/[0.02] border border-black/[0.04]",
              "text-ink-500",
            )}
          >
            <span className="text-ink-700">提示：</span>
            移动端长按图片也可保存；按 Esc 或点击空白处关闭。
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// 海报模板：小红书风格暖色卡片
// 设计要点：
//   - 米白底（#fffaf2 暖色调，比纯白柔和）
//   - 大字标题（28px 黑色加粗）
//   - 圆形彩色 chip（kind 标签 → 渐变橙粉）
//   - 时间轴用「卡片排列」而非细线（更小红书）
//   - 顶部留出 15-20% 给品牌区
//   - 底部「晌午局」品牌 + 二维码占位（可后续接真实小程序码）
// ============================================================

const PosterTemplate = forwardRef<HTMLDivElement, { itinerary: Itinerary }>(
  function PosterTemplate({ itinerary }, ref) {
    const maxStages = 8;
    const allStages = nodesToStages(itinerary);
    const stages = allStages.slice(0, maxStages);
    const overflow = Math.max(0, allStages.length - maxStages);
    const summary =
      (itinerary.summary || "本次行程").length <= 50
        ? itinerary.summary || "本次行程"
        : (itinerary.summary || "").slice(0, 47) + "…";
    const totalH = (itinerary.total_minutes / 60).toFixed(1);
    const date = new Date();
    const dateStr = `${date.getMonth() + 1}.${String(date.getDate()).padStart(2, "0")}`;

    return (
      <div
        ref={ref}
        style={{
          width: "390px",
          // 不固定 height，让内容自然撑开（小红书海报常见做法，比例 4:5 / 3:4 都常见）
          padding: "28px 24px 24px",
          background: "#fffaf2",
          color: "#1f1f1f",
          fontFamily:
            '"PingFang SC", "Microsoft YaHei", "HarmonyOS Sans SC", system-ui, -apple-system, sans-serif',
          display: "flex",
          flexDirection: "column",
          gap: "20px",
          position: "relative",
          // 模拟纸张柔光（生成图片时透明度 fallback）
          boxShadow: "inset 0 0 80px rgba(251, 146, 60, 0.04)",
        }}
      >
        {/* 头部：品牌标 + 日期 */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div
              style={{
                width: "26px",
                height: "26px",
                borderRadius: "8px",
                background: "linear-gradient(135deg, #fb923c 0%, #ec4899 100%)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "white",
                fontSize: "13px",
                fontWeight: 800,
                letterSpacing: "-0.5px",
              }}
            >
              晌
            </div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <span style={{ fontSize: "13px", fontWeight: 700, color: "#1f1f1f" }}>
                晌午局
              </span>
              <span style={{ fontSize: "10px", color: "#8a8a8a", marginTop: "1px" }}>
                半日出行管家
              </span>
            </div>
          </div>
          <span
            style={{
              fontSize: "11px",
              color: "#8a8a8a",
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              letterSpacing: "0.5px",
            }}
          >
            {dateStr}
          </span>
        </div>

        {/* 主标题区 */}
        <div>
          <div
            style={{
              fontSize: "11px",
              color: "#fb923c",
              fontWeight: 600,
              letterSpacing: "1px",
              marginBottom: "10px",
            }}
          >
            ✦ 今日下午
          </div>
          <div
            style={{
              fontSize: "22px",
              fontWeight: 800,
              lineHeight: "1.35",
              color: "#1f1f1f",
              letterSpacing: "-0.5px",
              marginBottom: "8px",
            }}
          >
            {summary}
          </div>
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "4px 10px",
              borderRadius: "999px",
              background: "rgba(251, 146, 60, 0.1)",
              fontSize: "11px",
              color: "#c2410c",
              fontWeight: 600,
            }}
          >
            <span style={{ fontFamily: "ui-monospace, monospace" }}>
              {totalH}
            </span>
            <span>小时安排</span>
          </div>
        </div>

        {/* 时间轴：卡片堆叠（小红书风） */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "10px",
          }}
        >
          {stages.map((stage, idx) => (
            <div
              key={idx}
              style={{
                display: "flex",
                gap: "12px",
                padding: "12px 14px",
                background: "#ffffff",
                borderRadius: "12px",
                boxShadow:
                  "0 1px 0 rgba(0,0,0,0.04), 0 4px 12px -4px rgba(251,146,60,0.08)",
                border: "1px solid rgba(251, 146, 60, 0.08)",
              }}
            >
              {/* 序号 + 时间 */}
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: "6px",
                  paddingTop: "2px",
                  minWidth: "44px",
                }}
              >
                <div
                  style={{
                    width: "22px",
                    height: "22px",
                    borderRadius: "50%",
                    background:
                      "linear-gradient(135deg, #fb923c 0%, #ec4899 100%)",
                    color: "white",
                    fontSize: "11px",
                    fontWeight: 700,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontFamily: "ui-monospace, monospace",
                  }}
                >
                  {idx + 1}
                </div>
                <div
                  style={{
                    fontSize: "10px",
                    color: "#525252",
                    fontFamily: "ui-monospace, monospace",
                    textAlign: "center",
                    lineHeight: "1.4",
                  }}
                >
                  <div>{stage.start}</div>
                  <div style={{ color: "#a3a3a3" }}>{stage.end}</div>
                </div>
              </div>

              {/* 内容区 */}
              <div style={{ flex: 1, minWidth: 0, paddingTop: "1px" }}>
                <div
                  style={{
                    display: "inline-block",
                    fontSize: "10px",
                    padding: "2px 8px",
                    borderRadius: "999px",
                    background: "rgba(236, 72, 153, 0.08)",
                    color: "#be185d",
                    fontWeight: 600,
                    marginBottom: "6px",
                  }}
                >
                  {stage.kind}
                </div>
                <div
                  style={{
                    fontSize: "14px",
                    fontWeight: 600,
                    color: "#1f1f1f",
                    lineHeight: "1.4",
                    letterSpacing: "-0.2px",
                  }}
                >
                  {stage.title}
                </div>
                {stage.note && (
                  <div
                    style={{
                      fontSize: "11px",
                      color: "#737373",
                      marginTop: "4px",
                      lineHeight: "1.5",
                    }}
                  >
                    {stage.note.length <= 36
                      ? stage.note
                      : stage.note.slice(0, 33) + "…"}
                  </div>
                )}
              </div>
            </div>
          ))}

          {overflow > 0 && (
            <div
              style={{
                fontSize: "11px",
                color: "#a3a3a3",
                textAlign: "center",
                fontStyle: "italic",
                padding: "4px",
              }}
            >
              · · · 等 {overflow} 个地点 · · ·
            </div>
          )}
        </div>

        {/* 底部品牌条 + 装饰点 */}
        <div
          style={{
            paddingTop: "16px",
            borderTop: "1px dashed rgba(251, 146, 60, 0.2)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div
            style={{
              fontSize: "11px",
              color: "#737373",
              letterSpacing: "0.3px",
            }}
          >
            <span style={{ color: "#fb923c", fontWeight: 600 }}>AI</span> 帮你串好行程
          </div>
          <div
            style={{
              fontSize: "10px",
              color: "#a3a3a3",
              fontFamily: "ui-monospace, monospace",
              letterSpacing: "1px",
            }}
          >
            shangwuju.app
          </div>
        </div>
      </div>
    );
  },
);

