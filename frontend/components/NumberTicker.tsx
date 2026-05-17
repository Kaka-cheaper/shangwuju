"use client";

/**
 * NumberTicker —— C 局部动效（Magic UI 派系）。
 *
 * 数字在变化时做 spring 风格的「滚动重算」效果，强调"Agent 真的改了"。
 * 仅用于：行程总时长 / 距离上限 等评委一眼会看到差异的字段。
 *
 * 工作原理：value 变化时把旧值滑出、新值滑入；用 cubic-bezier(0.34, 1.56, 0.64, 1)
 * 做轻微 overshoot 营造质感。
 */

import { useEffect, useRef, useState } from "react";

export interface NumberTickerProps {
  /** 当前数值（任意可格式化的 number） */
  value: number;
  /** 格式化函数（如 `(v) => v.toFixed(1)`），缺省 toString */
  format?: (v: number) => string;
  /** 自定义 className（应用到容器） */
  className?: string;
}

export default function NumberTicker({
  value,
  format = (v) => String(v),
  className = "",
}: NumberTickerProps) {
  const [displayValue, setDisplayValue] = useState(value);
  const [animKey, setAnimKey] = useState(0);
  const prevValue = useRef(value);

  useEffect(() => {
    if (prevValue.current !== value) {
      prevValue.current = value;
      setAnimKey((k) => k + 1);
      setDisplayValue(value);
    }
  }, [value]);

  return (
    <span className={`inline-flex tabular-nums ${className}`}>
      <span key={animKey} className="inline-block animate-tick-up">
        {format(displayValue)}
      </span>
    </span>
  );
}
