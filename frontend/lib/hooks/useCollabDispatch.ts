"use client";

/**
 * useCollabDispatch —— collabMode 分流的用户输入派发（治漂移根源）。
 *
 * 抽出动机：协作房间里，任何一个人打字都必须走 WS `constraint` 广播（+ 本地
 * 乐观追加一条用户消息——自己发的不会经 WS `constraint_added` 广播回显，见
 * collab-store.ts 对应注释），非协作模式下才走单人 HTTP `sendMessage`。
 * 这段判断此前逐字重复在两处：
 *   - ChatDock.tsx:255-277（submit()）
 *   - ChitchatBubble.tsx:89-115（handleChipClick 的非 confirm 分支）
 * MobileComposer（A5）是第三份重复——三处独立维护，任何一处漏写 collabMode
 * 分流就是"房间里打字消息不广播、不进约束池"的静默 bug。抽成一个不依赖
 * 具体 UI 的 hook，三处调用方从根上不可能再漏。
 *
 * 行为契约（与抽出前的 ChatDock.submit / ChitchatBubble.handleChipClick 逐字
 * 等价）：
 *   - collabMode=true：sendConstraint(text.trim()) + 本地乐观追加 user 消息
 *   - collabMode=false：sendMessage(text)（内部自己 trim + 空值兜底）
 */

import { useCallback } from "react";

import { useCollabStore } from "../collab-store";
import { useChatStore } from "../store";

export interface UseCollabDispatchResult {
  /** 当前是否处于协作房间模式（调用方仍可能需要它做其它分支判断，如按钮可见性）。 */
  collabMode: boolean;
  /** 统一入口：非协作走 HTTP sendMessage，协作走 WS constraint + 本地乐观追加。 */
  sendUserInput: (text: string) => void;
}

export function useCollabDispatch(): UseCollabDispatchResult {
  const collabMode = useCollabStore((s) => s.collabMode);
  const sendConstraint = useCollabStore((s) => s.sendConstraint);
  const sendMessage = useChatStore((s) => s.sendMessage);

  const sendUserInput = useCallback(
    (text: string) => {
      if (collabMode) {
        const trimmed = text.trim();
        if (!trimmed) return;
        sendConstraint(trimmed);
        // 本地也追加一条用户消息到 messages（WS 广播会同步给其他人；自己这份
        // 不会经 constraint_added 回显，需要本地乐观追加——同 ChatDock 既有先例）。
        useChatStore.setState((s) => ({
          messages: [
            ...s.messages,
            {
              id: `u-${Date.now()}`,
              role: "user" as const,
              text: trimmed,
              createdAt: Date.now(),
            },
          ],
        }));
        return;
      }
      // sendMessage 内部自己 trim + 空值兜底，这里不需要重复处理
      void sendMessage(text);
    },
    [collabMode, sendConstraint, sendMessage],
  );

  return { collabMode, sendUserInput };
}
