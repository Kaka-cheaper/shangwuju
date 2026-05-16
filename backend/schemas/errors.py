"""errors —— Tool 失败原因枚举。

收敛 reason 字段，避免下游写出 `reason="餐厅满了"` 这种自由字符串。
对应 `pitfalls.md` 异常分支 E1-E4。

不负责：
- 业务级错误响应包装（那是 Tool / API 层的事）。
"""

from enum import Enum


class FailureReason(str, Enum):
    """Tool 与 Agent 之间共享的失败原因。

    命名遵循 SCREAMING_SNAKE_CASE。值用 snake_case 字符串以便序列化为 JSON。
    """

    # E1：餐厅没位
    RESTAURANT_FULL = "restaurant_full"
    # E2：门票售罄
    TICKET_SOLD_OUT = "ticket_sold_out"
    # E3：距离超限
    DISTANCE_EXCEEDED = "distance_exceeded"
    # E4：总时长超限
    DURATION_EXCEEDED = "duration_exceeded"
    # 资源不存在
    NOT_FOUND = "not_found"
    # 候选集为空（约束过严）
    EMPTY_CANDIDATES = "empty_candidates"
    # 输入校验失败（schema / 参数错误）
    INVALID_INPUT = "invalid_input"
    # 上游服务失败（LLM / 数据库 / Mock 加载等）
    UPSTREAM_FAILURE = "upstream_failure"
