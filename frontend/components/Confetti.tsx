"use client";

/**
 * Confetti —— Tool 成功（订单创建）时的暖橙烟花。
 *
 * 设计意图：
 *   - 监听 itinerary.orders 从空 → 非空（用户「确认并预约」生效）
 *   - 用 fixed 全屏容器 + 纯 DOM 粒子，无外部依赖
 *   - 色板与黄昏胶片主题一致：brand-400 / sunset-600 / amber-400 / dusk-500
 *   - 1.6s 一次性飞散后自动卸载，不阻塞 streaming
 */

import { useEffect, useState } from "react";

import { useChatStore } from "@/lib/store";

interface Piece {
  id: string;
  x: number; // 起点 x（屏宽百分比）
  y: number; // 起点 y（屏高百分比）
  dx: number; // 飞行 x 偏移 px（带正负）
  dy: number; // 飞行 y 偏移 px（一般为负 = 向上）
  rot: number; // 旋转角度 deg
  color: string; // 色板
  shape: "rect" | "circle" | "line";
  delay: number; // ms
}

// 主题暖色板（不混入冷色避免破坏氛围）
const COLORS = [
  "#fb923c", // brand-400 暖橙
  "#f97316", // brand-500 橙
  "#ec4899", // sunset-600 莓粉
  "#fbbf24", // amber-400 暖金
  "#fde68a", // amber-200 浅金
  "#a78bfa", // dusk-400 浅紫（点缀，不超过 1/8）
  "#f472b6", // pink-400 粉
];

const SHAPES: Piece["shape"][] = ["rect", "rect", "rect", "circle", "line"];

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function makePieces(count: number, originX: number, originY: number): Piece[] {
  const pieces: Piece[] = [];
  for (let i = 0; i < count; i++) {
    // 360° 散射 + 向上偏置
    const angle = rand(0, Math.PI * 2);
    const radius = rand(160, 380);
    const upBias = rand(60, 200); // 向上额外偏置（重力反向）
    const dx = Math.cos(angle) * radius;
    const dy = Math.sin(angle) * radius - upBias;
    pieces.push({
      id: `p-${i}-${Date.now()}`,
      x: originX,
      y: originY,
      dx,
      dy,
      rot: rand(0, 720) * (Math.random() > 0.5 ? 1 : -1),
      color: COLORS[Math.floor(rand(0, COLORS.length))],
      shape: SHAPES[Math.floor(rand(0, SHAPES.length))],
      delay: rand(0, 240),
    });
  }
  return pieces;
}

export interface ConfettiOrigin {
  /** 起点 x（屏宽百分比）。 */
  ox: number;
  /** 起点 y（屏高百分比）。 */
  oy: number;
}

// 默认落点：桌面端两栏布局下行程卡大致在这个位置（右侧偏上）。
const DEFAULT_ORIGIN: ConfettiOrigin = { ox: 70, oy: 38 };

export default function Confetti({ origin = DEFAULT_ORIGIN }: { origin?: ConfettiOrigin }) {
  const itinerary = useChatStore((s) => s.itinerary);
  const [pieces, setPieces] = useState<Piece[]>([]);

  // 监听 itinerary.orders 从 0 → >0，触发烟花
  useEffect(() => {
    const orderCount = itinerary?.orders.length ?? 0;
    if (orderCount === 0) return;

    // B9：origin 可由调用方按自己的布局重定位——移动端是单栏居中布局，桌面端
    // 默认的「70%/38%（右侧偏上，桌面两栏布局下行程卡所在位置）」在手机上
    // 会飞到屏幕边缘外，MobileHomeView 传入居中偏上的坐标。
    const newPieces = makePieces(64, origin.ox, origin.oy);
    setPieces(newPieces);

    // 1.8s 后清空（动画 1.6s + 缓冲 200ms）
    const timer = setTimeout(() => setPieces([]), 1800);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itinerary?.orders.length]);

  if (pieces.length === 0) return null;

  return (
    <div className="confetti-stage" aria-hidden>
      {pieces.map((p) => (
        <span
          key={p.id}
          className={`confetti-piece shape-${p.shape}`}
          style={
            {
              left: `${p.x}%`,
              top: `${p.y}%`,
              backgroundColor: p.color,
              color: p.color, // 用于 box-shadow currentColor 发光
              animationDelay: `${p.delay}ms`,
              "--cf-dx": `${p.dx}px`,
              "--cf-dy": `${p.dy}px`,
              "--cf-rot": `${p.rot}deg`,
            } as React.CSSProperties
          }
        />
      ))}
    </div>
  );
}
