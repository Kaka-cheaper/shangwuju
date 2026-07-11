import { describe, expect, it } from "vitest";

import { resolveInterfaceMode } from "./interface-mode";

describe("resolveInterfaceMode", () => {
  it("窄屏进入移动界面", () => {
    expect(
      resolveInterfaceMode({ compactViewport: true, touchFirst: false }),
    ).toBe("mobile");
  });

  it("手机横屏仍按触屏优先能力进入移动界面", () => {
    expect(
      resolveInterfaceMode({ compactViewport: false, touchFirst: true }),
    ).toBe("mobile");
  });

  it("宽屏且鼠标优先时进入桌面界面", () => {
    expect(
      resolveInterfaceMode({ compactViewport: false, touchFirst: false }),
    ).toBe("desktop");
  });
});
