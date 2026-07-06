/**
 * 「确认后会发生什么」预告文案的纯派生逻辑——B8/spec interaction-experience
 * -review M4：不点确认也能让用户看到 Agent 一键执行会做的三件事。
 *
 * 抽出动机：原是 ItineraryCard.tsx 的私有实现（ConfirmPreviewCard 组件内联的
 * 文案拼装）。移动端 MobilePlanCard（B8）需要同一套判定（多顿饭不能只提第一
 * 家 / 人均桌位数 / 加购服务截断），抽成纯函数两端共用，避免各写一份措辞
 * 分叉——这段文案的分支条件本身就是容易悄悄漂移的地方（3 家及以上截断、
 * 有无 social_context 两种 memoryLine 文案）。
 */

import type { IntentExtraction, Itinerary } from "./types";

export interface ConfirmPreviewCopy {
  /** "Agent 会先到 xxx 锁定 14:00 的 3 人位" 这类句子（按餐厅数量分 1/2/3+ 三档）。 */
  restaurantLine: string;
  /** "；并加购xxx、xxx" 或空串。 */
  extraLine: string;
  /** "把这次「xxx」场景的偏好写进...让下次重启后还能想起来"。 */
  memoryLine: string;
  /** 加购服务列表（截断显示用，最多取第一项）。 */
  extraServices: string[];
}

export function buildConfirmPreviewCopy(
  intent: IntentExtraction | null,
  itinerary: Itinerary,
): ConfirmPreviewCopy {
  // 找全部用餐节点，用作"锁餐厅时段"预览——方案含多顿饭（如下午茶+晚饭）时
  // 不能只提第一家，否则第二顿会被误读成"漏排"。
  const restaurants = itinerary.nodes.filter(
    (n) => n.target_kind === "restaurant",
  );
  const partySize =
    intent?.companions?.reduce((acc, c) => acc + (c.count ?? 1), 0) ?? 0;
  const partySizeText = partySize > 0 ? `${partySize + 1} 人位` : "桌位";
  const socialCtx = intent?.social_context || "";
  const extraServices = (intent?.extra_services ?? [])
    .map((s) => s.trim())
    .filter(Boolean);
  const extraLine =
    extraServices.length > 0
      ? `；并加购${extraServices.slice(0, 3).join("、")}`
      : "";

  // 三件事的简短描述（按"动词 + 名词"模式，避免 + 堆叠）
  const restaurantLine = (() => {
    if (restaurants.length === 0) return "Agent 会按行程方案锁定预约";
    if (restaurants.length === 1) {
      return `Agent 会先到 ${restaurants[0].title} 锁定 ${restaurants[0].start_time} 的 ${partySizeText}`;
    }
    if (restaurants.length === 2) {
      return `Agent 会依次到 ${restaurants[0].title} 和 ${restaurants[1].title} 锁定各自时段的${partySizeText}`;
    }
    // 3 家及以上：只点名前两家，其余截断为"等 N 家"，避免句子被地点名堆满
    return `Agent 会依次到 ${restaurants[0].title}、${restaurants[1].title} 等 ${restaurants.length} 家锁定各自时段的${partySizeText}`;
  })();

  const memoryLine = socialCtx
    ? `把这次「${socialCtx}」场景的偏好写进 user_profile.json，让下次重启后还能想起来`
    : "把这次的偏好写进长期记忆，让下次重启后还能想起来";

  return { restaurantLine, extraLine, memoryLine, extraServices };
}
