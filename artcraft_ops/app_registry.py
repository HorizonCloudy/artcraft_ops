"""artcraft_ops —— 领域层：内置 adapter 表 + 运行时配置合并（§3 差异表）。

三个已实机核验的 app 零配置可跑（``BUILTIN_SPECS`` 兜底）；
新增一个 app 只需在运行时配置加一段 ``[artcraft.apps.<id>]``（约 3 行）；
只有 argv 形态全新才需要在 ``sequence.ARGV_BUILDERS`` 注册 builder。
"""
from __future__ import annotations

import dataclasses
import os
from typing import Any

from .errors import AppNotFound
from .spec import AppSpec, Transport

_ARTCRAFT_ROOT = "E:\\Program Files\\Artcraft"


def _spec(
    app_id: str,
    dirname: str,
    cli: str,
    version: str,
    **kw: Any,
) -> AppSpec:
    """按安装目录约定造一条 AppSpec，避免 17 条各写 25 行。

    ``cli_candidates`` / ``install_root`` 由 ``dirname`` + ``cli`` 推出；
    其余差异（argv 族、目录获取方式、保存 flag）全部显式传，不靠猜。

    路径一律走 :func:`os.path.join`——手拼反斜杠会被写成双正斜杠，
    Windows 的 ``isfile`` 照样返回 True，但 ``resolve_cli`` 的前缀比较会踩坑。
    """
    root = os.path.join(_ARTCRAFT_ROOT, dirname)
    return AppSpec(
        app_id=app_id,
        display_name=kw.pop("display_name", app_id),
        version=version,
        cli_candidates=(os.path.join(root, cli),),
        install_root=root,
        transport=Transport.CLI_RUN,
        **kw,
    )


# ---------------------------------------------------------------------------
# §19 全 17 app 契约表（2026-11-08 逐个实机摸 `--help` / `run --help` / `commands`）
#
# 6 个 argv 族，差异**不是配置能抹平的**，每族一个 builder：
#   run_cmd        --new JSON --cmd ID --params JSON --out F        PhotoCraft
#   run_cmd       --in F --cmd ID --params JSON --export F        VectorCraft
#   cmd_inline    --cmd 'ID={json}'  （CADCraft 是 '--cmd ID {json}'，空格分隔）
#   cmd_positional <input> <ID> <json>   /   <ID> <json> ...      位置参数族
#   script_file   run <script.json>                               脚本族
#   cmd_kv        run <tool> key=value ...                        PdfCraft
#   tool_subcmd   <tool> --json --project F                       Craft Anything
# ---------------------------------------------------------------------------
BUILTIN_SPECS: dict[str, AppSpec] = {
    # --- 族 A：具名 flag（--cmd ID --params JSON） ---------------------------
    "photocraft": _spec(
        "photocraft", "PhotoCraft", "photocraft-cli.exe", "0.21.0",
        display_name="PhotoCraft",
        input_mode="positional", input_flag="", new_flag="--new", output_flag="--out",
        instance_json=r"%APPDATA%\Photocraft\instance.json",
        builder="run_cmd", has_serve=True,
    ),
    "vectorcraft": _spec(
        "vectorcraft", "VectorCraft", "vectorcraft-cli.exe", "0.22.0",
        display_name="VectorCraft",
        input_mode="flag_in", input_flag="--in",
        # 实机：`vectorcraft-cli run` 没有 `--new`（unknown run option）。
        # 旧表写着 "--new" 但从未被触发过，属于潜伏错误——传 new_doc 必炸。
        new_flag="",
        output_flag="--export", output_repeatable=True,
        commands_flag="--commands", supports_filter=False, filter_mode="local",
        # 实测：VectorCraft 的 commands 输出无 disabledReason / needsDocument 字段，
        # 所以这两个开关必须是 False，否则解析器会去找不存在的键。
        has_disabled_reason=False, has_needs_document=False,
        has_serve=False, control_port=7979,
        builder="run_cmd",
    ),
    # --- 族 B：内联 cmd（--cmd 'ID={json}'） --------------------------------
    "cadcraft": _spec(
        "cadcraft", "CADCraft", "cadcraft-cli.exe", "0.18.0",
        display_name="CADCraft",
        input_mode="positional", input_flag="", new_flag="", output_flag="",
        save_flag="--export", cmd_join="inline_space",
        commands_mode="json_default", has_serve=False,
        builder="cmd_inline",
    ),
    "soundcraft": _spec(
        "soundcraft", "SoundCraft", "soundcraft-cli.exe", "0.18.0",
        display_name="SoundCraft",
        input_mode="flag_in", input_flag="--in", new_flag="", output_flag="",
        save_flag="--save", cmd_join="inline",
        commands_mode="plain", has_serve=False,
        builder="cmd_inline",
    ),
    "deckcraft": _spec(
        "deckcraft", "DeckCraft", "deckcraft-cli.exe", "0.16.0",
        display_name="DeckCraft",
        input_mode="flag_in", input_flag="--in", new_flag="", output_flag="",
        save_flag="--save", cmd_join="inline",
        commands_mode="plain", has_serve=False,
        builder="cmd_inline",
    ),
    "designcraft": _spec(
        "designcraft", "DesignCraft", "designcraft-cli.exe", "0.18.0",
        display_name="DesignCraft",
        input_mode="flag_in", input_flag="--in", new_flag="--sample", output_flag="",
        save_flag="--export", cmd_join="inline",
        # 实测：`commands` 默认吐 JSON；加 `--json` 反而返回 `[]`（flag 被当过滤器吞掉）。
        commands_mode="json_default", supports_filter=False,
        has_serve=False, builder="cmd_inline",
    ),
    "gridcraft": _spec(
        "gridcraft", "GridCraft", "gridcraft-cli.exe", "0.17.0",
        display_name="GridCraft",
        input_mode="flag_in", input_flag="--in", new_flag="--sample", output_flag="",
        save_flag="--out", cmd_join="inline",
        commands_mode="json_flag", has_serve=False,
        builder="cmd_inline",
    ),
    "wordcraft": _spec(
        "wordcraft", "WordCraft", "wordcraft-cli.exe", "0.16.0",
        display_name="WordCraft",
        input_mode="flag_in", input_flag="--file", new_flag="", output_flag="",
        save_flag="--save", cmd_join="inline",
        commands_mode="json_flag", has_serve=False,
        builder="cmd_inline",
    ),
    # --- 族 C：位置参数 ------------------------------------------------------
    "texturecraft": _spec(
        "texturecraft", "TextureCraft", "texturecraft-cli.exe", "0.4.0",
        display_name="TextureCraft",
        input_mode="positional", input_flag="", new_flag="", output_flag="",
        save_flag="-o", cmd_join="positional",
        commands_mode="plain", single_step=True, requires_input=True,
        has_serve=False, builder="cmd_positional",
    ),
    "materialcraft": _spec(
        "materialcraft", "MaterialCraft", "materialcraft-cli.exe", "0.2.0",
        display_name="MaterialCraft",
        input_mode="positional", input_flag="", new_flag="", output_flag="",
        save_flag="-o", cmd_join="positional",
        commands_mode="json_default", single_step=True, requires_input=True,
        has_serve=False, builder="cmd_positional",
    ),
    "effectcraft": _spec(
        "effectcraft", "EffectCraft", "effectcraft-cli.exe", "0.22.0",
        display_name="EffectCraft",
        input_mode="positional", input_flag="", new_flag="", output_flag="",
        save_flag="--save", cmd_join="positional",
        commands_mode="plain", has_serve=False,
        script_flag="--commands", script_format="array_cmd",
        builder="cmd_positional",
    ),
    "lightcraft": _spec(
        "lightcraft", "LightCraft", "lightcraft-cli.exe", "0.19.0",
        display_name="LightCraft",
        input_mode="flag_in", input_flag="--import", new_flag="", output_flag="",
        save_flag="", cmd_join="kv",
        commands_mode="json_flag", has_serve=False, single_step=True,
        # `--script` 要的是 **JSON Lines**（每行一个 {"command","params"}），
        # 不是缩进数组——给数组会被当成坏文件（实机踩到）。
        script_flag="--script", script_format="jsonl_cmd",
        # LightCraft 的 `run COMMAND k=v …` 无法表达嵌套 params，且 `{json}` 与
        # 「下一个 COMMAND 以无 = 的词开头」规则冲突 → 一律走 --script 通道。
        builder="script_file",
    ),
    # --- 族 D：脚本文件（多步必须落脚本） ------------------------------------
    "figcraft": _spec(
        "figcraft", "FigCraft", "figcraft-cli.exe", "0.16.0",
        display_name="FigCraft",
        input_mode="flag_in", input_flag="--in", new_flag="", output_flag="--out",
        cmd_join="inline", commands_mode="json_flag", single_step=True,
        script_subcmd="run", script_format="array_cmd",
        has_serve=False, builder="script_file",
    ),
    "filmcraft": _spec(
        "filmcraft", "FilmCraft", "filmcraft-cli.exe", "0.18.0",
        display_name="FilmCraft",
        input_mode="positional", input_flag="", new_flag="", output_flag="",
        save_flag="--save", cmd_join="inline",
        commands_mode="json_flag", single_step=True,
        script_subcmd="run", script_format="jsonl_id",
        has_serve=False, builder="script_file",
    ),
    "solidcraft": _spec(
        "solidcraft", "SolidCraft", "solidcraft-cli.exe", "0.30.1",
        display_name="SolidCraft",
        input_mode="flag_in", input_flag="--in", new_flag="", output_flag="--out",
        save_flag="--save", cmd_join="inline",
        commands_mode="json_flag", single_step=True,
        script_subcmd="run", script_format="array_cmd",
        has_serve=False, builder="script_file",
    ),
    # --- 族 E：工具式（tool + key=value） ------------------------------------
    "pdfcraft": _spec(
        "pdfcraft", "PdfCraft", "pdfcraft-cli.exe", "0.19.0",
        display_name="PdfCraft",
        input_mode="positional", input_flag="", new_flag="", output_flag="--out",
        cmd_join="kv", single_step=True,
        # PdfCraft 没有 `commands`，但 `tools` 就是它的目录（输出 JSON Schema）。
        commands_subcmd="tools", commands_mode="json_flag",
        script_flag="--script", script_format="array_tool",
        has_serve=False, builder="script_file",
    ),
    # --- 特殊：Craft Anything（无 run，tool 子命令） -------------------------
    "craft_anything": _spec(
        "craft_anything", "Craft Anything", "craft-anything-cli.exe", "0.5.0",
        display_name="Craft Anything",
        input_mode="flag_project", input_flag="--project", new_flag="", output_flag="",
        supports_filter=False, filter_mode="local", capabilities_gate=True,
        output_format_flag="--output-format json",
        # CA 的命令表是另一套字段（command/needs_target/summary/tool/toolset），
        # 必须走专用解析器；写回 photocraft 会让整张目录解析成空。
        commands_parser="craft_anything",
        has_disabled_reason=False, has_needs_document=False,
        commands_mode="json_flag", single_step=True,
        has_serve=False, builder="tool_subcmd",
    ),
}

# 对外可变别名（测试与诊断都读 APP_SPECS）；是独立 dict，防止 configure() 影响内置表
APP_SPECS: dict[str, AppSpec] = dict(BUILTIN_SPECS)

# 允许被运行时配置覆盖的字段白名单（防止配置里塞进未知键制造隐蔽行为）
_MERGEABLE_FIELDS: frozenset[str] = frozenset(f.name for f in dataclasses.fields(AppSpec))


def get_spec(app_id: str) -> AppSpec:
    """取 app 规格；未注册 → ``APP_NOT_FOUND``（不静默返回默认值）。"""
    key = str(app_id or "").strip()
    spec = APP_SPECS.get(key)
    if spec is None:
        raise AppNotFound("APP_NOT_FOUND", app=key or "unknown")
    return spec


def has_spec(app_id: str) -> bool:
    return str(app_id or "").strip() in APP_SPECS


def merge_specs(
    builtin: dict[str, AppSpec], overrides: dict[str, Any] | None,
) -> dict[str, AppSpec]:
    """内置 3 个必存在（兜底）→ 配置段按 ``app_id`` 覆盖字段 → 新 ``app_id`` 用默认值构造。"""
    merged: dict[str, AppSpec] = dict(builtin)
    if not isinstance(overrides, dict):
        return merged
    for app_id, node in overrides.items():
        if not isinstance(node, dict) or not str(app_id or "").strip():
            continue
        key = str(app_id).strip()
        fields = {
            name: value
            for name, value in node.items()
            if name in _MERGEABLE_FIELDS and not name.startswith("_")
        }
        base = merged.get(key)
        if base is None:
            payload: dict[str, Any] = {"app_id": key, "display_name": key, "version": "0.0.0"}
            payload.update(fields)
            try:
                merged[key] = AppSpec(**payload)
            except TypeError:
                continue
            continue
        try:
            merged[key] = dataclasses.replace(base, **fields)
        except TypeError:
            continue
    return merged


async def load_specs(plugin: Any) -> dict[str, AppSpec]:
    """读运行时配置并合并（配置读失败 → 只用内置表，不阻塞插件加载）。"""
    from . import config  # 局部导入：config 是叶子，仅此处需要

    try:
        settings = await config.load_settings(plugin)
        overrides = settings.apps
    except Exception:
        overrides = {}
    return merge_specs(BUILTIN_SPECS, overrides)


def configure(overrides: dict[str, Any] | None) -> dict[str, AppSpec]:
    """把运行时配置里新增/覆盖的 app 合并进 ``APP_SPECS``（同步、不 spawn）。

    startup 只做这一层字典合并，绝不触发 ``commands --json``（§1 lifecycle 收紧）。
    """
    merged = merge_specs(BUILTIN_SPECS, overrides)
    APP_SPECS.clear()
    APP_SPECS.update(merged)
    return APP_SPECS


__all__ = [
    "BUILTIN_SPECS", "APP_SPECS", "get_spec", "has_spec",
    "merge_specs", "load_specs", "configure",
]
