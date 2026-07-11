"use client";

/**
 * Web / 移动根视图的唯一选择入口。
 *
 * 只负责读取屏幕与主输入设备能力，并按需挂载 HomeView 或
 * MobileHomeView；不承担会话、规划、协作或路由业务。两棵重组件不会
 * 同时挂载，避免重复初始化、请求和 WebSocket 连接。
 */

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import {
  COMPACT_VIEW_QUERY,
  resolveInterfaceMode,
  TOUCH_FIRST_QUERY,
  type InterfaceMode,
} from "@/lib/interface-mode";

const DesktopHomeView = dynamic(() => import("./HomeView"), {
  loading: AdaptiveLoadingShell,
  ssr: false,
});

const MobileHomeView = dynamic(() => import("./mobile/MobileHomeView"), {
  loading: AdaptiveLoadingShell,
  ssr: false,
});

interface AdaptiveAppShellProps {
  /** `/m` 作为兼容入口时强制移动模式；其余入口保持自动判定。 */
  forcedMode?: InterfaceMode;
}

export default function AdaptiveAppShell({
  forcedMode,
}: AdaptiveAppShellProps) {
  const detectedMode = useDetectedInterfaceMode(forcedMode);

  if (detectedMode === null) {
    return <AdaptiveLoadingShell />;
  }

  return detectedMode === "mobile" ? <MobileHomeView /> : <DesktopHomeView />;
}

function useDetectedInterfaceMode(
  forcedMode: InterfaceMode | undefined,
): InterfaceMode | null {
  const [mode, setMode] = useState<InterfaceMode | null>(forcedMode ?? null);

  useEffect(() => {
    if (forcedMode) {
      setMode(forcedMode);
      return;
    }
    if (typeof window === "undefined" || !window.matchMedia) return;

    const compactViewport = window.matchMedia(COMPACT_VIEW_QUERY);
    const touchFirst = window.matchMedia(TOUCH_FIRST_QUERY);
    const update = () => {
      setMode(
        resolveInterfaceMode({
          compactViewport: compactViewport.matches,
          touchFirst: touchFirst.matches,
        }),
      );
    };

    update();
    const cleanups = [
      subscribeMediaQuery(compactViewport, update),
      subscribeMediaQuery(touchFirst, update),
    ];
    return () => cleanups.forEach((cleanup) => cleanup());
  }, [forcedMode]);

  return forcedMode ?? mode;
}

function subscribeMediaQuery(
  query: MediaQueryList,
  listener: () => void,
): () => void {
  if (query.addEventListener) {
    query.addEventListener("change", listener);
    return () => query.removeEventListener("change", listener);
  }

  // Safari <14 兼容。
  query.addListener(listener);
  return () => query.removeListener(listener);
}

function AdaptiveLoadingShell() {
  return (
    <div className="min-h-screen grid place-items-center bg-[#fffdf7] text-ink-500">
      <div className="flex items-center gap-2 text-sm" role="status">
        <span className="h-2 w-2 animate-pulse rounded-full bg-[#FFD100]" />
        正在打开晌午局…
      </div>
    </div>
  );
}
