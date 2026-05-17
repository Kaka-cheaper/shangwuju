"""verify_repository —— ConversationRepository 抽象层端到端验证（Phase 0.11）。

测试场景：
  1. SESSION_STORE=memory：get_or_create / save / get round-trip
  2. SESSION_STORE=redis ：所有方法抛 NotImplementedError 含「Milestone 2」字样
  3. 旧名兼容：ConversationStore / get_default_store 仍可 import 与工作
  4. 跨 user_id 切换：messages 被清空但 session_id 保留
  5. _reset_default_repo_for_tests 切换 backend：能在不重启进程的前提下从 memory 切到 redis

跑法：
  cd backend
  .venv\\Scripts\\python.exe -m scripts.verify_repository
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _set_session_store(value: str) -> None:
    """切换 env + 重置单例，让下次 get_default_repo() 按新 env 解析。"""
    from agent.v2.conversation import _reset_default_repo_for_tests

    os.environ["SESSION_STORE"] = value
    _reset_default_repo_for_tests()


async def case1_memory_round_trip() -> None:
    """case 1：memory backend 的 get_or_create / save / get / delete / stats round-trip。"""
    print("\n[case 1] SESSION_STORE=memory round-trip")
    _set_session_store("memory")

    from agent.v2.conversation import (
        ConversationRepository,
        ConversationState,
        get_default_repo,
    )

    repo = get_default_repo()
    print(f"  repo.name = {repo.name}")
    assert repo.name == "memory", f"期望 memory，实际 {repo.name}"
    assert isinstance(repo, ConversationRepository), "InMemoryRepository 应满足 Protocol"

    # 1) 不存在 → get 返 None
    s0 = await repo.get("verify_repo_001")
    assert s0 is None, f"未创建前 get 应返 None，实际 {s0}"

    # 2) get_or_create 后存在
    s1 = await repo.get_or_create("verify_repo_001", user_id="alice")
    assert isinstance(s1, ConversationState)
    assert s1.session_id == "verify_repo_001"
    assert s1.user_id == "alice"
    assert s1.messages == []
    print(f"  get_or_create 创建 session: {s1.session_id} / {s1.user_id}")

    # 3) save 写入快照
    s1.intent_snapshot = {"distance_max_km": 5}
    s1.itinerary_snapshot = {"summary": "测试方案"}
    await repo.save(s1)

    s2 = await repo.get("verify_repo_001")
    assert s2 is not None
    assert s2.intent_snapshot == {"distance_max_km": 5}
    assert s2.itinerary_snapshot == {"summary": "测试方案"}
    print(f"  save → get 快照一致：{s2.itinerary_snapshot}")

    # 4) stats
    stats = repo.stats()
    print(f"  stats = {stats}")
    assert stats.get("sessions", 0) >= 1

    # 5) delete
    await repo.delete("verify_repo_001")
    s3 = await repo.get("verify_repo_001")
    assert s3 is None, "delete 后应返 None"
    print("  delete 后 get 返 None ✓")

    print("  ✓ case 1 通过")


async def case2_redis_stub_raises() -> None:
    """case 2：SESSION_STORE=redis 所有写方法都抛 NotImplementedError 含 Milestone 2 字样。"""
    print("\n[case 2] SESSION_STORE=redis 所有方法抛 NotImplementedError")
    _set_session_store("redis")

    from agent.v2.conversation import (
        ConversationRepository,
        get_default_repo,
    )

    repo = get_default_repo()
    print(f"  repo.name = {repo.name}")
    assert repo.name == "redis", f"期望 redis，实际 {repo.name}"
    assert isinstance(repo, ConversationRepository), "RedisRepositoryStub 应满足 Protocol"

    # get_or_create
    try:
        await repo.get_or_create("any_id")
    except NotImplementedError as e:
        assert "Milestone 2" in str(e), f"提示语应含 Milestone 2，实际：{e}"
        print(f"  get_or_create 抛 NotImplementedError ✓ ({str(e)[:50]}...)")
    else:
        raise AssertionError("get_or_create 应该抛 NotImplementedError")

    # get
    try:
        await repo.get("any_id")
    except NotImplementedError:
        print("  get 抛 NotImplementedError ✓")
    else:
        raise AssertionError("get 应该抛 NotImplementedError")

    # save
    from agent.v2.conversation import ConversationState

    fake_state = ConversationState(session_id="x")
    try:
        await repo.save(fake_state)
    except NotImplementedError:
        print("  save 抛 NotImplementedError ✓")
    else:
        raise AssertionError("save 应该抛 NotImplementedError")

    # delete
    try:
        await repo.delete("x")
    except NotImplementedError:
        print("  delete 抛 NotImplementedError ✓")
    else:
        raise AssertionError("delete 应该抛 NotImplementedError")

    # stats（设计上 stub 不抛，返回 backend 标记，让 /health 之类能问 stats 不直接 500）
    stats = repo.stats()
    print(f"  stats（不抛）= {stats}")
    assert stats.get("backend") == "redis-stub"

    print("  ✓ case 2 通过")


async def case3_legacy_names_still_work() -> None:
    """case 3：旧名 ConversationStore / get_default_store 仍可 import 与工作。

    main.py / orchestrator.py 都在用这两个名字，不能破。
    """
    print("\n[case 3] 旧名 ConversationStore / get_default_store 兼容")
    _set_session_store("memory")

    # 旧风格 import
    from agent.v2.conversation import ConversationStore, get_default_store

    # ConversationStore 应能当类用（实例化）
    standalone = ConversationStore()
    print(f"  ConversationStore() 实例 .name = {standalone.name}")
    assert standalone.name == "memory"

    # 也能当作 backwards-compatible 单例 getter
    repo = get_default_store()
    print(f"  get_default_store() = {type(repo).__name__}")
    assert repo.name == "memory"

    # 实际操作能 round-trip
    state = await repo.get_or_create("verify_legacy_001", user_id="bob")
    state.itinerary_snapshot = {"summary": "兼容测试"}
    await repo.save(state)

    fetched = await repo.get("verify_legacy_001")
    assert fetched is not None
    assert fetched.itinerary_snapshot == {"summary": "兼容测试"}
    print(f"  旧名 round-trip OK：{fetched.itinerary_snapshot}")

    await repo.delete("verify_legacy_001")
    print("  ✓ case 3 通过")


async def case4_user_switch_clears_messages() -> None:
    """case 4：跨 user_id 切换时 messages 被清，session_id 保留。

    这是 Phase 0.7 用户切换语义的核心 —— 不同人对 Agent 的偏好不同，
    保留 session_id 让前端 sticky session 不抖，但消息历史必须清。
    """
    print("\n[case 4] 跨 user_id 切换：messages 清，session_id 保留")
    _set_session_store("memory")

    from agent.v2.conversation import _reset_default_repo_for_tests, get_default_repo
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    # 完全隔离：清掉单例 + 重新 set_session_store
    _reset_default_repo_for_tests()
    _set_session_store("memory")

    repo = get_default_repo()
    sid = "verify_user_switch_001"

    # alice 进
    s_a = await repo.get_or_create(sid, user_id="alice")
    s_a.messages.append(ModelRequest(parts=[UserPromptPart(content="alice 的话")]))
    s_a.itinerary_snapshot = {"summary": "alice 的方案"}
    await repo.save(s_a)
    assert len(s_a.messages) == 1
    print(f"  alice：messages={len(s_a.messages)} session_id={s_a.session_id}")

    # bob 切入同一 session_id
    s_b = await repo.get_or_create(sid, user_id="bob")
    print(f"  bob ：messages={len(s_b.messages)} session_id={s_b.session_id}")
    assert s_b.session_id == sid, "session_id 应保留"
    assert s_b.user_id == "bob"
    assert s_b.messages == [], f"切 user 应清 messages，实际 {len(s_b.messages)} 条"
    assert s_b.itinerary_snapshot is None, "切 user 应清快照"

    # alice 再切回（同样会清，因为 b 写了 bob）
    s_a2 = await repo.get_or_create(sid, user_id="alice")
    assert s_a2.user_id == "alice"
    assert s_a2.messages == []

    await repo.delete(sid)
    print("  ✓ case 4 通过")


async def case5_invalid_session_store() -> None:
    """case 5：SESSION_STORE 取非法值时 fail fast。"""
    print("\n[case 5] SESSION_STORE=postgres（未来支持）暂时应 fail fast")
    _set_session_store("postgres")

    from agent.v2.conversation import get_default_repo

    try:
        get_default_repo()
    except ValueError as e:
        msg = str(e)
        print(f"  ValueError: {msg}")
        assert "Unknown SESSION_STORE" in msg
        assert "postgres" in msg
    else:
        raise AssertionError("非法值应抛 ValueError")

    print("  ✓ case 5 通过")


async def _run() -> int:
    # 备份 env，跑完恢复，避免污染后续脚本
    original = os.environ.get("SESSION_STORE")
    try:
        await case1_memory_round_trip()
        await case2_redis_stub_raises()
        await case3_legacy_names_still_work()
        await case4_user_switch_clears_messages()
        await case5_invalid_session_store()
    finally:
        if original is None:
            os.environ.pop("SESSION_STORE", None)
        else:
            os.environ["SESSION_STORE"] = original
        from agent.v2.conversation import _reset_default_repo_for_tests

        _reset_default_repo_for_tests()

    print("\n✓ verify_repository 5/5 全部通过（含 4 项核心 + 1 项 fail-fast 反向）")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
