"""真机端到端：用插件自己的模块（app_registry → sequence → jobs.execute）驱动真实 CLI。

与 tests/test_smoke_integration.py 的区别：那条是裸 subprocess 验 argv 契约；
这条走**我们自己的代码路径**（argv 构造 + runner 管道排空 + jobs 终态机 + 结果解析），
证明插件逻辑本身能跑通真 CLI，并覆盖 v1 承诺的全部 3 个 app。

用法：python probe/verify_e2e_real.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "artcraft_ops", "tests"))

# 复用 tests/conftest.py 的两件事：把 artcraft_ops 预注册成命名空间包（不执行
# __init__.py）、装 plugin.sdk.plugin 的 stub。这样在 N.E.K.O 宿主外也能 import 实现模块。
import conftest  # noqa: E402,F401  （import 即生效，无副作用外溢）

from artcraft_ops import app_registry, jobs, locator, roots, sequence, spec as spec_mod  # noqa: E402

SVG_IN = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
    '<rect width="120" height="80" fill="#c0392b"/></svg>'
)

CASES = [
    {
        "app": "photocraft",
        "label": "5 步图层流水线（新建 → 建层 → 填充 → 建层 → 填充 → 检视）",
        "source": {"new_doc": {"width": 640, "height": 400, "background": "white"}},
        "steps": [
            ("layer.new.layer", {"name": "Accent"}),
            ("edit.fill", {"color": "#c0392b"}),
            ("layer.new.layer", {"name": "Veil", "opacity": 0.5}),
            ("edit.fill", {"color": "#f1c40f"}),
            ("document.inspect", {}),
        ],
        "out_name": "e2e_layers.png",
        "expect_magic": b"\x89PNG\r\n\x1a\n",
    },
    {
        "app": "vectorcraft",
        "label": "打开 SVG → 检视 → 导出（--in / --export 适配）",
        "source_file": "in.svg",
        "source": {},
        "steps": [("document.inspect", {})],
        "out_name": "out.svg",
        "expect_magic": b"<?xml",
    },
    {
        "app": "craft_anything",
        "label": "tool 子命令 core_session_show（--json --project 适配）",
        "source": {},
        "steps": [("core_session_show", {})],
        "out_name": None,
        "expect_magic": None,
    },
]


def run_case(case: dict, base: str) -> bool:
    print("\n" + "=" * 78)
    print(f"[{case['app']}] {case['label']}")
    print("=" * 78)

    sp = app_registry.get_spec(case["app"])
    sp = dataclasses.replace(sp, cli_path=locator.resolve_cli(case["app"]))
    print(f"  cli_path        = {sp.cli_path}")
    print(f"  原生分隔符       = {os.sep in sp.cli_path}")

    # 落盘统一挂到本次 run 的 base 下，跑完由 main() 一次性清理（不再一个 app 一个
    # mkdtemp 永留 %TEMP%）。设 ARTCRAFT_OPS_KEEP_ARTIFACTS=1 可保留产物排查。
    tmp = os.path.join(base, case["app"])
    os.makedirs(tmp, exist_ok=True)
    policy = roots.default_policy(tmp, tmp)
    out = os.path.join(tmp, case["out_name"]) if case["out_name"] else ""

    src_kwargs = dict(case["source"])
    if case.get("source_file"):
        src_path = os.path.join(tmp, case["source_file"])
        with open(src_path, "w", encoding="utf-8") as fh:
            fh.write(SVG_IN)
        src_kwargs["input_file"] = src_path
    elif case["app"] == "craft_anything":
        # §15.2：--project 必须指向**尚不存在**的路径
        src_kwargs["input_file"] = os.path.join(tmp, "proj.cany")

    steps = [sequence.Step(cmd=c, params=p) for c, p in case["steps"]]
    argv = sequence.build_argv(sp, steps, spec_mod.Source(**src_kwargs), out, policy)
    leaked = [t for t in argv if "automation-read-root" in t or "automation-write-root" in t]
    print(f"  argv            = {argv[1:]}")
    print(f"  root flag 泄漏   = {leaked}")

    job = jobs.create_job(case["app"], argv, is_edit_sequence=True)
    job.step_total = len(steps)
    res = asyncio.run(jobs.execute(job, None, sp, policy))

    print(f"  ok / exit / status = {res.ok} / {res.exit_code} / {jobs.status_value(job)}")
    print(f"  error_code      = {res.error_code or '(none)'}")
    print(f"  协议转录条数      = {len(res.steps)}")
    from artcraft_ops import result as result_mod
    cmd_n = result_mod.command_step_count(res.steps)
    print(f"  命令步数（口径）  = {cmd_n}  (期望 {len(steps)})")
    print(f"  user_message    = {res.user_message.splitlines()[0] if res.user_message else '(empty)'}")
    if res.warnings:
        print(f"  warnings        = {list(res.warnings)}")

    checks = {
        "ok": res.ok,
        "exit0": res.exit_code == 0,
        "no_root_flag": not leaked,
        "command_count": cmd_n == len(steps),
        "user_message_count": f"共 {len(steps)} 步" in (res.user_message or ""),
    }
    if out:
        exists = os.path.isfile(out)
        size = os.path.getsize(out) if exists else 0
        magic_ok = True
        if exists and case["expect_magic"]:
            with open(out, "rb") as fh:
                magic_ok = fh.read(len(case["expect_magic"])) == case["expect_magic"]
        print(f"  产物            = exists={exists} size={size} magic_ok={magic_ok}")
        checks["output_exists"] = exists and size > 0
        checks["output_magic"] = magic_ok
    if case["app"] == "craft_anything" and res.steps:
        first = res.steps[0]
        print(f"  CA 首条结果键    = {sorted(first)[:6]}")

    bad = [k for k, v in checks.items() if not v]
    print(f"  --> {'PASS' if not bad else 'FAIL: ' + ', '.join(bad)}")
    return not bad


def main() -> int:
    base = tempfile.mkdtemp(prefix="artcraft_e2e_")
    print(f"workdir = {base}")
    results = [(c["app"], run_case(c, base)) for c in CASES]
    print("\n" + "=" * 78)
    for app, ok in results:
        print(f"  {app:16s} {'PASS' if ok else 'FAIL'}")
    passed = sum(1 for _a, ok in results if ok)
    print(f"\n=== {passed}/{len(results)} {'ALL PASS' if passed == len(results) else 'HAS FAILURE'} ===")

    # 收尾：不留 %TEMP% 垃圾（排查时用 ARTCRAFT_OPS_KEEP_ARTIFACTS=1 保留）
    if os.environ.get("ARTCRAFT_OPS_KEEP_ARTIFACTS") == "1":
        print(f"artifacts kept: {base}")
    else:
        shutil.rmtree(base, ignore_errors=True)
        print(f"workdir cleaned: {not os.path.exists(base)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
