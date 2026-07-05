"""agent.planning.memory_writer —— 行程档案回写（TravelAgent / TriFlow 范式）。

【为什么需要这一层】

TravelAgent (NeurIPS'24) / TriFlow 的关键发现：把每次行程结果写回
`recent_trips` 档案，下次意图解析阶段把最新条目注入 prompt
（"用户上次「家庭」场景的行程：{summary}"），LLM 输出明显贴合用户偏好。

【身份边界（记忆身份读写分离批，ADR-0015 身份边界补充决策，2026-07-05）】

demo 无账号体系，**会话即身份**：本模块写入的行程档案按 `state["session_id"]`
键控，存进 `data.memory_store` 的会话私有区（进程内，与 SESSION_STORE 同生命
周期语义）。历史实现写全局单文件 user_profile.json——单用户假设下成立，演示日
多访客并发时 A 确认的行程会注进 B 的意图 prompt（跨访客串味），且共享文件的
读-改-写有并发竞态。改为会话私有进程内存储后两个问题一起消失；
user_profile.json 退为只读模板（dietary_preference / home_location 等），
运行时零文件写入。生产迁移 = 把键从会话 ID 换成账号 ID，机制不动。

（伴生退役：旧档案的 social_context_history 字段全系统无读者，随文件写路径
一并退役，不再累积；`mock_data_runtime/` 运行副本护栏因"不再写文件"失去
保护对象，一并移除。）

【设计纪律（spec algorithm-redesign R5，键变机制不动）】

- **永不阻断主流程**：失败 / cancel / 无会话身份 → log warning，不抛异常
- **隐私脱敏**：summary 由 LLM 生成时 prompt 显式约束「不出现具体年龄数字
  （5 岁 → 学龄前儿童）/ 不出现具体地址 / 经纬度」
- **幂等键**：social_context + 5 分钟 timestamp 窗口（同 session 重复不追加，
  在 memory_store.record_recent_trip 写入点原子执行）
- **5 条上限**：写入时丢弃 5 条之外的旧记录（保 LIFO 顺序，同上原子执行）

【触发点：confirm 路径，绑定下单动作而非方案就绪】

触发者是 `graph/nodes/execute_finalize.py:_persist_memory_side_effect`（用户确认后的
下单/购票/加购执行节点），在执行成功后作为副作用调 `persist_memory(state)`。
失败时 try/except 包裹不阻断 execute_finalize 主输出。

语义关键：记忆绑定的是用户「确认下单」这一动作，**不是 plan-ready（方案就绪）**——
方案没确认就持久化偏好是错误的产品语义。早期版本曾挂在 narrate_node（方案就绪即写），
后迁到 confirm 路径；narrate 节点现仅算 pending_actions、不再触发记忆回写
（交叉引用 `graph/nodes/narrate.py:226`）。

不负责：
- 存储与幂等/上限策略的原子执行（在 data/memory_store.py 的 recent_trips 区）
- 跨 session 持久化数据库（生产换账号键时接 Postgres，机制不动）
- 复杂 RAG 检索（单 session 召回最近 1-2 条已足够；不引入 vector 索引）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from agent.core.llm_client import LLMMessage, MIMO_THINKING_DISABLED_EXTRA_BODY
from data.memory_store import record_recent_trip
from schemas.domain import RecentTrip
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# 摘要 prompt（脱敏由 prompt 显式约束 + LLM 自觉）
_SUMMARIZE_PROMPT = """你是一个本地生活行程摘要助手。用 1-2 句中文描述这次行程，
让用户下次回顾时能快速理解。

【脱敏要求（必须严格遵守）】
- ❌ 不出现具体年龄数字（如「5 岁」），用「学龄前儿童」/「学童」/「老人」/「成年朋友」
- ❌ 不出现具体地址 / 经纬度
- ❌ 不出现具体景点 / 餐厅名（只描述类型，如「亲子博物馆」/「健康轻食餐厅」）
- ✓ 描述场景（家庭日常 / 情侣约会等）+ 整体节奏（轻松 / 紧凑）+ 主活动类型

【输出长度】50-150 字
【格式】纯中文文本，无 JSON / Markdown
"""


# ============================================================
# 主接口
# ============================================================


def persist_memory(
    state: dict[str, Any],
    *,
    client: Any | None = None,
) -> bool:
    """把当前行程结果写进本会话的行程档案（memory_store 会话私有区）。

    Args:
        state: AgentState dict（含 intent / itinerary / user_decision / session_id）
        client: LLMClient 用于生成 summary；None 时懒加载 + 摘要兜底

    Returns:
        True 表示成功写入；False 表示跳过 / 失败（永不抛异常）

    设计纪律：
    - 永不抛异常：任何错误都 catch 后 return False
    - 无会话身份不写：state 缺 session_id 时跳过（会话即身份，没有身份就没有归属）
    - cancel / 失败方案不写入：state.get("user_decision") == "cancel" 时跳过
    - 5 分钟幂等：同 session 同 social_context 窗口内重复不追加
    """
    try:
        return _persist_memory_impl(state, client=client)
    except Exception as exc:
        logger.warning("memory_writer: persist_memory failed: %s", exc)
        return False


def _persist_memory_impl(state: dict[str, Any], *, client: Any | None) -> bool:
    intent = state.get("intent")
    itinerary = state.get("itinerary")

    if intent is None or itinerary is None:
        return False

    # 会话即身份：没有 session_id 就没有归属，不写（读写分离批不变式）
    session_key = state.get("session_id")
    if not session_key:
        logger.info("memory_writer: skip persist（无 session_id，无会话身份）")
        return False

    # cancel / 未确认 / 失败方案不写入
    user_decision = state.get("user_decision")
    success = bool(user_decision == "confirm")

    # 不强制要求 user_decision == "confirm" —— 我们用 success=False 标记尚未
    # 下单的草案，让 recent_trips 也能记录草稿（但 cancel 跳过）
    if user_decision == "cancel":
        return False

    social_context = getattr(intent, "social_context", "") or ""
    if not social_context:
        # social_context 是召回 key，缺失则没有意义
        return False

    # 生成脱敏 summary
    summary = _summarize_trip(itinerary, intent, client=client)
    if not summary:
        return False

    # 写入会话私有档案（幂等窗 + 5 条上限在写入点原子执行）
    trip = RecentTrip(
        timestamp=_now_iso(),
        social_context=social_context,
        summary=summary,
        success=success,
    )
    written = record_recent_trip(str(session_key), trip)
    if not written:
        logger.info("memory_writer: skip duplicate within 5min window")
    return written


# ============================================================
# Helpers
# ============================================================


def _now_iso() -> str:
    """ISO 8601 时间戳（UTC，含 Z 后缀）"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _summarize_trip(
    itinerary: Itinerary,
    intent: IntentExtraction,
    *,
    client: Any | None = None,
) -> Optional[str]:
    """LLM 生成脱敏摘要；失败时返简单兜底文本（永不返 None 让流程过不去）"""
    # 构造给 LLM 的轻量描述
    nodes_desc: list[str] = []
    for node in itinerary.nodes:
        if node.target_kind == "home":
            continue
        kind_map = {"poi": "活动", "restaurant": "用餐"}
        type_label = kind_map.get(node.target_kind, node.target_kind)
        nodes_desc.append(f"{type_label}({node.duration_min}min)")
    nodes_str = " → ".join(nodes_desc) if nodes_desc else "未生成行程"

    fallback_summary = (
        f"{intent.social_context}场景行程：{nodes_str}；"
        f"总时长约 {itinerary.total_minutes // 60}小时。"
    )

    # stub 模式短路
    if client is not None and getattr(client, "provider", None) == "stub":
        return fallback_summary

    if client is None:
        try:
            from agent.core.llm_client import get_llm_client

            client = get_llm_client()
        except Exception:
            return fallback_summary

    if getattr(client, "provider", None) == "stub":
        return fallback_summary

    user_msg = (
        f"【场景】{intent.social_context}\n"
        f"【行程节点序列】{nodes_str}\n"
        f"【总时长】{itinerary.total_minutes}min\n\n"
        f"请输出 50-150 字脱敏摘要。"
    )

    try:
        resp = client.chat(
            [
                LLMMessage(role="system", content=_SUMMARIZE_PROMPT),
                LLMMessage(role="user", content=user_msg),
            ],
            temperature=0.4,
            # A6（2026-07-04）：关思考模式——只要 50-150 字摘要文本，思考 token
            # 挤占输出预算会把正文截空（narrator.py 有同款事故根因记录）。
            # 本调用不喂用户原始文本（输入是脱敏后的结构化行程描述），不必 wrap。
            extra_body=MIMO_THINKING_DISABLED_EXTRA_BODY,
        )
        text = (resp.content or "").strip()
        if not text:
            return fallback_summary
        # 长度上限保护
        return text[:500]
    except Exception as exc:
        logger.warning("memory_writer: LLM summarize failed: %s", exc)
        return fallback_summary


__all__ = ["persist_memory"]
