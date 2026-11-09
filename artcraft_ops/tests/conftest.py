"""artcraft_ops 红阶段 conftest（stub-SDK 模式）。

对应 neko-plugin-sdk/references/testing.md §1/§2/§3：

1. 把插件根 ``artcraft_ops/`` 插进 ``sys.path``；
2. 把 ``artcraft_ops`` 预注册成**只有 __path__ 的命名空间包**，
   这样 ``import artcraft_ops.roots`` 能工作而**不执行** ``__init__.py``
   （``__init__.py`` 会 ``from plugin.sdk.plugin import ...``）；
3. 造 ``plugin`` / ``plugin.sdk`` / ``plugin.sdk.plugin`` / ``plugin.plugins`` 的 shim，
   供端到端测试以 ``plugin.plugins.artcraft_ops`` 名字加载真实 ``__init__.py``；
4. 清单对账测试用 ``ast`` 读装饰器 + ``tomllib`` 读 ``plugin.toml``，不 import 插件。

设计决策（[VALUE JUDGMENT]，供主理人裁决）：
  测试**不在文件顶层** import 实现模块，而是经 ``impl`` fixture 返回的加载器在
  **测试函数体内** import。好处：
    - 红阶段能真实收集到全部 N 个测试（不是 "collected 0 items / N errors"）；
    - 每个用例各自报 ``ModuleNotFoundError: No module named 'artcraft_ops.xxx'``，
      根因精确指向缺哪个模块，直接指导实现方；
    - 转绿判定标准干净：``N failed -> 0 failed / N passed``；
    - fixture 自身不抛异常，因此不会产生 "ERROR at setup"，error 数恒为 0。
  若主理人更想要 collection error，把 ``impl`` 改成顶层 import 即可，一行之差。
"""
from __future__ import annotations

import importlib
import importlib.util
import shutil
import sys
import tempfile
import types
import uuid
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent                 # .../artcraft_ops
SESSION_ROOT = PLUGIN_ROOT.parent             # .../2026-11-07-22-33-50

# 本 session 所有 tmp_path 的公共父目录（pytest_sessionfinish 一次性清理）
_TMP_BASE = Path(tempfile.gettempdir()) / "artcraft_ops_pytest"

for _p in (str(PLUGIN_ROOT), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# 1) 命名空间包：只有 __path__，不执行 __init__.py
# ---------------------------------------------------------------------------
if "artcraft_ops" not in sys.modules:
    _pkg = types.ModuleType("artcraft_ops")
    _pkg.__path__ = [str(PLUGIN_ROOT)]          # type: ignore[attr-defined]
    _pkg.__package__ = "artcraft_ops"           # type: ignore[attr-defined]
    sys.modules["artcraft_ops"] = _pkg

# ---------------------------------------------------------------------------
# 2) stub SDK shim
# ---------------------------------------------------------------------------
def _install_stub_sdk() -> None:
    if "plugin.sdk.plugin" in sys.modules:
        return

    plugin_pkg = types.ModuleType("plugin")
    plugin_pkg.__path__ = []                    # type: ignore[attr-defined]
    sdk_pkg = types.ModuleType("plugin.sdk")
    sdk_pkg.__path__ = []                       # type: ignore[attr-defined]
    sdk_plugin = types.ModuleType("plugin.sdk.plugin")
    plugins_pkg = types.ModuleType("plugin.plugins")
    plugins_pkg.__path__ = []                   # type: ignore[attr-defined]

    class SdkError(Exception):
        def __init__(self, message: str = "") -> None:
            super().__init__(message)
            self.message = message

    class Ok:
        def __init__(self, value=None) -> None:
            self.value = value

        def __bool__(self) -> bool:
            return True

        def __repr__(self) -> str:              # pragma: no cover
            return f"Ok({self.value!r})"

    class Err:
        def __init__(self, error=None) -> None:
            self.error = error
            self.code = "INTERNAL"             # [FACT] 宿主把 Err.code 抹平为 INTERNAL

        def __bool__(self) -> bool:
            return False

        def __repr__(self) -> str:              # pragma: no cover
            return f"Err({self.error!r})"

    class NekoPluginBase:
        """testing.md §3 的最小桩基类。"""

        def __init__(self, ctx) -> None:
            self.ctx = ctx
            self.config = getattr(ctx, "config", None)
            self.logger = getattr(ctx, "logger", None)
            self.plugin_id = getattr(ctx, "plugin_id", "artcraft_ops")
            self.plugin_dir = getattr(ctx, "plugin_dir", PLUGIN_ROOT)
            self.storage_dir = getattr(ctx, "storage_dir", PLUGIN_ROOT)

        def data_path(self, *parts: str) -> Path:
            base = Path(self.storage_dir)
            return base.joinpath("data", *parts)

        def cache_path(self, *parts: str) -> Path:
            return Path(self.storage_dir).joinpath("cache", *parts)

        def report_status(self, status) -> None:
            pushes = getattr(self.ctx, "statuses", None)
            if pushes is not None:
                pushes.append(status)

        def push_message(self, **kwargs) -> dict:
            pushes = getattr(self.ctx, "pushes", None)
            if pushes is not None:
                pushes.append(kwargs)
            return {"submitted": True}

    def _make_decorator(deco_name: str):
        """支持 @deco / @deco(...) / @deco("startup") 三种写法，全部返回原对象。"""

        def decorator(*args, **kwargs):
            def record(obj):
                meta = list(getattr(obj, "__neko_decorators__", ()))
                meta.append({"name": deco_name, "args": args, "kwargs": kwargs})
                try:
                    obj.__neko_decorators__ = meta
                except (AttributeError, TypeError):
                    pass
                return obj

            if len(args) == 1 and callable(args[0]) and not kwargs:
                return record(args[0])
            return record

        return decorator

    sdk_plugin.SdkError = SdkError              # type: ignore[attr-defined]
    sdk_plugin.Ok = Ok                          # type: ignore[attr-defined]
    sdk_plugin.Err = Err                        # type: ignore[attr-defined]
    sdk_plugin.NekoPluginBase = NekoPluginBase  # type: ignore[attr-defined]
    for _deco in (
        "neko_plugin", "plugin_entry", "llm_tool", "lifecycle",
        "timer_interval", "custom_event", "message",
    ):
        setattr(sdk_plugin, _deco, _make_decorator(_deco))

    plugin_pkg.sdk = sdk_pkg                    # type: ignore[attr-defined]
    plugin_pkg.plugins = plugins_pkg            # type: ignore[attr-defined]
    sdk_pkg.plugin = sdk_plugin                 # type: ignore[attr-defined]

    sys.modules["plugin"] = plugin_pkg
    sys.modules["plugin.sdk"] = sdk_pkg
    sys.modules["plugin.sdk.plugin"] = sdk_plugin
    sys.modules["plugin.plugins"] = plugins_pkg


_install_stub_sdk()

# ---------------------------------------------------------------------------
# 3) fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def impl():
    """实现模块加载器（延迟 import）。

    用法::

        def test_x(impl):
            sequence = impl("sequence")     # 红阶段 -> ModuleNotFoundError (FAILED)
            ...

    fixture 本身只返回闭包、不 import、不抛异常 => 不会产生 ERROR at setup。
    红阶段每个用例的失败根因恒为 ``No module named 'artcraft_ops.<name>'``。
    """

    def load(module_name: str):
        return importlib.import_module(f"artcraft_ops.{module_name}")

    return load


@pytest.fixture
def load_plugin_init():
    """以 ``plugin.plugins.artcraft_ops`` 名字加载真实 ``__init__.py``（testing.md §3）。

    返回一个闭包：测试函数体内调用，红阶段 ``__init__.py`` 未落盘 -> pytest.fail
    （FAILED，根因=实现缺失），同样不产生 ERROR at setup。
    """

    def load():
        init_path = PLUGIN_ROOT / "__init__.py"
        if not init_path.is_file():
            pytest.fail(
                "实现缺失：artcraft_ops/__init__.py 尚未落盘（端到端入口尚未实现）",
                pytrace=False,
            )
        name = "plugin.plugins.artcraft_ops"
        spec = importlib.util.spec_from_file_location(
            name, str(init_path), submodule_search_locations=[str(PLUGIN_ROOT)],
        )
        if spec is None or spec.loader is None:
            pytest.fail(f"无法为 {init_path} 构造 import spec", pytrace=False)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        return module

    return load


@pytest.fixture
def tmp_path():
    """覆盖 pytest 内置 tmp_path：只创建不删除（session 结束时统一清理）。

    testing.md §4 指出某些宿主/沙箱会在 session teardown 把内置 tmp_path 目录
    safe-delete 到不可写位置，导致"用例全过但退出码 1"。这里用 tempfile 自建目录，
    不经过 ``tmp_path_factory``，插件就无从拦截；所有目录挂在同一个基目录下，
    由 ``pytest_sessionfinish`` 一次性 rmtree，**不留跨 run 垃圾**。
    实测本机 (Python 3.13/3.14, tempfile.gettempdir()=...\\Temp) 工作正常。
    """
    base = _TMP_BASE / f"t{uuid.uuid4().hex[:12]}"
    base.mkdir(parents=True, exist_ok=False)
    return base


def pytest_sessionfinish(session, exitstatus) -> None:
    """收尾清理本 session 在系统临时目录留下的所有东西。

    卫生口径（主理人纪律「不留临时文件」）：
      before=$(ls "$TEMP/artcraft_ops_pytest" | wc -l)  -> 0
      pytest -> after                                                  -> 0
    用 ``ignore_errors=True``：清理失败绝不能影响退出码（那正是内置 tmp_path
    在部分宿主上踩过的坑）。
    """
    shutil.rmtree(_TMP_BASE, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4) marker 注册
# ---------------------------------------------------------------------------
def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "integration: 需要本机真实 photocraft-cli.exe 的端到端冒烟（默认 skip）",
    )
