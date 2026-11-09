"""§10 #32 / #33 — 清单对账（ast + tomllib，**不 import 插件**）。

===========================================================================
§12 字段冲突常量区 —— 云雨 stub-SDK 核验结论回来后**只改这一块**
===========================================================================
主理人：9 处冲突见 ARCHITECTURE §12。下面每个常量对应一处，对账逻辑全部从
常量读，不要在测试体里硬编码字段名。拿到云雨的字段修正表后改常量即可。

  #1  [[plugin.entries]] 只有 name，SKILL.md 要 id 且 id 对应 @plugin_entry(id=...)
  #2  [plugin.sdk] min_version vs recommended/supported
  #3  @plugin_entry(name=..., llm_result_fields=...) vs @plugin_entry(id=..., name=..., timeout=...)
  #4  llm_result_fields 写在 toml 还是装饰器参数
  #5  @plugin_entry 与 @llm_tool 叠放顺序
  #6  [plugin.store] / [plugin_runtime] 下业务子段是否合法
  #7  @lifecycle("startup") 位置参数 vs @lifecycle(id="startup")
  #8  type = "plugin" 是否需要/值是否不同
  #9  self.data_path("jobs.json") 签名
===========================================================================
"""
from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from _fixtures import EXPECTED_ENTRIES

ROOT = Path(__file__).resolve().parents[1]

# --- §12 #1 / #3：入口标识字段 -------------------------------------------------
ENTRY_ID_FIELD = "id"
ENTRY_NAME_FIELD = "name"
PLUGIN_ID_FIELD = "id"
PLUGIN_TYPE_FIELD = "type"

# --- §12 #2：SDK 版本区间字段 -------------------------------------------------
SDK_TABLE_KEY = "sdk"
SDK_VERSION_FIELDS = ("min_version", "recommended", "supported")

# --- §12 #4：llm_result_fields 的位置 ----------------------------------------
LLM_RESULT_FIELDS_KEY = "llm_result_fields"
LLM_RESULT_FIELDS_LOCATION = "toml"          # 可改为 "decorator"

# --- §12 #5：装饰器叠放顺序（[INFERENCE] 未核验） -----------------------------
PLUGIN_ENTRY_DECORATOR = "plugin_entry"
LLM_TOOL_DECORATOR = "llm_tool"
LIFECYCLE_DECORATOR = "lifecycle"
NEKO_PLUGIN_DECORATOR = "neko_plugin"
# 语义已按 §17 实测从「顺序」放宽为「集合」：两个装饰器都必须在场，顺序无关
# （spec/ARCHITECTURE-FINAL.md:1133 / :1300）。名字保留以免动 test_* 的引用。
EXPECTED_DECORATOR_ORDER = (PLUGIN_ENTRY_DECORATOR, LLM_TOOL_DECORATOR)
EXPECTED_DECORATOR_SET = frozenset(EXPECTED_DECORATOR_ORDER)

# --- §17.1 清单字段硬约束（schema.py / decorators.py / lifecycle_service.py） ---
PLUGIN_ENTRY_FIELD = "entry"
PLUGIN_AUTHOR_TABLE = "author"
EXPECTED_ENTRY_VALUE = "plugin.plugins.artcraft_ops:ArtcraftOpsPlugin"
SDK_ALLOWED_KEYS = frozenset({"recommended", "supported", "untested", "conflicts"})
SDK_FORBIDDEN_KEYS = frozenset({"min_version"})
RUNTIME_ALLOWED_KEYS = frozenset(
    {"enabled", "auto_start", "priority", "timeout", "startup_failure"}
)
RUNTIME_REQUIRED_KEY = "startup_failure"
LIFECYCLE_FORBIDDEN_KWARGS = frozenset({"startup_failure"})
DYNAMIC_LLM_TOOL_ID_PREFIX = "__llm_tool__"
PLUGIN_BASE_CLASS_NAME = "NekoPluginBase"

# --- §12 #6：清单表 -----------------------------------------------------------
MANIFEST_PLUGIN_KEY = "plugin"
MANIFEST_ENTRIES_KEY = "entries"
MANIFEST_RUNTIME_TABLE_KEY = "plugin_runtime"
PLUGIN_STORE_TABLE_KEY = "store"

# --- §12 #7：lifecycle 写法 ---------------------------------------------------
LIFECYCLE_ID_FIELD = "id"
LIFECYCLE_STARTUP_ID = "startup"
LIFECYCLE_SHUTDOWN_ID = "shutdown"

# --- §12 #8 / #9 --------------------------------------------------------------
PLUGIN_TYPE_VALUE = "plugin"
DATA_PATH_METHOD = "data_path"

PLUGIN_TOML_NAME = "plugin.toml"
CONFIG_EXAMPLE_NAME = "config.example.toml"


# ---------------------------------------------------------------------------
# 通用读取（红阶段文件未落盘 -> 明确报「实现缺失」，不报测试自己写错）
# ---------------------------------------------------------------------------
def _read_text(relname: str) -> str:
    p = ROOT / relname
    if not p.is_file():
        pytest.fail(f"实现缺失：artcraft_ops/{relname} 尚未落盘（应由实现方提供）",
                    pytrace=False)
    return p.read_text(encoding="utf-8")


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None


def _decorators(node):
    """返回 [(deco_name, kwargs_dict, positional_args)]，保留源码顺序。"""
    out = []
    for deco in node.decorator_list:
        if isinstance(deco, ast.Call):
            name = getattr(deco.func, "id", None) or getattr(deco.func, "attr", None)
            kwargs = {k.arg: _literal(k.value) for k in deco.keywords if k.arg}
            args = [_literal(a) for a in deco.args]
        elif isinstance(deco, ast.Name):
            name, kwargs, args = deco.id, {}, []
        elif isinstance(deco, ast.Attribute):
            name, kwargs, args = deco.attr, {}, []
        else:
            continue
        out.append((name, kwargs, args))
    return out


def _functions(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _entry_ident(kwargs: dict, fallback: str) -> str:
    for field in (ENTRY_ID_FIELD, ENTRY_NAME_FIELD):
        val = kwargs.get(field)
        if isinstance(val, str) and val:
            return val
    return fallback


def _manifest_entry_ident(entry: dict) -> str:
    for field in (ENTRY_ID_FIELD, ENTRY_NAME_FIELD):
        val = entry.get(field)
        if isinstance(val, str) and val:
            return val
    return ""


def _declared_entry_dicts() -> list[dict]:
    data = tomllib.loads(_read_text(PLUGIN_TOML_NAME))
    return list(data[MANIFEST_PLUGIN_KEY][MANIFEST_ENTRIES_KEY])


def _decorated_entry_ids() -> set[str]:
    tree = ast.parse(_read_text("__init__.py"))
    found: set[str] = set()
    for node in _functions(tree):
        for name, kwargs, _args in _decorators(node):
            if name == PLUGIN_ENTRY_DECORATOR:
                found.add(_entry_ident(kwargs, node.name))
    return found


def _literal_assign(tree, var_name: str):
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(t, ast.Name) and t.id == var_name for t in targets):
            return _literal(node.value)
    return None


def _class_methods(tree):
    """[(ClassDef, FunctionDef)]：只取**类体内的直接方法**（模块级函数不算）。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((node, item))
    return out


def _class_decorator_names(klass) -> set[str]:
    return {n for n, _kw, _a in _decorators(klass)}


def _class_base_names(klass) -> set[str]:
    names: set[str] = set()
    for base in klass.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


# ---------------------------------------------------------------------------
# #32 清单对账
# ---------------------------------------------------------------------------
def test_entries_match_decorators():
    """#32 P0：[[plugin.entries]] 集合 == @plugin_entry 装饰函数名集合（防 refresh 后 404）。"""
    entries = _declared_entry_dicts()
    declared = {_manifest_entry_ident(e) for e in entries}
    decorated = _decorated_entry_ids()

    assert decorated, "__init__.py 里没有找到任何 @plugin_entry 装饰的入口"
    assert declared == decorated, (
        f"清单与装饰器不一致：仅清单有 {sorted(declared - decorated)}，"
        f"仅装饰器有 {sorted(decorated - declared)}"
    )
    assert declared == set(EXPECTED_ENTRIES), "五个入口名必须精确匹配 §5/§9"


def test_manifest_id_equals_directory_name():
    """验收：plugin.toml 的 id 必须 == 目录名 artcraft_ops（宿主唯一加载源）。"""
    data = tomllib.loads(_read_text(PLUGIN_TOML_NAME))
    plugin = data[MANIFEST_PLUGIN_KEY]
    assert plugin.get(PLUGIN_ID_FIELD) == ROOT.name == "artcraft_ops"
    assert plugin.get(PLUGIN_TYPE_FIELD) == PLUGIN_TYPE_VALUE


def test_manifest_field_layout_follows_constants():
    """§12 #2/#4/#6/#8：按常量核对字段布局；云雨结论回来只改常量区。"""
    data = tomllib.loads(_read_text(PLUGIN_TOML_NAME))
    plugin = data[MANIFEST_PLUGIN_KEY]

    # #6 [plugin_runtime] 顶段必须存在；[plugin.store] 允许存在（合法性待核）
    assert MANIFEST_RUNTIME_TABLE_KEY in data
    # #2 [plugin.sdk] 至少命中一个候选版本字段
    if SDK_TABLE_KEY in plugin:
        sdk = plugin[SDK_TABLE_KEY]
        assert any(f in sdk for f in SDK_VERSION_FIELDS), \
            f"[plugin.sdk] 未命中任何候选字段 {SDK_VERSION_FIELDS}"
    # #4 llm_result_fields 按常量声明的位置核对
    entries = plugin[MANIFEST_ENTRIES_KEY]
    if LLM_RESULT_FIELDS_LOCATION == "toml":
        assert all(LLM_RESULT_FIELDS_KEY in e for e in entries)
    # #8
    assert plugin.get(PLUGIN_TYPE_FIELD) == PLUGIN_TYPE_VALUE


def test_entry_timeouts_present():
    """边界：§9 五个入口都带 timeout，且 search=15 / run=10。"""
    entries = {_manifest_entry_ident(e): e for e in _declared_entry_dicts()}
    assert all(isinstance(e.get("timeout"), (int, float)) and e["timeout"] > 0
               for e in entries.values())
    assert entries["artcraft_ops_search_commands"]["timeout"] == 15
    assert entries["artcraft_ops_run_sequence"]["timeout"] == 10


def test_all_entries_async_and_eat_kwargs():
    """验收清单：每个 entry / llm_tool 都是 async def 且签名吃 **_（宿主注入 _ctx）。"""
    tree = ast.parse(_read_text("__init__.py"))
    checked = 0
    for node in _functions(tree):
        names = [n for n, _kw, _a in _decorators(node)]
        if PLUGIN_ENTRY_DECORATOR in names or LLM_TOOL_DECORATOR in names:
            assert isinstance(node, ast.AsyncFunctionDef), f"{node.name} 必须是 async def"
            assert node.args.kwarg is not None, f"{node.name} 必须有 **_ 兜底"
            checked += 1
    assert checked >= len(EXPECTED_ENTRIES)


def test_decorator_stacking_order():
    """§12 #5：@plugin_entry 与 @llm_tool **双注册都要发生**（顺序无关，[INFERENCE] 已消解）。

    [FACT] spec/ARCHITECTURE-FINAL.md:1133 / :1300 真 SDK 实测：
    ``shared/core/decorators.py:248-252``、``llm_tool.py:225-256`` 两个装饰器都只
    ``setattr`` 后原样返回，两种叠放顺序均得到
    ``collect_entries() == {'run': 60.0}`` + ``collect_llm_tool_methods() ==
    [('run_tool', 60.0)]``，动态 id ``__llm_tool__run_tool`` 无冲突。
    故这里只断言集合相等，**不**断言顺序——顺序是巧合，双注册才是契约。
    """
    tree = ast.parse(_read_text("__init__.py"))
    checked = 0
    for node in _functions(tree):
        names = [n for n, _kw, _a in _decorators(node)]
        if PLUGIN_ENTRY_DECORATOR in names and LLM_TOOL_DECORATOR in names:
            observed = {n for n in names if n in EXPECTED_DECORATOR_SET}
            assert observed == EXPECTED_DECORATOR_SET, \
                f"{node.name} 缺装饰器：{sorted(EXPECTED_DECORATOR_SET - observed)}"
            checked += 1
    assert checked >= len(EXPECTED_ENTRIES)


def test_run_schema_max_items_256():
    """#19 schema 层：RUN_SCHEMA.steps.maxItems 精确等于 256（§5/§6）。"""
    tree = ast.parse(_read_text("__init__.py"))
    schema = _literal_assign(tree, "RUN_SCHEMA")
    assert isinstance(schema, dict), "RUN_SCHEMA 必须是模块级字面量 dict"
    assert schema["type"] == "object"
    assert schema["properties"]["steps"]["maxItems"] == 256
    assert schema["required"] == ["steps"]
    search = _literal_assign(tree, "SEARCH_SCHEMA")
    assert isinstance(search, dict)
    assert search["required"] == ["query"]
    assert search["properties"]["top_k"]["maximum"] == 30


def test_lifecycle_decorators_present():
    """§12 #7：startup / shutdown 两个 lifecycle 都注册（写法由常量裁决）。"""
    tree = ast.parse(_read_text("__init__.py"))
    ids: list[str] = []
    for node in _functions(tree):
        for name, kwargs, args in _decorators(node):
            if name != LIFECYCLE_DECORATOR:
                continue
            candidate = kwargs.get(LIFECYCLE_ID_FIELD)
            if not isinstance(candidate, str):
                candidate = next((a for a in args if isinstance(a, str)), None)
            if isinstance(candidate, str):
                ids.append(candidate)
    assert LIFECYCLE_STARTUP_ID in ids and LIFECYCLE_SHUTDOWN_ID in ids


def test_lifecycle_does_not_upload_images():
    """验收清单/§5：ctx.images.upload() 只能在结果阶段调，绝不在 lifecycle。"""
    text = _read_text("__init__.py")
    tree = ast.parse(text)
    for node in _functions(tree):
        if any(n == LIFECYCLE_DECORATOR for n, _kw, _a in _decorators(node)):
            segment = ast.get_source_segment(text, node) or ""
            assert "images.upload" not in segment, \
                f"{node.name} 在 lifecycle 里调了 images.upload"


# ---------------------------------------------------------------------------
# #33 config.example.toml
# ---------------------------------------------------------------------------
def test_config_example_has_no_plugin_section():
    """#33 P0：config.example.toml 不含 [plugin] 段，但允许 [plugin_runtime]。

    刻意**不做文本子串检查**（``"[plugin]" not in text``）：注释可以合法提及段名
    （本文件就有「宿主保留该段 [plugin]」这类说明），子串假红；反过来
    ``"[plugin_runtime]" in text`` 也能被一行注释蒙过，子串假绿风险高于收益。
    唯一可信的判据是 ``tomllib`` 解析后的顶层键集合。
    """
    data = tomllib.loads(_read_text(CONFIG_EXAMPLE_NAME))

    assert "plugin" not in data, "config.example.toml 不得包含 [plugin] 段"
    assert "plugin_runtime" in data, "允许并保留 [plugin_runtime] 段"


def test_config_example_security_and_limits():
    """边界：config.example.toml 的 roots/limits 与 §9 一致（256 步 / 127.0.0.1）。"""
    data = tomllib.loads(_read_text(CONFIG_EXAMPLE_NAME))
    security = data["artcraft"]["security"]
    limits = data["artcraft"]["limits"]

    assert security["loopback_host"] == "127.0.0.1"
    assert "localhost" not in security["loopback_host"]
    assert security["user_read_roots"] == []
    assert security["user_write_roots"] == []
    assert limits["max_steps"] == 256
    assert limits["max_reply_bytes"] == 8_388_608
    assert limits["max_request_line_bytes"] == 1_048_576


# ---------------------------------------------------------------------------
# §17.5 补强：清单字段硬约束 / 类方法硬约束 / llm_result_fields 覆盖风险
# ---------------------------------------------------------------------------
def test_plugin_toml_sdk_fields_17_1():
    """§17.1 清单硬约束：entry 必填 / author 必须是表 / sdk 键集 / runtime 键集 / lifecycle / 动态 id。"""
    data = tomllib.loads(_read_text(PLUGIN_TOML_NAME))
    plugin = data[MANIFEST_PLUGIN_KEY]

    # ① [plugin].entry 必填，module:Class 形式，且精确等于宿主加载路径
    #    （decorators.py:127-134 / registry.py 用它 import）
    entry = plugin.get(PLUGIN_ENTRY_FIELD)
    assert isinstance(entry, str) and entry, "[plugin].entry 必填（module:Class）"
    assert ":" in entry, f"entry 必须是 module:Class 形式，实际 {entry!r}"
    assert entry == EXPECTED_ENTRY_VALUE

    # ② [plugin.author] 必须是表（写成字符串会让 pydantic 模型直接崩）
    author = plugin.get(PLUGIN_AUTHOR_TABLE)
    assert isinstance(author, dict), \
        f"[plugin.author] 必须是表，实际类型 {type(author).__name__}"

    # ③ [plugin.sdk] 不得含 min_version，键必须 ⊆ {recommended, supported, untested, conflicts}
    #    （schema.py:69-74）
    sdk = plugin[SDK_TABLE_KEY]
    assert not (SDK_FORBIDDEN_KEYS & set(sdk)), \
        f"[plugin.sdk] 不得含 {sorted(SDK_FORBIDDEN_KEYS)}"
    assert set(sdk) <= SDK_ALLOWED_KEYS, \
        f"[plugin.sdk] 出现非法键 {sorted(set(sdk) - SDK_ALLOWED_KEYS)}"

    # ④ [plugin_runtime] 键 ⊆ 白名单，且 startup_failure 必须在其中
    #    （schema.py:77-81,264-282；它属于清单，不属于 @lifecycle 装饰器）
    runtime = data[MANIFEST_RUNTIME_TABLE_KEY]
    assert set(runtime) <= RUNTIME_ALLOWED_KEYS, \
        f"[plugin_runtime] 出现非法键 {sorted(set(runtime) - RUNTIME_ALLOWED_KEYS)}"
    assert RUNTIME_REQUIRED_KEY in runtime, \
        f"[plugin_runtime] 必须显式声明 {RUNTIME_REQUIRED_KEY}"

    # ⑤ @lifecycle 不得带 startup_failure 参数（lifecycle_service.py:912-920 会 TypeError）
    for node in _functions(ast.parse(_read_text("__init__.py"))):
        for name, kwargs, _args in _decorators(node):
            if name != LIFECYCLE_DECORATOR:
                continue
            bad = LIFECYCLE_FORBIDDEN_KWARGS & set(kwargs)
            assert not bad, \
                f"@{LIFECYCLE_DECORATOR}({node.name}) 不得带 {sorted(bad)}（该键属于清单）"

    # ⑥ [[plugin.entries]] 不得出现任何依赖动态注册的 __llm_tool__ id（宿主不接受）
    bad_ids = [ident for ident in map(_manifest_entry_ident, plugin[MANIFEST_ENTRIES_KEY])
               if ident.startswith(DYNAMIC_LLM_TOOL_ID_PREFIX)]
    assert not bad_ids, f"清单里不得出现动态注册 id：{bad_ids}"


def test_entry_handlers_are_class_methods():
    """§17 硬约束①：五个入口必须写在 @neko_plugin 类体内（模块级函数**不注册**）。

    [FACT] ``base.py:46-87``：宿主只扫描 ``NekoPluginBase`` 子类的类成员，
    模块级函数即使带了 ``@plugin_entry`` 也一个都不注册——单测全绿、真机面板 404。
    现有 ``test_all_entries_async_and_eat_kwargs`` 只查 ``async def`` + ``**_``，
    查不出这个洞，所以单独一条。
    """
    tree = ast.parse(_read_text("__init__.py"))

    # 反例先行：模块级（顶层）的 @plugin_entry 一律不算注册
    module_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for name, kwargs, _args in _decorators(node):
                if name == PLUGIN_ENTRY_DECORATOR:
                    module_level.add(_entry_ident(kwargs, node.name))
    assert not module_level, \
        f"以下入口写在模块级，宿主不会注册（面板 404）：{sorted(module_level)}"

    # 正例：@neko_plugin + NekoPluginBase 子类体内的类方法
    located: dict[str, tuple[str, str]] = {}
    for klass, fn in _class_methods(tree):
        if NEKO_PLUGIN_DECORATOR not in _class_decorator_names(klass):
            continue
        if not any(PLUGIN_BASE_CLASS_NAME in base for base in _class_base_names(klass)):
            continue
        for name, kwargs, _args in _decorators(fn):
            if name == PLUGIN_ENTRY_DECORATOR:
                located[_entry_ident(kwargs, fn.name)] = (klass.name, fn.name)

    missing = sorted(set(EXPECTED_ENTRIES) - set(located))
    assert not missing, (
        f"以下入口不在 @{NEKO_PLUGIN_DECORATOR}+{PLUGIN_BASE_CLASS_NAME} 子类的类体内"
        f"（模块级函数不注册）：{missing}"
    )


def test_manifest_llm_result_fields_match_decorators():
    """§17.4：清单 llm_result_fields 会**静默覆盖**装饰器值，两边必须逐条相等。

    [FACT] spec/ARCHITECTURE-FINAL.md:1300 结论 #4：``registry.py:683-690`` 在有清单值时
    直接采用清单值，装饰器值被丢弃且**不报错**。不一致 → LLM 侧字段静默缺失/多出。
    """
    manifest = {
        _manifest_entry_ident(e): tuple(e.get(LLM_RESULT_FIELDS_KEY) or ())
        for e in _declared_entry_dicts()
    }
    decorator: dict[str, tuple] = {}
    for node in _functions(ast.parse(_read_text("__init__.py"))):
        for name, kwargs, _args in _decorators(node):
            if name == PLUGIN_ENTRY_DECORATOR:
                decorator[_entry_ident(kwargs, node.name)] = tuple(
                    kwargs.get(LLM_RESULT_FIELDS_KEY) or ()
                )

    assert set(manifest) == set(EXPECTED_ENTRIES), \
        f"清单入口集合异常：{sorted(set(manifest) ^ set(EXPECTED_ENTRIES))}"
    assert set(decorator) == set(EXPECTED_ENTRIES), \
        f"装饰器入口集合异常：{sorted(set(decorator) ^ set(EXPECTED_ENTRIES))}"

    diffs = {k: {"manifest": list(manifest[k]), "decorator": list(decorator[k])}
             for k in manifest if manifest[k] != decorator[k]}
    assert not diffs, f"清单与装饰器 llm_result_fields 不一致（清单会静默覆盖）：{diffs}"
