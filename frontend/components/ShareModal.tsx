"use client";

/**
 * 分享弹窗：展示房间链接 + 二维码。
 * 点击"邀请同行人"按钮后弹出。
 */

import { useState, useEffect } from "react";
import { useCollabStore } from "@/lib/collab-store";
import { cn } from "@/lib/utils";

interface ShareModalProps {
  open: boolean;
  onClose: () => void;
  roomId: string;
}

export default function ShareModal({ open, onClose, roomId }: ShareModalProps) {
  const [copied, setCopied] = useState(false);

  if (!open) return null;

  const shareUrl = `${window.location.origin}/room/${roomId}`;
  // 二维码用公共 API 生成（Demo 够用；生产用 qrcode 库）
  const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(shareUrl)}`;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // fallback
      const input = document.createElement("input");
      input.value = shareUrl;
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      document.body.removeChild(input);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={cn(
          "bg-[#12121a] border border-white/[0.1] rounded-2xl p-6 w-[360px]",
          "shadow-2xl animate-in fade-in zoom-in-95 duration-200",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-ink-900 mb-1">邀请同行人</h3>
        <p className="text-sm text-ink-500 mb-4">
          分享链接或扫码加入，一起决定下午去哪
        </p>

        {/* 二维码 */}
        <div className="flex justify-center mb-4">
          <div className="bg-white rounded-xl p-3">
            <img
              src={qrUrl}
              alt="分享二维码"
              width={160}
              height={160}
              className="rounded"
            />
          </div>
        </div>

        {/* 链接 */}
        <div className="flex items-center gap-2 mb-4">
          <input
            type="text"
            readOnly
            value={shareUrl}
            className="flex-1 bg-white/[0.05] border border-white/[0.1] rounded-lg px-3 py-2 text-xs text-ink-400 truncate"
          />
          <button
            type="button"
            onClick={handleCopy}
            className={cn(
              "px-3 py-2 rounded-lg text-xs font-medium transition-all",
              copied
                ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                : "bg-brand-500/20 text-brand-400 border border-brand-500/30 hover:bg-brand-500/30",
            )}
          >
            {copied ? "已复制 ✓" : "复制链接"}
          </button>
        </div>

        {/* 说明 */}
        <p className="text-[11px] text-ink-500 text-center mb-4">
          同行人打开链接后可以提约束、投票，Agent 会实时合并所有人的偏好重新规划
        </p>

        <button
          type="button"
          onClick={onClose}
          className="w-full py-2 rounded-lg bg-white/[0.06] hover:bg-white/[0.1] text-sm text-ink-600 transition-colors"
        >
          关闭
        </button>
      </div>
    </div>
  );
}
