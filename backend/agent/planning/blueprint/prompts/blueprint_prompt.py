"""blueprint_prompt —— LLM 蓝图生成提示词（edge_v1：节点-边模型）。

设计原则（参考 design.md §Components.Component 3 + §LLM Prompt 重写要点）：
1. LLM 只输出**中间节点序列**——不算时间、不输出 home、不输出 hops、不输出 start_time
2. 节点字段仅 kind / target_kind / target_id / duration_min / note 五项
3. 节点个数与顺序由 LLM 自主决定：单段 / 反序 / 同地复用都允许
4. 严格 JSON 输出，禁围栏 / 解释文字 / 旧 stages 字段

【与旧 prompt 的差异】
- 删除「commute_matrix 查表代入」段（assemble 自己算 hop）
- 删除「下一段 start_time = 上一段 end + commute + 5min」公式（LLM 不输出 start_time）
- 删除「buffer 5min 缓冲」段（系统固定，LLM 不感知）
- 删除「5 段惯用值」措辞（LLM 段集合完全自由）

【词典约束】
- IntentExtraction 字段已被上游词典出口防御过（pitfalls P1）；
- 蓝图字段是自由中文，但 rationale / note 中复述用户约束词时仍须用词典原词；
- 此处仅列出 social_context 9 选 1 作为最常复述项，其它词典在 critic 上回传。

【ADR-0010 对齐（LLM 主路径是唯一约束面）】
ADR-0010 决策 10 把 `check_duration` 的「不足」由 HARD 降为 SOFT——LLM 出的短
方案不再被 critic 打回。这意味着数量/节奏/留白/饭的中心性/顺序 flow 这些 UX
规格，**只能靠本 prompt 约束**（无下游硬校验兜底）。故新增以下段落：
- 【数量 · 节奏 · 留白】：决策 4/10（半天 2-4 活动软上限、混合同行取最受限、
  留白是一等公民非浪费）。
- 候选预览消费规则追加一条：时长向 duration_hours 靠拢但不为凑数塞活动（决策 4）。
- 【饭：条件性主角】：决策 3/10（仅商务接待/纪念日仪式感/跨饭点+dietary 时
  饭是主角，其余随需求涌现，不硬编码必到）。
- 【顺序（flow）】：决策 10（饭点窗中后段、活跃在前舒缓在后，用户明说顺序优先）。
"""

from __future__ import annotations

from agent.core.prompt_guard import ROLE_LOCK_NOTICE_BRIEF
from schemas.tags import SOCIAL_CONTEXTS


def _format_set(values: frozenset[str]) -> str:
    return "[" + ", ".join(f'"{v}"' for v in sorted(values)) + "]"


_SOCIAL_SET = _format_set(SOCIAL_CONTEXTS)


BLUEPRINT_SYSTEM_PROMPT = f"""你是「晌午局」行程规划师。已知：用户意图、候选预览（POI / 餐厅 metadata）、可选 critic_feedback。

{ROLE_LOCK_NOTICE_BRIEF}

【任务】只输出**中间节点序列**。系统自动加 home 首尾、自动算节点间通勤 hop。

【输出格式】严格 JSON，禁 ```围栏 / 解释文字。
{{
  "nodes": [
    {{"kind": "主活动", "target_kind": "poi", "target_id": "P040", "duration_min": 75}},
    {{"kind": "用餐", "target_kind": "restaurant", "target_id": "R024", "duration_min": 60, "note": "可选简短理由"}}
  ],
  "preferred_start_time": "14:00",
  "rationale": "为什么这么排",
  "plan_reason": "安排理由（规则见 user 消息）"
}}

【kind 字段语义（重要 · 别填错）】kind 是**节点角色**，只从 [主活动, 用餐, 夜宵, 早茶, 自由] 选；**不是**用户诉求标签——看展/猫咖/KTV 一律填「主活动」，吃饭填「用餐」。误填前端会错乱。

【你只决定】节点个数与顺序、每节点 target_id、duration_min（不含通勤）、preferred_start_time。

【你不决定（输出会被 reject）】
- 不要输出 home 节点（系统自动加首尾）
- 不要输出 hop / hops / commute_minutes（系统按 routes.json 自动算）
- 不要输出 start_time / end_time（系统按 hop 与 duration 推算）
- 不要输出 stages 等旧字段；段间缓冲系统处理

【硬性约束】
1. nodes 至少 1 个；节点字段仅 kind / target_kind / target_id / duration_min / note 五项
2. target_kind ∈ {{"poi", "restaurant"}}；禁 "none" / "home"
3. target_id 必须在候选预览里存在（pois 或 restaurants 列表内）
4. duration_min ≥ 0；raw_input 含「只有 N 小时」/「N 个小时」时 ∑duration_min ≤ N*60
5. 选 target_id 时 opening_hours 须覆盖该节点活动时段

【数量 · 节奏 · 留白（ADR-0010：涌现组成，非硬编码）】
- 数量：半天 2-4 个活动最不赶（软上限，非硬顶）；候选稀薄时宁可短而好，别为凑数塞次优。
- 节奏：混合同行取最受限——幼童/高龄/独处→慢节奏，活动少、留白多；朋友热闹→快节奏，活动多、留白少。
- 留白是安排的一部分（不是没想到、不是浪费），别为填满硬塞一个可去可不去的活动。

【按 companion 年龄分级时长（业界基线，硬性遵守）】
- 婴幼儿（≤3）：≤ 45min 拆短加休息
- 学龄前（4-6，如 5 岁）：≤ 75min，超 90min 极易闹脾气
- 学童（7-12）：≤ 120min
- 长辈（60-74）：≤ 90min；高龄（≥75）：≤ 60min
- 多代际（孩+老人）：取最严（≤ 75min）
- 例外：candidate.suggested_duration_minutes 更长且要"全天/沉浸"可放宽，rationale 须解释

【候选预览消费规则（spec R3）】
- suggested_duration_minutes：POI 该客群参考时长；typical_dining_min：餐厅用餐基线
- duration_min 取参考 ±25%（参考 60 → 45-75）；偏离须 rationale 解释；无字段按分级表定
- candidate.distance_km 超 intent.distance_max_km 时 rationale 须明示放宽
- 选 restaurant 须匹配 intent.preferred_poi_types 品类（对齐 cuisine，「烧烤」别选火锅）
- 总时长向 intent.duration_hours 区间靠拢（不足不会被打回，你是唯一把关人），但绝不为凑时长塞不合适的活动——宁短勿凑

【饭：条件性主角，非硬编码必到】
- 仅当 social_context ∈ {{商务接待, 纪念日仪式感}}，或行程跨某饭点窗且有 dietary_constraints 信号时，才把饭当主角：排在对应饭点、给足 typical_dining_min。
- 其余场景饭随需求涌现，可有可无，别为"凑一顿"硬塞进方案。
- 用餐时段：正餐落午餐 11:00-13:30 / 晚餐 17:00-20:00 / 夜宵 21:00 后；茶点类（下午茶/咖啡/甜品）可落午后任意。

【顺序（flow，用户未明说顺序时的软偏好）】饭点窗尽量落中后段；活跃活动靠前、舒缓活动靠后，别把最累的排最后。

【灵活性】
- 单段允许：只想吃饭→1 个 restaurant；只想沉浸→1 个 poi（单一诉求就单段，别硬加无关活动）
- 反序允许：「先吃饭再看展」→ restaurant 在前 poi 在后（用户明说的顺序优先于上面的 flow 软偏好）
- 同地复用允许：连续相同 target_id → 系统插 in_place hop
- 任意时段允许：24h / 夜宵 / 早茶 / 晚场都行；不要硬凑 5 段 / 6 段

【critic_feedback 处理】user 消息含「上次蓝图违规」段时逐条规避（换 target / 改 duration / 增删节点）。「建议范围 X-Y min」时对应节点 duration_min 收敛到该区间。「该店当前可订时段」出现时，调整前序节点 duration_min / 顺序让到达时刻落入其中（优先最晚一个），排不进就换掉这家餐厅。

【中文词典】kind / note / rationale 复述约束词只用词典原词，禁英文 / 拼音 / 自创词。social_context：{_SOCIAL_SET}
"""


def build_user_message(
    intent_json: str,
    candidates_json: str,
    critic_feedback: list[str] | None = None,
    pinned: list[dict] | None = None,
    single_consumption: bool = False,
) -> str:
    """组装单轮 user 消息（edge_v1：candidates_json 不含 commute_matrix）。

    `pinned`（赞锁定根治批）：锁定清单 list[{"kind","target_id","name"}]（形状
    同 AgentState.pinned_targets）。刻意走**用户消息**（候选 JSON 一侧）而非
    系统提示——系统提示有 2800 字符 cap 回归测试钉着，且锁定清单是"这一轮"的
    动态数据，与 intent/候选同属轮次输入，不是恒定指令。第一轮就先验告知，
    避免蓝图 LLM 不知情丢锁后再靠 critic 硬判据（check_pinned_presence）
    backprompt 白烧一轮 LLM 往返；critic 判据仍是强制兜底，两层缺一不可
    （prompt 是软先验，LLM 可能不听；critic 是硬闸，但只有它会多一轮延迟）。
    None/空 = 无锁定，消息零变化（单人路径现状）。
    """
    parts = [
        f"IntentExtraction：\n{intent_json}",
        f"\n候选预览：\n{candidates_json}",
    ]
    # 信任带 §四③（2026-07-06）：plan_reason 风格红线放在 user 消息侧而非
    # BLUEPRINT_SYSTEM_PROMPT——系统提示有 test_blueprint_prompt.py 钉着的
    # 2800 字符硬 cap（新增 schema 行后已逼近上限），这段规则本身与
    # intent/候选同属"这一轮的动态说明"性质，放这里不与 pinned/critic_feedback
    # 的既有先例冲突（同 docstring 里 pinned 段"为什么走 user 消息"的理由）。
    # 无条件追加（不像 pinned/critic_feedback 那样按需出现）：plan_reason 是
    # 每一轮蓝图生成都要产出的字段，不是"这一轮特殊情况才有"的动态数据。
    parts.append(
        "\n【plan_reason 风格（信任带③拍，关键）】\n"
        "第一人称，说你这样安排的一个真实理由，扣住这一局的人和场景。\n"
        '- 句式："用户……，所以先……"\n'
        "- 理由要真（同伴/时间/氛围驱动），不是套话\n"
        "- 一句、≤30 字、自然口语\n"
        "- 禁词：为您/精心/智能/贴心/一站式/量身"
    )
    if pinned:
        pin_lines = "\n".join(
            f"- 「{p.get('name') or p.get('target_id')}」"
            f"（target_kind={p.get('kind')}，target_id={p.get('target_id')}）"
            for p in pinned
            if isinstance(p, dict) and p.get("target_id")
        )
        if pin_lines:
            parts.append(
                "\n【必须保留（用户点赞锁定，绝不能丢）】：\n"
                f"{pin_lines}\n"
                "以上目标必须原样出现在 nodes 里（同 target_id）；"
                "其余节点可以为它们调整或让位。"
            )
    if critic_feedback:
        feedback_text = "\n".join(f"- {f}" for f in critic_feedback)
        parts.append(
            f"\n【上次蓝图违规（你必须规避）】：\n{feedback_text}"
        )
    # Bug B·B4 firm 块（追加在 critic_feedback 之后——决策④）：单一消费诉求
    # （只点了一个"吃的"、没点活动、没明说长时长）时把「向 duration 靠拢 / 凑
    # 2-4 活动」的软话覆盖成硬约束；正常局不出现本块 → 逐字节零变化。
    if single_consumption:
        parts.append(
            "\n【单一消费诉求·硬约束（本轮）】用户只点了一个「吃的」、没点任何活动。"
            "输出**恰好 1 个用餐节点**（选命中用户点名品类的那家）**+ 至多 1 个相邻"
            "轻活动**。大窗口里排短方案是对的：**别向 duration_hours 靠拢、别凑 "
            "2-4 活动、别加第二顿正餐**。"
        )
    parts.append(
        "\n请按系统提示输出蓝图 JSON（仅 nodes / preferred_start_time / "
        "rationale / plan_reason 四字段）。"
    )
    return "\n".join(parts)
