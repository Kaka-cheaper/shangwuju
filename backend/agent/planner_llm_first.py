"""agent.planner_llm_first —— LLM-First Planner 主流程（按 problem.md 问题 14 设计）。

⚠️ 冻结声明（2026-05-22）：
    本文件是 plan_itinerary_with_mode("llm") 的子策略实现，自 LangGraph 主架构上线
    后**不再演进**。所有新功能改动应在 `agent/graph/` 下完成。

    保留理由：LangGraph blueprint_llm + assemble_blueprint 节点复用了本文件抽象出的
    PlanBlueprint / Critic backprompt 范式。

完整范式（参考 ItiNera EMNLP 2024 + LLM-Modulo NeurIPS 2024）：

```
[阶段 1] 候选搜索（Tool）
   - search_pois 按 intent 拉 POI 候选
   - search_restaurants 按 intent 拉餐厅候选
   - 失败兜底：放宽距离再重试一次（不删 dietary，让 LLM 自行裁掉）

[阶段 2] LLM 蓝图生成（带候选预览）
   - 调 blueprint_llm.generate_blueprint
   - LLM 自主决定段集合 / 顺序 / 时长 / target_id
   - 失败抛 BlueprintGenError → 阶段 4 fallback

[阶段 3] 蓝图 Critic 验证
   - run_blueprint_critics 跑时序 / 时长 / 营业时间 critic
   - 硬违规 → 把违规消息作为 critic_feedback 反馈给 LLM 重生成（最多 MAX_RETRIES）
   - 仍硬违规 → 阶段 4 fallback

[阶段 4] Fallback 链
   - LLM_GEN_RETRIES 用尽 → fallback hybrid（mode="llm" 的旧实现）
   - hybrid 失败 → fallback rule
   每层 fallback 都推一条 agent_thought 让评委看到决策过程

[阶段 5] 蓝图拼装为 Itinerary
   - assemble_from_blueprint
   - 推 itinerary_ready 事件
```

设计纪律：
- 蓝图阶段不调 search_pois/search_restaurants 之外的 Tool（蓝图全靠 LLM 决策）
- Critic 反馈必须文本可读，让 LLM 能针对性修改
- 每层 fallback 都通过 tracer.emit("agent_thought", ...) 让评委看到为什么 fallback
"""

from __future__ import annotations

import os
from typing import Any

from schemas.domain import Poi, Restaurant
from schemas.errors import FailureReason
from schemas.intent import IntentExtraction
from schemas.tools import (
    SearchPoisInput,
    SearchPoisOutput,
    SearchRestaurantsInput,
    SearchRestaurantsOutput,
)

from .assemble_blueprint import assemble_from_blueprint
from .blueprint import PlanBlueprint, run_blueprint_critics
from .blueprint_llm import BlueprintGenError, generate_blueprint
from .llm_client import LLMClient
from .trace import Tracer
from tools.registry import ToolInvocationResult, invoke_tool


# ============================================================
# 配置
# ============================================================

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# Critic backprompt 重试次数（含首次共 N+1 次蓝图生成）
LLM_FIRST_MAX_CRITIC_RETRIES = _env_int("PLANNER_LLM_FIRST_RETRIES", 2)


# ============================================================
# 公共结果
# ============================================================

from dataclasses import dataclass, field
from typing import Optional

from schemas.itinerary import Itinerary


@dataclass
class LlmFirstResult:
    """LLM-First Planner 的独立结果（不是公共 API；上层包成 PlannerResult）。"""

    success: bool
    itinerary: Optional[Itinerary] = None
    blueprint: Optional[PlanBlueprint] = None
    failure_reason: Optional[FailureReason] = None
    failure_detail: Optional[str] = None


# ============================================================
# 主入口
# ============================================================

def plan_llm_first(
    intent: IntentExtraction,
    *,
    client: LLMClient,
    tracer: Tracer,
) -> LlmFirstResult:
    """LLM-First Planner 主流程。

    Returns:
        LlmFirstResult（成功 → itinerary 非空；失败 → failure_reason 非空）
    """
    # ---- 阶段 1：候选搜索 ----
    pois = _query_pois(intent, tracer)
    if pois is None:  # 失败但已上抛 trace
        return LlmFirstResult(
            success=False,
            failure_reason=FailureReason.EMPTY_CANDIDATES,
            failure_detail="POI 候选为空（即使放宽距离）",
        )

    restaurants = _query_restaurants(intent, tracer)
    if restaurants is None:
        return LlmFirstResult(
            success=False,
            failure_reason=FailureReason.EMPTY_CANDIDATES,
            failure_detail="餐厅候选为空（即使放宽距离）",
        )

    # 用户明示「只想吃饭」/「单段沉浸」时，pois 或 restaurants 可能为空——这是合法的
    # 蓝图 LLM 看到空候选会自动调整段集合
    tracer.emit(
        "agent_thought",
        {
            "text": (
                f"候选准备就绪：POI {len(pois)} 个 / 餐厅 {len(restaurants)} 个，"
                "交给 LLM 出蓝图"
            ),
        },
    )

    # ---- 阶段 2-3：LLM 蓝图 + Critic 重试循环 ----
    critic_feedback: list[str] = []
    blueprint: PlanBlueprint | None = None
    last_error: str | None = None

    for attempt in range(LLM_FIRST_MAX_CRITIC_RETRIES + 1):
        # 调 LLM 出蓝图
        try:
            blueprint = generate_blueprint(
                intent,
                pois,
                restaurants,
                client=client,
                critic_feedback=critic_feedback if attempt > 0 else None,
            )
        except BlueprintGenError as e:
            last_error = f"{e.reason}: {e.detail}"
            tracer.emit(
                "agent_thought",
                {
                    "text": (
                        f"LLM 蓝图生成第 {attempt + 1} 次失败：{e.reason}；"
                        + ("将重试" if attempt < LLM_FIRST_MAX_CRITIC_RETRIES else "已耗尽重试")
                    ),
                },
            )
            continue

        tracer.emit(
            "agent_thought",
            {
                "text": (
                    f"LLM 蓝图（第 {attempt + 1} 次）：{len(blueprint.stages)} 段，"
                    f"总时长 {blueprint.total_minutes()} 分钟。理由：{blueprint.rationale[:80]}"
                ),
                "blueprint": blueprint.to_dict(),
            },
        )

        # 跑 Critic
        report = run_blueprint_critics(blueprint, intent)
        tracer.emit(
            "agent_thought",
            {
                "text": (
                    f"蓝图 Critic：passed={report.passed}，"
                    f"违规 {len(report.violations)} 条（硬 {len(report.hard_violations())}）"
                ),
                "critic_report": report.to_dict(),
            },
        )

        if report.passed:
            # ---- 阶段 5：拼装 ----
            itinerary = assemble_from_blueprint(intent, blueprint)
            tracer.emit(
                "agent_thought",
                {
                    "text": (
                        f"蓝图通过 critic，已组装 itinerary（"
                        f"{len(itinerary.stages)} 段，{itinerary.total_minutes} 分钟）"
                    ),
                },
            )
            return LlmFirstResult(
                success=True, itinerary=itinerary, blueprint=blueprint
            )

        # 硬违规 → 准备 critic_feedback 给下一轮 LLM
        critic_feedback = [v.message for v in report.hard_violations()]
        if attempt < LLM_FIRST_MAX_CRITIC_RETRIES:
            tracer.emit(
                "replan_triggered",
                {
                    "reason": "blueprint_critic_hard_violation",
                    "from_tool": "blueprint_critics",
                    "violations": critic_feedback,
                    "action": "regenerate_with_critic_feedback",
                },
            )

    # ---- 阶段 4：失败 ----
    return LlmFirstResult(
        success=False,
        failure_reason=FailureReason.UPSTREAM_FAILURE,
        failure_detail=(
            f"LLM 蓝图重试 {LLM_FIRST_MAX_CRITIC_RETRIES + 1} 次仍失败"
            + (f"；最后错误：{last_error}" if last_error else "")
        ),
        blueprint=blueprint,  # 暴露最后一次蓝图供调试
    )


# ============================================================
# 候选搜索（带兜底）
# ============================================================

def _query_pois(intent: IntentExtraction, tracer: Tracer) -> list[Poi] | None:
    """搜 POI；首次失败放宽 +2km 再试一次。空集返 []（合法），错误返 None。"""
    args = SearchPoisInput(
        distance_max_km=intent.distance_max_km,
        physical_constraints=list(intent.physical_constraints),
        experience_tags=list(intent.experience_tags),
        social_context=intent.social_context,
        age_in_party=[c.age for c in intent.companions if c.age is not None] or None,
        limit=20,
    ).model_dump()

    res = _call_tool("search_pois", args, tracer)
    if res.success:
        out = SearchPoisOutput.model_validate(res.output)
        if out.candidates:
            return list(out.candidates)

    # 放宽
    args["distance_max_km"] = float(intent.distance_max_km) + 2
    tracer.emit(
        "replan_triggered",
        {
            "reason": "empty_candidates",
            "from_tool": "search_pois",
            "action": "loosen_distance_+2km",
        },
    )
    res = _call_tool("search_pois", args, tracer)
    if res.success:
        out = SearchPoisOutput.model_validate(res.output)
        return list(out.candidates)
    return []  # 兜底空，让 LLM 自行决定不带 POI


def _query_restaurants(
    intent: IntentExtraction, tracer: Tracer
) -> list[Restaurant] | None:
    args = SearchRestaurantsInput(
        distance_max_km=intent.distance_max_km,
        dietary_constraints=list(intent.dietary_constraints),
        experience_tags=list(intent.experience_tags),
        social_context=intent.social_context,
        capacity_requirement=intent.capacity_requirement,
        limit=20,
    ).model_dump()

    res = _call_tool("search_restaurants", args, tracer)
    if res.success:
        out = SearchRestaurantsOutput.model_validate(res.output)
        if out.candidates:
            return list(out.candidates)

    args["distance_max_km"] = float(intent.distance_max_km) + 2
    tracer.emit(
        "replan_triggered",
        {
            "reason": "empty_candidates",
            "from_tool": "search_restaurants",
            "action": "loosen_distance_+2km",
        },
    )
    res = _call_tool("search_restaurants", args, tracer)
    if res.success:
        out = SearchRestaurantsOutput.model_validate(res.output)
        return list(out.candidates)
    return []


def _call_tool(
    tool: str, args: dict[str, Any], tracer: Tracer
) -> ToolInvocationResult:
    tracer.emit("tool_call_start", {"tool": tool, "input": args})
    res = invoke_tool(tool, args)
    tracer.emit(
        "tool_call_end",
        {
            "tool": tool,
            "output": res.output,
            "success": res.success,
            "reason": res.reason.value if res.reason else None,
            "duration_ms": res.duration_ms,
        },
    )
    return res
