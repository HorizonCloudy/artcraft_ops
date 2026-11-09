"""Red-phase test fixtures for artcraft_ops.

[事实 / FACT] 所有数据均抄自：
  - ../../probe/RESULTS.md（§2.2 commands --json、§5.5 命名空间报错、
    §6.1 多步图层流水线真实 stdout 回放、§6.2 document.inspect 结构、§6.3 检索样例）
  - ../../spec/ARCHITECTURE-FINAL.md（§2 数据结构默认值、§3 APP_SPECS 表、§8 错误码表、§9 清单）
    （旧稿 spec/ARCHITECTURE.md 已作废并在清理中删除，见 FINAL 文首声明）

本文件是**纯数据夹具**：只依赖 Python 标准库（asyncio / json / dataclasses / pathlib），
绝不 import artcraft_ops 的任何实现模块，因此红阶段（实现不存在）本文件永远能加载。
测试文件通过 `from _fixtures import ...` 取真实数据；conftest.py 已把 tests/ 插入 sys.path。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

# ---------------------------------------------------------------------------
# 安装路径（RESULTS §1 / ARCHITECTURE §3）[FACT]
# 注意：测试**绝不启动**这些 exe，只把路径当字符串断言。
# ---------------------------------------------------------------------------
PHOTOCRAFT_CLI = r"E:\Program Files\Artcraft\PhotoCraft\photocraft-cli.exe"
VECTORCRAFT_CLI = r"E:\Program Files\Artcraft\VectorCraft\vectorcraft-cli.exe"
CRAFT_ANYTHING_CLI = r"E:\Program Files\Artcraft\Craft Anything\craft-anything-cli.exe"

KNOWN_PATHS = {
    "photocraft": PHOTOCRAFT_CLI,
    "vectorcraft": VECTORCRAFT_CLI,
    "craft_anything": CRAFT_ANYTHING_CLI,
}

# ---------------------------------------------------------------------------
# 拍平警告（RESULTS §6.1 / ARCHITECTURE §2 AppSpec.flatten_warning_substr）[FACT]
# ---------------------------------------------------------------------------
FLATTEN_WARNING_SUBSTR = "flattened; layers, masks and blend modes are not kept"
FLATTEN_WARNING_FULL_LINE = (
    "warning: 3 layer(s) flattened; layers, masks and blend modes are not kept"
)
LAYER_PIPELINE_STDERR = FLATTEN_WARNING_FULL_LINE + "\n"

# ---------------------------------------------------------------------------
# document.inspect 真实返回结构（RESULTS §6.2，节选真实字段）[FACT]
# ---------------------------------------------------------------------------
INSPECT_RESULT: dict = {
    "activeLayer": 4,
    "canUndo": True,
    "canRedo": False,
    "channels": {"alpha": [], "colors": [{"name": "Red", "visible": True}], "composite": "RGB"},
    "depth": 8,
    "width": 640,
    "height": 400,
    "mode": "Rgb",
    "resolution": 72.0,
    "revision": 5,
    "hasSelection": False,
    "selectionBounds": None,
    "history": ["Open", "New Layer", "Fill", "New Layer", "Fill"],
    "layers": [
        {
            "id": 4, "name": "Veil", "kind": "Pixel", "blend": "Normal", "opacity": 0.5,
            "bounds": [0, 0, 640, 400], "visible": True, "hasMask": False,
            "hasVectorMask": False, "clipped": False, "fill": 1.0,
            "labelColor": "none", "selected": True,
        },
        {
            "id": 3, "name": "Accent", "kind": "Pixel", "blend": "Normal", "opacity": 1.0,
            "bounds": [0, 0, 640, 400], "visible": True, "hasMask": False,
            "hasVectorMask": False, "clipped": False, "fill": 1.0,
            "labelColor": "none", "selected": False,
        },
        {
            "id": 2, "name": "Background", "kind": "Pixel", "blend": "Normal", "opacity": 1.0,
            "bounds": [0, 0, 640, 400], "visible": True, "hasMask": False,
            "hasVectorMask": False, "clipped": False, "fill": 1.0,
            "labelColor": "none", "selected": False,
        },
    ],
    "selectedLayers": [4],
    "snapshots": [{"id": 0, "name": "Untitled"}],
    "layerComps": [],
    "notes": [],
    "timeline": None,
    "symmetry": None,
    "measurement": {"count": 0, "ruler": None, "scale": "1 pixels = 1.0000 pixels"},
}

# ---------------------------------------------------------------------------
# §6.1 多步图层流水线：真实逐行 stdout 回放 [FACT]
# ---------------------------------------------------------------------------
LAYER_PIPELINE_STDOUT_LINES: tuple[str, ...] = (
    '{"command":"layer.new.layer","result":{"layer":3}}',
    '{"command":"edit.fill","result":{"contents":"color"}}',
    '{"command":"layer.new.layer","result":{"layer":4}}',
    '{"command":"edit.fill","result":{"contents":"color"}}',
    '{"command":"document.inspect","result":' + json.dumps(INSPECT_RESULT, ensure_ascii=False) + '}',
)
LAYER_PIPELINE_OUT_FILE = "probe/e2e_layers.png"          # RESULTS §6.1 产物 [FACT]
LAYER_PIPELINE_OUT_ABS = r"C:\work\artcraft\probe\e2e_layers.png"

# §6.1 真实 --new / --cmd / --params 序列（供 build_argv 断言）
LAYER_PIPELINE_NEW_DOC = {"width": 640, "height": 400, "background": "white"}
LAYER_PIPELINE_STEPS: tuple[tuple[str, dict], ...] = (
    ("layer.new.layer", {"name": "Accent"}),
    ("edit.fill", {"color": "#c0392b"}),
    ("layer.new.layer", {"name": "Veil", "opacity": 0.5}),
    ("edit.fill", {"color": "#f1c40f"}),
    ("document.inspect", {}),
)

# ---------------------------------------------------------------------------
# VectorCraft run 的 stdout 是「伪步」形状（主理人真机验收，2026-11 记录）
# ---------------------------------------------------------------------------
# [FACT 结构] 一次只下 1 个 --cmd，但 stdout 有 3 行，**只有中间那行带 command**：
#   0  step=open    command=None                keys=['path','result','step']
#   1  step=cmd     command=document.inspect    keys=['command','result','step']
#   2  step=export  command=None                keys=['result','step']
# 对照组 [FACT]：PhotoCraft 5 行 keys=['command','result']（无 step 键，5 行全是命令）；
#                Craft Anything 单行 keys=['result']（一个 command 都没有）。
# 注意：键结构是真机事实，**具体取值是占位**（in.svg/out.svg 等），只用于断言计数语义。
VECTORCRAFT_STDOUT_LINES: tuple[str, ...] = (
    # keys=['path','result','step']
    json.dumps({"path": "in.svg", "result": {"opened": True}, "step": "open"}),
    # keys=['command','result','step']  ← 唯一带 command 的一行
    json.dumps({"command": "document.inspect", "result": INSPECT_RESULT, "step": "cmd"}),
    # keys=['result','step']
    json.dumps({"result": {"path": "out.svg", "format": "svg", "bytes": 1024},
                "step": "export"}),
)

# ---------------------------------------------------------------------------
# commands --json 真实结构（RESULTS §2.2 / §3）[FACT]
# PhotoCraft 7 字段（含 disabledReason / needsDocument）；VectorCraft 6 字段（少 2）
# ---------------------------------------------------------------------------
COMMANDS_JSON_PHOTOCRAFT_7: list[dict] = [
    {
        "disabledReason": None,
        "enabled": True,
        "id": "file.new",
        "label": "New…",
        "menu": ["File"],
        "needsDocument": False,
        "params": '{"width":1..300000=1920,"height":1..300000=1080}',
        "shortcut": "Cmd+N",
    },
    {
        "disabledReason": None,
        "enabled": True,
        "id": "filter.sharpen.unsharpMask",
        "label": "Unsharp Mask…",
        "menu": ["Filter", "Sharpen"],
        "needsDocument": True,
        "params": '{"amount":1..500=50,"radius":0.1..1000=1,"threshold":0..255=0}',
        "shortcut": "",
    },
]
COMMANDS_JSON_VECTORCRAFT_6: list[dict] = [
    {
        "enabled": True,
        "id": "file.new",
        "label": "New…",
        "menu": ["File"],
        "params": '{"width":1..300000=1920}',
        "shortcut": "Cmd+N",
    },
]
SHARPEN_UNSHARPMASK_PARAMS = '{"amount":1..500=50,"radius":0.1..1000=1,"threshold":0..255=0}'

# §6.3 --filter sharpen → 5 条 [FACT]
SHARPEN_COMMAND_IDS: tuple[str, ...] = (
    "filter.sharpen.sharpen",
    "filter.sharpen.sharpenMore",
    "filter.sharpen.sharpenEdges",
    "filter.sharpen.unsharpMask",
    "filter.sharpen.smartSharpen",
)

# ---------------------------------------------------------------------------
# §5.5 命名空间陷阱：真实报错与 exit code [FACT]
# ---------------------------------------------------------------------------
NAMESPACE_TRAP_STDERR = (
    "error: `doc.inspect`: unknown command `doc.inspect`; "
    "did you mean `document.inspect` or `session.inspect`?"
)
NAMESPACE_TRAP_EXIT_CODE = 2
NAMESPACE_TRAP_DID_YOU_MEAN_STDERR = (
    "error: unknown command `doc.inspect`; did you mean `document.inspect`?"
)

# ---------------------------------------------------------------------------
# §8 / §9.4 roots 未授权 stderr 原文 [FACT]
# ---------------------------------------------------------------------------
ROOTS_NOT_GRANTED_STDERR_A = "automation filesystem access is not granted: read authority is absent"
ROOTS_NOT_GRANTED_STDERR_B = "read authority is absent"

# ---------------------------------------------------------------------------
# §2.6 instance.json 真实结构（token 用占位 64hex）[FACT 结构]
# ---------------------------------------------------------------------------
INSTANCE_JSON_SAMPLE = json.dumps(
    {"pid": 11664, "port": 52903, "token": "a" * 64},
)
LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_URL_OK = "http://127.0.0.1:52903/"
LOOPBACK_URL_LOCALHOST = "http://localhost:52903/"

# ---------------------------------------------------------------------------
# ARCHITECTURE §8 错误码表（19 个）[FACT]
# ---------------------------------------------------------------------------
EXPECTED_ERROR_CODES: frozenset[str] = frozenset({
    "APP_NOT_FOUND", "APP_VERSION_FAILED", "COMMANDS_DISCOVERY_FAILED",
    "NO_COMMAND_MATCH", "POLICY_VIOLATION", "PATH_OUTSIDE_ROOTS",
    "PATH_UNSAFE", "NAMESPACE_ERROR", "LIMIT_STEPS", "LIMIT_REPLY",
    "JOB_NOT_FOUND", "JOB_TIMEOUT", "JOB_CANCELLED", "JOB_CRASHED",
    "CLI_USAGE_ERROR", "CLI_TASK_FAILED", "ROOTS_NOT_GRANTED",
    "BRIDGE_NOT_IMPLEMENTED", "STUDIO_NOT_IMPLEMENTED",
})

# ARCHITECTURE §5/§9 入口名 [FACT]（6 个：前 5 个是 CLI 控制面，第 6 个是 Studio GUI 交接）
EXPECTED_ENTRIES: tuple[str, ...] = (
    "artcraft_ops_search_commands",
    "artcraft_ops_run_sequence",
    "artcraft_ops_get_job",
    "artcraft_ops_cancel_job",
    "artcraft_ops_list_apps",
    "artcraft_ops_open_in_studio",
)

# ARCHITECTURE §2 AppSpec / RootsPolicy / CommandPolicy 默认值 [FACT]
DEFAULT_READONLY_PREFIXES: tuple[str, ...] = (
    "document.inspect", "session.list", "jobs.list", "command.list",
)
DEFAULT_DENY_PREFIXES: tuple[str, ...] = ("prefs.", "plugin.", "updates.")
DEFAULT_READ_FLAG = "--automation-read-root"
DEFAULT_WRITE_FLAG = "--automation-write-root"
DEFAULT_MAX_STEPS = 256
MAX_REQUEST_LINE_BYTES = 1_048_576      # 1 MiB
MAX_REPLY_BYTES = 8_388_608            # 8 MiB
PUSH_MAX_BYTES = 524_288               # 512 KiB
PUSH_MIN_INTERVAL_S = 2.0


# ---------------------------------------------------------------------------
# 假对象工厂（纯标准库，测试拥有自己的数据，不启动真实进程）
# ---------------------------------------------------------------------------
class _EofReader:
    """退化替身：无 running loop 时的最小 StreamReader（纯内存，不碰事件循环）。

    只实现 runner 实际用到的只读子集，语义与 ``asyncio.StreamReader`` 对齐：
      ``readline()`` / ``read(n)`` / ``readuntil(sep)`` / ``readexactly(n)``
      返回 coroutine；``at_eof()`` 同步返回是否已读完；``drain()`` 是 no-op。
    数据在构造时就已全部喂入并处于 EOF（等同 ``feed_data`` + ``feed_eof``），
    因此这些 await 不会真的挂起，但调用方仍须 ``await``。
    """

    __slots__ = ("_buf", "_pos")

    def __init__(self, chunks: "list[bytes]") -> None:
        self._buf = b"".join(chunks)
        self._pos = 0

    def at_eof(self) -> bool:
        return self._pos >= len(self._buf)

    async def drain(self) -> None:
        return None

    async def readline(self) -> bytes:
        return await self.readuntil(b"\n")

    async def readuntil(self, separator: bytes = b"\n") -> bytes:
        if not separator:
            raise ValueError("Separator should be at least one-byte string")
        idx = self._buf.find(separator, self._pos)
        end = len(self._buf) if idx < 0 else idx + len(separator)
        chunk = self._buf[self._pos:end]
        self._pos = end
        return chunk

    async def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk = self._buf[self._pos:]
            self._pos = len(self._buf)
            return chunk
        chunk = self._buf[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    async def readexactly(self, n: int) -> bytes:
        chunk = self._buf[self._pos:self._pos + n]
        if len(chunk) < n:
            self._pos = len(self._buf)
            raise asyncio.IncompleteReadError(chunk, n)
        self._pos += n
        return chunk

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<_EofReader {len(self._buf) - self._pos}/{len(self._buf)}B left>"


def _make_stream_reader(chunks: list[bytes]):
    """按「首次访问时是否有 running loop」二选一造 reader。

    [FACT] 本机 Python 3.13.12 的 ``StreamReader.__init__`` 内部执行
    ``self._loop = events.get_event_loop()`` 且**不再接受** ``loop=`` 参数：
      - 同步上下文（无 running loop，或前一用例 ``asyncio.run()`` 结束后
        current loop 被置空）→ ``RuntimeError: There is no current event loop
        in thread 'MainThread'``；
      - 若强行绑定一个再被关闭的 loop，后续 ``read()`` → ``_wait_for_data()``
        → ``self._loop.create_future()`` → ``RuntimeError: Event loop is closed``。
    因此**只在有 running loop 时**用真 StreamReader（此时它绑定的正是消费方
    runner 的 loop），否则退化到 loop 无关的 ``_EofReader``。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _EofReader(chunks)
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return reader


class FakeProcess:
    """最小 asyncio.subprocess.Process 替身。

    - stdout/stderr **惰性**构造（property）：``__init__`` 只缓存待灌入字节，
      首次访问时才建 reader，从而拿到消费方（runner 的 ``asyncio.run``）的 loop
    - stdout/stderr 是真实 asyncio.StreamReader（支持 read / readuntil / feed_eof）；
      若首次访问发生在无 running loop 的同步上下文，则退化为 ``_EofReader``
    - terminate()/kill() 只计数，不碰真实进程（并把 returncode 改成 -15 / -9）
    - wait() 直接返回预设 returncode

    约束：一个实例只在一个消费 loop 内读（首次访问时绑定的那个）。跨多次
    ``asyncio.run()`` 复用同一实例不在支持范围内——现有用例都是「一进程一实例」。
    """

    def __init__(
        self,
        stdout_lines: tuple[str, ...] = (),
        stderr_text: str = "",
        returncode: int = 0,
        encoding: str = "utf-8",
    ) -> None:
        self.pid = 424242
        self._returncode = returncode
        self._stdout_chunks = [(line + "\n").encode(encoding) for line in stdout_lines]
        self._stderr_chunks = [stderr_text.encode(encoding)] if stderr_text else []
        self._stdout = None
        self._stderr = None
        self.terminate_calls = 0
        self.kill_calls = 0

    @property
    def stdout(self):
        if self._stdout is None:
            self._stdout = _make_stream_reader(self._stdout_chunks)
        return self._stdout

    @property
    def stderr(self):
        if self._stderr is None:
            self._stderr = _make_stream_reader(self._stderr_chunks)
        return self._stderr

    @property
    def returncode(self) -> int:
        return self._returncode

    async def wait(self) -> int:
        return self._returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._returncode = -15

    def kill(self) -> None:
        self.kill_calls += 1
        self._returncode = -9

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<FakeProcess rc={self._returncode} term={self.terminate_calls}>"


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, tuple]] = []

    def _log(self, level: str, *args, **kwargs) -> None:
        self.records.append((level, args))

    def debug(self, *a, **k): self._log("debug", *a)
    def info(self, *a, **k): self._log("info", *a)
    def warning(self, *a, **k): self._log("warning", *a)
    def error(self, *a, **k): self._log("error", *a)
    def exception(self, *a, **k): self._log("exception", *a)


class FakeConfig:
    async def dump(self) -> dict:
        return {}


class FakeImages:
    def __init__(self) -> None:
        self.uploads: list[bytes] = []

    async def upload(self, data: bytes, mime: str = "image/png") -> dict:
        self.uploads.append(data)
        return {"type": "image", "url": "stub://uploaded"}


class FakeCtx:
    """stub-SDK 端到端用的最小上下文（ARCHITECTURE §5 / testing.md §3）。"""

    def __init__(self, storage_dir: Path, plugin_dir: Path) -> None:
        self.config = FakeConfig()
        self.logger = FakeLogger()
        self.images = FakeImages()
        self.plugin_id = "artcraft_ops"
        self.plugin_dir = plugin_dir
        self.storage_dir = storage_dir
        self.pushes: list[dict] = []
        self.statuses: list = []
        self.current_lanlan = "lanlan"

    def push_message(self, **kwargs) -> dict:
        self.pushes.append(kwargs)
        return {"submitted": True}


def fake_handle(job_id: str = "job-1", proc: FakeProcess | None = None) -> SimpleNamespace:
    """runner.ProcHandle 的 duck-typed 替身（jobs 层测试用）。"""
    return SimpleNamespace(
        job_id=job_id,
        proc=proc if proc is not None else FakeProcess(),
        started_at=0.0,
    )
