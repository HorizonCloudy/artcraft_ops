"""S12-b 三方判据对撞：云测原版 vs 主理人「含 runner.py」vs 云严「剔除测试帧后 ≥2」。

对 7 种退化形态，分别算三个判据的绿/红，输出对撞矩阵。
判据定义：
  A 云测原版 : 三面签名（含测试帧）两两不等
  B 主理人版 : A + 每面签名含至少一个 runner.py 帧
  C 云严版   : 剔除测试自身帧后，三面两两不等 + 每面 >=2 个互不重复帧
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile

sys.path.insert(0, ".")
_spec = importlib.util.spec_from_file_location("v", "probe/s12b_verdict.py")
v = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v)

PAIRS = [("executor_schedule", "open"), ("executor_schedule", "read"), ("open", "read")]


def is_noise(base):
    return base.startswith("test_") or base.startswith("s12b_") or base in ("<stdin>",)


def base_of_block(block_lines):
    m = v.FRAME_LINE.match(block_lines[0])
    return os.path.basename(m.group(1)) if m else ""


def degrade(text, mode):
    head, frames, tail = v.split_tb(text)
    if not frames or mode == "full":
        return text
    if mode == "first1":
        keep = frames[:1]
    elif mode == "inner1":
        keep = frames[-1:]
    elif mode == "outer_inner":
        keep = [frames[0], frames[-1]] if len(frames) > 1 else frames
    elif mode == "runner_plus_inner":
        r = [b for b in frames if base_of_block(b) == "runner.py"]
        keep, seen = [], []
        for b in (r[:1] + frames[-1:]):
            if b not in keep:
                keep.append(b)
        keep = keep or frames[-1:]
    elif mode == "prod_last_only":
        prod = [b for b in frames if not is_noise(base_of_block(b))]
        keep = prod[-1:] if prod else []
    else:
        raise ValueError(mode)
    return head + "".join("".join(b) for b in keep) + tail


def pseudo(text):
    return "Exception: 见 locator.py 第 12 行（栈已省略）"


def A(sigs):
    return all(sigs[a] != sigs[b] for a, b in PAIRS)


def B(sigs):
    return A(sigs) and all(any(b == "runner.py" for b, _ in sigs[s]) for s in v.SURFACES)


def C(sigs):
    st = {s: tuple(x for x in sigs[s] if not is_noise(x[0])) for s in v.SURFACES}
    return all(st[a] != st[b] for a, b in PAIRS) and all(len(set(st[s])) >= 2 for s in v.SURFACES)


async def main():
    with tempfile.TemporaryDirectory() as td:
        exe = os.path.join(td, "s12b-cli.exe")
        open(exe, "wb").write(b"MZ" + b"\x00" * 32)
        raw = {}
        for s in v.SURFACES:
            got, msg = await v.trigger(s, exe)
            assert got == "unknown", (s, got)
            raw[s] = msg

        modes = ["full", "first1", "inner1", "outer_inner", "runner_plus_inner",
                 "prod_last_only", "pseudo"]
        print("=" * 100)
        print("对撞矩阵   A=云测原版  B=+含runner.py  C=剔除测试帧后>=2且两两不等")
        print("          期望：full 绿；其余 6 种全红")
        print("=" * 100)
        print(f"{'退化形态':22s} {'A 原版':>10s} {'B runner':>10s} {'C 云严':>10s}   判定")
        print("-" * 100)
        rows = {}
        for mode in modes:
            texts = {s: (pseudo(raw[s]) if mode == "pseudo" else degrade(raw[s], mode)) for s in v.SURFACES}
            sigs = {s: v.sig(texts[s]) for s in v.SURFACES}
            ra, rb, rc = A(sigs), B(sigs), C(sigs)
            rows[mode] = (ra, rb, rc)
            want = (mode == "full")
            verdict = "OK" if rc == want else "** C 错 **"
            print(f"{mode:22s} {'GREEN' if ra else 'RED':>10s} {'GREEN' if rb else 'RED':>10s} "
                  f"{'GREEN' if rc else 'RED':>10s}   {verdict}")

        print()
        print("=" * 100)
        print("C 判据逐面明细（剔除测试帧后）")
        print("=" * 100)
        for mode in modes:
            texts = {s: (pseudo(raw[s]) if mode == "pseudo" else degrade(raw[s], mode)) for s in v.SURFACES}
            sigs = {s: v.sig(texts[s]) for s in v.SURFACES}
            st = {s: tuple(x for x in sigs[s] if not is_noise(x[0])) for s in v.SURFACES}
            print(f"\n--- {mode} ---")
            for s in v.SURFACES:
                d = len(set(st[s]))
                print(f"   {s:18s} {d} 个不重复  {st[s]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
