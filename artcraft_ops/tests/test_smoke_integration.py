"""§10 #8 补充 — 真实 CLI 冒烟（默认必须 skip）+ 回放夹具自洽性检查。

团队纪律：全部测试默认不启动 photocraft-cli.exe。真实冒烟需**同时**满足
  ① 环境变量 ARTCRAFT_OPS_RUN_INTEGRATION=1（显式 opt-in）
  ② 本机确实存在 RESULTS §1 记录的 CLI 绝对路径
因此即便本机装了 PhotoCraft，默认 pytest 也一定 skip，不会误启动。
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from _fixtures import (
    FLATTEN_WARNING_FULL_LINE,
    FLATTEN_WARNING_SUBSTR,
    LAYER_PIPELINE_NEW_DOC,
    LAYER_PIPELINE_OUT_FILE,
    LAYER_PIPELINE_STDERR,
    LAYER_PIPELINE_STDOUT_LINES,
    PHOTOCRAFT_CLI,
)

CLI_EXISTS = os.path.isfile(PHOTOCRAFT_CLI)
RUN_INTEGRATION = os.environ.get("ARTCRAFT_OPS_RUN_INTEGRATION") == "1"
SKIP_REASON = (
    "真实冒烟默认跳过；需同时满足 ARTCRAFT_OPS_RUN_INTEGRATION=1 且本机存在 "
    f"{PHOTOCRAFT_CLI}（CLI_EXISTS={CLI_EXISTS}）"
)


# ---------------------------------------------------------------------------
# 常驻（不 skip）：夹具自洽性 —— 证明用的是 RESULTS §6.1 真实回放而不是编造
# ---------------------------------------------------------------------------
def test_replay_fixture_matches_probe_6_1():
    """夹具自洽：5 行 stdout 全是合法 JSON，拍平警告原文完整，产物名精确。"""
    assert len(LAYER_PIPELINE_STDOUT_LINES) == 5
    parsed = [json.loads(line) for line in LAYER_PIPELINE_STDOUT_LINES]
    assert [p["command"] for p in parsed] == [
        "layer.new.layer", "edit.fill", "layer.new.layer",
        "edit.fill", "document.inspect",
    ]
    assert parsed[0]["result"] == {"layer": 3}
    assert parsed[2]["result"] == {"layer": 4}
    assert parsed[1]["result"] == {"contents": "color"}
    assert parsed[4]["result"]["activeLayer"] == 4
    assert [x for x in parsed[4]["result"]["layers"] if x["id"] == 4][0]["name"] == "Veil"

    assert LAYER_PIPELINE_STDERR.strip() == FLATTEN_WARNING_FULL_LINE
    assert FLATTEN_WARNING_SUBSTR in LAYER_PIPELINE_STDERR
    assert LAYER_PIPELINE_OUT_FILE == "probe/e2e_layers.png"
    assert LAYER_PIPELINE_NEW_DOC == {"width": 640, "height": 400, "background": "white"}


# ---------------------------------------------------------------------------
# 真实冒烟（默认 skip）
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.skipif(not (RUN_INTEGRATION and CLI_EXISTS), reason=SKIP_REASON)
def test_e2e_layers_pipeline_real_cli(tmp_path):
    """RESULTS §6.1 端到端：新建→5 步图层流水线→导出 PNG，退出码 0 且警告原样出现。"""
    out = tmp_path / "e2e_layers.png"
    argv = [
        PHOTOCRAFT_CLI, "run",
        "--new", json.dumps(LAYER_PIPELINE_NEW_DOC),
        "--cmd", "layer.new.layer", "--params", json.dumps({"name": "Accent"}),
        "--cmd", "edit.fill", "--params", json.dumps({"color": "#c0392b"}),
        "--cmd", "layer.new.layer", "--params", json.dumps({"name": "Veil", "opacity": 0.5}),
        "--cmd", "edit.fill", "--params", json.dumps({"color": "#f1c40f"}),
        "--cmd", "document.inspect", "--params", "{}",
        "--out", str(out),
    ]

    proc = subprocess.run(              # noqa: S603 - argv list, shell=False
        argv, cwd=str(tmp_path), capture_output=True, text=True, timeout=300,
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert out.is_file(), "产物 e2e_layers.png 必须生成"
    json_lines = [ln for ln in proc.stdout.splitlines() if ln.lstrip().startswith("{")]
    assert len(json_lines) == 5
    assert FLATTEN_WARNING_SUBSTR in proc.stderr
