/**
 * 图标映射 —— B+D 范式去 emoji 化。
 *
 * 设计目标（来自问题 10 的研究）：
 * - 替换所有装饰性 emoji 为 Lucide monoline SVG
 * - 后端返回的 emoji 字段（scenarios.icon / personas.icon）通过本地映射转 Lucide
 * - 业务语义性 emoji（订单 ✓ / 警告 ⚡）也统一转图标，去除"塑料感"
 *
 * 不破坏后端契约：scenarios / personas 的 icon 字段保留 emoji，只在渲染时映射；
 * 后端不需要改。
 */

import {
  Activity,
  AlertTriangle,
  Baby,
  Briefcase,
  CalendarHeart,
  CheckCircle2,
  Coffee,
  Compass,
  Copy,
  Heart,
  Leaf,
  Loader2,
  type LucideIcon,
  MapPin,
  Mic,
  Quote,
  Sparkles,
  Sun,
  Trash2,
  User,
  UserCog,
  Users,
  UtensilsCrossed,
  Wand2,
  X,
  XCircle,
  type LucideProps,
} from "lucide-react";

// ============================================================
// Scenario icon 映射（后端 /scenarios 返 emoji，前端转 Lucide）
// ============================================================

const SCENARIO_ICONS: Record<string, LucideIcon> = {
  S1: Users, // 家庭主线
  S2: Compass, // 朋友 4 人
  S3: Heart, // 情侣看展
  S4: Leaf, // 带父母散步
  S5: Coffee, // 闺蜜下午茶
  S6: Briefcase, // 商务接待
  S7: Sparkles, // 独处放空
  S8: CalendarHeart, // 跨代际纪念日
};

/** 按 scenario id 拿 Lucide 图标组件，缺省回 MapPin。 */
export function scenarioIcon(id: string): LucideIcon {
  return SCENARIO_ICONS[id] ?? MapPin;
}

// ============================================================
// Persona / User 图标映射（后端 emoji → Lucide）
//
// 后端 personas 用的 emoji 我没法穷举（A 同学定义），所以提供一个
// 从 emoji 文本到图标的启发式映射；真不命中时返回通用 User 图标。
// ============================================================

const PERSONA_EMOJI_MAP: Array<[RegExp, LucideIcon]> = [
  [/👤/, User],
  [/👨‍👩‍👧|👨‍👩|👩‍👧|👶/, Baby],
  [/🧑|👤|🙂/, User],
  [/💼/, Briefcase],
  [/🌿|🍃/, Leaf],
  [/💕|❤|💗/, Heart],
  [/🍳|🍽|🥗/, UtensilsCrossed],
  [/🎓/, UserCog],
];

export function personaIconFromEmoji(emoji: string | undefined): LucideIcon {
  if (!emoji) return User;
  for (const [re, icon] of PERSONA_EMOJI_MAP) {
    if (re.test(emoji)) return icon;
  }
  return User;
}

// ============================================================
// 业务语义图标（直接 export 给组件用，不走映射）
// ============================================================

export const Icons = {
  // 状态
  thinking: Loader2,
  success: CheckCircle2,
  fail: XCircle,
  warn: AlertTriangle,
  spark: Sparkles,
  refine: Wand2,
  share: Quote,
  copy: Copy,
  trash: Trash2,
  close: X,
  voice: Mic,
  // 通用
  user: User,
  pin: MapPin,
  pulse: Activity,
  sun: Sun,
} as const;

export type IconKey = keyof typeof Icons;

// ============================================================
// SmartIcon：scenarios.icon (emoji) / persona.icon (emoji) 通用渲染器
// 优先按 emoji 启发式查 PERSONA_EMOJI_MAP，回 User 兜底
// ============================================================

export function SmartIcon({
  emoji,
  className,
  ...rest
}: { emoji: string | undefined } & LucideProps) {
  const Comp = personaIconFromEmoji(emoji);
  return <Comp className={className} {...rest} />;
}
