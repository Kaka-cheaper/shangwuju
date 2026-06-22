"""tests.conftest —— pytest 共享 fixture。

职责：
1. 让 `import schemas` / `import data` / `import tools` 命中 backend/ 下的包
2. 锁定 SHANGWUJU_MOCK_DIR，使所有测试一律走仓库根 mock_data/
3. 每次测试自动重置 data.loader 的 lru_cache（避免不同测试相互污染）
4. 按文件名区分两套 Tool 注册策略：
   - test_agent_flow.py / test_intent_parser.py → 注册 fake_tools（A 同学端到端用）
   - 其它测试（如 test_tools.py）→ 用真实 Tool 实现 + 真 mock 数据

切换策略：在每个测试前为 TOOL_REGISTRY 拍快照，注册 fake；teardown 时回滚到快照。
保证两类测试在同一 pytest 会话里互不污染。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_ROOT.parent
_TESTS_DIR = Path(__file__).resolve().parent

# 让 `import schemas` / `import data` / `import tools` 直接命中 backend/ 下的包
sys.path.insert(0, str(_BACKEND_ROOT))

# 在 conftest 加载时一次性触发真 Tool 注册（副作用 import）
import tools as _real_tools  # noqa: E402,F401


_FAKE_TEST_FILES: set[str] = set()  # A 同学评估后决定全部用真 mock 数据；保留 fake_tools.py 备未来用


def _load_fake_tools_module():
    """按文件路径动态加载 fake_tools.py，避开 tests 是否为包的歧义。"""
    if "_shangwuju_fake_tools" in sys.modules:
        return sys.modules["_shangwuju_fake_tools"]
    fake_path = _TESTS_DIR / "fake_tools.py"
    spec = importlib.util.spec_from_file_location(
        "_shangwuju_fake_tools", fake_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 fake_tools: {fake_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_shangwuju_fake_tools"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session", autouse=True)
def _isolated_mock_dir(tmp_path_factory):
    """把仓库 mock_data 复制到 session 临时目录；所有测试的 mock 读写都走副本。

    根因：memory_writer（narrate 副作用）会把 recent_trips 写回 user_profile.json，
    旧 conftest 把 SHANGWUJU_MOCK_DIR 指向活体仓库 mock_data/，导致跑测试会污染
    版本控制的 mock 数据（recent_trips 被测试场景刷掉）。指向副本后真目录永不被写。
    读照旧（副本同内容）；个别测试仍可用 monkeypatch.setenv 覆盖到自己的 tmp。
    """
    import shutil

    dst = tmp_path_factory.mktemp("mock_data_copy")
    shutil.copytree(_REPO_ROOT / "mock_data", dst, dirs_exist_ok=True)
    return dst


@pytest.fixture(autouse=True)
def _isolate_tools_and_loader_cache(request, _isolated_mock_dir):
    os.environ["SHANGWUJU_MOCK_DIR"] = str(_isolated_mock_dir)
    # 测试默认走 stub LLM 客户端，避免误调真 endpoint 或因缺 API key 失败
    os.environ.setdefault("LLM_PROVIDER", "stub")
    from data.loader import reset_cache

    reset_cache()

    use_fake = request.node.path.name in _FAKE_TEST_FILES
    if use_fake:
        from tools.registry import TOOL_REGISTRY

        fake_mod = _load_fake_tools_module()
        snapshot = dict(TOOL_REGISTRY)
        fake_mod.register_fake_tools()
        try:
            yield
        finally:
            TOOL_REGISTRY.clear()
            TOOL_REGISTRY.update(snapshot)
    else:
        yield

    reset_cache()
