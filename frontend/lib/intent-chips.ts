/**
 * Intent chips —— 「为你考虑了什么」可视化（方案 C）。
 * 把 intent 命中的 4-6 个关键约束做成小标签，让用户一眼看到 Agent 真在为你考虑。
 *
 * 抽出动机：原本是 ItineraryCard.tsx 的私有实现（NarrationBlock 的一部分），
 * 移动端 MobilePlanCard（B1：narration 暖心文案 + "为你考虑了" chips + 取舍
 * 说明）需要完全同一套判定逻辑——不是"看起来像"，是同一份代码两处渲染，
 * 避免后续任一端改判定规则时另一端悄悄漂移。
 */

import type { Icons } from "./icon-map";
import type { IntentExtraction } from "./types";

export interface ChipItem {
  icon: keyof typeof Icons;
  label: string;
}

export function buildIntentChips(intent: IntentExtraction): ChipItem[] {
  const chips: ChipItem[] = [];

  // 距离
  if (intent.distance_max_km != null) {
    chips.push({
      icon: "pin",
      label: `${intent.distance_max_km % 1 === 0 ? intent.distance_max_km : intent.distance_max_km.toFixed(1)} km 内`,
    });
  }

  // 同行人（家庭/朋友/独处推断）
  if (intent.companions && intent.companions.length > 0) {
    const totalCount = intent.companions.reduce(
      (sum, c) => sum + (c.count ?? 1),
      0,
    );
    const hasChild = intent.companions.some(
      (c) => c.age != null && c.age <= 12,
    );
    const hasElder = intent.companions.some(
      (c) => c.age != null && c.age >= 60,
    );
    let label: string;
    let icon: keyof typeof Icons;
    if (hasChild) {
      const child = intent.companions.find((c) => c.age != null && c.age <= 12);
      label = `带 ${child?.age ?? ""} 岁孩子`;
      icon = "baby";
    } else if (hasElder) {
      label = "陪长辈";
      icon = "heart";
    } else if (totalCount > 1) {
      label = `${totalCount} 人同行`;
      icon = "users";
    } else {
      label = intent.companions[0].role || "同行";
      icon = "user";
    }
    chips.push({ icon, label });
  } else if (intent.social_context && intent.social_context.includes("独处")) {
    chips.push({ icon: "sun", label: "独处时间" });
  }

  // 饮食偏好（按内容匹配图标）
  const dietary = (intent.dietary_constraints || []).slice(0, 2);
  for (const d of dietary) {
    const icon = matchDietaryIcon(d);
    chips.push({ icon, label: d });
  }

  // 物理约束（按内容匹配图标）
  const physical = (intent.physical_constraints || []).slice(0, 2);
  for (const p of physical) {
    const icon = matchPhysicalIcon(p);
    chips.push({ icon, label: p });
  }

  // 时长
  if (
    intent.duration_hours &&
    Array.isArray(intent.duration_hours) &&
    intent.duration_hours.length === 2
  ) {
    const [lo, hi] = intent.duration_hours;
    if (lo === hi) {
      chips.push({ icon: "clock", label: `${lo} 小时` });
    } else {
      chips.push({ icon: "clock", label: `${lo}-${hi} 小时` });
    }
  }

  return chips.slice(0, 6);
}

/** 饮食约束 → 图标匹配 */
function matchDietaryIcon(text: string): keyof typeof Icons {
  if (/低脂|减脂|少油/.test(text)) return "leaf";
  if (/健康|轻食|沙拉/.test(text)) return "salad";
  if (/素食|蔬菜/.test(text)) return "leaf";
  if (/清淡|少盐/.test(text)) return "leaf";
  if (/甜|糖/.test(text)) return "utensils";
  return "utensils";
}

/** 物理约束 → 图标匹配 */
function matchPhysicalIcon(text: string): keyof typeof Icons {
  if (/亲子|儿童|孩子|宝宝/.test(text)) return "baby";
  if (/无障碍|轮椅|台阶/.test(text)) return "footprints";
  if (/低强度|不累|轻松/.test(text)) return "sun";
  if (/室内|遮阳|空调/.test(text)) return "sun";
  if (/步行|走路/.test(text)) return "footprints";
  return "spark";
}
