"use client";

/**
 * PosterGenerator —— 一键生成行程海报（spec R5）。
 *
 * 设计动机：
 *   评委评分项「多模态输出」加分项。用户转发到微信群的不是文字，而是一张
 *   好看的竖版海报图。
 *   纯前端 html2canvas 截图，无后端改动。
 *
 * 工作流：
 *   1. 点击「生成海报」按钮
 *   2. 渲染隐藏 DOM 海报模板（off-screen，竖版 375×667 / 2x 输出）
 *   3. html2canvas 截图 → blob → Object URL
 *   4. 弹出预览 Modal + 下载按钮
 *   5. 5 秒超时 → 错误提示 + 重试 / 复制文字版降级
 *
 * 触发条件：store.itinerary 非空（unchecked，按钮不渲染）
 *
 * 位置：ItineraryCard 的 ShareMessage 区域旁（"复制" 按钮的右侧）。
 *
 * 不负责：
 *   - 文字版转发（已有 ShareMessage 组件）
 *   - 实际网络分享（用户保存到本地后自行转发）
 */

import { useEffect, useRef, useState } from "react";
import { Image as ImageIcon, Download, X, Loader2 } from "lucide-react";

import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import type { Itinerary } from "@/lib/types";

// ============================================================
// 海报渲染超时（防 html2canvas 卡死）
// ============================================================

const POSTER_RENDER_TIMEOUT_MS = 5000;

// ============================================================
// 文字版降级：复用 ShareMessage 已有逻辑（这里独立实现）
// ============================================================

function buildTextFallback(itinerary: Itinerary): string {
  const lines: string[] = [];
  lines.push(`【晌午局】${itinerary.summary || "本次行程"}`);
  lines.push(`总时长 ${(itinerary.total_minutes / 60).toFixed(1)} 小时`);
  lines.push("");
  for (const stage of itinerary.stages) {
    lines.push(`${stage.start}-${stage.end}  ${stage.title}（${stage.kind}）`);
  }
  return lines.join("\n");
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // execCommand 兜底
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
// 主组件
// ============================================================

export default function PosterGenerator() {
  const itinerary = useChatStore((s) => s.itinerary);
  const pushToast = useChatStore((s) => s.pushToast);

  const [generating, setGenerating] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewBlob, setPreviewBlob] = useState<Blob | null>(null);
  const templateRef = useRef<HTMLDivElement>(null);

  // 卸载时释放 Object URL（防内存泄漏）
  // 用 ref 存当前 url 让 cleanup 取到最新值，避免 effect 依赖 previewUrl 导致频繁重跑
  const previewUrlRef = useRef<string | null>(null);
  useEffect(() => {
    previewUrlRef.current = previewUrl;
  }, [previewUrl]);
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
      // 动态 import html2canvas（避免 SSR 阶段加载 + 减小首屏 bundle）
      const html2canvasModule = await import("html2canvas");
      const html2canvas = html2canvasModule.default;

      // Promise.race 防卡死
      const canvas = await Promise.race([
        html2canvas(templateRef.current, {
          scale: 2, // 2x 输出（750×1334 物理像素）
          useCORS: true,
          backgroundColor: null,
          logging: false,
        }),
        new Promise<never>((_, reject) =>
          setTimeout(
            () => reject(new Error("render_timeout")),
            POSTER_RENDER_TIMEOUT_MS,
          ),
        ),
      ]);

      const blob: Blob | null = await new Promise((resolve) => {
        canvas.toBlob((b) => resolve(b), "image/png");
      });

      if (!blob) {
        throw new Error("toBlob_returned_null");
      }

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
      // 降级：复制文字版
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
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    setPreviewBlob(null);
  };

  return (
    <>
      {/* 触发按钮：复制按钮旁边 */}
      <button
        type="button"
        onClick={generate}
        disabled={generating}
        className={cn(
          "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded transition-colors",
          "bg-white/[0.06] text-ink-700 border border-white/[0.08]",
          "hover:bg-white/[0.1] hover:text-ink-900",
          "disabled:opacity-50 disabled:cursor-not-allowed",
        )}
        title="把行程渲染成竖版海报图，可保存转发"
      >
        {generating ? (
          <>
            <Loader2 className="w-3 h-3 animate-spin" strokeWidth={2.5} />
            <span>生成中…</span>
          </>
        ) : (
          <>
            <ImageIcon className="w-3 h-3" strokeWidth={2} />
            <span>生成海报</span>
          </>
        )}
      </button>

      {/* 隐藏海报模板（off-screen，html2canvas 截图源） */}
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

      {/* 预览 Modal */}
      {previewUrl && (
        <PreviewModal
          imageUrl={previewUrl}
          onDownload={download}
          onClose={closePreview}
        />
      )}
    </>
  );
}

// ============================================================
// 海报模板（375×667 竖版，2x 输出 = 750×1334）
// ============================================================

const PosterTemplate = function PosterTemplate({
  ref,
  itinerary,
}: {
  ref: React.RefObject<HTMLDivElement>;
  itinerary: Itinerary;
}) {
  // 最多 8 段
  const maxStages = 8;
  const stages = itinerary.stages.slice(0, maxStages);
  const overflow = Math.max(0, itinerary.stages.length - maxStages);
  // summary 最多 60 字符
  const summary =
    (itinerary.summary || "本次行程").length <= 60
      ? itinerary.summary || "本次行程"
      : (itinerary.summary || "").slice(0, 57) + "……";
  const totalH = (itinerary.total_minutes / 60).toFixed(1);
  const date = new Date();
  const dateStr = `${date.getMonth() + 1} 月 ${date.getDate()} 日`;

  return (
    <div
      ref={ref}
      style={{
        width: "375px",
        minHeight: "667px",
        padding: "32px 24px",
        background:
          "linear-gradient(160deg, #1a1a2e 0%, #16213e 45%, #0f3460 100%)",
        color: "#f5f5f7",
        fontFamily:
          'system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        display: "flex",
        flexDirection: "column",
        gap: "20px",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* 顶部装饰 */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: "4px",
          background:
            "linear-gradient(90deg, #fb923c 0%, #ec4899 50%, #8b5cf6 100%)",
        }}
      />

      {/* Header */}
      <div>
        <div
          style={{
            fontSize: "11px",
            color: "#fb923c",
            letterSpacing: "2px",
            textTransform: "uppercase",
            marginBottom: "6px",
          }}
        >
          晌午局 · Half-Day Plan
        </div>
        <div
          style={{
            fontSize: "13px",
            color: "#94a3b8",
            marginBottom: "12px",
          }}
        >
          {dateStr}
        </div>
        <div
          style={{
            fontSize: "18px",
            fontWeight: 600,
            lineHeight: "1.4",
            color: "#f5f5f7",
            letterSpacing: "-0.2px",
          }}
        >
          {summary}
        </div>
        <div
          style={{
            fontSize: "12px",
            color: "#fb923c",
            marginTop: "8px",
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
          }}
        >
          总时长 {totalH} 小时
        </div>
      </div>

      {/* 时间轴 */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: "12px",
          paddingTop: "8px",
        }}
      >
        {stages.map((stage, idx) => (
          <div
            key={idx}
            style={{
              display: "flex",
              gap: "12px",
              alignItems: "flex-start",
            }}
          >
            {/* 时间列 */}
            <div
              style={{
                minWidth: "44px",
                fontFamily: "ui-monospace, SFMono-Regular, monospace",
                fontSize: "11px",
                color: "#cbd5e1",
                textAlign: "right",
                paddingTop: "2px",
              }}
            >
              <div>{stage.start}</div>
              <div style={{ color: "#64748b" }}>{stage.end}</div>
            </div>
            {/* 时间点 */}
            <div
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                marginTop: "6px",
                background: "linear-gradient(135deg, #fb923c, #ec4899)",
                flexShrink: 0,
                boxShadow: "0 0 0 2px rgba(251,146,60,0.2)",
              }}
            />
            {/* 内容 */}
            <div style={{ flex: 1, paddingTop: "2px", minWidth: 0 }}>
              <div
                style={{
                  display: "inline-block",
                  fontSize: "10px",
                  padding: "1px 6px",
                  borderRadius: "3px",
                  background: "rgba(251,146,60,0.12)",
                  color: "#fb923c",
                  marginBottom: "4px",
                }}
              >
                {stage.kind}
              </div>
              <div
                style={{
                  fontSize: "13px",
                  fontWeight: 500,
                  color: "#f5f5f7",
                  lineHeight: "1.4",
                }}
              >
                {stage.title}
              </div>
            </div>
          </div>
        ))}

        {overflow > 0 && (
          <div
            style={{
              fontSize: "11px",
              color: "#94a3b8",
              fontStyle: "italic",
              paddingLeft: "68px",
              paddingTop: "4px",
            }}
          >
            等 {overflow} 个地点……
          </div>
        )}
      </div>

      {/* 底部品牌 */}
      <div
        style={{
          paddingTop: "16px",
          borderTop: "1px solid rgba(255,255,255,0.08)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
        }}
      >
        <div>
          <div
            style={{
              fontSize: "16px",
              fontWeight: 700,
              color: "#fb923c",
              letterSpacing: "-0.3px",
            }}
          >
            晌午局
          </div>
          <div
            style={{
              fontSize: "11px",
              color: "#94a3b8",
              marginTop: "2px",
            }}
          >
            半日出行管家
          </div>
        </div>
        <div
          style={{
            fontSize: "10px",
            color: "#64748b",
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
          }}
        >
          AI Generated
        </div>
      </div>
    </div>
  );
};

// ============================================================
// 预览 Modal
// ============================================================

function PreviewModal({
  imageUrl,
  onDownload,
  onClose,
}: {
  imageUrl: string;
  onDownload: () => void;
  onClose: () => void;
}) {
  // ESC 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4 animate-fade-in"
      style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(8px)" }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="行程海报预览"
    >
      <div
        className="relative bg-[#08080d] rounded-xl border border-white/[0.08] p-4 max-w-md w-full"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <span className="text-[12px] font-medium text-ink-900 tracking-tight">
            预览海报
          </span>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded hover:bg-white/[0.08] text-ink-500 hover:text-ink-900"
            aria-label="关闭"
          >
            <X className="w-3.5 h-3.5" strokeWidth={2} />
          </button>
        </div>

        <div className="rounded-lg overflow-hidden border border-white/[0.08] mb-3 max-h-[70vh] overflow-y-auto">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt="行程海报"
            className="w-full h-auto block"
            style={{ display: "block" }}
          />
        </div>

        <button
          type="button"
          onClick={onDownload}
          className={cn(
            "w-full py-2 rounded-lg font-medium text-sm transition-all flex items-center justify-center gap-2",
            "bg-gradient-to-r from-brand-500 to-accent-500 text-white",
            "hover:from-brand-400 hover:to-accent-400",
            "shadow-lg shadow-brand-500/20",
          )}
        >
          <Download className="w-4 h-4" strokeWidth={2.25} />
          <span>保存到本地</span>
        </button>
      </div>
    </div>
  );
}
