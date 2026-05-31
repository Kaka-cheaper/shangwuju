# Design Document

## Overview

修复「反馈无用」故障。核心思路：把「是否反馈」的判断从「关键词词典 + 字数硬限」升级为「has_itinerary 上下文 + 扩充词典 + LLM 语义感知」三者协同，同时消除 checkpointer 反序列化隐患。不动 LangGraph 拓扑（遵守 AGENTS.md 编排冻结纪律）。

只改 4 处：
- `router_node` 三层判定逻辑（graph/nodes 层）
- `feedback_detector` 词典（core 层）
- `classify_input` 增加上下文参数 + router prompt 增加上下文说明（intent 层）
- checkpointer 构造加类型注册（graph/build.py 内 InMemorySaver 初始化，不动拓扑）

## Architecture

### 现状三层判定（修复前）

```text
router_node(state)
  Layer 1: _looks_like_feedback(state)
            = has_itinerary AND feedback_detector(text)   # 词典窄 -> 漏判
  Layer 2: classify_input(text, client)                   # 看不到 has_itinerary
  Layer 3: has_itinerary AND len<15 AND kind in (ambiguous,chitchat)  # 字数武断
```

### 目标三层判定（修复后）

```text
router_node(state)
  has_itinerary = bool(state.get("itinerary"))

  Layer 1（强信号快速命中，不调 LLM）:
            has_itinerary AND feedback_detector(text)
            扩充后词典覆盖语义类措辞 -> 命中即 feedback

  Layer 2（LLM 语义分类，带上下文）:
            classify_input(text, client, has_itinerary=has_itinerary)
            prompt 告知 LLM「用户已有方案」-> LLM 能判 feedback

  Layer 3（兜底，放宽）:
            has_itinerary AND kind != planning
            倾向 feedback（去掉 <15字 限制；planning 明确时不吞）
```

关键变化：
- Layer 1 词典扩充（R2）
- Layer 2 新增 has_itinerary 入参，让 LLM 感知上下文（R3）
- Layer 3 去掉字数限制，改为「has_itinerary 且非明确 planning -> feedback」（R1 + R3），保留 planning 强信号优先（R4）

## Components and Interfaces

### C1: feedback_detector 词典扩充（R2）

文件：backend/agent/core/feedback_detector.py

在 _FEEDBACK_KEYWORDS 增加语义类反馈词（不带数字单位的口语表达）：

```text
| 新增分类       | 词                                          |
|--------------|--------------------------------------------|
| 节奏/强度类    | 节奏, 太赶, 赶, 轻松, 悠闲, 慢一点, 紧凑, 太满, 满  |
| 主观评价类    | 不太好, 不喜欢, 一般, 没意思, 优雅, 高级, 普通      |
| 疲惫类（补全）  | 有点累 / 太累 的语义                          |
```

边界（R2.3 不误伤新需求）：
- 「想轻松点」「太赶了」这类短句才是反馈强信号
- 「今天下午想带孩子出去玩」含「想」但不含反馈词 -> 仍 False
- 词典命中是 necessary signal，caller（router_node）仍须配合 has_itinerary 判断

### C2: classify_input 增加上下文参数（R3）

文件：backend/agent/intent/router.py

签名变更（向后兼容，新增可选参数）：

```python
def classify_input(
    user_input: str,
    *,
    client: LLMClient,
    has_itinerary: bool = False,   # 新增：当前 session 是否已有方案
) -> RouterDecision:
```

实现：has_itinerary=True 时在 messages 追加一条上下文提示（user 前缀），告知 LLM「用户已经有一份行程方案，这次输入很可能是对方案的反馈或调整」。

风险隔离：has_itinerary=False（无方案）时行为与现状完全一致（R6.4），不影响首轮规划/闲聊分类。

### C3: router prompt 增加反馈上下文说明（R3）

文件：backend/agent/intent/prompts/router_prompt.py

新增一个上下文前缀常量（仅 has_itinerary 时注入），说明：
- 用户已有方案
- 「太赶/想轻松/换一个/不太好」等是对方案的反馈，倾向判 ambiguous（让 router_node Layer 3 接管为 feedback）
- 明确的全新场景（「换成和朋友打球」「带爸妈吃饭」）仍是 planning（R4）

不改 6 类主 prompt 结构（feedback 不进 InputKind 枚举——feedback 由 router_node 在 LangGraph 层判定）。LLM 仍输出 6 类，router_node 用「has_itinerary + LLM 判非 planning」组合推断 feedback。

### C4: router_node 三层重构（R1 + R3 + R4）

文件：backend/agent/graph/nodes/router.py

```python
def router_node(state):
    user_input = state.get("user_input") or ""
    has_itinerary = bool(state.get("itinerary"))

    # Layer 1: 强信号词典（has_itinerary 前提）
    if has_itinerary and looks_like_feedback(user_input):
        return {"route_kind": "feedback", "router_decision": None}

    # Layer 2: LLM 分类（带 has_itinerary 上下文）
    client = get_llm_client()
    try:
        decision = classify_input(user_input, client=client, has_itinerary=has_itinerary)
    except Exception:
        decision = fallback_decision(user_input)
    route_kind = decision.input_kind

    # Layer 3: 兜底（放宽：has_itinerary 且非明确 planning -> feedback）
    if has_itinerary and route_kind != "planning":
        return {"route_kind": "feedback", "router_decision": None}

    return {"router_decision": decision, "route_kind": route_kind}
```

R4 保障：LLM 判 planning（明确新需求）-> 不进 Layer 3 -> 仍走 planning。
风险：若 LLM 把真新需求判成 ambiguous，会被 Layer 3 吞成 feedback。缓解——C3 的 prompt 明确教 LLM「全新场景判 planning」，降低误判。

### C5: checkpointer 类型注册（R5）

文件：backend/agent/graph/build.py

InMemorySaver() 默认 msgpack serde。需注册 AgentState 出现的 Pydantic 业务类型，消除 unregistered 警告。

方案（按 langgraph 版本 API 择一，tasks 落地时验证）：
- 优先：用 langgraph.checkpoint.serde 的 allowed 模块注册机制
- 或：构造 InMemorySaver(serde=...) 传入注册了业务模块的 serializer

涉及类型（实测日志）：

```text
schemas.domain.Poi / Restaurant
schemas.intent.IntentExtraction
schemas.itinerary.Itinerary
schemas.router.InputKind / RouterDecision
schemas.tools.GetUserProfileOutput
agent.planning.critic._rules.types.ViolationCode / Severity / Violation
```

不改 AgentState schema 字段（R5.3）。

## Data Models

无新增 schema。复用现有 RouterDecision / IntentExtraction / AgentState。classify_input 仅加入参，返回类型不变。

## Error Handling

```text
| 场景                          | 处理                                      |
|------------------------------|------------------------------------------|
| LLM router 调用失败           | fallback_decision（判 planning）；但 Layer 3 仍会在 has_itinerary 时改判 feedback |
| has_itinerary=False           | 全程走原逻辑，不进任何新分支（行为不变）     |
| checkpointer 注册 API 不存在  | tasks 阶段验证 langgraph 实际 API；失败则保留警告（不阻断功能）|
| feedback_detector 误判新需求   | Layer 2 LLM + R4 验收用例双重把关          |
```

## Testing Strategy

### 单元测试（feedback_detector）

backend/tests/test_feedback_detector.py（新增或扩充）：
- R2.2：7 个漏判措辞断言 True（太赶了/节奏太快/想轻松点/再优雅一点/行程太满了/能不能轻松些/这个不太好）
- R2.3：新需求措辞断言 False（今天下午想带孩子出去玩 / 周末带爸妈吃饭）

### 集成测试（router_node 三层）

backend/tests/test_router_node_feedback.py（新增）：
- R1：has_itinerary=True + 4 个长反馈 -> route_kind=feedback
- R4：has_itinerary=True + 2 个真新需求 -> route_kind=planning
- R6.4：has_itinerary=False + 任意输入 -> 行为与原逻辑一致

用 stub LLM client 隔离（避免真 LLM 慢 + 不确定性）；R3 的 LLM 上下文感知用 mock 验证 prompt 注入。

### 回归测试

- pytest -x -q --ignore=tests/test_browser 全过（R6.2）
- 前端 pnpm run verify:all 4/4（R6.3）；本 spec 不动前端，应自然通过
- checkpointer 跨 turn 端到端验证无警告（R5.2）

## Verification Plan

```text
| 需求 | 验证方式                                          |
|-----|--------------------------------------------------|
| R1  | test_router_node_feedback.py 长反馈用例           |
| R2  | test_feedback_detector.py 7 措辞 + 新需求用例      |
| R3  | router prompt 注入断言 + has_itinerary 路由用例    |
| R4  | test_router_node_feedback.py 新需求用例           |
| R5  | 跨 turn 调用日志无 unregistered 警告               |
| R6  | 全量 pytest + 强信号反馈用例 + 前端 verify          |
```
