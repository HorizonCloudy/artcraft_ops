"""S10 第二探针：Windows 到底怎么解析「段内尾随空格/点」。

判据只有一个：**同一个字符串，用不同分隔符、不同 API 去打开 root 外的文件，能不能读到。**
能读到 = 可利用；读不到 = 门是宽的，但还构不成穿越。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def try_read(label: str, raw: str) -> None:
    out = "?"
    try:
        with open(raw, "rb") as fh:
            out = f"READ OK <{fh.read().decode()}>"
    except FileNotFoundError:
        out = "FileNotFoundError"
    except NotADirectoryError:
        out = "NotADirectoryError"
    except OSError as exc:
        out = f"OSError {exc.errno}: {exc.strerror}"
    print(f"  {label:44s} {out}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="s10b_") as tmp:
        base = os.path.join(tmp, "base")
        root = os.path.join(base, "root")
        os.makedirs(root)
        with open(os.path.join(base, "SECRET.txt"), "w", encoding="utf-8") as fh:
            fh.write("PWNED")
        # 对照：root 内建一个真文件，用来测「尾随空格是否被 OS 吞掉」
        with open(os.path.join(root, "inside.txt"), "w", encoding="utf-8") as fh:
            fh.write("INSIDE")

        b = base.replace("/", "\\")
        r = root.replace("/", "\\")

        print(f"base = {b}\n")

        print("[1] 基准：正常穿越（应该读到 PWNED）")
        try_read("base\\root\\..\\SECRET.txt", b + "\\root\\..\\SECRET.txt")
        try_read("base/root/../SECRET.txt", base.replace("\\", "/") + "/root/../SECRET.txt")

        print("\n[2] `.. ` 尾随空格（反斜杠分隔符）")
        try_read("base\\root\\.. \\SECRET.txt", b + "\\root\\.. \\SECRET.txt")
        try_read("base\\root\\..  \\SECRET.txt", b + "\\root\\..  \\SECRET.txt")

        print("\n[3] `.. ` 尾随空格（正斜杠分隔符）")
        try_read("base/root/.. /SECRET.txt", base.replace("\\", "/") + "/root/.. /SECRET.txt")

        print("\n[4] `..` 尾随点")
        try_read("base\\root\\..\\SECRET.txt 之外的 '...'", b + "\\root\\...\\SECRET.txt")

        print("\n[5] 尾随空格会不会被 OS 吞掉（对照 inside.txt）")
        try_read("base\\root\\inside.txt（基准）", b + "\\root\\inside.txt")
        try_read("base\\root\\inside.txt （尾随空格）", b + "\\root\\inside.txt ")
        try_read("base\\root\\inside.txt. （尾随点）", b + "\\root\\inside.txt.")

        print("\n[6] 设备名在中段（不是串尾，不会被整体 strip 吃掉）")
        try_read("base\\root\\con \\inside.txt", b + "\\root\\con \\inside.txt")

        print("\n[7] 用 PowerShell 的 [IO.File]::ReadAllText 再验一次（另一套 API）")
        for label, cand in (
            ("'.. ' 反斜杠", b + "\\root\\.. \\SECRET.txt"),
            ("'..'  反斜杠", b + "\\root\\..\\SECRET.txt"),
        ):
            ps = (
                "$ErrorActionPreference='Stop';"
                f"try {{ [IO.File]::ReadAllText('{cand}') }} "
                "catch { 'ERR: ' + $_.Exception.GetType().Name }"
            )
            rc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=30,
            )
            print(f"  {label:44s} rc={rc.returncode} out={rc.stdout.strip()!r} err={rc.stderr.strip()[:80]!r}")

        print("\n[8] cmd.exe 的 dir 怎么看（第三套 API）")
        for label, cand in (
            ("'.. '", b + "\\root\\.. "),
            ("'..'", b + "\\root\\.."),
        ):
            rc = subprocess.run(
                ["cmd", "/c", "dir", "/b", cand],
                capture_output=True, text=True, timeout=30,
            )
            print(f"  {label:44s} rc={rc.returncode} out={rc.stdout.strip()[:120]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
