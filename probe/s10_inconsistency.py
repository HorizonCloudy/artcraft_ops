"""S10 立项证据：证明 `_assert_safe_segments` 存在「串尾严 / 串中宽」的判据不一致。

不主张可利用（`.. ` 已被三套 API 证伪为不可穿越）；只主张**同一逻辑段，
出现在串尾被拒、出现在串中放行**，这不是设计，是 bug。
"""
from __future__ import annotations

import os
import sys
import types

_PLUGIN_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artcraft_ops",
)
sys.path.insert(0, _PLUGIN_ROOT)
if "artcraft_ops" not in sys.modules:
    _pkg = types.ModuleType("artcraft_ops")
    _pkg.__path__ = [_PLUGIN_ROOT]           # type: ignore[attr-defined]
    _pkg.__package__ = "artcraft_ops"        # type: ignore[attr-defined]
    sys.modules["artcraft_ops"] = _pkg

from artcraft_ops import roots  # noqa: E402
from artcraft_ops.errors import ArtcraftError  # noqa: E402

ROOT = "C:/state/data"


def verdict(raw: str) -> str:
    try:
        roots.resolve_and_check(raw, (ROOT,))
    except ArtcraftError as exc:
        return f"REJECT {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR {type(exc).__name__}"
    return "ALLOW"


# (逻辑段, 串尾写法, 串中写法)  —— 两者的**语义**应当一致
CASES = (
    ("..",        f"{ROOT}/..",        f"{ROOT}/../x.png"),
    (".. + 空格",  f"{ROOT}/.. ",       f"{ROOT}/.. /x.png"),
    (".. + 制表",  f"{ROOT}/..\t",      f"{ROOT}/..\t/x.png"),
    (".. + CR",   f"{ROOT}/..\r",       f"{ROOT}/..\r/x.png"),
    (".. + NBSP", f"{ROOT}/..\u00a0",   f"{ROOT}/..\u00a0/x.png"),
    ("con",       f"{ROOT}/con",        f"{ROOT}/con/x.png"),
    ("con + 空格", f"{ROOT}/con ",       f"{ROOT}/con /x.png"),
    ("con.txt",   f"{ROOT}/con.txt",    f"{ROOT}/con.txt/x.png"),
    ("8.3",       f"{ROOT}/PROGRA~1",   f"{ROOT}/PROGRA~1/x.png"),
    ("8.3 + 空格", f"{ROOT}/PROGRA~1 ",  f"{ROOT}/PROGRA~1 /x.png"),
    ("ADS",       f"{ROOT}/ok.png:h",   f"{ROOT}/ok.png:h/x.png"),
    ("ADS + 空格", f"{ROOT}/ok.png:h ",  f"{ROOT}/ok.png:h /x.png"),
)

print(f"root = {ROOT}\n")
print(f"{'逻辑段':<14}{'串尾写法':<22}{'串尾判定':<28}{'串中写法':<24}{'串中判定':<28}{'一致?'}")
print("-" * 130)
bad = 0
for label, at_end, in_mid in CASES:
    v_end = verdict(at_end)
    v_mid = verdict(in_mid)
    same = v_end.split()[0] == v_mid.split()[0]
    if not same:
        bad += 1
    print(f"{label:<14}{at_end:<22}{v_end:<28}{in_mid:<24}{v_mid:<28}{'✓' if same else '✗ 不一致'}")

print(f"\n不一致项：{bad} / {len(CASES)}")

print("\n[段级直查] 只调 _assert_safe_segments（跳过 normalize 的整串 strip）")
for seg in ("..", ".. ", "..\t", "..\r", "..\u00a0", "con", "con ", "con.txt ",
            "PROGRA~1", "PROGRA~1 ", "ok.png:h", "ok.png:h "):
    try:
        roots._assert_safe_segments(f"C:/state/data/{seg}/x")
        print(f"  {seg!r:14s} -> PASS")
    except ArtcraftError as exc:
        print(f"  {seg!r:14s} -> REJECT {exc.code}")

print("\n[对照] 正常路径不得误杀")
for ok in (f"{ROOT}/a/b.png", f"{ROOT}/a b/c.png", f"{ROOT}/中文/图.png", f"{ROOT}/a-b_c.d.png"):
    print(f"  {ok:<34} -> {verdict(ok)}")
