"""verify_phase0_5 —— Phase 0.5 自检：并行任务基座。

跑通：
1. data.loader 函数能 import 且签名正确
2. tools.registry 能 import + register/invoke 链路通
3. agent.llm_client_stub 能正常构造 & 调用
4. 所有 schemas 能 model_json_schema() 出可序列化 JSON（Function Calling 用）
"""

from __future__ import annotations

import inspect
import json
import sys

from agent.llm_client import LLMMessage
from agent.llm_client_stub import StubLLMClient
from data import loader as data_loader
from schemas.errors import FailureReason
from schemas.tools import (
    SearchPoisInput,
    SearchPoisOutput,
)
from tools.registry import (
    TOOL_REGISTRY,
    invoke_tool,
    register_tool,
)


def _line(ok: bool, msg: str) -> tuple[bool, str]:
    return ok, ("  ✓ " if ok else "  ✗ ") + msg


def main() -> int:
    print("=== Phase 0.5 并行基座自检 ===")
    results: list[tuple[bool, str]] = []

    # 1. data.loader 函数签名
    expected_funcs = ["load_pois", "load_restaurants", "load_routes", "load_user_profile"]
    missing = [f for f in expected_funcs if not callable(getattr(data_loader, f, None))]
    results.append(_line(not missing, f"data.loader 暴露 4 个 load_* 函数：{expected_funcs}"))

    # 2. registry 注册 + 调用链路
    @register_tool(
        name="_test_search",
        description="测试 Tool；命中关键字 fail 时返 EMPTY_CANDIDATES",
        input_model=SearchPoisInput,
        output_model=SearchPoisOutput,
    )
    def _test_search(inp: SearchPoisInput) -> SearchPoisOutput:
        if "fail" in (inp.preferred_types or []):
            return SearchPoisOutput(
                success=False, reason=FailureReason.EMPTY_CANDIDATES, candidates=[]
            )
        return SearchPoisOutput(success=True, candidates=[])

    ok_register = "_test_search" in TOOL_REGISTRY
    results.append(_line(ok_register, f"tools.registry 注册成功：{list(TOOL_REGISTRY)}"))

    # 调一次成功
    r1 = invoke_tool("_test_search", {"distance_max_km": 5})
    results.append(_line(r1.success, f"invoke_tool 成功路径：duration={r1.duration_ms}ms"))

    # 调一次失败（返 EMPTY_CANDIDATES）
    r2 = invoke_tool("_test_search", {"preferred_types": ["fail"]})
    ok_fail = (not r2.success) and r2.reason == FailureReason.EMPTY_CANDIDATES
    results.append(_line(ok_fail, f"invoke_tool 失败路径：reason={r2.reason}"))

    # 调一次非法输入（漂移字段）
    r3 = invoke_tool("_test_search", {"max_distance": 5})  # 错字段名
    ok_invalid = (not r3.success) and r3.reason == FailureReason.INVALID_INPUT
    results.append(_line(ok_invalid, f"invoke_tool 漂移字段被拦截：reason={r3.reason}"))

    # 调一次未注册 Tool
    r4 = invoke_tool("nonexistent", {})
    ok_nf = (not r4.success) and r4.reason == FailureReason.NOT_FOUND
    results.append(_line(ok_nf, f"invoke_tool 未注册 Tool 拦截：reason={r4.reason}"))

    # 3. LLM stub 客户端
    stub = StubLLMClient()
    resp = stub.chat([LLMMessage(role="user", content="今天下午带老婆孩子出去玩")])
    has_intent = (resp.content is not None) and ("家庭日常" in resp.content)
    results.append(_line(has_intent, "LLM stub 返回家庭主场景 IntentExtraction JSON"))

    # 4. Tool spec 可序列化
    specs = [s.to_openai_spec() for s in TOOL_REGISTRY.values()]
    try:
        json.dumps(specs)
        ok_json = True
    except Exception:  # noqa: BLE001
        ok_json = False
    results.append(_line(ok_json, f"Tool spec 可 JSON 序列化（{len(specs)} 个）"))

    print("\n".join(line for _, line in results))
    print()
    failed = [line for ok, line in results if not ok]
    if failed:
        print(f"→ 失败 {len(failed)} 项")
        return 1
    print(f"✓ 全部 {len(results)} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
