"""§10 #5 / #6 / #7 / #24 / #25 / #27 — 命名空间归一化与 argv 构造（P0 核心）。

覆盖：正常路径（doc.*↔document.*、两 app argv、inspect 追加）
      + 边界（幂等/非 doc 前缀、Craft Anything tool 子命令、inspect 关闭、shell 注入）
      + 错误输入（空命令、deny_prefixes、未命中注册表）。

[FACT] 依据 RESULTS §5.5（命名空间陷阱真实报错）、§2.1/§3（run 参数差异）、
ARCHITECTURE §3（APP_SPECS 表）、§5（防注入）。
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from _fixtures import (
    CRAFT_ANYTHING_CLI,
    DEFAULT_READ_FLAG,
    DEFAULT_WRITE_FLAG,
    PHOTOCRAFT_CLI,
    VECTORCRAFT_CLI,
)

READ_ROOT = r"C:\state\artcraft_ops\data"
WRITE_ROOT = r"C:\state\artcraft_ops\out"
OUT_FILE = "probe/e2e_layers.png"
NEW_DOC = {"width": 640, "height": 400, "background": "white"}


def _spec(impl, app_id: str, cli_path: str):
    """从真实 APP_SPECS 取 spec，并注入 cli_path（红/绿阶段都用实现自己的类型）。"""
    registry = impl("app_registry")
    return dataclasses.replace(registry.get_spec(app_id), cli_path=cli_path)


def _roots_policy(impl):
    spec = impl("spec")
    return spec.RootsPolicy(read_roots=(READ_ROOT,), write_roots=(WRITE_ROOT,))


def _step(impl, cmd: str, params: dict | None = None):
    return impl("spec").Step(cmd=cmd, params=params or {})


def _detail(exc: BaseException) -> str:
    return f"{exc} {type(exc).__name__}"


# ---------------------------------------------------------------------------
# #5 命名空间归一化（P0）
# ---------------------------------------------------------------------------
def test_normalize_namespace_doc_to_document(impl):
    """#5 P0 正常：引擎命令空间用 document.*，serve 用 doc.*。"""
    sequence = impl("sequence")
    spec = _spec(impl, "photocraft", PHOTOCRAFT_CLI)

    assert sequence.normalize_command_id("doc.inspect", spec, "engine") == "document.inspect"
    assert sequence.normalize_command_id("doc.save", spec, "engine") == "document.save"
    assert sequence.normalize_command_id("doc.open", spec, "engine") == "document.open"


def test_normalize_namespace_serve_direction(impl):
    """#5 正常：反向（target=serve）把 document.* 还原成 doc.*。"""
    sequence = impl("sequence")
    spec = _spec(impl, "photocraft", PHOTOCRAFT_CLI)

    assert sequence.normalize_command_id("document.inspect", spec, "serve") == "doc.inspect"
    assert sequence.normalize_command_id("document.save", spec, "serve") == "doc.save"


def test_normalize_namespace_idempotent_and_non_doc_unchanged(impl):
    """#5 边界：已是目标的幂等；非 doc/document 前缀（session.* 等）原样保留。

    [INFERENCE] 规格 §10 #5 提到「session.* 同理」，但 §2/§3 未给出 session 命名
    空间映射表，故此处不臆测映射，只断言「非 doc 前缀不被改写」这一确定性行为。
    """
    sequence = impl("sequence")
    spec = _spec(impl, "photocraft", PHOTOCRAFT_CLI)

    assert sequence.normalize_command_id("document.inspect", spec, "engine") == "document.inspect"
    assert sequence.normalize_command_id("doc.inspect", spec, "serve") == "doc.inspect"
    assert sequence.normalize_command_id("session.list", spec, "engine") == "session.list"
    assert sequence.normalize_command_id("edit.fill", spec, "engine") == "edit.fill"
    assert sequence.normalize_command_id("filter.sharpen.unsharpMask", spec, "engine") == \
        "filter.sharpen.unsharpMask"


# ---------------------------------------------------------------------------
# #6 / #7 argv 构造（P0）
# ---------------------------------------------------------------------------
def test_build_argv_photocraft(impl):
    """#6 P0 正常：PhotoCraft 用位置参数 / --new / --out / --cmd+--params，**不带 root flag**。

    契约（云雨照此实现）：argv 顺序为
      <cli> run --new <JSON> (--cmd <ID> --params <JSON>)* --out <FILE>
    真机实测：``run`` 子命令不接受 ``--automation-read-root`` / ``--automation-write-root``
    （exit=2, ``error: unexpected argument '--automation-read-root' found``），
    这两个 flag 只存在于 ``serve`` / ``mcp``。v1 主通道 CLI_RUN 不带 root flag，
    roots 由插件侧 ``roots.resolve_and_check()`` 强制。
    JSON 一律 ``json.dumps`` 序列化成**单个** argv 元素。
    """
    sequence = impl("sequence")
    spec = impl("spec")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)
    steps = [
        _step(impl, "edit.fill", {"color": "#c0392b"}),
        _step(impl, "document.inspect", {}),
    ]
    source = spec.Source(new_doc=NEW_DOC)

    argv = sequence.build_argv(spec_pc, steps, source, OUT_FILE, _roots_policy(impl))

    expected = [
        PHOTOCRAFT_CLI, "run",
        "--new", json.dumps(NEW_DOC),
        "--cmd", "edit.fill", "--params", json.dumps({"color": "#c0392b"}),
        "--cmd", "document.inspect", "--params", "{}",
        "--out", OUT_FILE,
    ]
    assert argv == expected
    assert DEFAULT_READ_FLAG not in argv and DEFAULT_WRITE_FLAG not in argv, \
        "run 子命令不接受 automation root 参数（实测 exit 2），roots 由插件侧 resolve_and_check 强制"
    # 防注入补充：kwargs 里不得出现 shell 键
    assert "shell" not in impl("runner").build_subprocess_kwargs()


def test_build_argv_params_are_single_json_elements(impl):
    """#6 边界：每个 --params 值是单个可解析的 JSON 元素，值精确等于原 params。"""
    sequence = impl("sequence")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)
    spec_mod = impl("spec")
    steps = [
        _step(impl, "edit.fill", {"color": "#c0392b"}),
        _step(impl, "layer.new.layer", {"name": "Veil", "opacity": 0.5}),
        _step(impl, "document.inspect", {}),
    ]
    argv = sequence.build_argv(
        spec_pc, steps, spec_mod.Source(new_doc=NEW_DOC), OUT_FILE, _roots_policy(impl),
    )
    param_values = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--params"]
    assert len(param_values) == 3
    assert json.loads(param_values[0]) == {"color": "#c0392b"}
    assert json.loads(param_values[1]) == {"name": "Veil", "opacity": 0.5}
    assert json.loads(param_values[2]) == {}
    assert all(isinstance(v, str) for v in param_values)


def test_build_argv_vectorcraft(impl):
    """#7 P0 正常：VectorCraft 用 --in / --export（不是 --out），且暴露 --commands。

    [INFERENCE] §3 的 ``commands_flag="--commands"`` 未规定它在 run argv 中的位置，
    故这里只断言该字段「可用」（等于 --commands），不臆测 argv 位置；
    真正精确的是输入/输出参数名差异（--in vs 位置参数、--export vs --out）。
    """
    sequence = impl("sequence")
    spec_mod = impl("spec")
    spec_vc = _spec(impl, "vectorcraft", VECTORCRAFT_CLI)
    assert spec_vc.commands_flag == "--commands"

    in_file = READ_ROOT + r"\in.svg"
    steps = [_step(impl, "file.new", {}), _step(impl, "document.inspect", {})]
    argv = sequence.build_argv(
        spec_vc, steps, spec_mod.Source(input_file=in_file), "out.svg",
        _roots_policy(impl),
    )

    assert argv[0] == VECTORCRAFT_CLI
    assert argv[1] == "run"
    assert "--in" in argv and in_file in argv
    assert "--export" in argv and "out.svg" in argv
    assert "--out" not in argv
    assert "--cmd" in argv
    assert DEFAULT_READ_FLAG not in argv and DEFAULT_WRITE_FLAG not in argv, \
        "vectorcraft run 同样不接受 root 参数；root 只在 mcp 上有"


def test_build_argv_craft_anything_tool_subcmd(impl):
    """#25 边界：Craft Anything 用 `<cli> <tool> --json --project <file>`，不含 --cmd。"""
    sequence = impl("sequence")
    spec_mod = impl("spec")
    spec_ca = _spec(impl, "craft_anything", CRAFT_ANYTHING_CLI)
    project = READ_ROOT + r"\proj.cany"
    steps = [_step(impl, "triage", {})]

    argv = sequence.build_argv(
        spec_ca, steps, spec_mod.Source(input_file=project), "", _roots_policy(impl),
    )

    assert argv[0] == CRAFT_ANYTHING_CLI
    assert "triage" in argv
    assert "--json" in argv
    assert "--project" in argv and project in argv
    assert "--cmd" not in argv
    assert "--out" not in argv
    assert "--new" not in argv


# ---------------------------------------------------------------------------
# #24 / #27 防注入与命名空间陷阱（P0）
# ---------------------------------------------------------------------------
def test_no_shell_injection(impl):
    """#24 P0 边界：文件名 `a.png; del *` 只作为单个 argv 元素，且 kwargs 无 shell 键。"""
    sequence = impl("sequence")
    spec_mod = impl("spec")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)
    evil = "a.png; del *"
    steps = [_step(impl, "edit.fill", {"color": "#fff"})]

    argv = sequence.build_argv(
        spec_pc, steps, spec_mod.Source(input_file=evil), "out.png", _roots_policy(impl),
    )

    assert evil in argv                                  # 完整文件名是单个元素
    assert sum(1 for a in argv if ";" in a) == 1          # 没有任何其它元素含分号
    assert "del" not in argv                              # 未被拆成独立 token
    assert "*" not in argv
    kwargs = impl("runner").build_subprocess_kwargs()
    assert "shell" not in kwargs
    assert kwargs.get("shell") is not True


def test_namespace_trap_normalized_before_spawn(impl):
    """#27 P0：调用方直接给 doc.inspect -> 归一化 -> spawn argv 里已是 document.inspect。"""
    sequence = impl("sequence")
    spec_mod = impl("spec")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)

    raw = [_step(impl, "doc.inspect", {})]
    normalized_ids = [sequence.normalize_command_id(s.cmd, spec_pc, "engine") for s in raw]
    assert normalized_ids == ["document.inspect"]

    normalized = [spec_mod.Step(cmd=c, params={}) for c in normalized_ids]
    argv = sequence.build_argv(
        spec_pc, normalized, spec_mod.Source(new_doc=NEW_DOC), OUT_FILE,
        _roots_policy(impl),
    )
    assert "document.inspect" in argv
    assert "doc.inspect" not in argv


# ---------------------------------------------------------------------------
# 正常补充：plan_inspect / is_readonly
# ---------------------------------------------------------------------------
def test_plan_inspect_appends_document_inspect(impl):
    """正常：inspect=true 在序列末尾追加 document.inspect（不是 doc.inspect）。"""
    sequence = impl("sequence")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)
    steps = [_step(impl, "edit.fill", {"color": "#c0392b"})]
    out = sequence.plan_inspect(steps, spec_pc, enabled=True)
    assert [s.cmd for s in out] == ["edit.fill", "document.inspect"]


def test_plan_inspect_disabled_and_no_duplicate(impl):
    """边界：inspect=false 不追加；末尾已有 inspect 时不重复追加。"""
    sequence = impl("sequence")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)
    steps = [_step(impl, "edit.fill", {"color": "#c0392b"})]
    assert [s.cmd for s in sequence.plan_inspect(steps, spec_pc, False)] == ["edit.fill"]

    already = [
        _step(impl, "edit.fill", {"color": "#c0392b"}),
        _step(impl, "document.inspect", {}),
    ]
    out = sequence.plan_inspect(already, spec_pc, True)
    assert [s.cmd for s in out].count("document.inspect") == 1


def test_is_readonly_prefixes(impl):
    """正常：§2 readonly_prefixes 判定（供 jobs 编辑类鉴别）。"""
    sequence = impl("sequence")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)
    assert sequence.is_readonly("document.inspect", spec_pc) is True
    assert sequence.is_readonly("session.list", spec_pc) is True
    assert sequence.is_readonly("jobs.list", spec_pc) is True
    assert sequence.is_readonly("command.list", spec_pc) is True
    assert sequence.is_readonly("edit.fill", spec_pc) is False
    assert sequence.is_readonly("layer.new.layer", spec_pc) is False


# ---------------------------------------------------------------------------
# 错误输入：命令策略
# ---------------------------------------------------------------------------
def test_check_command_allowed_rejects_deny_prefixes(impl):
    """错误：prefs./plugin./updates. 前缀即便命中注册表也必须拒绝。"""
    sequence = impl("sequence")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)
    policy = impl("spec").CommandPolicy()
    catalog = frozenset({"prefs.setTheme", "plugin.install", "updates.check", "edit.fill"})
    for cmd in ("prefs.setTheme", "plugin.install", "updates.check"):
        with pytest.raises(Exception) as ei:
            sequence.check_command_allowed(cmd, spec_pc, policy, catalog)
        assert ("POLICY_VIOLATION" in _detail(ei.value)
                or "PolicyViolation" in _detail(ei.value)), _detail(ei.value)


def test_check_command_allowed_requires_registry_match(impl):
    """错误/#29 纯逻辑层：命令不在注册表 -> POLICY_VIOLATION。"""
    sequence = impl("sequence")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)
    policy = impl("spec").CommandPolicy(require_registry_match=True)
    catalog = frozenset({"edit.fill", "document.inspect", "layer.new.layer"})
    with pytest.raises(Exception) as ei:
        sequence.check_command_allowed("evil.inject", spec_pc, policy, catalog)
    assert ("POLICY_VIOLATION" in _detail(ei.value)
            or "registry" in _detail(ei.value).lower()
            or "PolicyViolation" in _detail(ei.value)), _detail(ei.value)


def test_check_command_allowed_allows_registered_command(impl):
    """正常：注册表中且不在黑名单的命令通过（返回 None，不抛异常）。"""
    sequence = impl("sequence")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)
    policy = impl("spec").CommandPolicy()
    catalog = frozenset({"edit.fill", "document.inspect", "file.new"})
    assert sequence.check_command_allowed("edit.fill", spec_pc, policy, catalog) is None
    assert sequence.check_command_allowed("file.new", spec_pc, policy, catalog) is None


def test_check_command_allowed_empty_cmd_rejected(impl):
    """错误输入：空命令 id 必须被拒，不得拼进 argv。"""
    sequence = impl("sequence")
    spec_pc = _spec(impl, "photocraft", PHOTOCRAFT_CLI)
    policy = impl("spec").CommandPolicy()
    with pytest.raises(Exception) as ei:
        sequence.check_command_allowed("", spec_pc, policy, frozenset({"edit.fill"}))
    assert "POLICY_VIOLATION" in _detail(ei.value) or "PolicyViolation" in _detail(ei.value), \
        _detail(ei.value)
