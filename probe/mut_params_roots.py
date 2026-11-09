"""#26 变异自检：在临时副本上改生产代码，证明用例非恒绿。不碰真实工作树。"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

SRC = pathlib.Path(r"C:\Users\54301\WorkBuddy\2026-11-07-22-33-50\artcraft_ops")
BASE = pathlib.Path(tempfile.gettempdir()) / "mut26_run"


def fresh_copy(tag: str) -> pathlib.Path:
    dst = BASE / tag / "artcraft_ops"
    if dst.parent.exists():
        shutil.rmtree(dst.parent)
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return dst


def patch(dst: pathlib.Path, pattern: str, repl: str, label: str) -> bool:
    p = dst / "sequence.py"
    s = p.read_text(encoding="utf-8")
    s2, n = re.subn(pattern, repl, s, count=1)
    p.write_text(s2, encoding="utf-8")
    print(f"[{label}] sequence.py 替换次数 = {n}")
    return n == 1


def run(dst: pathlib.Path, target: str, label: str) -> None:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=dst, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    tail = [ln for ln in out.stdout.strip().splitlines() if ln.strip()][-6:]
    print(f"[{label}] {target}")
    for ln in tail:
        print("   ", ln)
    print()


TARGET = "tests/test_params_roots.py::test_new_doc_channel_uses_explicit_path_keys"

# 基线
d = fresh_copy("base")
run(d, TARGET, "基线(未变异)")

# M-A：NEW_DOC_PATH_KEYS 清空 → 第 3 半（profile 越界必须拒）应红
d = fresh_copy("ma")
ok = patch(d, r'NEW_DOC_PATH_KEYS: frozenset\[str\] = frozenset\(\{[^}]*\}\)',
           'NEW_DOC_PATH_KEYS: frozenset[str] = frozenset()', "M-A")
if ok:
    run(d, TARGET, "M-A NEW_DOC_PATH_KEYS=frozenset()")

# M-B：validate_params 的 extra_path_keys 默认值塞 {"name"} → 第 1 半应红
# 注意锚点必须带 validate_params 的 docstring：`_check_params_paths` 同名形参在
# 它前面、且默认值从不生效（validate_params 一直显式传），改它等于空变异。
d = fresh_copy("mb")
ok = patch(d, r'extra_path_keys: Iterable\[str\] = \(\),\r?\n\) -> None:\r?\n    """params 里禁止凭证类字段',
           'extra_path_keys: Iterable[str] = frozenset({"name"}),\n) -> None:\n    """params 里禁止凭证类字段',
           "M-B")
if ok:
    run(d, TARGET, "M-B 默认值=frozenset({'name'})")
    run(d, "tests/", "M-B 全库")

# M-C：默认值改成通道键表 {"profile"} → 谁在钉「profile 不进默认」？
d = fresh_copy("mc")
ok = patch(d, r'extra_path_keys: Iterable\[str\] = \(\),\r?\n\) -> None:\r?\n    """params 里禁止凭证类字段',
           'extra_path_keys: Iterable[str] = frozenset({"profile"}),\n) -> None:\n    """params 里禁止凭证类字段',
           "M-C")
if ok:
    run(d, "tests/test_params_roots.py", "M-C 默认值=frozenset({'profile'})")

# M-B'：通道键表多塞 name → 第 2 半（文档名不得误杀）应红
d = fresh_copy("mb2")
ok = patch(d, r'NEW_DOC_PATH_KEYS: frozenset\[str\] = frozenset\(\{[^}]*\}\)',
           'NEW_DOC_PATH_KEYS: frozenset[str] = frozenset({"profile", "name"})', "M-B'")
if ok:
    run(d, TARGET, "M-B' NEW_DOC_PATH_KEYS={profile,name}")
