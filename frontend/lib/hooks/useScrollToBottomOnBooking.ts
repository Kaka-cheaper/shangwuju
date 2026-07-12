"use client";

/**
 * useScrollToBottomOnBooking —— 预约成功后变速滚到页面底部，露出"预约成功"态。
 *
 * 触发：itinerary.orders 从 0 → 有（预约落地的确定信号）。单人 confirm() 与协作
 * sendConfirm() 最终都让 orders 落地，故监听 orders 数一处即覆盖两种模式。
 *
 * 变速手感（用户指定，非匀速/原生 smooth）：每帧移动"剩余距离"的固定比例，
 * 速度 ∝ 剩余距离 —— 离底越远走得越快、越近越慢（指数式 ease-out）。
 *
 * 覆盖面：HomeView（web）+ MobileHomeView（移动）各调一次即可；房间（RoomClient）
 * 经 AdaptiveAppShell 复用这两个视图，故 web/移动/协作三端一致覆盖，无需单独写
 * 房间代码。两视图都是 min-h-screen（window 滚动），统一滚 window。
 */

import { useEffect, useRef } from "react";

import { useChatStore } from "../store";

let activeRaf = 0;

function easeScrollWindowToBottom(): void {
  if (typeof window === "undefined") return;
  cancelAnimationFrame(activeRaf);
  let frames = 0;
  const step = () => {
    frames += 1;
    // 每帧重算目标：预约成功态 DOM 渲染进来后 scrollHeight 会增长，跟着追到真底部。
    const target = Math.max(
      0,
      document.documentElement.scrollHeight - window.innerHeight,
    );
    const current = window.scrollY;
    const delta = target - current;
    // 到底或超时（~2s，防 scrollHeight 因动画持续增长导致不收敛）→ 收尾。
    if (delta <= 1 || frames > 120) {
      window.scrollTo(0, target);
      return;
    }
    // 走剩余距离的 18%，且至少 1px 保证收敛（末端 delta*0.18 < 1 时不至于卡住）。
    window.scrollTo(0, current + Math.max(1, delta * 0.18));
    activeRaf = requestAnimationFrame(step);
  };
  activeRaf = requestAnimationFrame(step);
}

export function useScrollToBottomOnBooking(): void {
  const ordersLen = useChatStore((s) => s.itinerary?.orders?.length ?? 0);
  const prevRef = useRef(ordersLen);

  useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = ordersLen;
    // 只在 0 → 有 这一次跃迁时滚（重载已含订单不滚、取消再订也只在再订时滚）。
    if (prev === 0 && ordersLen > 0) {
      // 下一帧起动，给"预约成功"态一帧时间进 DOM，scrollHeight 到位再追。
      requestAnimationFrame(() => easeScrollWindowToBottom());
    }
  }, [ordersLen]);
}
