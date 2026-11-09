"""§10 #2 / §3 per-app adapter 差异表。

覆盖：正常路径（差异表精确值）+ 边界（所有 AppSpec 公共字段）+ 错误输入（未知 app）。
"""
from __future__ import annotations

import pytest

from _fixtures import (
    CRAFT_ANYTHING_CLI,
    DEFAULT_READONLY_PREFIXES,
    FLATTEN_WARNING_SUBSTR,
    PHOTOCRAFT_CLI,
    VECTORCRAFT_CLI,
)


def test_app_registry_diff_table(impl):
    """正常路径：三 app 的参数名差异必须与 ARCHITECTURE §3 表逐项一致。"""
    registry = impl("app_registry")

    # §19：从 3 个扩到本机实装的全部 17 个。名单是实机 ls 出来的，
    # 不是猜的；任何增删都必须同步这条断言，防止「悄悄少一个」。
    assert set(registry.APP_SPECS) == {
        "photocraft", "vectorcraft", "craft_anything",
        "cadcraft", "soundcraft", "deckcraft", "designcraft", "gridcraft", "wordcraft",
        "texturecraft", "materialcraft", "effectcraft", "lightcraft",
        "figcraft", "filmcraft", "solidcraft", "pdfcraft",
    }
    assert len(registry.APP_SPECS) == 17

    pc = registry.get_spec("photocraft")
    assert pc.app_id == "photocraft"
    assert pc.display_name == "PhotoCraft"
    assert pc.version == "0.21.0"
    assert pc.input_mode == "positional"
    assert pc.input_flag == ""
    assert pc.new_flag == "--new"
    assert pc.output_flag == "--out"
    assert pc.output_repeatable is False
    assert pc.has_disabled_reason is True
    assert pc.has_needs_document is True
    assert pc.has_serve is True
    assert pc.control_port == 0
    assert pc.builder == "run_cmd"
    assert "instance.json" in pc.instance_json

    vc = registry.get_spec("vectorcraft")
    assert vc.app_id == "vectorcraft"
    assert vc.version == "0.22.0"
    assert vc.input_mode == "flag_in"
    assert vc.input_flag == "--in"
    assert vc.output_flag == "--export"
    assert vc.output_repeatable is True
    assert vc.commands_flag == "--commands"
    assert vc.has_disabled_reason is False
    assert vc.has_needs_document is False
    assert vc.has_serve is False
    assert vc.control_port == 7979
    assert vc.builder == "run_cmd"

    ca = registry.get_spec("craft_anything")
    assert ca.app_id == "craft_anything"
    assert ca.version == "0.5.0"
    assert ca.input_mode == "flag_project"
    assert ca.input_flag == "--project"
    assert ca.new_flag == ""
    assert ca.output_flag == ""
    assert ca.has_serve is False
    assert ca.builder == "tool_subcmd"


def test_app_registry_versions_match_probe(impl):
    """边界：版本 token 必须是 RESULTS §1 实测值（缓存 key 依赖它）。"""
    registry = impl("app_registry")
    assert registry.get_spec("photocraft").version == "0.21.0"
    assert registry.get_spec("vectorcraft").version == "0.22.0"
    assert registry.get_spec("craft_anything").version == "0.5.0"


def test_app_registry_common_defaults(impl):
    """边界：所有 AppSpec 共享 §2 的命名空间/白名单/警告默认值。"""
    registry = impl("app_registry")
    for app_id, _cli in (
        ("photocraft", PHOTOCRAFT_CLI),
        ("vectorcraft", VECTORCRAFT_CLI),
        ("craft_anything", CRAFT_ANYTHING_CLI),
    ):
        s = registry.get_spec(app_id)
        assert s.serve_namespace == "doc"
        assert s.engine_namespace == "document"
        assert tuple(s.readonly_prefixes) == DEFAULT_READONLY_PREFIXES
        assert s.flatten_warning_substr == FLATTEN_WARNING_SUBSTR
        assert s.cmd_flag == "--cmd"
        assert s.params_flag == "--params"
        assert s.commands_subcmd == "commands"
        assert s.commands_json_flag == "--json"
        assert s.newline_json is True
        # cli_path 允许为空（locator 运行时注入），但字段必须存在
        assert isinstance(s.cli_path, str)


def test_get_spec_unknown_app_raises(impl):
    """错误输入：未知 app 必须抛 AppNotFound / APP_NOT_FOUND，不得静默返回。"""
    registry = impl("app_registry")
    with pytest.raises(Exception) as ei:
        registry.get_spec("no_such_app_9999")
    detail = str(ei.value) + " " + type(ei.value).__name__
    assert ("APP_NOT_FOUND" in detail) or ("AppNotFound" in detail), detail


def test_app_registry_families_are_consistent(impl):
    """§19：6 个 argv 族的结构不变量。

    比逐条字段断言更强——它保证「族名 ↔ builder ↔ 必要 flag」三者自洽，
    防止后来加 app 时把 builder 写错却静默产出错误 argv。
    """
    registry = impl("app_registry")
    seq = impl("sequence")

    by_builder: dict[str, list[str]] = {}
    for app_id, spec in registry.APP_SPECS.items():
        by_builder.setdefault(spec.builder, []).append(app_id)
        # 族名必须是已注册的 builder，否则运行期才炸
        assert spec.builder in seq.ARGV_BUILDERS, (app_id, spec.builder)

    assert sorted(by_builder) == [
        "cmd_inline", "cmd_positional", "run_cmd", "script_file", "tool_subcmd",
    ]
    assert len(by_builder["run_cmd"]) == 2          # photocraft / vectorcraft
    assert len(by_builder["cmd_inline"]) == 6       # cad/sound/deck/design/grid/word
    assert len(by_builder["cmd_positional"]) == 3   # texture/material/effect
    assert len(by_builder["script_file"]) == 5      # fig/film/solid/light/pdf
    assert by_builder["tool_subcmd"] == ["craft_anything"]

    for app_id, spec in registry.APP_SPECS.items():
        # 每个 app 必须能定位到 CLI，否则等于没接
        assert spec.cli_candidates, app_id
        assert spec.install_root, app_id
        # 只要带 script_format，就必须同时给出通道（子命令或 flag）。
        # 注意：有脚本通道 ≠ 只能走脚本——EffectCraft 既有位置参数对，
        # 也有 `--commands FILE`，两者都合法，所以这里不按 builder 一刀切。
        if spec.script_format:
            assert spec.script_flag or spec.script_subcmd, app_id
        if spec.builder == "script_file":
            assert spec.script_format, app_id
            assert spec.script_flag or spec.script_subcmd, app_id
        # 保存 flag：要么 save_flag 要么 output_flag，至少一个。
        # 例外只有两个，且都有实机依据：
        #   * craft_anything —— 无 --out/--new，tool 直接产出 + --project 落盘；
        #   * lightcraft     —— run 没有保存 flag，导出靠 `app.export path=…` 命令。
        if app_id not in ("craft_anything", "lightcraft"):
            assert spec.save_flag or spec.output_flag, app_id


def test_app_registry_versions_match_installed(impl):
    """§19：注册表里的版本号必须等于本机 CLI 自报版本（防止表与实物脱节）。"""
    registry = impl("app_registry")
    assert registry.get_spec("cadcraft").version == "0.18.0"
    assert registry.get_spec("pdfcraft").version == "0.19.0"
    assert registry.get_spec("texturecraft").version == "0.4.0"
    assert registry.get_spec("solidcraft").version == "0.30.1"
    assert registry.get_spec("materialcraft").version == "0.2.0"
