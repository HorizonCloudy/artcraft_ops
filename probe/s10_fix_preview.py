"""S10 修法预演：在进程内替换 `_assert_safe_segments` 为「先 strip 再判」，看效果。

**不改任何生产文件**。目的：
  1. 修法是否把 12/12 拉齐；
  2. 是否误杀正常路径；
  3. 既有应拒形态（字面 `..` / 设备名 / ADS / 8.3）是否仍拒。
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


CASES = (
    ("..",          f"{ROOT}/..",         f"{ROOT}/../x.png"),
    (".. 空格",      f"{ROOT}/.. ",        f"{ROOT}/.. /x.png"),
    (".. 制表",      f"{ROOT}/..\t",       f"{ROOT}/..\t/x.png"),
    (".. CR",       f"{ROOT}/..\r",       f"{ROOT}/..\r/x.png"),
    (".. NBSP",     f"{ROOT}/..\u00a0",   f"{ROOT}/..\u00a0/x.png"),
    ("con",         f"{ROOT}/con",        f"{ROOT}/con/x.png"),
    ("con 空格",     f"{ROOT}/con ",       f"{ROOT}/con /x.png"),
    ("con.txt",     f"{ROOT}/con.txt",    f"{ROOT}/con.txt/x.png"),
    ("8.3",         f"{ROOT}/PROGRA~1",   f"{ROOT}/PROGRA~1/x.png"),
    ("8.3 空格",     f"{ROOT}/PROGRA~1 ",  f"{ROOT}/PROGRA~1 /x.png"),
    ("ADS",         f"{ROOT}/ok.png:h",   f"{ROOT}/ok.png:h/x.png"),
    ("ADS 空格",     f"{ROOT}/ok.png:h ",  f"{ROOT}/ok.png:h /x.png"),
)

# 必须仍放行的正常路径（防误杀）
NORMAL = (
    f"{ROOT}/a/b.png",
    f"{ROOT}/a b/c.png",          # 段内空格合法
    f"{ROOT}/中文/图.png",
    f"{ROOT}/a-b_c.d.png",
    f"{ROOT}/a/b c/d e.png",      # 多段段内空格
    f"{ROOT}/x/y.png ",           # 字面尾随空格（被整串 strip 吃掉，末尾段）
)

# 必须仍拒绝的既有形态（防放水）
MUST_REJECT = (
    f"{ROOT}/../x.png",
    f"{ROOT}/sub/../../x.png",
    f"{ROOT}/CON",
    f"{ROOT}/NUL.txt",
    f"{ROOT}/AUX",
    f"{ROOT}/ok.png:hidden:$DATA",
    f"{ROOT}/PROGRA~1/evil.png",
)


def patched_assert_safe_segments(text: str) -> None:
    """预演版：与现实现唯一差别 = `core = seg.strip()`，后续判据全对 core。"""
    if not text:
        roots._raise("PATH_UNSAFE")
    path_only = text[2:] if roots._DRIVE_RE.match(text) else text
    segments = [seg for seg in path_only.replace("\\", "/").split("/")]
    for seg in segments:
        core = seg.strip()                      # ← 新增
        if core in ("", "."):
            continue
        if core == "..":
            roots._raise("PATH_UNSAFE")
        lowered = core.lower()
        stem = lowered.split(".", 1)[0]
        if stem in roots._DEVICE_NAMES:
            roots._raise("PATH_UNSAFE")
        if ":" in core:
            roots._raise("PATH_UNSAFE")
        if "~" in core:
            roots._raise("PATH_UNSAFE")


def run_table(tag: str) -> int:
    print(f"\n===== {tag} =====")
    bad = 0
    for label, at_end, in_mid in CASES:
        v_end, v_mid = verdict(at_end), verdict(in_mid)
        same = v_end.split()[0] == v_mid.split()[0]
        if not same:
            bad += 1
        flag = "✓" if same else "✗"
        print(f"  {label:<12} 串尾={v_end:<20} 串中={v_mid:<20} {flag}")
    print(f"  不一致：{bad}/{len(CASES)}")
    return bad


def run_normal(tag: str) -> int:
    print(f"\n----- {tag}：正常路径（必须全 ALLOW）-----")
    bad = 0
    for p in NORMAL:
        v = verdict(p)
        if v != "ALLOW":
            bad += 1
            print(f"  ✗ {p!r} -> {v}")
    print(f"  误杀：{bad}/{len(NORMAL)}")
    return bad


def run_reject(tag: str) -> int:
    print(f"\n----- {tag}：既有应拒（必须全 REJECT）-----")
    bad = 0
    for p in MUST_REJECT:
        v = verdict(p)
        if not v.startswith("REJECT"):
            bad += 1
            print(f"  ✗ {p!r} -> {v}")
    print(f"  放水：{bad}/{len(MUST_REJECT)}")
    return bad


orig = roots._assert_safe_segments
tot_bad = run_table("修前") + run_normal("修前") + run_reject("修前")

roots._assert_safe_segments = patched_assert_safe_segments   # 进程内替换
tot_bad += run_table("修后（预演）") + run_normal("修后（预演）") + run_reject("修后（预演）")

roots._assert_safe_segments = orig
print(f"\n>>> 生产文件未改动；预演期间仅进程内替换。总问题数 = {tot_bad}")
