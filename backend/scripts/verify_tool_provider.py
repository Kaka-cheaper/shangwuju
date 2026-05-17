"""verify_tool_provider —— Phase 0.11 抽象层自检脚本（Agent B 交付）。

跑 5 项端到端：
1. DATA_PROVIDER=mock     → search_pois 拿到 mock 数据
2. DATA_PROVIDER=gaode    → search_pois 抛 NotImplementedError 含友好提示
3. DATA_PROVIDER=dianping → 同上
4. observability.get_logger 能拿 logger；LOG_FORMAT=json 时输出 JSON
5. trace_span 正常情况记录 elapsed_ms，异常时记录 error_type 并重新抛

运行：
    cd backend && python -m scripts.verify_tool_provider
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
from contextlib import redirect_stdout

from agent.v2.observability import (
    bind_session_context,
    clear_session_context,
    get_logger,
    trace_span,
)
from agent.v2.tool_provider import (
    DianpingToolProviderStub,
    GaodeToolProviderStub,
    MockToolProvider,
    get_tool_provider,
)
from schemas.tools import SearchPoisInput


# ============================================================
# helpers
# ============================================================

def _set_env(name: str, value: str | None) -> str | None:
    """临时设置环境变量，返回原值用于还原。"""
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    return old


def _restore_env(name: str, old: str | None) -> None:
    if old is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = old


def _reset_observability_config() -> None:
    """清掉 observability._configure_once 的「已配置」标记，让下一次 get_logger 重读 LOG_FORMAT。"""
    from agent.v2 import observability

    if hasattr(observability._configure_once, "_done"):
        delattr(observability._configure_once, "_done")
    # 同时清 contextvars 与 structlog 缓存
    import structlog

    structlog.reset_defaults()


# ============================================================
# 5 个验证项
# ============================================================

async def check_1_mock_provider() -> tuple[bool, str]:
    """DATA_PROVIDER=mock：search_pois 能拿到 mock 数据。"""
    label = "[1] DATA_PROVIDER=mock → search_pois 拿到 mock 数据"
    old = _set_env("DATA_PROVIDER", "mock")
    try:
        provider = get_tool_provider()
        if not isinstance(provider, MockToolProvider):
            return False, f"  ✗ {label}\n      provider 类型错误：{type(provider).__name__}"

        out = await provider.search_pois(
            SearchPoisInput(distance_max_km=10.0, limit=5)
        )
        if not out.success:
            return False, f"  ✗ {label}\n      success=False reason={out.reason}"
        if not out.candidates:
            return False, f"  ✗ {label}\n      候选为空"
        return True, (
            f"  ✓ {label}\n"
            f"      provider=MockToolProvider name={provider.name} "
            f"返回 {len(out.candidates)} 个 POI（如 {out.candidates[0].name}）"
        )
    finally:
        _restore_env("DATA_PROVIDER", old)


async def check_2_gaode_stub() -> tuple[bool, str]:
    """DATA_PROVIDER=gaode：search_pois 抛 NotImplementedError 含友好提示。"""
    label = "[2] DATA_PROVIDER=gaode → 抛 NotImplementedError 含文档锚点"
    old = _set_env("DATA_PROVIDER", "gaode")
    try:
        provider = get_tool_provider()
        if not isinstance(provider, GaodeToolProviderStub):
            return False, f"  ✗ {label}\n      provider 类型错误：{type(provider).__name__}"

        try:
            await provider.search_pois(SearchPoisInput(distance_max_km=5.0))
        except NotImplementedError as e:
            msg = str(e)
            # 友好提示必须含文档锚点 + 服务名
            if "Gaode integration" not in msg or "数据源切换路径" not in msg:
                return False, (
                    f"  ✗ {label}\n      错误消息缺关键字（应含 'Gaode integration' 与 '数据源切换路径'）：\n      {msg}"
                )
            return True, f"  ✓ {label}\n      错误消息：{msg[:80]}..."
        else:
            return False, f"  ✗ {label}\n      未抛 NotImplementedError"
    finally:
        _restore_env("DATA_PROVIDER", old)


async def check_3_dianping_stub() -> tuple[bool, str]:
    """DATA_PROVIDER=dianping：同上抛错含文档锚点。"""
    label = "[3] DATA_PROVIDER=dianping → 抛 NotImplementedError 含文档锚点"
    old = _set_env("DATA_PROVIDER", "dianping")
    try:
        provider = get_tool_provider()
        if not isinstance(provider, DianpingToolProviderStub):
            return False, f"  ✗ {label}\n      provider 类型错误：{type(provider).__name__}"

        try:
            await provider.search_restaurants.__wrapped__ if False else None  # noqa: E711
            from schemas.tools import SearchRestaurantsInput

            await provider.search_restaurants(SearchRestaurantsInput(distance_max_km=5.0))
        except NotImplementedError as e:
            msg = str(e)
            if "Dianping integration" not in msg or "数据源切换路径" not in msg:
                return False, (
                    f"  ✗ {label}\n      错误消息缺关键字（应含 'Dianping integration' 与 '数据源切换路径'）：\n      {msg}"
                )
            return True, f"  ✓ {label}\n      错误消息：{msg[:80]}..."
        else:
            return False, f"  ✗ {label}\n      未抛 NotImplementedError"
    finally:
        _restore_env("DATA_PROVIDER", old)


def check_4_logger_and_json() -> tuple[bool, str]:
    """observability.get_logger 能拿 logger；LOG_FORMAT=json 时输出 JSON 行。"""
    label = "[4] observability.get_logger / LOG_FORMAT=json → JSON 行输出"
    old = _set_env("LOG_FORMAT", "json")
    try:
        _reset_observability_config()
        log = get_logger("verify.test")

        buf = io.StringIO()
        with redirect_stdout(buf):
            bind_session_context(session_id="sess_verify", turn_id="t1", user_id="demo")
            log.info("verify.event", extra="hello", count=42)
            clear_session_context()

        output = buf.getvalue().strip()
        if not output:
            return False, f"  ✗ {label}\n      logger 没输出"

        # 必须是合法 JSON 行（一行一个对象）
        first_line = output.splitlines()[0]
        try:
            obj = json.loads(first_line)
        except json.JSONDecodeError:
            return False, f"  ✗ {label}\n      LOG_FORMAT=json 但输出不是 JSON：\n      {first_line[:120]}"

        # 字段断言
        required = {"event", "level", "timestamp", "session_id", "turn_id", "user_id", "extra", "count"}
        missing = required - obj.keys()
        if missing:
            return False, f"  ✗ {label}\n      JSON 缺字段：{missing}\n      实际：{obj}"
        if obj["session_id"] != "sess_verify" or obj["count"] != 42:
            return False, f"  ✗ {label}\n      字段值错误：{obj}"

        return True, (
            f"  ✓ {label}\n"
            f"      JSON 行 keys={sorted(obj.keys())[:6]}... session_id={obj['session_id']} count={obj['count']}"
        )
    finally:
        _restore_env("LOG_FORMAT", old)
        _reset_observability_config()


def check_5_trace_span() -> tuple[bool, str]:
    """trace_span 正常情况记录 elapsed_ms，异常时记录 error_type 并重新抛。"""
    label = "[5] trace_span 正常 → end+elapsed_ms / 异常 → error+error_type 并 raise"
    old = _set_env("LOG_FORMAT", "json")
    try:
        _reset_observability_config()

        # 正常路径
        buf = io.StringIO()
        with redirect_stdout(buf):
            with trace_span("verify_op", tool="search_pois"):
                pass

        lines = [json.loads(line) for line in buf.getvalue().strip().splitlines() if line.strip()]
        if len(lines) < 2:
            return False, f"  ✗ {label}\n      正常路径事件数 < 2：\n      {lines}"

        if lines[0]["event"] != "verify_op.start" or lines[-1]["event"] != "verify_op.end":
            return False, f"  ✗ {label}\n      事件名错：{[l['event'] for l in lines]}"

        if "elapsed_ms" not in lines[-1]:
            return False, f"  ✗ {label}\n      end 事件缺 elapsed_ms：{lines[-1]}"

        # 异常路径
        buf2 = io.StringIO()
        raised = False
        try:
            with redirect_stdout(buf2):
                with trace_span("verify_fail", scope="x"):
                    raise RuntimeError("planned failure")
        except RuntimeError as e:
            raised = True
            if "planned failure" not in str(e):
                return False, f"  ✗ {label}\n      异常消息错：{e}"

        if not raised:
            return False, f"  ✗ {label}\n      trace_span 吞了异常，未重新抛"

        err_lines = [json.loads(line) for line in buf2.getvalue().strip().splitlines() if line.strip()]
        err_event = next((l for l in err_lines if l["event"] == "verify_fail.error"), None)
        if err_event is None:
            return False, f"  ✗ {label}\n      未找到 verify_fail.error 事件：{err_lines}"

        if err_event.get("error_type") != "RuntimeError" or "elapsed_ms" not in err_event:
            return False, f"  ✗ {label}\n      error 事件字段错：{err_event}"

        return True, (
            f"  ✓ {label}\n"
            f"      正常路径 elapsed_ms={lines[-1]['elapsed_ms']}; "
            f"异常路径 error_type={err_event['error_type']} elapsed_ms={err_event['elapsed_ms']}"
        )
    finally:
        _restore_env("LOG_FORMAT", old)
        _reset_observability_config()


# ============================================================
# 入口
# ============================================================

async def _run_async() -> list[tuple[bool, str]]:
    return [
        await check_1_mock_provider(),
        await check_2_gaode_stub(),
        await check_3_dianping_stub(),
    ]


def main() -> int:
    print("=== Phase 0.11 ToolProvider + Observability 自检 ===")
    print()

    results: list[tuple[bool, str]] = []

    # 1-3：异步 provider 检查
    results.extend(asyncio.run(_run_async()))
    # 4-5：同步 observability 检查
    results.append(check_4_logger_and_json())
    results.append(check_5_trace_span())

    for ok, msg in results:
        print(msg)

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print()
    print(f"=== 通过 {passed}/{total} ===")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
