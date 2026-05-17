"""intent —— IntentExtraction（§5.7 D-SoT 唯一权威实现）。

字段定义严格遵循 `docs/01-requirements/需求分析.md` §5.7：
- 不出现 scene_type / relation_type / is_family 等枚举字段（D9 硬条款）
- physical_constraints / dietary_constraints / experience_tags 仅接受 §5.x 词典
- social_context 是 §5.5 9 选 1
- companions[].role 是自由文本

不负责：
- 解析逻辑（在 Agent 层）。
- LLM Prompt 设计（在 backend/prompts）。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, conint, conlist

from schemas.tags import (
    DietaryTag,
    ExperienceTag,
    PhysicalTag,
    SocialContext,
)


# 时长 [min, max] 元组：min ≤ max ≤ 12（半日上限放宽给 8-10 兜底场景）
DurationRange = conlist(conint(ge=0, le=12), min_length=2, max_length=2)


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
    gender_mix: Optional[str] = Field(
        default=None, description='仅多人时填，如 "2男2女"'
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

    # ===== 元数据 =====
    raw_input: str = Field(..., description="原始用户输入字符串")
    parse_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="LLM 自报的置信度；< 0.6 时 Agent 应 ask back"
    )
    ambiguous_fields: list[str] = Field(
        default_factory=list, description='LLM 自报"哪些字段我不确定"'
    )
