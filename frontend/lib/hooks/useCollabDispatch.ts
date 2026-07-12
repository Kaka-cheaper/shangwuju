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
 * 演示场景同源（2026-07-12）：`QuickScenarios`（web）与 `MobileScenarioRail`
 * （移动）的场景按钮此前直接调单人 `useChatStore.sendScenario` → 打单人 HTTP
 * `/chat/turn`，是**最后一个绕过协作分流的入口**——房间里点场景只更新点击者
 * 本地、不经 WS 广播，其他成员界面不同步（用户报告：只有反馈重规划才同步）。
 * 收进本 hook 的 `sendScenario`：协作模式把场景输入当"一句完整规划请求"发进
 * 房间（WS `constraint`），后端 `route_turn` 判为 planning → `_trigger_fresh_plan`
 * 全房全新规划广播（见 backend/collab/room.py add_constraint 义务分发表）；
 * scenario_id 是单人上下文种子（房间无单一归属，见 context/sources.py:120），
 * 协作路径不透传，本就正确。
 *
 * 行为契约：
 *   - sendUserInput(text)：collab → sendConstraint + 本地乐观追加；solo → sendMessage
 *   - sendScenario(input, id)：collab → 同 sendUserInput（发进房间广播）；
 *                              solo → 单人 sendScenario（开新 session + scenario_id）
 */

import { useCallback } from "react";

import { useCollabStore } from "../collab-store";
import { useChatStore } from "../store";

export interface UseCollabDispatchResult {
  /** 当前是否处于协作房间模式（调用方仍可能需要它做其它分支判断，如按钮可见性）。 */
  collabMode: boolean;
  /** 统一入口：非协作走 HTTP sendMessage，协作走 WS constraint + 本地乐观追加。 */
  sendUserInput: (text: string) => void;
  /** 演示场景点击的分流入口：协作走房间广播（全房全新规划），单人走 sendScenario。 */
  sendScenario: (input: string, scenarioId: string) => void;
}

export function useCollabDispatch(): UseCollabDispatchResult {
  const collabMode = useCollabStore((s) => s.collabMode);
  const sendConstraint = useCollabStore((s) => s.sendConstraint);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const sendScenario = useChatStore((s) => s.sendScenario);

  // 协作发送：WS constraint 广播 + 本地乐观追加 user 消息（自己发的不经
  // constraint_added 回显）。sendUserInput 与 sendScenario 的 collab 分支共用。
  const collabSend = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      sendConstraint(trimmed);
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
    },
    [sendConstraint],
  );

  const sendUserInput = useCallback(
    (text: string) => {
      if (collabMode) {
        collabSend(text);
        return;
      }
      // sendMessage 内部自己 trim + 空值兜底，这里不需要重复处理
      void sendMessage(text);
    },
    [collabMode, collabSend, sendMessage],
  );

  const dispatchScenario = useCallback(
    (input: string, scenarioId: string) => {
      if (collabMode) {
        // 房间共享一个方案：场景点击 = 把这条完整规划请求发进房间，触发全房
        // 全新规划广播（scenario_id 不透传，房间无单一归属）。
        collabSend(input);
        return;
      }
      void sendScenario(input, scenarioId);
    },
    [collabMode, collabSend, sendScenario],
  );

  return { collabMode, sendUserInput, sendScenario: dispatchScenario };
}
