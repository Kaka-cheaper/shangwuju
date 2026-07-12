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
 *   ⑥ 换引擎 —— plan_fallback，仅 `.to` 落在真实引擎边界（ils/rule）才生成
 *              （固定句；见下方"琥珀误分类修复"，llm_backprompt/give_up 不生成）
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
 * 3. `buildHealRoundsAndFallbacks` 在真实引擎切换处重置 `current`——引擎切换=
 *    轮边界，切换后到达的 fix_attempt 不再误挂回切换前的旧违规轮（曾用旧
 *    违规码的措辞、却顶着切换后的新 seq，排序后修正句跑到发现句前面）。
 *
 * 琥珀误分类修复（2026-07-11 同日修订，止损三修之后发现的第四处真 bug）：
 * 后端 `emit_replan_router` 对**每次** replan 决策都发一条 PLAN_FALLBACK——
 * 包括 `llm_backprompt`（LLM 自己重试重出蓝图，根本没换引擎）。旧实现不读
 * `PlanFallbackHop.to`，一律当⑥"换引擎"渲染+重置 discover/fix 配对，真机上
 * 连出三条⑥、中间零④⑤配对、最后一句因果倒序重复——像系统抽风。改为按
 * `.to` 分类（见 `REAL_ENGINE_SWITCH_STAGES` 常量 docstring 完整取值集穷举）：
 * 只有 `.to ∈ {"ils","rule"}` 才是真实引擎交接，生成⑥拍+重置轮边界；
 * `.to === "llm_backprompt"` 不生成拍、不重置（重试的故事交给④⑤配对+既有
 * ≥3 轮折叠讲）；`.to === "give_up"` 也不生成⑥拍（收尾交给⑦拍诚实分支）。
 *
 * ⑦拍引擎自证收据（本批新增，见下方 `buildEngineSelfCertificationReceipt`
 * 完整 docstring）：ILS/rule 兜底成功从不回 critic（`build.py::_route_after_
 * ils` 硬编码直通 finalize），换引擎成功收尾的局因此永远拿不到⑦拍质检收据
 * ——恰恰是最需要收尾证据的局却最没有证据可看。纯前端判定（复用本批的
 * `isRealEngineSwitchHop` 分类）：本局真发生过引擎切换 && 非 give_up && 无
 * 质检收据 → 显示"算法引擎按硬约束求解通过"，与质检收据互斥，同一收据槽位。
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
// §三：④⑤ 分种落词（原文照抄设计文档，8 种：7 具名 + 1 兜底）
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
  // 显式要吃饭但这版没排上（四条不变式批 I3·C5b，
  // 后端 check_explicit_dining_presence 的 explicit_dining_missing HARD 码）
  explicit_dining_missing: { discover: "你说了要吃饭，这版还没排上", fix: "让我补一顿进去" },
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
// 琥珀误分类修复（2026-07-11 真机实锤，见路演PPT/信任带设计终稿.md 同批修订）
// ============================================================

/**
 * 真引擎边界——只有 `.to` 落在这个集合，才可能是"换了个引擎接手"。取值集
 * 穷举自后端两条 PLAN_FALLBACK 发射路径（`_emit_handlers.py::
 * emit_replan_router` 的 `_STRATEGY_TO_LABEL` + `emit_ils_replan` 从
 * `ils_replan_node.fallback_chain` 增量推的 `to_stage`）：
 * - "llm_backprompt"：LLM 自己重试重出蓝图，引擎压根没换——旧实现把它也当⑥
 *   渲染，导致"换引擎"三连里混进了根本没发生的换引擎（真机症状根因之一）。
 *   这条不生成任何拍，重试的故事完全交给④⑤配对 + 已有的"连续同违规折叠"
 *   逻辑去讲（§二剪辑规则）。
 * - "ils" / "rule"：LLM 三次仍未过 critic → 切 ILS；ILS 也失败 → 回 rule
 *   planner——这两个才是真实的"引擎交接"，唯一配得上⑥拍的两种（还需下方
 *   `isRealEngineSwitchHop` 的 `from !== to` 这层过滤）。
 * - "give_up"：不是新引擎接手，是"全部试完，保留当前最佳方案"（见
 *   `ils_replan_node` docstring + `finalize_plan.py::_FINAL_STRATEGY_BY_LAST_HOP`）
 *   ——⑦拍的 give_up 分支（`GIVE_UP_FIXED_TEXT`）已经诚实讲了这个结局，
 *   ⑥拍再喊一遍"换成算法引擎"反而是编造了一次并未发生的引擎切换。这条
 *   同样不生成拍，把收尾交给⑦。
 * - "error"（schema 保留值，当前后端从未产出）：未识别值一律归入"不生成拍"
 *   的保守桶，不臆造。
 */
const REAL_ENGINE_SWITCH_STAGES = new Set(["ils", "rule"]);

/**
 * 真实引擎切换判定：`.to` 落在 `REAL_ENGINE_SWITCH_STAGES` 且 `from !== to`
 * （同阶段自环不算切换）。`ils_replan_node` 在 ILS 真给出可行方案时会补写
 * 一跳 `from_stage="ils", to_stage="ils"`（reason="ILS 算法给出可行方案，
 * 成功兜底，不再进一步降级"）——这跳的语义是"确认已经切换过去的引擎成功
 * 了"，不是"又切了一次"。若只按 `.to` 判断会让这跳也命中 "ils"，在真实的
 * llm→ils 切换之后再生成第二条⑥"还是不行，换成算法引擎"，语义上等于重复
 * 宣布了一次并未发生的第二次切换——同样是"忠实不编"要防的误分类，只是换了
 * 个触发路径。
 */
function isRealEngineSwitchHop(hop: { from: string; to: string }): boolean {
  return hop.from !== hop.to && REAL_ENGINE_SWITCH_STAGES.has(hop.to);
}

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
// ⑦拍引擎自证收据（本批新增，见路演PPT/信任带设计终稿.md 2026-07-11 修订
// "五收据体系"表格"质检"行——本收据是同一行的互斥兄弟，不是第六种收据）
// ============================================================

/**
 * 真 bug 背景：ILS/rule 兜底成功从不回 critic（`build.py::_route_after_ils`
 * 硬编码直通 finalize，防 ILS 死循环——见该函数 docstring），`checks_run` 只在
 * critic 真正跑到"通过"分支才产生（见 `buildChecksRunReceipt`），换引擎成功
 * 收尾的局因此永远拿不到质检收据——⑦拍偏偏是"换引擎"这条最需要收尾证据的
 * 局，却最没有收据可看，评委看不到"它换的这个引擎到底解没解成"的确认。
 *
 * 方案 a（纯前端判定，不动后端）：itineraryReady && 非 give_up 收尾 && 本局
 * 发生过至少一次真实引擎切换（复用 `isRealEngineSwitchHop`——与⑥拍"该不该
 * 生成换引擎拍"同一把尺子，不能⑥拍判定"没真的换引擎"却在这里判定"换了"）
 * && 没有质检收据（checksRun==null，即 critic 从未走到"通过"分支）→ 显示
 * "算法引擎按硬约束求解通过"。与质检收据互斥（`ChecksRunReceiptRow` 二选一，
 * 见 `components/TrustBelt.tsx` 调用处），两者共享⑦拍下同一个收据槽位。
 *
 * 为什么"发生过真实引擎切换"是必要条件，不能只看 checksRun==null：一次过
 * 局如果因为某种原因 checksRun 也是 null（如未来 critic 分支变化），不该
 * 无中生有一句"算法引擎按硬约束求解通过"——那局压根没有 ILS/rule 参与，
 * 这句话对它不成立（忠实不编）。give_up 收尾同理排除：`ils_replan_node` 两级
 * 都失败时"引擎"并没有"求解通过"，是"没通过、保留旧方案"，此时既不该显示
 * 质检收据也不该显示引擎自证收据（⑦拍自己的 give_up 诚实改口已经把这个
 * 结局讲清楚，不需要再叠一句聚焦"通过"的收据）。
 */
export function buildEngineSelfCertificationReceipt(input: {
  fallbackHops: ReadonlyArray<{ from: string; to: string }>;
  checksRun: number | null;
  itineraryReady: boolean;
  finalStrategy: string | null;
}): boolean {
  if (!input.itineraryReady) return false;
  if (input.finalStrategy === "give_up") return false;
  if (input.checksRun != null) return false;
  return input.fallbackHops.some((hop) => isRealEngineSwitchHop(hop));
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
      // fallback（plan_fallback）：先按 `.from`/`.to` 分类，不是每次 replan
      // 决策都配得上⑥"换引擎"这句话——琥珀误分类修复（见上方
      // `isRealEngineSwitchHop` docstring）：`llm_backprompt` 是 LLM 自己
      // 重试，`give_up` 是诚实收尾，`ils→ils` 自环是"确认已切换的引擎成功了"
      // ——都不生成⑥拍；只有真实引擎交接（llm→ils / ils→rule 这类 from!==to
      // 且 to∈{ils,rule}）才生成。
      if (isRealEngineSwitchHop(item.data)) {
        // 止损修 2：按 fallback 出现次序（非全局 seq）取措辞变体，连续换引擎
        // 不再一字不差重复；变体用完则停在最后一种（真机极少见 >3 次换引擎的
        // 场景，停在最后一种仍诚实，不比循环回第一句更奇怪）。注意计数只数
        // 真实引擎切换次数（fallbackBeats.length），llm_backprompt/give_up
        // 被上面的分类挡在外面，不会占用变体序号。
        const wordingIdx = Math.min(fallbackBeats.length, FALLBACK_FIXED_TEXTS.length - 1);
        fallbackBeats.push({
          id: `fallback-${item.data.seq}`,
          kind: "fallback",
          text: FALLBACK_FIXED_TEXTS[wordingIdx],
          seq: item.data.seq,
          amber: true,
        });
      }
      // 止损修 3（根因）：真实引擎切换 = 轮边界。旧引擎最后一轮的违规若还没
      // 等到 fix_attempt 就切换了引擎，那条 fix_attempt 不该再补给它——新引擎
      // 自己的 backprompt 循环会产生新的 violations/fix_attempt 对，若不重置
      // current，新引擎的 fix_attempt 会被错挂到旧引擎那条 violation round 上
      // （措辞用旧违规码的，seq 却是新引擎产生的），排序后出现"发现句还没
      // 出现、修正句先跑到换引擎前面"的因果倒序（真机症状根因）。
      // `llm_backprompt` 不重置 current：它就是"当前这一轮 LLM 还在自己改"，
      // 紧随其后到达的 fix_attempt 本来就该配对给这一轮的 discover——重置了
      // 反而会把这对真实的④⑤拆散，制造新的错挂。`give_up` 是终局（之后不会
      // 再有新的 violations/fix_attempt 到达），重置与否不影响展示，为语义
      // 一致仍归为"边界"重置。
      if (item.data.to !== "llm_backprompt") {
        current = null;
      }
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
    if (beat.kind === "understanding") {
      count = 0;
    } else if (beat.kind === "search") {
      count = ctx.searchTotalCount;
    } else if (beat.kind === "planning") {
      count = ctx.midNodeCount;
    } else if (beat.kind === "done" && ctx.checksRun != null) {
      count = ctx.checksRun;
    }
    return { id: beat.id, kind: beat.kind, count, amber: beat.amber };
  });
}
