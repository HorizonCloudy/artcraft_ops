"""§10 #8 / #9 / #20 / #26 — 逐行 JSON 解析、警告透传、用户文案。

覆盖：正常路径（§6.1 真实 stdout 回放、拍平警告原样透传、inspect 提取）
      + 边界（8 MiB 截断、en 语言也不翻译警告、畸形行跳过）
      + 错误输入（exit 2 整批无产物、exit 1、无 inspect）
      + 步数计数（真机回归：VectorCraft 伪步 open/export 不得算成步）

夹具全部来自 RESULTS §6.1 / §6.2 的真实回放与主理人真机验收记录，不启动 CLI。
"""
from __future__ import annotations

import asyncio
import json
import os

from _fixtures import (
    FLATTEN_WARNING_FULL_LINE,
    FLATTEN_WARNING_SUBSTR,
    INSPECT_RESULT,
    LAYER_PIPELINE_OUT_FILE,
    LAYER_PIPELINE_STDERR,
    LAYER_PIPELINE_STDOUT_LINES,
    MAX_REPLY_BYTES,
    VECTORCRAFT_STDOUT_LINES,
)


def _raw(impl, *, rc=0, lines=(), stderr="", truncated=False):
    return impl("runner").RawOutput(
        returncode=rc, stdout_lines=tuple(lines), stderr_text=stderr,
        truncated=truncated,
    )


def _job(impl, *, result_path=""):
    return impl("spec").JobState(
        job_id="job-e2e-1", app_id="photocraft", result_path=result_path,
    )


def _spec(impl):
    return impl("app_registry").get_spec("photocraft")


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
def test_e2e_layers_pipeline(impl):
    """#8 P0 正常：RESULTS §6.1 真实 stdout 回放 -> 5 步 / Veil(id=4) / 拍平警告 / png。"""
    result = impl("result")
    r = result.parse_exec_output(
        _raw(impl, lines=LAYER_PIPELINE_STDOUT_LINES, stderr=LAYER_PIPELINE_STDERR),
        _spec(impl),
        _job(impl, result_path=LAYER_PIPELINE_OUT_FILE),
    )

    assert r.ok is True
    assert len(r.steps) == 5, "5 条 stdout 行必须解析成 5 个 step"
    assert [s.get("command") for s in r.steps] == [
        "layer.new.layer", "edit.fill", "layer.new.layer",
        "edit.fill", "document.inspect",
    ]

    assert r.inspect is not None
    veil = [layer for layer in r.inspect["layers"] if layer["id"] == 4]
    assert len(veil) == 1
    assert veil[0]["name"] == "Veil"
    assert veil[0]["opacity"] == 0.5
    assert r.inspect["activeLayer"] == 4
    assert r.inspect["width"] == 640 and r.inspect["height"] == 400

    assert any(FLATTEN_WARNING_SUBSTR in w for w in r.warnings), \
        "拍平警告必须原样进 warnings"
    assert LAYER_PIPELINE_OUT_FILE in r.output_files
    assert any(f.endswith(".png") for f in r.output_files)


def test_flatten_warning_verbatim(impl):
    """#9 P0 正常：user_message 含完整英文警告串，未翻译、未截断。"""
    result = impl("result")
    spec_mod = impl("spec")
    warnings = result.collect_warnings(LAYER_PIPELINE_STDERR, _spec(impl))

    assert any(FLATTEN_WARNING_SUBSTR in w for w in warnings)
    assert all("拍平" not in w and "扁平化" not in w for w in warnings), "不得翻译成中文"

    r = spec_mod.ExecResult(
        ok=True, app_id="photocraft", warnings=warnings,
        output_files=(LAYER_PIPELINE_OUT_FILE,),
    )
    msg = result.compose_user_message(r, "zh")
    assert FLATTEN_WARNING_SUBSTR in msg, "完整英文警告串必须出现在用户文案里"
    assert "拍平" not in msg and "扁平化" not in msg


def test_extract_inspect_returns_result(impl):
    """正常：从 steps 中精确抽出 document.inspect 的 result 字典。"""
    result = impl("result")
    got = result.extract_inspect((
        {"command": "edit.fill", "result": {"contents": "color"}},
        {"command": "document.inspect", "result": INSPECT_RESULT},
    ))
    assert got == INSPECT_RESULT
    assert got["selectedLayers"] == [4]


def test_extract_inspect_custom_command(impl):
    """边界：EffectCraft 只在显式给 inspect_command 时认 comp.info，默认不臆造。"""
    result = impl("result")
    steps = (
        {"command": "comp.new", "result": {"comp": 1}},
        {"command": "comp.info", "result": {"id": 1, "name": "ProbeComp"}},
    )
    assert result.extract_inspect(steps, "comp.info") == {"id": 1, "name": "ProbeComp"}
    assert result.extract_inspect(steps) is None


def test_collect_warnings_only_flatten(impl):
    """正常：只把拍平警告收进 warnings，普通 stderr 噪声不误报为警告。"""
    result = impl("result")
    stderr = "info: gpu backend ready\n" + LAYER_PIPELINE_STDERR
    warnings = result.collect_warnings(stderr, _spec(impl))
    assert any(FLATTEN_WARNING_SUBSTR in w for w in warnings)
    assert all("gpu backend" not in w for w in warnings)


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_compose_user_message_en_keeps_warning_verbatim(impl):
    """#9 边界：英文语言下警告原文同样一字不改（不翻译、不截断）。"""
    result = impl("result")
    spec_mod = impl("spec")
    warnings = (FLATTEN_WARNING_FULL_LINE,)
    r = spec_mod.ExecResult(ok=True, app_id="photocraft", warnings=warnings)

    for lang in ("zh", "en"):
        msg = result.compose_user_message(r, lang)
        assert FLATTEN_WARNING_SUBSTR in msg
        assert "拍平" not in msg


def test_reply_8mib_truncate(impl):
    """#20 边界：stdout 累计 > 8 MiB -> LIMIT_REPLY 提示，且不抛异常。"""
    result = impl("result")
    big_lines = tuple(
        json.dumps({"command": "edit.fill", "result": {"blob": "y" * 400_000}})
        for _ in range(25)
    )                                                  # ~10 MiB，每行 < 1 MiB
    assert sum(len(x) for x in big_lines) > MAX_REPLY_BYTES

    r = result.parse_exec_output(
        _raw(impl, lines=big_lines), _spec(impl), _job(impl),
    )
    hit = (
        "LIMIT_REPLY" in (r.error_code or "")
        or any("LIMIT_REPLY" in w or "截断" in w for w in r.warnings)
        or "LIMIT_REPLY" in (r.user_message or "")
    )
    assert hit, "超过 8 MiB 必须以 LIMIT_REPLY / 截断提示告知用户"


def test_parse_exec_output_malformed_line_skipped(impl):
    """边界/错误：单行坏 JSON 被跳过，前后有效行仍解析，不抛异常。"""
    result = impl("result")
    r = result.parse_exec_output(
        _raw(impl, lines=("this-is-not-json", LAYER_PIPELINE_STDOUT_LINES[1])),
        _spec(impl), _job(impl),
    )
    assert any(s.get("command") == "edit.fill" for s in r.steps)


# ---------------------------------------------------------------------------
# 错误输入
# ---------------------------------------------------------------------------
def test_exit2_no_output(impl):
    """#26 P0 错误：exit 2 用法错误 -> CLI_USAGE_ERROR 且整批不保存任何产物。"""
    result = impl("result")
    r = result.parse_exec_output(
        _raw(impl, rc=2, stderr="usage error: --out == FILE is not allowed"),
        _spec(impl), _job(impl),
    )

    assert r.ok is False
    assert r.error_code == "CLI_USAGE_ERROR"
    assert r.output_files == ()
    assert r.steps == ()
    # #31：关键码必须写进 message（Err.code 会被宿主抹平为 INTERNAL）
    assert r.error_message.startswith("CLI_USAGE_ERROR: ")


def test_exit1_maps_cli_task_failed(impl):
    """错误：exit 1 -> CLI_TASK_FAILED（与 exit 2 的用法错误区分开）。"""
    result = impl("result")
    r = result.parse_exec_output(
        _raw(impl, rc=1, stderr="command execution failed: disk full"),
        _spec(impl), _job(impl),
    )
    assert r.error_code == "CLI_TASK_FAILED"
    assert r.ok is False


def test_extract_inspect_returns_none_when_absent(impl):
    """错误：序列里没有 document.inspect -> inspect is None，不臆造文档状态。"""
    result = impl("result")
    assert result.extract_inspect(()) is None
    assert result.extract_inspect(({"command": "edit.fill", "result": {}},)) is None


# ---------------------------------------------------------------------------
# 步数计数（真机回归：VectorCraft 伪步 open/export 不得算成步）
#
# 契约 ``result.command_step_count(steps) -> int``：
#   - 返回 steps 中 ``isinstance(s, dict) and s.get("command")`` 为真的条目数
#   - 若该数为 0，回退 ``len(steps or ())``（覆盖 Craft Anything 纯 {"result": …} 形状）
#   - 容忍 None / 非 dict 元素（跳过，不抛）
#   - **不改变** ExecResult.steps 的数据形状（完整协议转录仍保留）
# ---------------------------------------------------------------------------
def test_vectorcraft_pseudo_steps_not_counted(impl):
    """核心回归：vectorcraft 只下 1 个 --cmd，stdout 3 行（open/cmd/export）只算 1 步。

    [FACT] 真机 stdout 3 行，只有中间那行带 ``command``，open/export 是协议伪步：
      0  step=open    command=None                keys=['path','result','step']
      1  step=cmd     command=document.inspect    keys=['command','result','step']
      2  step=export  command=None                keys=['result','step']
    修复前用户看到「共 3 步」（result.py:107-120 把每个可解析行都 append 进 steps）。
    """
    result = impl("result")
    spec_mod = impl("spec")
    spec_vc = impl("app_registry").get_spec("vectorcraft")
    job = spec_mod.JobState(job_id="job-vc-1", app_id="vectorcraft", result_path="out.svg")

    r = result.parse_exec_output(
        _raw(impl, lines=VECTORCRAFT_STDOUT_LINES), spec_vc, job,
    )

    # ① 协议转录完整保留（数据不丢：export 的 bytes/format/path 对用户有价值）
    assert len(r.steps) == 3, "3 行都必须保留在 steps 里"
    assert [s.get("command") for s in r.steps] == [None, "document.inspect", None]
    assert [s.get("step") for s in r.steps] == ["open", "cmd", "export"]

    # ② 计数只认带 command 的行
    assert result.command_step_count(r.steps) == 1

    # ③ 用户文案不得再说「共 3 步」
    assert "共 1 步" in r.user_message
    assert "共 3 步" not in r.user_message

    # ④ 消息面回包同口径
    payload = asyncio.run(result.materialize(r, spec_mod.SpillPolicy()))
    assert payload["step_count"] == 1


def test_craft_anything_single_result_line_counts_one(impl):
    """边界：Craft Anything 单行纯 ``{"result": …}``（无 command）→ 回退计数 = 1。"""
    result = impl("result")
    steps = ({"result": {"output": "out.pdf", "pages": 3}},)

    assert result.command_step_count(steps) == 1, "无 command 时必须回退为 len(steps)"


def test_photocraft_five_commands_all_counted(impl):
    """防回归：PhotoCraft §6.1 回放 5 行全是命令 → 计数 5（无 step 键不影响）。"""
    result = impl("result")
    r = result.parse_exec_output(
        _raw(impl, lines=LAYER_PIPELINE_STDOUT_LINES, stderr=LAYER_PIPELINE_STDERR),
        _spec(impl),
        _job(impl, result_path=LAYER_PIPELINE_OUT_FILE),
    )

    assert result.command_step_count(r.steps) == 5
    assert "共 5 步" in r.user_message


def test_command_step_count_degenerate_inputs(impl):
    """错误输入：空序列 / None / 混入非 dict 元素均不抛，且回退计数正确。"""
    result = impl("result")

    assert result.command_step_count(()) == 0
    assert result.command_step_count(None) == 0
    # 全是非 dict：无 command 命中 → 回退 len()，且绝不抛
    assert result.command_step_count(("garbage", None, 42)) == 3
    # 混入一个真命令：按 command 计数，非 dict 元素跳过
    assert result.command_step_count(("garbage", {"command": "edit.fill"}, None)) == 1


def test_spill_summary_keeps_command_step_count(impl, tmp_path):
    """#回归：spill 回执不得丢 steps，摘要必须写「共 5 步」而不是「共 0 步」。

    [FACT] 缺陷形态：``materialize`` 的 spill 分支构造临时 ``ExecResult`` 时没带 ``steps``
    → ``command_step_count(())`` 命中回退分支得 0 → 用户看到「共 0 步」，而实际跑了 N 步。
    N.E.K.O 消息面 512 KiB + ``steps`` 里带完整 ``document.inspect`` 时最容易触发。
    用 §6.1 的 5 步真实回放 + 极小 ``max_bytes=512`` 逼出 spill。
    """
    result = impl("result")
    spec_mod = impl("spec")
    policy = spec_mod.SpillPolicy(max_bytes=512, spill_dir=str(tmp_path))
    r = result.parse_exec_output(
        _raw(impl, lines=LAYER_PIPELINE_STDOUT_LINES, stderr=LAYER_PIPELINE_STDERR),
        _spec(impl),
        _job(impl, result_path=LAYER_PIPELINE_OUT_FILE),
    )
    assert result.command_step_count(r.steps) == 5

    reply = asyncio.run(result.materialize(r, policy))

    # ① 真的走了 spill 分支（不是普通路径的偶然命中）
    assert reply["status"] == "spilled"
    handle = reply["result_handle"]
    assert handle and os.path.isfile(handle), f"spill 必须落盘到真实文件：{handle!r}"

    # ② 摘要步数：真实的 5 步，绝不是回退值的 0 步
    assert "共 5 步" in reply["summary"]
    assert "共 0 步" not in reply["summary"]

    # ③ 落盘文件仍保留完整协议转录（截断只针对消息面，不动落盘数据）
    with open(handle, encoding="utf-8") as fh:
        spilled = json.load(fh)
    assert len(spilled["steps"]) == 5
    assert [s.get("command") for s in spilled["steps"]][-1] == "document.inspect"
