"""S12-b 可行性探针（只读，不改生产代码、不写仓库）。

回答三个问题：
  Q1 sha256 分支内部「调度失败 / open 失败 / read 失败」三种失败面的
     帧签名 (路径, 行号) 集合，两两是否可区分？
  Q2 若实现退化成「只保留最外一帧」，上述签名是否会塌缩成同一个？
  Q3 若改用「跨三条降级路径（long_path / probe / sha256）」比较签名，
     退化后是否仍能区分（即该替代判据能否挡住 first_frame_only）？

判据签名正则与原 S12-b 表一致：File "…", line N
"""
from __future__ import annotations

import asyncio
import builtins
import io
import logging
import re
import sys
import traceback
import types
from concurrent.futures import ThreadPoolExecutor

_ROOT = r"C:\Users\54301\WorkBuddy\2026-11-07-22-33-50"
sys.path.insert(0, _ROOT)

# 复用 tests/conftest.py 的做法：预注册成只带 __path__ 的命名空间包，
# 这样 import 子模块不会执行 __init__.py（它要 plugin.sdk，宿主才有）。
if "artcraft_ops" not in sys.modules:
    _pkg = types.ModuleType("artcraft_ops")
    _pkg.__path__ = [_ROOT + r"\artcraft_ops"]
    _pkg.__package__ = "artcraft_ops"
    sys.modules["artcraft_ops"] = _pkg

from artcraft_ops import locator  # noqa: E402

SIG_RE = re.compile(r'File\s+["\']([^"\']+)["\'],\s*line\s+(\d+)')


def sigs(text: str) -> tuple[tuple[str, int], ...]:
    """抽帧签名；路径只留 basename 以便跨机器比较。"""
    import os
    return tuple(
        (os.path.basename(p), int(n)) for p, n in SIG_RE.findall(text or "")
    )


def first_frame_only(exc: BaseException) -> str:
    """模拟退化实现：只保留最外一帧 + 异常消息（丢掉内层帧）。"""
    tb = exc.__traceback__
    if tb is None:
        return "".join(traceback.format_exception_only(type(exc), exc))
    summary = traceback.StackSummary.extract(traceback.walk_tb(tb))
    summary = traceback.StackSummary.from_list(list(summary)[:1])
    return (
        "Traceback (most recent call last):\n"
        + "".join(summary.format())
        + "".join(traceback.format_exception_only(type(exc), exc))
    )


class Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.rows: list[tuple[str, str]] = []

    def emit(self, record):
        self.rows.append((record.getMessage(), traceback.format_exc() if record.exc_info else ""))


def _tail(rows, needle):
    hit = [m for m, _ in rows if needle in m]
    return hit[-1] if hit else ""


async def run_case(kind: str, tmp_exe: str) -> tuple[str, str]:
    """触发 detect_version 的 sha256 回退，返回 (完整帧文本, 只留最外帧文本)。"""
    loop = asyncio.get_running_loop()
    real_open = builtins.open
    patch_open = None
    ex = None

    if kind == "schedule":
        ex = ThreadPoolExecutor(max_workers=1)
        ex.shutdown(wait=True)
        loop.set_default_executor(ex)
    elif kind == "open":
        def _boom(path, *a, **kw):
            if str(path).endswith("fake-cli.exe"):
                raise PermissionError(13, "Permission denied (probe: open)")
            return real_open(path, *a, **kw)
        patch_open = _boom
        builtins.open = _boom
    elif kind == "read":
        class BadFh(io.BytesIO):
            def read(self, *a, **kw):
                raise OSError(5, "I/O error (probe: read)")

        def _fake_open(path, *a, **kw):
            if str(path).endswith("fake-cli.exe"):
                return BadFh(b"")
            return real_open(path, *a, **kw)
        patch_open = _fake_open
        builtins.open = _fake_open

    handler = Collect()
    logger = logging.getLogger("artcraft_ops.locator")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        # 直接捕获被吞掉的原始异常：复用 detect_version 内部同一个 _sha256_12
        from artcraft_ops import runner
        try:
            await runner.blocking(locator._sha256_12, tmp_exe)
        except BaseException as exc:  # noqa: BLE001
            full = traceback.format_exc()
            outer = first_frame_only(exc)
        else:
            full, outer = "(no exception)", "(no exception)"
    finally:
        logger.removeHandler(handler)
        if patch_open is not None:
            builtins.open = real_open
        if ex is not None:
            loop.set_default_executor(ThreadPoolExecutor(max_workers=2))

    return full, outer


async def main() -> int:
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        exe = os.path.join(td, "fake-cli.exe")
        with open(exe, "wb") as fh:
            fh.write(b"MZ" + b"\x00" * 64)

        print("=" * 78)
        print("Q1/Q2  sha256 分支内部三种失败面（真实 _sha256_12，经 runner.blocking）")
        print("=" * 78)
        rows = {}
        for kind in ("schedule", "open", "read"):
            full, outer = await run_case(kind, exe)
            rows[kind] = (sigs(full), sigs(outer))
            print(f"\n--- {kind} ---")
            print(f"  完整帧 签名 {rows[kind][0]}")
            print(f"  只留最外 签名 {rows[kind][1]}")

        full_set = {k: v[0] for k, v in rows.items()}
        outer_set = {k: v[1] for k, v in rows.items()}
        n_full = len({tuple(sorted(v)) for v in full_set.values()})
        n_outer = len({tuple(sorted(v)) for v in outer_set.values()})
        print(f"\n  [Q1] 完整帧下不同签名数 = {n_full} / 3  -> 可区分 = {n_full >= 2}")
        print(f"  [Q2] 只留最外帧下不同签名数 = {n_outer} / 3 -> 塌缩 = {n_outer == 1}")

        print()
        print("=" * 78)
        print("Q3  三条降级路径的 except 块位置（只留最外一帧时是否仍不同）")
        print("=" * 78)
        src = open(r"C:\Users\54301\WorkBuddy\2026-11-07-22-33-50\artcraft_ops\locator.py",
                   encoding="utf-8").read().splitlines()
        for label, lineno in (("long_path", 127), ("version_probe", 212), ("sha256_fallback", 222)):
            print(f"  {label:16s} except 块首行 locator.py:{lineno}  -> {src[lineno-2].strip()[:60]}")
        print("\n  [Q3] 三条路径 except 块在不同行 -> 只留最外一帧后签名仍互不相同")
        print("        ⇒ 跨三条路径比较签名【挡不住】first_frame_only [INFERENCE]")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
