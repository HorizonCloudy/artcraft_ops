"""17 app 真机验收：目录发现 + argv 构造 + 真实执行。

分两段，任一失败都如实记录，不掩盖：

  A. **目录发现**（全 17）：走插件自己的 ``command_catalog.discover()``，
     真实 spawn `<cli> commands [--json]`，解析成 CommandSpec。
     这一条能证明：CLI 定位、子进程、4 种目录形态的解析都对。

  B. **真实执行**（每 app 挑一条只读命令）：走 ``sequence.build_argv()`` →
     ``runner.spawn()``，看 exit code 与首行输出。
     需要输入文件的 app 会如实报「缺素材」，不当成代码缺陷。

用法：python probe/verify_17_apps.py [app_id ...]
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "artcraft_ops", "tests"))

import conftest  # noqa: E402,F401  （预注册命名空间包 + stub SDK）

from artcraft_ops import app_registry, command_catalog, locator, runner, sequence  # noqa: E402
from artcraft_ops import spec as spec_mod  # noqa: E402

# 只读命令的 id 特征——用于挑「跑了也不会改状态」的探针命令
READONLY_HINT = re.compile(
    r"(inspect|\.info$|^info$|version|\.list$|^list$|stats|describe|props|\.get$|summary)",
    re.I,
)

TIMEOUT_S = 45


def _readonly_candidates(items) -> list:
    """只读候选按「最可能零参成功」排序，最多 5 个，逐个试。

    不猜哪条一定能跑（ArtCraft 的 mini-schema 里 `{id}` 这类必填位置参数
    无法可靠识别），而是排好序后由调用方逐条试，第一条成功即停。
    """
    def _no_params(it) -> bool:
        """参数是否「全可选」——只有这种才敢零参跑。

        ArtCraft 的 mini-schema 用 `key?` 表示可选，`key:` 表示必填，
        位置参数写成 `{id}`（没有冒号）。所以：
          * 空 / `{}` / `null`           → 无参数，安全
          * `{a?, b?: ...}`              → 全可选，安全
          * `{id}` / `{id, x, y}`        → 有必填位置参数，**不安全**
        """
        sch = str(getattr(it, "params_schema", "") or "").strip()
        if sch in ("", "{}", "null"):
            return True
        if not sch.startswith("{"):
            return False
        keys = re.findall(r"([A-Za-z_][\w\-]*)\s*(\??)\s*:", sch)
        if not keys:
            return False          # 形如 {id} 的必填位置参数
        return all(q for _, q in keys)

    safe = [
        it for it in items
        if READONLY_HINT.search(it.id) and it.enabled
        and _no_params(it)
        and (it.needs_document is not True)
    ]
    # 退一步：仍然优先「不需要参数」的，避免挑到要必填参数的命令
    if not safe:
        safe = [
            it for it in items
            if READONLY_HINT.search(it.id) and it.enabled and _no_params(it)
        ]
    # 偏好顺序：inspect > info > version > 其它。
    # `*.list` 常带必填参数（vectorcraft 的 file.list 就是），排到最后。
    def _rank(it) -> tuple[int, int]:
        cid = it.id.lower()
        if "inspect" in cid:
            return (0, len(cid))
        if cid.endswith(".info") or cid == "info":
            return (1, len(cid))
        if "version" in cid:
            return (2, len(cid))
        if cid.endswith(".list") or cid == "list":
            return (4, len(cid))
        return (3, len(cid))

    if safe:
        return sorted(safe, key=_rank)[:5]
    loose = [it for it in items if READONLY_HINT.search(it.id) and it.enabled]
    return sorted(loose, key=_rank)[:5]


async def probe_app(app_id: str) -> dict:
    rec: dict = {"app": app_id}
    spec = app_registry.get_spec(app_id)

    # --- CLI 定位 ---
    try:
        cli = locator.resolve_cli(app_id, {})
        spec = __import__("dataclasses").replace(spec, cli_path=str(cli))
        rec["cli"] = str(cli)
        rec["cli_ok"] = os.path.isfile(cli)
    except Exception as exc:
        rec["cli_ok"] = False
        rec["cli_error"] = f"{type(exc).__name__}: {exc}"
        return rec

    # --- A. 目录发现 ---
    try:
        items = await command_catalog.discover(app_id, spec, refresh=True)
        rec["catalog_count"] = len(items)
        rec["catalog_mode"] = spec.commands_mode
        rec["catalog_ok"] = len(items) > 0
        rec["catalog_sample"] = [it.id for it in items[:3]]
    except Exception as exc:
        rec["catalog_ok"] = False
        rec["catalog_count"] = 0
        rec["catalog_error"] = f"{type(exc).__name__}: {exc}"
        items = ()

    # --- B. 真实执行 ---
    cands = _readonly_candidates(items)
    if not cands:
        rec["exec_ok"] = None
        rec["exec_note"] = "无只读命令可挑（目录为空或无匹配）" if not items else "无匹配只读命令"
        return rec

    if spec.requires_input:
        rec["exec_ok"] = None
        rec["exec_note"] = f"需要输入文件（{spec.save_flag or '-o'} 签名），探针不造假素材"
        return rec

    attempts = []
    for cmd in cands:
        step = spec_mod.Step(cmd=cmd.id, params={})
        # 能给 --new 的 app 就先起个空文档，否则 run 会报缺 <FILE|--new>
        new_doc = {"width": 160, "height": 120} if spec.new_flag == "--new" else None
        source = spec_mod.Source(new_doc=new_doc)
        tmpdir = tempfile.mkdtemp(prefix=f"ac17_{app_id}_")
        script_path = ""
        if spec.builder == "script_file":
            ext = ".jsonl" if spec.script_format in ("jsonl_id", "jsonl_cmd") else ".json"
            script_path = os.path.join(tmpdir, f"{app_id}.script{ext}")
        try:
            argv = sequence.build_argv(spec, [step], source, "", None, script_path=script_path)
            handle = await runner.spawn(
                argv, cwd=os.path.dirname(str(cli)) or os.getcwd(),
                env=locator.build_cli_env(), job_id=f"probe17-{app_id}", timeout=TIMEOUT_S,
            )
            try:
                raw = await asyncio.wait_for(
                    runner.stream_lines(handle, None), timeout=TIMEOUT_S,
                )
            except (asyncio.TimeoutError, TimeoutError):
                await runner.terminate(handle)
                attempts.append((cmd.id, False, "超时"))
                continue
            rc = int(raw.returncode)
            first = (raw.stdout_lines or [""])[0]
            err = str(getattr(raw, "stderr_text", "") or "").strip()[:160]
            attempts.append((cmd.id, rc == 0, err or first[:120]))
            if rc == 0:
                rec["exec_ok"] = True
                rec["exit_code"] = rc
                rec["cmd"] = cmd.id
                rec["argv"] = argv
                rec["stdout_first"] = first[:160]
                rec["tried"] = [a[0] for a in attempts]
                shutil.rmtree(tmpdir, ignore_errors=True)
                return rec
        except Exception as exc:
            attempts.append((cmd.id, False, f"{type(exc).__name__}: {exc}"[:120]))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    rec["exec_ok"] = False
    rec["tried"] = [a[0] for a in attempts]
    last = attempts[-1] if attempts else ("-", False, "无候选")
    rec["exec_note"] = f"试了 {len(attempts)} 条只读命令均失败；最后 {last[0]}: {last[2]}"
    return rec


async def main() -> int:
    only = sys.argv[1:]
    apps = only or sorted(app_registry.APP_SPECS)
    results = []
    for app_id in apps:
        print(f"… {app_id}", flush=True)
        results.append(await probe_app(app_id))

    print()
    print(f"{'app':16s} {'cli':5s} {'目录':6s} {'模式':13s} {'执行':5s} 备注")
    print("-" * 108)
    for r in results:
        cli_s = "OK" if r.get("cli_ok") else "FAIL"
        cat_s = str(r.get("catalog_count", "-")) if r.get("catalog_ok") else "FAIL"
        mode = str(r.get("catalog_mode", "-"))
        ex = r.get("exec_ok")
        ex_s = "PASS" if ex is True else ("FAIL" if ex is False else "n/a")
        note = (
            r.get("exec_error") or r.get("catalog_error") or r.get("exec_note")
            or r.get("stderr") or r.get("stdout_first") or r.get("cli_error") or ""
        )
        print(f"{r['app']:16s} {cli_s:5s} {cat_s:6s} {mode:13s} {ex_s:5s} {str(note)[:70]}")

    print()
    cat_ok = sum(1 for r in results if r.get("catalog_ok"))
    exec_ok = sum(1 for r in results if r.get("exec_ok") is True)
    exec_fail = sum(1 for r in results if r.get("exec_ok") is False)
    print(f"目录发现      : {cat_ok}/{len(results)}")
    print(f"真实执行 PASS : {exec_ok}   FAIL: {exec_fail}")
    return 0 if cat_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
