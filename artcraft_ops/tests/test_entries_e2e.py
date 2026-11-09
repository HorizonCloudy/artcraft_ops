"""stub-SDK 端到端：§10 #10 / #13 / #14 / #19 / #29 / #30。

以 ``plugin.plugins.artcraft_ops`` 名字加载真实 ``__init__.py``（conftest 提供
plugin.sdk.plugin shim），只替换重资源：monkeypatch ``asyncio.create_subprocess_exec``
**绝不启动真实 CLI**。异步一律 ``asyncio.run`` 包装，不用 pytest-asyncio mode 配置。

等待策略：等可观测条件（job 进入终态），用 ``await asyncio.sleep(0)`` 让出事件循环，
**不用固定 wall-clock 睡眠**。
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import time
from pathlib import Path

from _fixtures import FakeCtx, FakeProcess

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"succeeded", "failed", "cancelled", "crashed"}


def _instantiate(module, tmp_path):
    """兼容两种形态：@neko_plugin 插件类，或 §5 的模块级入口函数。"""
    from plugin.sdk.plugin import Err, NekoPluginBase, Ok

    classes = [
        obj for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, NekoPluginBase)
        and obj is not NekoPluginBase
    ]
    ctx = FakeCtx(storage_dir=tmp_path, plugin_dir=PLUGIN_ROOT)
    app = classes[0](ctx) if classes else None
    return app, ctx, Err, Ok


def _get_entry(module, app, name):
    fn = getattr(module, name, None)
    if fn is None and app is not None:
        fn = getattr(app, name, None)
    assert fn is not None, f"入口 {name} 未找到（模块级函数或插件类方法）"
    return fn


def _jobs_module(module):
    m = getattr(module, "jobs", None)
    if m is not None:
        return m
    for key, val in sys.modules.items():
        if key.startswith("plugin.plugins.artcraft_ops") and key.endswith(".jobs"):
            return val
    return None


def _no_spawn(monkeypatch, calls):
    """拦住真实进程创建，记录 argv；返回立即可退出的 FakeProcess。"""

    async def fake_exec(*args, **kwargs):
        calls.append(tuple(args))
        return FakeProcess(stdout_lines=(), returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec, raising=False)
    return fake_exec


async def _drive_to_terminal(get_fn, job_id, ctx, max_yields: int = 500):
    """等可观测条件（终态），失败留最后一次状态作为证据。"""
    last = None
    for _ in range(max_yields):
        last = await get_fn(job_id=job_id, _ctx=ctx)
        payload = getattr(last, "value", None)
        status = payload.get("status") if isinstance(payload, dict) else "failed"
        if status in TERMINAL:
            break
        await asyncio.sleep(0)       # 让出循环，不用固定睡眠
    return last


def _status(last) -> str:
    payload = getattr(last, "value", None)
    return payload.get("status", "") if isinstance(payload, dict) else ""


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
def test_run_sequence_returns_queued_fast(load_plugin_init, tmp_path, monkeypatch):
    """#10 正常：入口秒回 queued（<1s），create_task 在 entry handler 内创建。"""
    module = load_plugin_init()
    app, ctx, Err, Ok = _instantiate(module, tmp_path)
    fn = _get_entry(module, app, "artcraft_ops_run_sequence")
    calls: list = []
    _no_spawn(monkeypatch, calls)

    created = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro, *args, **kwargs):
        created.append(real_create_task(coro, *args, **kwargs))
        return created[-1]

    monkeypatch.setattr(asyncio, "create_task", spy_create_task, raising=True)

    async def scenario():
        coro = fn(
            app="photocraft",
            steps=[{"cmd": "edit.fill", "params": {"color": "#fff"}}],
            out_file="out.png", inspect=True, _ctx=ctx,
        )
        assert inspect.isawaitable(coro), "所有入口必须是 async def"
        res = await coro
        for _ in range(50):
            await asyncio.sleep(0)
            if all(t.done() for t in created):
                break
        for t in created:
            if not t.done():
                t.cancel()
        return res

    t0 = time.monotonic()
    res = asyncio.run(scenario())
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"入口必须 <10s 秒回，实测 {elapsed:.3f}s"
    assert isinstance(res, Ok), f"期望 Ok，实际 {res!r}"
    payload = res.value
    assert payload["status"] == "queued"
    assert payload["poll_with"] == "artcraft_ops_get_job"
    assert isinstance(payload["job_id"], str) and payload["job_id"]
    assert created, "asyncio.create_task 必须在 entry handler 内创建"


def test_llm_tool_missing_params_no_typeerror(load_plugin_init, tmp_path, monkeypatch):
    """#30 正常/错误：`Any=None` 防御式签名 + `**_`，缺全部字段不抛 TypeError。"""
    module = load_plugin_init()
    app, ctx, Err, Ok = _instantiate(module, tmp_path)
    fn = _get_entry(module, app, "artcraft_ops_run_sequence")
    calls: list = []
    _no_spawn(monkeypatch, calls)

    coro = fn()                       # 一个参数都不给
    assert inspect.isawaitable(coro), "入口必须是 async def"
    res = asyncio.run(coro)

    assert isinstance(res, (Ok, dict)), "不得抛 TypeError，必须返回 Ok/Err"
    assert isinstance(res, dict), "缺 steps 应业务失败返回错误 envelope"
    assert res["error"]["code"] == "POLICY_VIOLATION", res["error"]
    assert calls == [], "参数校验失败不得 spawn"


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_limit_steps_256(load_plugin_init, tmp_path, monkeypatch):
    """#19 P0 边界：257 步 -> LIMIT_STEPS 错误 envelope，runner.spawn 未调用。"""
    module = load_plugin_init()
    app, ctx, Err, Ok = _instantiate(module, tmp_path)
    fn = _get_entry(module, app, "artcraft_ops_run_sequence")
    calls: list = []
    _no_spawn(monkeypatch, calls)

    steps = [{"cmd": "edit.fill"} for _ in range(257)]
    coro = fn(app="photocraft", steps=steps, out_file="o.png", _ctx=ctx)
    assert inspect.isawaitable(coro)
    res = asyncio.run(coro)

    assert isinstance(res, dict), f"期望错误 envelope(LIMIT_STEPS)，实际 {res!r}"
    assert res["error"]["code"] == "LIMIT_STEPS", res["error"]
    assert str(res["error"]["message"]).startswith("LIMIT_STEPS: ")
    assert calls == [], "超限时不得启动子进程"


def test_command_not_in_registry(load_plugin_init, tmp_path, monkeypatch):
    """#29 P0 错误：evil.inject 不在注册表 -> POLICY_VIOLATION，不 spawn。"""
    module = load_plugin_init()
    app, ctx, Err, Ok = _instantiate(module, tmp_path)
    fn = _get_entry(module, app, "artcraft_ops_run_sequence")
    calls: list = []
    _no_spawn(monkeypatch, calls)

    coro = fn(
        app="photocraft", steps=[{"cmd": "evil.inject"}],
        out_file="o.png", _ctx=ctx,
    )
    assert inspect.isawaitable(coro)
    res = asyncio.run(coro)

    assert isinstance(res, dict), f"期望错误 envelope(POLICY_VIOLATION)，实际 {res!r}"
    assert res["error"]["code"] == "POLICY_VIOLATION", res["error"]
    assert str(res["error"]["message"]).startswith("POLICY_VIOLATION: ")
    assert calls == [], "命令未命中注册表时不得 spawn"


# ---------------------------------------------------------------------------
# 断线重发：#13 编辑类 / #14 只读（用 fake 断线驱动真实 execute 路径）
# ---------------------------------------------------------------------------
def test_edit_sequence_never_retried(load_plugin_init, tmp_path, monkeypatch):
    """#13 P0：编辑类序列断线 -> 最多 1 次 spawn，终态 FAILED/CRASHED 且文案含「核对」。"""
    module = load_plugin_init()
    app, ctx, Err, Ok = _instantiate(module, tmp_path)
    run_fn = _get_entry(module, app, "artcraft_ops_run_sequence")
    get_fn = _get_entry(module, app, "artcraft_ops_get_job")
    calls: list = []

    async def flaky_exec(*args, **kwargs):
        calls.append(tuple(args))
        raise OSError("bridge disconnected (edit sequence)")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", flaky_exec, raising=False)

    async def scenario():
        res = await run_fn(
            app="photocraft",
            steps=[{"cmd": "edit.fill"}, {"cmd": "document.inspect"}],
            out_file="o.png", _ctx=ctx,
        )
        assert isinstance(res, Ok), f"入口应秒回 queued，实际 {res!r}"
        return await _drive_to_terminal(get_fn, res.value["job_id"], ctx)

    last = asyncio.run(scenario())

    assert len(calls) <= 1, f"编辑类断线绝不重发，实际 spawn {len(calls)} 次"
    assert _status(last) in {"failed", "crashed"}, f"实际终态 {_status(last)!r}"
    assert "核对" in str(getattr(last, "value", last)), \
        "编辑类崩溃文案必须提示用检视命令核对文档状态"


def test_readonly_retry_once(load_plugin_init, tmp_path, monkeypatch):
    """#14：纯只读断线 -> 最多重发一次（绝不 3 次以上），且 retried_readonly 置位。

    [INFERENCE / 规格冲突] §4/§7 注明「v1 CLI_RUN 不触发只读重发，仅预留 bridge
    通道生效」，§10 #14 却期望重发一次。本测试锁定两边都成立的安全不变量：
    **最多一次重发、绝不无限重试**；冲突已上报主理人，待云雨/云远裁决后收紧断言。
    """
    module = load_plugin_init()
    app, ctx, Err, Ok = _instantiate(module, tmp_path)
    run_fn = _get_entry(module, app, "artcraft_ops_run_sequence")
    get_fn = _get_entry(module, app, "artcraft_ops_get_job")
    calls: list = []

    async def flaky_exec(*args, **kwargs):
        calls.append(tuple(args))
        if len(calls) == 1:
            raise OSError("bridge disconnected (readonly)")
        return FakeProcess(
            stdout_lines=('{"command":"document.inspect","result":{}}',),
            returncode=0,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", flaky_exec, raising=False)

    async def scenario():
        res = await run_fn(
            app="photocraft", steps=[{"cmd": "document.inspect"}],
            out_file="o.png", _ctx=ctx,
        )
        assert isinstance(res, Ok), f"入口应秒回 queued，实际 {res!r}"
        job_id = res.value["job_id"]
        last = await _drive_to_terminal(get_fn, job_id, ctx)
        return job_id, last

    job_id, last = asyncio.run(scenario())

    assert 1 <= len(calls) <= 2, f"只读断线最多重发一次，实际 spawn {len(calls)} 次"
    assert len(calls) < 3, "第二次断线绝不再重发"
    if len(calls) == 2:
        jobs_mod = _jobs_module(module)
        assert jobs_mod is not None
        state = jobs_mod.get_job(job_id)
        assert state is not None and state.retried_readonly is True
    assert _status(last) in TERMINAL


# ---------------------------------------------------------------------------
# 安全配置真的到达入口层（[真缺陷] 曾一律传空 dict）
# ---------------------------------------------------------------------------
def test_run_sequence_honours_configured_user_root(load_plugin_init, tmp_path, monkeypatch):
    """配置允许的用户目录：注入前拒、注入后放 —— 证明 startup 的注入链是通的。

    [FACT] 2026-11-08 前，``artcraft_ops_run_sequence`` 写的是
    ``default_policy(data_dir, cache_dir, {})``，第三参恒空 ⇒ 即使
    ``[artcraft.security] allow_user_dirs = true``，入口仍只认 data/cache。
    """
    module = load_plugin_init()
    app, ctx, Err, Ok = _instantiate(module, tmp_path)
    fn = _get_entry(module, app, "artcraft_ops_run_sequence")
    _no_spawn(monkeypatch, [])
    roots = module.roots_mod

    user_dir = tmp_path / "Pictures"
    user_dir.mkdir()
    src = user_dir / "shot.png"
    src.write_bytes(b"png")
    steps = [{"cmd": "edit.fill", "params": {"color": "#fff"}}]

    async def scenario():
        async def call():
            return await fn(
                app="photocraft", steps=steps, input_file=str(src),
                out_file="out.png", inspect=True, _ctx=ctx,
            )

        roots.configure_security({})
        denied = await call()
        assert not isinstance(denied, Ok), "未授权目录不应放行"
        assert isinstance(denied, dict), f"期望错误 envelope，实际 {denied!r}"
        assert str(denied["error"]["message"]).startswith("PATH_OUTSIDE_ROOTS"), denied["error"]

        roots.configure_security({
            "allow_user_dirs": True,
            "user_read_roots": [str(user_dir)],
        })
        allowed = await call()
        assert isinstance(allowed, Ok), getattr(allowed, "error", allowed)
        assert allowed.value.get("status") == "queued"

    try:
        asyncio.run(scenario())
    finally:
        roots.configure_security({})


# ---------------------------------------------------------------------------
# `--new` 是第二条 LLM 可控 JSON 通道（云严实测发现）
# ---------------------------------------------------------------------------
def test_new_doc_is_second_llm_controlled_channel(load_plugin_init, tmp_path, monkeypatch):
    """#26 P0：`new_doc` 的 JSON 里也有路径字段，必须和 step.params 同样过 roots。

    [真缺陷 · 云严实测] `--new` 的 payload 是 ``json.dumps(new_doc)`` **整段**进 argv，
    CLI 侧 `profile` 这类键就是文件路径。修好 step.params 之后它仍是完整旁路：
    入口直接回 ``queued``，argv 里能看到
    ``--new {"profile": "C:\\\\Windows\\\\win.ini", ...}``。

    两半都在：
      * `new_doc={"profile": win.ini, "width":10, "height":10}` → **不是 Ok**，
        且 ``res["error"]["code"]`` 是 fail-closed 码（``PATH_OUTSIDE_ROOTS`` /
        ``PATH_UNSAFE``）、且**没有 spawn**。
        （`width`/`height` 是数字，不触发判据 —— 触发的是那个字符串 `profile`。）
      * `new_doc={"width":10,"height":10,"background":"white"}` → 正常 ``queued``。
        这一半防「一律拒 new_doc」式过度修复：新建文档是主路径，不能红。
    """
    module = load_plugin_init()
    app, ctx, Err, Ok = _instantiate(module, tmp_path)
    fn = _get_entry(module, app, "artcraft_ops_run_sequence")
    calls: list = []
    _no_spawn(monkeypatch, calls)

    created = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro, *args, **kwargs):
        created.append(real_create_task(coro, *args, **kwargs))
        return created[-1]

    monkeypatch.setattr(asyncio, "create_task", spy_create_task, raising=True)

    steps = [{"cmd": "edit.fill", "params": {"color": "#fff"}}]

    async def scenario():
        bad = await fn(
            app="photocraft",
            new_doc={
                "profile": r"C:\Windows\win.ini",
                "width": 10,
                "height": 10,
            },
            steps=steps, out_file="out.png", _ctx=ctx,
        )
        calls_after_bad = list(calls)          # 越界那一半绝不能 spawn

        good = await fn(
            app="photocraft",
            new_doc={"width": 10, "height": 10, "background": "white"},
            steps=steps, out_file="out.png", _ctx=ctx,
        )
        for _ in range(50):
            await asyncio.sleep(0)
            if all(t.done() for t in created):
                break
        for t in created:
            if not t.done():
                t.cancel()
        return bad, calls_after_bad, good

    bad, calls_after_bad, good = asyncio.run(scenario())

    assert not isinstance(bad, Ok), f"越界 new_doc 不得放行，实际 {bad!r}"
    assert isinstance(bad, dict), f"越界 new_doc 必须回错误 envelope，实际 {bad!r}"
    assert bad["error"]["code"] in ("PATH_OUTSIDE_ROOTS", "PATH_UNSAFE"), \
        f"越界路径必须回 fail-closed 码，实际 error={bad['error']!r}"
    assert calls_after_bad == [], \
        f"new_doc 校验失败不得启动子进程，实际 spawn {calls_after_bad!r}"

    assert isinstance(good, Ok), f"正常 new_doc 应 queued，实际 {good!r}"
    assert good.value.get("status") == "queued", good.value
