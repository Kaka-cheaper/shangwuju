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
import { Image as ImageIcon, Download, X, Loader2, RefreshCw } from "lucide-react";

import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import type { Itinerary } from "@/lib/types";

// ============================================================
// 海报渲染超时
// ============================================================

const POSTER_RENDER_TIMEOUT_MS = 5000;

// ============================================================
// 文字版降级（与 ShareMessage 复制按钮等价的兜底）
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
// 主组件：触发按钮 + 内联预览
// ============================================================

export default function PosterGenerator() {
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
      {/* 触发按钮（替代/隐藏 generated 后） */}
      {!previewUrl && (
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
      )}

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

      {/* 就地内联预览（替代旧的全屏 Modal） */}
      {previewUrl && (
        <InlinePreview
          imageUrl={previewUrl}
          onDownload={download}
          onRegenerate={() => {
            closePreview();
            // 立即触发重新生成，让用户立刻看到刷新动作
            void generate();
          }}
          onClose={closePreview}
        />
      )}
    </>
  );
}

// ============================================================
// 内联预览（替代全屏 Modal）—— 「卡片内一段」而非 popup
// ============================================================

function InlinePreview({
  imageUrl,
  onDownload,
  onRegenerate,
  onClose,
}: {
  imageUrl: string;
  onDownload: () => void;
  onRegenerate: () => void;
  onClose: () => void;
}) {
  return (
    <div className="mt-2 rounded-md border border-brand-500/20 bg-gradient-to-br from-brand-500/[0.04] to-accent-500/[0.04] p-2.5 animate-collapse-in">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <ImageIcon
            className="w-3 h-3 text-brand-400"
            strokeWidth={2}
          />
          <span className="text-[11px] font-medium text-ink-800 tracking-tight">
            海报预览
          </span>
          <span className="text-[10px] text-ink-500">点击可保存</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onRegenerate}
            className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded text-ink-500 hover:text-ink-900 hover:bg-white/[0.08] transition-colors"
            title="重新生成"
          >
            <RefreshCw className="w-2.5 h-2.5" strokeWidth={2} />
            <span>重做</span>
          </button>
          <button
            type="button"
            onClick={onClose}
            className="p-0.5 rounded text-ink-500 hover:text-ink-900 hover:bg-white/[0.08] transition-colors"
            title="收起"
            aria-label="收起预览"
          >
            <X className="w-3 h-3" strokeWidth={2} />
          </button>
        </div>
      </div>

      {/* 海报图片（点击下载） */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={imageUrl}
        alt="行程海报"
        onClick={onDownload}
        className={cn(
          "w-full max-w-[280px] mx-auto rounded-md cursor-pointer",
          "shadow-lg shadow-black/20",
          "ring-1 ring-white/[0.06]",
          "hover:ring-2 hover:ring-brand-400/40 transition-all",
          "block",
        )}
        title="点击保存到本地"
      />

      <button
        type="button"
        onClick={onDownload}
        className={cn(
          "mt-2 w-full py-1.5 rounded-md text-xs font-medium transition-all",
          "bg-gradient-to-r from-brand-500 to-accent-500 text-white",
          "hover:from-brand-400 hover:to-accent-400",
          "flex items-center justify-center gap-1.5",
          "shadow shadow-brand-500/20",
        )}
      >
        <Download className="w-3 h-3" strokeWidth={2.5} />
        <span>保存到本地</span>
      </button>
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
    const stages = itinerary.stages.slice(0, maxStages);
    const overflow = Math.max(0, itinerary.stages.length - maxStages);
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
