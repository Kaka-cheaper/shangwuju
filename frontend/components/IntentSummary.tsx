"use client";

import type { IntentExtraction } from "@/lib/types";

/** 意图解析结果摘要：作为评委可见的「Agent 听懂了什么」。 */
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

  return (
    <div className="card px-3 py-2.5 border-brand-100 bg-brand-50/40 animate-fade-in-up">
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-xs font-medium text-brand-700">
          🎯 意图解析结果
        </div>
        <div className="text-[11px] text-ink-500">
          置信度 {(intent.parse_confidence * 100).toFixed(0)}%
        </div>
      </div>
      <div className="space-y-1 text-xs text-ink-700">
        <div>
          <span className="text-ink-500">时间：</span>
          {intent.start_time} · {dur}
        </div>
        <div>
          <span className="text-ink-500">距离上限：</span>
          {intent.distance_max_km} km
        </div>
        {companions && (
          <div>
            <span className="text-ink-500">同行：</span>
            {companions}
          </div>
        )}
        <div>
          <span className="text-ink-500">社交语境：</span>
          <span className="chip">{intent.social_context}</span>
        </div>
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-0.5">
            {tags.map((t) => (
              <span key={t} className="chip bg-white border border-ink-200">
                {t}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
