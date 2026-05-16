"""persona —— 用户画像（persona）+ 历史偏好（memory）契约。

业务故事（方案 C：persona + memory 组合）：
- persona：用户身份档案（5 个 mock，"我是谁"），写死不学习
- memory ：用户历史偏好统计（accepted_tags / rejected_tags / distance_history），
            confirm 后累积 / refine 后扣分

意图解析层将两者合并为 prior，注入 prompt：
    "你是 {persona.label}（{persona.notes}）。
     近期高频偏好：[低脂(5次), 亲子(3次)]。
     若用户输入与默认冲突以输入为准；未提及的字段用偏好补全。"

D9 边界（不破）：
- persona 是 user 维度，**不是** scene 维度
- persona.default_tags 仅作 prior 注入，**不**变成 Tool 内的 if-else 分支
- 用户输入永远优先于档案默认值

不负责：
- persona 选择 UI（在 frontend/）
- memory 累积逻辑（在 backend/data/memory_store.py）
- prompt 注入文案（在 backend/agent/prompts/system_prompt.py）
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt

from schemas.tags import (
    DietaryTag,
    ExperienceTag,
    PhysicalTag,
    SocialContext,
)


# ============================================================
# Persona（用户画像，5 个 mock）
# ============================================================

class PersonaDefaultTags(BaseModel):
    """persona 自带的默认 tag 偏好。仅作 prior 注入，不强制。"""

    model_config = ConfigDict(extra="forbid")

    physical: list[PhysicalTag] = Field(default_factory=list)
    dietary: list[DietaryTag] = Field(default_factory=list)
    experience: list[ExperienceTag] = Field(default_factory=list)
    suitable_for_priority: list[SocialContext] = Field(
        default_factory=list,
        description="该 persona 最常出现的 social_context（影响 POI/餐厅排序优先级）",
    )


class Persona(BaseModel):
    """用户画像档案（mock）。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., description="如 u_dad / u_solo / u_biz")
    label: str = Field(..., description="人话标签：新手爸爸 / 商务白领 / 独居青年 ...")
    icon: str = Field(default="👤", description="emoji 图标")
    notes: str = Field(
        ..., description="一句话画像描述，给 LLM 看；如「带 5 岁孩子，偏好近 + 室内备份」"
    )
    home_location: str = Field(default="", description="默认家位置展示名")
    default_distance_max_km: NonNegativeFloat = Field(default=5.0)
    default_budget: NonNegativeFloat = Field(default=300.0)
    default_tags: PersonaDefaultTags = Field(default_factory=PersonaDefaultTags)


# ============================================================
# Memory（学到的偏好，每 user 一份）
# ============================================================

class TagCounter(BaseModel):
    """{ "低脂": 5, "亲子友好": 3 } —— tag 计数。"""

    model_config = ConfigDict(extra="forbid")

    counts: dict[str, NonNegativeInt] = Field(default_factory=dict)

    def bump(self, tag: str, delta: int = 1) -> None:
        """累加；负值用于扣分（refine 时去掉的 tag）。"""
        cur = self.counts.get(tag, 0) + delta
        if cur < 0:
            cur = 0
        self.counts[tag] = cur

    def top(self, n: int = 5) -> list[tuple[str, int]]:
        """按计数倒序返回前 n 条。"""
        return sorted(self.counts.items(), key=lambda kv: kv[1], reverse=True)[:n]


class UserMemory(BaseModel):
    """单个 user 的累积偏好。

    更新时机（详见 backend/data/memory_store.py）：
    - confirm 时 → accepted_tags 累计 itinerary 命中的所有 tag
    - refine 时（changed_fields 含「去掉 X」）→ rejected_tags +1，accepted_tags -1
    - distance_history append 命中方案的 distance_max_km
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str
    accepted_tags: TagCounter = Field(default_factory=TagCounter)
    rejected_tags: TagCounter = Field(default_factory=TagCounter)
    distance_history: list[NonNegativeFloat] = Field(
        default_factory=list,
        description="历次成功方案的 distance_max_km；中位数可作为下次默认",
    )
    last_updated_ms: Optional[NonNegativeInt] = Field(
        default=None, description="毫秒时间戳"
    )

    def median_distance(self) -> Optional[float]:
        if not self.distance_history:
            return None
        sorted_d = sorted(self.distance_history)
        mid = len(sorted_d) // 2
        if len(sorted_d) % 2:
            return float(sorted_d[mid])
        return float((sorted_d[mid - 1] + sorted_d[mid]) / 2)


# ============================================================
# 合并视图（intent_parser / 前端偏好面板都用这个）
# ============================================================

class UserPreferenceView(BaseModel):
    """persona + memory 的合并展示，前端面板与 prompt 注入共用。

    评分计算（见 backend/data/memory_store.py.compute_priors）：
      final_weight(tag) = persona.default_weight * 0.3 + memory.count * 0.7
    输出 top_priors 给 prompt 注入用。
    """

    model_config = ConfigDict(extra="forbid")

    persona: Persona
    memory: UserMemory
    top_priors: list[str] = Field(
        default_factory=list,
        description="按权重倒序的 top tag（合并 persona + memory），如 ['低脂','亲子友好','无台阶']",
    )
    suggested_distance_max_km: Optional[NonNegativeFloat] = Field(
        default=None,
        description="建议默认距离（memory 中位数；为空时用 persona.default_distance_max_km）",
    )
