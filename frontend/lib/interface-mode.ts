/**
 * 界面模式判定的唯一真相源。
 *
 * 只负责把浏览器能力信号折算为 mobile / desktop；不负责
 * 读取 window、渲染 React 组件或跳转路由。保持纯函数，便于还原手机
 * 竖屏、横屏和电脑窄窗口等边界。
 */

export type InterfaceMode = "mobile" | "desktop";

/** 窄屏时使用移动界面；768px 与 Tailwind `md` 断点对齐。 */
export const COMPACT_VIEW_QUERY = "(max-width: 767px)";

/**
 * 触屏优先且没有 hover 的设备继续使用移动界面，即使手机
 * 横屏后宽度超过 767px，也不会突然切到桌面版。
 */
export const TOUCH_FIRST_QUERY = "(pointer: coarse) and (hover: none)";

export interface InterfaceSignals {
  compactViewport: boolean;
  touchFirst: boolean;
}

export function resolveInterfaceMode({
  compactViewport,
  touchFirst,
}: InterfaceSignals): InterfaceMode {
  return compactViewport || touchFirst ? "mobile" : "desktop";
}
