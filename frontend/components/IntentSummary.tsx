"use client";

import { Icons } from "@/lib/icon-map";
import type { IntentExtraction } from "@/lib/types";

const START_TIME_LABELS: Record<string, string> = {
  today_morning: "今天上午",
  today_noon: "今天中午",
  today_lunch: "今天中午",
  today_afternoon: "今天下午",
  today_evening: "今天晚上",
  tomorrow_morning: "明天上午",
  tomorrow_noon: "明天中午",
  tomorrow_lunch: "明天中午",
  tomorrow_afternoon: "明天下午",
  tomorrow_evening: "明天晚上",
  saturday_morning: "周六上午",
  saturday_noon: "周六中午",
  saturday_lunch: "周六中午",
  saturday_afternoon: "周六下午",
  saturday_evening: "周六晚上",
  sunday_morning: "周日上午",
  sunday_noon: "周日中午",
  sunday_lunch: "周日中午",
  sunday_afternoon: "周日下午",
  sunday_evening: "周日晚上",
  weekend_morning: "周末上午",
  weekend_noon: "周末中午",
  weekend_lunch: "周末中午",
  weekend_afternoon: "周末下午",
  weekend_evening: "周末晚上",
};

/** 意图解析结果摘要（黄昏深色主题：暖橙图标 + glass 进度槽）。 */
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
    <div className="card px-4 py-3.5 animate-fade-in-up">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-1.5">
          <Icons.spark
            className="w-4 h-4 text-brand-600"
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
          <div className="w-20 h-1.5 rounded-full bg-black/[0.06] overflow-hidden">
            <div
              className="h-full transition-[width] duration-500 rounded-full"
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
      <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm text-ink-800 lg:grid-cols-4">
        <SummaryField label="时间">
          {formatStartTime(intent.start_time)} · {dur}
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
          <span className="chip px-2 py-0.5 text-sm font-medium">
            {intent.social_context}
          </span>
        </SummaryField>
      </div>
      {tags.length > 0 && (
        <div className="mt-3 flex items-start gap-3 border-t border-black/[0.06] pt-2.5">
          <span className="w-12 shrink-0 text-sm font-semibold text-ink-700">
            标签
          </span>
          <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
            {tags.map((t) => (
              <span key={t} className="chip px-2 py-0.5 text-sm font-medium">
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function formatStartTime(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return "时间待定";
  return START_TIME_LABELS[normalized] ?? value;
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

