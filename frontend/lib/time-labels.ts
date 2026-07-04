const DAY_LABELS: Record<string, string> = {
  today: "今天",
  tonight: "今晚",
  tomorrow: "明天",
  monday: "周一",
  tuesday: "周二",
  wednesday: "周三",
  thursday: "周四",
  friday: "周五",
  saturday: "周六",
  sunday: "周日",
  weekday: "工作日",
  weekdays: "工作日",
  workday: "工作日",
  weekend: "周末",
};

const TIME_PART_PATTERNS: Array<[RegExp, string, string]> = [
  [/(^|_)early_morning(_|$)/, "early_morning", "清晨"],
  [/(^|_)late_morning(_|$)/, "late_morning", "上午晚些时候"],
  [/(^|_)late_afternoon(_|$)/, "late_afternoon", "傍晚"],
  [/(^|_)late_evening(_|$)/, "late_evening", "晚上晚些时候"],
  [/(^|_)late_night(_|$)/, "late_night", "深夜"],
  [/(^|_)midnight(_|$)/, "midnight", "深夜"],
  [/(^|_)morning(_|$)/, "morning", "上午"],
  [/(^|_)forenoon(_|$)/, "forenoon", "上午"],
  [/(^|_)noon(_|$)/, "noon", "中午"],
  [/(^|_)midday(_|$)/, "midday", "中午"],
  [/(^|_)lunch(_|$)/, "lunch", "中午"],
  [/(^|_)afternoon(_|$)/, "afternoon", "下午"],
  [/(^|_)dusk(_|$)/, "dusk", "傍晚"],
  [/(^|_)evening(_|$)/, "evening", "晚上"],
  [/(^|_)night(_|$)/, "night", "晚上"],
];

const RELATIVE_PREFIXES = /^(this_|next_|current_|coming_|on_|at_|the_)+/;

export function formatStartTimeLabel(value: string | null | undefined): string {
  const raw = (value ?? "").trim();
  if (!raw) return "时间待定";

  const hhmm = /^(\d{1,2}):(\d{2})$/.exec(raw);
  if (hhmm) {
    return `${hhmm[1].padStart(2, "0")}:${hhmm[2]}`;
  }

  const iso = /^(\d{4})-(\d{1,2})-(\d{1,2})[t\s](\d{1,2}):(\d{2})/i.exec(raw);
  if (iso) {
    return `${Number(iso[2])}月${Number(iso[3])}日 ${iso[4].padStart(2, "0")}:${iso[5]}`;
  }

  const normalized = raw
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(RELATIVE_PREFIXES, "");

  if (!normalized) return "时间待定";

  const tokens = normalized.split("_").filter(Boolean);
  const dayToken = tokens.find((token) => DAY_LABELS[token]);
  const timePart = findTimePart(normalized);

  if (dayToken === "tonight") {
    return timePart && !["evening", "night", "late_evening"].includes(timePart.key)
      ? `${DAY_LABELS[dayToken]}${timePart.label}`
      : DAY_LABELS[dayToken];
  }

  if (dayToken && timePart) {
    return `${DAY_LABELS[dayToken]}${timePart.label}`;
  }
  if (dayToken) {
    return DAY_LABELS[dayToken];
  }
  if (timePart) {
    return timePart.label;
  }

  return raw;
}

function findTimePart(normalized: string): { key: string; label: string } | null {
  for (const [pattern, key, label] of TIME_PART_PATTERNS) {
    if (pattern.test(normalized)) {
      return { key, label };
    }
  }
  return null;
}
