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

# ============================================================
# collection-time 提前锁定 SHANGWUJU_MOCK_DIR（望京数据集切换批，2026-07-10 实测发现）
# ============================================================
#
# 背景：pytest 在**收集阶段**（跑任何 fixture 之前）就会 import 每个测试模块；
# 若某模块在 import 时（模块级代码，不在函数体内）就调用了 `data.loader` 系
# 的函数（如 `test_critic_phase_a_characterization.py` 的模块级 `_BATTERY = [...]`
# 直接调 `lookup_hop()`/`load_user_profile()` 构造固定夹具），这些调用发生在
# `_isolate_tools_and_loader_cache` fixture 第一次运行**之前**——此时若
# `SHANGWUJU_MOCK_DIR` 还未设置，`data.loader._mock_dir()` 回退到默认值
# `<repo>/mock_data/`（仓库顶层，望京数据集切换后就是望京活集，不再是杭州）。
# 该模块级代码于是拿望京数据集算出的 hop 分钟数（R002 等杭州专属 ID 在望京集
# 里不存在，`lookup_hop` 4 级降级到底给出固定兜底值）钉进夹具，与稍后真正
# 跑测试时（此时 fixture 已把 env 指对杭州归档，重新算出真实值）产生不一致，
# 触发 `hop_infeasible` 等虚假违规——过去顶层 mock_data/ 与隔离拷贝内容逐字节
# 相同，这个"两次算出不同值"的缺口从未被撞见。
#
# 修法：在 conftest **模块级**（比任何 fixture 都早，紧跟 sys.path 设置之后）
# 就把 SHANGWUJU_MOCK_DIR 指向杭州归档目录本身——collection 阶段和实际测试
# 运行阶段读的是同一份数据，不会再算出两个不同答案。session fixture
# `_isolated_mock_dir` 仍然把它拷贝到临时目录供实际测试运行期使用（隔离写
# 污染的纵深防御不变），只是 collection 阶段这个更早的窗口也不再读到错误
# 数据源。
os.environ["SHANGWUJU_MOCK_DIR"] = str(_REPO_ROOT / "mock_data" / "hangzhou")

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
    """把杭州归档数据集复制到 session 临时目录；存量测试的 mock 读写都走副本。

    历史根因：memory_writer 曾把 recent_trips 写回 user_profile.json，指向活体
    仓库 mock_data/ 会污染版本控制的种子。记忆身份读写分离批（2026-07-05）后
    memory_writer 已改写进程内会话私有存储、运行时零文件写——本隔离保留作为
    纵深防御（任何未来写者都打不到真种子），且个别测试仍用 monkeypatch.setenv
    覆盖到自己的 tmp 构造窄目录场景。

    拷贝源改指 `mock_data/hangzhou/`（望京数据集切换批，2026-07-10）：`mock_data/`
    顶层现在是望京现场演示集，1700+ 存量测试逐字锚定杭州实体 ID，拷贝源改指
    杭州全套归档，存量测试原样全绿。
    """
    import shutil

    dst = tmp_path_factory.mktemp("mock_data_copy")
    shutil.copytree(_REPO_ROOT / "mock_data" / "hangzhou", dst, dirs_exist_ok=True)
    return dst


def _reset_all_mock_caches() -> None:
    """清空所有跨模块缓存了 mock 数据派生结果的 lru_cache。

    `data.loader.reset_cache()` 只清 loader 自己的 4 个 lru_cache，但
    `agent.planning.commute.lookup_hop` 另有 3 个模块级 `lru_cache(maxsize=1)`
    （`_route_index`/`_poi_coord_index`/`_restaurant_coord_index`，均从
    `load_routes()/load_pois()/load_restaurants()` 物化而来），`agent.planning.
    blueprint.demand_scope._dining_cuisines` 同理。每个测试前后都清一遍，
    防止任何测试残留的模块级缓存跨测试污染（`test_assemble_blueprint.py`
    已用同名局部 fixture 自证过 `lookup_hop` 这个缺口的存在）。
    """
    from data.loader import reset_cache as _reset_loader_cache

    _reset_loader_cache()
    try:
        from agent.planning.commute.lookup_hop import reset_cache as _reset_hop_cache

        _reset_hop_cache()
    except ImportError:
        pass
    try:
        from agent.planning.blueprint.demand_scope import _dining_cuisines

        _dining_cuisines.cache_clear()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _isolate_tools_and_loader_cache(request, _isolated_mock_dir):
    os.environ["SHANGWUJU_MOCK_DIR"] = str(_isolated_mock_dir)
    # 测试默认走 stub LLM 客户端，避免误调真 endpoint 或因缺 API key 失败
    os.environ.setdefault("LLM_PROVIDER", "stub")
    _reset_all_mock_caches()

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

    _reset_all_mock_caches()
