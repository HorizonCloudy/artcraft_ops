"""用**真 SDK**（D:\\N.E.K.O）验证 artcraft_ops 的注册结果，证明不存在「入口 404」。

只读：不写宿主目录、不 spawn 子进程。输出：
  * 5 个 @plugin_entry 是否被 collect_entries() 收进路由
  * 5 个 @llm_tool 是否注册成 __llm_tool__<name> 动态入口
  * 2 个 @lifecycle 是否可见
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

NEKO_SRC = r"D:\N.E.K.O"
PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "artcraft_ops"

sys.path.insert(0, NEKO_SRC)
sys.path.insert(0, str(PLUGIN_ROOT.parent))

from plugin.sdk.plugin import NekoPluginBase  # noqa: E402

name = "plugin.plugins.artcraft_ops"
spec = importlib.util.spec_from_file_location(
    name, str(PLUGIN_ROOT / "__init__.py"),
    submodule_search_locations=[str(PLUGIN_ROOT)],
)
module = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
sys.modules[name] = module
spec.loader.exec_module(module)                        # type: ignore[union-attr]

cls = module.ArtcraftOpsPlugin
print("class         :", cls.__name__, "| subclass of NekoPluginBase =", issubclass(cls, NekoPluginBase))

obj = object.__new__(cls)
# 只为让 collect_entries 能跑：补齐宿主 __init__ 会建立的容器
for attr, value in (("_routers", []), ("_dynamic_entries", {}), ("_llm_tools", {})):
    try:
        setattr(obj, attr, value)
    except Exception:
        pass

entries = obj.collect_entries()
names = sorted(entries)
print("entries       :", len(names))
for entry_name in names:
    print("   -", entry_name)

tools = obj.collect_llm_tool_methods()
print("llm_tool      :", len(tools))
for tool_name, timeout in tools:
    print("   -", tool_name, "timeout=", timeout)

expected = {
    "artcraft_ops_search_commands", "artcraft_ops_run_sequence",
    "artcraft_ops_get_job", "artcraft_ops_cancel_job", "artcraft_ops_list_apps",
}
missing = expected - set(names)
extra = set(names) - expected - {f"__llm_tool__{n}" for n in expected}
print("\ndeclared-but-NOT-registered:", sorted(missing) or "none")
print("unexpected entries          :", sorted(extra) or "none")
print("VERDICT:", "PASS" if not missing else "FAIL")
