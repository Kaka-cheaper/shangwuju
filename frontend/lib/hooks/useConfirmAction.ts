"use client";

/**
 * useConfirmAction —— 「确认并预约」的可执行判定 + 房主守卫 + collabMode 分流。
 *
 * 抽出动机：此前只在 ItineraryCard.tsx:220-221 实现过一次：
 *   const canConfirm = canAct && (!collabMode || myRole === "owner");
 *   const handleConfirm = collabMode ? sendCollabConfirm : confirm;
 * MobileActionRail（A6）的确认按钮完全没有这道房主守卫——`onClick={confirm}`
 * 无条件调用单人确认 action，协作房间里任何参与者都能绕过"仅房主可确认"、
 * 也绕开了 WS confirm 通道（对方看不到你确认了）。抽成 hook 后两端共用同一份
 * 判定逻辑，不可能再有一端漏掉守卫。
 *
 * extraGate：调用方可叠加自己的局部禁用态（如 ItineraryCard 的时间轴 stagger
 * 动画期间 !animating），不必把 streaming/cancelled/hasOrders/collabMode 的
 * 判定逻辑重新抄一遍——默认 true（无额外限制，多数调用方，如 MobileActionRail，
 * 没有这类局部动画态）。
 */

import { useCallback } from "react";

import { useCollabStore } from "../collab-store";
import { useChatStore } from "../store";

export interface UseConfirmActionResult {
  /** 是否允许点击确认（含 streaming/cancelled/hasOrders/房主守卫/extraGate）。 */
  canConfirm: boolean;
  /** 按 collabMode 分流的确认动作：协作房间走 WS sendConfirm（内部自带房主
   * 守卫），单人走 HTTP confirm。 */
  handleConfirm: () => void;
  /** 按钮文案：执行中 / 等待发起人确认（协作房间非房主）/ 确认并预约。 */
  confirmLabel: string;
  /** 当前是否正在执行（驱动 icon/spinner 切换）。 */
  streaming: boolean;
  /** 协作房间内且非房主——用于按钮 title/文案的守卫提示（避免调用方再单独
   * 读 collabMode + myRole 拼一遍同样的判断）。 */
  blockedByOwnerGuard: boolean;
}

export function useConfirmAction(extraGate: boolean = true): UseConfirmActionResult {
  const itinerary = useChatStore((s) => s.itinerary);
  const streaming = useChatStore((s) => s.streaming);
  const cancelled = useChatStore((s) => s.cancelled);
  const confirm = useChatStore((s) => s.confirm);
  const collabMode = useCollabStore((s) => s.collabMode);
  const myRole = useCollabStore((s) => s.myRole);
  const sendCollabConfirm = useCollabStore((s) => s.sendConfirm);

  const hasOrders = (itinerary?.orders?.length ?? 0) > 0;
  const canAct = !streaming && !hasOrders && !cancelled && extraGate;
  const blockedByOwnerGuard = collabMode && myRole !== "owner";
  const canConfirm = canAct && !blockedByOwnerGuard;

  const handleConfirm = useCallback(() => {
    if (collabMode) {
      sendCollabConfirm();
      return;
    }
    void confirm();
  }, [collabMode, sendCollabConfirm, confirm]);

  const confirmLabel = streaming
    ? "执行中"
    : blockedByOwnerGuard
      ? "等待发起人确认"
      : "确认并预约";

  return { canConfirm, handleConfirm, confirmLabel, streaming, blockedByOwnerGuard };
}
