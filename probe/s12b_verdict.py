"""S12-b 裁决探针：独立验证云测判据在四种退化下的行为。

不跑 pytest，直接用独立 logging.Handler 收真实 DEBUG 记录，复算：
  A. 云测判据（三面签名两两不相等）在每种退化下是绿还是红
  B. 叠加「每面签名必须含 runner.py 帧」后，是否补上 inner1 / outer_inner
  C. 去掉「注入桩帧」后，open vs read 是否仍可区分（云严 P6）
"""
from __future__ import annotations

import asyncio
import builtins
import io
import logging
import os
import re
import sys
import tempfile
import types
from concurrent.futures import ThreadPoolExecutor

_ROOT = r"C:\Users\54301\WorkBuddy\2026-11-07-22-33-50"
sys.path.insert(0, _ROOT)

if "artcraft_ops" not in sys.modules:
    _pkg = types.ModuleType("artcraft_ops")
    _pkg.__path__ = [_ROOT + r"\artcraft_ops"]
    _pkg.__package__ = "artcraft_ops"
    sys.modules["artcraft_ops"] = _pkg

from artcraft_ops import locator  # noqa: E402

FRAME_LINE = re.compile(r'^\s+File\s+["\'](.+?)["\'],\s*line\s+(\d+)(?:,\s*in\s+\S+)?\s*$')
SIG_RE = re.compile(r'File\s+["\']([^"\']+)["\'],\s*line\s+(\d+)')


def sig(text):
    return tuple((os.path.basename(p), int(n)) for p, n in SIG_RE.findall(text or ""))


def split_tb(text):
    """拆成 (head, [frame_block...], tail)。"""
    lines = (text or "").splitlines(keepends=True)
    if not lines:
        return "", [], ""
    head, frames, tail = [], [], ""
    cur = None
    started = False
    for line in lines:
        if not started and not FRAME_LINE.match(line):
            head.append(line)
            continue
        started = True
        if FRAME_LINE.match(line):
            if cur:
                frames.append(cur)
            cur = [line]
        elif cur is not None and (line[:1] in (" ", "\t")):
            cur.append(line)
        else:
            if cur:
                frames.append(cur)
                cur = None
            tail += line
    if cur:
        frames.append(cur)
    return "".join(head), frames, tail


def degrade(text, mode):
    head, frames, tail = split_tb(text)
    if mode == "full" or not frames:
        return text
    if mode == "first1":
        keep = frames[:1]
    elif mode == "inner1":
        keep = frames[-1:]
    elif mode == "outer_inner":
        keep = [frames[0], frames[-1]] if len(frames) > 1 else frames
    else:
        raise ValueError(mode)
    return head + "".join("".join(b) for b in keep) + tail


def pseudo(text):
    m = re.findall(r"^([A-Za-z_][\w.]*):", text or "", re.M)
    return f"{m[-1] if m else 'Exception'}: 见 locator.py 第 12 行（栈已省略）"


class Sink(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.rows = []

    def emit(self, record):
        self.rows.append(record.getMessage())


SURFACES = ("executor_schedule", "open", "read")
EXC = {"executor_schedule": "RuntimeError", "open": "PermissionError", "read": "OSError"}


async def trigger(surface, exe):
    real_open = builtins.open
    loop = asyncio.get_running_loop()
    cli_name = os.path.basename(exe)
    dead = None

    async def failing_exec(*a, **kw):
        raise FileNotFoundError("--version unsupported")

    _orig_exec = asyncio.create_subprocess_exec
    _orig_shell = asyncio.create_subprocess_shell
    asyncio.create_subprocess_exec = failing_exec
    asyncio.create_subprocess_shell = failing_exec

    if surface == "open":
        def go(file, *a, **kw):
            if os.path.basename(str(file)) == cli_name:
                raise PermissionError(13, "denied")
            return real_open(file, *a, **kw)
        builtins.open = go
    elif surface == "read":
        class Bad(io.BytesIO):
            def read(self, *a, **kw):
                raise OSError(5, "io error")
        def go(file, *a, **kw):
            if os.path.basename(str(file)) == cli_name:
                return Bad(b"")
            return real_open(file, *a, **kw)
        builtins.open = go
    else:
        dead = ThreadPoolExecutor(max_workers=1)
        dead.shutdown(wait=True)
        loop.set_default_executor(dead)

    sink = Sink()
    lg = logging.getLogger("artcraft_ops.locator")
    lg.addHandler(sink)
    lg.setLevel(logging.DEBUG)
    try:
        got = await locator.detect_version(exe, None)
    finally:
        lg.removeHandler(sink)
        asyncio.create_subprocess_exec = _orig_exec
        asyncio.create_subprocess_shell = _orig_shell
        if surface in ("open", "read"):
            builtins.open = real_open
        if dead is not None:
            loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
    # sha256 分支那条：点名 exc 类型，取最后一条
    hits = [m for m in sink.rows if EXC[surface] in m]
    return got, (hits[-1] if hits else "")


async def main():
    with tempfile.TemporaryDirectory() as td:
        exe = os.path.join(td, "s12b-cli.exe")
        open(exe, "wb").write(b"MZ" + b"\x00" * 32)

        raw = {}
        for s in SURFACES:
            got, msg = await trigger(s, exe)
            assert got == "unknown", f"注入未生效: {s} -> {got!r}"
            raw[s] = msg
            print(f"[注入] {s:18s} 记录 len={len(msg):4d} 签名={sig(msg)}")

        print("\n" + "=" * 92)
        print("A/B  各退化形态下的判据行为（云测=三面两两不等；硬化=再要求每面含 runner.py）")
        print("=" * 92)
        modes = ["full", "first1", "inner1", "outer_inner", "pseudo"]
        for mode in modes:
            texts = {s: (pseudo(raw[s]) if mode == "pseudo" else degrade(raw[s], mode)) for s in SURFACES}
            sigs = {s: sig(texts[s]) for s in SURFACES}
            pairs = [("executor_schedule", "open"), ("executor_schedule", "read"), ("open", "read")]
            green_yunce = all(sigs[a] != sigs[b] for a, b in pairs)
            has_runner = {s: any(b == "runner.py" for b, _ in sigs[s]) for s in SURFACES}
            green_hard = green_yunce and all(has_runner.values())
            print(f"\n--- {mode} ---")
            for s in SURFACES:
                print(f"   {s:18s} {len(sigs[s])} 帧  runner.py={'Y' if has_runner[s] else 'N'}  {sigs[s]}")
            print(f"   => 云测判据: {'GREEN 假绿!' if green_yunce else 'RED 正确'}"
                  f"   | 硬化后: {'GREEN 正确' if green_hard else 'RED'}")

        print("\n" + "=" * 92)
        print("C  去掉注入桩帧后，open vs read 是否仍可区分（云严 P6）")
        print("=" * 92)
        for mode in ("full", "inner1"):
            texts = {s: degrade(raw[s], mode) for s in SURFACES}
            stripped = {}
            for s in SURFACES:
                stripped[s] = tuple(
                    (b, n) for b, n in sig(texts[s]) if b not in ("test_locator.py", "<stdin>")
                )
            same = stripped["open"] == stripped["read"]
            print(f"\n--- {mode} 去掉测试文件帧 ---")
            for s in SURFACES:
                print(f"   {s:18s} {stripped[s]}")
            print(f"   => open vs read {'相同（区分力依赖注入帧）' if same else '仍不同（生产栈自带区分力）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
