"""agent.weights_llm —— 主观规划权重的 LLM 决策层（A+C 混合方案的 A 段第一步）。

# FROZEN: 仅 ILS 路径，不被 graph 路径消费（spec agent-directory-restructure R3.5）。

学术依据：[Vansteenwegen et al. 2009 Metaheuristics for Tourist Trip Planning],
[Gunawan et al. 2019 Multi-objective TOPTW with adjustment ILS]——多目标 TOPTW
用加权和把 comfort / time / cost 等维度合成单目标 utility，权重由「用户偏好」决定。

本模块负责出权重 4 元组（comfort/time/cost/smoothness），让 LLM 看 IntentExtraction
+ 用户输入语境，给出主观分数。当 LLM 不可用（无 API key / stub provider）时走启发式
兜底——按 social_context + companions 结构静态映射。

不负责：
- 候选生成与搜索（在 planner_hybrid.py）
- 客观打分（在 planner_hybrid.py 的 utility_score）
- Critic 验证（在 critics.py）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from schemas.intent import IntentExtraction


# ============================================================
# 数据结构
# ============================================================

@dataclass
class PlanningWeights:
    """ILS 加权效用函数的 4 个权重；和 ≈ 1.0（容忍 0.05 浮点偏差）。

    含义（来自 [Multi-obj TOPTW Gunawan 2019]）：
    - comfort：舒适度——同行人偏好、标签匹配度、评分权重
    - time   ：时间效率——总耗时与距离短优先
    - cost   ：成本敏感——餐厅人均与门票价格惩罚
    - smoothness：路线连贯——避免跨区跳跃，POI 与餐厅距离匹配
    """

    comfort: float
    time: float
    cost: float
    smoothness: float
    rationale: str = ""
    source: str = "stub"  # "llm" / "stub" / "fallback"

    def normalize(self) -> "PlanningWeights":
        """归一化到和为 1（防 LLM 出权重和 ≠ 1）。"""
        total = self.comfort + self.time + self.cost + self.smoothness
        if total <= 0:
            # 全 0 兜底：按场景默认分布
            return PlanningWeights(
                comfort=0.4, time=0.2, cost=0.2, smoothness=0.2,
                rationale=self.rationale, source=self.source,
            )
        return PlanningWeights(
            comfort=self.comfort / total,
            time=self.time / total,
            cost=self.cost / total,
            smoothness=self.smoothness / total,
            rationale=self.rationale,
            source=self.source,
        )

    def summary(self) -> str:
        return (
            f"舒适 {self.comfort:.2f} / 时间 {self.time:.2f} / "
            f"成本 {self.cost:.2f} / 连贯 {self.smoothness:.2f}"
        )

    def to_dict(self) -> dict:
        return {
            "comfort": round(self.comfort, 3),
            "time": round(self.time, 3),
            "cost": round(self.cost, 3),
            "smoothness": round(self.smoothness, 3),
            "rationale": self.rationale,
            "source": self.source,
        }


# ============================================================
# 启发式兜底（按 social_context + 同行人结构静态映射）
# ============================================================

# (comfort, time, cost, smoothness) 各 social_context 的预设权重
_DEFAULT_WEIGHTS_BY_CONTEXT: dict[str, tuple[float, float, float, float]] = {
    # 家庭日常：孩子在场 → comfort 与 smoothness 高，time 中
    "家庭日常":      (0.40, 0.20, 0.15, 0.25),
    # 老人伴助：身体限制 → comfort 极高，路线连贯（无台阶/可休息）
    "老人伴助":      (0.45, 0.10, 0.10, 0.35),
    # 闺蜜聊天：体验导向 → comfort 高，cost 中
    "闺蜜聊天":      (0.40, 0.15, 0.20, 0.25),
    # 朋友热闹：偏向均衡，cost 略高（学生/职场友人对预算敏感）
    "朋友热闹":      (0.30, 0.20, 0.30, 0.20),
    # 情侣亲密：体验+连贯并重
    "情侣亲密":      (0.40, 0.15, 0.20, 0.25),
    # 商务接待：time 高（赶时间）+ cost 不敏感（公司报销）+ comfort 体面
    "商务接待":      (0.30, 0.40, 0.05, 0.25),
    # 同学重聚：cost 较敏感
    "同学重聚":      (0.30, 0.20, 0.30, 0.20),
    # 独处放空：comfort 极高（自己舒服为先）
    "独处放空":      (0.50, 0.15, 0.20, 0.15),
    # 纪念日仪式感：cost 极不敏感 + comfort 顶 + 连贯重
    "纪念日仪式感":  (0.45, 0.10, 0.05, 0.40),
}


def _heuristic_weights(intent: IntentExtraction) -> PlanningWeights:
    """无 LLM 时的启发式兜底。

    规则：
    1. 取 social_context 默认权重
    2. 同行老人/儿童 → comfort + 0.05、time - 0.05
    3. 反馈含「快」「赶时间」→ time + 0.1（intent.raw_input 里检测）
    4. 反馈含「便宜」「划算」→ cost + 0.1
    5. 归一化
    """
    base = _DEFAULT_WEIGHTS_BY_CONTEXT.get(
        intent.social_context, (0.35, 0.25, 0.20, 0.20)
    )
    comfort, time_w, cost, smooth = base

    # 同行老人/儿童修正
    has_special = any(
        c.is_special_role or (c.age is not None and (c.age < 12 or c.age > 60))
        for c in intent.companions
    )
    if has_special:
        comfort += 0.05
        time_w -= 0.05

    # 反馈关键词修正（应对 /chat/refine 后的迭代）
    raw = intent.raw_input or ""
    if any(kw in raw for kw in ("快", "赶时间", "急")):
        time_w += 0.1
    if any(kw in raw for kw in ("便宜", "划算", "省钱", "学生")):
        cost += 0.1
    if any(kw in raw for kw in ("讲究", "体面", "高端", "纪念")):
        comfort += 0.05

    rationale = (
        f"启发式兜底：social_context={intent.social_context}；"
        f"同行特殊角色={'有' if has_special else '无'}"
    )
    return PlanningWeights(
        comfort=max(0.05, comfort),
        time=max(0.05, time_w),
        cost=max(0.05, cost),
        smoothness=max(0.05, smooth),
        rationale=rationale,
        source="stub",
    ).normalize()


# ============================================================
# LLM 模式
# ============================================================

_LLM_PROMPT_SYSTEM = """你是一个出行规划权重打分助手。

任务：根据用户的出行意图，给 ILS 启发式搜索的目标函数出 4 个 [0, 1] 权重。
4 个权重和应等于 1。

权重定义：
- comfort: 舒适度（标签匹配、评分、年龄适配）
- time: 时间效率（总耗时短、距离近）
- cost: 成本敏感（餐厅人均、门票价格惩罚强度）
- smoothness: 路线连贯（避免跨区跳跃、风格统一）

输出 JSON 格式（不要 markdown 围栏）：
{
  "comfort": 0.40,
  "time": 0.20,
  "cost": 0.15,
  "smoothness": 0.25,
  "rationale": "为什么这样分配（中文 ≤80 字）"
}

启发：
- 老人/孩子在场 → comfort 重、smoothness 重（无台阶很关键）
- 商务接待 → time 重、cost 极轻（公司报销）
- 独处放空 → comfort 高（一人舒服为先）
- 纪念日 → comfort 顶配、cost 极轻
- 学生/聚会 → cost 中等偏重
"""


def _llm_weights(intent: IntentExtraction, client) -> Optional[PlanningWeights]:
    """调 LLM 出权重；任何异常返回 None 让上层兜底。"""
    from ..core.llm_client import LLMMessage, strip_json_fence

    user_payload = {
        "social_context": intent.social_context,
        "companions": [
            {
                "role": c.role,
                "age": c.age,
                "count": c.count,
                "is_special_role": c.is_special_role,
                "is_birthday": c.is_birthday,
            }
            for c in intent.companions
        ],
        "physical_constraints": list(intent.physical_constraints),
        "dietary_constraints": list(intent.dietary_constraints),
        "experience_tags": list(intent.experience_tags),
        "distance_max_km": intent.distance_max_km,
        "duration_hours": list(intent.duration_hours),
        "raw_input": intent.raw_input,
    }
    messages = [
        LLMMessage(role="system", content=_LLM_PROMPT_SYSTEM),
        LLMMessage(
            role="user",
            content="意图 JSON：\n" + json.dumps(user_payload, ensure_ascii=False),
        ),
    ]
    try:
        resp = client.chat(
            messages,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = strip_json_fence(resp.content) or ""
        data = json.loads(content)
        return PlanningWeights(
            comfort=float(data.get("comfort", 0)),
            time=float(data.get("time", 0)),
            cost=float(data.get("cost", 0)),
            smoothness=float(data.get("smoothness", 0)),
            rationale=str(data.get("rationale", "")).strip()[:200],
            source="llm",
        ).normalize()
    except Exception:  # noqa: BLE001
        return None


# ============================================================
# 主入口
# ============================================================

def get_planning_weights(
    intent: IntentExtraction, *, client=None,
) -> PlanningWeights:
    """权重决策入口。

    优先级：
    1. client 非 None → LLM 模式（失败兜底到启发式）
    2. client 为 None → 启发式

    强制归一化 + 字段下限 0.05（防 LLM 给极端值导致某维度被完全忽略）。
    """
    if client is not None and getattr(client, "provider", None) != "stub":
        result = _llm_weights(intent, client)
        if result is not None:
            return result
        # LLM 失败 → 兜底，但标 source=fallback 以便观察
        fallback = _heuristic_weights(intent)
        fallback.source = "fallback"
        return fallback

    return _heuristic_weights(intent)
