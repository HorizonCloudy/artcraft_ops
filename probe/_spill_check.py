"""手工验 spill 回执的步数口径（临时代码，不属于 tests/）。

用法：python probe/_spill_check.py
把 SpillPolicy.max_bytes 压到 512 逼出 spill 分支，对 PhotoCraft 那 5 步真机跑一次，
打印 spill 回执的 summary / result_handle / step_count。
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

import conftest  # noqa: E402,F401  （命名空间包 + plugin.sdk stub）

from artcraft_ops import (  # noqa: E402
    app_registry, jobs, locator, result as result_mod, roots, sequence, spec as spec_mod,
)


def main() -> int:
    sp = app_registry.get_spec("photocraft")
    sp = dataclasses.replace(sp, cli_path=locator.resolve_cli("photocraft"))

    tmp = tempfile.mkdtemp(prefix="artcraft_spill_")
    policy = roots.default_policy(tmp, tmp)
    out = os.path.join(tmp, "spill.png")

    steps = [
        sequence.Step(cmd="layer.new.layer", params={"name": "Accent"}),
        sequence.Step(cmd="edit.fill", params={"color": "#c0392b"}),
        sequence.Step(cmd="layer.new.layer", params={"name": "Veil", "opacity": 0.5}),
        sequence.Step(cmd="edit.fill", params={"color": "#f1c40f"}),
        sequence.Step(cmd="document.inspect", params={}),
    ]
    src = spec_mod.Source(new_doc={"width": 640, "height": 400, "background": "white"})
    argv = sequence.build_argv(sp, steps, src, out, policy)

    job = jobs.create_job("photocraft", argv, is_edit_sequence=True)
    job.step_total = len(steps)
    res = asyncio.run(jobs.execute(job, None, sp, policy))
    print(f"ok / exit       = {res.ok} / {res.exit_code}")
    print(f"协议转录条数      = {len(res.steps)}")
    print(f"命令步数（口径）  = {result_mod.command_step_count(res.steps)}")

    # 把 max_bytes 压到 512 → 必然走 spill 分支
    # spill_dir 显式指到本次临时目录：不往 %TEMP%\artcraft_ops_results 留垃圾
    # （否则验收时会被误认成插件自身行为）
    tight = dataclasses.replace(spec_mod.SpillPolicy(), max_bytes=512, spill_dir=tmp)
    reply = asyncio.run(result_mod.materialize(res, tight))
    print(f"materialize 状态 = {reply.get('status') or '(未 spill)'}")
    print(f"payload_bytes   = {res.payload_bytes}")
    print(f"result_handle   = {reply.get('result_handle')}")
    print("---- summary ----")
    print(reply.get("summary"))
    print("-----------------")

    handle = str(reply.get("result_handle") or "")
    spill_ok = os.path.isfile(handle) if handle else False
    print(f"落盘文件存在     = {spill_ok} ({handle})")

    bad = []
    if reply.get("status") != "spilled":
        bad.append("spill 未触发")
    if "共 5 步" not in str(reply.get("summary") or ""):
        bad.append("summary 步数不对")
    if "共 0 步" in str(reply.get("summary") or ""):
        bad.append("summary 写成 0 步")
    print(f"VERDICT: {'PASS' if not bad else 'FAIL: ' + ', '.join(bad)}")

    # 收尾清理本次临时目录（含 CLI 产物与落盘 JSON），不留残留
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
