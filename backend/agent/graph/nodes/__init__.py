"""agent.graph.nodes —— LangGraph 各节点实现。

每个节点是一个 module，导出 `node()` 函数（同步或异步），签名：

    def node(state: AgentState) -> dict[str, Any]:
        ...
        return {"key1": value1, "key2": value2}  # State diff

LangGraph 自动 merge diff 到 State。
"""
