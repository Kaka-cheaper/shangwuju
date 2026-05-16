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

    # ===== 同行人结构 =====
    companions: list[Companion] = Field(
        default_factory=list,
        description="同行人列表；独处场景为空数组",
    )

    # ===== 三类 tag 约束（仅接受词典内值）=====
    physical_constraints: list[PhysicalTag] = Field(default_factory=list)
    dietary_constraints: list[DietaryTag] = Field(default_factory=list)
    experience_tags: list[ExperienceTag] = Field(default_factory=list)

    # ===== 社交上下文（单值 enum）=====
    social_context: SocialContext = Field(
        default="家庭日常",
        description="9 选 1，从约束反推的氛围标签",
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
