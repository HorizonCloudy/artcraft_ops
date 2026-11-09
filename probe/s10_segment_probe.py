"""S10 可行性探针：`_assert_safe_segments` 的「段内尾随空格/点」是否真能穿越。

只读 + 临时目录，不改任何生产文件。
判定分三步，任一步为否都不算可利用：
  1. `resolve_and_check` 是否**放行**（不抛 PATH_UNSAFE / PATH_OUTSIDE_ROOTS）；
  2. 放行后返回的路径，交给 `os.stat` 是否真能读到 root 外的文件；
  3. 对照：把尾随空格去掉后，同一路径是否被 `..` 判据正确拒绝。
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

_PLUGIN_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artcraft_ops",
)
sys.path.insert(0, _PLUGIN_ROOT)

# 与 tests/conftest.py 同款：预注册成只有 __path__ 的命名空间包，
# 避免执行 __init__.py（它会 import 宿主 SDK，本机不可用）。
if "artcraft_ops" not in sys.modules:
    _pkg = types.ModuleType("artcraft_ops")
    _pkg.__path__ = [_PLUGIN_ROOT]           # type: ignore[attr-defined]
    _pkg.__package__ = "artcraft_ops"        # type: ignore[attr-defined]
    sys.modules["artcraft_ops"] = _pkg

from artcraft_ops import roots  # noqa: E402
from artcraft_ops.errors import ArtcraftError  # noqa: E402


def attempt(label: str, raw: str, root: str) -> None:
    try:
        got = roots.resolve_and_check(raw, (root,))
    except ArtcraftError as exc:
        print(f"  {label:34s} -> REJECT code={exc.code}")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:34s} -> ERROR {type(exc).__name__}: {exc}")
        return
    # 放行了：看操作系统是否真按穿越语义解析
    verdict = "?"
    try:
        with open(got, "rb") as fh:
            verdict = f"READ OK, {len(fh.read())} bytes"
    except FileNotFoundError:
        verdict = "FileNotFoundError（放行但没读到，说明未穿越）"
    except OSError as exc:
        verdict = f"OSError {exc.errno}: {exc.strerror}"
    print(f"  {label:34s} -> ALLOW  got={got!r}  真打开: {verdict}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="s10_probe_") as tmp:
        base = os.path.join(tmp, "base")
        root = os.path.join(base, "root")
        os.makedirs(root)
        secret = os.path.join(base, "SECRET.txt")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("TOP-SECRET-OUTSIDE-ROOT")

        root_fwd = root.replace("\\", "/")
        base_fwd = base.replace("\\", "/")

        print(f"root   = {root_fwd}")
        print(f"secret = {base_fwd}/SECRET.txt  (在 root 的上一层)\n")

        print("[A] 对照组：正常越界（应 REJECT）")
        attempt("plain ../SECRET.txt", f"{root_fwd}/../SECRET.txt", root_fwd)
        attempt("root 外绝对路径", f"{base_fwd}/SECRET.txt", root_fwd)

        print("\n[B] 尾随空格绕过尝试（`.. ` 段）")
        attempt("'.. /SECRET.txt'", f"{root_fwd}/.. /SECRET.txt", root_fwd)
        attempt("'..  /SECRET.txt' (两空格)", f"{root_fwd}/..  /SECRET.txt", root_fwd)
        attempt("'..\\t/SECRET.txt' (制表符)", f"{root_fwd}/..\t/SECRET.txt", root_fwd)

        print("\n[C] 尾随点绕过尝试（`..` 加尾随点）")
        attempt("'... /SECRET.txt'", f"{root_fwd}/... /SECRET.txt", root_fwd)
        attempt("'.. . /SECRET.txt'", f"{root_fwd}/.. . /SECRET.txt", root_fwd)

        print("\n[D] 设备名尾随空格（Windows 会把 'con ' 当 CON）")
        for name in ("con", "con ", "con.", "nul ", "aux."):
            attempt(f"'root/{name}'", f"{root_fwd}/{name}", root_fwd)

        print("\n[E] 8.3 短名尾随空格")
        attempt("'PROGRA~1 '", f"{root_fwd}/PROGRA~1 ", root_fwd)

        print("\n[F] 直接看 _assert_safe_segments 对段列表的判定")
        for seg in ("..", ".. ", "...", "con", "con ", "con."):
            try:
                roots._assert_safe_segments(f"C:/root/{seg}/x")
                print(f"  seg={seg!r:10s} -> PASS（未拒绝）")
            except ArtcraftError as exc:
                print(f"  seg={seg!r:10s} -> REJECT {exc.code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
