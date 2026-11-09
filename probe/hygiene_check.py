"""验收卫生审计：隔离 TEMP + 仓库快照 + home 浅层快照（三者都空才算过）。

用法：python probe/hygiene_check.py
为什么不是「扫 %TEMP% 找新文件」：
- 扫 %TEMP% 会抓到自己这次的输出文件、harness 写的 cv_debug.log / codebuddy-* / UUID *.tmp
  → 假红；靠 grep -v 黑名单打补丁，harness 换名就再红一次
- 反过来，被测代码写进黑名单里的名字 → 假绿
- 且全树遍历 O(n)，随机器上别的项目产物变慢

隔离法把噪音从构造上消掉：给 pytest 子进程换成空 TEMP，
`tempfile.gettempdir()` 在子进程里读 TEMP，所以一切「默认落点」都会落进隔离目录。
残余盲区（``expanduser("~")`` / cwd / 硬编码绝对路径）由另外两个快照补上。

已知覆盖边界（务必如实告知，别当全 covered）：
- **home 腿只扫浅两层**：``~/.local/share/xxx`` 这类三层以上泄漏逃得掉（性能换覆盖，已接受）
- 硬编码到 ``~`` 与仓库之外的绝对路径（如 ``C:\\ProgramData``）不在任何一条腿里
- 隔离 TEMP 只劫持 ``TEMP/TMP/TMPDIR``；若子进程读 ``LOCALAPPDATA``/``USERPROFILE`` 派生的临时路径，仍会逃
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".workbuddy", "node_modules"}
# 验收不能被挂死的子进程拖住；真机 CLI 卡住时这条是唯一的出口
_PYTEST_TIMEOUT_S = 600


def _scan_repo() -> set[str]:
    """仓库内（排除缓存/版本库/记忆目录）的相对路径快照。"""
    seen: set[str] = set()
    for root, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            seen.add(os.path.relpath(os.path.join(root, name), _ROOT))
    return seen


def _scan_home() -> set[str]:
    """home 浅两层快照：抓 expanduser("~") 派生路径的泄漏。"""
    home = os.path.expanduser("~")
    seen: set[str] = set()
    try:
        for entry in os.scandir(home):
            if entry.is_file():
                seen.add(entry.name)
            elif entry.is_dir() and entry.name not in _SKIP_DIRS:
                try:
                    for sub in os.scandir(entry.path):
                        seen.add(os.path.join(entry.name, sub.name))
                except OSError:
                    continue
    except OSError:
        return seen
    return seen


def main() -> int:
    # base 必须**每次唯一**：用固定名（早前是 "artcraft_iso_tmp"）时，两个并发审计会
    # 互相 rmtree —— 后启动的那个在 os.listdir(base) 处 FileNotFoundError，
    # 或者把对方的残留算成自己的泄漏（假 FAIL）。实测并发两跑可稳定复现。
    base = tempfile.mkdtemp(prefix="artcraft_iso_tmp_")
    # 建目录与清目录之间必须 try/finally：审计脚本自己也得守
    # 「makedirs/mkdtemp 必须在异常路径也清理」这条规则
    try:
        repo_before = _scan_repo()
        home_before = _scan_home()

        env = dict(os.environ, TEMP=base, TMP=base, TMPDIR=base)
        bad: list[str] = []
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "artcraft_ops/tests", "-q", "--no-header"],
                cwd=_ROOT, env=env, capture_output=True, text=True,
                timeout=_PYTEST_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            # 没有超时会永远挂着，验收给不出结论；真机 CLI 卡住时这条尤其重要
            print(f"pytest TIMEOUT after {_PYTEST_TIMEOUT_S}s")
            bad.append(f"pytest 超时(> {_PYTEST_TIMEOUT_S}s)")
            print(f"VERDICT: FAIL: {', '.join(bad)}")
            return 1

        print("pytest exit  =", proc.returncode)
        tail = (proc.stdout or "").strip().splitlines()
        print("pytest tail  =", tail[-1] if tail else "(no stdout)")

        residual = os.listdir(base)
        repo_after = _scan_repo()
        home_after = _scan_home()

        repo_new = sorted(repo_after - repo_before)
        home_new = sorted(home_after - home_before)

        print(f"隔离 TEMP 残留 = {residual}")
        print(f"仓库新增       = {repo_new}")
        print(f"home 浅层新增  = {home_new}")

        if proc.returncode != 0:
            bad.append(f"pytest exit={proc.returncode}")
        if residual:
            bad.append("TEMP 有残留")
        if repo_new:
            bad.append("仓库有新增")
        if home_new:
            bad.append("home 有新增")
        print(f"VERDICT: {'PASS' if not bad else 'FAIL: ' + ', '.join(bad)}")
        return 0 if not bad else 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
