"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useCollabDispatch } from "@/lib/hooks/useCollabDispatch";
import { scenarioIcon } from "@/lib/icon-map";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

type ScenarioCenter = {
  x: number;
  y: number;
};

/** 8 个快捷场景按钮：暖色玻璃底 + 鼠标邻近放大 + 跟随光效。 */
export default function QuickScenarios({
  enlarged = false,
}: {
  enlarged?: boolean;
}) {
  const scenarios = useChatStore((s) => s.scenarios);
  const streaming = useChatStore((s) => s.streaming);
  // collab 分流：房间里点场景走 WS 广播（全房同步），单人走 sendScenario。
  const { sendScenario } = useCollabDispatch();
  const dockRef = useRef<HTMLDivElement | null>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [pointer, setPointer] = useState<ScenarioCenter | null>(null);
  const [centers, setCenters] = useState<ScenarioCenter[]>([]);

  const updateCenters = useCallback(() => {
    const dock = dockRef.current;
    if (!dock) return;
    const dockRect = dock.getBoundingClientRect();
    setCenters(
      itemRefs.current.map((el) => {
        if (!el) return { x: 0, y: 0 };
        const rect = el.getBoundingClientRect();
        return {
          x: rect.left - dockRect.left + rect.width / 2,
          y: rect.top - dockRect.top + rect.height / 2,
        };
      }),
    );
  }, []);

  useEffect(() => {
    updateCenters();
    window.addEventListener("resize", updateCenters);
    return () => window.removeEventListener("resize", updateCenters);
  }, [enlarged, scenarios.length, updateCenters]);

  const handleMouseMove = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      const dock = dockRef.current;
      if (!dock) return;
      const rect = dock.getBoundingClientRect();
      if (centers.length !== scenarios.length) updateCenters();
      setPointer({
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
    },
    [centers.length, scenarios.length, updateCenters],
  );

  const scenarioScale = useCallback(
    (index: number) => {
      if (!pointer || streaming) return 1;
      const center = centers[index];
      if (!center) return 1;
      const distance = Math.hypot(pointer.x - center.x, pointer.y - center.y);
      const effectRadius = enlarged ? 230 : 170;
      if (distance > effectRadius) return 1;

      const theta = (distance / effectRadius) * Math.PI;
      const influence = (1 + Math.cos(theta)) / 2;
      const maxLift = enlarged ? 0.16 : 0.13;
      return 1 + influence * maxLift;
    },
    [centers, enlarged, pointer, streaming],
  );

  if (!scenarios.length) {
    return (
      <div className="card rounded-[30px] px-4 py-3 text-sm text-ink-500">
        正在拉取演示场景...
      </div>
    );
  }

  return (
    <div className="card overflow-visible rounded-[30px] px-4 py-3">
      <div className="mb-2.5 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className="text-lg font-black tracking-tight text-ink-900">
            演示场景
          </span>
        </div>
      </div>
      <div
        ref={dockRef}
        className={cn(
          "relative grid gap-2 overflow-visible rounded-[22px]",
          enlarged
            ? "grid-cols-2 sm:grid-cols-4 lg:grid-cols-4"
            : "grid-cols-2 sm:grid-cols-4 lg:grid-cols-8",
        )}
        onMouseEnter={updateCenters}
        onMouseLeave={() => setPointer(null)}
        onMouseMove={handleMouseMove}
      >
        <span
          aria-hidden
          className="pointer-events-none absolute -inset-8 z-0 rounded-[30px] blur-2xl transition-opacity duration-300"
          style={{
            opacity: pointer && !streaming ? 1 : 0,
            background: pointer
              ? `radial-gradient(circle at ${pointer.x}px ${pointer.y}px, rgba(245,158,11,0.26) 0%, rgba(245,158,11,0.12) 18%, rgba(255,255,255,0) 48%)`
              : "transparent",
          }}
        />
        {scenarios.map((s, index) => {
          const Icon = scenarioIcon(s.id);
          const scale = scenarioScale(index);
          const elevated = scale > 1.025;

          return (
            <button
              key={s.id}
              ref={(el) => {
                itemRefs.current[index] = el;
              }}
              disabled={streaming}
              onClick={() => sendScenario(s.input, s.id)}
              className={cn(
                "group relative z-10 flex flex-col items-center justify-center gap-2 overflow-hidden rounded-[24px]",
                "border-2 border-[#FFD100]/70 bg-white text-center",
                "transition-[background-color,border-color,box-shadow,color,transform] duration-200 ease-out",
                "hover:border-[#e6bc00] hover:shadow-sm",
                "active:scale-[0.98]",
                "disabled:cursor-not-allowed disabled:opacity-50",
                "disabled:hover:border-[#FFD100]/40 disabled:hover:bg-white disabled:hover:shadow-none",
                enlarged ? "min-h-[100px] px-4 py-5" : "min-h-[80px] px-3 py-4",
              )}
              style={{
                transform: `scale(${scale}) translateY(${elevated ? -4 * (scale - 1) : 0}px)`,
                zIndex: Math.round(scale * 100),
                boxShadow: elevated
                  ? `0 ${10 + (scale - 1) * 80}px ${24 + (scale - 1) * 120}px -22px rgba(17,24,39,0.55), 0 0 ${18 + (scale - 1) * 90}px -14px rgba(245,158,11,0.55)`
                  : undefined,
              }}
              title={s.input}
            >
              <span
                aria-hidden
                className="pointer-events-none absolute inset-0 rounded-[24px] opacity-0 transition-opacity duration-200 group-hover:opacity-100"
                style={{
                  background:
                    "linear-gradient(135deg, rgba(255,255,255,0.78) 0%, rgba(255,252,237,0.86) 48%, rgba(245,158,11,0.08) 100%)",
                }}
              />
              <span
                aria-hidden
                className="pointer-events-none absolute bottom-0 left-1/2 h-[5px] w-10 -translate-x-1/2 rounded-full bg-accent-500 opacity-0 shadow-[0_0_16px_rgba(245,158,11,0.55)] transition-all duration-200 group-hover:w-14 group-hover:opacity-100"
              />
              <Icon
                className={cn(
                  "relative text-ink-600 transition-all duration-200 group-hover:text-ink-800",
                  enlarged ? "h-7 w-7" : "h-6 w-6",
                )}
                strokeWidth={1.75}
              />
              <span
                className={cn(
                  "relative text-center font-semibold tracking-tight text-ink-700 group-hover:text-ink-900",
                  enlarged ? "text-lg" : "text-base",
                )}
              >
                {s.title}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
