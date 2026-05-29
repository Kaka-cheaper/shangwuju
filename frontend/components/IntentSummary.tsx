"use client";

import { Icons } from "@/lib/icon-map";
import type { IntentExtraction } from "@/lib/types";

/** 意图解析结果摘要（黄昏深色主题：暖橙图标 + glass 进度槽）。 */
export default function IntentSummary({
  intent,
}: {
  intent: IntentExtraction;
}) {
  const tags: string[] = [
    ...intent.physical_constraints,
    ...intent.dietary_constraints,
    ...intent.experience_tags,
  ];
  const dur = `${intent.duration_hours[0]}-${intent.duration_hours[1]} 小时`;
  const companions = intent.companions
    .map((c) => `${c.role}${c.age ? `(${c.age}岁)` : ""}×${c.count}`)
    .join("、");

  const confidencePct = Math.round(intent.parse_confidence * 100);

  return (
    <div className="card px-3.5 py-3 animate-fade-in-up">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Icons.spark
            className="w-3.5 h-3.5 text-brand-600"
            strokeWidth={2.5}
          />
          <span className="text-xs font-medium text-ink-800 tracking-tight">
            意图解析
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-ink-500 mono">
            {confidencePct}%
          </span>
          <div className="w-16 h-1 rounded-full bg-black/[0.06] overflow-hidden">
            <div
              className="h-full transition-[width] duration-500"
              style={{
                width: `${confidencePct}%`,
                background:
                  "linear-gradient(90deg, #FFD100 0%, #f59e0b 100%)",
                boxShadow: "0 0 8px rgb(255 209 0 / 0.4)",
              }}
            />
          </div>
        </div>
      </div>
      <div className="space-y-1 text-xs text-ink-800">
        <Row label="时间">
          {intent.start_time} · {dur}
        </Row>
        <Row label="距离上限">
          <span className="mono text-ink-900">{intent.distance_max_km} km</span>
        </Row>
        {companions && <Row label="同行">{companions}</Row>}
        <Row label="社交">
          <span className="chip">{intent.social_context}</span>
        </Row>
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-1.5">
            {tags.map((t) => (
              <span key={t} className="chip">
                {t}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-xs text-ink-500 uppercase tracking-wider w-12 shrink-0">
        {label}
      </span>
      <span className="flex-1 min-w-0">{children}</span>
    </div>
  );
}

