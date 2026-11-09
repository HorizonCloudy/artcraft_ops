#!/usr/bin/env python
"""artcraft_ops 发布打包器 —— 可复跑、可校验、字节稳定。

用法：
    python tools/build_release.py            # 打包 + 自检
    python tools/build_release.py --verify   # 只校验已有包

设计约束（每条都有原因，别随手改）：
  1. **只打运行时必需文件**：宿主把插件目录整体加载，`tests/` `probe/` 进去了
     只会拖慢安装、并在宿主的模块扫描里制造噪声。
  2. **固定时间戳**：zip 内每个条目用 git 提交时间（可用 SOURCE_DATE_EPOCH 覆盖），
     同样内容重复打包 → 同一份 sha256。否则「发布物哈希」没有任何意义。
  3. **不写 __pycache__ / *.pyc**：那是本机字节码，跨解释器版本必失效。
  4. **打包后回读自检**：解压到临时目录 → py_compile 全模块 → 逐文件比对 sha256，
     任何一步失败即非零退出。不接受「打出来了就算成功」。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(ROOT, "artcraft_ops")
DIST_DIR = os.path.join(ROOT, "dist")

# 运行时必需：清单 + 配置模板 + 文档 + 全部顶层模块
REQUIRED_EXTRA = ("plugin.toml", "config.example.toml", "README.md")
EXCLUDE_DIRS = {"tests", "__pycache__", ".pytest_cache"}
EXCLUDE_SUFFIX = (".pyc", ".pyo")


def plugin_meta() -> tuple[str, str]:
    with open(os.path.join(PLUGIN_DIR, "plugin.toml"), "rb") as fh:
        data = tomllib.load(fh)
    plugin = data["plugin"]
    return str(plugin["id"]), str(plugin["version"])


def collect() -> list[str]:
    """返回（相对插件根的）待打包文件清单，已排序。"""
    out: list[str] = []
    for name in os.listdir(PLUGIN_DIR):
        path = os.path.join(PLUGIN_DIR, name)
        if os.path.isdir(path):
            continue
        if name.endswith(EXCLUDE_SUFFIX):
            continue
        if name.endswith(".py") or name in REQUIRED_EXTRA:
            out.append(name)
    out.sort()
    return out


def source_epoch() -> int:
    """优先 SOURCE_DATE_EPOCH，其次 git 提交时间，最后退回文件 mtime。"""
    env = os.environ.get("SOURCE_DATE_EPOCH")
    if env and env.isdigit():
        return int(env)
    try:
        raw = subprocess.run(
            ["git", "-C", ROOT, "log", "-1", "--format=%ct"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if raw.isdigit():
            return int(raw)
    except Exception:
        pass
    return int(max(os.path.getmtime(os.path.join(PLUGIN_DIR, f)) for f in collect()))


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build() -> tuple[str, str]:
    plugin_id, version = plugin_meta()
    files = collect()
    epoch = source_epoch()
    # zip 的 date_time 最小粒度是 2 秒，且年份不得早于 1980
    import time
    stamp = time.gmtime(max(epoch, 315532800))[:6]

    os.makedirs(DIST_DIR, exist_ok=True)
    zip_path = os.path.join(DIST_DIR, f"{plugin_id}-{version}.zip")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in files:
            src = os.path.join(PLUGIN_DIR, name)
            info = zipfile.ZipInfo(filename=f"{plugin_id}/{name}", date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            with open(src, "rb") as fh:
                zf.writestr(info, fh.read())

    digest = sha256(zip_path)
    size = os.path.getsize(zip_path)
    print(f"[打包] {zip_path}")
    print(f"[打包] {len(files)} 个文件 / {size} B / sha256 {digest}")
    print(f"[打包] 时间戳源 = {epoch} ({time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(epoch))} UTC)")

    # 校验清单落盘（对外发布的哈希以此为准）
    manifest = os.path.join(DIST_DIR, "SHA256SUMS.txt")
    with open(manifest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{digest}  {plugin_id}-{version}.zip\n")
        for name in files:
            fh.write(f"{sha256(os.path.join(PLUGIN_DIR, name))}  {plugin_id}/{name}\n")
    print(f"[打包] 校验清单 {manifest}")

    return zip_path, digest


def verify(zip_path: str) -> None:
    """回读自检：解压 → py_compile → 逐文件 sha256 比对 → 清单一致性。"""
    plugin_id, version = plugin_meta()
    expected = {n: sha256(os.path.join(PLUGIN_DIR, n)) for n in collect()}
    tmp = tempfile.mkdtemp(prefix="artcraft_release_verify_")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise SystemExit(f"[FAIL] zip 损坏，首个坏条目：{bad}")
            names = zf.namelist()
            zf.extractall(tmp)

        # 1) 条目集合必须与预期完全一致（不多不少）
        got = sorted(n.split("/", 1)[1] for n in names)
        if got != sorted(expected):
            raise SystemExit(f"[FAIL] 条目集合不一致：多={set(got)-set(expected)} 少={set(expected)-set(got)}")
        # 2) 顶层目录必须是 plugin_id（宿主按目录名 == id 校验）
        tops = {n.split("/", 1)[0] for n in names}
        if tops != {plugin_id}:
            raise SystemExit(f"[FAIL] 顶层目录应为 {plugin_id}，实际 {tops}")
        # 3) 逐文件 sha256
        for name, want in expected.items():
            got_hash = sha256(os.path.join(tmp, plugin_id, name))
            if got_hash != want:
                raise SystemExit(f"[FAIL] 内容不一致：{name}\n  期望 {want}\n  实际 {got_hash}")
        # 4) 全模块可编译
        py_files = [n for n in expected if n.endswith(".py")]
        for name in py_files:
            py_compile.compile(os.path.join(tmp, plugin_id, name), doraise=True)
        # 5) 清单里声明的版本与 plugin.toml 一致
        if f"{plugin_id}-{version}.zip" not in os.path.basename(zip_path):
            raise SystemExit("[FAIL] 包名与 plugin.toml 版本不一致")

        print(f"[校验] zip 完整 ✓  条目 {len(got)} 个 ✓  顶层目录 {plugin_id}/ ✓")
        print(f"[校验] {len(expected)} 个文件 sha256 全部一致 ✓  {len(py_files)} 个模块编译通过 ✓")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="只校验 dist/ 下已有的包")
    args = ap.parse_args()

    plugin_id, version = plugin_meta()
    zip_path = os.path.join(DIST_DIR, f"{plugin_id}-{version}.zip")

    if args.verify:
        if not os.path.isfile(zip_path):
            print(f"[FAIL] 找不到 {zip_path}，先跑一次打包")
            return 1
        verify(zip_path)
        return 0

    zip_path, _ = build()
    verify(zip_path)
    print("[完成] 发布包就绪：", zip_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
