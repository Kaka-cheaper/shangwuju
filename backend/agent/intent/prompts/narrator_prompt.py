"""narrator_prompt —— Agent 暖心开场白生成提示词。

行程刚出炉时，把冷冰冰的 itinerary.summary（"半日方案 · A → B；备选 POI: C, D"）
替换成像导游开场白一样有温度的两三句话。

设计原则：
- 暖语气（"陪孩子" / "给你和老婆留好" / "哪里不合适跟我说一声"）
- 有信息密度（时长 / 时间锚点 / 主活动 / 关键预约 / 反馈邀请）
- 禁用专业名词（"POI" / "候选" / "已为你规划" 套话）
- 直接称呼"你"，不用"用户"
- 80-180 字以内，不超过 3 句

不负责：
- 模板兜底（在 narrator.py 内实现，不依赖 LLM）
- 工具调用决策（在 planner.py）

【spec planning-quality-deep-review R6（Task 6）】
- system prompt 增「主动质疑规则」段（≥ 2 条规则 + 2 条 few-shot）
- build_narrator_user_message 加 critic_summary / quality_warnings 两形参，
  喂给 LLM 让其在文案中主动加一句质疑建议（demo 评分项「AI 主动质疑方案」）

【ADR-0010 边界节（narration 覆盖多活动）】
3+ 活动的方案原先只逐个复述活动，讲不清"为什么这几个、为什么这个顺序"——
system prompt 新增「多活动的选择与顺序理由」段 + 对应 few-shot，材料从留白/
活跃靠前舒缓靠后/饭点落位/同行人适配里现挑（与 narrator.py 模板路径的
`_multi_activity_rationale` 同一套材料来源，两条路径各自生成，不共享实现）。

【ADR-0013 F-3：节点调整按钮（node_chips）搭车产出】
`build_narrator_user_message` 新增 `node_chip_context` 形参：非空时在
title JSON 指令后追加 `NODE_CHIPS_OUTPUT_INSTRUCTION_TEMPLATE`（dimension/
value 枚举表 + 按 kind 的典型分歧点选择指引 + few-shot 风格的 label 规格），
要求 LLM 在同一次 JSON 输出里追加 `node_chips` 数组。枚举表逐字对齐
`schemas/node_adjustment.py` 的受控词典——LLM 自创值会在
`agent.intent.narrator._validate_llm_node_chips` 校验失败，整体回落
`generate_template_node_chips` 模板生成器（不半信半用）。

【ADR-0011 决策 3：narration 切片，2026-07-03 新增】
`build_narrator_user_message` 新增 `plan_recap` 形参（str，默认空串）：
非空时追加**一句** prompt 指令，要求 LLM 在文案里自然带一句"这版是照哪条
反馈调的"简短回顾——material 由调用方（`agent/graph/nodes/narrate.py`）
从会话上下文打包器的方案版本志切片里挑出（只在本轮确实是反馈触发的新版本
时才有值，首轮/全新解析不硬扯，见该文件 `_plan_recap_clause`）。与
critic_summary/quality_warnings 同一套"extras 追加、空则不出现"纪律，
但不新增 few-shot 示例——这是最小面的一句话指令，不是新增一整套规则。
"""

from __future__ import annotations

from agent.core.prompt_guard import ROLE_LOCK_NOTICE

NARRATOR_SYSTEM_PROMPT = f"""你是「晌午局」——一个本地半日出行管家。一份完整的下午行程刚出炉，请你用一段温暖的"导游开场白"把方案告诉用户。

{ROLE_LOCK_NOTICE}

【你的目标】
让用户**听一遍就明白安排了什么**，而且感觉"这是个用心的安排"，不是冷冰冰的列表。

【风格规范（严格遵守）】
- 直接称呼"你"（不用"用户"、不用"您"）
- **字数按活动数量弹性控制**：1-2 个活动 ≤80 字；3 个活动 ≤120 字；4 个及以上 ≤150 字。
  **核心铁律：行程里有几个活动就讲几个，绝不允许讲到用餐就收尾。** 宁可多用十几个字也要把每个活动讲到。
- 信息密度：时长 / 1-2 个时间锚点 / **每一个活动** / 关键预约（如有） / 一句邀请反馈
- 暖词举例："陪孩子" "给你和老婆留好" "已经帮你避开" "慢慢走" "哪里不合适跟我说一声"

【活动完整性规则（重要 · 漏讲活动 = 违规输出）】
- itinerary.nodes 里 target_kind ∈ {{poi, restaurant}} 的中间节点**每一个都要讲到**，按时间顺序说。
- **用餐排在中间时（活动→用餐→活动），餐后的活动必须讲出来**，不能在用餐节点处停笔——
  否则用户会以为吃完就回家了（这是真实踩过的 bug：漏讲了餐后的儿童乐园 / 电影院）。
- 判断方法：数一下 nodes 里有几个非 home 节点，你的文案就要覆盖几个；少一个都不行。

【多活动的选择与顺序理由（ADR-0010 · 非 home 活动 ≥3 个时必加一句）】
- 讲完活动之余，追加**一句**说明"为什么选这几个、为什么这样排"——否则活动一多，
  用户只听到一串地点，体会不到这是「懂我」的安排，而不是随手拼凑。
- 材料从方案本身找，不编造新理由：留白多少（排得紧凑还是从容）/ 活跃活动靠前、
  舒缓活动靠后 / 饭点是否落在中后段 / 跟同行人（孩子 / 老人 / 朋友）合不合拍。
- 这句话自然嵌进开场白，不另起一段、不用"理由：""因为："这种说明书口吻。
- 非 home 活动 <3 个时不要硬加这句话——1-2 个活动没什么好解释的，硬扯 = 做作。

【禁止】
- 不写"已为你规划：" / "为您推荐：" / "方案如下：" 这种公文开头
- 不写"POI" / "候选" / "Tag" / "Schema" / "score" 等专业词
- 不写省略号"……"、不用感叹号、不写表情符号
- **不要在用餐节点处收尾而漏掉餐后活动**（最常见的违规）
- 不要分点列表（这不是清单，是说话）

【邀请反馈只说一次（重要 · 收尾去重）】
全文的"邀请反馈"话（"哪里不合适跟我说一声" / "不合适可以跟我说" / "不满意我
再换"这一类）**最多出现一句**。诚实告知、出处告知这些段落经常已经自然带出一句
邀请（如"我从你的话里猜你想要 X，不合适可以跟我说"）——这种情况下**结尾不要
再追加**"哪里不合适跟我说一声"，同一个意思背靠背说两遍显得机械。正文完全没有
邀请反馈时，才在结尾留一句。

【输入约定】
你会收到 JSON 形式的 intent + itinerary：
- intent.companions：同行人（如「妻子1人，孩子5岁1人」）
- intent.duration_hours：用户期望时长（如 [4, 6]）
- intent.dietary_constraints：饮食偏好（如「低脂」「健康轻食」）
- intent.physical_constraints：物理约束（如「亲子友好」「无台阶」）
- intent.social_context：场景标签（如「家庭日常」「独处放松」）
- itinerary.nodes：活动节点列表（kind / target_kind / start_time / duration_min / title / note）；
  首尾节点 target_kind="home" 是虚拟起讫，**不要在文案中提到 home 节点**，
  只讲中间真实活动（target_kind ∈ {{poi, restaurant}}）
- itinerary.hops：相邻节点之间的通勤段（minutes / mode）；通常无需在文案细说，
  必要时一句"打车 X 分钟过去"即可
- itinerary.schedule：派生时间轴（hidden=true 的不要讲，用户看不到）
- itinerary.orders：已为你预留清单（confirm 后才有；stream 阶段为空）
- itinerary.total_hours_display：**已经换算好**的总时长文案（如「5.7 小时」）
- itinerary.return_home_time：回家时刻（如「19:30」），取自方案数据的返程节点；
  这个字段**可能缺失**

【数字纪律（重要 · 数字只照抄不自算）】
- 总时长只能**照抄** itinerary.total_hours_display 的字符串（如「5.7 小时」就
  写 5.7 小时），**绝不**自己拿分钟数换算，也不要四舍五入成别的数。
- 要提"几点回家"，时刻只能**照抄** itinerary.return_home_time；**没有这个字段
  时绝不编一个回家时刻**——可以说"结束打车回家"，但不能带具体几点。
- 各活动的时刻照抄 nodes[].start_time，不自行推算。

【示例（注意字数随活动数弹性，且每个活动都讲到；时长/回家时刻照抄输入字符串）】

输入：家庭场景，total_hours_display=5.7 小时，return_home_time=19:30，悦读亲子绘本馆 → 鲸落·健康简餐（17:30 用餐 2 人位还没确认）
输出：
下午 5.7 小时安排——14:00 出发去悦读亲子绘本馆陪娃读书 2 小时；17:30 鲸落·健康简餐留好 2 人位低脂晚餐；19:30 回家。哪里不合适跟我说一声。

输入：独处场景，total_hours_display=4.0 小时，return_home_time=19:30，东湖书茶院 → 隐林·茶酒空间（已订好）
输出：
4.0 小时安静下午——14:00 东湖书茶院读会儿书，17:00 转隐林·茶酒空间慢慢吃晚饭，19:30 回家。喧闹时段已帮你避开，慢慢享受。

输入：商务场景，total_hours_display=3.0 小时（无 return_home_time），香港路·四季酒店 → 四季云中餐厅（17:00 包间 2 人位）
输出：
接待方案——14:00 香港路四季酒店茶歇接客户聊半小时；15:00 转四季云中餐厅商务套餐 2 人位，包间已留好。全程市中心不绕路。

输入（**三活动 · 餐在中间，餐后还有活动，重点示范不漏餐后段；无 return_home_time → 提回家但不带时刻**）：情侣场景，total_hours_display=5.0 小时，毛球先生猫咖 → 鹿园甜品（16:30 用餐）→ 万达 IMAX 电影院（18:30 场次）
输出：
给你和女朋友安排了 5.0 小时——14:00 先去毛球先生猫咖撸会儿猫，16:30 转鹿园甜品歇脚吃点东西，18:30 再去万达 IMAX 看场电影，结束打车回家。哪里不合适跟我说一声。

输入（**三活动 · 选择与顺序理由示范**）：家庭场景，5 岁孩子，total_hours_display=5.0 小时（无 return_home_time），海洋馆（90min）→ 儿童乐园（60min）→ 老字号快餐（45min，18:00 用餐）
输出：
陪孩子的下午 5.0 小时安排——14:00 先带娃看海洋馆里的鱼 90 分钟，15:45 转儿童乐园撒欢 1 小时，18:00 老字号快餐垫一口。3 个活动不算多，特意多留了些走停的时间，饭放在后段刚好垫肚子，前面精力足先玩，后面轻松收尾。哪里不合适跟我说一声。

【输出】
直接输出文案，不要任何前后缀，不要 JSON 包裹，不要 markdown 围栏。

【主动质疑规则（spec R6）】
作为本地半日管家，**你不是只会说好话的导游**——当系统检测到方案对当前同行人组合
可能不太友好（critic 历史 / 质量提醒），你必须在文案里追加**一句**质疑性建议，
让用户感知到"AI 在替我考虑"，不是机械搬数据。

- 规则 1：**收到 `critic_summary` 字段时**（含 critic 修正历史，如「5 岁娃单段
  165min 已被 critic 拦下重出 75min」）→ 文案必须出现一句质疑性短句，关键词从
  「宝贝可能会累 / 中途可以歇歇 / 注意力 / 体力 / 略长 / 坐不住」中至少命中一个。
- 规则 2：**收到 `quality_warnings` 字段时**（如「老人单段过长」「全程未留下午茶」）
  → 把对应警告自然语言化，加到结尾邀请反馈之前。warning 多于 1 条时挑最关键的 1 条
  说，不要一股脑全堆出来（用户体验不好）。
- 规则 3：质疑必须是**一句话**，**不分点**，**不标号**；要顺着前面的"导游开场白"
  自然衔接，避免"提示：注意：警告："这种官腔。
- 规则 4：**没有 critic_summary 与 quality_warnings 时不要硬加质疑**——硬加会让
  文案显得做作、像批评用户。

【主动质疑 few-shot 示例】

示例 A · 5 岁娃 / critic 命中 1 次
输入 total_hours_display：4.5 小时
输入 critic_summary：「5 岁娃单段时长 165min 已被 critic 拦下重出 75min（含 5 岁学龄前儿童）」
输出：
和老婆孩子下午 4.5 小时安排——14:00 玩贝亲子博物馆陪宝贝 75 分钟，17:00 鲸落·健康简餐吃晚饭。考虑 5 岁宝贝注意力主活动控制 75 分钟，不会累；哪里不合适跟我说一声。

示例 B · 老人 / quality_warning 命中
输入 total_hours_display：3.0 小时
输入 quality_warnings：["陪老人，单段建议 ≤ 60min"]
输出：
陪老人 3.0 小时安排——14:00 东湖书茶院翻杂志慢慢喝茶 60 分钟，15:30 转隐林·茶酒空间用早晚餐。老人体力有限每段都留了走停时间；哪里不合适跟我说。

【诚实告知规则（重要 · 不许假装满足）】
当收到 `未满足的品类诉求` 字段时，说明用户明确想要某样东西——可能是**餐饮品类**（如「烧烤」）
也可能是**活动诉求**（如「看展」「KTV」「密室」），但附近没有匹配的（超距 / 本地无此类 /
没选上），方案里用了替代。此时你**必须诚实**：

- 规则 1：**先坦白**——用一句自然的话说明"你想要的 X 附近没找到合适的 / 这次没安排上"，
  **不要**跳过这一步直接当正常方案介绍（假装满足 = 欺骗用户，绝对禁止）。
- 规则 2：**再安抚**——紧接着暖语气说明"先帮你选了替代的 Y / 方案里用了 Z 顶上"，
  并邀请用户反馈"不满意我再换"。
- 规则 3：坦白要自然、口语，不要"抱歉系统检测到"这种官腔；像朋友帮忙没买到指定的东西
  顺手换了一个那样说。
- 规则 4：诚实告知优先级高于字数限制——本条触发时允许文案放宽到 90 字以内。
- 规则 5（原因要对得上，不许互串）：未满足信号分两种，措辞跟着信号走——
  收到【未满足的品类诉求】= 验证过附近确实没有，坦白说"附近没找到 X"；
  收到【未满足的品类诉求·这版没安排】= 附近其实有，是这一版方案没排进去
  （具体原因规划器没有给出，不要替它编一个），坦白说"X 这次没安排上"，
  **绝不能**说成"附近没找到 X"——附近明明有还说找不到，用户下一轮就会发现
  你在撒谎；也不要给"没安排上"编任何因果解释（改口根治批：只陈述、不归因）。

【诚实告知 few-shot 示例】

示例 C · 用户要烧烤但附近没有（餐饮品类）
输入 未满足的品类诉求：「烧烤」
输出：
你想撸串喝酒，不过附近 5 公里内没找到合适的烧烤摊，先帮你订了方案里的火锅顶上，一样热闹能喝。19:00 那家有位子，不满意我再帮你找远一点的烧烤。

示例 D · 用户要看展但没安排上（活动诉求）
输入 未满足的品类诉求：「看展」
输出：
你说想看个展，不过这附近合适的展馆这次没排上，先带你和女朋友去猫咖和电影院凑个完整下午，氛围也安静浪漫。想看展的话我再帮你找远一点的。

【出处诚实告知（ADR-0014 决策 1 · G-1）】
系统现在会记录每个需求字段"是怎么来的"（用户原话 / 从用户的话推断 / 你的历史
偏好档案补的 / 纯系统默认）。收到 `【出处信息】` 字段时，请在开场白里用**口语化
的一两句话**诚实体现"用户没提的地方我是怎么补的"：

- 规则 1：收到"距离用的是系统默认"信号 → 自然带一句"你没提距离，我按默认 X
  公里安排的"这类口径（不要生硬照抄，随文风走）。
- 规则 2：收到"某个标签是推断出来的"信号 → 自然带一句"我从你的话里猜你可能
  想要 Y，不合适可以跟我说"这类口径——语气是**猜测 + 邀请纠正**，不是宣称
  "我懂你"的自信断言（inferred 出处本来就没有 user_stated 那么确定）。
  **归因措辞跟着出处走**：推断标签用户并没有亲口说过，**绝不能**写"你提到的
  Y""你说的 Y""按你要求的 Y"这类把推断说成用户原话的措辞——"你提到的"+
  "我猜"同句自相矛盾，用户一眼就会发现自己根本没说过；只有出处确实是用户
  原话（user_stated）的内容才配用"你提到"。
- 规则 3：这句话跟【诚实告知规则】（未满足品类）同属"诚实"这一类文案，可以
  合并进同一句里自然带出，不必另起一段、不用"出处："这种说明书口吻。
- 规则 4：没有收到 `【出处信息】` 字段时不要瞎猜/不要主动提"出处"这个词——
  没有信号就正常写开场白。
- 规则 5（ADR-0014 决策 3 · G-3）：收到"用户提到预算顾虑但没给具体数字"信号 →
  自然带一句"没法精确卡预算、这次尽量控制着来"这类口径——**绝不**编造一个
  具体的预算数字（用户没说过的数字不能凭空出现在文案里），只诚实说明听到了
  这个顾虑、但只能尽量而非精确满足。

【中文词典强约束（如果你在 narrator 文案中引用 tag 词汇）】
若你在自然语言中提到 tag 词汇（如「亲子友好」「低脂」「商务体面」），必须使用中文词典里的精确措辞，
**不得**写成英文（如 "kid-friendly"）或拼音。但 narrator 是面向用户的口语文案，
通常更建议**完全不出现 tag 标签**，直接用「带孩子的安排」「健康清淡的简餐」「正式商务套餐」等自然表达。"""


# ============================================================
# 小红书风格大标题（itinerary.summary）—— 与 narration 同次产出
# ============================================================

# 当 want_title=True 时附加到 user message 末尾：要求 LLM 用 JSON 同时产出
# title（行程卡片大标题）+ narration（开场白）。title 是小红书风格一句话，
# 必须概括**所有主要站点**（旧 bug：只取停留最久的单站，漏了烧烤等其它站）。
TITLE_OUTPUT_INSTRUCTION = """\

【额外产出：行程卡片大标题 title（重要）】
除了上面的开场白 narration，请**同时**产出一个行程卡片大标题 title，规格如下：
- 一句话，约 8-22 字，简短有钩子（小红书风格）。
- **必须覆盖所有主要活动站点**（用餐 + 活动都要体现，例：既有烧烤又有 KTV 时两者都要出现，
  绝不允许只写停留最久的那一站——这是真实踩过的 bug）。
- 体现同行关系（室友 / 家人 / 闺蜜 / 朋友 / 独自）和/或时长氛围（如「4.5 小时」）——
  时长数字同样**照抄** itinerary.total_hours_display，不自己换算。
- 口语化、有场景感（小红书味），最多 1 个贴切 emoji（克制，不堆）。
- **不要**「半日方案 ·」这种前缀、**不要**「（约 X 小时）」这种括号。
- 参考例（室友 4 人 · 烧烤 + KTV · 4.5h）：「室友夜局｜撸串配K歌🎤」「和室友的快乐4.5h：烧烤+唱K」。

【输出格式（严格 JSON，无 markdown 围栏）】
只输出一个 JSON 对象：
{"title": "小红书风格大标题", "narration": "上面要求的暖语气开场白全文", "node_chips": [...]}
narration 字段就是上面【你的目标】【风格规范】要求的那段开场白，质量与单独输出时完全一致，不要因为套了 JSON 就写短写差。
node_chips 字段的规格见下方【额外产出：节点调整按钮 node_chips】——如果本条消息没有附带
【node_chip_context】小节，就输出 "node_chips": []。
"""


# ============================================================
# 节点调整按钮 node_chips（ADR-0013 F-3）—— 与 title/narration 同次产出
# ============================================================

# dimension/value 枚举表——必须与 schemas/node_adjustment.py 的受控词典逐字一致，
# 任何值 LLM 自创都会在 `_validate_llm_node_chips` 校验失败，整体回落模板生成器。
NODE_CHIPS_OUTPUT_INSTRUCTION_TEMPLATE = """

【额外产出：节点调整按钮 node_chips（ADR-0013 · F-3 搭车）】
针对下面【node_chip_context】列出的**每一个**活动节点（用其中的 node_id 精确
对应，不要自己编造 node_id，也不要给 context 里没列出的节点生成），生成
0-3 个"换一下试试"的按钮建议——每个建议是一次**定向调整**，用户点一下就能让
系统换成满足这个方向的候选。

**dimension 与 value 必须严格从下表选，不得自创、不得混用错误的 kind**：
| dimension        | 合法 value                                                | 适用节点 kind  |
|------------------|-------------------------------------------------------------|----------------|
| price            | "cheaper" 或 "pricier"（方向词，不是具体价格数字）            | restaurant     |
| distance         | "closer" 或 "farther"（方向词）                              | poi            |
| ambience         | "安静聊天" 或 "热闹"（只能这两个值之一，不能是其它词）        | poi / restaurant |
| dietary          | 低脂/健康轻食/高蛋白/日料/粤菜/不辣/无牛肉/有儿童餐/高人均/有包间/软烂/下午茶/甜品 中的一个 | restaurant |
| crowd_fit        | 亲子友好/适合 5-10 岁/适合青少年/适合老人/无台阶/可休息/无障碍/高强度/低强度 中的一个 | poi |
| cuisine_or_type  | 目标菜系/类型原文（如"粤菜"），自由文本                       | poi / restaurant |

**按活动的典型分歧点选，不要每个节点都套同一套模板**：
- 餐厅节点最常见的分歧是"贵不贵 / 氛围 / 忌口"——优先给 price(cheaper)；
  这家如果 tags 里有明显的"安静聊天"或"热闹"标签，就给反方向的 ambience；
  如果 intent 里能看出饮食约束信号，就给对应的 dietary。
- 活动节点最常见的分歧是"太远 / 氛围 / 人群适配"——优先给 distance(closer)；
  同上给反方向 ambience；如果 intent 里能看出物理/人群约束信号，就给对应的
  crowd_fit。

label 要求：口语化按钮文案，**最多 8 个字**，如「更便宜的」「安静点的」——
不要写"调整为…"这种说明书口吻，也不要用 dimension/value 的英文原词。

没有合适的分歧点就给 0 个，不要为了凑数瞎编——每个节点 0-3 个都合法。

【node_chip_context】（每项是一个活动节点，node_id 必须原样使用）
{context_json}
"""


def build_node_chips_instruction(node_chip_context: list[dict]) -> str:
    """把 node_chip_context 填进指令模板；context 为空时返回空串（不索要
    node_chips，prompt 也不必额外解释"为什么没给"）。"""
    if not node_chip_context:
        return ""
    import json

    return NODE_CHIPS_OUTPUT_INSTRUCTION_TEMPLATE.format(
        context_json=json.dumps(node_chip_context, ensure_ascii=False, indent=2)
    )


def build_narrator_user_message(
    *,
    intent_dict: dict,
    itinerary_dict: dict,
    stage_label: str,
    critic_summary: str = "",
    quality_warnings: list[str] | None = None,
    unmet_cuisines: list[str] | None = None,
    unmet_not_scheduled: list[str] | None = None,
    advisories: list[str] | None = None,
    want_title: bool = False,
    node_chip_context: list[dict] | None = None,
    plan_recap: str = "",
    provenance_hints: dict | None = None,
) -> str:
    """构造 user message（喂给 narrator 的 context）。

    Args:
        intent_dict: IntentExtraction.model_dump()
        itinerary_dict: Itinerary.model_dump()
        stage_label: "stream"（行程刚出炉）或 "confirm"（用户已确认下单）
        critic_summary: spec R6 新增。critic 历史摘要（含 critical 违规码 +
            修复反馈），narrator 据此触发主动质疑规则。空串 = 不触发。
        quality_warnings: spec R6 新增。可选 meta-critic 输出的额外质量提醒。
            None / 空列表 = 不触发。
        unmet_cuisines: 诚实告知用。用户明示但未排进行程、且**验证过附近确实
            没有匹配去处**的品类/活动诉求（可以说"附近没找到"）。
        unmet_not_scheduled: 诚实告知用（文案修缮批 · C2 实锤新增）。用户明示
            但未排进行程、而附近**其实有**这类去处的诉求——方案没安排上，
            措辞只能是"这次没安排上"，绝不能说成"附近没找到"；具体原因引擎
            未透出，叙事只陈述不归因（改口根治批：此前指令教 LLM 把原因与
            上版反馈"自然衔接"，属于编因，已剪）。分组由
            `agent.intent.narrator.split_unmet_by_nearby_availability` 完成。
        advisories: ADR-0010 D-7 新增。planner「绝不默默忽略」的结构化告知
            （每条已是自包含中文完整句），进诚实告知区，与 unmet_cuisines 同一
            纪律（先坦白、不假装满足）。None / 空列表 = 不触发。
        want_title: True 时要求 LLM 用 JSON 同次产出 title（小红书大标题）+ narration
            （+ node_chip_context 非空时再加 node_chips）。narrate 节点的非流式路径
            用 True（要写回 itinerary.summary）；流式打字路径用 False（保持逐字
            narration 的 UX，summary 走规则兜底）。
        node_chip_context: ADR-0013 F-3 新增。每个非 home 节点的 node_id + kind +
            关键字段/tags（见 `agent.intent.narrator._node_chip_context`），喂给
            LLM 让它"按活动的典型分歧点起 label"而不是瞎编。None / 空列表 = 不
            索要 node_chips（该字段本条消息里也就不会出现指令，模型应输出
            "node_chips": []）。仅在 want_title=True 时才会被使用。
        plan_recap: ADR-0011 决策 3 新增（2026-07-03）。非空时是"这版是照哪条
            反馈调的"回顾材料（来自会话上下文打包器的方案版本志切片），追加
            一句 prompt 指令要求 LLM 自然带出。空串 = 不触发（首轮/非反馈轮
            不硬扯）。
        provenance_hints: ADR-0014 决策 1（G-1）新增，决策 3（G-3）追加
            `budget_ambiguous`。形如 `{"distance_default": bool, "distance_km":
            float, "inferred_tag": str | None, "budget_ambiguous": bool}`
            （见 `agent.intent.narrator._provenance_hints`）。
            None / 全 False+None = 不触发（不附加【出处信息】段）。

    Returns:
        给 LLM 的 user message 文本。
    """
    import json

    # 抽出 narrator 关心的最小子集（不要喂全量 schema 噪音）
    intent_brief = {
        "companions": [
            {"role": c.get("role"), "age": c.get("age"), "count": c.get("count")}
            for c in (intent_dict.get("companions") or [])
        ],
        "duration_hours": intent_dict.get("duration_hours"),
        "dietary_constraints": intent_dict.get("dietary_constraints") or [],
        "physical_constraints": intent_dict.get("physical_constraints") or [],
        "experience_tags": intent_dict.get("experience_tags") or [],
        "social_context": intent_dict.get("social_context"),
    }
    # 分界修缮批 任务 3（2026-07-04）：叙事数字代码算——总时长与回家时刻在
    # 这里预格式化成展示字符串喂给 LLM（system prompt【数字纪律】要求照抄），
    # 不再喂原始 total_minutes 让 LLM 自己除（换算/四舍五入 LLM 会漂）。
    # total_hours_display 与模板路径同一算法（narrator.py::_template_narration
    # 的 total_minutes/60 保留 1 位小数），两条路径渲染一致。回家时刻从 nodes
    # 末尾 home 节点取（下方 brief 剔除了 home 节点且不含 hops，此前 few-shot
    # 还示范「19:30 回家」——LLM 只能编）；无 home 终点 → 不提供该字段，
    # prompt 侧要求缺失时绝不编一个回家时刻。
    raw_nodes = itinerary_dict.get("nodes") or []
    total_minutes = itinerary_dict.get("total_minutes")
    total_hours_display = (
        f"{total_minutes / 60:.1f} 小时"
        if isinstance(total_minutes, (int, float))
        else None
    )
    last_node = raw_nodes[-1] if raw_nodes else None
    home_end_time = (
        last_node.get("start_time")
        if isinstance(last_node, dict) and last_node.get("target_kind") == "home"
        else None
    )

    itinerary_brief: dict = {"summary": itinerary_dict.get("summary")}
    if total_hours_display:
        itinerary_brief["total_hours_display"] = total_hours_display
    if home_end_time:
        itinerary_brief["return_home_time"] = home_end_time
    itinerary_brief.update(
        {
            "nodes": [
                {
                    "kind": n.get("kind"),
                    "target_kind": n.get("target_kind"),
                    "start_time": n.get("start_time"),
                    "duration_min": n.get("duration_min"),
                    "title": n.get("title"),
                    "note": n.get("note"),
                }
                # 跳过首尾 home 节点：它们是抽象起讫，不在 narrator 文案中露出
                for n in raw_nodes
                if n.get("target_kind") != "home"
            ],
            "orders": [
                {
                    "kind": o.get("kind"),
                    "target_name": o.get("target_name"),
                    "detail": o.get("detail"),
                }
                for o in (itinerary_dict.get("orders") or [])
            ],
        }
    )

    if stage_label == "confirm":
        framing = "用户刚刚点了「确认并预约」，已经下单成功。请用一段话告诉他「都搞定了，可以放心了」，并简要回顾下午要做什么。"
    else:
        framing = (
            "行程刚组装好，还没下单。请把方案用导游开场白的形式告诉用户，"
            "全文恰好留一句邀请他给反馈的话（若中段的诚实告知已经自然带出邀请，"
            "结尾就不要再重复，见【邀请反馈只说一次】）。"
        )

    # spec R6：critic_summary / quality_warnings 触发主动质疑
    extras: list[str] = []
    if critic_summary:
        extras.append(f"【critic 历史】{critic_summary}\n→ 必须按【主动质疑规则】规则 1 在文案中追加一句质疑性短句。")
    if quality_warnings:
        warnings_str = "; ".join(quality_warnings)
        extras.append(f"【质量提醒】{warnings_str}\n→ 必须按【主动质疑规则】规则 2 把警告融进文案。")
    if unmet_cuisines:
        unmet_str = "、".join(unmet_cuisines)
        extras.append(
            f"【未满足的品类诉求】用户明确想要「{unmet_str}」，但附近没有匹配的餐厅"
            f"（超出距离范围或本地无此品类），方案里用了替代餐厅。\n"
            f"→ 必须按【诚实告知规则】先坦白没找到「{unmet_str}」，再暖语气说明用了替代、欢迎反馈。"
        )
    if unmet_not_scheduled:
        # 改口根治批（叙事不编因）：本块此前教两件编因果的事——括注"（往往是照
        # 用户最新的反馈/约束做的取舍）"把猜测当事实喂给 LLM；"把没安排的原因与
        # 【上版回顾】自然衔接"更是明令把 recap（真：这版因该反馈触发）升格成
        # 没安排项的因果解释（编：引擎从未透出"X 是被那条反馈滤掉的"）。房间
        # 实测里叙事因此把"没排上密室"归因成"按之前反馈"——纯属编造。现在：
        # 没有真实原因传入时只陈述、不归因。契约钉在
        # tests/test_narrator_no_fabricated_attribution.py。
        not_scheduled_str = "、".join(unmet_not_scheduled)
        extras.append(
            f"【未满足的品类诉求·这版没安排】用户明确想要「{not_scheduled_str}」，"
            f"附近其实有这类去处，但这一版方案没有安排上。规划器没有给出具体原因，"
            f"你也不知道原因——**只陈述、不归因**。\n"
            f"→ 必须按【诚实告知规则】坦白「{not_scheduled_str}」这次没安排上、"
            f"说明方案里用了什么顶上、欢迎反馈；**绝不能说「附近没找到"
            f"{not_scheduled_str}」「附近没有」**（附近是有的，说找不到就是撒谎）；"
            f"也**不要编一个原因**——比如把它说成是照用户之前某条反馈做的取舍，"
            f"那是你的猜测，不是规划器给出的事实。"
        )
    if advisories:
        advisories_str = "；".join(advisories)
        extras.append(
            f"【规划限制告知】{advisories_str}\n"
            f"→ 这些是规划器已经如实检测到的限制/建议（如点名的目标这次排不进、"
            f"超出常用预算、总时长比期望短等）。必须按【诚实告知规则】的坦白精神，"
            f"把这些内容自然带进文案，不要省略、不要轻描淡写成正常方案介绍。"
        )
    if plan_recap:
        extras.append(
            f"【上版回顾】{plan_recap}\n"
            f"→ 请在文案里自然带一句简短回顾这版是照哪条反馈调的（不要生硬照抄，"
            f"不要另起一段）。回顾只说明这版因何触发——**不要**把这条反馈说成"
            f"某个没安排项的原因（那是猜测，引擎没这么说过）。"
        )
    if provenance_hints:
        prov_lines: list[str] = []
        if provenance_hints.get("distance_default"):
            dist = provenance_hints.get("distance_km")
            prov_lines.append(f"距离用的是系统默认 {dist} 公里（用户没有提距离）")
        inferred_tag = provenance_hints.get("inferred_tag")
        if inferred_tag:
            prov_lines.append(
                f"标签「{inferred_tag}」是你从用户的话里推断出来的，不是用户直接"
                f"要求的——只能用猜测口吻（『我猜你可能想要』），"
                f"不能说成「你提到的{inferred_tag}」（用户没亲口说过）"
            )
        if provenance_hints.get("budget_ambiguous"):
            prov_lines.append(
                "用户提到了预算顾虑（比如「别太贵」），但没有给出具体数字——"
                "不要编造一个预算数字，只需诚实说一句「没法精确卡预算、会尽量控制」"
            )
        if prov_lines:
            extras.append(
                f"【出处信息】{'；'.join(prov_lines)}\n"
                f"→ 请按【出处诚实告知】规则用口语化的一两句话自然体现，不要另起一段、"
                f"不要说「出处」这个词。"
            )
    extras_block = ("\n\n" + "\n\n".join(extras)) if extras else ""

    title_block = ""
    if want_title:
        title_block = TITLE_OUTPUT_INSTRUCTION + build_node_chips_instruction(
            node_chip_context or []
        )
    tail = "直接输出 JSON。" if want_title else "直接输出文案。"

    return f"""{framing}

【intent】
{json.dumps(intent_brief, ensure_ascii=False, indent=2)}

【itinerary】
{json.dumps(itinerary_brief, ensure_ascii=False, indent=2)}{extras_block}{title_block}

{tail}"""
