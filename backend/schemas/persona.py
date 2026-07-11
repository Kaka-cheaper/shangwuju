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

from schemas.domain import RecentTrip
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


# ADR-0014 G-0（2026-07-03）砍除记录：`PaceProfile`（曾定义于此，spec
# planning-quality-deep-review R1+R8 引入）与 `Persona.default_pace_profile`
# 已一并砍除。原因：`PaceProfile` 唯一的实际用途是给 `IntentExtraction.pace_profile`
# 提供 prior 注入（`intent_parser_prompt.py::_format_pace_prior_section`），
# 而 `IntentExtraction.pace_profile` 本身已在同一次改动中砍除（全系统无消费方，
# 见 `schemas/intent.py` 砍除记录）——留着 `default_pace_profile` 会变成"注入一个
# 不会再被读取的 prior"，同属空转，一并砍除更诚实。`mock_data/personas.json`
# 中原 `default_pace_profile` 键已同步移除（`Persona.model_config` 是
# `extra="forbid"`，留着会在加载 mock 数据时校验失败）。
# 详见 `docs/adr/0014-requirement-analysis-provenance-and-hard-constraints.md` G-0 段。


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
                  + visited_targets append 本次 itinerary 中的 poi/restaurant id
                  + preferred_routes (from→to) 通过次数累加（连续访问的两段）
    - refine 时（changed_fields 含「去掉 X」）→ rejected_tags +1，accepted_tags -1
    - distance_history append 命中方案的 distance_max_km

    Step 7：visited / route 记忆（个性化记忆深度）
    - visited_targets 让 search_pois / search_restaurants 可选 exclude_recently_visited
    - preferred_routes 让 weights_llm 知道「这个用户常走哪条路径」
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str
    accepted_tags: TagCounter = Field(default_factory=TagCounter)
    rejected_tags: TagCounter = Field(default_factory=TagCounter)
    distance_history: list[NonNegativeFloat] = Field(
        default_factory=list,
        description="历次成功方案的 distance_max_km；中位数可作为下次默认",
    )
    visited_targets: list["VisitedRecord"] = Field(
        default_factory=list,
        description=(
            "曾经 confirm 过的 POI/餐厅 id 与时间戳。"
            "search_pois / search_restaurants 可按此排除最近 N 天访问过的。"
            "Step 7 新增；空列表向后兼容。"
        ),
    )
    preferred_routes: dict[str, NonNegativeInt] = Field(
        default_factory=dict,
        description=(
            "（from→to）路径通过次数。键格式 'from_id|to_id'（避免 dict tuple key）。"
            "weights_llm 看这个调整 smoothness 权重；评分项 4 商业价值的核心。"
        ),
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

    def recently_visited_ids(
        self, *, within_days: int = 30, now_ms: Optional[int] = None
    ) -> list[str]:
        """返回最近 within_days 内访问过的 target_id 列表（去重）。

        若 now_ms 为 None，用当前时间。
        """
        import time as _time

        cutoff_ms = (now_ms or int(_time.time() * 1000)) - within_days * 86400 * 1000
        seen: set[str] = set()
        for r in self.visited_targets:
            if r.visited_at_ms >= cutoff_ms:
                seen.add(r.target_id)
        return list(seen)


class VisitedRecord(BaseModel):
    """单次访问记录（confirm 后写入）。"""

    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(..., description="POI 或餐厅 id，如 P011 / R007")
    target_kind: str = Field(..., description="poi / restaurant")
    visited_at_ms: NonNegativeInt = Field(..., description="访问时刻（毫秒时间戳）")
    rating_given: Optional[float] = Field(
        default=None,
        description="用户回访打分（产品下一阶段；当前 demo 不要求）",
    )
    cooldown_days: NonNegativeInt = Field(
        default=30, description="冷却期（天）；之内不再推荐"
    )


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
    recent_trips: list[RecentTrip] = Field(
        default_factory=list,
        description=(
            "本会话最近行程档案（LIFO，最新在头，最多 5 条），"
            "`data.memory_store.get_recent_trips(session_id)` 原样透传。"
            "用户偏好面板全环方案 §2.3/§14.3 新增字段——`summary` 已是后端 LLM"
            "生成的脱敏自然语言，前端「去过」子项直接渲染，不需要模板拼接。"
            "session_id 缺省时恒为空列表（同 memory 区的模板视图纪律）。"
        ),
    )


# 解析 forward reference："VisitedRecord" 在 UserMemory 定义之后才声明
# Pydantic v2 自动处理但保险起见显式 rebuild
UserMemory.model_rebuild()
