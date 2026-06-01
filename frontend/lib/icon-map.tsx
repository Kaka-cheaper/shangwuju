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
  Clock,
  Coffee,
  Compass,
  Copy,
  Footprints,
  Heart,
  Leaf,
  Loader2,
  type LucideIcon,
  MapPin,
  Mic,
  Music,
  Quote,
  Salad,
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
  S1: Music,           // 学生党 KTV
  S2: UtensilsCrossed, // 兄弟撸串夜宵
  S3: Baby,            // 家庭主线
  S4: Users,           // 朋友 4 人
  S5: Heart,           // 情侣看展
  S6: Coffee,          // 闺蜜下午茶
  S7: Briefcase,       // 商务接待
  S8: Sun,             // 独处放空
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

/** 按 persona label 文字匹配图标（优先于 emoji 匹配） */
const PERSONA_LABEL_MAP: Array<[RegExp, LucideIcon]> = [
  [/爸爸|爸|父/, Baby],
  [/白领|商务|接待/, Briefcase],
  [/孝顺|父母|长辈|老人/, Users],
  [/独居|独处|一个人/, Leaf],
  [/情侣|恋人|女朋友|男朋友|约会/, Heart],
  [/闺蜜|朋友/, Coffee],
];

export function personaIconFromEmoji(emoji: string | undefined, label?: string): LucideIcon {
  // 优先按 label 文字匹配
  if (label) {
    for (const [re, icon] of PERSONA_LABEL_MAP) {
      if (re.test(label)) return icon;
    }
  }
  // 再按 emoji 匹配
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
  // 标签语义图标
  clock: Clock,
  baby: Baby,
  leaf: Leaf,
  salad: Salad,
  utensils: UtensilsCrossed,
  footprints: Footprints,
  heart: Heart,
  users: Users,
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
