"""S17 变异自检：在临时副本上逐条破坏家目录规则，证明用例不是恒绿。不碰工作树。

八组变异，每组只改一处、只跑相关用例，看**红的是不是该红的那几条**：

  M1  删掉 ``redact_text`` 里的家目录循环        -> 全部正向归一用例红（17）
  M2  掩码 ``~`` 改成 ``***``                    -> 精确等值断言全红（17）
  M3  去掉 ``/root`` 的 ``(?=/|$)`` 边界         -> ``/roots`` 误伤用例红（2）
  M4  ``redact()`` 改走 ``redact_text``          -> 功能句柄 + 工具出口 + spill 回归（3）
  M5  ``_details_from`` 改回 ``redact()``        -> details / message 去标识化红（2）
  M6  含空格分支去掉 ``(?=[\\\\/])``              -> 终止性用例红（2）
  M7  ``TOOL_OUTPUT_TEXT_FIELDS`` 删 ``warnings`` -> 工具出口用例红（1）
  M8  jobs metadata 改回裸 ``str()``             -> 消息面 metadata 用例红（1）

M4/M5 是对**划界**的变异：它们证明「去标识化只走文本出口」这条边界有牙齿。
M6 是对**终止性**的变异：它证明「含空格/逗号分支只许在后接分隔符时生效」这条有牙齿。
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

SRC = pathlib.Path(__file__).resolve().parents[1] / "artcraft_ops"
BASE = pathlib.Path(tempfile.gettempdir()) / "mut_s17_home"


def _pick_python() -> str:
    """选一个装了 pytest 的解释器来跑测试子进程。

    本脚本曾直接用 ``sys.executable``：默认 ``python``（3.15）没装 pytest，质量门用它
    启动本脚本时，9 次 pytest 子进程全部静默失败（stdout 空 -> 0 红 -> M1-M8 全判
    「没有任何用例红」，exit 仍为 0）。故先试本解释器，退回项目固定的 Python314；
    两处都没有 pytest 就大声退出，绝不静默产出「全绿」结果。
    """
    candidates = [sys.executable, r"C:\Python314\python.exe"]
    for py in candidates:
        probe = subprocess.run(
            [py, "-m", "pytest", "--version"], capture_output=True, text=True,
        )
        if probe.returncode == 0:
            return py
    sys.exit(f"没有找到装了 pytest 的解释器（试过 {candidates}），变异结果无效")


PY = _pick_python()

TARGETS = [
    "tests/test_redact_home.py",
    "tests/test_result.py::test_spill_summary_keeps_command_step_count",
]


def fresh_copy(tag: str) -> pathlib.Path:
    dst = BASE / tag / "artcraft_ops"
    if dst.parent.exists():
        shutil.rmtree(dst.parent)
    shutil.copytree(
        SRC, dst,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "data", "config"),
    )
    return dst


def patch(dst: pathlib.Path, filename: str, pattern: str, repl: str, label: str) -> bool:
    p = dst / filename
    s = p.read_text(encoding="utf-8")
    s2, n = re.subn(pattern, lambda _m: repl, s, count=1)   # 函数式替换：repl 里的 \w 不是模板转义
    p.write_text(s2, encoding="utf-8")
    # 输出契约（工作流 parseMutation）：每组第一个 ``[M{i}]`` 片段必须含 pytest 摘要与
    # 「红:」名单，故 patch 行用 ``[patch]`` 前缀、不得占用 ``[M{i}]``。
    print(f"[patch] {label} {filename} 替换次数 = {n}")
    return n == 1


def run(dst: pathlib.Path, label: str) -> tuple[int, int]:
    out = subprocess.run(
        [PY, "-m", "pytest", *TARGETS, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=dst, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
    if not lines:      # pytest 层面没跑起来（解释器/环境问题），绝不当作「全绿」
        sys.exit(f"[{label}] pytest 无输出，结果无效；stderr 前 3 行："
                 f"{(out.stderr or '').strip().splitlines()[:3]}")
    summary = lines[-1]
    failed = [ln.split("::")[-1].split(" ")[0]
              for ln in lines if ln.startswith("FAILED ")]
    print(f"[{label}] {summary}")
    for name in failed:
        print(f"    红: {name}")
    print()
    m = re.search(r"(\d+) failed", summary)
    return (int(m.group(1)) if m else 0), len(failed)


print("== 基线 ==")
run(fresh_copy("base"), "BASE(未变异)")

cases = [
    ("errors.py", "M1", r"    for pattern in _REDACT_HOME_PATTERNS:\n        text = pattern\.sub\(REDACT_HOME_MASK, text\)\n", ""),
    ("errors.py", "M2", r'REDACT_HOME_MASK = "~"', 'REDACT_HOME_MASK = "***"'),
    ("errors.py", "M3", r"\(\?<!\[A-Za-z0-9_\.:-\]\)/root\(\?=/\|\$\)", r"(?<![A-Za-z0-9_.:-])/root"),
    ("errors.py", "M4", r"        return _mask_credentials\(obj\)", "        return redact_text(obj)"),
    ("errors.py", "M5", r"out\[key_text\] = redact_text\(value\)", "out[key_text] = redact(value)"),
    ("errors.py", "M6", r"\{_HOME_NAME_SPACED\}\(\?=\[\\\\/\]\)", "{_HOME_NAME_SPACED}"),
    ("spec.py", "M7", r'\n    "warnings",[^\n]*', "\n"),
    ("jobs.py", "M8", r'"result_path": redact_text\(str\(getattr\(job, "result_path", ""\) or ""\)\)',
     '"result_path": str(getattr(job, "result_path", "") or "")'),
]

for filename, label, pattern, repl in cases:
    d = fresh_copy(label.lower())
    if patch(d, filename, pattern, repl, label):
        run(d, label)
    else:
        print(f"[{label}] 锚点未命中，变异未生效 —— 该组结果无效\n")

shutil.rmtree(BASE, ignore_errors=True)      # 卫生：不留 %TEMP%\mut_s17_home
print(f"清理临时副本：{BASE}")
