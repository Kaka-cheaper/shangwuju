/**
 * WebSocket 客户端：连接协作房间 + 自动重连 + 消息分发。
 *
 * 设计：
 * - 连接 {API_BASE 对应 ws/wss}/ws/{roomId}?user_id={userId}&nickname={nickname}
 * - 自动重连 3 次（指数退避 1s/2s/4s）
 * - 上行：send(JSON) → constraint / vote / confirm / ping
 * - 下行：onmessage → JSON 解析 → 回调分发
 */

import { buildWsUrl } from "./utils";

export interface WsOptions {
  roomId: string;
  userId: string;
  nickname: string;
  onMessage: (data: WsMessage) => void;
  onOpen?: () => void;
  onClose?: (reason: string) => void;
  onError?: (error: string) => void;
}

export type WsMessage = Record<string, unknown> & { type: string };

export interface WsClient {
  send: (msg: WsMessage) => void;
  close: () => void;
  isConnected: () => boolean;
}

const MAX_RETRIES = 3;
const BASE_DELAY_MS = 1000;

export function createWsClient(options: WsOptions): WsClient {
  const { roomId, userId, nickname, onMessage, onOpen, onClose, onError } = options;

  let ws: WebSocket | null = null;
  let retryCount = 0;
  let intentionalClose = false;
  let pingInterval: ReturnType<typeof setInterval> | null = null;

  function getWsUrl(): string {
    const params = new URLSearchParams({ user_id: userId, nickname });
    return buildWsUrl(`/ws/${roomId}`, params);
  }

  function connect() {
    const url = getWsUrl();
    ws = new WebSocket(url);

    ws.onopen = () => {
      retryCount = 0;
      onOpen?.();
      // 心跳：每 25s 发 ping 保活
      pingInterval = setInterval(() => {
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, 25000);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WsMessage;
        if (data.type === "pong") return; // 心跳回复，不分发
        onMessage(data);
      } catch {
        onError?.("消息解析失败");
      }
    };

    ws.onclose = (event) => {
      if (pingInterval) {
        clearInterval(pingInterval);
        pingInterval = null;
      }
      if (intentionalClose) {
        onClose?.("主动断开");
        return;
      }
      // 自动重连
      if (retryCount < MAX_RETRIES) {
        const delay = BASE_DELAY_MS * Math.pow(2, retryCount);
        retryCount++;
        setTimeout(connect, delay);
        onError?.(`连接断开，${delay / 1000}s 后重连（第 ${retryCount} 次）`);
      } else {
        onClose?.(`重连失败（已尝试 ${MAX_RETRIES} 次）`);
      }
    };

    ws.onerror = () => {
      onError?.("WebSocket 连接错误");
    };
  }

  connect();

  return {
    send: (msg: WsMessage) => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg));
      }
    },
    close: () => {
      intentionalClose = true;
      if (pingInterval) {
        clearInterval(pingInterval);
        pingInterval = null;
      }
      ws?.close();
    },
    isConnected: () => ws?.readyState === WebSocket.OPEN,
  };
}
