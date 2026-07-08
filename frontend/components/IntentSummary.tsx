"use client";

import { Icons } from "@/lib/icon-map";
import { formatStartTimeLabel } from "@/lib/time-labels";
import type { IntentExtraction } from "@/lib/types";

/** 意图解析结果摘要（浅灰玻璃卡：克制承载解析信息，不和主按钮抢黄色焦点）。 */
export default function IntentSummary({
  intent,
}: {
  intent: IntentExtraction;
}) {
  const physicalConstraints = intent.physical_constraints || [];
  const dietaryConstraints = intent.dietary_constraints || [];
  const experienceTags = intent.experience_tags || [];
  const durationHours = intent.duration_hours || [0, 0];
  const companionList = intent.companions || [];
  const tags: string[] = [
    ...physicalConstraints,
    ...dietaryConstraints,
    ...experienceTags,
  ];
  const dur = `${durationHours[0]}-${durationHours[1]} 小时`;
  const companions = companionList
    .map((c) => `${c.role}${c.age ? `(${c.age}岁)` : ""}×${c.count}`)
    .join("、");

  const confidencePct = Math.round((intent.parse_confidence ?? 0.88) * 100);

  return (
    <div className="relative overflow-hidden rounded-[28px] border border-white/[0.72] bg-[linear-gradient(135deg,rgba(255,255,255,0.96)_0%,rgba(250,251,253,0.92)_35%,rgba(239,243,248,0.82)_100%)] px-4 py-3.5 shadow-[0_22px_54px_-42px_rgba(15,23,42,0.58),0_1px_2px_rgba(15,23,42,0.04),inset_0_1px_0_rgba(255,255,255,0.96)] ring-1 ring-black/[0.045] backdrop-blur-2xl backdrop-saturate-150 animate-fade-in-up">
      <div
        className="pointer-events-none absolute inset-x-5 top-0 h-px bg-gradient-to-r from-transparent via-white/95 to-transparent"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(780px_circle_at_18%_-18%,rgba(255,255,255,0.95),transparent_48%),radial-gradient(520px_circle_at_92%_18%,rgba(226,232,240,0.44),transparent_56%)]"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -right-14 -top-16 h-36 w-36 rounded-full bg-slate-300/30 blur-3xl"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -bottom-20 left-8 h-32 w-48 rounded-full bg-white/72 blur-3xl"
        aria-hidden
      />
      <div className="relative flex items-center justify-between mb-3">
        <div className="flex items-center gap-1.5">
          <Icons.spark
            className="w-4 h-4 text-ink-500"
            strokeWidth={2.5}
          />
          <span className="text-sm font-semibold text-ink-900 tracking-tight">
            意图解析
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-ink-600 mono">
            {confidencePct}%
          </span>
          <div className="w-20 h-1.5 rounded-full bg-black/[0.08] overflow-hidden">
            <div
              className="h-full transition-[width] duration-500 rounded-full"
              style={{
                width: `${confidencePct}%`,
                background:
                  "linear-gradient(90deg, #f59e0b 0%, #d97706 100%)",
                boxShadow: "0 0 8px rgb(245 158 11 / 0.4)",
              }}
            />
          </div>
        </div>
      </div>
      <div className="relative grid grid-cols-2 gap-x-6 gap-y-2 text-sm text-ink-800 lg:grid-cols-4">
        <SummaryField label="时间">
          {formatStartTimeLabel(intent.start_time)} · {dur}
        </SummaryField>
        <SummaryField label="距离">
          {intent.distance_max_km} km
        </SummaryField>
        {intent.budget_per_person != null && (
          <SummaryField label="预算">
            人均 ¥{intent.budget_per_person}
          </SummaryField>
        )}
        {companions && <SummaryField label="同行">{companions}</SummaryField>}
        <SummaryField label="社交">
          <span className="inline-flex rounded-full border border-black/[0.08] bg-white/70 px-2.5 py-0.5 text-sm font-semibold text-ink-700 shadow-[inset_0_1px_0_rgba(255,255,255,0.78)]">
            {intent.social_context}
          </span>
        </SummaryField>
      </div>
      {tags.length > 0 && (
        <div className="relative mt-3 flex items-start gap-3 border-t border-black/[0.07] pt-2.5">
          <span className="w-12 shrink-0 text-sm font-semibold text-ink-700">
            标签
          </span>
          <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
            {tags.map((t) => (
              <span
                key={t}
                className="inline-flex rounded-full border border-black/[0.08] bg-white/70 px-2.5 py-0.5 text-sm font-semibold text-ink-700 shadow-[inset_0_1px_0_rgba(255,255,255,0.78)]"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function SummaryField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-baseline gap-2.5">
      <span className="shrink-0 text-sm font-semibold text-ink-700">
        {label}
      </span>
      <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-800">
        {children}
      </span>
    </div>
  );
}

