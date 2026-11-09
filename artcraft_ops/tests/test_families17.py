"""§19 全 17 app 覆盖：6 个 argv 族 + 4 种目录形态的回归测试。

这些用例全部对准「测试绿但线上炸」的那类坑——每条都对应一个实机踩到的真实缺陷：

  * locator 用硬编码名单把门，注册表扩到 17 个时 14 个 app 全 APP_NOT_FOUND
  * TextureCraft/MaterialCraft 无输入时把命令 id 拼进 `<input>` 槽
  * LightCraft 的 `--script` 只吃 JSON Lines，不吃缩进数组
  * VectorCraft 根本没有 `--new`（旧表写着，传 new_doc 必炸）
  * DesignCraft 的 `commands --json` 返回 `[]`，不带 flag 才吐 JSON
"""
from __future__ import annotations

import dataclasses
import json
import os

import pytest


def _spec(impl, app_id, **over):
    reg = impl("app_registry")
    spec = reg.get_spec(app_id)
    if over:
        spec = dataclasses.replace(spec, **over)
    return spec


# ---------------------------------------------------------------------------
# 族 B：--cmd 'ID={json}'
# ---------------------------------------------------------------------------
def test_cmd_inline_uses_equals_separator(impl):
    """SoundCraft / DeckCraft 走 `--cmd 'ID=JSON'`（一个 argv 元素）。"""
    seq = impl("sequence")
    spec = impl("spec")
    sp = _spec(impl, "soundcraft", cli_path="C:/x/soundcraft-cli.exe")
    argv = seq.build_argv(
        sp,
        [spec.Step(cmd="session.new", params={"name": "T"})],
        spec.Source(), "",
    )
    assert argv == [
        "C:/x/soundcraft-cli.exe", "run",
        "--cmd", 'session.new={"name": "T"}',
    ]


def test_cmd_inline_cadcraft_uses_space_separator(impl):
    """CADCraft 的 `--cmd` 取值是「id + 空格 + JSON」，不是 `=`。"""
    seq = impl("sequence")
    spec = impl("spec")
    sp = _spec(impl, "cadcraft", cli_path="C:/x/cadcraft-cli.exe")
    argv = seq.build_argv(
        sp, [spec.Step(cmd="zoom.set", params={"factor": 2})], spec.Source(), "out.png",
    )
    assert argv == [
        "C:/x/cadcraft-cli.exe", "run",
        "--cmd", 'zoom.set {"factor": 2}',
        "--export", "out.png",
    ]


def test_cmd_inline_multi_step_keeps_order(impl):
    """多步必须按顺序重复 --cmd，不能合并成一个。"""
    seq = impl("sequence")
    spec = impl("spec")
    sp = _spec(impl, "gridcraft", cli_path="C:/x/gridcraft-cli.exe")
    argv = seq.build_argv(
        sp,
        [spec.Step(cmd="a.one", params={}), spec.Step(cmd="b.two", params={"k": 1})],
        spec.Source(), "",
    )
    assert argv.count("--cmd") == 2
    assert argv[argv.index("--cmd") + 1] == "a.one={}"
    assert argv[-1] == 'b.two={"k": 1}'


# ---------------------------------------------------------------------------
# 族 C：位置参数 + requires_input 的 fail-closed
# ---------------------------------------------------------------------------
def test_cmd_positional_effectcraft_pairs(impl):
    """EffectCraft：`run <id> <json> <id> <json>`，没有输入文件。"""
    seq = impl("sequence")
    spec = impl("spec")
    sp = _spec(impl, "effectcraft", cli_path="C:/x/effectcraft-cli.exe")
    argv = seq.build_argv(
        sp,
        [spec.Step(cmd="comp.add", params={"kind": "solid"}), spec.Step(cmd="frame.render", params={})],
        spec.Source(), "",
    )
    assert argv[:3] == ["C:/x/effectcraft-cli.exe", "run", "comp.add"]
    assert '{"kind": "solid"}' in argv
    assert "frame.render" in argv


def test_cmd_positional_requires_input_fails_closed(impl):
    """TextureCraft 没有输入时必须**当场拒绝**，不能把命令拼进 <input> 槽。

    实机证据：错 argv 会让 CLI 报 `run: error: missing <command-id>`——
    用户看到的是一句天书，而不是「你忘了给输入文件」。
    """
    seq = impl("sequence")
    spec = impl("spec")
    sp = _spec(impl, "texturecraft", cli_path="C:/x/texturecraft-cli.exe")
    with pytest.raises(Exception) as ei:
        seq.build_argv(sp, [spec.Step(cmd="edit.undo", params={})], spec.Source(), "")
    msg = str(ei.value)
    assert "texturecraft" in msg
    assert "input_file" in msg


def test_cmd_positional_single_step_rejects_multi(impl):
    """TextureCraft 一次只吃一条命令，多步要报错而不是静默丢步。"""
    seq = impl("sequence")
    spec = impl("spec")
    sp = _spec(impl, "texturecraft", cli_path="C:/x/tc.exe")
    src = spec.Source(input_file="C:/x/a.tcraft")
    with pytest.raises(Exception) as ei:
        seq.build_argv(
            sp,
            [spec.Step(cmd="a", params={}), spec.Step(cmd="b", params={})],
            src, "",
        )
    assert "只接受一条命令" in str(ei.value)


# ---------------------------------------------------------------------------
# 族 D：脚本序列化（5 种格式）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "app_id, fmt, expect_key, is_jsonl",
    [
        ("figcraft", "array_cmd", "command", False),
        ("filmcraft", "jsonl_id", "id", True),
        ("lightcraft", "jsonl_cmd", "command", True),
        ("pdfcraft", "array_tool", "tool", False),
    ],
)
def test_serialize_script_formats(impl, app_id, fmt, expect_key, is_jsonl):
    """每种格式的键名与是否 JSONL 必须与该 app 的 `--help` 原文一致。"""
    seq = impl("sequence")
    spec = impl("spec")
    sp = _spec(impl, app_id)
    assert sp.script_format == fmt
    text = seq.serialize_script(
        sp,
        [spec.Step(cmd="x.y", params={"n": 1}), spec.Step(cmd="z.w", params={})],
        spec.Source(), "",
    )
    if is_jsonl:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert len(lines) == 2, f"{app_id} 应是每行一个对象"
        obj = json.loads(lines[0])
        assert set(obj) == {expect_key, "params"}
    else:
        arr = json.loads(text)
        assert isinstance(arr, list) and len(arr) == 2
        assert expect_key in arr[0]
    assert "x.y" in text


def test_serialize_script_batch_dict_not_used_by_default(impl):
    """batch_dict 是 TextureCraft 的备用格式；当前没有 app 用它，但必须能序列化。"""
    seq = impl("sequence")
    spec = impl("spec")
    sp = _spec(impl, "texturecraft", script_format="batch_dict")
    text = seq.serialize_script(
        sp, [spec.Step(cmd="edit.undo", params={})],
        spec.Source(input_file="in.tcraft"), "out.tcraft",
    )
    body = json.loads(text)
    assert body["open"] == "in.tcraft"
    assert body["save"] == "out.tcraft"
    assert body["steps"][0]["id"] == "edit.undo"


def test_script_file_writes_file_and_builds_argv(impl, tmp_path):
    """脚本族必须真的把脚本写出来，argv 只引用路径。"""
    seq = impl("sequence")
    spec = impl("spec")
    sp = _spec(impl, "figcraft", cli_path="C:/x/figcraft-cli.exe")
    script = str(tmp_path / "s.json")
    argv = seq.build_argv(
        sp, [spec.Step(cmd="page.add", params={})], spec.Source(), "out.fig",
        script_path=script,
    )
    assert os.path.isfile(script)
    assert argv[:3] == ["C:/x/figcraft-cli.exe", "run", script]
    assert argv[-2:] == ["--out", "out.fig"]
    assert json.loads(open(script, encoding="utf-8").read())[0]["command"] == "page.add"


def test_script_file_without_path_fails_closed(impl):
    """没给 script_path 就报错——不能产出引用不存在文件的 argv。"""
    seq = impl("sequence")
    spec = impl("spec")
    sp = _spec(impl, "solidcraft", cli_path="C:/x/solidcraft-cli.exe")
    with pytest.raises(Exception) as ei:
        seq.build_argv(sp, [spec.Step(cmd="part.extrude", params={})], spec.Source(), "")
    assert "script_path" in str(ei.value)


# ---------------------------------------------------------------------------
# 目录发现：4 种形态
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "app_id, mode, expect_json_flag",
    [
        ("photocraft", "json_flag", True),
        ("cadcraft", "json_default", False),
        ("designcraft", "json_default", False),
        ("deckcraft", "plain", False),
        ("texturecraft", "plain", False),
        ("pdfcraft", "json_flag", True),
    ],
)
def test_commands_argv_modes(impl, app_id, mode, expect_json_flag):
    """`--json` 只能出现在 json_flag 模式里。

    实机反例：DesignCraft 的 `commands --json` 返回 `[]`，不带 flag 才吐真表。
    """
    cc = impl("command_catalog")
    sp = _spec(impl, app_id)
    assert sp.commands_mode == mode
    argv = cc._commands_argv(app_id, sp, "C:/x/cli.exe")
    assert argv[0] == "C:/x/cli.exe"
    assert argv[1] == sp.commands_subcmd
    assert ("--json" in argv) is expect_json_flag


def test_pdfcraft_commands_subcmd_is_tools(impl):
    """PdfCraft 没有 `commands`，目录子命令叫 `tools`。"""
    reg = impl("app_registry")
    cc = impl("command_catalog")
    sp = reg.get_spec("pdfcraft")
    assert sp.commands_subcmd == "tools"
    assert cc._commands_argv("pdfcraft", sp, "C:/x/cli.exe") == [
        "C:/x/cli.exe", "tools", "--json",
    ]


def test_parse_commands_plain_both_layouts(impl):
    """纯文本目录有两种版式：破折号分隔 与 多空格分栏。"""
    cc = impl("command_catalog")
    raw = (
        "file.new — New Presentation: {theme?: name, size?: [w,h] pt}\n"
        "slide.add — Add Slide: {layout?: name}\n"
        "\n"
        "# 注释行要跳过\n"
    )
    items = cc.parse_commands_plain(raw)
    ids = [it.id for it in items]
    assert ids == ["file.new", "slide.add"]
    assert items[0].label == "New Presentation"
    assert "theme?" in items[0].params_schema

    raw2 = (
        "edit.undo                    Undo                                 {steps?: 1}\n"
        "edit.redo                    Redo                                 {steps?: 1}\n"
    )
    items2 = cc.parse_commands_plain(raw2)
    assert [it.id for it in items2] == ["edit.undo", "edit.redo"]
    assert items2[0].label == "Undo"
    assert "{steps?: 1}" in items2[0].params_schema


# ---------------------------------------------------------------------------
# locator：单一真相源
# ---------------------------------------------------------------------------
def test_locator_derives_all_17_from_registry(impl):
    """locator 不能再靠硬编码名单——17 个 app 全都要有候选。

    实机缺陷：`_CLI_NAMES` 只写了 3 个 app，注册表扩到 17 个后
    14 个 app 全部报 `APP_NOT_FOUND: 未找到 xxx 的命令行程序`。
    """
    reg = impl("app_registry")
    loc = impl("locator")
    missing = [a for a in reg.APP_SPECS if not loc._iter_candidates(a)]
    assert missing == []
    # 候选首位必须是注册表里声明的实装路径
    for app_id, sp in reg.APP_SPECS.items():
        assert loc._iter_candidates(app_id)[0] == sp.cli_candidates[0]


def test_locator_resolves_every_installed_app(impl):
    """本机 17 个 app 全部能解析出真实存在的 CLI（实机断言，缺装就 FAIL 提醒）。"""
    reg = impl("app_registry")
    loc = impl("locator")
    missing = []
    for app_id in reg.APP_SPECS:
        try:
            path = loc.resolve_cli(app_id, {})
            if not os.path.isfile(path):
                missing.append((app_id, "not a file"))
        except Exception as exc:
            missing.append((app_id, str(exc)[:60]))
    assert missing == [], f"这些 app 解析不到 CLI：{missing}"


def test_vectorcraft_has_no_new_flag(impl):
    """VectorCraft 的 run 没有 `--new`（实机 unknown run option）。"""
    reg = impl("app_registry")
    assert reg.get_spec("vectorcraft").new_flag == ""


def test_schema_app_enum_covers_every_registered_app(load_plugin_init):
    """LLM 侧 schema 的 ``app.enum`` 必须与注册表逐字一致。

    [真缺陷] 注册表从 3 个扩到 17 个时，``SEARCH_SCHEMA`` / ``RUN_SCHEMA`` 的
    ``enum`` 还停在 ``["photocraft","vectorcraft","craft_anything"]``——模型看到
    的候选只有 3 个，另外 14 个 app 等于不可达。这条断言把它钉死。
    """
    mod = load_plugin_init()
    expected = set(mod.app_registry.APP_SPECS)
    assert len(expected) == 17
    for name in ("SEARCH_SCHEMA", "RUN_SCHEMA"):
        enum = getattr(mod, name)["properties"]["app"]["enum"]
        assert set(enum) == expected, (
            f"{name} 的 app.enum 与注册表不一致："
            f"缺 {sorted(expected - set(enum))}，多 {sorted(set(enum) - expected)}"
        )
