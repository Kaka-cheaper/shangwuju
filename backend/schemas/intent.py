"""intent —— IntentExtraction（§5.7 D-SoT 唯一权威实现）。

字段定义严格遵循 `docs/01-requirements/需求分析.md` §5.7：
- 不出现 scene_type / relation_type / is_family 等枚举字段（D9 硬条款）
- physical_constraints / dietary_constraints / experience_tags 仅接受 §5.x 词典
- social_context 是 §5.5 9 选 1
- companions[].role 是自由文本

ADR-0014 G-0（2026-07-03）砍除记录：
- `pace_profile`（spec planning-quality-deep-review R8 引入）已砍除——全系统
  无消费方（规划器 `agent/planning/planners/pace_budget.py` 自证不读，走自己的
  三档节奏模型），"太久了"类反馈的收缩契约已迁移到 `duration_hours` 上界
  （见 `agent/intent/refiner.py::_rule_fallback`），原字段纯属空转，砍除更诚实。
- `Companion.gender_mix` 已砍除——全仓零消费，纯抽取无下游读取。
- 详见 `docs/adr/0014-requirement-analysis-provenance-and-hard-constraints.md` G-0 段。

兼容提醒（redis 持久 checkpoint）：`model_config = ConfigDict(extra="forbid")`
（见下方）意味着本次砍字段前落盘的旧 checkpoint（`SESSION_STORE=redis` 模式下
经 LangGraph Redis checkpointer 持久化、含 `pace_profile`/`gender_mix` 键的
存量 `IntentExtraction`）在本次发布后重新反序列化会校验失败。当前默认
`SESSION_STORE=memory`（进程重启即清，不受影响）；线上若曾切到 redis 模式，
升级前需清空/等旧 checkpoint 过期（hackathon demo 场景，不做旧 checkpoint 迁移，
这是本次拍板的已知代价而非遗漏）。

不负责：
- 解析逻辑（在 Agent 层）。
- LLM Prompt 设计（在 backend/prompts）。
"""

from typing import Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, conint, conlist

from schemas.tags import (
    DietaryTag,
    ExperienceTag,
    PhysicalTag,
    SocialContext,
)


# 时长 [min, max] 元组：min ≤ max ≤ 12（半日上限放宽给 8-10 兜底场景）
DurationRange = conlist(conint(ge=0, le=12), min_length=2, max_length=2)


# ADR-0014 决策 1（G-1，2026-07-03，二轮拷问修订）：字段出处四值枚举。
# - user_stated：用户这句话原话直接给出（即使做了口语→词典的直译，如"老婆"→"妻子"）。
# - inferred：用户没有直接要求这个属性，是从其他信息（年龄/身体状况/情绪/同伴关系等）
#   推断出来的（如"孩子 5 岁"推断出"亲子友好"；S7 类"安安静静"推断出更贴切的安静类
#   标签，标签本身源于用户的话但非字面复述）——降级序位居中：比 user_stated 弱、
#   比 prior/default 强，narration 可以说"我猜你想要…，不对可以说"。
# - prior：值来自 persona 画像/历史偏好注入（用户这句话没有另外提及），见
#   `intent_parser_prompt.compute_injected_priors`。
# - default：用户未提且无可用先验，纯粹是 schema 默认值。
FieldProvenance = Literal["user_stated", "inferred", "prior", "default"]


class Companion(BaseModel):
    """同行人结构。role 为自由文本（D9：开放性的体现）。"""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(
        ...,
        min_length=1,
        description="自由文本：妻子 / 孩子 / 朋友 / 女朋友 / 外公 / 商务客户 / 闺蜜 / ...",
    )
    age: Optional[NonNegativeInt] = Field(
        default=None, description="可选，整数岁数"
    )
    count: NonNegativeInt = Field(
        default=1, description="同 role 人数，默认 1"
    )
    is_birthday: bool = Field(
        default=False, description="是否当事人生日"
    )
    is_special_role: bool = Field(
        default=False, description="商务客户 / 长辈 等需特别尊重场合"
    )


class IntentExtraction(BaseModel):
    """意图解析模块的唯一输出格式（§5.7 D-SoT）。

    禁止顶层出现 scene_type / relation_type / is_family / is_friends 字段。
    `extra="forbid"` 配合 grep gate 双重防御。

    【新增字段纪律（四条不变式批沉淀，2026-07-11；2026-07-12 补记见下）】
    任何新增字段必须同步 `agent/intent/prompts/refiner_prompt.py` 的
    REFINER_FEW_SHOTS 全部示例——refiner 反馈轮对 intent 是**整体替换**
    （refiner.py 无字段级保留逻辑，靠 `_inherit_missing_keys` 守卫兜底），
    不在 few-shots 里的字段 LLM 大概率照猫画虎省略、Pydantic 默认值静默顶上，
    显式诉求在反馈轮被二次击穿且无报警（budget_per_person /
    explicit_dining_requested 两次踩过同一雷）。

    【缺键继承守卫已落地（forge-intent-loss 收敛，2026-07-12）】上一版本
    此处遗留"同时评估是否需要 `refine_intent` 的缺键继承守卫"——已落地为
    `agent/intent/refiner.py::_inherit_missing_keys`（C1 通用守卫，收编原
    explicit_dining_requested 专属补丁）。**新增字段时的检查项相应更新为**：
    若新字段的 schema 默认值不是"语义空值"（即 duration_hours=[4,6]/
    social_context="家庭日常" 这类"有意义的具体值"，而非 None/""/[]/
    default_factory=list 那种"就是没设"的空语义），或字段本身是可选/自由
    字段但键缺失会静默丢失 user_stated 诉求，需评估是否要把新字段加进
    `_inherit_missing_keys` 的白名单（`_INHERITABLE_FIELDS`，逐字段显式
    枚举，不接受"凡带 default 就继承"这类宽泛规则——理由见该函数所在模块的
    大段设计注释）。必传字段（`Field(...)` 无默认值）键缺失会触发响亮的
    ValidationError（现有错误回灌重试/兜底路径已覆盖），不需要加进守卫。
    """

    model_config = ConfigDict(extra="forbid")

    # ===== 时间维度 =====
    start_time: str = Field(
        ...,
        description="ISO-like 形如 2026-05-09T14:00 或 today_afternoon | tomorrow_evening",
    )
    start_weekday: Optional[str] = Field(
        default=None,
        description="可选 weekday 标签如 saturday / sunday",
    )
    duration_hours: DurationRange = Field(  # type: ignore[valid-type]
        default=[4, 6],
        description="[min, max]，默认 [4, 6]",
    )

    # ===== 空间维度 =====
    distance_max_km: float = Field(
        default=5.0, ge=0, le=100, description="距离上限（km），默认 5"
    )

    # ===== 同行人结构（必传：用户提到任何同行人就填；独自/一个人场景填空数组）=====
    companions: list[Companion] = Field(
        ...,
        description=(
            "同行人列表（companions）；用户提到「老婆/孩子/朋友/外公外婆/客户/闺蜜/女朋友/同事」"
            "等任意同行人就必须填；明确说「一个人/自己/独自」时填空数组 []。"
            "**禁止省略本字段**——LLM 必须显式输出 [] 而非缺省。"
        ),
    )

    # ===== 三类 tag 约束（仅接受词典内值；必传字段，机械触发不命中则空数组）=====
    physical_constraints: list[PhysicalTag] = Field(
        ...,
        description=(
            "物理约束（physical constraints）：从中文词典机械触发，例如「亲子友好(kid-friendly)/"
            "适合老人(senior-friendly)/无台阶(step-free)/可休息(rest-area)/低强度(low-intensity)」。"
            "**只能从中文词典选词，不得输出英文/拼音/自创词**。词典不命中则填空数组 []。"
            "**禁止省略本字段**——必须显式输出 [] 而非缺省。"
        ),
    )
    dietary_constraints: list[DietaryTag] = Field(
        ...,
        description=(
            "饮食约束（dietary constraints）：从中文词典机械触发，例如「低脂(low-fat)/健康轻食(healthy)/"
            "粤菜(cantonese)/日料(japanese)/不辣(non-spicy)/有儿童餐(kids-meal)/高人均(premium)/"
            "有包间(private-room)/软烂(soft-food)/下午茶(afternoon-tea)」。"
            "**只能从中文词典选词，不得输出英文/拼音/自创词**。词典不命中则填空数组 []。"
            "**禁止省略本字段**——必须显式输出 [] 而非缺省。"
        ),
    )
    experience_tags: list[ExperienceTag] = Field(
        ...,
        description=(
            "体验偏好（experience tags）：从中文词典机械触发，例如「拍照友好(photogenic)/"
            "网红打卡(trendy-spot)/安静聊天(quiet)/热闹(lively)/独处舒缓(solo-calm)/"
            "商务体面(business)/礼仪感(formal)/亲密情侣(romantic)/学习成长(learning)/看展(exhibition)」。"
            "**只能从中文词典选词，不得输出英文/拼音/自创词**。词典不命中则填空数组 []。"
            "**禁止省略本字段**——必须显式输出 [] 而非缺省。"
        ),
    )

    # ===== 社交上下文（单值 enum）=====
    social_context: SocialContext = Field(
        default="家庭日常",
        description=(
            "9 选 1（social context）：从「家庭日常/老人伴助/闺蜜聊天/朋友热闹/情侣亲密/"
            "商务接待/同学重聚/独处放空/纪念日仪式感」中**选最贴切的一个**；"
            "**不得发明新值，不得输出英文（如 family / friends / business 都禁止）**。"
        ),
    )

    # ===== 容量与额外服务（可选）=====
    capacity_requirement: Optional[NonNegativeInt] = Field(
        default=None, description="同行 ≥4 人时填"
    )
    extra_services: list[str] = Field(
        default_factory=list, description="仪式场合需附加服务，如 [蛋糕]"
    )
    preferred_poi_types: list[str] = Field(
        default_factory=list, description="用户明示 POI 类型，如 [展览, 美术馆]"
    )

    # ===== 显式就餐诉求三态（四条不变式批 I3，2026-07-11）=====
    explicit_dining_requested: Optional[bool] = Field(
        default=None,
        description=(
            "泛化就餐意愿三态（I3 显式诉求零丢失）。**None=用户没提及**（就餐"
            "安排走既有推断触发——商务/纪念日软锚、跨饭点窗+dietary，现状行为"
            "分毫不变）；**True=显式要吃饭**（「找个地方吃饭/搓一顿/顺便吃个饭」"
            "这类意愿动词但无具体品类的表达——点名了品类（『想吃烧烤』）时由"
            " preferred_poi_types 承接品类、本字段仍可同时为 True，两者不互斥）；"
            "**False=显式不要**（「我们吃过了/不用排饭/别安排吃的」——否定对象"
            "是**就餐行为本身**才填 False；否定对象是品类/口味（『不吃辣』）走"
            " dietary 词典轨，与本字段无关）。"
            "三态而非 bool：把「没提」和「明确不要」折叠成同一个 False 本身就是"
            "一次归一化吞显式——显式拒绝必须可表达（I3 宪法双向：要与不要都压过"
            "推断）。"
            "边界①（勿扩大）：本字段只管「有没有这顿饭」，不承载「这是不是唯一"
            "诉求」——不得用于扩大 demand_scope.is_single_consumption 的判定。"
            "边界②（勿泛化）：这是吃饭专属信号，不是通用泛化意愿信号——『想"
            "活动一下』等其它类别的同款缺口需专项评估（方案 1.8），别看到本字段"
            "就假设该模式已覆盖所有类别。"
            "Optional 默认 None：旧 checkpoint（无此字段）免迁移。"
        ),
    )

    # ===== 预算（ADR-0014 决策 3，G-3）=====
    budget_per_person: Optional[float] = Field(
        default=None,
        ge=0,
        description=(
            "用户明说的人均预算（元）。仅当原话**明确给出数字**时才填（如"
            "「人均 50」「预算 100 一个人」「一人一百以内」→ 50/100/100）；"
            "定性表达（「别太贵」「便宜点」「穷游」等，没有具体数字）**禁止**"
            "编造一个数字——一律留 null。Optional 默认 None：旧 checkpoint"
            "（无此字段）免迁移。"
        ),
    )

    # ===== 信任带 §四①（2026-07-06）=====
    understanding: str = Field(
        default="",
        description=(
            "第一人称『我理解成……』一句话（信任带①拍专用，非结构化字段的"
            "补充叙事）。LLM 现生成，风格红线见 intent_parser_prompt："
            "句式「用户……，我理解成……」、暴露一次推断、≤40 字、禁词见 prompt。"
            "Optional 默认空串——旧 checkpoint / stub 不产该字段时前端静默不"
            "渲染①拍，不影响任何既有消费方（D-SoT 结构化字段不受影响）。"
        ),
    )

    # ===== 元数据 =====
    raw_input: str = Field(..., description="原始用户输入字符串")
    parse_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="LLM 自报的置信度；< 0.6 时 Agent 应 ask back"
    )
    ambiguous_fields: list[str] = Field(
        default_factory=list, description='LLM 自报"哪些字段我不确定"'
    )

    # ===== 字段出处（ADR-0014 决策 1，G-1）=====
    field_provenance: Optional[dict[str, FieldProvenance]] = Field(
        default=None,
        description=(
            "字段/元素出处标注。标量字段键=字段名本身（如 'distance_max_km'）；"
            "列表字段键='字段名:元素值'（如 'dietary_constraints:不辣'），逐元素标——"
            "一个 dietary 列表里可能'不辣'是用户说的、'日料'是先验注入的，字段级一个"
            "标签盖不住。覆盖范围（G-1 拍板，非全字段；budget_per_person 为 G-3"
            "追加，同款标量规范——None 时不写键，非 None 时几乎恒为 user_stated，"
            "因为该字段唯一的产出路径就是原话明说数字）：标量 start_time/"
            "start_weekday/duration_hours/distance_max_km/social_context/"
            "capacity_requirement/budget_per_person；列表 physical_constraints/dietary_constraints/"
            "experience_tags/extra_services。companions（自由文本、无先验注入通道）、"
            "preferred_poi_types（自由文本、无 canonical 化正向函数、且当前 prompt"
            "设计下只有 user_stated 一条来源路径，标了也恒为 user_stated）、"
            "raw_input/parse_confidence/ambiguous_fields（描述抽取过程本身而非需求"
            "内容）不在本字段覆盖范围内，理由见 docs/adr/0014 决策 1 落地报告。"
            "Optional 默认 None——旧 checkpoint（无此字段）免迁移，读取时把 None"
            "当『无出处信息』处理，不强行倒推。"
        ),
    )


def extract_tag_provenance(
    intent: IntentExtraction, field: str, tags: Iterable[str]
) -> Optional[dict[str, str]]:
    """从 `intent.field_provenance` 摘取某个受控词典字段的逐 tag 出处子集。

    ADR-0014 决策 2（G-2）：`tools._helpers.relax_tag_search` 的 soft tag
    降级序需要按出处排序，但它只关心"这次 required_tags 里每个 tag 的出处"，
    不关心整份 `field_provenance` 的其它字段——本函数做这次收窄，键从
    `field_provenance` 的复合键（`"字段名:元素值"`）降级成裸 tag 值（单个
    SearchXxxInput 调用里 tag 值本身已无歧义，不需要复合键）。

    三条 `SearchXxxInput` 构造点共用（改一处查三处，与既有"三处
    SearchRestaurantsInput 构造点"注释同一纪律）：
    - `agent/runtime/tools/search_adapter.py::search_pois_for_intent` /
      `search_restaurants_for_intent`
    - `agent/planning/planners/rule_planner.py::_query_pois` / `_query_restaurants`
    - `agent/planning/planners/ils_planner.py::_query_pois` / `_query_restaurants`

    Returns:
        `{tag值: 出处}`；`intent.field_provenance` 为 None（旧 checkpoint /
        未跑校正）或本次 tags 一个都没有出处记录 → 返回 None（而非空 dict），
        让 `relax_tag_search` 的默认降级序接管，不强行编造出处信息。
    """
    provenance = intent.field_provenance
    if not provenance:
        return None
    out: dict[str, str] = {}
    for tag in tags:
        key = f"{field}:{tag}"
        if key in provenance:
            out[tag] = provenance[key]
    return out or None
