"use client";

/**
 * TtsPlayer —— 行程语音播报（Web Speech API）。
 *
 * 设计动机（对应 R6 / spec frontend-experience-innovation §6）：
 *   评委评分项「多模态输出」加分项。用户在不看屏幕的情况下听完行程摘要。
 *   纯前端 Web Speech API（speechSynthesis），无外部依赖，无后端改动。
 *
 * 状态机（Property 4）：
 *   idle → playing：点击「语音播报」
 *   playing → paused：点击「暂停」
 *   paused → playing：点击「继续」
 *   playing/paused → idle：点击「停止」 / 自然播完（onend）/ 出错（onerror）
 *
 * 降级（R6 #6）：
 *   - 浏览器不支持 speechSynthesis：return null（静默隐藏）
 *   - itinerary 为 null：return null
 *
 * 摘要文本生成（R6 #2）：
 *   遍历 stages 拼接为「[start] 去[title]（[kind]）」，用「，然后」连接。
 *   总长度截断到 500 字符防 TTS 过长。
 */

import { useEffect, useRef, useState } from "react";
import { Mic, Pause, Play, Square } from "lucide-react";

import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import type { Itinerary } from "@/lib/types";

// ============================================================
// 摘要文本生成
// ============================================================

function buildSpeechText(itinerary: Itinerary): string {
  // edge_v1：用 nodes（过滤 home）做语音文案；与原 stages 文本结构等价。
  const speakable = (itinerary.nodes || []).filter(
    (n) => n.target_kind !== "home",
  );
  if (speakable.length === 0) {
    return itinerary.summary || "暂无行程信息";
  }
  const parts = speakable.map((n) => {
    return `${n.start_time} 去${n.title}（${n.kind}）`;
  });
  const text = parts.join("，然后");
  // 加摘要开头
  const intro = itinerary.summary
    ? `${itinerary.summary}。本次行程：`
    : "本次行程：";
  const full = intro + text;
  return full.length <= 500 ? full : full.slice(0, 497) + "……";
}

// ============================================================
// 浏览器能力探测（SSR 阶段需 typeof window === 'undefined' 兜底）
// ============================================================

function hasSpeechSynthesis(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.speechSynthesis !== "undefined" &&
    typeof window.SpeechSynthesisUtterance !== "undefined"
  );
}

// ============================================================
// 主组件
// ============================================================

type TtsStatus = "idle" | "playing" | "paused";

export default function TtsPlayer() {
  const itinerary = useChatStore((s) => s.itinerary);
  const [status, setStatus] = useState<TtsStatus>("idle");
  const [supported, setSupported] = useState(false);
  const utterRef = useRef<SpeechSynthesisUtterance | null>(null);

  // 客户端 mount 后检测能力，避免 SSR hydration mismatch
  useEffect(() => {
    setSupported(hasSpeechSynthesis());
  }, []);

  // 卸载时停止播报，避免组件销毁后语音继续
  useEffect(() => {
    return () => {
      if (typeof window !== "undefined" && window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  // itinerary 变化时（refine 后）也停止当前播报
  useEffect(() => {
    if (typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel();
      setStatus("idle");
    }
  }, [itinerary]);

  if (!supported || !itinerary) return null;

  const play = () => {
    const text = buildSpeechText(itinerary);
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "zh-CN";
    utter.rate = 1.0;
    utter.onend = () => setStatus("idle");
    utter.onerror = () => setStatus("idle");
    utterRef.current = utter;
    // 防止上一次未清干净
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
    setStatus("playing");
  };

  const pause = () => {
    window.speechSynthesis.pause();
    setStatus("paused");
  };

  const resume = () => {
    window.speechSynthesis.resume();
    setStatus("playing");
  };

  const stop = () => {
    window.speechSynthesis.cancel();
    setStatus("idle");
  };

  // ============================================================
  // 渲染：单按钮（idle）/ 三按钮组（playing / paused）
  // ============================================================

  if (status === "idle") {
    return (
      <button
        type="button"
        onClick={play}
        className={cn(
          "mt-2 w-full py-1.5 rounded-lg",
          "bg-white/[0.04] hover:bg-white/[0.08]",
          "border border-white/[0.08] hover:border-white/[0.16]",
          "text-ink-700 hover:text-ink-900 text-xs",
          "transition-all flex items-center justify-center gap-1.5",
        )}
        title="使用浏览器内置语音朗读行程摘要"
      >
        <Mic className="w-3.5 h-3.5 text-brand-400" strokeWidth={2} />
        <span>语音播报行程</span>
      </button>
    );
  }

  return (
    <div
      className={cn(
        "mt-2 w-full py-1.5 px-3 rounded-lg",
        "bg-brand-500/10 border border-brand-500/30",
        "flex items-center gap-2",
      )}
      role="status"
      aria-live="polite"
    >
      {/* 波形动画 */}
      <WaveformIndicator active={status === "playing"} />
      <span className="text-[11px] text-brand-300 font-medium tracking-tight flex-1 truncate">
        {status === "playing" ? "播报中…" : "已暂停"}
      </span>
      {status === "playing" ? (
        <button
          type="button"
          onClick={pause}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-ink-700 hover:text-ink-900 bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.08] transition-colors"
        >
          <Pause className="w-3 h-3" strokeWidth={2} />
          <span>暂停</span>
        </button>
      ) : (
        <button
          type="button"
          onClick={resume}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-ink-700 hover:text-ink-900 bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.08] transition-colors"
        >
          <Play className="w-3 h-3" strokeWidth={2} />
          <span>继续</span>
        </button>
      )}
      <button
        type="button"
        onClick={stop}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-rose-300 hover:text-rose-200 bg-rose-500/5 hover:bg-rose-500/10 border border-rose-500/20 transition-colors"
      >
        <Square className="w-3 h-3" strokeWidth={2} />
        <span>停止</span>
      </button>
    </div>
  );
}

// ============================================================
// 波形动画指示器
// ============================================================

function WaveformIndicator({ active }: { active: boolean }) {
  return (
    <div className="flex items-center gap-0.5" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <span
          key={i}
          className={cn(
            "w-0.5 bg-brand-400 rounded-full",
            active && "animate-pulse",
          )}
          style={{
            height: active ? `${6 + (i % 3) * 4}px` : "4px",
            animationDelay: `${i * 0.15}s`,
            animationDuration: "0.8s",
          }}
        />
      ))}
    </div>
  );
}
