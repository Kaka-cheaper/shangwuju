"use client";

/**
 * 协作房间客户端入口。
 *
 * 支持两种 URL：
 * - /room?room_id={roomId}&nickname=老婆      GitHub Pages 静态导出可用
 * - /room/{roomId}?nickname=老婆             本地 / Node 部署兼容入口
 */

import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";

import HomeView from "@/components/HomeView";
import MobileHomeView from "@/components/mobile/MobileHomeView";
import { useCollabStore } from "@/lib/collab-store";

interface RoomClientProps {
  roomIdFromPath?: string;
}

// A4：邀请链接落地页视口二选一。
//
// 房间邀请链接恒是 `/room?room_id=...`（ShareModal.tsx 生成，Web/移动端共用
// 同一个 URL，不区分 `/m/room`）——协作的天然入口就是扫码，扫码的人几乎都在
// 手机上。此前 RoomClient 全仓无 UA/视口判断，恒 `return <HomeView />`，手机
// 扫码进房看到的是被浏览器挤压显示的桌面版（不是 404，是错的布局）。
//
// 与新增 `app/m/room` 路由这个候选方案相比，选择"同一路由内视口二选一"：
// - 昵称收集卡（NicknameEntryCard）/ 连接中态（RoomShell）本身已是响应式居中
//   小卡片，不需要为移动端另写一份；只有"已连接"之后的主视图需要分叉。
// - 分享链接生成端（ShareModal）不需要跟着改判断逻辑（还是一个 `/room` URL）。
// 视口检测用 matchMedia，SSR/首次渲染恒按桌面路径出（与 HomeView 一致，避免
// hydration mismatch），mount 后一次性根据真实视口纠正——同本文件其余状态
// （collabMode 是否已连接）的"先出兜底态、mount 后纠正"既有模式一致。
const MOBILE_MAX_WIDTH_PX = 640; // 与 Tailwind `sm` 断点对齐（app/m 路由面向同一档位）

function useIsMobileViewport(): boolean {
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(`(max-width: ${MOBILE_MAX_WIDTH_PX}px)`);
    const update = () => setIsMobile(mql.matches);
    update();
    // Safari <14 无 addEventListener，兼容走 addListener（同类兼容写法在
    // 本仓库暂无其它先例，这里是新引入的视口探测，独立兜底）。
    if (mql.addEventListener) {
      mql.addEventListener("change", update);
      return () => mql.removeEventListener("change", update);
    }
    mql.addListener(update);
    return () => mql.removeListener(update);
  }, []);
  return isMobile;
}

export default function RoomClient({ roomIdFromPath }: RoomClientProps) {
  const searchParams = useSearchParams();
  const queryRoomId = searchParams.get("room_id") || searchParams.get("id") || "";
  const roomId = roomIdFromPath || queryRoomId;
  // 深链昵称（扫码邀请场景）：URL 带 nickname 时保持原「无摩擦直接进房」行为。
  const nicknameFromUrl = (searchParams.get("nickname") || "").trim();
  const queryUserId = searchParams.get("user_id");

  const joinRoom = useCollabStore((s) => s.joinRoom);
  const collabMode = useCollabStore((s) => s.collabMode);
  const connected = useCollabStore((s) => s.connected);
  const connectionError = useCollabStore((s) => s.connectionError);
  const isMobileViewport = useIsMobileViewport();

  const [userId] = useState(() => {
    if (queryUserId?.trim()) return queryUserId.trim();
    if (typeof window === "undefined") return "anon";
    const stored = localStorage.getItem("collab_user_id");
    if (stored) return stored;
    const newId = `u_${Date.now().toString(36)}`;
    localStorage.setItem("collab_user_id", newId);
    return newId;
  });

  // ADR-0013 决策 6：URL 无 nickname 时不再默认「参与者」直接进房——先在进房卡片
  // 收集昵称，用户提交后才落到这里触发 joinRoom。手动提交的昵称只在本次会话内存活
  // （不持久化，房散即毁，呼应决策 6「身份归属房间」的方向）。
  const [manualNickname, setManualNickname] = useState<string | null>(null);
  const effectiveNickname = nicknameFromUrl || manualNickname || "";

  useEffect(() => {
    if (roomId && userId && effectiveNickname) {
      joinRoom(roomId, userId, effectiveNickname);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, userId, effectiveNickname]);

  if (!roomId) {
    return (
      <RoomShell>
        <p className="text-red-400 text-sm mb-2">缺少房间 ID</p>
        <p className="text-ink-500 text-xs">请从邀请链接重新进入</p>
      </RoomShell>
    );
  }

  // 没有深链昵称、也还没提交进房卡片 → 先收集昵称，不自动 joinRoom。
  if (!nicknameFromUrl && !manualNickname) {
    return <NicknameEntryCard roomId={roomId} onSubmit={setManualNickname} />;
  }

  if (!collabMode || !connected) {
    return (
      <RoomShell>
        {connectionError ? (
          <>
            <p className="text-red-400 text-sm mb-2">{connectionError}</p>
            <p className="text-ink-500 text-xs">请检查链接是否正确，或刷新重试</p>
          </>
        ) : (
          <>
            <p className="text-ink-400 text-sm mb-2">正在加入房间...</p>
            <p className="text-ink-600 text-xs">房间 ID：{roomId}</p>
          </>
        )}
      </RoomShell>
    );
  }

  return isMobileViewport ? <MobileHomeView /> : <HomeView />;
}

export function RoomShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[#08080d]">
      <div className="text-center">
        <div className="text-2xl mb-3">☕</div>
        {children}
      </div>
    </div>
  );
}

// ============================================================
// NicknameEntryCard —— ADR-0013 决策 6：进房卡片
// URL 无 nickname（非扫码深链）时，先让用户填昵称再进房，而不是拿「参与者」
// 默认值直接连进去。复用 RoomShell 的暗色壳，卡片本身也走暗色语言（RoomShell
// 背景是 #08080d，仓库里唯一的 .card 全局类是浅色主题，套在这里会跳色）。
// ============================================================

function NicknameEntryCard({
  roomId,
  onSubmit,
}: {
  roomId: string;
  onSubmit: (nickname: string) => void;
}) {
  const [value, setValue] = useState("");
  const trimmed = value.trim();
  const valid = trimmed.length >= 1 && trimmed.length <= 12;

  const submit = () => {
    if (!valid) return;
    onSubmit(trimmed);
  };

  return (
    <RoomShell>
      <div className="w-[280px] rounded-xl border border-white/10 bg-white/[0.04] backdrop-blur-sm px-5 py-6 text-left">
        <p className="text-ink-100 text-sm font-medium mb-1">加入房间</p>
        <p className="text-ink-500 text-xs mb-4">房间 ID：{roomId}</p>
        <label htmlFor="room-nickname-input" className="block text-ink-400 text-xs mb-1.5">
          你的昵称
        </label>
        <input
          id="room-nickname-input"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          maxLength={12}
          placeholder="1-12 字，例如「老婆」"
          className="w-full rounded-md border border-white/10 bg-white/5 px-3 py-2 text-sm text-ink-100 placeholder:text-ink-600 focus:outline-none focus:ring-1 focus:ring-accent-500/50 mb-3"
        />
        <button
          type="button"
          disabled={!valid}
          onClick={submit}
          className="btn-primary w-full disabled:opacity-40 disabled:cursor-not-allowed"
        >
          进入房间
        </button>
      </div>
    </RoomShell>
  );
}
