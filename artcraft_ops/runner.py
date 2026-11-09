"""artcraft_ops —— 基础设施层：子进程执行（零第三方依赖）。

[FACT 链 1] CLI 冷启动实测 0.294s、一次 `run` 完成「新建→命令→导出」。
[FACT 链 2] 宿主 ``CANCEL_RUN`` 只 ``task.cancel()``（`host.py:1627-1632`），**不杀 Popen**
→ 句柄必须自己持有，``terminate()`` 升级到 ``kill()``。

防注入三条（§5）：
  1. ``asyncio.create_subprocess_exec(*argv)`` —— argv list，永不 ``shell=True``；
  2. ``build_subprocess_kwargs()`` 里**没有** ``shell`` 键；
  3. ``CREATE_NO_WINDOW`` 避免弹控制台窗（Windows 服务态运行必需）。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable

from .spec import MAX_REPLY_BYTES, MAX_REQUEST_LINE_BYTES

logger = logging.getLogger("artcraft_ops")

_STDERR_CAP_BYTES = 1024 * 1024      # stderr 只留 1 MiB（警告行在头部）
_READ_CHUNK = 65536


@dataclass
class ProcHandle:
    """子进程句柄：宿主取消协程后，靠它把真实进程收掉。"""

    job_id: str
    proc: Any
    started_at: float = 0.0


@dataclass
class RawOutput:
    returncode: int
    stdout_lines: tuple[str, ...] = ()
    stderr_text: str = ""
    truncated: bool = False


def _windows_creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)


def build_subprocess_kwargs() -> dict[str, Any]:
    """``create_subprocess_exec`` 的固定 kwargs —— **不含 shell 键**（防注入契约）。"""
    return {
        "stdin": subprocess.DEVNULL,                 # 不读父进程 stdin，避免等待输入
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "creationflags": _windows_creationflags(),   # 非 Windows 为 0
    }


async def spawn(
    argv: list[str],
    cwd: str = "",
    env: dict[str, str] | None = None,
    job_id: str = "",
    timeout: float = 60.0,          # noqa: ARG001 - 由调用方 wait_for 控制，保留签名
) -> ProcHandle:
    """拉起一次 CLI（单次控制调用），返回可 ``terminate`` 的句柄。"""
    if not argv or not str(argv[0] or "").strip():
        raise ValueError("argv is empty; refusing to spawn")
    kwargs = build_subprocess_kwargs()
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=(cwd or None), env=env, **kwargs,
    )
    return ProcHandle(job_id=job_id, proc=proc, started_at=time.monotonic())


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").rstrip("\r")


async def _consume_stdout(
    stream: Any,
    on_line: Callable[[str], None] | None,
    max_line_bytes: int,
    max_reply_bytes: int,
    sink: list[str],
    state: dict[str, Any],
) -> None:
    """逐行消费 stdout：单行超限跳过，累计超限截断（不抛异常）。

    不用 ``StreamReader.readline()``：它默认 64 KiB limit，遇到 >1 MiB 长行会
    ``ValueError``。这里自己切行，才能实现「跳过超长行、保留前后有效行」。
    """
    buf = bytearray()
    skipping = False
    while not state["truncated"]:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        while True:
            idx = buf.find(b"\n")
            if idx < 0:
                if len(buf) > max_line_bytes:
                    skipping = True
                    buf.clear()
                break
            raw = bytes(buf[:idx])
            del buf[: idx + 1]
            if skipping:
                skipping = False
                continue
            if len(raw) > max_line_bytes:
                state["skipped_long_lines"] += 1
                continue
            if state["total"] + len(raw) > max_reply_bytes:
                state["truncated"] = True
                buf.clear()
                break
            state["total"] += len(raw)
            line = _decode(raw)
            sink.append(line)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:  # 回调异常不得影响管道读取
                    logger.debug("artcraft_ops stream callback raised")

    if state["truncated"] or skipping or not buf:
        return
    raw = bytes(buf)
    buf.clear()
    if 0 < len(raw) <= max_line_bytes and state["total"] + len(raw) <= max_reply_bytes:
        state["total"] += len(raw)
        line = _decode(raw)
        sink.append(line)
        if on_line is not None:
            try:
                on_line(line)
            except Exception:  # pragma: no cover
                logger.debug("artcraft_ops stream callback raised")


async def stream_lines(
    handle: ProcHandle,
    on_line: Callable[[str], None] | None = None,
    max_line_bytes: int = MAX_REQUEST_LINE_BYTES,
    max_reply_bytes: int = MAX_REPLY_BYTES,
) -> RawOutput:
    """读完 stdout（逐行回调）+ stderr，返回 ``RawOutput``。

    stderr 与 stdout **并发**读，避免 64 KiB 管道缓冲写满互相阻塞。
    """
    proc = handle.proc if isinstance(handle, ProcHandle) else handle
    stdout_stream = getattr(proc, "stdout", None)
    stderr_stream = getattr(proc, "stderr", None)

    stderr_bytes = bytearray()
    stderr_state = {"truncated": False}

    async def _drain_stderr() -> None:
        if stderr_stream is None:
            return
        while True:
            chunk = await stderr_stream.read(_READ_CHUNK)
            if not chunk:
                break
            room = _STDERR_CAP_BYTES - len(stderr_bytes)
            if room <= 0:
                stderr_state["truncated"] = True
                continue
            stderr_bytes.extend(chunk[:room])

    sink: list[str] = []
    state: dict[str, Any] = {"total": 0, "truncated": False, "skipped_long_lines": 0}

    stderr_task = asyncio.create_task(_drain_stderr())
    try:
        await asyncio.sleep(0)          # 让 stderr 读取先启动，避免短命令下永远读不到
        if stdout_stream is not None:
            await _consume_stdout(
                stdout_stream, on_line, max_line_bytes, max_reply_bytes, sink, state,
            )
        returncode = await proc.wait()
    finally:
        if stderr_task.done():
            if stderr_task.exception() is not None:  # pragma: no cover
                logger.debug("artcraft_ops stderr drain failed")
        else:
            # 进程结束后 stderr 应已 EOF；给它一次机会读完（真实管道可能还在缓冲）
            try:
                await asyncio.wait_for(stderr_task, timeout=0.5)
            except (asyncio.TimeoutError, TimeoutError):
                # stderr_text 是 CLI_USAGE_ERROR / CLI_TASK_FAILED 的文案来源，
                # 被静默截半会让人误判根因 → 至少留一条诊断日志
                logger.warning(
                    "artcraft_ops stderr drain timed out after %.1fs; stderr may be partial",
                    0.5,
                )
                stderr_task.cancel()
                with contextlib.suppress(BaseException):
                    await stderr_task

    if state["skipped_long_lines"]:
        logger.warning(
            "artcraft_ops skipped %d oversized stdout line(s) (> %d bytes)",
            state["skipped_long_lines"], max_line_bytes,
        )

    return RawOutput(
        returncode=int(returncode) if returncode is not None else -1,
        stdout_lines=tuple(sink),
        stderr_text=bytes(stderr_bytes).decode("utf-8", "replace"),
        truncated=bool(state["truncated"]),
    )


async def _safe_wait(proc: Any) -> Any:
    try:
        return await proc.wait()
    except Exception:  # pragma: no cover - 进程已被回收
        return None


async def terminate(handle: ProcHandle | None, grace_s: float = 2.0) -> None:
    """``terminate()`` → 宽限 → ``kill()``。

    刻意**不**先看 ``returncode``：宿主可能已 cancel 协程但进程仍在跑，
    而我们拿到的 ``returncode`` 未必反映真实状态。
    """
    if handle is None:
        return
    proc = getattr(handle, "proc", None) or handle

    with contextlib.suppress(ProcessLookupError, OSError, RuntimeError, AttributeError):
        proc.terminate()

    exited = False
    try:
        await asyncio.wait_for(_safe_wait(proc), timeout=max(0.0, float(grace_s)))
        exited = True
    except (asyncio.TimeoutError, TimeoutError):
        exited = False
    except Exception:  # pragma: no cover
        exited = True

    if exited:
        return

    with contextlib.suppress(ProcessLookupError, OSError, RuntimeError, AttributeError):
        proc.kill()
    with contextlib.suppress(asyncio.TimeoutError, TimeoutError, Exception):
        await asyncio.wait_for(_safe_wait(proc), timeout=1.0)


async def blocking(fn: Callable[..., Any], *args: Any) -> Any:
    """>5s 同步阻塞（文件 sha256 / 大文件读写）统一入口。"""
    return await asyncio.to_thread(fn, *args)


__all__ = [
    "ProcHandle", "RawOutput", "build_subprocess_kwargs", "spawn",
    "stream_lines", "terminate", "blocking",
]
