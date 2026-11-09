"""artcraft_ops 实机冒烟：**经插件代码**调用真实 ``photocraft-cli.exe``。

与 pytest 无关（不进 tests/，不改宿主），只做一件事：
  加载真实 ``artcraft_ops/__init__.py``（用最小 SDK shim，与 tests/conftest.py 同构）
  → ``on_startup`` → ``artcraft_ops_run_sequence``（RESULTS §6.1 的 5 步图层流水线）
  → 轮询 ``artcraft_ops_get_job`` 到终态 → 打印 argv / stdout / exit code / PNG 尺寸。

运行：
  python probe/plugin_smoke.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import struct
import sys
import types
import uuid
from pathlib import Path

SESSION = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = SESSION / "artcraft_ops"
STATE_DIR = SESSION / "probe" / "smoke_state"

NEW_DOC = {"width": 640, "height": 400, "background": "white"}
STEPS = [
    {"cmd": "layer.new.layer", "params": {"name": "Accent"}},
    {"cmd": "edit.fill", "params": {"color": "#c0392b"}},
    {"cmd": "layer.new.layer", "params": {"name": "Veil", "opacity": 0.5}},
    {"cmd": "edit.fill", "params": {"color": "#f1c40f"}},
    {"cmd": "document.inspect", "params": {}},
]
TERMINAL = {"succeeded", "failed", "cancelled", "crashed"}


# ---------------------------------------------------------------------------
# 最小 SDK shim（仅本脚本；与 artcraft_ops/tests/conftest.py 同构）
# ---------------------------------------------------------------------------
class SdkError(Exception):
    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class Ok:
    def __init__(self, value=None) -> None:
        self.value = value

    def __bool__(self) -> bool:
        return True


class Err:
    def __init__(self, error=None) -> None:
        self.error = error
        self.code = "INTERNAL"

    def __bool__(self) -> bool:
        return False


class _Ctx:
    pass


def _install_sdk() -> None:
    plugin_pkg = types.ModuleType("plugin")
    plugin_pkg.__path__ = []                      # type: ignore[attr-defined]
    sdk_pkg = types.ModuleType("plugin.sdk")
    sdk_pkg.__path__ = []                         # type: ignore[attr-defined]
    sdk_plugin = types.ModuleType("plugin.sdk.plugin")
    plugins_pkg = types.ModuleType("plugin.plugins")
    plugins_pkg.__path__ = []                     # type: ignore[attr-defined]

    class NekoPluginBase:
        def __init__(self, ctx) -> None:
            self.ctx = ctx
            self.config = getattr(ctx, "config", None)
            self.logger = getattr(ctx, "logger", None)
            self.plugin_id = getattr(ctx, "plugin_id", "artcraft_ops")
            self.plugin_dir = getattr(ctx, "plugin_dir", PLUGIN_ROOT)
            self.storage_dir = getattr(ctx, "storage_dir", PLUGIN_ROOT)

        def data_path(self, *parts: str) -> Path:
            return Path(self.storage_dir).joinpath("data", *parts)

        def cache_path(self, *parts: str) -> Path:
            return Path(self.storage_dir).joinpath("cache", *parts)

        def report_status(self, status) -> None:
            self.ctx.statuses.append(status)

        def push_message(self, **kwargs) -> dict:
            self.ctx.pushes.append(kwargs)
            return {"submitted": True}

    def make_decorator(name: str):
        def decorator(*args, **kwargs):
            def record(obj):
                meta = list(getattr(obj, "__neko_decorators__", ()))
                meta.append({"name": name, "args": args, "kwargs": kwargs})
                try:
                    obj.__neko_decorators__ = meta
                except (AttributeError, TypeError):
                    pass
                return obj

            if len(args) == 1 and callable(args[0]) and not kwargs:
                return record(args[0])
            return record

        return decorator

    sdk_plugin.SdkError = SdkError          # type: ignore[attr-defined]
    sdk_plugin.Ok = Ok                      # type: ignore[attr-defined]
    sdk_plugin.Err = Err                    # type: ignore[attr-defined]
    sdk_plugin.NekoPluginBase = NekoPluginBase  # type: ignore[attr-defined]
    for deco in ("neko_plugin", "plugin_entry", "llm_tool", "lifecycle",
                 "timer_interval", "custom_event", "message"):
        setattr(sdk_plugin, deco, make_decorator(deco))

    plugin_pkg.sdk = sdk_pkg                # type: ignore[attr-defined]
    plugin_pkg.plugins = plugins_pkg        # type: ignore[attr-defined]
    sdk_pkg.plugin = sdk_plugin             # type: ignore[attr-defined]
    sys.modules["plugin"] = plugin_pkg
    sys.modules["plugin.sdk"] = sdk_pkg
    sys.modules["plugin.sdk.plugin"] = sdk_plugin
    sys.modules["plugin.plugins"] = plugins_pkg


class Logger:
    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        print("  [warn]", a[0] % a[1:] if len(a) > 1 else a[0])

    def error(self, *a, **k):
        print("  [err ]", a[0] % a[1:] if len(a) > 1 else a[0])

    def exception(self, *a, **k):
        print("  [exc ]", a)


class Config:
    async def dump(self) -> dict:
        return {}


class Images:
    def __init__(self) -> None:
        self.uploads = 0

    async def upload(self, data: bytes, mime: str = "image/png") -> dict:
        self.uploads += 1
        return {"type": "image", "url": f"stub://preview/{self.uploads}.png"}


class Ctx:
    def __init__(self, storage_dir: Path) -> None:
        self.config = Config()
        self.logger = Logger()
        self.images = Images()
        self.plugin_id = "artcraft_ops"
        self.plugin_dir = PLUGIN_ROOT
        self.storage_dir = storage_dir
        self.pushes: list = []
        self.statuses: list = []
        self.current_lanlan = "lanlan"

    def push_message(self, **kwargs) -> dict:
        self.pushes.append(kwargs)
        return {"submitted": True}

    def report_status(self, status) -> None:
        self.statuses.append(status)


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        raw = path.read_bytes()[:32]
    except OSError:
        return None
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", raw[16:24])
    return width, height


def load_plugin_module():
    name = "plugin.plugins.artcraft_ops"
    spec = importlib.util.spec_from_file_location(
        name, str(PLUGIN_ROOT / "__init__.py"),
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


async def main() -> int:
    _install_sdk()
    module = load_plugin_module()
    storage = STATE_DIR / f"state-{uuid.uuid4().hex[:8]}"
    storage.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(storage)
    plugin = module.ArtcraftOpsPlugin(ctx)

    await plugin.on_startup(_ctx=ctx)
    print("[startup] ok; cache_dir =", module.command_catalog.current_cache_dir())

    # 只读埋点：记录真实 stdout（不改实现，只在 harness 侧 tee 一份）
    captured: list[str] = []
    original = module.runner.stream_lines

    async def spy(handle, on_line=None, *args, **kwargs):
        def tee(line: str) -> None:
            captured.append(line)
            if on_line is not None:
                on_line(line)

        return await original(handle, tee, *args, **kwargs)

    module.runner.stream_lines = spy

    started = asyncio.get_event_loop().time()
    result = await plugin.artcraft_ops_run_sequence(
        app="photocraft", steps=STEPS, new_doc=NEW_DOC,
        out_file="smoke_layers.png", inspect=False, _ctx=ctx,
    )
    elapsed = asyncio.get_event_loop().time() - started
    if not isinstance(result, Ok):
        print("FAIL 入口未返回 Ok:", getattr(result, "error", result))
        return 1
    payload = result.value
    print(f"[entry ] 秒回 {elapsed * 1000:.1f} ms -> {json.dumps(payload, ensure_ascii=False)}")

    job_id = payload["job_id"]
    last = None
    for _ in range(3000):
        answer = await plugin.artcraft_ops_get_job(job_id=job_id, _ctx=ctx)
        last = answer.value if isinstance(answer, Ok) else {"status": "failed"}
        if last.get("status") in TERMINAL:
            break
        await asyncio.sleep(0.05)

    job = module.jobs.get_job(job_id)
    argv = list(job.argv) if job else []
    print("\n[argv  ]", json.dumps(argv, ensure_ascii=False))
    print("[stdout] %d line(s):" % len(captured))
    for line in captured:
        print("   ", line[:400])
    print("[result]", json.dumps(last, ensure_ascii=False)[:1200])

    out_path = storage / "data" / "smoke_layers.png"
    size = png_size(out_path)
    print("[png   ]", out_path, "bytes=", out_path.stat().st_size if out_path.exists() else -1,
          "size=", size)

    warnings = last.get("warnings") or []
    print("[warn  ]", warnings)
    await plugin.on_shutdown(_ctx=ctx)
    print("[shutdn] ok; pushes =", len(ctx.pushes), "statuses =", len(ctx.statuses))

    ok = (
        last.get("status") == "succeeded"
        and last.get("exit_code") == 0
        and out_path.is_file()
        and size is not None
        and len(captured) >= 5
        and any("flattened" in w for w in warnings)
    )
    print("\nVERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(asyncio.run(main()))
