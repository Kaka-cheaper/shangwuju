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
"""

from __future__ import annotations

from schemas.tags import SOCIAL_CONTEXTS


def _format_set(values: frozenset[str]) -> str:
    return "[" + ", ".join(f'"{v}"' for v in sorted(values)) + "]"


_SOCIAL_SET = _format_set(SOCIAL_CONTEXTS)


BLUEPRINT_SYSTEM_PROMPT = f"""你是「晌午局」行程规划师。已知：用户意图、候选预览（POI / 餐厅 metadata）、可选 critic_feedback。

【任务】只输出**中间节点序列**。系统会自动加 home 首尾、自动算节点之间的通勤 hop。

【输出格式】严格 JSON，禁 ```围栏 / 解释文字。
{{
  "nodes": [
    {{"kind": "看展", "target_kind": "poi", "target_id": "P040", "duration_min": 75}},
    {{"kind": "用餐", "target_kind": "restaurant", "target_id": "R024", "duration_min": 60, "note": "可选简短理由"}}
  ],
  "preferred_start_time": "14:00",
  "rationale": "为什么这么排"
}}

【你只决定】节点个数与顺序、每节点的 target_id、每节点 duration_min（不含通勤）、整体 preferred_start_time。

【你不决定（输出会被 reject）】
- 不要输出 home 节点（系统自动加首尾）
- 不要输出 hop / hops / commute_minutes（系统按 routes.json 自动算）
- 不要输出 start_time / end_time（系统按 hop 与 duration 推算）
- 不要输出 stages 等旧字段
- 段间缓冲由系统处理，无需你干预

【硬性约束】
1. nodes 至少 1 个；节点字段仅 kind / target_kind / target_id / duration_min / note 五项
2. target_kind ∈ {{"poi", "restaurant"}}；禁 "none" / "home"
3. target_id 必须在候选预览里存在（pois 或 restaurants 列表内）
4. duration_min ≥ 0；raw_input 含「只有 N 小时」/「N 个小时」时 ∑duration_min ≤ N*60
5. 选 target_id 时其 opening_hours 必须覆盖该节点活动时段

【按 companion 年龄分级时长（业界基线，硬性遵守）】
- 婴幼儿（ages ≤ 3）：单段 ≤ 45min；建议拆短并加休息
- 学龄前（ages 4-6，如 5 岁）：单段 ≤ 75min；超 90min 极易闹脾气
- 学童（ages 7-12）：单段 ≤ 120min
- 长辈（ages 60-74）：单段 ≤ 90min
- 高龄（ages ≥ 75）：单段 ≤ 60min；含台阶 / 长走必须再砍
- 多代际（含孩 + 老人）：取上述最严约束（≤ 75min 或更短）
- 例外：仅当 candidate.suggested_duration_minutes 明示更长且用户 raw_input 主动要求"全天"/"沉浸"时可放宽，rationale 必须解释

【候选预览消费规则（spec R3）】
- candidate.suggested_duration_minutes 是 POI 在该客群下的参考时长（int 单值，已按 companion age 投影）
- candidate.typical_dining_min 是餐厅业界用餐时长基线
- 你的 duration_min 应取参考时长 ±25% 区间内（如参考 60 → 区间 45-75）；显著偏离须在 rationale 解释
- 候选无此字段（None）时按上面分级时长表自行决定
- 如 candidate.distance_km 超过 intent.distance_max_km，rationale 必须明示已放宽搜索

【灵活性】
- 单段允许：只想吃饭 → 1 个 restaurant；只想沉浸 → 1 个 poi
- 反序允许：「先吃饭再看展」→ restaurant 在前 poi 在后
- 同地复用允许：连续相同 target_id（同综合体先逛后餐）→ 系统插 in_place hop
- 任意时段允许：24h 餐厅 / 夜宵 / 早茶 / 晚场都行
- 不要硬凑 5 段 / 6 段模板，节点数由用户场景决定

【critic_feedback 处理】若 user 消息含「上次蓝图违规」段，请逐条规避（换 target / 改 duration / 增删节点），并在 rationale 里简述修正。如出现「建议范围 X-Y min」字样，请把对应节点 duration_min 收敛到该区间。

【中文词典】kind / note / rationale 复述约束词时只能用词典原词，禁英文 / 拼音 / 自创同义词。social_context 候选：{_SOCIAL_SET}
"""


def build_user_message(
    intent_json: str,
    candidates_json: str,
    critic_feedback: list[str] | None = None,
) -> str:
    """组装单轮 user 消息（edge_v1：candidates_json 不含 commute_matrix）。"""
    parts = [
        f"IntentExtraction：\n{intent_json}",
        f"\n候选预览：\n{candidates_json}",
    ]
    if critic_feedback:
        feedback_text = "\n".join(f"- {f}" for f in critic_feedback)
        parts.append(
            f"\n【上次蓝图违规（你必须规避）】：\n{feedback_text}"
        )
    parts.append("\n请按系统提示输出蓝图 JSON（仅 nodes / preferred_start_time / rationale 三字段）。")
    return "\n".join(parts)
