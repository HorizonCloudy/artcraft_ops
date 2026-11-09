"""§10 #20 / #23 / #24 — runner 子进程执行、超时杀进程、体积上限。

覆盖：正常路径（exec 非 shell、逐行输出、ProcHandle 字段）
      + 边界（8 MiB 截断、单行 >1 MiB 跳过、terminate 升级到 kill）
      + 错误输入（shell 变体必须被拒、超时路径）。

全部用 FakeProcess / monkeypatch asyncio.create_subprocess_exec，
**不启动真实 photocraft-cli.exe**。
"""
from __future__ import annotations

import asyncio
import subprocess

from _fixtures import (
    FakeProcess,
    MAX_REPLY_BYTES,
    MAX_REQUEST_LINE_BYTES,
    PHOTOCRAFT_CLI,
)


def _kw(impl):
    return impl("runner").build_subprocess_kwargs()


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
def test_build_subprocess_kwargs_no_shell(impl):
    """#24 P0 正常：kwargs 里不出现 shell 键（argv list + shell=False）。"""
    kw = _kw(impl)
    assert isinstance(kw, dict)
    assert "shell" not in kw
    assert kw.get("shell") is not True


def test_build_subprocess_kwargs_create_no_window(impl):
    """正常：Windows 下带 CREATE_NO_WINDOW（不弹控制台窗口）。"""
    kw = _kw(impl)
    assert "creationflags" in kw
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flag:
        assert kw["creationflags"] & flag, "必须包含 CREATE_NO_WINDOW"


def test_spawn_uses_exec_not_shell(impl, monkeypatch):
    """正常：spawn 走 create_subprocess_exec(*argv)，绝不走 create_subprocess_shell。"""
    runner = impl("runner")
    argv = [PHOTOCRAFT_CLI, "run", "--cmd", "edit.fill"]
    calls: list = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess(stdout_lines=('{"command":"edit.fill","result":{}}',))

    async def fake_shell(*args, **kwargs):
        raise AssertionError("spawn 绝不能走 shell 变体")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec, raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell, raising=False)

    handle = asyncio.run(runner.spawn(argv, r"C:\work\abs", {"PATH": "x"}, "job-1"))

    assert calls and calls[0][0] == tuple(argv)
    assert calls[0][1].get("shell") is not True
    assert handle.job_id == "job-1"
    assert handle.proc is not None


def test_stream_lines_yields_each_line(impl):
    """正常：逐行 JSON 输出原样收集，回调每行触发一次。"""
    runner = impl("runner")
    lines = (
        '{"command":"edit.fill","result":{"contents":"color"}}',
        '{"command":"document.inspect","result":{}}',
    )
    proc = FakeProcess(stdout_lines=lines, stderr_text="", returncode=0)
    handle = runner.ProcHandle(job_id="j1", proc=proc, started_at=0.0)
    seen: list[str] = []

    raw = asyncio.run(runner.stream_lines(
        handle, seen.append, MAX_REQUEST_LINE_BYTES, MAX_REPLY_BYTES,
    ))

    assert raw.returncode == 0
    assert tuple(raw.stdout_lines) == lines
    assert seen == list(lines)
    assert raw.truncated is False


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_timeout_terminates_child(impl):
    """#23 P0：模拟 wait_for 超时分支 -> 真实 runner.terminate 必须调 Process.terminate。"""
    runner = impl("runner")
    proc = FakeProcess(stdout_lines=("still running",), returncode=0)

    async def never_finishes():
        await asyncio.Future()          # 永不完成，模拟卡住的子进程

    proc.wait = never_finishes          # type: ignore[method-assign]
    handle = runner.ProcHandle(job_id="j1", proc=proc, started_at=0.0)

    async def scenario():
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.01)
        except (asyncio.TimeoutError, TimeoutError):
            await runner.terminate(handle, grace_s=0.0)

    asyncio.run(scenario())
    assert proc.terminate_calls >= 1, "超时后必须 terminate 子进程"


def test_terminate_escalates_to_kill_after_grace(impl):
    """#23 边界：terminate -> 宽限 -> kill（宿主只 task.cancel() 不杀 Popen）。"""
    runner = impl("runner")
    proc = FakeProcess(stdout_lines=(), returncode=0)

    async def never_finishes():
        await asyncio.Future()

    proc.wait = never_finishes          # type: ignore[method-assign]
    handle = runner.ProcHandle(job_id="j1", proc=proc, started_at=0.0)

    asyncio.run(runner.terminate(handle, grace_s=0.0))

    assert proc.terminate_calls == 1
    assert proc.kill_calls == 1, "宽限期后仍未退出必须 kill"


def test_stream_lines_truncates_at_8mib(impl):
    """#20 边界：累计 stdout > 8 MiB -> truncated=True，且不抛异常。"""
    runner = impl("runner")
    lines = tuple("x" * 500_000 for _ in range(20))     # 10 MiB，每行 < 1 MiB
    proc = FakeProcess(stdout_lines=lines, returncode=0)
    handle = runner.ProcHandle(job_id="j1", proc=proc, started_at=0.0)

    raw = asyncio.run(runner.stream_lines(
        handle, lambda _l: None, MAX_REQUEST_LINE_BYTES, MAX_REPLY_BYTES,
    ))

    assert raw.truncated is True
    assert len(raw.stdout_lines) < 20
    assert sum(len(x) for x in raw.stdout_lines) <= MAX_REPLY_BYTES


def test_stream_lines_skips_line_over_1mib(impl):
    """#20 边界：单行 > 1 MiB 被跳过，前后正常行仍保留（§6 超限降级不崩）。"""
    runner = impl("runner")
    huge = "y" * (MAX_REQUEST_LINE_BYTES + 64)
    lines = ("first-ok", huge, "last-ok")
    proc = FakeProcess(stdout_lines=lines, returncode=0)
    handle = runner.ProcHandle(job_id="j1", proc=proc, started_at=0.0)

    raw = asyncio.run(runner.stream_lines(
        handle, lambda _l: None, MAX_REQUEST_LINE_BYTES, MAX_REPLY_BYTES,
    ))

    assert "first-ok" in raw.stdout_lines
    assert "last-ok" in raw.stdout_lines
    assert huge not in raw.stdout_lines


# ---------------------------------------------------------------------------
# 错误输入
# ---------------------------------------------------------------------------
def test_raw_output_dataclass_fields(impl):
    """错误输入：exit 2 的 RawOutput 字段可精确读取（供 result 层映射）。"""
    runner = impl("runner")
    raw = runner.RawOutput(
        returncode=2,
        stdout_lines=(),
        stderr_text="usage error: unknown command",
        truncated=False,
    )
    assert raw.returncode == 2
    assert raw.stdout_lines == ()
    assert raw.stderr_text == "usage error: unknown command"
    assert raw.truncated is False


def test_stream_lines_preserves_stderr_and_returncode(impl):
    """错误输入：exit 2 的 stderr 与 returncode 原样透传给 result 层（不吞、不改）。"""
    runner = impl("runner")
    proc = FakeProcess(
        stdout_lines=(), stderr_text="usage error: unknown command", returncode=2,
    )
    handle = runner.ProcHandle(job_id="j1", proc=proc, started_at=0.0)

    raw = asyncio.run(runner.stream_lines(
        handle, lambda _l: None, MAX_REQUEST_LINE_BYTES, MAX_REPLY_BYTES,
    ))

    assert raw.returncode == 2
    assert raw.stderr_text == "usage error: unknown command"
    assert raw.stdout_lines == ()
