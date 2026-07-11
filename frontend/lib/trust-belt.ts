/**
 * 信任带（AI 思考流）—— 编辑剪辑层：把散落的后端事件剪成一条七拍第一人称思考流。
 *
 * 唯一权威规格：`路演PPT/信任带设计终稿.md`（只读，不改）。本文件实现该文档
 * §二 七拍映射表 + §三 ④⑤ 分种落词 + §二/§五 剪辑规则与四种收尾判定，是
 * `components/TrustBelt.tsx`（Web + 移动端共用同一份组件）的纯数据层——不依赖
 * React，可独立单测（同 `lib/critic-timeline.ts` 的既有先例：数据判定与渲染分层）。
 *
 * 七拍：
 *   ① 理解   —— intent.understanding（LLM 现生成，新字段）
 *   ② 检索   —— 固定句，首次出现 search_pois/search_restaurants 工具调用时触发一次
 *   ③ 规划   —— blueprint.plan_reason（LLM 现生成，新字段，随 agent_thought 兄弟字段到达）
 *   ④ 发现问题 —— critic_violations（按违规码分种落词，§三）
 *   ⑤ 修正   —— critic_fix_attempt（同一违规码的修正句，§三）
 *   ⑥ 换引擎 —— plan_fallback（固定句）
 *   ⑦ 定稿   —— itinerary 就绪（固定句；give_up 策略时诚实改口，§五"失败保留"）
 *
 * 剪辑规则（§二"信息流要剪辑"+ 本文件的可执行落地）：
 * - 15 条思考压 3 拍：不直灌 store.thoughts 的自由文本，只认七拍里"有资格"的
 *   信号源（understanding / 首次 search 工具调用 / plan_reason / critic 时间线 /
 *   itinerary 就绪），普通 agent_thought 自由文本不生成额外拍。
 * - 3 次同违规合成"发现→压→换引擎"递进：同一违规码连续出现 ≥3 轮时，只保留
 *   首轮的④ + 最后一轮的⑤，中间重复对被吞掉（见 `collapseRepeatedHealRounds`）。
 * - 总量 ~7 拍上限：①②③⑦是固定锚点，④⑤⑥ 预算 = 7 − 已出现的锚点数 − (⑦占1)；
 *   超预算时保留**最新**的几拍（冻结态窗口天然停在⑤⑥⑦，呼应 §七"时态"要求）。
 *
 * 修订（2026-07-10）：②拍检索收据芯片——`buildSearchPreviewChips` 是本文件新增
 * 的第二个导出纯函数，从 store.toolCalls 里两个 fan-out 搜索 worker 的
 * tool_call_end.output.preview（后端 `_top_rated_preview` 已排好的评分 top-3）
 * 剪出芯片行数据。芯片**不是新的一拍**——它是②拍正文下挂的附件，七拍剪辑
 * 纪律不变；渲染判定（动效/降级）留给 `components/TrustBelt.tsx`，本文件只管
 * 数据剪辑（分组 + 去重 + 总数）。
 *
 * 止损三修（2026-07-11，真机截图实锤——琥珀行渲染出因果倒序+重复，见
 * 路演PPT/信任带设计终稿.md 同日修订"止损三修"节）：
 * 1. 裁剪单位从"拍"改成"轮"（`HealUnit`）——预算裁剪按轮整体保留/丢弃，不再
 *    从中间切断④⑤配对（见 `collapseRepeatedHealRounds` / 主入口裁剪循环）。
 * 2. ⑥换引擎固定句加措辞变体池（`FALLBACK_FIXED_TEXTS`）——连续换引擎不再
 *    一字不差重复，按出现次序取变体，最后一种封顶。
 * 3. `buildHealRoundsAndFallbacks` 在 fallback 处重置 `current`——fallback=
 *    引擎切换=轮边界，切换后到达的 fix_attempt 不再误挂回切换前的旧违规轮
 *    （曾用旧违规码的措辞、却顶着切换后的新 seq，排序后修正句跑到发现句
 *    前面）。
 */

import type { useChatStore } from "./store";
import { buildCriticTimeline, type CriticTimelineItem } from "./critic-timeline";

// ============================================================
// 类型
// ============================================================

export type TrustBeltBeatKind =
  | "understanding"
  | "search"
  | "planning"
  | "discover"
  | "fix"
  | "fallback"
  | "done";

export interface TrustBeltBeat {
  /** 稳定 key（同一拍跨重渲染保持同一 id，供 React key / 动效"是否新拍"判定）。 */
  id: string;
  kind: TrustBeltBeatKind;
  text: string;
  /** 排序/去重辅助（非展示字段）；①②③ 用负数占位保证排在真实 seq 之前。 */
  seq: number;
  /** ④⑤⑥ 琥珀重音（§七"自愈重音"）。 */
  amber: boolean;
}

type StoreState = ReturnType<typeof useChatStore.getState>;

/** ②拍检索收据芯片单条（2026-07-10 新增，见路演PPT/信任带设计终稿.md 同日修订）。
 * 字段与后端 `_top_rated_preview` 投影一一对应，不多不少。 */
export interface SearchPreviewChip {
  kind: "poi" | "restaurant";
  name: string;
  rating: number;
}

/**
 * 一条 fan-out 搜索 worker 的 tool_call 记录——②拍锚点判定只需要 `tool`（既有
 * 用法，见下方 SEARCH_TOOLS 判定），芯片提取额外需要 `arrivalIdx`（判断"最后
 * 一组"）+ `output.preview`（取候选）。后两个字段设为可选：②拍存在性判定与
 * 芯片提取是两件独立的事，只做②拍判定的调用方（如既有单测的 `{ tool }` 最小
 * 夹具）不该被强制补齐芯片专用字段——`buildSearchPreviewChips` 内部对缺失的
 * `arrivalIdx` 按 0 兜底（同一 tool 只有一条记录时不影响"取最后一条"的判定）。
 * 用结构化最小接口而非 import store 内部的 `ToolCallRecord`——同 `TrustBeltInput.
 * toolCalls` 既有的"只声明本文件需要的字段"手法。
 */
export interface TrustBeltToolCall {
  tool: string;
  arrivalIdx?: number;
  output?: Record<string, unknown> | null;
}

export interface TrustBeltInput {
  /** intent?.understanding ?? ""——①拍来源。 */
  understanding: string;
  /** store.toolCalls——②拍触发判定（search_pois/search_restaurants 命中一次即够）
   * + 检索收据芯片数据源（本文件 `buildSearchPreviewChips` 消费 arrivalIdx/output）。 */
  toolCalls: ReadonlyArray<TrustBeltToolCall>;
  /** store.thoughts——③拍来源（首个携带 planReason 的条目）+ ⑦拍质检收据
   * 来源（首个携带 checksRun 的条目，见 `buildChecksRunReceipt`）。 */
  thoughts: ReadonlyArray<{ seq: number; planReason?: string | null; checksRun?: number | null }>;
  /** store.criticReport——④⑤⑥拍来源，复用 `buildCriticTimeline` 既有判定。 */
  criticReport: StoreState["criticReport"];
  /** itinerary != null——⑦拍触发判定。 */
  itineraryReady: boolean;
  /** itinerary?.decision_trace?.final_strategy ?? null——⑦拍收尾文案分支。 */
  finalStrategy: string | null;
}

// ============================================================
// §三：④⑤ 分种落词（原文照抄设计文档，7 种：6 具名 + 1 兜底）
// ============================================================

interface HealWording {
  discover: string; // ④ 发现
  fix: string; // ⑤ 修正
}

const VIOLATION_WORDING: Record<string, HealWording> = {
  // 超时（已锁）
  duration_out_of_range: { discover: "方案超出时间限制", fix: "让我压缩一下时间" },
  // 超预算
  budget_exceeded: { discover: "花费超了预算", fix: "让我换些实惠的" },
  // 桌型/坐不下
  capacity_requirement_violated: {
    discover: "有的店坐不下这么多人",
    fix: "让我挑能容下的",
  },
  // 忌口冲突
  dietary_violation: { discover: "有一站不合忌口", fix: "让我避开这个" },
  // 太远
  distance_exceeded: { discover: "有的点离得太远", fix: "让我找近一点的" },
  // 营业时间
  opening_hours_violation: { discover: "有的店那个点没开门", fix: "让我调下时段" },
};

// 兜底（未列类型：invariant_broken / nodes_incomplete / timeline_inconsistent /
// hop_infeasible / restaurant_full_unresolved / physical_violation /
// social_context_mismatch / age_duration_mismatch / tool_response_inconsistency /
// meal_time_unreasonable / pinned_entity_missing，以及 violations=[] 的
// "这稿压根没生成出方案"场景）
const FALLBACK_WORDING: HealWording = {
  discover: "方案有个地方不太对",
  fix: "让我调整一下",
};

function wordingForCode(code: string | undefined): HealWording {
  if (code && VIOLATION_WORDING[code]) return VIOLATION_WORDING[code];
  return FALLBACK_WORDING;
}

// ============================================================
// ② 检索 / ⑥ 换引擎 / ⑦ 定稿：固定句（原文照抄）
// ============================================================

const SEARCH_FIXED_TEXT = "让我先查询附近的店铺和时间";
// 止损修 2（真机症状：连续⑥一字不差连发像系统抽风）——第二次及以后换引擎换一句
// 措辞变体，仍是固定句家族（忠实不编：这几句都只是"还是不行，再换一种办法"的
// 口语变体，不编造未发生的动作），保持第一人称思考腔、不带宪法§四禁词。
const FALLBACK_FIXED_TEXTS = [
  "还是不行，换成算法引擎",
  "算法引擎也卡住了，再压一轮",
  "再试一版，还是得靠算法硬排",
];
const DONE_FIXED_TEXT = "规划成功";
// §五"失败保留"：诚实分档，不硬编高潮
const GIVE_UP_FIXED_TEXT = "试了几版都排不下，先保留这版方案";

const SEARCH_TOOLS = new Set(["search_pois", "search_restaurants"]);

// ============================================================
// ②拍检索收据芯片（2026-07-10 新增，见路演PPT/信任带设计终稿.md 同日修订）
// ============================================================

const CHIPS_PER_KIND = 3;

/** buildSearchPreviewChips 的返回形态：展示用芯片列表 + "+N" 徽章需要的余量。 */
export interface SearchPreviewResult {
  /** 已展示的芯片，餐厅组在前、POI 组在后，各自组内按后端已排好的评分降序。 */
  chips: SearchPreviewChip[];
  /** "+N" 徽章的 N；两类总召回数 − 已展示数；≤0 时前端不渲染徽章。 */
  overflowCount: number;
}

const EMPTY_PREVIEW_RESULT: SearchPreviewResult = { chips: [], overflowCount: 0 };

/**
 * 从 store.toolCalls 里两个 fan-out 搜索 worker 的 tool_call_end.output.preview
 * 剪出②拍芯片行数据。纯函数、不依赖 React（同本文件其余判定的分层纪律）。
 *
 * 剪辑规则（对应任务规格）：
 * - 数据源＝后端已经算好的评分 top-3（`_top_rated_preview`），本函数不重新排序，
 *   只做"分组 + 取最后一组 + 拼总数"。
 * - 同名工具可能因反馈重规划再次触发检索（同一轮内）——store.toolCalls 是
 *   PER-TURN 清空的，但同轮 refinement 仍可能追加新的 search 事件；取
 *   **最后一条**該 tool 的 tool_call_end（按 arrivalIdx 最大）为准，同
 *   `event-handlers.ts` "找最近一个匹配 tool 且未结束的记录"一贯的"最新覆盖"
 *   语义对齐。
 * - 总数（供 +N 徽章）＝该轮 poi/restaurant 两个 output.count 之和（不是
 *   preview.length 之和——count 是真实召回总量，preview 只是展示切片）。
 * - 召回为 0（两类 count 都是 0，或压根没有 search 事件）→ 返回空芯片 + 0，
 *   调用方据此让整行芯片不出现。
 */
export function buildSearchPreviewChips(
  toolCalls: ReadonlyArray<TrustBeltToolCall>,
): SearchPreviewResult {
  let latestPoi: TrustBeltToolCall | undefined;
  let latestRestaurant: TrustBeltToolCall | undefined;

  for (const call of toolCalls) {
    if (!call.output) continue;
    // 只认**带 preview 的**记录当收据（对抗审查修复，2026-07-10）：preview 只有
    // fan-out 搜索 worker 产出；ILS/rule 兜底重查也发同名 tool 的 tool_call_end，
    // 但 output 是完整 SearchPoisOutput（candidates，无 preview 无 count）——若按
    // "arrivalIdx 最大者赢"不加区分，⑥换引擎重查会让它覆盖 fan-out 收据 →
    // totalCount 归 0 → 芯片行在换引擎瞬间凭空塌掉（带高度跳变 + 已真实发生过
    // 的检索收据蒸发）。Array.isArray 同时兜住线上数据形状（非数组不消费）。
    if (!Array.isArray(call.output.preview) || call.output.preview.length === 0) {
      continue;
    }
    const arrivalIdx = call.arrivalIdx ?? 0;
    if (call.tool === "search_pois") {
      if (!latestPoi || arrivalIdx >= (latestPoi.arrivalIdx ?? 0)) latestPoi = call;
    } else if (call.tool === "search_restaurants") {
      if (!latestRestaurant || arrivalIdx >= (latestRestaurant.arrivalIdx ?? 0)) {
        latestRestaurant = call;
      }
    }
  }

  const restaurantPreview = (latestRestaurant?.output?.preview as SearchPreviewChip[]) ?? [];
  const poiPreview = (latestPoi?.output?.preview as SearchPreviewChip[]) ?? [];
  const restaurantCount = (latestRestaurant?.output?.count as number) ?? 0;
  const poiCount = (latestPoi?.output?.count as number) ?? 0;

  const totalCount = restaurantCount + poiCount;
  if (totalCount <= 0) return EMPTY_PREVIEW_RESULT;

  // 餐厅在前、POI 在后（任务规格排列顺序），各组各自截 top-3；展示名剥尾部
  // 分店括号后缀（拍板 2026-07-10）——只改芯片显示，不动 store/后端数据。
  const chips = [
    ...restaurantPreview.slice(0, CHIPS_PER_KIND),
    ...poiPreview.slice(0, CHIPS_PER_KIND),
  ].map((c) => ({ ...c, name: chipDisplayName(String(c?.name ?? "")) }));
  const overflowCount = totalCount - chips.length;

  return { chips, overflowCount: Math.max(0, overflowCount) };
}

/**
 * ②拍芯片行尾"放宽重搜"提示（2026-07-11 新增，见路演PPT/信任带设计终稿.md
 * 同日修订「五收据」放宽重搜行）。数据源＝后端已经算好的 `relaxed_tags`（本次
 * 实际丢弃的 soft tag）+ `count`（丢完之后的真实候选数，见 `tools._helpers.
 * relax_tag_search` docstring「Returns」），本函数不重新判定放宽逻辑，只做
 * "取最后一组 + 挑第一个被丢的 tag 拼句子"。
 *
 * 只取**第一个**被丢的 tag（不是全部）：`relax_tag_search` 按出处降级序逐级丢，
 * 只列第一个已经足够让评委看懂"它确实放宽过、不是编的"，多个 tag 一起念会
 * 显得啰嗦（同 §三 违规落词"短句优先"的既有克制）。poi/restaurant 两条 fan-out
 * 记录若都发生了放宽，只展示其中一条——同一时间只有一个"检索"锚点（②拍固定句
 * 不分 poi/restaurant 两次），提示行也不该比它承载的锚点更啰嗦；取 poi 优先
 * （与 buildSearchPreviewChips「餐厅在前、POI 在后」的展示顺序无关——这里选
 * 谁优先不影响忠实性，只是两条都发生时的展示取舍，poi 与 restaurant 同等
 * 真实，选哪个都不算编造）。
 */
export interface RelaxedSearchNotice {
  tag: string;
  count: number;
}

/**
 * 从 store.toolCalls 剪出①拍芯片行尾的放宽提示。无放宽（relaxed_tags 为空或
 * 缺省）→ 返回 null，调用方据此不渲染这一行——不是每一局搜索都触发过放宽，
 * 没发生就不该编一句"放宽后…"出来。
 */
export function buildRelaxedSearchNotice(
  toolCalls: ReadonlyArray<TrustBeltToolCall>,
): RelaxedSearchNotice | null {
  let latestPoi: TrustBeltToolCall | undefined;
  let latestRestaurant: TrustBeltToolCall | undefined;

  for (const call of toolCalls) {
    if (!call.output) continue;
    // 同 buildSearchPreviewChips 的既有纪律：只认带 preview 的记录（fan-out
    // 搜索 worker 产出），ILS/rule 兜底重查的完整 SearchXxxOutput 没有
    // relaxed_tags 的"这一轮"语义（那是重查时的放宽，不是本轮①拍要展示的
    // 检索期放宽），不消费，避免⑥换引擎后这行凭空改写成旧数据的陈述。
    if (!Array.isArray(call.output.preview) || call.output.preview.length === 0) {
      continue;
    }
    const arrivalIdx = call.arrivalIdx ?? 0;
    if (call.tool === "search_pois") {
      if (!latestPoi || arrivalIdx >= (latestPoi.arrivalIdx ?? 0)) latestPoi = call;
    } else if (call.tool === "search_restaurants") {
      if (!latestRestaurant || arrivalIdx >= (latestRestaurant.arrivalIdx ?? 0)) {
        latestRestaurant = call;
      }
    }
  }

  for (const call of [latestPoi, latestRestaurant]) {
    const relaxedTags = call?.output?.relaxed_tags;
    if (Array.isArray(relaxedTags) && relaxedTags.length > 0) {
      const count = typeof call?.output?.count === "number" ? call.output.count : 0;
      return { tag: String(relaxedTags[0]), count };
    }
  }
  return null;
}

/** 尾部括号段（全角/半角），前置可有空白；只剥**尾部**，不动名中括号。 */
const TRAILING_PAREN = /\s*[（(][^（）()]*[）)]\s*$/;

/**
 * 芯片展示名：反复剥掉尾部括号段——mock 数据店名带「(凯德MALL店)」「（望京店）」
 * 这类分店后缀，7-8em 截断预算被括号吃掉一半、截出「绿茶餐厅(凯德…」的破相，
 * 信息量还为零（评委不关心分店）。剥空（整名都是括号）回退原名，不显示空芯片。
 */
function chipDisplayName(name: string): string {
  let out = name.trim();
  while (TRAILING_PAREN.test(out)) {
    out = out.replace(TRAILING_PAREN, "").trim();
  }
  return out || name.trim();
}

// ============================================================
// ①拍画像收据（2026-07-11 新增，见路演PPT/信任带设计终稿.md 同日修订）
// ============================================================

/** ①拍画像收据单条——字段与后端 `_consumed_profile_fields` 投影一一对应。 */
export interface ProfileFieldReceipt {
  field: string;
  label: string;
  tags: string[];
}

/**
 * 从 store.toolCalls 里 get_user_profile fan-out worker 的 tool_call_end.
 * output.profile_fields 剪出①拍画像收据数据。纯函数、不依赖 React（同本文件
 * 其余判定的分层纪律）。
 *
 * 剪辑规则：
 * - 数据源＝后端已经判定好的"真被 field_provenance 标为 prior 的字段"
 *   （`_consumed_profile_fields`），本函数不重新判定出处，只做"取最后一条
 *   get_user_profile 记录 + 透传"。
 * - 同 `buildSearchPreviewChips` 的"取最后一组"语义：同轮内 get_user_profile
 *   可能因反馈重规划再次触发（refiner 合并后的新 intent 可能改变哪些字段是
 *   prior），取 arrivalIdx 最大的一条为准。
 * - 无 profile_fields（未召回 / 这局压根没有字段被画像先验改写）→ 返回空
 *   数组，调用方据此让①拍不挂收据行——忠实不编、宁缺勿滥（任务规格原文），
 *   不能为了"看起来有内容"而展示一个没有真实消费证据的收据。
 */
export function buildProfileFieldsReceipt(
  toolCalls: ReadonlyArray<TrustBeltToolCall>,
): ProfileFieldReceipt[] {
  let latest: TrustBeltToolCall | undefined;
  for (const call of toolCalls) {
    if (call.tool !== "get_user_profile" || !call.output) continue;
    if (!Array.isArray(call.output.profile_fields) || call.output.profile_fields.length === 0) {
      continue;
    }
    const arrivalIdx = call.arrivalIdx ?? 0;
    if (!latest || arrivalIdx >= (latest.arrivalIdx ?? 0)) latest = call;
  }
  return (latest?.output?.profile_fields as ProfileFieldReceipt[]) ?? [];
}

// ============================================================
// ⑦拍质检收据（2026-07-11 新增，见路演PPT/信任带设计终稿.md 同日修订）
// ============================================================

/**
 * 从 store.thoughts 里剪出⑦拍质检收据——首个携带 `checksRun` 的条目（同③拍
 * `plan_reason` 的既有取法："首个携带该字段的条目"，见主入口③拍锚点判定）。
 * 数据源＝后端 `emit_critic` 从 `validate.REGISTRY` 现场数出来的真实检查数
 * （见 `AgentThoughtPayload.checks_run` 字段注释），本函数不做任何再计算。
 *
 * 缺省（undefined/null）→ 返回 null，调用方据此不渲染⑦拍质检收据行——只有
 * critic 真正跑到"通过"分支（has_critical=false）才会有这个字段，give_up /
 * 结构违规短路等路径没有一个稳定的"全部检查跑完"事实可以宣称，不该编。
 */
export function buildChecksRunReceipt(
  thoughts: ReadonlyArray<{ checksRun?: number | null }>,
): number | null {
  const entry = thoughts.find((t) => typeof t.checksRun === "number");
  return entry?.checksRun ?? null;
}

// ============================================================
// 总量上限（§二剪辑规则）
// ============================================================

const MAX_BEATS = 7;

// ============================================================
// ④⑤⑥：质检自愈时间线 → 剪辑规则
// ============================================================

interface HealRound {
  /** 该轮④的 seq（用于排序与"连续同码"判定）。 */
  seq: number;
  code: string | undefined;
  discover: TrustBeltBeat;
  fix?: TrustBeltBeat;
}

/**
 * 止损修 1：裁剪的最小单位——"一轮"，不是"一拍"。一个 HealUnit 要么是一对
 * discover+fix（1-2 拍，谁被 critic 判定成配对就打包成配对；无 fix_attempt
 * 时只有 discover 一拍），要么是一个独立的 fallback（换引擎，天然单拍成轮，
 * 见任务规格"fallback 拍算独立轮"）。`buildTrustBeltBeats` 的预算裁剪按
 * `beats.length` 整体丢弃/保留 HealUnit，不会拆开 `beats` 内部——这就是
 * "④⑤成对保留或成对丢弃"的落地机制。
 */
interface HealUnit {
  /** 排序键：该轮第一拍的 seq。 */
  seq: number;
  beats: TrustBeltBeat[];
}

function buildHealRoundsAndFallbacks(criticReport: StoreState["criticReport"]): {
  rounds: HealRound[];
  fallbackBeats: TrustBeltBeat[];
} {
  const timeline: CriticTimelineItem[] = buildCriticTimeline(criticReport);
  const rounds: HealRound[] = [];
  const fallbackBeats: TrustBeltBeat[] = [];
  let current: HealRound | null = null;

  for (const item of timeline) {
    if (item.kind === "violations") {
      const code = item.data.violations[0]?.code;
      const wording = wordingForCode(code);
      current = {
        seq: item.data.seq,
        code,
        discover: {
          id: `discover-${item.data.seq}`,
          kind: "discover",
          text: wording.discover,
          seq: item.data.seq,
          amber: true,
        },
      };
      rounds.push(current);
    } else if (item.kind === "fix_attempt") {
      if (current) {
        const wording = wordingForCode(current.code);
        current.fix = {
          id: `fix-${item.data.seq}`,
          kind: "fix",
          text: wording.fix,
          seq: item.data.seq,
          amber: true,
        };
      }
    } else {
      // fallback（plan_fallback → ILS/rule 等）：§三注 + §二①"⑥ 换引擎"固定句，
      // 不区分 to_stage 细分（ILS/rule 对评委而言都是"换了个引擎接手"）。
      // 止损修 2：按 fallback 出现次序（非全局 seq）取措辞变体，连续换引擎不再
      // 一字不差重复；变体用完则停在最后一种（真机极少见 >3 次换引擎的场景，
      // 停在最后一种仍诚实，不比循环回第一句更奇怪）。
      const wordingIdx = Math.min(fallbackBeats.length, FALLBACK_FIXED_TEXTS.length - 1);
      fallbackBeats.push({
        id: `fallback-${item.data.seq}`,
        kind: "fallback",
        text: FALLBACK_FIXED_TEXTS[wordingIdx],
        seq: item.data.seq,
        amber: true,
      });
      // 止损修 3（根因）：fallback = 引擎切换 = 轮边界。旧引擎最后一轮的违规若
      // 还没等到 fix_attempt 就切换了引擎，那条 fix_attempt 不该再补给它——
      // 新引擎自己的 backprompt 循环会产生新的 violations/fix_attempt 对，若不
      // 重置 current，新引擎的 fix_attempt 会被错挂到旧引擎那条violation round
      // 上（措辞用旧违规码的，seq 却是新引擎产生的），排序后出现"发现句还没
      // 出现、修正句先跑到换引擎前面"的因果倒序（真机症状根因）。fallback 后
      // current=null，若新引擎确实还会再报同一条 violations，下一条 violations
      // 事件会重新开一轮、拿到自己的 discover+fix 配对，不会错挂历史。
      current = null;
    }
  }

  return { rounds, fallbackBeats };
}

/**
 * 3 次同违规合成"发现→压→换引擎"递进：同一违规码连续出现 ≥3 轮时，只保留
 * 首轮④ + 最后一轮⑤（丢弃中间重复对），避免评委看到同一句话反复刷屏；
 * 命中 1-2 轮的正常情况保持逐轮④⑤全展示。
 *
 * 止损修 1（配套改动）：返回值从"拍的扁平数组"改为"轮的数组"（每轮 1-2 拍，
 * discover 与其配对的 fix 打包在同一个 HealUnit 里）——扁平数组会让下游总量
 * 裁剪（`buildTrustBeltBeats` 的 budget slice）按拍硬切，可能把④⑤从中间切断
 * （宪法§一.1"高潮必须两拍"的红线）。保持"轮"为最小可裁剪单位，裁剪只能整轮
 * 丢弃，不能拆散一对。
 */
function collapseRepeatedHealRounds(rounds: HealRound[]): HealUnit[] {
  const out: HealUnit[] = [];
  let i = 0;
  while (i < rounds.length) {
    let j = i;
    while (j + 1 < rounds.length && rounds[j + 1].code === rounds[i].code) {
      j += 1;
    }
    const runLength = j - i + 1;
    if (runLength >= 3) {
      const first = rounds[i];
      const last = rounds[j];
      const fix: TrustBeltBeat = last.fix ?? {
        id: `${first.discover.id}-fix-collapsed`,
        kind: "fix",
        text: wordingForCode(first.code).fix,
        seq: last.seq,
        amber: true,
      };
      out.push({ seq: first.seq, beats: [first.discover, fix] });
    } else {
      for (let k = i; k <= j; k += 1) {
        const beats: TrustBeltBeat[] = [rounds[k].discover];
        if (rounds[k].fix) beats.push(rounds[k].fix as TrustBeltBeat);
        out.push({ seq: rounds[k].seq, beats });
      }
    }
    i = j + 1;
  }
  return out;
}

// ============================================================
// 主入口
// ============================================================

export function buildTrustBeltBeats(input: TrustBeltInput): TrustBeltBeat[] {
  const anchors: TrustBeltBeat[] = [];

  // ① 理解
  if (input.understanding) {
    anchors.push({
      id: "understanding",
      kind: "understanding",
      text: input.understanding,
      seq: -3,
      amber: false,
    });
  }

  // ② 检索（固定句；出现一次即够，不随重复调用重复出现）
  if (input.toolCalls.some((t) => SEARCH_TOOLS.has(t.tool))) {
    anchors.push({ id: "search", kind: "search", text: SEARCH_FIXED_TEXT, seq: -2, amber: false });
  }

  // ③ 规划（首个携带 plan_reason 的 thought 条目）
  const planReasonEntry = input.thoughts.find((t) => !!t.planReason);
  if (planReasonEntry?.planReason) {
    anchors.push({
      id: "planning",
      kind: "planning",
      text: planReasonEntry.planReason,
      seq: -1,
      amber: false,
    });
  }

  // ④⑤⑥
  const { rounds, fallbackBeats } = buildHealRoundsAndFallbacks(input.criticReport);
  // fallback 拍算独立轮（任务规格原文）：每个 fallback beat 自成一个 1 拍的
  // HealUnit，与 collapseRepeatedHealRounds 产出的 discover+fix 轮合并后按
  // 轮首 seq 排序——轮之间的相对顺序仍是"哪轮先发生哪轮先展示"，轮内部的
  // discover/fix 顺序则由构造时的推入顺序保证（discover 恒在 fix 之前）。
  const healUnits: HealUnit[] = [
    ...collapseRepeatedHealRounds(rounds),
    ...fallbackBeats.map((beat): HealUnit => ({ seq: beat.seq, beats: [beat] })),
  ].sort((a, b) => a.seq - b.seq);

  // 止损修 1（根因修复）：总量 ~7 拍上限的裁剪单位从"拍"改成"轮"——旧实现对
  // 拍的扁平数组做 `slice`，预算不够整除时会把一轮从中间切断（如只保留
  // [fix, discover, fallback]，丢了 fix 的配对 discover），产生"发现还没出现
  // 修正就先来"的因果倒序（真机症状根因）。现在按轮从后往前累加拍数，一轮的
  // beats 要么整体保留要么整体丢弃，`MAX_BEATS` 预算不足以放下某一轮时就丢
  // 整轮，不拆散配对——高潮"必须两拍"（宪法§一.1）在裁剪层也不会被破坏。
  const budget = Math.max(0, MAX_BEATS - anchors.length - (input.itineraryReady ? 1 : 0));
  const trimmedHealUnits: HealUnit[] = [];
  let usedBudget = 0;
  for (let i = healUnits.length - 1; i >= 0; i -= 1) {
    const unit = healUnits[i];
    if (usedBudget + unit.beats.length > budget) continue;
    trimmedHealUnits.unshift(unit);
    usedBudget += unit.beats.length;
  }
  const trimmedHealBeats = trimmedHealUnits.flatMap((u) => u.beats);

  const beats = [...anchors, ...trimmedHealBeats];

  // ⑦ 定稿（§五 四种收尾：give_up 诚实改口，其余固定"规划成功"）
  if (input.itineraryReady) {
    const isGiveUp = input.finalStrategy === "give_up";
    beats.push({
      id: "done",
      kind: "done",
      text: isGiveUp ? GIVE_UP_FIXED_TEXT : DONE_FIXED_TEXT,
      seq: Number.MAX_SAFE_INTEGER,
      amber: false,
    });
  }

  return beats;
}

// 修订（真机反馈后）：展示序号不再用固定角色号（原 BEAT_ORDINAL 的
// ①..⑦ 已废弃并移除）——①..⑦ 只是本文件 docstring 里的角色代号，不是
// 展示号。组件层（components/TrustBelt.tsx）改按 revealed 数组的实际
// index+1 现算展示序号，同一 kind 在不同局里可能显示不同数字（例如一次过
// 局里没有④⑤⑥，⑦就显示"4"而不是固定的"7"）。

// ============================================================
// 折叠脊柱（2026-07-11 新增，见路演PPT/信任带设计终稿.md 同日修订）
// ============================================================

/** 脊柱节点——每个原拍的图标缩影 + 关键数字（对象恒存：评委刚看过的东西
 * 换形态但没消失）。数字来源逐种严格限定为"本文件/组件已经算好的真实数据"
 * ——不臆造（同"忠实不编"教义）：没有可靠数字来源的拍种，`count` 为 null，
 * 组件只渲染图标，不editorial 编一个数字出来凑"看起来有内容"。 */
export interface TrustBeltSpineNode {
  id: string;
  kind: TrustBeltBeatKind;
  /** 关键数字；null = 这一拍没有可靠的真实数字来源，只显示图标。 */
  count: number | null;
  /** ④⑤⑥自愈拍——琥珀节保持琥珀（对象恒存，见任务规格「折叠脊柱」）。 */
  amber: boolean;
}

/** 折叠脊柱的数字来源上下文——只收"组件层已经算好、有真实数据支撑"的量，
 * 不在这里反向解析拍正文的字符串（脆弱且等于臆造）。 */
export interface SpineNumberContext {
  /** ②检索：本轮召回总数（芯片展示数 + 溢出数，均为真实召回，见
   * `buildSearchPreviewChips`）。 */
  searchTotalCount: number;
  /** ③规划：最终方案里的活动节点数（不含首尾 home）。 */
  midNodeCount: number;
  /** ⑦定稿：质检收据（`buildChecksRunReceipt`），无则 null。 */
  checksRun: number | null;
}

/**
 * 把 beats（原拍，含④⑤合并去重后的最终展示序列）投影成脊柱节点——折叠态用，
 * 每个原拍收窄成"图标 + 一个数字"。同一 kind 出现多次时（如④⑤各命中 2 轮）
 * 逐条投影，不合并——脊柱是"这条带发生过什么"的压缩视图，不是再剪辑一遍。
 */
export function buildSpineNodes(
  beats: ReadonlyArray<TrustBeltBeat>,
  ctx: SpineNumberContext,
): TrustBeltSpineNode[] {
  return beats.map((beat) => {
    let count: number | null = null;
    if (beat.kind === "search" && ctx.searchTotalCount > 0) {
      count = ctx.searchTotalCount;
    } else if (beat.kind === "planning" && ctx.midNodeCount > 0) {
      count = ctx.midNodeCount;
    } else if (beat.kind === "done" && ctx.checksRun != null) {
      count = ctx.checksRun;
    }
    return { id: beat.id, kind: beat.kind, count, amber: beat.amber };
  });
}
