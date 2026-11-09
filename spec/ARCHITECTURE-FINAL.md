# artcraft_ops — Phase 2 **最终**实现规格

⚠️ **本文件是唯一有效版本**，`spec/ARCHITECTURE.md`（初版，33 测试）已作废。云测 / 云雨 / 云简 / 云严 / 云安 / 云稳一律以本文件为准。
来源：neko-architect Phase 2 最终交付（已并入 3 条修正 + 4 条新增 + 6 条收口补遗）。
证据底座：`../probe/RESULTS.md`（9 节）。
标注：[FACT] 证据约束 / [VALUE JUDGMENT] 架构决策 / [INFERENCE] 需实机核验。

---

## §0 主理人裁决（云远交回的 4 点口径，逐条定案）

| # | 争议 | 裁决 | 依据 |
|---|---|---|---|
| (a) | 状态根运行时配置叫 `config\config.toml` 还是 `config\plugin.toml` | **`config\plugin.toml`**（我原任务书的 `config\config.toml` 作废） | `neko-plugin-sdk/SKILL.md:111-113` 原文：「`config.example.toml` … 宿主首次安装时**逐字节复制到 `<状态根>/config/plugin.toml`**」。代码根清单 `plugin.toml`（含 `[plugin]`）与状态根配置 `config\plugin.toml`（不含 `[plugin]`）是**两个不同位置**，同名不冲突。 |
| (b) | 装饰器/清单用 `id=` 还是 `name=` | **`id` 必须保留**（唯一标识，与 `@plugin_entry(id=...)` 对应）；`name` 作显示名可选 | SKILL.md 最小插件示例同时有 `id` 与 `name`；云远最终版已统一 `id=`，认可。是否 `name` 必填 → 云雨 stub-SDK 核验。 |
| (c) | 修正2「显式发 ArtCraft 侧取消」在 v1 主通道的落点 | **认可云远分层**：`CLI_RUN` 单进程无远端 job → `terminate()` 本地 Popen 即满足「不只 cancel 协程」；`CLI_SERVE`/`HTTP_BRIDGE` 才**强制**显式发 `jobs.cancel(remote_job_id)`。 | 我原修正2 的意图是「宿主 `CANCEL_RUN` 只 cancel 本地协程，不能就此了事」，`terminate()` 已超出该范畴。 |
| (d) | 工具名分叉（`run_command`/`execute`/`tools`）与 AppSpec 表是否冲突 | **不冲突**，属补充证据 | 命令 id/工具名一律动态发现，AppSpec 只收敛 **CLI 参数名**差异。 |

**两条证据链必须分开，不混成一套**：
- 链 1（ArtCraft 协议侧）[FACT]：官方 `docs/`+`book/src/`+`photocraft.im`+releases API+本机 CLI 实测 → 定义「ArtCraft 允许什么」。
- 链 2（N.E.K.O 宿主侧）[FACT]：`D:\N.E.K.O` 源码 `file:line` + 本机实机 → 定义「N.E.K.O 能做什么」。
- **两链交汇只在 4 处**：`runner`（CLI 冷启 0.294s ↔ >5s stall 要 `to_thread`）、`jobs`（`jobs.cancel` ↔ `CANCEL_RUN` 不代调 → 显式转发）、`result`（8 MiB 回复 ↔ 512 KiB 整条丢弃 → 落盘只推句柄）、`errors`（token 非授权边界 ↔ 不做工具级授权 + Err 原样回包 → 插件白名单 + `redact()` 四处脱敏）。

---

## 结论先行

`artcraft_ops` = 单插件、零第三方依赖，以「一次性无头 CLI `run`」为主通道，采用「per-app 薄 adapter 表（运行时配置驱动）+ 两级 LLM 工具（检索→执行）+ 长任务 job 状态机 + 消息面落盘句柄」分层，依赖方向 UI→应用→领域→基础设施（依赖倒置、无循环）；13 个 app 用 `AppSpec` 插槽预留，ArtCraft Studio 本地 GUI / 云端生成两条通道 v1 只留接口不实现。**不是从零造轮子**，从宿主 3 个成熟骨架适配。

3 条理由：
1. [FACT 链 1] CLI 冷启动 0.294s、`run` 一次完成「新建→命令→导出」。走一次性 `run` 不必常驻 `serve`/bridge，直接规避 #9777 token 泄露与 roots 分置。零第三方依赖（asyncio 标准库）免去 vendor（缺 dist-info 拒载、须 cp311-win_amd64）。
2. [FACT 链 1] 993/933 条命令不能塞进模型上下文，`commands --json --filter` + `params` 人类可读 schema 天然支持「检索 + 执行」两段式；工具名分叉（RFC-0012 rollout 中）与参数名差异用可配置 `AppSpec` 表收敛。**首次 entry 才 lazy discovery**（链 2：startup 瞬时 `asyncio.run` 会取消后台 task）。
3. [FACT 链 2] 入口 10s 且只 `task.cancel()` 不杀 Popen、LLM 工具 300s、消息面 512 KiB → 必须「入口秒回 `job_id` + 后台子进程 + `*_status`/`*_cancel` 轮询 + 进度节流 + **大响应落盘只推句柄**」，自存 Popen 句柄在 shutdown/取消时 `terminate()`。

术语：YUI=宿主对话 Agent；adapter=把各 app CLI 参数差异翻译成统一调用的薄层；roots=ArtCraft 目录级读写授权（fail-closed）；消息面=宿主 `push_message`/`report_status` 回包通道。

---

## 1. 模块划分与函数签名

代码根（宿主唯一加载源，目录名必须 == plugin id）：`C:\Users\54301\AppData\Local\N.E.K.O\.neko-plugin-installations\plugins\artcraft_ops\`
状态根：`C:\Users\54301\AppData\Local\N.E.K.O\plugins\artcraft_ops\{config,data,cache}\`
**开发期代码落点（本次会话）**：`C:\Users\54301\WorkBuddy\2026-11-07-22-33-50\artcraft_ops\`（不写宿主安装目录）
cwd 一律绝对路径 [FACT 链 2：`launcher_core/runtime.py:1211-1213` 的 `os.chdir(sys._MEIPASS)`]。

```
artcraft_ops/
├── plugin.toml            # A 清单（代码根，含 [plugin]）见 §9
├── config.example.toml    # C 模板（不含 [plugin]）见 §9
├── __init__.py            # 应用层：入口 + LLM 工具双注册 + lifecycle
├── spec.py                # 叶子：dataclass / Enum / 常量（§2）
├── app_registry.py        # 领域：BUILTIN_SPECS + 运行时配置合并（§3）
├── locator.py             # 基础设施：CLI 定位顺序 / 版本 token / 长名路径
├── command_catalog.py     # 领域：lazy discovery + 缓存 + 检索
├── sequence.py            # 领域：命名空间归一化 / 白名单 / argv 构造
├── roots.py               # 领域：roots 纯文本比对 + Windows 路径白名单
├── runner.py              # 基础设施：子进程执行（create_subprocess_exec）
├── jobs.py                # 应用层：job 状态机 / 句柄回收 / 节流 / 恢复 / report_status
├── result.py              # 领域：逐行 JSON 解析 / 警告透传 / 消息面落盘 materialize
├── errors.py              # 叶子：错误码表 + CLI→宿主映射 + redact() + safe_err()
├── config.py              # 叶子：await self.config.dump() 运行时配置加载
├── i18n.py                # 叶子：CODE→zh/en 文案
├── bridge.py              # 基础设施[预留]：回环控制通道，v1 不实现
├── studio_channel.py      # 基础设施[预留]：Studio 云端生成 MCP，v1 不实现
└── tests/                 # 见 §10
```

依赖方向：`__init__` → `jobs/sequence/command_catalog/config/result/errors`；`jobs` → `runner/result/errors/spec`；`sequence` → `command_catalog/roots/spec/app_registry`；`command_catalog` → `runner/spec`；`runner/locator/bridge/studio_channel` → `spec`；`spec/errors/config/i18n/app_registry` 为叶子。防循环：runner 不 import jobs（jobs 通过 `_PROC_HANDLES` 持有 ProcHandle）；result 不 import runner（吃 `RawOutput`）。

**从成熟骨架适配（非从零造轮子）** [FACT 链 2]：
- `maa_end\maaend\controlapi.py:100-160`（外部 exe + `/run` `/stop` `/events?since=N`）→ `runner.py` spawn/terminate/探活 + `jobs.py` 按需拉起
- `dsh_harness_bridge\__init__.py:146-155`（`httpx` 打 `127.0.0.1:<port>`）→ `bridge.py` `build_http_client_no_proxy()`
- `plugin\plugins\mcp_adapter\__init__.py:344-374`（stdio/sse/streamable-http MCP + `create_subprocess_exec`）→ `runner.spawn()` argv/stdio 契约（**仅抄骨架，不走 mcp_adapter 注册链路**）
- 外部参考：`dsh-artcraft`（统一 dataRoot + `/artcraft/api/*`）、`theCosmicCrafter/artcraft-mcp`（`/health|start|stop|token` 受管子进程）→ `jobs.py` 生命周期

```python
# locator.py
CLI_LOCALAPPDATA_TPL = r"%LOCALAPPDATA%\Programs\ArtCraft\<AppDir>\<app>-cli.exe"
def resolve_cli(app_id: str, cfg: dict) -> str:
    """定位顺序，命中即止：
      1. cfg 显式覆盖（状态根 config\plugin.toml 的 [artcraft.apps.<id>] cli_path）
      2. expandvars(r"%LOCALAPPDATA%\Programs\ArtCraft\<AppDir>\<app>-cli.exe")
      3. 注册表卸载项（HKLM/HKCU Uninstall DisplayName→InstallLocation 推导 <app>-cli.exe）
      4. 三个本机实测 E 盘绝对路径兜底（见 §3）
      5. 全未命中 → raise AppNotFound → safe_err(APP_NOT_FOUND)
    注意：C:\Program Files\ArtCraft\artcraft.exe 是 L4 Studio，无 CLI；不假设其同目录有 CLI。
    只在此处允许直读 os.environ 的 LOCALAPPDATA / ProgramFiles（安装定位用 env）。"""
def long_path(p: str) -> str:
    """从 os.environ['LOCALAPPDATA'] 或 plugin.data_path().resolve() 派生；转正斜杠 C:/...；
       必要时 ctypes GetLongPathNameW 展开 8.3 短名；再展开 \\?\C:\… / \\?\UNC\… 前缀（v0.18.0 起接受）。
       禁止拼 sys.executable / os.getcwd()（cwd=sys._MEIPASS，且含 PROGRA~2 短名会被 roots 拒）。"""
async def detect_version(cli_path: str, spec: "AppSpec") -> str:
    """优先 --version；失败回退 exe sha256 前 12 位作 version_token（[INFERENCE] --version 未验证，回退不抛）。"""
def read_instance_json(path: str) -> dict[str, Any]:
    """读 {pid,port,token:<64hex>}；校验文件属主=当前用户；内容只入内存、绝不进日志/回包。"""
def build_cli_env() -> dict[str, str]:
    """完整继承 os.environ（含 PATH/LOCALAPPDATA/ProgramFiles），返回脱敏副本供日志。"""

# config.py
async def load_runtime_config(plugin) -> dict:
    """await self.config.dump() 读 <state root>\plugins\artcraft_ops\config\plugin.toml。
       adapter 差异表 / roots / limits / i18n 放这里，**不放进程 env**
       （宿主代理热切换改 HTTP_PROXY/NO_PROXY/ALL_PROXY，config_router/proxy.py:82-101）；
       只有 LOCALAPPDATA / ProgramFiles 安装定位用 env 才允许 os.environ 直读。"""

# app_registry.py
BUILTIN_SPECS: dict[str, "AppSpec"] = {...}      # 3 个已验证 app 默认值，零配置可跑
async def load_specs(plugin) -> dict[str, "AppSpec"]:
    cfg = await load_runtime_config(plugin)
    return merge_specs(BUILTIN_SPECS, cfg.get("artcraft", {}).get("apps", {}).get("specs", {}))
def get_spec(app_id: str) -> "AppSpec": ...      # 未注册 → raise AppNotFound

# command_catalog.py —— lazy discovery
_DISCOVERY_LOCKS: dict[str, asyncio.Lock] = {}   # per-app，保证并发可重入只 spawn 一次
def cache_key(app_id: str, version_token: str, commands_hash: str) -> str:
    """key = app 版本 + commands 表 hash（不只是版本号）。"""
def parse_commands_json(raw: str, spec: "AppSpec") -> tuple["CommandSpec", ...]: ...  # 兼容 7/6 字段差异
async def discover(app_id: str, spec: "AppSpec", refresh: bool = False) -> tuple["CommandSpec", ...]:
    """lazy、可重入（_DISCOVERY_LOCKS）、带缓存（data_path()，key 含 commands_hash）。
       绝不在 startup 调用；仅由首次 search/run entry 触发。"""
async def search(query: str, app_id: str, top_k: int = 8) -> tuple["CommandSpec", ...]: ...
def load_cache(key: str, cache_dir: str) -> tuple["CommandSpec", ...] | None: ...
def save_cache(key: str, items: tuple["CommandSpec", ...], cache_dir: str) -> None: ...

# sequence.py
def normalize_command_id(cmd: str, spec: "AppSpec", target: Literal["engine","serve"]) -> str: ...
def is_readonly(cmd: str, spec: "AppSpec") -> bool: ...
def check_command_allowed(cmd: str, spec: "AppSpec", policy: "CommandPolicy", catalog_ids: frozenset[str]) -> None: ...
def plan_inspect(steps: list["Step"], spec: "AppSpec", enabled: bool) -> list["Step"]: ...
def build_argv(spec: "AppSpec", steps: list["Step"], source: "Source",
               out_file: str, roots: "RootsPolicy") -> list[str]: ...
ARGV_BUILDERS: dict[str, Callable[..., list[str]]] = {"run_cmd": ..., "tool_subcmd": ...}

# roots.py
def normalize_win_path(raw: str, base: str) -> str: ...   # 大小写/symlink/\\?\C:\ 归一化
def resolve_and_check(path: str, roots: tuple[str, ...],
                      direction: Literal["read","write"]) -> str: ...   # 违规 raise PolicyViolation
def build_root_flags(policy: "RootsPolicy") -> list[str]: ...
def default_policy(data_dir: str, cache_dir: str, cfg: dict) -> "RootsPolicy": ...

# runner.py（零第三方依赖）
@dataclass
class ProcHandle:
    job_id: str; proc: "asyncio.subprocess.Process"; started_at: float
@dataclass
class RawOutput:
    returncode: int; stdout_lines: tuple[str, ...]; stderr_text: str; truncated: bool
def build_subprocess_kwargs() -> dict[str, Any]: ...   # creationflags=CREATE_NO_WINDOW；无 shell 键
async def spawn(argv: list[str], cwd: str, env: dict[str,str], job_id: str,
                timeout: float = 60.0) -> ProcHandle: ...   # 单次控制调用显式 ≥60.0
async def stream_lines(h: ProcHandle, on_line: Callable[[str], None],
                       max_line_bytes: int, max_reply_bytes: int) -> RawOutput: ...
async def terminate(h: ProcHandle, grace_s: float = 2.0) -> None: ...   # terminate→宽限→kill
async def blocking(fn, *args): return await asyncio.to_thread(fn, *args)   # >5s 同步阻塞统一入口

# jobs.py
_PROC_HANDLES: dict[str, "ProcHandle"]; _JOBS: dict[str, "JobState"]; _LOCK: asyncio.Lock
def create_job(app_id: str, argv: list[str], is_edit_sequence: bool, transport: "Transport") -> "JobState": ...
async def execute(job: "JobState", deps: "Deps") -> "ExecResult": ...    # 由 entry handler 内 create_task 调
def get_job(job_id: str) -> "JobState | None": ...
async def cancel_job(job_id: str, ctx) -> "JobState":
    """transport in (CLI_SERVE, HTTP_BRIDGE) 且 cancel_method 非空 → 先显式发 ArtCraft 侧取消命令
       (jobs.cancel / serve cancel method，带 remote_job_id)，再 terminate 本地句柄；
       transport == CLI_RUN（v1 主通道，单进程无远端 job）→ terminate 本地 Popen 兜底。"""
async def shutdown_all() -> None: ...
def should_push(job: "JobState", now: float, new_pct: int) -> bool: ...
async def push_progress(job: "JobState", ctx) -> None: ...
async def report_completion(job: "JobState", result: "ExecResult", ctx) -> None:
    """终态：report_status(...)（base.py:481-484 → context.py:653-664）结构化回执
       + push_message(redact(完成摘要))（含 result_handle / preview_url）。"""
def persist_jobs(path: str) -> None: ... / def restore_jobs(path: str) -> None: ...

# result.py
def parse_exec_output(raw: "RawOutput", spec: "AppSpec", job: "JobState") -> "ExecResult": ...
def extract_inspect(steps: tuple[dict, ...]) -> dict | None: ...
def collect_warnings(stderr_text: str, spec: "AppSpec") -> tuple[str, ...]: ...
def compose_user_message(r: "ExecResult", lang: str) -> str: ...
def materialize(r: "ExecResult", policy: "SpillPolicy", ctx) -> dict[str, Any]:
    """把 ExecResult 裁剪成 ≤512 KiB 消息面回包（一等公民，不是事后兜底）：
       超限 → 完整结果落盘 data_path() 得 result_handle，回包只推句柄 + 摘要；
       预览 PNG → ctx.images.upload() 得 URL part，**禁止 inline base64**；
       回包文本一律经 redact()。"""

# errors.py
ERROR_CODES: dict[str, dict[str, str]]                  # code -> {zh, en, redact_required}
def map_cli_error(returncode: int, stderr: str, spec: "AppSpec") -> tuple[str, str]: ...
REDACT_FIELDS: tuple[str, ...] = ("token","authorization","control_token","cookie","api_key",
                                  "instance_json","control-token","auth.params.token","secret")
def redact(obj: Any) -> Any:
    """递归脱敏任意 dict/list/str；覆盖 4 处：错误消息、日志、@llm_tool output、push_message 文本。
       命中 REDACT_FIELDS 键 → 值替 '***'；文本中 64hex token → '***'。"""
def redact_text(s: str) -> str: ...
def safe_err(code: str, **kwargs) -> dict:
    """唯一对外错误出口：返回宿主 is_envelope 认的裸 dict（S16 起不再是 Err）。
       业务代码禁止直接 new SdkError —— 防 token 经 entry_executor.py:169-173/220-229 原样回包。"""

# config.py / i18n.py
def load_settings(plugin) -> "Settings": ...
def t(code: str, lang: str = "zh", **kwargs) -> str: ...

# bridge.py（v1 预留，不实现）
LOOPBACK_HOST: str = "127.0.0.1"                       # 字面量，禁 localhost（#9777）
def assert_no_localhost(url: str) -> None: ...
def build_http_client_no_proxy(): ...                  # httpx.AsyncClient(trust_env=False, timeout=60.0)
def read_token_user_only(instance_json: str) -> str: ...
class ControlChannel(Protocol):
    async def connect(self, port: int, token: str) -> None: ...
    async def call(self, method: str, params: dict, timeout: float = 60.0) -> dict: ...
    async def cancel(self, remote_job_id: str) -> None: ...   # 显式 jobs.cancel
    async def close(self) -> None: ...

# studio_channel.py（v1 预留）
class StudioCloudChannel(Protocol):
    async def create_session(self) -> dict: ...        # POST /v1/mcp/session/create
    async def close(self) -> None: ...
```

**lifecycle 收紧**：
```python
@lifecycle(id="startup", startup_failure="warn")
async def on_startup(plugin):
    jobs.restore_jobs(plugin.data_path("jobs.json"))   # 纯文件读 + 内存登记，容错
    jobs.init_handles()
    # 绝不做 commands --json / warm catalog：startup 走瞬时 asyncio.run(host.py:1327)，
    # 在此起的后台 task 返回即被取消(game_agent_minecraft/__init__.py:192-200)；
    # 成熟范式是首次 entry lazy-start(:204-214)。
@lifecycle(id="shutdown")
async def on_shutdown(plugin):
    await jobs.shutdown_all()                          # 遍历 _PROC_HANDLES 全部 terminate()
```

---

## 2. 关键数据结构（`spec.py`）

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional

class AppId(str, Enum):
    PHOTOCRAFT = "photocraft"; VECTORCRAFT = "vectorcraft"; CRAFT_ANYTHING = "craft_anything"

class Transport(str, Enum):
    CLI_RUN = "cli_run"           # v1 主通道：一次性无头 run（单进程，无远端 job）
    CLI_SERVE = "cli_serve"       # 预留：stdio/端口常驻（有 jobs.cancel）
    HTTP_BRIDGE = "http_bridge"   # 预留：回环控制通道（有 jobs.cancel）
    STUDIO_CLOUD = "studio_cloud" # 预留：Studio 云端生成（不读写本地场景）

class JobStatus(str, Enum):
    QUEUED="queued"; RUNNING="running"; SUCCEEDED="succeeded"
    FAILED="failed"; CANCELLED="cancelled"; CRASHED="crashed"

class RunMode(str, Enum):
    HEADLESS = "headless"; BRIDGE = "bridge"

@dataclass(frozen=True)
class AppSpec:
    app_id: str
    display_name: str
    version: str
    cli_path: str = ""                     # locator.resolve_cli 运行时填充（绝对路径）
    cli_candidates: tuple[str, ...] = ()
    install_root: str = ""
    input_mode: Literal["positional","flag_in","flag_project"] = "positional"
    input_flag: str = ""                   # ""=位置参数 FILE；否则 "--in"/"--project"
    new_flag: str = "--new"
    output_flag: str = "--out"
    output_repeatable: bool = False        # VectorCraft --export 可多次
    cmd_flag: str = "--cmd"
    params_flag: str = "--params"
    commands_flag: str = ""                # VectorCraft "--commands"
    commands_subcmd: str = "commands"      # PdfCraft="tools"
    commands_json_flag: str = "--json"
    filter_flag: str = "--filter"
    has_disabled_reason: bool = True
    has_needs_document: bool = True
    has_serve: bool = True                 # VectorCraft 无 serve
    serve_namespace: str = "doc"           # serve method 用 doc.*
    engine_namespace: str = "document"     # run --cmd 用 document.*
    control_port: int = 0                  # 0=动态(instance.json)；VectorCraft=7979
    instance_json: str = ""
    transport: Transport = Transport.CLI_RUN
    builder: str = "run_cmd"
    readonly_prefixes: tuple[str, ...] = ("document.inspect","session.list","jobs.list","command.list")
    newline_json: bool = True
    flatten_warning_substr: str = "flattened; layers, masks and blend modes are not kept"

@dataclass(frozen=True)
class CommandSpec:
    id: str
    label: str = ""
    menu: tuple[str, ...] = ()
    params_schema: str = ""                 # 人类可读 schema 字符串，原样喂 LLM（非 JSON Schema）
    enabled: bool = True
    disabled_reason: Optional[str] = None   # VectorCraft 无此字段 → None
    needs_document: Optional[bool] = None
    shortcut: str = ""
    readonly: bool = False

@dataclass(frozen=True)
class RootsPolicy:
    read_roots: tuple[str, ...]             # 已 long_path() 展开的绝对路径
    write_roots: tuple[str, ...]
    mode: RunMode = RunMode.HEADLESS
    read_flag: str = "--automation-read-root"
    write_flag: str = "--automation-write-root"
    allow_user_dirs: bool = False           # 用户显式目录需配置批准、重启生效

@dataclass
class JobState:
    job_id: str
    app_id: str
    status: JobStatus = JobStatus.QUEUED
    argv: tuple[str, ...] = ()
    cwd: str = ""
    created_at: float = 0.0
    started_at: float = 0.0
    updated_at: float = 0.0
    finished_at: float = 0.0
    progress: float = 0.0
    progress_label: str = ""
    pid: int = 0
    exit_code: Optional[int] = None
    stdout_lines: int = 0
    last_push_at: float = 0.0
    last_progress_pct: int = -1
    cancel_requested: bool = False
    is_edit_sequence: bool = False         # 含任一编辑命令 → 整体编辑类，绝不重发
    retried_readonly: bool = False
    error_code: str = ""
    error_message: str = ""
    result_path: str = ""
    warnings: tuple[str, ...] = ()
    attempts: int = 0
    transport: Transport = Transport.CLI_RUN
    remote_job_id: str = ""                # serve/bridge 的 ArtCraft 侧 job id
    cancel_method: str = ""                # 如 "jobs.cancel"；空=主通道无远端取消命令

@dataclass
class ExecResult:
    ok: bool
    app_id: str
    job_id: str = ""
    exit_code: int = 0
    steps: tuple[dict[str, Any], ...] = ()
    inspect: Optional[dict[str, Any]] = None  # document.inspect 完整结构（LLM 的眼睛）
    warnings: tuple[str, ...] = ()
    output_files: tuple[str, ...] = ()
    error_code: str = ""
    error_message: str = ""
    user_message: str = ""
    elapsed_ms: int = 0
    result_handle: str = ""                   # 落盘 data_path() 的完整结果文件路径
    preview_url: str = ""                     # ctx.images.upload() 返回的 URL part（禁 inline base64）
    payload_bytes: int = 0
    spilled: bool = False

MESSAGE_PLANE_MAX_BYTES: int = 512 * 1024    # plugin/settings.py:610
@dataclass(frozen=True)
class SpillPolicy:
    max_bytes: int = 512 * 1024
    spill_dir: str = ""                       # plugin.data_path()
    preview_via_upload: bool = True
    inline_base64_forbidden: bool = True
    redact_output: bool = True

@dataclass(frozen=True)
class Step:
    cmd: str
    params: dict[str, Any] = field(default_factory=dict)
@dataclass(frozen=True)
class Source:
    input_file: str = ""
    new_doc: Optional[dict[str, Any]] = None
@dataclass(frozen=True)
class CommandPolicy:
    deny_exact: tuple[str, ...] = ()
    deny_prefixes: tuple[str, ...] = ("prefs.","plugin.","updates.")
    require_registry_match: bool = True
    require_confirm_on_write: bool = False
    max_steps: int = 256
```

---

## 3. per-app adapter 差异表

| 维度 | PhotoCraft 0.21.0 | VectorCraft 0.22.0 | Craft Anything 0.5.0 |
|---|---|---|---|
| CLI 实测兜底 | `E:\Program Files\Artcraft\PhotoCraft\photocraft-cli.exe` | `E:\Program Files\Artcraft\VectorCraft\vectorcraft-cli.exe` | `E:\Program Files\Artcraft\Craft Anything\craft-anything-cli.exe` |
| `input_mode`/`input_flag` | `positional`/`""` | `flag_in`/`--in` | `flag_project`/`--project` |
| `new_flag` | `--new` | `--new` | 无（`""`） |
| `output_flag` | `--out` | `--export`（`output_repeatable=True`） | 无（tool 直接产出 + `--format`） |
| `commands_subcmd` | `commands` | `commands` | `commands` |
| `commands --json` 字段 | 7（含 disabledReason/needsDocument） | 6 → `has_*=False` | [INFERENCE] `False/False` |
| `has_serve` | True | **False** | [INFERENCE] False |
| `control_port` | 0（动态 instance.json） | 7979 | 0 |
| `builder` | `run_cmd` | `run_cmd` | **`tool_subcmd`** |
| 命名空间 | engine `document.*` / serve `doc.*` | 同左 [INFERENCE] | 未核验 |
| 附加能力 | convert/info/batch/droplet/mcp/serve | convert/info/bench/mcp/render/commands | capabilities/triage/report/chat/rebuild/ai |

工具名分叉（RFC-0012 仍在 rollout，**禁止写死**）：PhotoCraft `command_list`/`run_command`、VectorCraft `run_command`/`command_run`、WordCraft `execute`/`batch`、PdfCraft `tools` 表 snake_case。命令数与工具名一律动态发现，缓存 key 带 app 版本 + `commands` 表 hash。本机 993/933 是注册表总条目，README「500+」过时、`docs/target-app-parity.md` 631/631 是菜单 catalog 项 —— 三种口径不混用。

差异表放**状态根 `config\plugin.toml`**（`await self.config.dump()` 读），`BUILTIN_SPECS` 作零配置兜底：

```python
BUILTIN_SPECS: dict[str, AppSpec] = {
  "photocraft": AppSpec(app_id="photocraft", display_name="PhotoCraft", version="0.21.0",
      cli_candidates=(r"E:\Program Files\Artcraft\PhotoCraft\photocraft-cli.exe",),
      input_mode="positional", input_flag="", new_flag="--new", output_flag="--out",
      has_disabled_reason=True, has_needs_document=True, has_serve=True, control_port=0,
      instance_json=r"%APPDATA%\Photocraft\instance.json", builder="run_cmd"),
  "vectorcraft": AppSpec(app_id="vectorcraft", display_name="VectorCraft", version="0.22.0",
      cli_candidates=(r"E:\Program Files\Artcraft\VectorCraft\vectorcraft-cli.exe",),
      input_mode="flag_in", input_flag="--in", new_flag="--new", output_flag="--export",
      output_repeatable=True, commands_flag="--commands",
      has_disabled_reason=False, has_needs_document=False, has_serve=False,
      control_port=7979, builder="run_cmd"),
  "craft_anything": AppSpec(app_id="craft_anything", display_name="Craft Anything", version="0.5.0",
      cli_candidates=(r"E:\Program Files\Artcraft\Craft Anything\craft-anything-cli.exe",),
      input_mode="flag_project", input_flag="--project", new_flag="", output_flag="",
      commands_json_flag="--json", has_disabled_reason=False, has_needs_document=False,
      has_serve=False, builder="tool_subcmd"),
}
```

`merge_specs()` 语义：内置 3 个必存在（兜底）→ 配置段按 `app_id` 覆盖字段 → 配置里新 `app_id` 用 `AppSpec` 默认值 + 配置字段构造。**新增一个 app 只需**在运行时配置加一段 `[artcraft.apps.<id>]`（约 3 行）；只有 argv 形态全新才需在 `sequence.ARGV_BUILDERS` 注册 builder（多数复用 `run_cmd`，无需改代码）。

---

## 4. 时序图（文字化）

```
[0] LLM 意图解析：app=photocraft；input_file=当前图。
    失败：路径空 → PATH_OUTSIDE_ROOTS；app 未知 → APP_NOT_FOUND。
[1] @llm_tool artcraft_ops_search_commands(query="调亮 brightness levels", app, top_k=8)
    lazy discovery 触发点：_DISCOVERY_LOCKS[app] 保证并发只 spawn 一次；startup 绝不做。
    查缓存 key=(app, version_token, commands_hash)；未命中 → resolve_cli（§1 顺序）
      → runner.spawn([cli,"commands","--json","--filter","bright"]) → parse → save_cache(data_path())；
      命令表 hash 用 stdout 的 sha256。
    超时：单次 control 调用 timeout=60.0（对齐官方 55s 客户端下限）；CLI 冷启实测 0.294s。
      >5s 同步阻塞（文件 hash）一律 await asyncio.to_thread(...)。
    失败：CLI 不存在→APP_NOT_FOUND；filter 命中 0→回退全量本地子串匹配；
      超时→降级旧缓存（hint 标 stale）；仍无→NO_COMMAND_MATCH。
    返回 8 条 {id,label,params_schema,menu,needs_document}（不含 993 全量），经 redact()。
    进度点①：默认不 push；仅 refresh=true 推「命令表已刷新」。
[2] LLM 依据 params_schema 生成 steps。[INFERENCE] 亮度类命令 id 未验证，运行时由 [1] 决定；
    已验证示例：edit.fill / layer.new.layer / document.inspect / filter.sharpen.unsharpMask。
[3] @llm_tool artcraft_ops_run_sequence(...)
    参数软校验 → roots.resolve_and_check(input,"read")/(out,"write") →
      normalize_command_id(cmd,spec,"engine")（doc.*→document.*）→ check_command_allowed →
      plan_inspect 末尾追加 document.inspect → build_argv（含 --automation-*-root，路径经 long_path()）
      → create_job(QUEUED) → **在 entry handler 内** asyncio.create_task(jobs.execute(job,deps))
      → 立即返回 {job_id,status:"queued",poll_with:"artcraft_ops_status",cancel_with:"artcraft_ops_cancel"}
      （@plugin_entry(id=..., timeout=10.0)，<10s）。
    失败：任一校验失败 → 同步 safe_err(CODE)，不建 job、不 spawn。
[4] jobs.execute：QUEUED→RUNNING；runner.spawn(argv, 绝对 cwd, 继承 env, CREATE_NO_WINDOW,
      shell=False, timeout=60.0) → ProcHandle 登记 _PROC_HANDLES（_LOCK）。
    进度点②：RUNNING 必推一条 push_message(redact(摘要))（≤512 KiB）。
    逐行读 stdout → steps 追加、progress、updated_at。
    进度点③：状态变化 或 (整数百分比跨 10 的倍数 且 now-last_push_at≥2.0s)。
    整体超时：asyncio.wait_for(..., timeout=60.0)；@llm_tool 外层默认 30s / 硬上限 300s
      → 长任务靠 *_status 轮询，不一次等到底。超时 → cancel → runner.terminate（terminate→2s→kill）。
    只读断线重试：仅 transport∈(CLI_SERVE,HTTP_BRIDGE) 且 is_edit_sequence=False 且 retried_readonly=False
      → 空闲 25s 先重连再发；只读工具断线重试一次；编辑类绝不重发。v1 CLI_RUN 不触发。
    失败：exit 2 → 整批不保存 output_files=()；exit 1 → CLI_TASK_FAILED；
      stderr 拍平警告 → warnings（非错误）。
[5] result.parse_exec_output → ExecResult；**result.materialize() 在组装回包时即执行**：
      payload_bytes>512 KiB → 落盘 data_path() 得 result_handle，spilled=True，
      回包只推句柄 + 摘要（避免 context.py:1203-1210 整条丢弃连文本一起）；
      预览 PNG → ctx.images.upload() 得 preview_url，**禁止 inline base64**；回包文本 redact()。
      单行>1 MiB 跳过记 warning；累计>8 MiB 截断 + LIMIT_REPLY。
    进度点④：jobs.report_completion() 用 report_status(...) 结构化回执
      + push_message(redact(完成摘要))（含 result_handle / preview_url / 拍平警告原文）。
[6] ctx.images.upload() **只在结果阶段调，绝不在 lifecycle 调** [FACT]。
    YUI 回用户：摘要 + result_handle + preview_url + 「warning: 3 layer(s) flattened; ...」原文。
[7] @plugin_entry(id="artcraft_ops_status"|"artcraft_ops_cancel", timeout=15.0) 轮询/取消；
    cancel_job：serve/bridge → 显式发 jobs.cancel(remote_job_id) 再 terminate；
    CLI_RUN → terminate 本地 Popen（主通道无远端 job）。job TTL=3600s。
```

超时汇总：catalog/单次 control ≥`timeout=60.0`；入口 10.0 秒回；status/cancel 15.0；`@llm_tool` 默认 30s / 硬上限 300s；>5s 同步阻塞 `await asyncio.to_thread(...)`；[预留] bridge 55s 放弃（app 60s）、空闲 25s 重连、serve 30 分钟空闲退出。

---

## 5. 两级 LLM 工具（手写 JSON Schema）

两级核心 `search_commands` + `run_sequence`，3 个薄配套 `status` / `cancel` / `list_apps`。993 条命令绝不逐条暴露。入口名用 `*_status` / `*_cancel`。

```python
SEARCH_SCHEMA = {
  "type": "object",
  "properties": {
    "query": {"type": "string", "description": "自然语言或关键词，如 调亮 / sharpen / 新建图层；服务端按此过滤命令注册表"},
    "app":   {"type": "string", "enum": ["photocraft","vectorcraft","craft_anything"],
              "description": "目标 app；缺省取运行时配置 default_app（默认 photocraft）"},
    "top_k": {"type": "integer", "minimum": 1, "maximum": 30, "description": "默认 8"}
  },
  "required": ["query"]
}
RUN_SCHEMA = {
  "type": "object",
  "properties": {
    "app": {"type": "string", "enum": ["photocraft","vectorcraft","craft_anything"]},
    "steps": {"type": "array", "maxItems": 256,
      "description": "按序执行的命令；cmd 用引擎命令名（document.* 而非 doc.*）；params 依据 params_schema 填",
      "items": {"type": "object", "properties": {"cmd": {"type": "string"}, "params": {"type": "object"}},
                "required": ["cmd"]}},
    "input_file": {"type": "string", "description": "绝对路径；须在已授权 read root；与 new_doc 二选一"},
    "new_doc":    {"type": "object", "description": "如 {width:640,height:400,background:'white'}"},
    "out_file":   {"type": "string", "description": "输出文件名或绝对路径；写入 write root"},
    "inspect":    {"type": "boolean", "description": "序列末尾自动追加 document.inspect，默认 true"}
  },
  "required": ["steps"]
}
STATUS_SCHEMA = {"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"]}
CANCEL_SCHEMA = {"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"]}
LIST_APPS_SCHEMA = {"type":"object","properties":{}}
```

返回契约（一律经 `redact()`）：`search_commands`→`{commands:[{id,label,params_schema,menu,needs_document}],app,version,total_matched,hint}`；`run_sequence`→`{job_id,status:"queued",poll_with:"artcraft_ops_status",cancel_with:"artcraft_ops_cancel"}`；`status`→`{job_id,status,progress,progress_label,result:<精简 ExecResult 含 result_handle/preview_url>,error_code,error_message}`；`cancel`→`{job_id,status}`；`list_apps`→`{apps:[{app_id,display_name,version,cli_path_found}]}`。

**params 人类可读 schema** 原样返回（`{"amount":1..500=50,"radius":0.1..1000=1}`，非 JSON Schema）；`run_sequence` 按 range/enum/default 做软校验，不合法 → `safe_err(POLICY_VIOLATION)`，绝不拼进 argv。

**防注入**：① `asyncio.create_subprocess_exec(*argv)`（argv list、shell=False、`build_subprocess_kwargs()` 无 `shell` 键）；② 文件名只作单个独立 argv 元素，`params` 用 `json.dumps` 成单个 `--params` 元素；③ 文件名经 `resolve_and_check()`；④ 命令 id 经 `normalize_command_id` + `require_registry_match=True`；⑤ `Any=None` 防御式参数 + `**_`（照抄 `game_agent_minecraft/__init__.py:341-360`）。

双注册（叠放顺序 [INFERENCE] 云雨 stub-SDK 核验）：
```python
@plugin_entry(id="artcraft_ops_search_commands", timeout=60.0, llm_result_fields=[...])
@llm_tool(name="artcraft_ops_search_commands", parameters=SEARCH_SCHEMA, description="当用户想对图片/矢量/文档执行某操作但不知道命令名时，先按关键词检索命令与参数")
async def artcraft_ops_search_commands(query: Any=None, app: Any=None, top_k: Any=None, **_) -> Ok|Err: ...

@plugin_entry(id="artcraft_ops_run_sequence", timeout=10.0, llm_result_fields=[...])
@llm_tool(name="artcraft_ops_run_sequence", parameters=RUN_SCHEMA, description="当用户要在 ArtCraft 软件里执行一串编辑/导出命令时使用；秒回 job_id，用 status 查结果、cancel 取消")
async def artcraft_ops_run_sequence(app:Any=None, steps:Any=None, input_file:Any=None,
        new_doc:Any=None, out_file:Any=None, inspect:Any=None, **_) -> Ok|Err:
    ...  # asyncio.create_task(jobs.execute(...)) 必须在本 handler 内

@plugin_entry(id="artcraft_ops_status", timeout=15.0, llm_result_fields=[...])
@llm_tool(name="artcraft_ops_status", parameters=STATUS_SCHEMA, description="查询长任务状态、进度与结果句柄")
async def artcraft_ops_status(job_id: Any=None, **_) -> Ok|Err: ...

@plugin_entry(id="artcraft_ops_cancel", timeout=15.0, llm_result_fields=[...])
@llm_tool(name="artcraft_ops_cancel", parameters=CANCEL_SCHEMA, description="取消任务：显式转发 ArtCraft 侧取消并终止子进程")
async def artcraft_ops_cancel(job_id: Any=None, **_) -> Ok|Err: ...

@plugin_entry(id="artcraft_ops_list_apps", timeout=10.0, llm_result_fields=[...])
@llm_tool(name="artcraft_ops_list_apps", parameters=LIST_APPS_SCHEMA, description="列出本机已探测到的 ArtCraft App 与版本")
async def artcraft_ops_list_apps(**_) -> Ok|Err: ...
```

---

## 6. 安全边界

### 三个宿主侧安全放大点（显眼位置）
1. **脱敏 4 处**：`Err(SdkError(...))` message 原样塞进回包（`entry_executor.py:169-173,220-229`）、`push_message` 注入对话/HUD、`@llm_tool` output 回模型、日志 → **token / 控制文件内容绝不能进这四处**。唯一对外口 `errors.safe_err()` + `errors.redact()`。
2. **句柄回收**：超时只 cancel 协程不杀子进程（`entry_executor.py:134-155`）→ 无认证端口（VectorCraft 控制端口完全无认证）被遗留。存句柄，shutdown 与取消入口 `terminate()`，按需拉起用完即关（`maa_end/controlapi.py:118-160,170-178`）。
3. **插件侧白名单**：`permissions` 只是 UI 声明（`ui_manifest.py:177-199`），宿主不做工具级授权 → 与「token 不是授权边界」合并，工具/命令白名单必须插件侧实现。出站一律 `httpx.AsyncClient(trust_env=False)` + 字面 `127.0.0.1`（代理热切换 `config_router/proxy.py:82-101`）。

### roots 纯文本比对（不用「归一化」四个字带过）
`default_policy()` 只授权 `data\` 与 `cache\`；用户显式目录写进 `[artcraft.security] user_read_roots/user_write_roots` 且**重启生效**。headless 拼 `--automation-read-root/--automation-write-root` 到 CLI；bridge 模式 roots 配在**桌面启动命令** `photocraft --control ... --automation-*-root`，**不**往 `mcp --bridge` 传（usage error exit 2）；`PHOTOCRAFT_AUTOMATION_*` 只作用桌面进程 [FACT 链 1]。

官方原文（`photocraft.im/guides/mcp-cli#file-roots-what-the-assistant-can-touch`）：「the part of the request below the root is **never resolved on disk**」—— **逐段纯文本比对**，请求路径片段根本不落盘解析。因此 8.3 短名即使指向合法目录也拒（拒绝列表含 `a.bin:hidden` 等 ADS 与「other spellings such as 8.3 short names」），`..`、设备名（CON）、越界 symlink 同拒。本机实证后端日志有 `E:\PROGRA~2\Neko\RESOUR~1\bin\...`、插件 cwd=`sys._MEIPASS` → 路径只从 `os.environ["LOCALAPPDATA"]` 或 `self.data_path().resolve()` 派生，转 `C:/...` 正斜杠，必要时 `GetLongPathNameW`；**别拼 `sys.executable`/`os.getcwd()`**。**但 v0.18.0 起 `\\?\C:\…` / `\\?\UNC\…` 被接受**（原文「match the ordinary drive and UNC spellings」）→ `locator.long_path()` 展开成 `\\?\` 前缀是安全落地方式。

### 命令策略 [VALUE JUDGMENT]
不一刀切 `file.*`（`file.new` 合法，全禁误伤）[FACT file=59 条]；`deny_exact`（退出/关闭类，运行时按 label 含 quit/exit 动态加入 [INFERENCE]）；`deny_prefixes=("prefs.","plugin.","updates.")`；`require_registry_match=True`；roots 兜底；`require_confirm_on_write=False`（可配置开启）。

### token / #9777 [FACT 硬约束]
`bridge.py` v1 `raise NotImplementedError("BRIDGE_NOT_IMPLEMENTED: ...")`，常量/校验先落地：
```python
LOOPBACK_HOST = "127.0.0.1"                     # 绝不 localhost（localhost→[::1]，app 只绑 127.0.0.1）
def assert_no_localhost(url: str) -> None:
    assert "localhost" not in url.lower() and url.startswith(f"http://{LOOPBACK_HOST}:")
def build_http_client_no_proxy():
    import httpx; return httpx.AsyncClient(trust_env=False, timeout=60.0)
    # v1 主通道用 asyncio.open_connection / urllib + ProxyHandler({})，不依赖 httpx
def read_token_user_only(instance_json: str) -> str: ...
```
其余：bridge 前先 `asyncio.open_connection` 探活再送 token；token 文件用户独占；端口不长期常开；token **不当授权边界**（官方 `book/src/automation/security-model.md`），仍走 `check_command_allowed`。首帧 auth `{"method":"auth","params":{"token":"<64hex>"}}` 明文，修复 PR #9778 仍 Open → **v1 不启用 bridge**。

### 边界参数与降级
`[artcraft.limits]` max_steps=256 / 1 MiB 请求行 / 8 MiB 回复 / 16 活跃 TCP / 预览 2048px·5 MiB PNG / serve_idle 1800s / message_plane 512 KiB / control_call 60.0s。steps>256 → `safe_err(LIMIT_STEPS)` 不 spawn；单行>1 MiB 跳过记 warning；累计>8 MiB 截断 + `LIMIT_REPLY`；**消息面>512 KiB → `materialize()` 落盘只推句柄**（一等公民，不是事后兜底）。

### Studio 拆分 [FACT]
`studio_channel.py` 只留 `create_session()` 协议接口（`POST /v1/mcp/session/create` + 第三方 `artcraft-mcp` 的 API key / `%LOCALAPPDATA%\ai.artcraft.app\.cookies`），v1 不实现；本地 GUI L4，v1 不做 UIAutomation。两条通道不抽象成同一个 app 控制面（issue #2003：Scene state can't be read back）。**形态硬约束**：不能做成 PhotoCraft 内嵌 WASM 插件（ABI v1 沙箱禁 fs/网络导入、不支持面板/UI）→ 只能外部子进程 + 回环控制通道。

---

## 7. 长任务状态机

状态 `QUEUED|RUNNING|SUCCEEDED|FAILED|CANCELLED|CRASHED`。转换：
```
QUEUED → RUNNING     : jobs.execute 起来、runner.spawn 成功
QUEUED → CANCELLED   : *_cancel 在 spawn 前到达
RUNNING → SUCCEEDED  : returncode==0 且解析成功
RUNNING → FAILED     : returncode∈{1,2} / 解析失败 / policy 违规
RUNNING → CANCELLED  : *_cancel → 显式转发 ArtCraft 侧取消 → terminate 子进程
RUNNING → CRASHED    : 进程非正常退出 / OSError / worker 崩溃
CRASHED → FAILED     : 编辑类任务（不重发，提示核对 document.inspect）
CRASHED → RUNNING    : 仅只读任务且 retried_readonly==False → 重连重发一次
终态不再转换；job TTL=3600s [VALUE JUDGMENT]
```

**取消语义（核心修正）**：宿主 `CANCEL_RUN` 只 `task.cancel()`（`host.py:1627-1632`），不代调 ArtCraft cancel。
```python
async def cancel_job(job_id: str, ctx) -> JobState:
    job = get_job(job_id) or raise safe_err(JOB_NOT_FOUND)
    job.cancel_requested = True
    if job.transport in (Transport.CLI_SERVE, Transport.HTTP_BRIDGE) and job.cancel_method:
        await deps.channel.cancel(remote_job_id=job.remote_job_id)   # jobs.cancel / serve cancel method
    await runner.terminate(_PROC_HANDLES[job_id])                     # 本地句柄回收
    job.status = JobStatus.CANCELLED
    await report_completion(job, ExecResult(ok=False, app_id=job.app_id,
                            error_code="JOB_CANCELLED", error_message=redact_text(t("JOB_CANCELLED"))), ctx)
    return job
```

超时 `asyncio.wait_for(jobs.execute(...), timeout=60.0)` → cancel → `runner.terminate()`（terminate→2s→kill）。`persist_jobs(self.data_path("jobs.json"))`；`restore_jobs()` 把「RUNNING 但 pid 不存在」标 CRASHED；编辑类不自动重发。`_PROC_HANDLES` + `asyncio.Lock`，`execute` 的 `finally` pop；shutdown 遍历 terminate。阻塞调用统一 `await asyncio.to_thread(...)`；`CREATE_NO_WINDOW`。

进度节流：
```python
PUSH_MIN_INTERVAL_S = 2.0
def should_push(job, now, new_pct) -> bool:
    if job.status changed: return True
    crossed_ten = (new_pct // 10) > (job.last_progress_pct // 10)
    return crossed_ten and (now - job.last_push_at) >= PUSH_MIN_INTERVAL_S
```

编辑类绝不重发：`is_edit_sequence = any(not is_readonly(cmd) for cmd in steps)`；断线/恢复分支开头硬判断，含任一编辑命令即整体判编辑类。只读工具清单 `ui_inspect`/`doc_inspect`/`command_list`/`jobs_list`/`session_list`/`ui_screenshot`。[FACT 链 1] v0.21.0 `#8132/#8134` 等待式命令改 job，需 `jobs_list` 轮询 + 进度 + 取消 —— v1 CLI_RUN 不触发，仅预留通道生效。

---

## 8. 错误码表

`safe_err()` 返回宿主 `is_envelope` 认的**错误 envelope**（`success=False` + `error.code` + `time`，见 §19.15），`error.code` 原样落 `RunError.code`，**不再**被抹平为 `INTERNAL`。🔴 = **全部 CODE 的对外文案、日志、`@llm_tool` output、`push_message` 文本必须经 `redact()`，禁止出现 token / 控制文件内容 / instance.json 内容**；业务代码禁止直接 `new SdkError`。

| CODE | 用户文案 (zh) |
|---|---|
| `APP_NOT_FOUND` | 未找到 <app> 的命令行程序，请确认已安装 |
| `APP_VERSION_FAILED` | 无法读取 <app> 版本，命令缓存已禁用 |
| `COMMANDS_DISCOVERY_FAILED` | 命令清单读取失败，请重试 |
| `NO_COMMAND_MATCH` | 没有找到与「<query>」匹配的命令，换个说法试试 |
| `POLICY_VIOLATION` | 该操作被安全策略拒绝：<原因> |
| `PATH_OUTSIDE_ROOTS` | 文件不在已授权目录内：<path> |
| `PATH_UNSAFE` | 路径不安全，已拒绝 |
| `NAMESPACE_ERROR` | 命令名已自动从 doc.* 归一化为 document.* |
| `LIMIT_STEPS` | 命令序列超过 256 步上限，请拆分 |
| `LIMIT_REPLY` | 输出过大已截断（完整结果见句柄） |
| `JOB_NOT_FOUND` | 任务不存在或已过期 |
| `JOB_TIMEOUT` | 任务超时（<n>s），已终止子进程 |
| `JOB_CANCELLED` | 任务已取消（已转发 ArtCraft 侧取消） |
| `JOB_CRASHED` | 子进程异常退出，编辑类任务不会自动重发，请用检视命令核对文档状态 |
| `CLI_USAGE_ERROR` | 命令用法错误：<stderr 摘要>；整批未保存任何产物 |
| `CLI_TASK_FAILED` | 命令执行失败：<stderr 摘要> |
| `ROOTS_NOT_GRANTED` | 文件系统访问未授权：缺少 read/write 权限（headless 必须显式传 root 参数） |
| `BRIDGE_NOT_IMPLEMENTED` | 当前版本未启用回环控制通道 |
| `STUDIO_NOT_IMPLEMENTED` | 当前版本不控制 ArtCraft Studio 本地 GUI；云端生成通道未实现 |

`redact()` 覆盖字段：`REDACT_FIELDS = (token, authorization, control_token, cookie, api_key, instance_json, control-token, auth.params.token, secret)`；命中键 → 值替 `'***'`；`redact_text(s)` 正则替换 `\b[0-9a-fA-F]{64}\b`。四个强制调用点：`safe_err()`、`runner.build_cli_env()` 日志副本、`@llm_tool` 返回 `Ok(...)` 前、`jobs.push_progress()/report_completion()` 的 `push_message` 文本。

CLI 侧映射（`map_cli_error`）：exit 0 → Ok；exit 1 → `CLI_TASK_FAILED`（stderr 经 redact）；exit 2 → `CLI_USAGE_ERROR`（整批未保存）；stderr `unknown command ... did you mean document.inspect` → `NAMESPACE_ERROR`（先归一化）；`automation filesystem access is not granted` / `read authority is absent` → `ROOTS_NOT_GRANTED`；`flattened; layers, masks and blend modes are not kept` → **非错误，进 warnings，原样透传英文**；stdout 单行 >1 MiB → warning；`--cmd` id 未命中 catalog → `POLICY_VIOLATION`。

i18n：`ERROR_CODES[code] = {zh, en, redact_required}`。拍平警告原文不翻译、不截断 [FACT]。

---

## 9. 三个配置文件（§0 裁决后定案）

**A. 清单 `plugin.toml`（代码根，含 `[plugin]`）** — 字段待云雨 stub-SDK 核验，见 §12：
```toml
[plugin]
id = "artcraft_ops"                        # 目录名必须 == 此 id [FACT]
name = "ArtCraft 全家桶操控"
version = "1.0.0"
type = "plugin"
description = "用一句话让 YUI 操控本机 PhotoCraft / VectorCraft / Craft Anything：检索命令、编排序列、无头执行并回传图片；其余 App 以 adapter 插槽预留。"
short_description = "操控 ArtCraft 系列软件"
keywords = ["修图","调亮","图层","导出","锐化","PhotoCraft","VectorCraft","Craft Anything","ArtCraft"]
author = "neko-artcraft-ops"

[plugin.sdk]
min_version = "0.9.1"                      # [INFERENCE] 待核验

[plugin.store]
enabled = true

# [[plugin.entries]] 与 @plugin_entry 一一对应（漏写 refresh 后面板 404 [FACT]）
[[plugin.entries]]
id = "artcraft_ops_search_commands"
description = "按关键词检索指定 App 的命令注册表，返回命令 id 与人类可读参数 schema"
timeout = 60.0
llm_result_fields = ["commands","app","version","total_matched","hint"]

[[plugin.entries]]
id = "artcraft_ops_run_sequence"
description = "在指定 App 执行一串命令；秒回 job_id，用 status 查、cancel 取消"
timeout = 10.0
llm_result_fields = ["job_id","status","poll_with","cancel_with"]

[[plugin.entries]]
id = "artcraft_ops_status"
description = "查询长任务状态、进度与结果句柄"
timeout = 15.0
llm_result_fields = ["job_id","status","progress","progress_label","result","error_code","error_message"]

[[plugin.entries]]
id = "artcraft_ops_cancel"
description = "取消任务：显式转发 ArtCraft 侧取消并终止子进程"
timeout = 15.0
llm_result_fields = ["job_id","status"]

[[plugin.entries]]
id = "artcraft_ops_list_apps"
description = "列出本机已探测到的 ArtCraft App 与版本"
timeout = 10.0
llm_result_fields = ["apps"]

[plugin_runtime]
timeout = 30                               # [FACT] startup 超时，不是入口超时
# 无 vendor：零第三方依赖（asyncio 标准库），规避 dist-info 拒载 / cp311-win_amd64
```
**adapter 差异表 / roots / limits / i18n 不放本清单**，放 B。

**B. 运行时配置**：状态根 `C:\Users\54301\AppData\Local\N.E.K.O\plugins\artcraft_ops\config\plugin.toml`（**不含 `[plugin]`**，`await self.config.dump()` 读）。宿主首次安装时把 C 逐字节复制过来 [FACT SKILL.md:111-113]。

**C. `config.example.toml`（代码根，模板，不含 `[plugin]`）**：
```toml
# 复制到 C:\Users\54301\AppData\Local\N.E.K.O\plugins\artcraft_ops\config\plugin.toml
# 严禁出现 [plugin] 段（宿主保留）；由 await self.config.dump() 读取；不放进程 env。
[plugin_runtime]
timeout = 30

[artcraft.apps]
default = "photocraft"

[artcraft.apps.photocraft]
cli_path = ""            # 留空=走 locator 定位顺序；填绝对路径=最高优先覆盖
version = "0.21.0"
input_mode = "positional"
output_flag = "--out"
commands_subcmd = "commands"
has_disabled_reason = true
has_needs_document = true
has_serve = true
builder = "run_cmd"

[artcraft.apps.vectorcraft]
cli_path = ""
version = "0.22.0"
input_mode = "flag_in"
input_flag = "--in"
output_flag = "--export"
output_repeatable = true
commands_flag = "--commands"
has_disabled_reason = false
has_needs_document = false
has_serve = false
control_port = 7979
builder = "run_cmd"

[artcraft.apps.craft_anything]
cli_path = ""
version = "0.5.0"
input_mode = "flag_project"
input_flag = "--project"
builder = "tool_subcmd"

[artcraft.security]
user_read_roots = []
user_write_roots = []
deny_prefixes = ["prefs.","plugin.","updates."]
require_registry_match = true
require_confirm_on_write = false
token_file_acl_check = true
loopback_host = "127.0.0.1"
redact_fields = ["token","authorization","control_token","cookie","api_key","instance_json","control-token","auth.params.token","secret"]

[artcraft.limits]
max_steps = 256
max_request_line_bytes = 1048576
max_reply_bytes = 8388608
max_active_tcp = 16
preview_max_px = 2048
preview_max_png_bytes = 5242880
serve_idle_timeout_s = 1800
message_plane_max_bytes = 524288
control_call_timeout_s = 60.0
push_min_interval_s = 2.0
job_ttl_s = 3600

[artcraft.i18n]
default_lang = "zh"
supported = ["zh","en"]
passthrough_warnings = ["flattened; layers, masks and blend modes are not kept"]
```

**两套安装说明 [FACT 口径分裂]**：
- **稳妥路径（推荐）**：整个 `artcraft_ops\` 放入代码根 `...\.neko-plugin-installations\plugins\artcraft_ops\`（目录名==id），宿主 refresh 即加载。
- **路径 A（安装版 exe，2026-10-31 构建、早于 #3638）**：`<runtime root>/plugin-development.json` = `{"enabled":true,"registrations":[{"registration_id":"<uuid>","revision":1,"plugin_id":"artcraft_ops","source_dir":"<绝对路径>"}]}`。
- **路径 B（#3638 起源码态）**：`<runtime root>/plugin-authoring.json` = `{"version":1,"projects":[{"project_id":"artcraft_ops","source_dir":"<绝对路径>","added_at":"<ISO8601>"}]}` + `/plugin-projects` 工作台；`source="project"` 永不 autostart。

---

## 10. 测试切入点（云测 / TDD，**最终 47 条**）

### 正常路径
1. `test_locator_resolves_absolute_cli_path`：`resolve_cli("photocraft", cfg)` → `os.path.isabs` 且 `os.path.isfile` == 已验证 E 盘路径。
2. `test_app_registry_diff_table`：`BUILTIN_SPECS["photocraft"].input_mode=="positional"`；`["vectorcraft"].output_flag=="--export" and output_repeatable and not has_serve`；`["craft_anything"].builder=="tool_subcmd"`。
3. `test_lazy_discovery_not_in_startup`：`on_startup()` 后 `command_catalog.discover.call_count==0`、`runner.spawn.call_count==0`；startup 不抛、`startup_failure="warn"` 生效。
4. `test_lazy_discovery_reentrant_once`：并发 10 个 `search()` 同 app → `runner.spawn.call_count==1`（`_DISCOVERY_LOCKS`），首次 entry 才触发。
5. `test_cache_key_includes_commands_hash`：`cache_key(app,"0.21.0",hashA) != cache_key(app,"0.21.0",hashB)`。
6. `test_search_returns_params_schema`：`search("sharpen","photocraft")` 含 `filter.sharpen.unsharpMask` 且 `params_schema` 含 `"amount"`。
7. `test_normalize_namespace_doc_to_document`：`("doc.inspect",engine)=="document.inspect"`；`("document.inspect",serve)=="doc.inspect"`；`session.*` 同理 ← **命名空间陷阱核心回归**。
8. `test_build_argv_photocraft`：argv == `[cli,"run","--new",json,"--cmd","edit.fill","--params",json,...,"--out",path]` 且含 `--automation-*-root`；kwargs 无 `shell` 键。
9. `test_build_argv_vectorcraft`：用 `--in`、`--export`、`--commands` 能力可用。
10. `test_craft_anything_builder`：`<cli> <tool> --json --project <file>`，不含 `--cmd`。
11. `test_e2e_layers_pipeline`：用 RESULTS §6.1 真实 stdout 回放，`steps` 长度 5、`inspect.layers` 含 id=4 的 Veil、`warnings` 含拍平原文、`output_files` 含 png。
12. `test_flatten_warning_verbatim`：`user_message` 含完整英文 `warning: 3 layer(s) flattened; layers, masks and blend modes are not kept`，未翻译/截断。
13. `test_run_sequence_returns_queued_fast`：返回 `{job_id,status:"queued",poll_with:"artcraft_ops_status"}`，耗时 <1s（mock），`create_task` 在 entry handler 内创建。
14. `test_job_state_transitions`：QUEUED→RUNNING→SUCCEEDED；spawn 前 cancel → QUEUED→CANCELLED。
15. `test_progress_throttle`：+1% 循环 100 次，push 次数 == 状态变化 + 跨 10% 次数（≤约 12），相邻间隔 ≥2s（fake clock）。
16. `test_edit_sequence_never_retried`：`is_edit_sequence=True` 断线 → `runner.spawn.call_count==1`，status∈{FAILED,CRASHED} 且文案含「核对」。
17. `test_readonly_retry_once`：纯只读断线 → 重发一次、`retried_readonly=True`，第二次不再重发。
18. `test_shutdown_terminates_all_handles`：`shutdown_all()` 后 `_PROC_HANDLES=={}` 且每个 `Process.terminate` 被调。
19. `test_cancel_forwards_artcraft_cancel`：`transport=CLI_SERVE` 且 `remote_job_id="j1"` → `ControlChannel.cancel("j1")` 被调 **且** `runner.terminate` 被调。
20. `test_cancel_cli_run_terminates_only`：`transport=CLI_RUN` → 不发远端 cancel、只 `terminate`，status=CANCELLED。
21. `test_report_completion_uses_report_status`：终态 `report_status` 被调一次 + `push_message` 完成回执（含 redact 摘要）。
22. `test_materialize_spills_large_reply`：`payload_bytes>512 KiB` → `spilled=True`、`result_handle` 指向 `data_path()` 下真实文件、回包无完整 `steps`、无 inline base64。
23. `test_preview_uses_images_upload_not_inline`：`ctx.images.upload()` 被调、`preview_url` 非空、回包不含 `base64,`。
24. `test_control_call_timeout_60s`：`runner.spawn` 默认/显式 `timeout>=60.0`；断言不传 30.0。
25. `test_blocking_uses_to_thread`：`detect_version` 的文件 hash 走 `asyncio.to_thread`，主循环无 >5s 阻塞。

### 边界
26. `test_roots_default_only_data_cache`：`default_policy(data,cache,{})` 的 read/write roots 仅这俩目录。
27. `test_roots_plaintext_reject_shortname`：`resolve_and_check(r"E:\PROGRA~2\Neko\...")` 抛 `PATH_UNSAFE`；`a.bin:hidden` ADS、`CON`、`..`、8.3、越界 symlink 同拒。
28. `test_roots_accept_long_path_prefix`：`normalize_win_path` 产出的 `\\?\C:\...` 前缀被接受（v0.18.0 起）。
29. `test_long_path_no_cwd_no_sys_executable`：`long_path()` 输出不含 `PROGRA~`、`os.getcwd()`、`sys.executable`；从 LOCALAPPDATA 或 `data_path().resolve()` 派生、正斜杠。
30. `test_cli_search_order`：config 覆盖 > LOCALAPPDATA 模板 > 注册表 > 实测路径；`C:\Program Files\ArtCraft\artcraft.exe`（L4）同目录不被当作 CLI 源。
31. `test_roots_outside_denied`：文件不在 roots → `PATH_OUTSIDE_ROOTS`。
32. `test_limit_steps_256`：257 步 → `Err` message 以 `"LIMIT_STEPS: "` 开头，`runner.spawn` 未调。
33. `test_reply_8mib_truncate`：stdout >8 MiB → `LIMIT_REPLY` warning，不抛异常。
34. `test_proxy_bypass`：`assert_no_localhost("http://127.0.0.1:52903/")` 通过；`localhost` 抛错；`build_http_client_no_proxy().trust_env is False`。
35. `test_timeout_terminates_child`：`wait_for` 超时后 `Process.terminate` 被调。
36. `test_no_shell_injection`：文件名 `a.png; del *` 作单个 argv 元素，argv 长度固定、`shell=False`。
37. `test_redact_four_surfaces`：构造含 64hex token 的 stderr → `safe_err()` message、`@llm_tool` output、`push_message` 文本、日志四处 token 全替 `'***'`。
38. `test_redact_fields_dict`：`redact({"auth":{"params":{"token":"a"*64}}, "cookie":"x"})` → 两处均 `'***'`。

### 错误输入
39. `test_exit2_no_output`：exit 2 + `unknown command doc.inspect` → `CLI_USAGE_ERROR` 且 `output_files==()`。
40. `test_namespace_trap_normalized_before_spawn`：直接给 `doc.inspect` → spawn argv 里已是 `document.inspect`。
41. `test_roots_not_granted_stderr`：`read authority is absent` → `ROOTS_NOT_GRANTED` 且文案含「显式授权目录」。
42. `test_command_not_in_registry`：`evil.inject` → `POLICY_VIOLATION`，`runner.spawn` 未调。
43. `test_llm_tool_missing_params_no_typeerror`：`Any=None` 防御式签名，缺字段不抛 TypeError。
44. `test_err_code_in_message`：`error_message` 以 `"<CODE>: "` 开头。
45. `test_entries_match_decorators`：`[[plugin.entries]].id` 集合 == `@plugin_entry` 装饰函数名集合（防 404）。
46. `test_config_example_has_no_plugin_section`：不含 `[plugin]` 段（可含 `[plugin_runtime]`）。
47. `test_runtime_config_not_from_env`：mock `self.config.dump()` 驱动 adapter 表；断言不读 `os.environ["ARTCRAFT_*"]` 业务 env（只许 LOCALAPPDATA/ProgramFiles 定位）。

---

## 11. 证据分级（两条链分开）

**[FACT] 链 1（ArtCraft 协议侧）**：见 `../probe/RESULTS.md` 9 节 —— 3 个 CLI 路径/版本/命令数；`run` 契约；`commands --json` 字段差异（7/6）；`params` 人类可读 schema；`--filter` 服务端过滤；命名空间陷阱 exit 2 整批不保存；serve methods；`document.inspect` 结构；拍平警告原文；冷启动 0.294s；exit 0/1/2；`instance.json`；#9777 未修；token 非授权边界；roots fail-closed + 纯文本逐段比对 + 8.3 拒 + `\\?\` v0.18.0 接受；按模式分置；边界参数 256/1 MiB/8 MiB/16/2048px·5 MiB/30min；v0.21.0 `jobs_list` 轮询 + bridge 55s/25s/只读重试一次编辑类不重发；Studio L4 + 云端 MCP 两通道拆分；WASM ABI v1 沙箱禁 fs/网络；工具名分叉；RFC-0012 rollout 中；命令数三口径。

**[FACT] 链 2（N.E.K.O 宿主侧）**：worker 独立子进程（无能力白名单）、env 继承、cwd=`sys._MEIPASS`；entry 默认 10s（`settings.py:311`）且 cancel 不杀 Popen（`entry_executor.py:134-155`）；`CANCEL_RUN` 只 `task.cancel()`（`host.py:1627-1632`）；LLM 工具 30s/300s（`tool_router.py:183`）；startup 瞬时 `asyncio.run`（`host.py:1327`）会取消后台 task（`game_agent_minecraft/__init__.py:192-200`，lazy-start 范式 `:204-214`）；`startup_failure=warn|fail|ignore`；`MESSAGE_PLANE_PAYLOAD_MAX_BYTES=512*1024`（`settings.py:610`）、超限整条丢弃（`context.py:1203-1210`）；`Err` message 原样回包（`entry_executor.py:169-173,220-229`）；`permissions` 仅 UI 声明（`ui_manifest.py:177-199`）；`report_status`（`base.py:481-484`→`context.py:653-664`）；`self.data_path()/cache_path()`（`base.py:135-141`）；`self.config.dump()`；代理热切换（`config_router/proxy.py:82-101`）；>5s stall（`entry_executor.py:136-144`）；`@plugin_entry` async/`**_`/显式 timeout；`@llm_tool` 手写 JSON Schema + `Any=None`；只有 `params=<模型>` 校验、无 `inputs`；`Err.code` 抹平；`[[plugin.entries]]` 一一对应漏写 404；`ctx.images.upload()` 不在 lifecycle；vendor 缺 dist-info 拒载 + cp311-win_amd64；`CREATE_NO_WINDOW`；开发态注册口径分裂两套；目录名==plugin id；3 个成熟骨架。

**[VALUE JUDGMENT]**：一次性 run 主通道；两级工具+3 配套；roots 默认 data/cache 且用户目录重启生效；命令策略不一刀切 `file.*`；`require_confirm_on_write=False`；进度节流 2s/10%；job TTL 3600s；含任一编辑命令即整体判编辑类；13 app 用 AppSpec 表 + builder 钩子；Studio 两通道仅留接口；运行时配置驱动 adapter。

**[INFERENCE]**：亮度类命令具体 id（运行时动态发现）；Craft Anything `commands --json` 字段与 tool_subcmd 形态；`plugin.toml` 字段名与装饰器叠放顺序（见 §12）；13 app 的 CLI 路径；PdfCraft `tools` 表来自外部核验、本机未装；开发态注册本机 exe 走 A 还是 B；serve/bridge `jobs_list` 语义 v1 不触发。**未核验/阴性**：N.E.K.O↔ArtCraft 成品集成两通道均未发现 [INFERENCE]，但宿主内 3 个成熟骨架可适配 [FACT 链 2]。

**深化建议**：① v1.1 启用 stdio `serve` 常驻会话（stdio 无需 token，天然绕开 #9777）；② RFC-0012 落地后把 `[artcraft.apps.*]` 导出成 `apps.json`，新增 app 只改数据不改代码。

---

## 12. 落地前必须解决的字段冲突（云雨 stub-SDK 核验中）

`plugin.toml` 与 `neko-plugin-sdk` SKILL.md / 宿主源码疑似冲突。**裁决原则：以 `D:\N.E.K.O` 宿主源码 + 安装版实际行为为准，不以 SKILL.md 为准**（SKILL.md 已被证实对 #3638 之后的源码过时）。

| # | 规格原稿 | 已知/待核 | 风险 |
|---|---|---|---|
| 1 | `[[plugin.entries]]` 用 `id = "..."` | SKILL.md 同时有 `id` + `name`；`id` 与装饰器对应。`name` 是否必填待核 | 漏字段 → refresh 后 404 |
| 2 | `[plugin.sdk] min_version` | SKILL.md 明写 `recommended = ">=0.1.0,<0.2.0"` / `supported = ">=0.1.0,<0.3.0"` | 字段不识别 → 拒载 |
| 3 | `@plugin_entry(id=..., llm_result_fields=[...])` | SKILL.md 用 `@plugin_entry(id=..., name=..., timeout=...)`；`llm_result_fields` 是否支持待核 | 装饰器参数错误 |
| 4 | `llm_result_fields` 写在 toml | 可能是装饰器参数而非清单字段 | 字段被忽略 |
| 5 | `@plugin_entry` 与 `@llm_tool` 叠放顺序 | 未核验 | 双注册失败 |
| 6 | `[plugin.store]` / `[plugin_runtime]` 下业务子段 | 未核验 | 拒载 |
| 7 | `@lifecycle(id="startup", startup_failure="warn")` | SKILL.md 用 `@lifecycle(id="startup")`；`startup_failure` 参数待核 | startup 不触发 |
| 8 | `type = "plugin"` | 普通插件可能不需要或值不同 | 拒载 |
| 9 | `self.data_path("jobs.json")` | 需核签名（`base.py:135-141`） | 路径错误 |

云雨核验结论回来后，以他的**字段修正表 + 修正后完整 plugin.toml** 覆盖 §9-A。

---

## §13 主理人最终裁决 + token 泄漏补丁（v1 强制，覆盖前文任何冲突处）

来源：云雨「token 泄漏逐跳调用链」[FACT 宿主侧逐跳行号] + 云调补遗 II（2 处自我更正 + 正则不足修正） + 云远 P1–P6 补丁。**以下为唯一有效口径**，前文 §1/§5/§6/§7/§8 与之冲突处以本节为准。

### §13.0 三点裁决

| # | 争议 | 裁决 |
|---|---|---|
| 1 | `safe_error(code, exc)`（云雨）vs `safe_err(code, **kwargs)`（云远） | **`safe_error` 为正名 + `safe_err` 薄别名**。理由：保住云测 §10 已写的断言，同时支持异常分流。 |
| 2 | P3（push_message 双轴）/ P4（report_status 字段白名单）是否升格 v1 强制 | **升格强制**。理由：宿主侧逐跳行号证明 token 会进 `logger.info` 与聊天窗，且 `report_status` 正是云调上一版亲自推荐、自己埋的雷。§10 增补 3 条断言（47→50）。 |
| 3 | 脱敏用「黑名单正则」还是「白名单结构」 | **白名单结构为主 + 正则仅作兜底第二层**，采纳云调修正。理由：PhotoCraft/FilmCraft 是 64hex 能匹配，但 **PdfCraft 凭证在 `~/.pdfcraft-control.json`、README 只说 "random token" 未规定 64hex 格式**；VectorCraft 控制端口完全无认证；还需防 `--control-token-file` 路径、整段 auth 首帧、base64/URL 编码凭证。单一 64hex 正则必然漏网。 |

**核心原则**：所有对外文本只由**固定错误码 + `job_id` + 枚举 phase + 句柄**组成，异常对象**根本不插值**；`redact()` 只是第二层兜底。

### §13.1 六个必须脱敏/白名单的点（验收项）

| # | 位置 | 必须做什么 | 出处 [FACT 宿主侧] |
|---|---|---|---|
| 1 | `Err(SdkError(...))` message、任何 `except Exception` 转字符串 | 固定错误码 + 脱敏摘要；**绝不拼 token / auth 首帧 / 控制文件内容 / 原始协议帧 / argv / 连接串 / exc 详情** | `exceptions.py:6-22`、`entry_executor.py:169-173,220-229`、`llm_tools.py:214-223` |
| 2 | `@llm_tool` 返回值 `output/is_error/error` | 只回白名单字段；宿主原样透传不过滤 | `llm_tools.py:241-263`、`tool_calling.py:248-256,494-525` |
| 3 | `push_message` 的 `parts[].text` + `metadata` + `summary/detail` | 内部进度 `visibility=[]` + `ai_behavior="blind"`；对外文本不含认证材料 | `context.py:1167-1201,1403-1415`、`proactive_bridge.py:478-524` |
| 4 | `report_status(status)` 的 dict | 只放非密字段（整条会被 `logger.info` 落盘） | `context.py:653-666`、`settings.py:514-515` |
| 5 | 插件自身 `logger.*`，尤其 `logger.exception` traceback | 先 `redact()` 再预格式化；loguru `{}` / stdlib `%s` 都要兼容 | `llm_tools.py:215-218` |
| 6 | 工具/命令白名单 + `params` 内路径/端口校验 | 插件侧 `ALLOWED_COMMANDS` 显式校验；**不信任宿主 `permissions`**；`@llm_tool` arguments 是模型可控 kwargs | `ui_manifest.py:177-199`、`llm_tool.py:176-188` |

**宿主唯一已有的自我保护**：`host.py:2209-2224`（TRIGGER 日志只记 `type(args)`/`keys`，原文「Parameter values may contain schema-declared sensitive data」）—— **仅护入口日志，不覆盖任何出口**，不可当兜底。

**诚实阴性**：`PLUGIN_LOG_CTX_MESSAGE_PUSH`（`settings.py:518-519`，默认 True）在 `D:\N.E.K.O` HEAD 全仓只有声明与导出、**无消费点**，今天不存在「push payload 被写日志」这条链；但不能据此假定将来没有。

### §13.2 可直接复制的补丁代码

```python
# errors.py —— P1 统一错误出口
def safe_error(code: str, exc: BaseException | None = None, **kwargs) -> "Err":
    """唯一对外错误出口（业务代码禁止直接 new SdkError）。
    对外 message = f"{code}: {redact_text(t(code, **kwargs))}" —— 固定枚举码 + 脱敏摘要。
    exc 详情只允许进插件日志，且先 redact_text(str(exc))、用预格式化 logger。"""
    if exc is not None:
        logger.error("artcraft_ops %s failed: %s", code, redact_text(str(exc)))  # stdlib %s
    return err(code, **kwargs)      # 裸 dict envelope，见 §19.15；不要改回 Err

def safe_err(code: str, **kwargs) -> "Err":        # 薄别名，保住 §10 测试断言
    return safe_error(code, None, **kwargs)

# redact 用白名单结构为主、正则兜底（云调修正，覆盖非 64hex 凭证）
REDACT_KEY_PARTS = ("token","authorization","cookie","api_key","secret",
                    "control_file","control-token","auth.params.token")
REDACT_TEXT_PATTERNS = (
    r"\b[0-9a-fA-F]{32,}\b",                       # 任意 32+ 长十六进制串（不只 64）
    r'\{"id"\s*:\s*"auth".*?"token"\s*:\s*"[^"]+"\}',   # 整段 auth 首帧
    r"--control-token-file\s+\S+",                 # token 文件路径
)
def redact(obj: Any) -> Any:
    """递归：命中 REDACT_KEY_PARTS 的键 → 值替 '***'；字符串再过 REDACT_TEXT_PATTERNS。"""
def redact_text(s: str) -> str: ...

# __init__.py —— P2 工具返回值字段白名单
TOOL_OUTPUT_ALLOWLIST: frozenset[str] = frozenset({
    "job_id","status","poll_with","cancel_with","progress","progress_label",
    "phase","error_code","error_message","result_handle","preview_url",
    "app","version","total_matched","hint","commands","apps",
})
def tool_output(payload: dict) -> dict:
    """每个 @llm_tool 返回前必过：只留白名单字段 + redact()。
    一律不返回：响应体 / steps 全文 / inspect 全文 / argv / stderr_text /
                RawOutput / 连接串 / token / instance.json 内容。"""
    return redact({k: v for k, v in payload.items() if k in TOOL_OUTPUT_ALLOWLIST})

# jobs.py —— P3 push_message 双轴语义
INTERNAL_PROGRESS = {"visibility": [],       "ai_behavior": "blind"}  # 不注入、不渲染
USER_COMPLETION   = {"visibility": ["chat"], "ai_behavior": "blind"}  # 渲染、不注入模型
USER_COMPLETION_TEMPLATE = "任务 {job_id} 已完成：{status}；结果句柄 {result_handle}"
FIXED_PROGRESS_TEMPLATE  = "任务 {job_id} 进度 {pct}%：{phase}"
# 模板只插 job_id / status / pct / phase / result_handle；
# parts 的 text / metadata / summary / detail 四字段全部经 redact_text()；
# metadata/summary/detail 同样走 P2 字段白名单（不只 text）。
# 注意：blind + visibility=["chat"] 仍渲染给用户（proactive_bridge.py:524-528）。

# jobs.py —— P4 report_status 字段白名单（整条会被 logger.info 落盘）
REPORT_STATUS_ALLOWLIST = ("job_id", "phase", "progress", "error_code")
def report_status_payload(job: "JobState") -> dict:
    return {"job_id": job.job_id, "phase": job.status.value,
            "progress": round(job.progress, 3), "error_code": job.error_code}
# report_completion(): ctx.report_status(report_status_payload(job))
#   + push_message(parts=[{"text": redact_text(USER_COMPLETION_TEMPLATE.format(...))}], **USER_COMPLETION)

# sequence.py —— P5 params 内层校验（@llm_tool arguments 模型可控）
def validate_tool_kwargs(spec: "AppSpec", policy: "CommandPolicy",
                         kwargs: dict[str, Any], roots: "RootsPolicy",
                         allowed: frozenset[str]) -> None:
    """- steps[].cmd ∈ ALLOWED_COMMANDS（= 已发现注册表 ∩ 非 deny_exact/deny_prefixes）
       - params 内任何文件路径 → roots.resolve_and_check()
       - params 内任何 host/port → 必须字面 127.0.0.1（#9777），其余一律拒
       - params 内禁止出现 token / connection string / 控制文件内容字段
       - len(steps) ≤ policy.max_steps(256)
       违规一律 safe_error(POLICY_VIOLATION)。"""
ALLOWED_COMMANDS = frozenset(c.id for c in catalog if c.enabled) - deny_exact \
                   - {c.id for c in catalog if c.id.startswith(deny_prefixes)}

# P6 日志铁律
#  ① 只用预格式化：stdlib logger.error("... %s", val) / loguru logger.error("... {}", val)
#  ② 禁用 f-string 拼进 logger、禁用 logger.exception(...)
#  ③ 所有进 logger 的 val 先 redact_text(...)
#  ④ 异常详情只进日志不进对外 message
#  ⑤ 不依赖 host.py:2209-2224 作出口兜底
```

### §13.3 上层错误分支硬约束（云调要求单列）

`plugin\server\routes\llm_tools.py:214-223` 的失败返回是 **HTTP 200 + `{"output":{"error": f"{type(exc).__name__}: {exc}"}, "is_error": True}`**，不是 4xx/5xx。→ 插件上层**必须读 `is_error` / `output.error` 判断失败，不能靠 HTTP 状态码**。

### §13.4 测试增补（§10 从 47 条 → 50 条）

48. `test_push_message_dual_axis`：内部进度断言 `visibility==[] and ai_behavior=="blind"`；完成通知断言 `visibility==["chat"]` 且 `parts` 的 text/metadata/summary/detail 四字段均经 `redact_text`。
49. `test_report_status_field_allowlist`：`report_status_payload(job)` 的 key 集合 == `{job_id,phase,progress,error_code}`，断言不含 `result_handle`/`path`/`token`。
50. `test_logger_no_fstring_no_exception`：AST 扫描 `errors.py`/`jobs.py`，断言无 `logger.exception`、无 f-string 作 logger 首参。

补充两条断言（并入既有测试，不新增编号）：
- `test_safe_error_no_exc_detail_in_message`：`safe_error("CLI_TASK_FAILED", RuntimeError("token=abc..."))` 的对外 message 不含异常详情，只含 `"CLI_TASK_FAILED: "` 前缀。
- `test_redact_non_64hex_token`：`redact({"pdfcraft":{"control_file":"/home/u/.pdfcraft-control.json","token":"s3cr!et-not-hex"}})` → 两处均 `'***'`（验证不依赖 64hex 正则）。

### §13.5 与前文的关系

- P1 精确化 §1 `errors.py` / §8；P2 精确化 §5 返回契约；P3/P4 是 §7 **新增硬约束**（原规格缺失）；P5 精确化 §6 `CommandPolicy`；P6 精确化 §8 日志规范。
- 3 条骨架、lazy discovery、60s 超时、cancel 双路、`trust_env=False`、字面 `127.0.0.1` 已在最终版 §1/§4/§6/§7 写死，**无冲突、不重复**。
- 全部补丁属 **N.E.K.O 宿主侧证据链**（§11 第二条链），与 ArtCraft 协议侧正交，不混为一套证据。
- 云远已交回完整 11 项规格 + 本补丁，不再索要 84KB 全文重发。

---

## §14 v0.2 最终裁决（覆盖 §13 中对应条款）

来源：neko-architect v0.2 修订（D1–D5，交回主理人裁决 2 点）。**本节为最终口径**。

### §14.0 两点裁决

| # | 问题 | 裁决 |
|---|---|---|
| 1 | 是否确认「白名单结构为主防线、`redact()` 为兜底第二层」 | **确认**。与 §13.0 裁决 3 一致。v0.1 补丁里「`redact()` 为主防线 / 64-hex 正则」的表述**作废**；`REDACT_TOKEN_RE = r"\b[0-9a-fA-F]{64}\b"` 仅保留作**子集常量兼容云测 §10 已写测试**，不得再称主防线。 |
| 2 | `report_status_payload` 字段集用 `{job_id, phase, error_code}` 还是保留 `progress` | **保留 `progress`**（`{job_id, phase, progress, error_code}`）。理由：`*_status` 轮询与进度节流需要它，且是 0..1 数值、不扩风险面。附加硬约束：`phase` 与 `error_code` 必须是**固定枚举值**，禁止自由文本；`progress` 只许 float。 |

### §14.1 D1 — 替换 §13.2 的 `REDACT_TEXT_PATTERNS`

主防线 = 白名单结构（异常对象根本不插值）；下列规则只作**兜底第二层**：

```python
REDACT_KEY_RE = r"(?i)(token|authorization|cookie|\.token|control[_-]?file|secret|api[_-]?key)"
REDACT_VALUE_RES = (
    r"\b[0-9a-fA-F]{32,}\b",                              # 32+ 位十六进制（不止 64）
    r'(?i)["\']?method["\']?\s*:\s*["\']auth["\']',        # 整段 auth 首帧
    r"(?i)--control-token-file\s+\S+",                    # token 文件路径
    r"(?i)(bearer\s+|basic\s+)[A-Za-z0-9+/=._-]{16,}",   # base64 / URL 编码凭证
)
REDACT_TOKEN_RE = r"\b[0-9a-fA-F]{64}\b"                  # 子集常量，仅兼容测试
def redact(obj: Any) -> Any:
    """兜底第二层。① 键名命中 REDACT_KEY_RE → 值替 '***'；
       ② 字符串命中 REDACT_VALUE_RES 任一 → 替 '***'；
       ③ 递归 dict/list/tuple；parts.text / metadata / summary / detail 全字段覆盖。"""
```
理由 [FACT ArtCraft 协议侧]：64hex 只覆盖 PhotoCraft / FilmCraft；PdfCraft 凭证在 `~/.pdfcraft-control.json` 且格式未规定、VectorCraft 端口无认证、SoundCraft 端口自动分配 → 非 64hex 全漏网。白名单结构不依赖单 app token 格式，才能适配 13 app adapter。

§13.2 P6 日志铁律③改为「主防线=不插值；`redact` 仅兜底，且用上面扩展规则」，其余①/②/④/⑤不变。

### §14.2 D2 — 新增 `unwrap_tool_reply()`（§13.3 的可执行版）

```python
# errors.py 或 bridge.py
class ToolReplyError(Exception): ...
def unwrap_tool_reply(resp: Any) -> dict:
    """宿主工具回包统一解包：HTTP 200 也可能是错误（llm_tools.py:214-223）。
       先看 is_error / output.error，再看正常 output；禁止用 status_code==200 判成功。
       异常 message 会经 ToolResult.output_as_json_string() → {"role":"tool"} 进模型上下文
       （tool_calling.py:204-212,248-256,494-525；omni_offline_client/_tools.py:663-671；
        omni_realtime_client/_tools.py:890-893）→ 固定错误码是硬要求，不是风格建议。"""
    body = resp if isinstance(resp, dict) else {}
    out = body.get("output") or {}
    if body.get("is_error") is True or (isinstance(out, dict) and out.get("error")):
        raise ToolReplyError(redact_text(str(out.get("error", "is_error"))))
    return out
```
挂接：`runner` / `jobs` 的「控制调用结果读取」路径**全部**经此函数。

### §14.3 D3 — push_message 双轴补强

```python
FORBIDDEN_AI_BEHAVIORS = ("read", "respond")
# 原因：ai_behavior="read" 会在下一次 hot swap 作为背景上下文进新 session
#       （lifecycle.py:2959-2966）→ 内部进度只能用 "blind"。
# push_progress() / report_completion() 入口断言 ai_behavior not in FORBIDDEN_AI_BEHAVIORS。
# context.py:1403-1415 只做 512 KiB 体积检查、无内容审查
#   → 白名单结构是唯一可信防线，不能指望宿主过滤（与 P2 字段白名单、D1 闭合）。
```
§13.2 P3 的 `INTERNAL_PROGRESS` / `USER_COMPLETION` 双轴常量不变。

### §14.4 D4 / D5 — 对账结论（无冲突，仅记录）

- D4：`report_status_payload` 按 §14.0 裁决 2 定为 `{job_id, phase, progress, error_code}`；其余禁止项（token / path / raw frame / argv / result_handle / preview_url / stderr 进 status dict）完全保留。`PLUGIN_LOG_CTX_STATUS_UPDATE` 默认 True 会把整个 status dict `logger.info` 落盘。
- D5：路径校验「纯文本逐段比对 / 拒 `..`·设备名·ADS·8.3·越界 symlink / 长名正斜杠 / `\\?\` v0.18.0 起接受」已在 §6 覆盖，不重复。6 个脱敏点 ↔ P1–P6 + D2 的映射已闭合。`host.py:2209-2224` TRIGGER 日志只打 `type(args)`/`keys`，**只护 trigger 入口**。`PLUGIN_LOG_CTX_MESSAGE_PUSH` 默认 True 但 HEAD 全仓无消费点（诚实阴性）→ **不得据此假定将来没有**，日志面按当前即有风险处理。

### §14.5 测试增补（§10 从 50 条 → 51 条）

51. `test_tool_error_is_http200_with_is_error`：构造 HTTP 200 + `{"output":{"error":"RuntimeError: token=..."}, "is_error": True}` → `unwrap_tool_reply()` 抛 `ToolReplyError`，**不得当成功吞掉**；断言异常 message 不含 token 明文。

另补一条断言并入既有测试：`test_forbidden_ai_behaviors` —— `push_progress()` 传 `ai_behavior="read"` / `"respond"` 时断言抛错。

**测试总数最终 = 51 条**（§10 的 47 + §13.4 的 48/49/50 + §14.5 的 51；另有 3 条并入既有测试的补充断言）。

### §14.6 不变项（重申，防走样）

lazy discovery、动态命令发现、`plugin.toml` 运行时配置、roots 按模式分置、字面 `127.0.0.1` + `trust_env=False`、Popen 生命周期、job 轮询与显式 cancel、`timeout=60.0` + `asyncio.to_thread`、512 KiB 消息面走句柄、WASM 不可做插件、3 个成熟骨架适配、两条证据链正交 —— **全部不变**。

---

## §15 实机核验修正（云雨 · 真 CLI / 真 SDK，**覆盖前文任何冲突处**）

来源：neko-plugin-dev「实现前 INFERENCE 核验」，全部 [FACT] 实机命令输出 / 真实 SDK 运行结果 / 源码行号（仅「安装版 exe 属 pre-#3638」为 [INFERENCE]）。**本节优先级最高**，§1/§3/§9/§12 与之冲突处以本节为准。

### §15.1 三条必须改的硬伤

| # | 原规格写法 | 实机真相 | 落地修正 |
|---|---|---|---|
| 1 | CLI 定位首选 `%LOCALAPPDATA%\Programs\ArtCraft\<App>\<app>-cli.exe` | 本机实际在 **`E:\Program Files\Artcraft\<App>\`**（`Artcraft` **小写 c**）：`PhotoCraft\photocraft-cli.exe` 0.21.0、`VectorCraft\vectorcraft-cli.exe` 0.22.0、`Craft Anything\craft-anything-cli.exe` 0.5.0。三者**都不在 PATH**；注册表 Uninstall 的 `InstallLocation` 除 Studio 外**全为空**。证据：launcher MSI 日志（UTF-16LE）。 | `resolve_cli()` 改为：**config 显式路径 → 多根扫描（含 E 盘实测路径）→ `--version` 探针确认 → 报错**。删掉对注册表 InstallLocation 的依赖（只作可选兜底）。 |
| 2 | 动态发现统一用 `commands --json --filter "<text>"` | **Craft Anything 不支持 `--filter`**：`craft-anything-cli commands --json --filter "adjust"` → **exit 2** `unexpected argument '--filter' found`（`commands --help` 里根本没有该参数）。只有 PhotoCraft 有 `--filter <TEXT>`。 | `AppSpec` 增 `supports_filter: bool`（PhotoCraft=True / VectorCraft 待核 / CA=False）；CA 侧改为**拉全量后本地子串过滤**。 |
| 3 | `has_disabled_reason=False / has_needs_document=False`（CA 保守设） | **只对 CA 成立，对 PhotoCraft 是错的**：PhotoCraft `commands --json` = **993 条，8 字段** `disabledReason / enabled / id / label / menu / needsDocument / params / shortcut`，`params` 是字符串 mini-schema；CA `commands --json` = **321 条扁平 list，6 key** `command`(连字符) / `needs_target` / `summary` / `title` / `tool`(下划线) / `toolset`。 | `parse_commands_json()` 按 app 分适配器：PhotoCraft 8 字段映射；CA 6 key 映射（`command`→id、`title`→label、`summary`→params_schema、`tool`→id 别名）。**CA 可用性权威来源是 `capabilities --json`**（`{available, needs_target, reason, tool}`），本机 clean-room 下 **224/321 `available=false`** → 必须加能力门控（或 `--deep`），否则检索出一堆不可用命令。 |

### §15.2 Craft Anything 契约细节（实机）

- 「每个 tool 同时是子命令」**成立**（连字符与下划线两种写法都接受）。
- **`--json` 双义**：无值 = 输出格式；带值 = 传 params。无歧义输出开关是 **`--output-format json`**。
- **`--project` 必须指向尚不存在的路径**，传已存在目录返回 `bad_input: already exists`。
- 信封：成功 `{"result": ...}`；失败 `{"error": {category, code, message, remediation, retryable, details}}`；退出码 0/1/2。
- `tool_subcmd` builder 需按上述信封解析，不能沿用 PhotoCraft 的逐行 JSON。

### §15.3 装饰器与注册（REAL SDK 实测，非 stub）

- **`@plugin_entry` 与 `@llm_tool` 叠放顺序无关**：两个装饰器都只 `setattr` 后原样返回（`shared/core/decorators.py:248-252`、`llm_tool.py:225-256`）；两种顺序均得到 `collect_entries() == {'run': 60.0}` + `collect_llm_tool_methods() == [('run_tool', 60.0)]`，动态 id `__llm_tool__run_tool` 无冲突。**但两条路径的 timeout 各自独立，都要设**。
- **开发态注册「路径 C」最稳且已源码验证**：`settings.py:100-118,169-175` 定义 `.neko-plugin-installations\plugins` 为发现根；`registry_service.py:362-380` 用 `glob("*/plugin.toml")` 扫描，**无注册文件 / 锁门控**。→ §9 的「稳妥路径」升级为**首选且有源码依据**；A/B 仅备用说明。

### §15.4 `plugin.toml` 字段真相（REAL SDK `validate_plugin_config` + 真 CLI 校验器输出）

| 项 | 真相 | 出处 |
|---|---|---|
| `[plugin.sdk] min_version` | **不是合法字段** → 静默丢弃 + `WARNING [plugin.sdk].min_version is not a recognized plugin.toml field`。只能用 **`recommended` / `supported` / `untested` / `conflicts`** | `validate_cmd.py:388` |
| `[[plugin.entries]]` | **运行时必需，但完全不被校验**：校验后模型里 `entries` 被丢弃；真 CLI 校验器报 `WARNING [plugin].entries is not a recognized plugin.toml field`（全文 0 处 `entries`）。运行时由 `registry.py:879-891` 从**原始 toml** 读，且 **plugin.toml 优先于 plugin.meta.json** | `validate_cmd.py:176`、`registry.py:879-891` |
| → 后果 | **字段拼错零报错**，只能靠 AST 对账测试（§10 #45）发现。这是「测试全绿但 refresh 后面板 404」的头号风险 | — |
| `[[plugin.entries]]` 字段 | 真实范本 `agent_bridge\plugin.toml:22-36` 每条只写 `id` + `name` → **照抄** | — |
| `llm_result_fields` | 既是装饰器参数**也是**合法 manifest 字段（两处都可） | — |
| `[plugin_runtime]` | 只允许 **5 个键**；业务段**必须放顶层**（`schema.py:305` `extra="allow"`） | `validate_cmd.py:712`、`schema.py:305` |
| `[artcraft.i18n]` | 作为业务表允许但宿主**不读**；宿主 i18n 只认 `[plugin.i18n]` 的 `default_locale` / `locales_dir` | `i18n.py:243-268` |

→ **§9-A 草案必须修正**：删 `min_version`，改 `[plugin.sdk] recommended/supported`；业务段（§9-C 的 `[artcraft.*]`）**移到顶层 `plugin.toml` 或独立运行时配置**，不能塞在 `[plugin_runtime]` 下。

### §15.5 命名空间与命令 id（实机印证，替代 [INFERENCE]）

- PhotoCraft 全表 **0 个 `doc.*`、11 个 `document.*`**；`session.*` 只有 `session.inspect`，**无 `session.list`**。→ §1 的 `readonly_prefixes` 里 `session.list` / `jobs.list` / `command.list` 需按实际表修正。
- **调色 id 无需再猜**：`image.adjustments.*` 共 **23 个**，含 `.brightnessContrast` / `.levels` / `.exposure` / `.curves` / `.vibrance` / `.desaturate`。→ 时序图 §4[2] 的 [INFERENCE] 可去掉。
- ⚠️ **`enabled=false, disabledReason="no document open"` 是运行时状态，不是永久禁用** → **不能用 `enabled` 建永久白名单**；`ALLOWED_COMMANDS` 必须基于「注册表全量 id ∩ 黑名单」，不按 `enabled` 过滤。
- `run --cmd <ID> --params <JSON> --out <FILE>` 已从 `run --help` 确认；`--params` 作用于**前一个 `--cmd`**、可重复。

### §15.6 测试环境（沙盒 real 模式已跑通）

```
PYTHONPATH=<neko-runtime-sandbox> python -m sandbox.cli inspect <dir> --mode real --neko-src "D:\N.E.K.O" --json
→ mode=real, sdk_version=0.1.0
```
两个坑（已同步云测）：
1. `missing_in_meta` 里的 `__llm_tool__*` 是**动态 id 假阳性**，不要在 AST 对账里当缺失。
2. 本地 `import plugin.core.registry` 缺 `ormsgpack`（仅冻结 bin 有 cp311 .pyd）→ 测试改用 `plugin.sdk.plugin` + `plugin.config.schema`，不要 import `plugin.core.registry`。

### §15.7 对 §10 测试的增量修正（云测照此改）

- 新增 `test_ca_no_filter_flag`：`build_argv` 对 `craft_anything` **不产生 `--filter`**；CA 走本地过滤。
- 新增 `test_ca_capability_gate`：用真实 `capabilities --json` 样本（含 `available=false`）→ 检索结果过滤掉不可用命令。
- 新增 `test_ca_envelope_parse`：`{"result":...}` 与 `{"error":{...}}` 两种信封都能解析，exit 0/1/2 映射正确。
- 新增 `test_no_permanent_whitelist_from_enabled`：`enabled=false` 的命令**仍应**出现在检索结果里（只是标注运行时不可用）。
- 修改 #45：AST 对账要**同时**校验 `id` 与 `name`，并显式说明「宿主校验器不检查 `[[plugin.entries]]`，这是唯一防线」。
- 修改 #2 / #8 / #10：`BUILTIN_SPECS` 的路径断言改为 `E:\Program Files\Artcraft\...`（小写 c）；`commands --json` 字段断言按 PhotoCraft 8 / CA 6 分开。

**测试总数 51 → 56 条**（新增 5 条）+ #45/#2/#8/#10 四处断言修正。

---

## §16 最终收口（v0.3 + 补遗 III）· 设计阶段结束

**本文件 `ARCHITECTURE-FINAL.md`（含 §0–§16）是唯一权威版本。** 初版 `spec/ARCHITECTURE.md` 作废；v0.1/v0.2/v0.3 增量均已吸收进本文件，**不再另出 84KB 合并定稿**（避免四份文档对账）。设计阶段到此结束，进入实现。

### §16.1 裁决 E：CLI 定位顺序（云远交回，采纳云调的精确口径）

**不整条推翻上游**。云调复核 `vectorcraft/docs/mcp.md:61` 原文：
> `Windows (MSI) | %LOCALAPPDATA%\Programs\ArtCraft\VectorCraft\vectorcraft-cli.exe **by default** (C:\Program Files\ArtCraft\... for an all-users install, ... **or wherever you installed it otherwise**), not on PATH`

上游自带 "or wherever you installed it otherwise" 兜底，原报告标为 [FACT 上游默认路径] **依然成立**；本机 `E:\Program Files\Artcraft\<App>\`（`Artcraft` 小写 c）是上游允许的**自定义安装位置**，不是矛盾。

**定案 `resolve_cli()` 顺序**：
```
1. config 显式覆盖（[artcraft.apps.<id>].cli_path，最高优先）
2. 本机真实安装根探测：MSI 日志
   %LOCALAPPDATA%\ArtCraft Launcher\downloads\*.log（UTF-16LE，含 E:\Program Files\Artcraft\...\*-cli.exe）
3. 上游默认路径族：
   %LOCALAPPDATA%\Programs\ArtCraft\<App>\
   C:\Program Files\ArtCraft\<App>\（及 Program Files (x86) 32-bit 分支）
4. 注册表 Uninstall InstallLocation（仅可选兜底：实机除 Studio 外全为空，不可单独依赖）
5. --version 探针确认二进制可用
6. 全未命中 → safe_error(APP_NOT_FOUND)
```
附加 [FACT]：三个 CLI **都不在 PATH**；目录名大小写不统一，**不能硬编码**任一路径。能力矩阵表 Windows CLI 位置列改为「上游示例路径（默认） / 本机实测 `E:\Program Files\Artcraft\<App>\`」两行并列。

### §16.2 云远列出的另 4 点：已于前文裁决，不再重复

| 云远编号 | 状态 | 位置 |
|---|---|---|
| v0.1 `safe_error` vs `safe_err` 命名 | 已裁：`safe_error` 正名 + `safe_err` 薄别名 | §13.0 裁决 1 |
| v0.1 P3/P4 是否升格 v1 强制 | 已裁：升格强制 | §13.0 裁决 2 |
| v0.2 白名单结构为主 / redact 兜底 | 已裁：确认 | §13.0 裁决 3 + §14.0 裁决 1 |
| v0.2 P4 是否保留 `progress` | 已裁：保留 | §14.0 裁决 2 |

### §16.3 v0.3 功能正确性增量（并入实现）

**`AppSpec` 新增 5 个字段**（per-app 解析器分离，不能只靠 `has_*` 布尔）：
```python
commands_parser: str = "photocraft"   # "photocraft"(camelCase 8-key) | "craft_anything"(6-key)
filter_mode: str = "server"           # "server"(PhotoCraft --filter) | "local"(CA 拉全量本地过滤)
capabilities_gate: bool = False       # CA=True：commands.json ≠ 可用命令表
output_format_flag: str = ""          # CA="--output-format json"（避开 --json 双义）
params_mode: str = "flag"             # CA="@params.json" 或 stdin "-"
```
- `parse_commands_ca()` 与 PhotoCraft 解析器**完全分离**；`discover_available(spec)` 用 `capabilities --json` 门控（clean-room 下 224/321 `available=false`）。
- **CA `--project` 必须指向尚不存在的路径** → 用 `<data_path()>/<job_id>.cany`，不能把 `data_path()` 直接传进去。
- **`@llm_tool` 必须显式设 `timeout`**：普通入口取 `@plugin_entry(timeout=)`，LLM 工具取 `@llm_tool(timeout=)`（`base.py:517-545` → `register_dynamic_entry`），两条路径独立。§5 只给前者设了，**实现时补齐 5 个 `@llm_tool(timeout=...)`**。装饰器叠放顺序无关（REAL SDK 实测），[INFERENCE] 消解。
  **补正（§17.2 #3c）**：`[[plugin.entries]].timeout` 若声明，会**覆盖** `@plugin_entry(timeout=)`（`registry.py:685-696`）。装饰器是基线，清单是最终生效值 → **两边必须写同一值**，否则静默以清单为准。

**`plugin.toml` 修正片段**（替换 §9-A）：
```toml
[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"      # 替换非法的 min_version
supported   = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true                       # ★ 必需：registry.py:1295-1325 据此决定是否加载（路径 C 前提）
auto_start = true
timeout = 30
startup_failure = "warn"

# [[plugin.entries]] 每条只写 id + name（范本 agent_bridge\plugin.toml:22-36）
# 其余元数据（description/timeout/llm_result_fields）一律放 @plugin_entry 装饰器
# 绝不列 __llm_tool__*（动态注册 id）
[[plugin.entries]]
id = "artcraft_ops_search_commands"
name = "检索命令"
# ...共 5 条...
```
`[plugin_runtime]` 允许键白名单；业务段 `[artcraft.*]` 必须**顶层**（§9-C 已是顶层 ✅）；`[artcraft.i18n]` 宿主不读，仅业务用；真要宿主 i18n 得用 `[plugin.i18n]` 的 `default_locale`/`locales_dir`。

**AST 对账测试从「可选」升为「必需」**：`[[plugin.entries]]` 校验器完全不检查（`validate_cmd.py:176`），字段拼错**零报错**，对账以 `plugin.toml` 为准 —— 这是「测试全绿但 refresh 后面板 404」的唯一防线。

**开发态注册 [INFERENCE] 消解**：安装版 exe（2026-10-31，pre-#3638）→ 路径 A；`D:\N.E.K.O` #3638 / 上游 → 路径 B；**路径 C 与 A/B 无关、已源码验证**（`settings.py:100-118,169-175`、`registry_service.py:362-380` 用 `root.glob("*/plugin.toml")` 扫描，无注册文件/锁门控）→ **采用 C，前提补 `[plugin_runtime] enabled=true`**。

### §16.4 补遗 III 增量 [FACT / INFERENCE]

- **版本时效独立复核**：PhotoCraft 0.21.0 = `v0.21.0`（2026-11-06T20:22:03Z）、VectorCraft **0.22.0** = `v0.22.0`（20:22:17Z）、Craft Anything 0.5.0 = `v0.5.0`（20:21:17Z）—— **三个本机二进制恰好是上游最新一批、同一批协同发布**。用户「仓库不一定最新」的提醒在本机不成立；滞后的是 `photocraft.im` / `photocraftwiki.com` 等第三方站。所有版本数字仍带时点引用。
- **命令发现入口从 3 种变 4 种**：新增 `capabilities --json`（`toolsets[].tools[] = {available, needs_target, reason, tool}`，17 toolset）。`craft-anything/README.md` **全文没有 `capabilities` / `--filter` / `--output-format` / `analysis_mode`** → 这层能力面连官方 README 都没记载，只有实机 `--help` 能发现，实证「发现式、不硬编码」。
- **不可用性 3 种成因** [INFERENCE 归纳，底层 FACT]：① 文档状态门（PhotoCraft `enabled=false, disabledReason="no document open"`，打开文档后即可用）；② 策略/模式门（CA `analysis_mode="clean-room"` → 224/321 不可用，需 `--deep` / `settings_set deep_mode true`）；③ 依赖缺失门（外部引擎未装，未实机确认）。→ **discovery 时不要按 `enabled/available` 剪枝缓存**，缓存全表 + 每次调用前重探或读 `reason`（脱敏后透给用户）。
- **schema 方言 4 种**：PhotoCraft camelCase 8-key（`params` 是字符串 mini-schema）/ CA 扁平 6-key / PdfCraft snake_case JSON-Schema 工具表 / VectorCraft 带 params doc 的 catalogue → **字段级 per-app 映射**，不能共用一份 dataclass。
- **响应封套不统一**：CA `{"result":...}` / `{"error":{category,code,message,remediation,retryable,details}}`（含 **`retryable`** 可驱动重试）；PhotoCraft 控制通道 `{"id":N,"ok":true,"result":{...}}` JSON-lines；PhotoCraft `run` 每条命令一行 JSON → 每 adapter 独立 response parser；`retryable` 与「只读重试一次、编辑类绝不重发」对齐。
- **README 数字不可作设计依据**（再添一例）：CA README 写 320 tools，实机 `commands --json` = **321**；PhotoCraft README 500+ / 实机 993。
- **未核验项销项**：3 个本机 CLI 的真实安装路径/版本/命令表结构/可用性来源已实机核实；其余 13 个 app 的 Windows `*-cli.exe` 仍未核验。`capabilities --json` 是否 CA 独有、其他 app 是否也有 → 新增未核验。

### §16.5 测试最终口径：**58 条**（不再变更）

在 §15.7 的 56 条基础上再增 2 条：
57. `test_plugin_toml_sdk_fields`：断言 `[plugin.sdk]` 含 `recommended`/`supported`、**不含 `min_version`**；`[plugin_runtime] enabled=true` 存在；业务段在顶层；`[[plugin.entries]]` 每条只含 `id`+`name`。
58. `test_llm_tool_timeout_set`：5 个 `@llm_tool` 全部显式设 `timeout`（≥60 或 15/10），与对应 `@plugin_entry` 的 timeout 一致或各自合规。

`#45` 对账测试按 §16.3 升为**必需**且以 `plugin.toml` 为准；`__llm_tool__*` 是动态 id 假阳性，不算缺失。

**⚠️ 口径冻结**：`ARCHITECTURE-FINAL.md` §0–§16 为最终版，实现与测试一律以此为准，不再接受增量修订。

---

## §17 字段级实证补丁（真 SDK 端到端验证，架构结构不变，§0–§16 冻结仍有效）

来源：neko-plugin-dev §12 九处字段核验 —— **真 SDK 内存 import + `tomllib` 解析 + `validate_plugin_config()` + CLI 校验器 + `collect_entries()` / `_register_decorated_llm_tools()` 实跑**，非推断。**本节只修字段与写法，不改任何模块划分、数据结构或状态机。** 优先级最高。

### §17.1 两处致命漏项（规格 §9-A 原草案会被直接拒载）

| # | 原草案 | 真 SDK 实测 | 正确写法 | 出处 |
|---|---|---|---|---|
| 10 | `[plugin]` **没有 `entry`** | `ConfigValidationError 字段必填` / CLI `ERROR [plugin].entry must be a non-empty string` | `entry = "plugin.plugins.artcraft_ops:ArtcraftOpsPlugin"`（**必填**，格式 `module:Class`） | `schema.py:212-213,260`、`validate_cmd.py:174`、`entry_points.py:15-44` |
| 11 | `author = "neko-artcraft-ops"`（字符串） | `ConfigValidationError: Input should be a valid dictionary or instance of PluginAuthorSchema` / CLI `ERROR [plugin.author] must be a table` | `[plugin.author]` **表** + `name = "neko-artcraft-ops"`（可整段省略） | `schema.py:62-66,222`、`validate_cmd.py:377` |

另两条会崩/会错的写法：
- **`@lifecycle(id="startup", startup_failure="warn")` 写错即崩**：真 SDK 实测 `TypeError: lifecycle() got an unexpected keyword argument 'startup_failure'`。正确：`@lifecycle(id="startup")`；`startup_failure = "warn"` 写进 **`[plugin_runtime]`**（合法值 `warn|fail|ignore`，`schema.py:270`）。`@lifecycle` 合法 id：`startup/shutdown/reload/freeze/unfreeze/config_change`（`decorators.py:127-134`）。
- **入口与生命周期必须是类方法**：规格 §1 写成模块级 `async def on_startup(plugin)` 是**错的**。宿主只从 `instance.collect_entries()` 收集（`host.py:1204-1226`、`shared/core/base.py:203-217` 用 `getmembers_static(type(self))`），模块级函数**永远不注册**。`super().__init__(ctx)` 必须第一行（末尾自动注册 `@llm_tool`，`base.py:74-87`）。

### §17.2 九处核验结论（浓缩）

| # | 结论 | 出处 |
|---|---|---|
| 1 | `[[plugin.entries]].id` **必填**且必须 == `@plugin_entry(id=...)`；`name` 可选（显示名，缺失只是空串） | `registry.py:650-657,879-891` |
| 2 | `[plugin.sdk]` 只允许 `recommended`/`supported`/`untested`/`conflicts`；宿主 `SDK_VERSION="0.1.0"`（`plugin/_types/version.py:9`）。`supported` 不匹配→**拒载**（`registry.py:1157-1162`）、`conflicts` 命中→拒载（`:1149-1153`）、`recommended` 不匹配仅 warning（`:1165`）。**别把 app 版本 0.9.1 当 SDK 版本** | `schema.py:69-74`、`validate_cmd.py:388` |
| 3 | `@plugin_entry` 完整签名：`id/name/description/input_schema/params/kind/auto_start/persist/model_validate/timeout/llm_result_fields/llm_result_model/fields/metadata/quick_action`；`input_schema` 与 `params` 互斥、`llm_result_fields`/`llm_result_model`/`fields` 三者互斥；`llm_result_fields` 必须是字符串序列（混入非 str → `TypeError`） | `decorators.py:88-105`、`shared/core/decorators.py:360-455,374-382`、`result_contract.py:173-189` |
| 3b | 装饰器 `timeout` 无 per-entry 上限；`<=0`/`None`=不限时；生效值 `min(声明值, 调用方请求值)`。全局 `PLUGIN_TRIGGER_TIMEOUT` ≤3600、**LLM 工具注册硬上限 300s**。`timeout=60.0` 安全，别给 LLM 工具写 >300 | `entry_runtime.py:99-106`、`entry_executor.py:73-87`、`settings.py:778-790`、`tool_router.py:183` |
| 3c | **「清单覆盖装饰器」是一条通用规则，不止 `llm_result_fields`（2026-11-08 实证补正）**：`registry.py:685-696` 先取装饰器契约 `entry_contract_fields(base_meta)`，再 `controls.update((n,v) for n,v in declared.items() if n not in _ENTRY_DISPLAY_FIELDS)` 后 `setattr` 回写；`_ENTRY_DISPLAY_FIELDS = ("name","description","input_schema")`（`:894`）→ **除这三个字段外，清单声明一律覆盖装饰器**。`entry_metadata.py:13-28` 的 `ENTRY_CONTRACT_FIELDS` 明确含 `timeout`，`registry.py:902-932` 规范化时也保留它，故 `[[plugin.entries]].timeout` **会覆盖** `@plugin_entry(timeout=)`。宿主作者自己的注释反证在 `registry.py:545`：「Missing fields must inherit decorators: **manifest-only timeout overrides** previously erased name/description/input_schema」。**实践规则：两边写同一值**（本插件 5 条入口全部同值：search 15/15、run 10/10、get_job 15/15、cancel 15/15、list_apps 10/10），哪一侧生效结果都一致。⚠️ 运行时覆盖**未能观测**：本机 `import plugin.core.registry` 缺 `ormsgpack` 的 cp313 轮子（§15.6），沙盒 `inspect` 走 `collect_entries()` 只读装饰器。故本条为**源码级 [FACT]、运行时 [INFERENCE]**。 | `registry.py:545,685-696,894,902-932`、`entry_metadata.py:13-28` |
| 4 | `llm_result_fields` 清单与装饰器**都合法**，但**清单值覆盖装饰器值**（`registry.py:683-690`）→ 建议只在装饰器写，清单保持 `id`+`name` | `entry_metadata.py:11-26`、`registry.py:894-925` |
| 5 | 装饰器叠放**顺序无关**（真 SDK 实测两种顺序结果相同）；两条路径 timeout 独立都要设；`[[plugin.entries]]` **绝不能**列 `__llm_tool__*`（动态注册 id）；子类自定义 `@lifecycle` 不干扰基类自动注册（基类无 startup hook） | `shared/core/decorators.py:248-252`、`llm_tool.py:225-256`、`base.py:46-87,517-545,629-637` |
| 6 | `[plugin.store] enabled=true` 合法；`[plugin_runtime]` **只允许** `enabled/auto_start/priority/timeout/startup_failure` 五键，业务段严禁放其下（多余键被 pydantic 丢弃 + WARNING），必须**顶层**（`PluginConfigSchema extra="allow"`） | `schema.py:77-81,264-282,305`、`validate_cmd.py:712`、`base_runtime.py:100-102` |
| 7 | 见 §17.1（`startup_failure` 属清单不属装饰器） | `decorators.py:127-134`、`lifecycle_service.py:912-920,1243-1245` |
| 8 | `type = "plugin"` 合法但可省（`PluginSectionSchema.type` 默认 `"plugin"`）；`PluginType = Literal["plugin","adapter"]`，写别的值→拒载 | `plugin_types.py:8-11,29-34`、`schema.py:217,241` |
| 9 | `data_path(*parts) -> Path`（`base.py:135-137`）、`cache_path(*parts)`（`:139-141`）、`runtime_config_path` → `.../config/plugin.toml`（`:131-133`）、`config_dir` 是 `plugin_dir` 别名（`:121-123`）；**`config.dump(*, timeout=5.0)` 是 async**（`shared/core/config.py:177`），漏 await 拿到协程；写文件前 `.parent.mkdir(parents=True, exist_ok=True)` | 同上 |

补充：`short_description`/`keywords` 在 `[plugin]` 下合法（pydantic 忽略但不报错）；`config.example.toml` 严禁 `[plugin]`（`validate_cmd.py:126-141`）。

### §17.3 修正后完整 `plugin.toml`（真 SDK 验证：pydantic OK / CLI **0 error** / 1 已知 warning）

```toml
[plugin]
id = "artcraft_ops"                        # 必须 == 目录名
name = "ArtCraft 全家桶操控"
description = "用一句话操控本机 ArtCraft 系列软件"
short_description = "操控 ArtCraft 系列软件"
keywords = ["修图", "调亮", "图层", "导出", "锐化", "PhotoCraft", "VectorCraft", "Craft Anything"]
version = "1.0.0"
type = "plugin"                            # 可省，默认就是 plugin
entry = "plugin.plugins.artcraft_ops:ArtcraftOpsPlugin"   # ★ 必填

[plugin.author]                            # ★ 必须是表，不能写字符串
name = "neko-artcraft-ops"

[plugin.sdk]                               # ★ SDK_VERSION=0.1.0，不是 app 的 0.9.1
recommended = ">=0.1.0,<0.2.0"
supported   = ">=0.1.0,<0.3.0"

[plugin.store]
enabled = true

[[plugin.entries]]
id = "artcraft_ops_search_commands"
name = "检索命令"

[[plugin.entries]]
id = "artcraft_ops_run_sequence"
name = "执行序列"

[[plugin.entries]]
id = "artcraft_ops_status"
name = "查询任务状态"

[[plugin.entries]]
id = "artcraft_ops_cancel"
name = "取消任务"

[[plugin.entries]]
id = "artcraft_ops_list_apps"
name = "列出本机 App"

[plugin_runtime]
enabled = true
auto_start = false
startup_failure = "warn"                 # ★ 属于清单，不属于 @lifecycle 装饰器
timeout = 30                             # startup 超时（≤300），不是入口超时
# 业务段一律放 config.example.toml 顶层，不要挂在本段下面
```
已知噪音：CLI 校验器会报 `WARNING [plugin].entries is not a recognized plugin.toml field`（`allowed_plugin_keys` 缺 `entries`，全文 0 处校验）。运行时由 `registry.py:879-891` 从原始 toml 读且 plugin.toml 优先于 plugin.meta.json；**字段拼错零报错 → #45 AST 对账是唯一兜底**。

### §17.4 `__init__.py` 装饰器骨架（真 SDK 实跑：5 普通入口 + 2 lifecycle + 5 动态工具 = 12 id，无冲突）

```python
from typing import Any
from plugin.sdk.plugin import (
    Err, NekoPluginBase, Ok, SdkError,
    lifecycle, llm_tool, neko_plugin, plugin_entry,
)

@neko_plugin
class ArtcraftOpsPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)          # 必须第一行；末尾自动注册 @llm_tool

    @lifecycle(id="startup")          # ★ 不接受 startup_failure
    async def on_startup(self, **_): ...
    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_): ...

    @plugin_entry(id="artcraft_ops_search_commands", name="检索命令", timeout=60.0,
                  llm_result_fields=["commands", "app", "version", "total_matched", "hint"])
    @llm_tool(name="artcraft_ops_search_commands", parameters=SEARCH_SCHEMA,
              description="...", timeout=60.0)
    async def search_commands(self, query: Any = None, app: Any = None,
                              top_k: Any = None, **_): ...

    @plugin_entry(id="artcraft_ops_run_sequence", name="执行序列", timeout=10.0,
                  llm_result_fields=["job_id", "status", "poll_with", "cancel_with"])
    @llm_tool(name="artcraft_ops_run_sequence", parameters=RUN_SCHEMA,
              description="...", timeout=30.0)
    async def run_sequence(self, app: Any = None, steps: Any = None,
                           input_file: Any = None, new_doc: Any = None,
                           out_file: Any = None, inspect: Any = None, **_): ...

    @plugin_entry(id="artcraft_ops_status", name="查询任务状态", timeout=15.0)
    @llm_tool(name="artcraft_ops_status", parameters=STATUS_SCHEMA,
              description="...", timeout=30.0)
    async def status(self, job_id: Any = None, **_): ...

    @plugin_entry(id="artcraft_ops_cancel", name="取消任务", timeout=15.0)
    @llm_tool(name="artcraft_ops_cancel", parameters=CANCEL_SCHEMA,
              description="...", timeout=30.0)
    async def cancel(self, job_id: Any = None, **_): ...

    @plugin_entry(id="artcraft_ops_list_apps", name="列出本机 App", timeout=10.0)
    @llm_tool(name="artcraft_ops_list_apps", parameters=LIST_APPS_SCHEMA,
              description="...", timeout=30.0)
    async def list_apps(self, **_): ...
```
三条硬约束：**① 全部是类方法**（模块级函数不注册）；**② 全部 `async def`**（同步入口被 `entry_executor.py:106-109` 直接拒）；**③ 签名吃 `**_`**（`entry_runtime.py:17-22,197-202`）。

### §17.5 测试口径（**总数仍 58 条，不新增编号**）

把 §17.1–§17.4 的断言**并入既有 #57 与 #45**，避免测试数反复变动：
- `#57 test_plugin_toml_sdk_fields` 增强：断言 `[plugin].entry` 存在且格式 `module:Class`；`[plugin.author]` 是表（不是字符串）；`[plugin.sdk]` 无 `min_version`；`[plugin_runtime]` 五键合法且 `startup_failure` 在此；`@lifecycle` 装饰器**不含** `startup_failure` 参数；5 个入口是**类方法**且 `async def` 且吃 `**_`。
- `#45 AST 对账` 增强：以 **§17.3 的 plugin.toml 为准**，5 个 `id` 与 5 个 `@plugin_entry(id=...)` 完全相等；`__llm_tool__*` 动态 id 不算缺失。
- `#58 test_llm_tool_timeout_set` 不变。

**测试总数最终 = 58 条。**

### §17.6 版本口径（无需两套清单）

安装版 exe（2026-10-31，pre-#3638）经**同代源码副本** `C:\Users\54301\AppData\Local\N.E.K.O\plugins\_host_sdk\plugin\` 逐文件比对：`sdk/plugin/decorators.py` 逐字节相同；`config/schema.py` 仅注释/文档字符串差异、字段定义完全一致；`base.py`/`llm_tool.py` 仅多 `reply_tail` 参数与注释翻译。上游 `645da54` 与本地 `8d27877` 的 `plugin/sdk/**`、`plugin/core/**`、`config/schema.py` 除 2 个 hosted-ui 文件外逐字节一致。→ **§17 对安装版、本地源码、上游最新同时成立，不写两套清单。** 唯一差异是开发态注册（A vs B），与清单字段无关，按 §16.3 走路径 C。

**证据分级**：§17 全部 [FACT]（真 SDK 运行输出 / 源码行号）；仅「缺 `[[plugin.entries]]` 必导致 refresh 后 404」的精确机制为 [INFERENCE]（源码有装饰器扫描兜底 `registry.py:610-649`，但 refresh 先 `_remove_scanned_metadata` `:1770,1855,2335`，面板预览来自 `_effective_entries` `:1054-1077`）。保守规则不变：5 个 id 全部声明。

---

## §18 真宿主验收记录（2026-11-08，全部 [FACT]）

**宿主**：Steam 冻结版，`E:\Program Files (x86)\Steam\steamapps\common\n.e.k.o`（此前记录的 `C:` 盘路径有误，以此为准）。
**用户插件根**：`C:\Users\54301\AppData\Local\n.e.k.o\`（`.neko-plugin-installations\plugins\` 为安装副本，`plugins\` 为运行态 data/cache/config）。
**端口**：`MAIN=48911 / MEMORY=48912 / TOOL=48915 / USER_PLUGIN=48916`（`AppData\Roaming\n.e.k.o\port_config.json`）。

### §18.1 不重启即可注册（推翻「必须重启」的旧结论）

宿主 `USER_PLUGIN_SERVER` 暴露 `POST /plugins/refresh`，可原地重扫安装目录：

```
POST /plugins/refresh → 200, 2.2s
{"success":true,
 "added":[…,"artcraft_ops",…],       # 55 个
 "failed":[],                        # 关键：零失败
 "scanned_count":55}
```

→ **无需重启 N.E.K.O**。`POST /plugin/{id}/start` 即可拉起：

```
{"success":true,"plugin_id":"artcraft_ops",
 "message":"Plugin started successfully","preference_persisted":true}
```

`GET /plugin/status?plugin_id=artcraft_ops` → `{"status":{"status":"running"}}`。

**注**：`plugins.lock.json` 是**安装台账**（`channel`/`source`/`installed_at`），由 `/plugin-cli/install` 写；手工复制文件不产生台账条目。发现走**目录扫描**（`scanned_count:55` 可证），故台账缺失不影响发现与运行。若要面板显示「已安装」来源，应走 `/plugin-cli/install`。

### §18.2 worker 内入口收集（与本地 stub 复核完全一致）

`GET /plugin/artcraft_ops/logs` 取到 worker 进程自己打的日志：

```
[Plugin Process] Starting plugin 'artcraft_ops' from plugins.artcraft_ops:ArtcraftOpsPlugin
Plugin entries collected via collect_entries(): [
  'artcraft_ops_cancel_job', 'artcraft_ops_get_job', 'artcraft_ops_list_apps',
  'artcraft_ops_run_sequence', 'artcraft_ops_search_commands',
  'shutdown', 'startup',
  '__llm_tool__artcraft_ops_cancel_job', '__llm_tool__artcraft_ops_get_job',
  '__llm_tool__artcraft_ops_list_apps', '__llm_tool__artcraft_ops_run_sequence',
  '__llm_tool__artcraft_ops_search_commands']
```

**12 条**，与 §17.1 的本地真 SDK 复核一致；`N.E.K.O_Plugin_artcraft_ops_error.log` **0 字节**。

### §18.3 入口可被宿主真实调用（推翻「worker 子进程被沙箱拦」的 Phase 1 悬案）

入口分发接口 = `POST /runs`，契约 `RunCreateRequest{plugin_id, entry_id, args}` → `{run_id}`，结果经 `GET /runs/{id}/export` 取 `items[0].json`。

| 入口 | args | 结果 |
|---|---|---|
| `list_apps` | `{}` | `succeeded`，`data.apps` 3 条，`cli_path_found:true` ×3 |
| `search_commands` | `{app:photocraft, query:export, limit:5}` | `succeeded`，返回真实命令目录（含 `params_schema`） |
| `run_sequence` | photocraft / 5 步 / `out_file` | `succeeded`，`exit_code 0`，产出 `640×400` PNG 2053 B（`magic`+`IHDR` 校验通过） |
| `run_sequence` | vectorcraft / 1 步 / `in`+`export` | `succeeded`，`exit_code 0`，产出有效 SVG 369 B |
| `get_job` | 上述 job_id | `succeeded`，`ok:true`，`error_code:""` |

PhotoCraft 5 步跑同时验证了 **stderr 捕获修复**：`warnings: ["warning: 3 layer(s) flattened; layers, masks and blend modes are not kept"]` —— 这条来自 stderr，`runner.py` 未修前必丢。

→ **子进程在真实 worker 里可正常 spawn**，`asyncio.create_subprocess_exec` 无沙箱拦截，路径解析与 `cwd`（app 安装根）在冻结 Python 环境里均正确。

### §18.4 已知低危残留（记录，不返工）

`jobs.py:315-318` 的 `_on_line` 按 **stdout 行数**递增 `step_done`，而 `step_total` 按 **命令数**（`result.command_step_count`）——单位不一致。

| app | stdout_lines | step_total | step_done | 说明 |
|---|---|---|---|---|
| photocraft | 3 | 3 | 3 | 巧合对齐（1 行 = 1 命令） |
| photocraft | 5 | 5 | 5 | 同上 |
| vectorcraft | 3 | **1** | **3** | 转录 3 行 / 1 命令 → `3/1` |

**影响面**：`step_done` 不出现在 `get_job` 视图（`spec.py:255` 白名单无此字段），仅用于 `progress = min(0.99, step_done/step_total)`。多命令 VectorCraft 序列的进度会**偏快**（例如 5 命令 15 行，跑到第 2 条命令就 99%）。不会越界、不会卡死、完成时必到 100%。

**为何不修**：修正需区分「转录行」与「命令行」，而 Craft Anything 单行**无 `command` 键**，增量判定无法复用 `command_step_count` 的 `count or len(items)` 回退。属于进度粒度问题，非功能缺陷；临近交付做此改动收益低于回归风险。**留作 v1.1 候选：把 `step_total` 语义拆为 `command_total`（用户文案）+ `line_total`（进度），或改为按含 `command` 键的行计数并在首行判定 app 类型。**




lazy discovery、动态命令发现、`plugin.toml` 运行时配置、roots 按模式分置、字面 `127.0.0.1` + `trust_env=False`、Popen 生命周期、job 轮询与显式 cancel、`timeout=60.0` + `asyncio.to_thread`、512 KiB 消息面走句柄、WASM 不可做插件、3 个成熟骨架适配、两条证据链正交 —— **全部不变**。


## §19 全 17 app 契约矩阵 + Studio 本地 GUI 通道（2026-11-08，全部 [FACT]）

§18 记录的是「3 个 app 在真宿主里跑通」。本节记录**扩到全 17 个**之后的事实，
并新增第 6 个入口。§18.2 里的「12 条 entry」由 §19.4 的 **14 条**取代。

### §19.1 「一套 flag 打天下」是错的：17 个 app 分 5 个 argv 族

早期设计把 17 个 app 当成「同一套 `--cmd/--params/--out` 的不同取值」，这是**根本性误判**。
实机 `--help` 逐条比对后，argv 构造分 5 个互不兼容的族：

| 族 | 形态 | 成员 |
|---|---|---|
| A `run_cmd` | `run --new JSON --cmd ID --params JSON --out F` | PhotoCraft、VectorCraft |
| B `cmd_inline` | `run --cmd 'ID={json}' --out F` | SoundCraft、DeckCraft、DesignCraft、GridCraft、WordCraft、**CADCraft** |
| C `cmd_positional` | `run <input> <command-id> [params-json] [-o out]` | TextureCraft、MaterialCraft、EffectCraft |
| D `script_file` | `run <script.json>`（多步必须落脚本文件） | FigCraft、FilmCraft、SolidCraft、LightCraft、PdfCraft |
| E `tool_subcmd` | `<tool> --json --project F`（**没有 `run`**） | Craft Anything |

三个族内例外（全部踩过、全部有单测钉死）：

* **CADCraft** 的 `--cmd` 取值是 `id + 空格 + JSON`，**不是** `=`；
* **TextureCraft / MaterialCraft** 的 `run <input> <command-id>` 在没有输入文件时会把
  命令 id 拼进 `<input>` 槽 → `missing <command-id>`。故 `AppSpec.requires_input` 存在，
  且 argv 构造对无输入的 positional 调用 **fail-closed**；
* **VectorCraft 没有 `--new`**（旧表写着 `--new`，从未被触发过，触发即 `unknown run option`）。

`cmd_join="kv"`（LightCraft / PdfCraft）**不参与 argv**，只用于脚本序列化；
所以 `ARGV_BUILDERS` 是 5 条，不是 6 条。

### §19.2 命令目录有 4 种形态，子命令名也不统一

| 形态 | 调用 | 成员 |
|---|---|---|
| `json_flag` | `commands --json` | 12 个 |
| `json_default` | `commands`（**加 `--json` 反被拒或返回 `[]`**） | CADCraft、MaterialCraft、DesignCraft |
| `plain` | 人类可读文本，需按破折号/多空格分栏解析 | DeckCraft、SoundCraft、TextureCraft、EffectCraft |
| `none` | **没有 `commands`** | PdfCraft → 子命令叫 **`tools`** |

`designcraft commands --json` 返回 `[]`、不带 flag 才吐 JSON —— 这是最反直觉的一条。

脚本格式也有 5 种：`array_cmd` / `array_id` / `jsonl_cmd` / `jsonl_id` / `array_tool`。
**LightCraft 的 `--script` 只吃 JSON Lines**（缩进数组直接 `坏文件`）。

### §19.3 唯一真相源：删掉第二张名单才修好

`locator.py` 原本有一张硬编码 `_CLI_NAMES`（3 个 app），与 `app_registry.BUILTIN_SPECS`
构成**两份真相**。注册表扩到 17 后，**14 个 app 全部 `APP_NOT_FOUND`**，而单元测试
**全绿**——因为测试断言的是注册表，运行走的是名单。

修法是**删除**名单、从注册表派生候选路径（`_iter_candidates`）。此后不存在脱节可能。
`tests/test_families17.py::test_locator_derives_all_17_from_registry` 守住这条。

同理，`SEARCH_SCHEMA` / `RUN_SCHEMA` 的 `app.enum` 也曾停在 3 个，等于让模型看不见
另外 14 个 app。已改全量，并由 `test_schema_app_enum_covers_every_registered_app` 钉死。

### §19.4 第 6 个入口：`artcraft_ops_open_in_studio`（本地 GUI 交接）

Studio 是 **L4** 应用：安装目录只有 `artcraft.exe`（77,455,360 B）+ 卸载快捷方式，
**没有 CLI 二进制**，所以无法进 `APP_SPECS` 的 `CLI_RUN` 通道。降级策略写死在
`studio_local.py` 里，与只做云端的 `studio_channel.py` **分开**：

| 边界 | 实现 |
|---|---|
| **不做 UI 自动化** | 依赖面只有 `os` / `subprocess` / `typing`；`tests/test_studio_local.py` 用 AST 查 import 守卫 |
| **不接受调用方传 exe** | `STUDIO_SCHEMA` 只有 `files`；exe 来自 `[artcraft.studio] exe_path` 或探测。否则等于任意代码执行 |
| **文件 fail-closed** | 逐个过 `roots.resolve_and_check(...,"read")` + 必须已存在 + 常规文件 + ≤8 个 |
| **不谎报交付** | `handoff_verified` **恒 False**：Popen 成功 ≠ 窗口内已加载 |

宿主实测（`POST /runs`）：

```
entry_id = artcraft_ops_open_in_studio
args     = {"files": ["…/plugins/artcraft_ops/data/studio_handoff.png"]}
→ status = succeeded
→ data   = {launched: true, studio_available: true,
            exe_path: "C://Program Files//ArtCraft//artcraft.exe",
            file_count: 1, handoff_verified: false, note: "…"}
```
```
Get-Process artcraft → Id 2480, StartTime 2026/11/8 11:31:35,
                        MainWindowTitle "ArtCraft"
```

**进程确由本次调用产生**（StartTime 与调用时刻逐秒吻合），但**标题里没有文件名**，
所以「文件已被 Studio 打开」**未证实** —— 这正是 `handoff_verified: false` 的含义。

入口注册结果（`GET /plugins/artcraft_ops` → `plugin.entries`）：**14 条**
= 6 个面板入口 + 6 个 `__llm_tool__*` + `startup` / `shutdown`。
`refresh` 只重读清单、**不重建运行实例**，所以 `@llm_tool` 的动态注册必须
`stop → start` 才会出现（只 `refresh` 时会出现「面板有、LLM 工具没有」的错位）。

拒绝路径同样验证过（不经提示直接给 root 外路径）：

```
args = {"files": ["C:/Windows/win.ini"]}
→ status = failed
→ PATH_OUTSIDE_ROOTS: 文件不在已授权目录内：C:/Windows/win.ini
```

### §19.5 实测覆盖（`probe/verify_17_apps.py`）

| 项 | 结果 |
|---|---|
| CLI 定位 | **17/17** |
| 命令目录发现 | **17/17**（993/933/760/717/656/636/570/555/479/457/389/385/331/321/172/99/86 条） |
| 真实执行 | **13 PASS** |
| 探针假阴性（已另路验证） | 2：craft_anything（走 jobs 路径 3/3）、pdfcraft（`doc_open` 返回 `{pages:1}`） |
| 缺素材 n/a | 2：texturecraft / materialcraft（`run <input> <cmd>` 必需输入文件，探针不造假素材） |

单元测试基线：**197 passed / 1 skipped**（§19.5 的 188 之后，§19.6 又 +9）。

### §19.6 安全配置注入：`active_policy()` 成为入口唯一构造函数（2026-11-08，已修）

**原缺陷**：入口层两处都写 `roots_mod.default_policy(data_dir, cache_dir, {})`，第三个参数是
**空字典** → `[artcraft.security] allow_user_dirs` / `user_read_roots` 被读了、进了
`Settings.security`、却从未到达任何一次校验。结果是用户配了也不生效，只能操作插件
`data/`、`cache/` 内的文件。这是 §14 fail-closed 姿态的副作用，**不是安全漏洞**（方向偏严）。

**修法不是给两处各补一个 dict**（下次新增入口还会漏），而是把「当前生效策略」收敛成
一个具名函数：

```python
roots.configure_security(settings.security)   # startup 注入一次，非 dict 一律清空
roots.active_policy(data_dir, cache_dir)      # 入口层只准调这个
```

想绕开就必须显式写 `default_policy(..., {})`，代码里一眼能看出来。

三个附带的 fail-closed 收紧（都在 `configure_security` 里）：

| 项 | 行为 |
|---|---|
| 非 dict 配置（None / list / 字符串） | 清空，**绝不放宽** |
| `allow_user_dirs = "false"` | 用 `_as_bool` 判假；内置 `bool("false")` 是 **True**，等于反向放开 |
| 相对路径 / 空串 / 重复项 | 丢弃（相对 root 与绝对路径比对永远 False，留着只会让人以为已授权） |

写进策略的 root 统一是 `normalize_win_path` 归一后的形态（正斜杠），日志与错误里只有一种拼写。

**宿主实测（双向对照）**：

| 步骤 | 调用 | 结果 |
|---|---|---|
| 配置 `allow_user_dirs=false` | `run_sequence(input_file=…/Documents/artcraft_probe/user_pic.png)` | `failed` + `PATH_OUTSIDE_ROOTS: 文件不在已授权目录内：…` |
| 改 `true` + `user_read_roots=[那个目录]`，`stop → start` | 同上 | `job-23d051a66f02` → `succeeded`，`exit_code: 0` |
| 同上，验证真读到文件 | `image.imageSize{width:60,height:40}` + `out_file` | 输入 **640x400** → 输出 **60x40**（`data/userroot_out.png`，561 B） |
| 同上，另一处调用点 | `open_in_studio(files=[那个 png])` | `succeeded`，`launched:true`、`file_count:1`；`Get-Process artcraft` → PID 6228 @ 11:44:57 |
| 回退 `false` + `stop → start` | 再跑第一步 | 重新 `failed` + `PATH_OUTSIDE_ROOTS` |

尺寸从 640x400 变成 60x40 是**决定性证据**：CLI 真的读到了插件 `data/` 之外的文件，
不需要传 `--automation-*-root`（`build_root_flags` 至今无人调用，只是死代码）。

**边界警告**：`_is_within` 是纯文本前缀比对，`user_read_roots = ["C:\\"]` 等价于放开整块盘。
这是配置文件里的显式选择，代码**不夹紧**（静默夹紧比放开更危险），只在
`config.example.toml` 里写明。`allow_user_dirs` 与 `user_read_roots` 是**双开关**，缺一不生效；
改完必须 `stop → start`（startup 才重跑注入）。

**原「仍未覆盖的相邻缺口」→ 已在 §19.7 修**（2026-11-08 同一日）：`sequence.validate_params`
当时在 `validate_steps` 里是**不带 roots** 调用的，step `params` 里的 `path`/`file` 类字段
没有走 roots 校验；`--new` 那条 JSON 通道更是完全没过校验。两处同族（「形参吞掉」），
修法与实测见下一节。

### §19.7 step params 与 `--new` 两条 LLM 可控通道的路径闸门（2026-11-08，已修）

**缺陷族**：同一族的「形参收进来了、没往下传」。

| 位置 | 缺陷 | 后果 |
|---|---|---|
| `sequence.validate_steps` → `validate_params(step.params or {})` | 不传 `roots` | step `params` 里的 `path`/`file` 字段**完全不过 roots**，而入口层的 `input_file`/`out_file` 是过的 |
| 入口 `run_sequence(new_doc=…)` | 只过 `isinstance(dict)` 就 `json.dumps` 进 argv | `--new` 是**第二条**模型可控 JSON 通道，`{"profile": "C://Windows//win.ini"}` 一次校验都不走 |
| `validate_tool_kwargs(spec, policy, kwargs, roots, …)` | 收了 `roots` 却不下传，且全仓零调用点 | 死代码，撑不起任何闸门 |

**修法**（`artcraft_ops/sequence.py` + `artcraft_ops/__init__.py`）：

```python
validate_params(params, roots=None, direction="any", extra_path_keys=())
validate_steps(steps, spec, policy, catalog_ids=…, roots=None)   # 追加在末尾且带默认值
```

`roots` 追加在末尾、带默认值，是为了让现有 3 参 / 4 位置参调用点原样可用 —— 否则
「接线一次就 TypeError」，正是这一族缺陷的另一半。`roots=None` 时整段路径检查跳过，
保持旧行为；roots 必须是显式递进来的责任，不设全局默认。

**判定链**（`_check_params_paths` 递归下降 → `_check_one_param_path` 单值判定）：

| 值的形状 | 处置 |
|---|---|
| 盘符绝对或盘符相对（`C:/x`、`C:secret.png`） | 送 `roots.resolve_and_check` |
| 单分隔符开头（`\Windows\win.ini`、`/Windows/win.ini`，当前盘根） | 同上 |
| UNC（`\\server\share\x`）与设备前缀（`\??\`、`\\?\`、`\\.\`） | 同上（`\\.\` 走 `PATH_UNSAFE`，其余走 `PATH_OUTSIDE_ROOTS`） |
| 相对且含 `..` 段（含 `".. "` 空白变体） | 直接 `_violate`（`POLICY_VIOLATION`） |
| 普通相对名 / SVG path data / `layer:3` / `R:1` / URL / 8.3 短名 | 放行 |

`..` 必须在**这里**拒，不能丢给 roots：CLI 的 cwd 是**安装目录**（`jobs._cwd_for()` 返回
`E:/Program Files\Artcraft\PhotoCraft`），`../../secret.png` 拼出去是 Program Files 的上两级，
压根不经过 roots 的比对口径。空白变体（`".. "` 等）属**超额拦截**：实测只有字面 `..`
落父目录，`".. "`/`" .."`/`".. ."` 经 `os.stat` 都落自身 —— 这些段在 NTFS 上本来就打不开，
拒掉没有可用性代价。

**键名识别**：`_key_tokens` 先按驼峰断点（`(?<=[a-z0-9])(?=[A-Z])`）拆，再按非字母数字切分，
另补①去复数（`files`→`file`）②整键压缩（`pAtH`→`path`）。**不用**裸 substring：`profile`
含 `file`、`south` 含 `out`，会把正常参数误判成路径字段。词表 21 个 token
（`input/src/source/output/out/dest/save/export/path/file/target/dir/directory/folder/dst/
filename/filepath/log/template/mesh/into`），方向由 `_PATH_KEY_DIRECTION` 决定，冲突取并集。
父 key 命中路径词表时，其下**整棵 dict 子树继承**父词元（否则 `{"path": {"x": "C://…"}}`
是等价旁路）。深度上限 16，**只对容器计深**，超过直接拒（fail-closed）。

**`--new` 通道用「显式补键」而不是「任何字符串都按路径判」**：
第一次的实现是 `all_strings=True`，对整段 payload 用绝对路径判据 —— 复核实测
`new_doc={"name": "A: study"}` 被按盘符判据拒掉、报错还谎称 `PATH_OUTSIDE_ROOTS`。
改成 `extra_path_keys=NEW_DOC_PATH_KEYS`（`frozenset({"profile"})`）：

| payload | 结果 |
|---|---|
| `{"name": "A: study"}` | 放行（文档名不是路径）|
| `{"profile": "C://Windows//win.ini"}` | 拒（`PATH_OUTSIDE_ROOTS`）|
| `{"profile": "<data>\\in.png"}` | 放行 |

通道面已核：只有 3 个 app 的建文档 flag 收 JSON —— `photocraft` 的 `--new`（`app_registry.py:63`）、
`designcraft`/`gridcraft` 的 `--sample`（`:110`/`:119`）；后者若要路径是 `template`，本就在通用词表里。

**critical C1：单分隔符 root-relative 必须两处一起改**：

| 只改哪一处 | 结果 |
|---|---|
| 只改 `sequence.py` 判据 | **无效修复**：`roots.normalize_win_path` 会把 `\x` 当相对路径拼进 root 并返回 ALLOWED（假闸门）|
| 只改 `roots.py` 的 `is_abs` | 仍无效：`sequence` 那条分支走不到 `resolve_and_check` |
| 两处都改（`sequence.py` 的 `text[:1] in ("\\", "/")` + `roots.py` 的 `startswith("/")`，且后者在反斜杠统一替换之后）| 真闸门 |

**变异矩阵**（每条都是独立回退其余不动，证明测试非恒绿）：

| 变异 | 打红 |
|---|---|
| `roots.py` `startswith("/")` → `startswith("//")` | `#21a`（test_params_roots）+ `#21b`（test_roots）**双红** |
| `sequence.py` 去掉 `text[:1] in ("\\", "/")` | `#21a` 红 |
| `validate_params` 的 `all_strings`/补键默认改成 True / `("name",)` | `#26` 第二半红（`A: study` 被误杀）|
| `NEW_DOC_PATH_KEYS` 清空 | `test_new_doc_is_second_llm_controlled_channel` + `#26` 第三半**双红** |
| `seg.strip(" \t") == ".."` → `seg == ".."` | `#24` 红 |
| `_PATH_KEY_DIRECTION` 去掉 `log/template/mesh/into` | `#22` 红 |

**基线**：**225 passed / 1 skipped**（§19.6 的 197 之后 +28：`test_params_roots.py` 26 条、
`test_roots.py` +1（21b）、`test_entries_e2e.py` +1（`--new` 通道））。`errors.ERROR_CODES`
仍是 19 条固定集合，未新增错误码。

**已知边界**（逐条裁决「不修」，改动前先回到理由）：
1. `{"options": {"name": "C://Windows//win.ini"}}` 放行 —— 非路径 key 的字符串 dict 值不查。
   把任意字符串都当路径会把 SVG path data、`layer:3`、URL、8.3 短名一起误杀；真遇到
   「路径参数用了映射外的 key 名」，应往 `_PATH_KEY_DIRECTION` 补 token，而不是放宽判据。
2. `R:abc`（字母通道名）被当盘符相对拒 —— 不放宽：改成 `(?![0-9A-Za-z])` 会把
   `C:secret.png` 一起放行。
3. `profile` 在 step params 里是**预设名**、在 `--new` 里是**文件路径**，两处语义相反：
   不进通用词表，只由 `NEW_DOC_PATH_KEYS` 在 `--new` 通道单独覆盖。
4. 其余 5 个 `@llm_tool`（`search_commands`/`get_job`/`cancel_job`/`list_apps`/`open_in_studio`）
   仍各自解析入参，未收敛成统一前置校验。
5. `roots.resolve_and_check` 的 `direction` 形参目前不参与判定（只作语义标签）。

**部署**：工作区三个文件逐字节替换宿主
`C:/Users/54301/AppData/Local/N.E.K.O/.neko-plugin-installations/plugins/artcraft_ops/`，
sha256 一致（`sequence.py 8273d26e…`、`roots.py 3e3461e4…`、`__init__.py 370433b4…`），
`__pycache__` 已清；真 SDK 加载宿主副本 → `collect_entries()` **8 条**
（6 个 `artcraft_ops_*` + startup/shutdown），无入口 404。

### §19.8 `profile` / `droplet` / `file:` / URL 白名单（2026-11-08 晚，已修并部署）

§19.7 收口当晚又挖出四条同族缺口，全部属「同一个目标的**另一种拼写**」。

**新增词表项（方向与依据）**

| token | 方向 | 依据（[FACT] schema 原文） |
|---|---|---|
| `profile` | any | `edit.profileInfo` 的参数原文就是 `{"profile":"<builtin id>|document|/path/to/profile.icc"=document}`；另 5 条：`file.new`（`path to an .icc file`）、`image.mode.rgb\|grayscale\|cmyk\|lab` |
| `droplet` | read | `file.automate.runDroplet` 的 `{"droplet": path, "input": [...], "output": folder?}` —— **执行原语**，越界 = 让 CLI 跑盘外脚本 |
| `import` | read | `references.sources` 的 `{"import": path?}`。**该命令 id 不在本地 pc/vc 表里，依据来自复核记录、未从表复核**（docstring 已如实标注「标记待补」）；误伤面已核：两表唯一以 import 开头的形参是 `edit.keyboardShortcuts.importKys`，取值全相对 |

`profile` 之所以能进通用词表而不误伤：值判据只对「绝对 / 含 `..`」生效，`sRGB` / `working` / `Default` / `document` 这类预设名天然放行。**代价是两条旧断言必须翻面**——`test_params_roots.py` 里原先把「`{"profile": WIN_INI}` 必须放行」当作「禁止裸 substring」的载体，现在这个载体换成 `{"direction": …}`（含 `dir`）与 `{"south": …}`（含 `out`）。

**`file:` 协议是路径，不是 URL**

| 入参 | 处置 |
|---|---|
| `file:///C:/Windows/win.ini` | 剥前缀 → `C:/Windows/win.ini` → 送 roots → `PATH_OUTSIDE_ROOTS` |
| `file:///Windows/win.ini`（无盘符 authority） | 补回一个分隔符 → 按**当前盘根**拒（这一步就是 `had_authority` 那两行；删掉它，231 条测试全绿也发现不了，属测试缺口，已补断言） |
| `file:draft.png`（RFC 8089 相对形） | 放行，**不**补分隔符（否则误杀） |
| `file://server/share/x.png` | 按当前盘根拒（fail-closed） |

**🔴 URL 豁免曾让 `..` 判据整体作废（已修）**

第一版写的是「任何 `scheme://` 都跳过 `..` 判据」，理由是 CDN 的 `https://x/a/../b.png` 是合法 URL。云严用真 CLI 打穿：

```
{"path": "xyz://../../../out.ase"}   -> 闸门 ALLOW，CLI 在 roots 外落盘 2296 B
```

机制：Windows 把 `scheme://` **折叠成前置目录名**（`xyz://a/b` → `xyz/a/b`），`..` 照常消解。**逃逸发生在「归一」这一步，而是否需要归一看实现**（[FACT] 2026-11-08 逐层实测）：
| 层 | `xyz://../../payload.txt` 的结果 |
|---|---|
| `ntpath.normpath` | `..\payload.txt` —— 把 `xyz:` 当盘符前缀丢弃 ⇒ **逃到父目录** |
| `os.path.abspath` / `realpath` | `<cwd 的上一级>\payload.txt` |
| Python `open()`（原串直交 Win32） | `FileNotFoundError` —— **不**折叠 |
| Node `fs.readFileSync`（libuv 先归一） | **读成功** |

本插件的 CLI 是 Node 侧，属最后一类（这正是 roots 外落盘 2296 B 的来源）。结论："CLI 恰好不认这个拼写" 不能当缓解，因为归一层的语义已经把它变成一段普通相对路径。单字母 `x://` 恰好先命中 `_ABS_PATH_RE` 走 roots 所以安全，多字母不会 —— 于是「豁免」等于给所有多字母 scheme 开了一条 `..` 旁路。

修法改成**白名单** `_URL_SCHEMES = {"http", "https"}`（大小写不敏感），白名单外一律按路径判据：

| 入参 | 改前 | 改后 |
|---|---|---|
| `xyz://../../x.png` / `a+b://` / `con://` / `foo.bar://` | ALLOW 🔴 | `POLICY_VIOLATION` |
| `x://../../x.png` | `PATH_UNSAFE` | `PATH_UNSAFE`（顺序未变，先命中盘符判据） |
| `https://x/a/../b.png` / `HTTPS://…` | ALLOW | ALLOW |
| `ftp://x/a/../b.png` | ALLOW 🔴 | `POLICY_VIOLATION`（白名单外，**行为变更**）|
| `ftp://h/a/b.png`（不含 `..`） | ALLOW | ALLOW |
| `mailto:` / `data:`（无 `://`） | ALLOW | ALLOW（不受影响）|

**`..` 空白族收全（该族已闭合）**：`strip(" \t")` → `strip()`（按 `str.isspace()`）。逐个实测 15 个 Unicode 空白码点（` \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000`）：旧判据只盖住空格与 `\t` 两种，`strip()` 全盖；且每个变体在磁盘上都打不开（`open()` 全部失败）⇒ **没有可用性代价，这一族无需再补判据**。反向的真实相对路径 `a\b\x.png` 不受影响。

**宿主级证据（真 N.E.K.O + 真 CLI，`probe/host_gate_check.py` 13 条）**

| 组 | 用例 | 结果 |
|---|---|---|
| 拒 | `C:/Windows/win.ini`（step params）/ `\Windows\win.ini` / `--new` 的 `profile` / step 的 `profile` / `droplet` / `file:///C:/…` / `file:///Windows/…` / `xyz://../..` / `a+b://../..` | `PATH_OUTSIDE_ROOTS` ×7、`POLICY_VIOLATION` ×2 |
| 放 | SVG path data / `name="A: study"` / `profile="sRGB"` / `https://x/a/../b.png` | 入口 `success: true`，无路径错误码 |
| 端到端 | `new_doc{200×120}` + `image.imageSize{100,60}` | job `succeeded`、`exit 0`、落盘 PNG 实测 **100×60** |

**判据自身的两个假绿/假红（写进脚本注释了）**

1. 入口 id 写成裸 `run_sequence`（实为 `artcraft_ops_run_sequence`）→ 5 个「放行」用例因「没有任何错误码」被判 PASS，实际根本没执行到闸门。现在 `not found in plugin` 直接判 FAIL。
2. 宿主把**代码**放 `.neko-plugin-installations/plugins/<id>/`、**运行时状态**放 `N.E.K.O/plugins/<id>/`。用错目录时每条带 `out_file` 的用例都报 `PATH_OUTSIDE_ROOTS`，13 条全红而那个红来自公共参数。现在 `DATA_DIR` 指运行时状态目录。
3. 宿主还会把插件的 `PolicyViolation` 统一包成 `INTERNAL`，真错误码只留在 `error.message` 文本里 —— 判据必须扫整段序列化结果，不能只看顶层字段。

**已知边界新增第 9 条**：非白名单 `scheme://` 一律按路径判据处理，但**不额外识别设备名**（`con://` / `aux://`）。理由：折叠是「scheme 当前置目录」，不含 `..` 时退不回 roots 外；放行后该拼写在 Win32 上打不开（`con/a/b.png` 不是合法文件），但**打不开 ≠ 越界**，闸门契约只保 roots 包含性。加设备名黑名单是零收益 + 误杀 `concept/`、`console/` 这类真实路径（和本轮踩过的 `profile` 含 `file` 同族）。等出现「不含 `..` 也逃逸」的实测再补名单。

**基线**：**231 passed / 1 skipped**（§19.7 的 225 + 6）。宿主部署 `sequence.py b75d4bac…` / `roots.py 3e3461e4…` / `__init__.py 370433b4…`，三文件与工作区逐字节一致，回滚备份 `%TEMP%\artcraft_host_backup_20261108_2110`。

**部署与「生效」的四条判据（本轮各踩过一次，全部保留）**

1. **重启插件要 `stop` → 等 ≥5 秒 → `start`**，间隔 2 秒会拿到 `{"detail":"start_plugin failed"}`（宿主进程释放慢，不是代码问题）。
2. **端点单复数不对会静默成功**：真路由是 `/plugin/{id}/stop`、`/plugin/{id}/start`、`/plugin/{id}/reload`（**单数**）；写成 `/plugins/{id}/stop` 返回 404，而脚本若只看「状态是不是 running」就得到「1 秒就 running」的假绿 —— 插件**根本没重启**。重启脚本必须断言 `stop` 的 HTTP 200 + `status == "stopped"` 之后才 `start`。
3. **`/plugins/refresh` 的 `updated: [<id>]` 只表示「检测到文件变了」**，不证明进程已重新 import。别拿它当生效证据。
4. **生效的硬证据是 pyc 头部**：`__pycache__/sequence.cpython-311.pyc` 前 16 字节 `magic|flags|src_mtime|src_size`（小端 u32）若与 `sequence.py` 的 `st_mtime` / `st_size` 逐字段相等，就证明运行中的模块编译自**这一份**文件。本轮末版实测 `src_mtime=1794145202 src_size=56043`，pyc 56286 B，两边一致。比「复制后比 sha256」强：后者只证明盘上文件换了，不证明进程加载了。
   - ⚠️ **这条判据必须带时间窗**：解出的 `src_size` 要与**当前**源文件的大小相等，且 pyc 文件的 mtime ≥ 源文件 mtime。否则读到的是**重启尚未完成时的旧 pyc**，会把「还没 import」误判成「没生效」。本轮真实发生过：云严在 21:40:0x 读到 `src_size=55722`（上一版）并报 `match=NO`，而在 21:40:38 重新 import 之后实测 `56043 / match=True` —— 源 21:40:02 落盘、pyc 21:40:38 编译，两者相隔 36 s。**报这个数字时必须连 pyc 文件 mtime 一起报**，否则无法区分「没生效」与「还没轮到」。另：`src_size` 这个字段本身就能定性 —— 55722 只可能来自 55722 字节那一版源文件。
     - 定式（三个条件并列，缺一不可）：
       `match = (hdr.src_size == src.st_size) and (hdr.src_mtime == int(src.st_mtime)) and (pyc.stat().st_mtime >= src.stat().st_mtime)`
   - ⚠️ **路径必须写全，否则「读错地方」会伪装成「部署丢了」**：代码在 `%LOCALAPPDATA%\N.E.K.O\.neko-plugin-installations\plugins\artcraft_ops\`；`%LOCALAPPDATA%\N.E.K.O\plugins\artcraft_ops\` 是**运行数据**目录（只有 `cache/ config/ data/`），那里 `ls sequence.py` 会直接 `No such file`。云严本轮就按旧写法先 ls 失败过一次。判据必须能区分「NO」与「我读错目录了」——这两件事在报告里长得一模一样。
   - 注释-only 改动另有两条前瞻判据（改前用）：**AST 去 docstring 比对**（本轮 `True`，`ast.dump` 前把 Module/ClassDef/FunctionDef 的首个字符串 `Expr` 摘掉）与 **pyc `co_code` 比对**（云雨独立复验：可执行对象数 38 → 38，无差异）。

**最终签收（2026-11-08 21:3x，主理人自跑；云严/云测/云雨线程因框架侧 RPC 5s 超时中断，未取到其签署意见）**

| 项 | 证据 |
|---|---|
| 部署哈希 | 工作区 = 宿主 = `b75d4bac58ea3ef39222eb1df5cfded378e610663f6b6c0c166b7f7492068961`（56043 B） |
| 只改注释 | AST 去 docstring 与上一版 `3fa753c7…` 完全一致（两次改注释各验一次） |
| 进程已加载 | pyc 头部 `src_mtime=1794145202 src_size=56043` 与源文件逐字段一致；pyc 54839 → 55629 → **56286 B** |
| 13 条矩阵 | ALL PASS（`probe/host_gate_check.py`，H1–H13） |
| scheme 组补充 7 条 | `con://../..` / `prn://../..` / `s3://a/../` / `data://../` / `smb://../` 全 `POLICY_VIOLATION`；`https://x/a/../b.png` 与**已知边界第 9 条** `con://a/b.png`（无 `..`）放行 |
| 单测 | 231 passed / 1 skipped（`test_params_roots.py f26b56f1…`，32 条用例） |
| 第三方复验 | 云严独立跑完 13/13 + 7/7 + 231/1，**签收口径为 `5e43fce3`**（本版 `b75d4bac` 只改了它挑出的两处文字：悬空引用 `probe/` 的 ftp 对照 → 改指 `test_params_roots.py::test_url_with_parent_segments_allowed_but_relative_still_rejected`；「白名单外一律拒会杀掉 ftp」补上「仅当含 `..`」）。**AST 去 docstring 与本版完全一致 ⇒ 签收的可执行代码未变** |
| 复验新增判据 | 云严给「拒」的用例加了**零 spawn**断言：5 条被拒的 payload 里没有 `job_id` ⇒ 闸门在 `create_job` 之前就拒 ⇒ 「没落盘」是构造上成立，不靠事后看盘 |

已知边界第 9 条已**同时**落在三处：`sequence.py` 的 `validate_params` docstring 第 9 条（改动入口最容易被「顺手补设备名黑名单」的地方）、`test_params_roots.py` 的 `test_url_with_parent_segments_allowed_but_relative_still_rejected` docstring（刻意钉白、解释为什么不留断言）、本节。

---

### §19.9 B1 `file:` URI 三分支 / C5 可观测性（2026-11-08 夜，施工中）

**状态（2026-11-09 00:07 实测；云严 Task #3 已独立签收）**：3 处必修已落地、已部署到宿主、宿主 20 条矩阵全绿，**B1/C5 范围无条件同意签收**。

第三方复核要点：9 个 `..` 空白变体 × 3 分支 = 27/27 全 `POLICY_VIOLATION`；n=4/5 纯斜杠、n=4/6 混合形状全拒且 n=3 三态未误伤；`locator.py` 全文 `exc_info` 仅在注释、代码 0 处，无 f-string logger。三条主变异 BASELINE/MUTANT/RESTORED 的 pytest rc = **0/1/0**（删 L712-715、删 L687-688、patch `locator.redact_text`），新断言全有牙；并独立复现了「只 patch `errors.redact_text` → 锚点打空 → 假阴性」。云严另主动剔除误报一条：`. .` 不算空白族（`strip()` 后为 `". ."`≠`".."`），属既有正确口径。

```
工作区 = 宿主：
  sequence.py  845411e1f5e938ccfd58a40ca01dc8bdb1bdec6f692265f2759fd316db2125c0   60131 B
  locator.py   555abb5924e28fd64977e34ba8bc40c5a8572cce0d4c63d998b778fa25dff6ce   11172 B
宿主 pyc（两个都重编译，三条件全 True）：
  sequence.cpython-311.pyc  57487 B  src_size=60131 src_mtime=1794153734
  locator.cpython-311.pyc   15561 B  src_size=11172 src_mtime=1794153800
插件 running；回滚备份 %TEMP%\artcraft_host_backup_20261109_0010
全量  243 passed / 1 skipped（244 collected）
宿主  probe/host_gate_check.py exit=0，20 PASS / 0 FAIL（修前 19/20，唯一红 H20 已转绿）
```

裁决后形态实测（默认 / 授权 UNC / 授权本地三策略）：`file:////server/...`、`file://///server/...`、五个 Unicode 空白变体的 UNC 与 localhost 形态 → 全 `POLICY_VIOLATION`；`file:///C:/Windows/win.ini`、`file:///Windows/win.ini` 仍 `PATH_OUTSIDE_ROOTS`（n=3 未误伤）；`file:draft.png` 仍 ALLOW；`file://localhost/<data root>/x.png` ALLOW（H20 正向）；授权 UNC 根下 `file://server/share/art/x.png` ALLOW 而 `\\server\share\art\.. \x.png` 仍 ALLOW（**有意不一致**）；`ERROR_CODE_ORDER` 仍 19 条。C5 注入 64hex 异常后实测：返回原路径、日志**不含** 64hex、含 `RuntimeError` 类型名、含 `GetLongPathNameW` 栈帧。

**B1 性质（必须先读，别读成越界修复）**：默认策略（只授权 data/cache）下，UNC 原生拼写与 `file://host/...` 拼写**两种都 fail-closed 拒绝**，没有任何越界读写被放开。缺陷只在**显式授权了 UNC 读根**时显现：同一资源写成 `\\server\share\art\x.png` → ALLOW、写成 `file://server/share/art/x.png` → REJECT。旧实现 `rest.lstrip("/\\")` + `had_authority` 把**任何** authority 折成「当前盘根」，丢掉远程主机名；`localhost` 也未等价本地（段 `C:` 被 roots 当 ADS 判 `PATH_UNSAFE`）。**这是一致性 / 可用性缺陷，不是安全洞。**

**RFC 8089 三分支契约**（`sequence.py._check_one_param_path`）：剥 `file:` 后先数前导 `[\\/]` 连续个数 `n`；`n>=4` 直接 `_violate`；`n==3` 空 authority 本地；`n==2` 按 `[\\/]` 切一次取 `authority`/`path_body`，空串或 `localhost`（整段字面、大小写不敏感）→ 本地路径，其它 → 拼 UNC `\\host\...` 送 `roots.resolve_and_check`；`n<=1` 相对形保持原行为。禁止 `urlsplit` / `.hostname` / `unquote` / 剥 `@` / 剥端口——实测 `.hostname` 会把 `localhost@evil` 洗成 `evil`、`unquote` 把 `%2e%2e` 洗成 `..`，都会改变分支归属。

**两个 🔴 阻塞（主理人在真实落地代码上实测坐实，非纸面推演）**

| # | 形状（授权 `\\server\share\art`） | 修前 | 第一版 | 裁决 |
|---|---|---|---|---|
| 1 | `file://server/share/art/.. /x.png` 等 11 个空白变体 | REJECT | **ALLOW** | 必修：`file:` 分支产出的所有 `text` 送 roots 前统一跑 `seg.strip()==".."` + `_violate` |
| 2 | `file:////server/share/art/x.png` | REJECT | **ALLOW** | 必修：`n>=4` 直接 `_violate` |

- 🔴1 根因：UNC 串以 `\` 开头会在「单分隔符 → roots」分支**提前 return**，跳过 `sequence.py` 约 694 行的 `seg.strip()==".."` 判据；而 `roots._assert_safe_segments` 只认**字面** `seg == ".."`，不 strip。`file://localhost/C:/...` 走绝对分支同样漏。
- 🔴1 **性质修正**：原生 UNC 拼写 `\\server\share\art\.. \x.png` 在**修前就是 ALLOW**，所以这不是 B1 造的新洞，而是 B1 把 `file://` 对齐到既有宽松面；但对这条**新开的输入通道**确实 REJECT→ALLOW，扩大可达面，且测试零覆盖 ⇒ 仍判必修。
- 🔴2 根因：`rest="////server/..."` 剥 2 个得 `body="//server/..."`，split 出空 authority + `path_body="/server/..."`，空 authority 分支拼出 `text="\/server/..."`，`roots.normalize_win_path` 把 `\`→`/` 洗成 `//server/...` UNC。**本该是「当前盘根」却变成「远程 UNC」，是 B1 新引入的语义错位。**
- ⚠️ 实现陷阱：`n==3` 的合法 `file:///C:/...` 其 `body` **也**以分隔符开头，不能用「body 以分隔符开头」当判据，否则打红正向用例。

**🔴 设计意图：新通道从严（云严提示，必须保留）**。选项 A 只把空白族判据加在 `file:` 分支，不改 `roots.py`，于是修后 `\\server\share\art\.. \x.png` → ALLOW 而 `file://server/share/art/.. /x.png` → `POLICY_VIOLATION`，**两种拼写有意不一致**。这是刻意的：本轮新开的通道不该继承历史通道的宽松面；在 `roots._assert_safe_segments` 加 `strip()` 会改动所有历史通道，越出本轮范围，列为下一轮 **S10**。**下一轮若按「B1 的一致性目标」来「修齐」这两个结论，等于把这道护栏拆回去 —— 改 `roots.py` 前先回到本段。**

> **[S10 已落地，2026-11-09] 护栏未被拆 —— 收敛方向是「都变严」，不是「都变宽」。** `roots._assert_safe_segments` 改为判定前对**每一段**做 `str.strip()`（无参数）。于是原生 UNC 的 `\\server\share\art\.. \x.png` 由 **ALLOW → `PATH_UNSAFE`**，与 `file:` 分支的 `POLICY_VIOLATION` 重新一致（**两侧都拒**，新通道一点没放松）。
> `file:` 分支自带的那条 `seg.strip()==".."` 判据**仍在、仍先触发**，没有被删除 —— 删了会改变错误码（`POLICY_VIOLATION` → `PATH_UNSAFE`）、打红 `test_b1_file_scheme_whitespace_dotdot_rejected_in_all_branches`。它现在与 roots 层重复，但**重复是保守的**：一层在值层挡、一层在段层挡。细节见 §19.12。

**已知边界（新增，故意不钉断言，只由文档承接）**

- **第 10 条** `%2e%2e` 在授权 UNC 根下 ALLOW。原生 UNC 拼写修前同样 ALLOW，两通道一致；CLI 是否解码百分号**未核验**，按纪律不为未核验行为加判据。**不许把这条钉成 REJECT 断言，也不许钉成 ALLOW 断言**——前者是凭空加策略，后者会把「待观察」固化成「正确语义」。
- **第 11 条** `file:\server\share\...`（单反斜杠）修前修后都 ALLOW，被当「无 authority 相对形」，与双反斜杠 UNC 分支结论相反。属既有行为，本轮不改。

**错误码归类变化清单（不锁精确码）**：

| 形状 | 修前 | 修后 | 原因 |
|---|---|---|---|
| `file://C:/Windows/win.ini`（盘符被当 authority） | `PATH_OUTSIDE_ROOTS` | `PATH_UNSAFE` | 拼出的 UNC 含 `C:` 段，被 `_assert_safe_segments` 的 ADS 判据拒 |
| `file://LOCALHOST/C:/...` / `file://localhost/C:/...` | `PATH_UNSAFE` | `PATH_OUTSIDE_ROOTS` | localhost 归一为**本地绝对路径** `C:/...`，不存在 authority 含 `C:` 的情形；走 roots 越界判据 |
| `file:////...`（n>=4） | `PATH_OUTSIDE_ROOTS` | `POLICY_VIOLATION` | 形状非法，前置 `_violate` |

[2026-11-09 云严签收时挑出笔误并已订正] 上一版把 `file://LOCALHOST/...` 与 `file://C:/...` 合并写成「由 `PATH_OUTSIDE_ROOTS` 变 `PATH_UNSAFE`」，**方向与归因都错了**：实测宿主版 `file://LOCALHOST/C:/x.png`、`file://LOCALHOST/C:/Windows/win.ini`、`file://localhost/C:/Windows/win.ini` 三者全是 `PATH_OUTSIDE_ROOTS`。全部仍 REJECT，二值不变、不影响安全结论。**刻意不补精确码断言**：锁码会与下一轮 S7「统一两套错误码」冲突；闸门的契约是「拒」，不是「用哪个码拒」。

**C5：`errors.py:8` 铁律 3 优先于任务书**。铁律原文「进 logger 的值先 `redact_text()`，只用预格式化参数，禁 f-string / `logger.exception`」。第一版用了 `exc_info=True`（与 `logger.exception` 等价），实测 `ValueError("bad token <64hex>")` 会把凭证原样打进 traceback。但直接换成 `redact_text(str(exc))` 会**丢掉帧级区分能力**——`runner.blocking` 的三种失败面（线程池调度 / `open` / `fh.read`）正是靠 traceback 帧区分的，而那才是 C5 让 flaky 可定位的价值。最终写法两者都要：

```python
except Exception as exc:
    logger.debug(
        "<上下文> (%s): %s",
        type(exc).__name__,
        redact_text(traceback.format_exc()),
    )
```

`traceback.format_exc()` 保留完整栈帧；整串过 `redact_text()` 后作为**预格式化 `%s` 参数**传入，满足铁律 3（无 f-string、无 `exc_info`）；异常类名在 `rec.args` 里，`_debug_records_naming` 第二分支照常命中。`detect_version` 仍绝不抛异常、绝不阻塞发现流程。云测补**主动泄密探测**（注入含 64hex 的异常 message，断言 `caplog.text` 不含原文）：新写法下 traceback 确实带出 message，所以这条断言能区分「`exc_info=True` 旧写法（红）」与「`redact_text(format_exc())` 新写法（绿）」，弥补原 `_INSTANCE_TOKEN not in caplog.text` 只是护栏、无法替代实现审查的缺口。

⚠️ **脱敏能力的边界，别外推**：`errors.py:31-33` 明写 `redact_text` 是「兜底**第二层**」，主防线是白名单结构；`REDACT_VALUE_RES` 只有 4 条形态（32+ 位 hex / `method:"auth"` / `--control-token-file` / bearer·basic 编码凭证），**不覆盖任意凭证**（例如非 hex 的 `"token": "abc123"`）。所以「探测绿」只能证明 **64hex 形态**被挡住，**不可**写成「C5 日志不泄密」的一般结论。另有两个待观察边界（下一轮 S11 候选）：① `\b` 边界在「64hex 被嵌入更长字母数字串」时可能不打码；② `traceback.format_exc()` 含**源码行文本**（属源码字面量而非运行时凭证，风险低）。`_MAX_STRING_SCAN=256KiB` 对 traceback 不构成截断风险。

变异验证的 patch 目标要选对：实现是 `from .errors import redact_text`（模块内绑定），所以 `unittest.mock.patch` 应打 `artcraft_ops.locator.redact_text`；只 patch `artcraft_ops.errors.redact_text` 可能不生效，会得到「变异全绿」的**假阴性**。沿用上一轮长期规则：**变异返回全绿先验锚点**（确认 patch 后函数确实变了），再判用例。

**下一轮清单**：S12（帧级区分断言缺口，状态见下方 §19.10）、S10（`roots._assert_safe_segments` 统一加 `strip()`，改前回读「新通道从严」）、S11（`redact_text` 兜底第二层的覆盖边界：`\b` 嵌入更长征串、`format_exc()` 的源码行字面量）、S12（**帧级区分断言缺口**：现有 C5 断言只验「类名 + 脱敏」，实现若退化成「只打一行类名、丢栈帧」仍会全绿，flaky 根因将再次不可见。建议判据：不写死文本/换行，只验「日志含至少一个栈帧定位信息」）、S13（`python -m sandbox.cli check artcraft_ops` 的 2 个**既有环境 ERROR**：`bridge.py:70` 的 `httpx` 未装、`test_app_registry.py:9` 的 `_fixtures` 靠 conftest 注入 sys.path 而 sandbox 不加载 conftest。两文件非本轮改动、与 B1/C5 无因果，签收裁定「记为已知环境问题、不带入本轮」，但**不记为通过、不隐藏**）、S7（错误码统一）、S6（死代码）、S8（`direction` 死参数）。B1 / C5 已在本轮处置；`test_detect_version_falls_back_to_sha256` 那次 flaky 的具体触发源**仍未核验**（单跑 12 次未复现），C5 只保证下次可定位，不冒称已修根因。

**本轮方法论沉淀**：① 判据的「等价覆盖」不能靠推测——`sequence.py` 的 `seg.strip()==".."` 与 `roots._assert_safe_segments` 的字面 `seg==".."` 看起来都在做 `..` 检查，但 UNC 串在「单分隔符 → roots」分支**提前 return**，插件层判据根本没被执行；② 新增输入通道必须单独审「它是否继承了历史通道的宽松面」；③ **「grep 不到」只能证明「没落盘」，不能证明「没做过」**——云测的 M1–M10 / V1–V12 变异表只在消息里、从未落盘，全仓搜不到就断言「不存在」是过度判断；而把自己推演的锚点冠上成员编号仍属错误，两者要分开说；④ 文档口径必须与实际实现同步（本轮出现过「消息说已写进文档但尚未落盘」），且状态快照有时序，读到旧文字先核 mtime 再下结论；⑤ 团队协作一律经主理人中转，本轮两次因成员直连出现口径打架；⑥ **工具边界 ≠ 产品结论**：静态 import 检查（sandbox）不能否决已被真宿主运行态证实的交付，但也不能当它不存在，须如实记档。

---

### §19.10 S12 帧级区分断言（2026-11-09，纯测试补强）

**状态（2026-11-09 08:2x）**：**S12 已由云严独立复核并签收（同意，有保留）**。生产代码零改动。

最终被验对象（`test_locator.py`）：`42aa10647783bf8232c8f52af7cbe02675687875e333a99162b4c689c3e1f300` 24461 B（上一版 `509e7f79…` 22588 B 已作废，仅注释订正）。反向重建哈希吻合、去 docstring 的 AST 形状两版相同 ⇒ 断言逻辑一字不动。全量 `246 passed / 1 skipped`（247 collected）。

**要解决的问题**：C5 既有断言只验「异常类型可定位」（`_debug_records_naming`）与「64hex 已脱敏」（`secret not in caplog.text`）。实现若退化成「只打一行 `RuntimeError`、丢掉整个栈帧」，这四条用例仍会全绿，而 `runner.blocking` 的三种失败面（线程池调度 / `open(` / `fh.read`）将再也无法区分 —— 那次 `test_detect_version_falls_back_to_sha256` flaky 的根因会重新变得不可见。

**交付（[FACT]）**：只改 `artcraft_ops/tests/test_locator.py`，新增 `_FRAME_MARKERS`（5 个结构性正则：`File "` / `File '` / `\bline\s+\d+` / `\b[A-Za-z_]\w*\.py\b` / `,\s+in\s+[A-Za-z_]\w*`）与 `_debug_records_with_frame()`，一条 parametrize 用例覆盖 long_path / version_probe / sha256_fallback 三处降级路径。判据落在 `rec.getMessage()`（会展开 `%s` 预格式化参数）上，验「至少一个帧定位信息」；不写死日志措辞、行数、换行数，不硬编码任何具体函数名。
- 基线 `243 passed / 1 skipped` → 交付 `246 passed / 1 skipped`（247 collected），零误伤。
- **红阶段靠变异造**（当前实现本就绿）：`monkeypatch.setattr(loc, "traceback", SimpleNamespace(format_exc=lambda *a, **k: "RuntimeError"))` → `3 failed, 12 passed`，3 红只落在新 case、失败点统一在断言②；恢复后 `246 passed / 1 skipped`。
- 断言① `_debug_records_naming` 仍非空作**注入锚点**，变异下仍绿，正是「旧断言没牙」的证据。

**patch 目标的关键区别（长期规则）**：`locator.py` 是顶层 `import traceback` + `traceback.format_exc(...)` **属性查找**，故目标是 `locator.traceback`（模块内名字）；与上一轮 `from .errors import redact_text` 的**模块内值绑定**不同。patch `traceback.format_exc` 会污染全局 traceback 模块、连带影响 pytest 自身的失败报告格式化。先验锚点：`loc.traceback is real_tb` / patch 后 `format_exc()` 返回退化值 / `real_tb.format_exc is orig`。

**签收证据（云严正式复核，[FACT]）**：最终版 `42aa1064…` 上变异三连 `246 passed / 1 skipped → 3 failed, 12 passed（仅 3 个新 case 红、失败点 :501）→ 246 passed / 1 skipped`；patch 先验锚点三条全过（`loc.traceback is fake` / `format_exc()` 返回退化值 / `real_tb.format_exc is orig`）；临时插件 `rm` 后 `s12_* 残留=0`、`%TEMP%/artcraft_ops_pytest` 不存在、无新 pyc；生产两文件哈希未变。Q2/Q3/Q4 全部通过：新断言不碰 `rec.exc_info`（与铁律 3 无冲突）、helper 查 `getMessage()` 展开后文本（`rec.msg` 是模板无帧）、既有 5 个函数体哈希逐字一致。

**S12-b 剩余边界（主理人裁决，本轮不修）**：实测 5 个正则逐条跑——
```
形态              命中标记       任一命中   File " 行数
全丢(只一行类名)   []             否        0   ← 断言红（已挡住）
只留最外一帧       [1,3,4,5]      是        1   ← 断言绿（放行）
压平成单行         [1,3,4,5]      是        1   ← 断言绿（放行）
完整栈帧           [1,3,4,5]      是        2
```
即「只留最外一帧 / 压平单行」仍会过断言，但它们丢掉深层帧，三种失败面重新不可区分。

**S12-b 口径陷阱（云严补充复核新增，立项时必须先定）**：「`File "` **出现次数**」与「含**物理行数**」不是一回事——
```
形态            File " 出现次数   含帧物理行数
只留最外一帧          1                1
压平成单行            2                1     ← 两帧被压进同一行
完整栈帧              2                2
```
后果：补强若用「物理行数 ≥2」→ 会把**压平单行**（契约内有意放行、帧文本完整）误判成退化；若用「出现次数 ≥2」→ 压平放行、只留一帧打红，但回到「帧数依赖注入方式」的老问题（实测 `long_path`=2、`--version` 探针=2、sha256 回退=2；换注入方式可得 3/8/7）。这从第三个角度加固了「本轮不修」的理由。

**S12-b 立项备料：行为级判据（云严提出，不主张本轮做）**。不做帧数阈值，改抽**帧签名**：在同一用例内分别触发 `runner.blocking` 三种失败面，各取一条 DEBUG 消息，用 `re.findall(r'File\s+["\']([^"\']+)["\'],\s*line\s+(\d+)', msg)` 抽 `(路径, 行号)` 元组，断言 `len(set(三种签名)) >= 2`。
- 不数帧数（不依赖「≥2 帧」常量）、不硬编码 `to_thread`/`open`/`read`/`long_path`（只抽结构）、不锁死形态（多行/压平/任意措辞皆可，只要保留 `File "…", line N` 结构；该结构 3.11.16 与 3.13.14 实测一致）；
- 效果：只留最外一帧 → 三签名相同 → RED；全丢 → `()` → RED；压平单行 → 签名仍不同 → GREEN（符合契约）；**顺带挡住伪帧假绿**（一行 `locator.py` / `line 1` 不含完整 `File "…", line N` → 签名 `()` → RED）；
- 局限 [INFERENCE]：`open`/`read` 失败需 patch `builtins.open`、造假 `fh`，注入复杂度高于本轮，且仍依赖 CPython 帧行结构 → **适合单独一轮，不塞进 S12**。

**S12-b 第二形态：伪帧假绿（云严复核新发现，我独立实测坐实）**。判据是 5 个标记**任一命中**，而后 3 个弱标记（`.py` / `line N` / `, in fn`）可被单行普通自然语言独立满足：
```
纯一行类名   命中 []        → 红（挡住）
伪帧 .py     命中 [4]       → 绿（放行）
伪帧 line N  命中 [3]       → 绿（放行）
伪帧 , in fn 命中 [5]       → 绿（放行）
伪帧组合     命中 [3,4]     → 绿（放行）
真·完整栈帧  命中 [1,3,4,5] → 绿
```
因 `_debug_records_naming` 收窄只需「记录里能定位异常类型名」，日志前缀带 `(RuntimeError)` 即可过。裁决：**并入 S12-b，不另开编号**——与 `first_frame_only` 同属「至少一个帧定位信息 + 任一命中」这一弱判据的两个投影，拆号会碎片化。**收口句扩为「只验至少一个帧定位信息，不外推为三失败面逐一可区分，也不外推为能识别伪帧文本」**。pytest/caplog 层尚未逐条钉成回归用例（云严标 [INFERENCE]），留 S12-b 落地时补，不冒称已验证到那一层。

**不扩范围的理由**：补强需验「≥2 帧行」或「深层帧函数名」；前者帧数依赖注入方式（实测三条路径 `File "` 计数为 2 / 2 / 4，sha256_fallback 是两条 DEBUG 记录各 2），不能把 2 当普适常数；后者与「不钉死实现形态」的契约直接冲突。需要一个单独裁决的判据，不该塞进本轮纯测试补强。**S12 只保证「至少一个帧定位信息」，不外推为「三失败面逐一可区分」。**

**S14 记档**：`errors.py:54` 的 `redact_text` docstring 写「命中任一凭证形态 → 整段替换为 `***`」，但实现是 `pattern.sub(_MASK, text)` **局部替换**。属既有文档漂移、非本轮引入，本轮不动（避免破坏「零生产改动」口径）。

**本轮方法论**：① 当前实现已满足契约时，红阶段必须靠**运行期变异**造，不许改断言去迁就坏实现；② 变异前先验锚点，且要区分「真变异红」与「语法错误全红」（后者是 collection error 或全部用例红）；③ 判据要验**性质**而非形状——查「可定位」而非硬编码函数名/行数，否则实现换个表达方式就误伤。

---

### §19.11 S12-b 帧级区分断言硬化（2026-11-09，纯测试补强，**已签收**）

**状态**：**签收**。被验对象 `artcraft_ops/tests/test_locator.py` = `c5f673abbef4a2f102fe93ccc1cdb88f2dd790734c677f4bb61d9078bf496210` / 947 行 / 50972 B，**已冻结，签收后不再编辑**（本轮已发生 4 次对象漂移，见 §19.11.9）。生产代码零改动。

| 项 | 值 |
|---|---|
| 单文件 | `18 passed` |
| 全量 | **`249 passed / 1 skipped`**（250 collected；§19.10 基线 `246/1`，净增 3 条 parametrize） |
| 生产文件哈希 | 四文件逐字节未变，且与宿主逐字节一致 |
| 已签收旧段 | 前 505 行（去 13/15/20 三行）`24461 B` / `42aa1064…` —— 与 §19.10 签收版**逐字未变** |
| hygiene | `VERDICT: PASS` |
| 临时桩残留 | 0；无 `.orig` / `.rej` / 新 pyc |

---

#### §19.11.1 要堵的两条边界（承接 §19.10 末的裁决）

§19.10 把 S12 收口为「至少一个帧定位信息」，并明说**不外推**为「三失败面逐一可区分」。S12-b 就是把这两条被放行的漏洞堵掉：

| # | 形态 | §19.10 时的判定 | 后果 |
|---|---|---|---|
| ① | `first_frame_only`（只留最外一帧） | 5 标记任一命中 → **绿** | 丢掉失败行 `locator.py:181/182`，`open` 与 `read` 重新不可区分 |
| ② | **伪帧假绿**（弱标记 `.py` / `line N` / `, in fn` 被单行自然语言满足） | **绿** | 一行类名文本即可冒充栈帧，判据形同虚设 |

二者同一根因：**「至少一个帧定位信息 + 任一命中」是弱判据**。拆两个编号会碎片化，故并入 S12-b。

#### §19.11.2 判据四版演进（A → B → C → A1）

| 版 | 判据 | 挡住 | 被打穿的方式 |
|---|---|---|---|
| **A**（云测原版） | 三面帧签名**两两不等** | 2 种 | `first_frame_only`：三面最外帧互不相同 → 全绿 |
| **B**（主理人加硬） | A + 每面**必须含 `runner.py` 帧** | 4 种 | `runner_plus_inner`（保留 `runner.py:259` + 任意最内帧）**同时满足两条**，而失败行已丢 —— 云严提出、主理人复跑坐实 |
| **C**（云严版） | **剔除测试帧后**每面生产栈 ≥2 帧 + 三面两两不等 | 8 种 | 桩**搬出本文件**（`tests/_s12b_inject.py`）→ 桩帧不在剔除集合 → 白送每面唯一性 → `outer_inner` / `runner_plus_inner` 假绿 |
| **A1**（最终） | C + 剔除**整个测试套件**帧 + 两条运行期 tripwire | 10 种 | —— |

**关键结论**：② 深度 `>= 2` 与 ③ 三面两两不等**缺一不可**，二者测的是不同性质 —— ② 管「够不够深」，③ 管「三面是不是同一个」。`first2`（只留最外两帧）三面深度都是 2、**② 通过**，靠 ③ 才打红；**只做 ② 会让 `first2` 假绿**。

#### §19.11.3 紧界证明：为什么「open ≠ read」等价于「必须保留失败行」

云严给出的这条是本轮最有价值的结构性结论。剔除注入桩帧后，`open` 面与 `read` 面的栈**只差一行**：

```
共同帧  locator.py:220   runner.py:259   asyncio/threads.py:26   concurrent/futures/thread.py:59
差异帧  locator.py:181（open 面）   vs   locator.py:182（read 面）
```

⇒ 「`open` 签名 ≠ `read` 签名」**等价于**「失败行 `181` / `182` 仍在」。这不是「顺便挡住的」，是**判据的精确边界**：它既不要求保留 `locator.py:220`（调用点），也不要求保留 asyncio 中间帧 —— 那些都是冗余噪声，删了照样三面可分。

**这直接从结构上解释了 B 版为什么必然被绕**：B 只要求「含 `runner.py` 帧」，那是**共同帧**，对区分毫无贡献；真正承重的是**差异帧**。

#### §19.11.4 最终判据实现（`test_locator.py`，行号为 `c5f673ab` 版）

```python
_FRAME_HEADER_RE = re.compile(r'File\s+["\']([^"\']+)["\'],\s*line\s+(\d+)')   # :593
_SHA256_SURFACE_EXC = {...}                                                   # :602 三面→期望异常类型
_TESTS_DIR / _PLUGIN_ROOT / _SELF_TEST_BASENAME                                # :633-635
_SCANNED_TEST_MODULES   = frozenset(os.walk(_TESTS_DIR) 下全部 .py basename)   # :636-641
_PRODUCTION_MODULE_BASENAMES = frozenset(os.listdir(_PLUGIN_ROOT) 下 .py)      # :642-644  实时推导，不手写清单
_TEST_MODULE_WALK_OK    = bool(_SCANNED_TEST_MODULES)                          # :645
_COLLIDING_MODULE_BASENAMES = _TEST_MODULE_BASENAMES & _PRODUCTION_...         # :649

_frame_signature(text)      # :652  抽 ((basename, lineno), ...)，保序、刻意不去重
_production_frames(sig)     # :666  剔测试套件帧 + 两条 tripwire（raise AssertionError）
_production_depth(sig)      # :704  len(set(production))
_open_arg_basename(file)    # :724  os.fspath + os.fsdecode，还原不了返回 ""（避开 open(fd)）
_trigger_sha256_failure(...) # :741  强制探针失败 → 进 sha256 回退 → 让指定层真失败
test_c5_sha256_failure_surfaces_have_distinguishable_frame_signatures  # :838
```

**三层断言，失败文案刻意指向不同原因**：

| 层 | 位置 | 内容 | 红在此处的含义 |
|---|---|---|---|
| ① 注入锚点 | `:809` `assert got == "unknown"` | 探针必失败 → 必进回退 → 回退必失败 → 必返回 `unknown` | **注入没命中，测试自身问题**，不是实现退化 |
| ② 生产栈深度 | `:937` `assert depth >= 2` | 按 `depth==0`（帧结构整体丢失）/ `depth==1`（深度不足）**分叉文案** | 帧被截在最外/最内/首尾，或只剩失败行 |
| ③ 跨面差异 | `:941` `assert frames[left] != frames[right]` | 两个失败面的**生产帧**签名不相等，塌缩时打印两面取值 | 三面日志在内层已无结构差异 |

parametrize 三对：`executor_schedule-vs-open` / `executor_schedule-vs-read` / `open-vs-read`。

**为什么剔整个测试套件而不是本文件（A1）**：桩一旦搬到别的测试模块，只剔本文件就重新假绿。按 `tests/` **递归**下全部 `.py` 的 basename 集合剔除，`conftest.py` / `_fixtures.py` 也在集合内 —— 它们是测试代码，不是生产栈，剔得对。

#### §19.11.5 十形态对撞矩阵（**主理人独立实测，非成员报告转录**）

| 注入形态 | 结果（18 条） | 期望 | 备注 |
|---|---|---|---|
| `full` | `18 passed` | 绿 | 真实栈 |
| `first1`（只留最外一帧） | `3 failed, 15 passed` | 红 | ③ 打红 |
| `first2`（只留最外两帧） | `3 failed, 15 passed` | 红 | **② 通过、③ 打红** |
| `inner1`（只留最内一帧） | `3 failed, 15 passed` | 红 | ② 打红 |
| `outer_inner`（首尾各一帧） | `3 failed, 15 passed` | 红 | ② 打红 |
| `runner_plus_inner` | `3 failed, 15 passed` | 红 | **B 版在此失去拦截力** |
| `runner_inner_prod` | `18 passed` | **有意放行** | 见 §19.11.7 |
| `prod_last_only`（只剩失败行） | `3 failed, 15 passed` | 红 | ② 打红（深度 1） |
| `pseudo`（伪帧：一行类名 + `.py`） | `3 failed, 15 passed` | 红 | ② 深度 0 打红 |
| `flatten`（多帧压进一行） | `18 passed` | **契约内放行** | 见 §19.11.7 |

十形态**零误伤**：`full` 与两个有意放行形态全绿，其余七个全红，且红点全部落在新 case。

#### §19.11.6 搬桩边界：A1 修法前后对照

建真文件 `tests/_s12b_inject.py` 让 `os.walk` 扫到，改前/改后各跑一遍，跑完删除（`残留=0` 已核）：

| 形态 | 改前（只剔本文件） | 改后（剔整个套件） | 结论 |
|---|---|---|---|
| `full` | `18 passed` | `18 passed` | 无回归 |
| `inner1` | `3 failed, 15 passed` | `3 failed, 15 passed` | 无回归 |
| `first2` | `3 failed, 15 passed` | `3 failed, 15 passed` | 无回归 |
| `outer_inner` | **`18 passed`（假绿）** | `3 failed, 15 passed` | **已堵** |
| `runner_plus_inner` | **`18 passed`（假绿）** | `3 failed, 15 passed` | **已堵** |

#### §19.11.7 两条 tripwire（用 `raise AssertionError`，不是 `assert`）

```python
if not _TEST_MODULE_WALK_OK:        raise AssertionError("os.walk 没扫到任何 .py...")
if _COLLIDING_MODULE_BASENAMES:     raise AssertionError("tests/ 下出现了与生产模块同名的 .py...")
```

**为什么不用 `assert`**：`python -O` 会删掉 assert，正好让这两条防护**静默消失** —— 那恰恰是它们存在的理由。`AssertionError` 在 pytest 里仍是失败，语义不变。

**独立验证（注入假值强制触发，证明确实会 raise、不是死代码）**：

```
tripwire: 触发 ✓ -> tests/ 下出现了与生产模块同名的 .py：['__init__.py']；按 basename 剔除会把生产帧误剔 → 生产线深度少算 ...
walk-ok:  触发 ✓ -> os.walk 没扫到任何 .py（...\artcraft_ops\tests）...
```

**运行时集合实测**：`_TESTS_DIR` 为绝对路径（故与 cwd / rootdir / `--import-mode` 无关）；`_SCANNED_TEST_MODULES` = 19 个；`_PRODUCTION_MODULE_BASENAMES` = 16 个；**交集为空**；`WALK_OK = True`。

#### §19.11.8 强度上限与两条有意放行

| 形态 | 为什么放行 |
|---|---|
| `flatten` | 多帧压进**同一物理行**。帧个数没变、签名不变 —— §19.10 已裁决为契约内形态。若用「物理行数 ≥2」做判据会把它误判成退化。 |
| `runner_inner_prod` | 保留 `runner.py:259` + 失败行 `181/182`，丢掉调用点 `locator:220` 与 asyncio 中间帧。按 §19.11.3 的紧界，**差异帧还在 ⇒ 三面仍两两可分**。它是最小充分形态：既不绑「必须经 `detect_version` 驱动」，也不绑「必须保留调用点」。 |

**[强度上限声明] 本判据已到顶。** 继续收紧（要求保留 `locator.py:220`、要求 ≥3 帧、钉具体行号、绑驱动方式）只会绑死实现形态并引入假红，**不要再加断言**；要提升可观测性请改生产代码的日志内容。

#### §19.11.9 已知的保守假红（有意设计，记档不改）

若将来给 `tests/` 加 `__init__.py`，会与生产模块 `artcraft_ops/__init__.py` 同名 → `_COLLIDING_MODULE_BASENAMES` 非空 → **tripwire 挡红**。

这是**有意设计**：不挡的话，生产栈里的 `__init__.py` 帧会被误剔、深度少算、变成更难查的**假红**。文案已明说「必须改剔除策略（例如让 `_frame_signature` 保留相对路径），**不能只删这条检查**」。届时按文案改剔除策略，不要删 tripwire。

#### §19.11.10 本轮流程教训（写给下一个接手的人）

**（一）三次判断被推翻 —— 判据强度由对撞矩阵决定，不由谁的口径更严决定**

| # | 我的判断 | 被谁推翻 | 靠什么推翻 |
|---|---|---|---|
| 1 | B 版「每面必须含 `runner.py` 帧」够硬 | 云严 | `runner_plus_inner` 构造 + 我复跑 |
| 2 | 否决 A 方案（「绑目录结构、代价大」） | 云严 | 实测只要 3 行、零回归、语义更正确 |
| 3 | 判据只需「三面两两不等」 | 云严 + 对撞矩阵 | `first_frame_only` / `pseudo` 全绿 |

→ **规则**：任何判据在被至少 8 种退化形态对撞过之前，不许说「够了」。

**（二）4 次对象漂移（`2359d52d → cdab793b → e9f5ede2 → 4dbda9a7 → 3279de6b → c5f673ab`）**

根因是**返工指令分两次下发、成员依次执行，冻结指令发晚了**。云严已明确「第 4 次漂移后无法对某一份 hash 持续背书」。

→ **规则**：返工指令**一次给全**；或先在消息首行写「**暂停改文件**」，等新指令齐了再放开。验证必须在**钉死的 hash** 上跑，并在跑前跑后各算一次 hash 证明没跨过改动时刻（本轮已这样做：验证前后均 `c5f673ab`）。

**（三）归属事故的第 4 次（方向相反）**

我给云测的返工消息里写「云严预检提出」，那两点其实是我自己读代码的判断 —— 云严报告当时还没到。已当场发更正要求按「主理人要求」引用。此前三次是「把别人的产出记成自己的推演」。

→ **规则**：引用/否认他人产出前，先回查那条消息是否真的到达；推演结论一律显式标「我推演的」。

**（四）变异脚本本身要先自检**

本轮变异脚本把 pytest hook 参数名写成 `s`，pytest 报 `Argument(s) {'s'} are declared in the hookimpl but can not be found in the hookspec`。**若该参数被静默忽略，七种变异会全部显示绿**，我就会误判「判据完全失效」。

→ **规则**：变异返回全绿时，先怀疑变异本身（钩子没生效 / 参数被忽略 / 改的不是被 import 的那份文件），再下结论。

---

### §19.12 S10 段判据一致性收紧（2026-11-09，生产改动一处，**本地 + 宿主均已落地**）

**状态**：本地实现 + 测试 + 独立复核全部完成；**宿主已部署并实机验证通过**（2026-11-09 13:30，见 §19.12.9）。部署前后 `cmp` 逐字节一致，宿主 `roots.py` = `329422fb…`。

| 对象 | 改前 | 改后 |
|---|---|---|
| `roots.py` | `3e3461e4…` / **325 行** / 13313 B | **`329422fb…` / 359 行 / 15912 B** |
| `tests/test_roots.py` | `3a34312e…` / 335 行 / 27 用例 | **`3938969a…` / 424 行 / 41 用例** |
| `tests/test_params_roots.py` | `1beb2ba1…` / 1729 行 | `3c16ffb5…` / 1732 行（**仅 docstring 订正两处，零断言改动**） |
| `locator.py` / `errors.py` / `sequence.py` | —— | **哈希未变**（`555abb59…` / `6a44514f…` / `845411e1…`） |

全量：`249 passed / 1 skipped` → **`263 passed / 1 skipped`**。`+14` = 首轮 11 条（7 红 + 4 反向）+ 空白族补齐 3 条，**没有隐藏的连带改动**。

#### §19.12.1 立项：这是判据不一致，**不是漏洞**

主理人最初的判断是「真实目录穿越漏洞」，被自己的探针**当场推翻**，证据如下（三套独立 API 一致）：

| 尝试 | Python `open` | PowerShell `[IO.File]::ReadAllText` | `cmd /c dir` |
|---|---|---|---|
| `root/../SECRET` | **读到 PWNED** | 读到 PWNED | 列出上级目录 |
| `root/.. /SECRET` | FileNotFound | MethodInvocationException | rc=1（找不到） |
| `root/inside.txt `（**普通名**尾随空格） | **读到 `inside.txt`** | —— | —— |

⇒ Windows 确实会剥尾随空格，但**只对普通名**；`..` 必须在解析阶段**精确匹配**，剥完不再当 `..`。所以放行 `.. ` 不构成穿越。

**「5/12」是抽样数，不是族规模**（复核订正）：按 `str.isspace()` 整族计，`..` 与 `con` 各 9 个空白字符是 **18/18 全不一致**——精确等于型判据在这个族上是**系统性失效**。文档不得把 12 格当成族大小。

#### §19.12.2 根因：串尾的「严」是整串 strip 的副作用

`normalize_win_path`（`roots.py:68`）对**整串**做 `text.strip()`，于是同一个逻辑段：

| 位置 | 尾随空白 | 精确等于判据 |
|---|---|---|
| 串尾（最后一段） | 被整串 strip 顺手剥掉 | **命中 → 拒** |
| 串中（中间段） | 没人剥 | **落空 → 放行** |

**串尾的严不是判据在起作用，是整串 strip 的偶然覆盖。** 这是本轮的根因句。

两类判据表现不同，也解释了为什么只有一部分拼写漏：

| 判据类型 | 写法 | 被段尾空白绕过 |
|---|---|---|
| 精确等于 | `seg == ".."`、设备名 `stem in _DEVICE_NAMES` | **会**（`.. ` / `..\t` / `..\r` / `..\xa0` / `con ` …） |
| 包含子串 | `":" in seg`（ADS）、`"~" in seg`（8.3） | 不会（不看边界） |

#### §19.12.3 修法（`roots.py:151-165`，判据段 15 行含注释）

```python
for seg in segments:
    core = seg.strip()          # 唯一新增判定行；无参数，按 isspace() 剥
    if core in ("", "."):
        continue
    if core == "..":                     _raise("PATH_UNSAFE")
    if core.lower().split(".", 1)[0] in _DEVICE_NAMES:  _raise("PATH_UNSAFE")
    if ":" in core:                      _raise("PATH_UNSAFE")   # ADS
    if "~" in core:                      _raise("PATH_UNSAFE")   # 8.3
```

`core` **只用于判定**，返回给调用方的路径保持原样，**不改写用户拼写**。

**为什么全部判据都用 `core`，而不是只改精确等于型**（复核给出，三条）：
1. `":" in seg` ≡ `":" in seg.strip()`（strip 只删空白，不删 `:`/`~`）→ 改成 `core` **判定完全等价**，不会引入新行为；
2. 若只改精确型，同一段会出现**两套拼写**，认知面更大；
3. `if core in ("", ".")` 的前置跳过**必须**用 `core`，否则 `" . "` 会掉进设备名 `stem` 判据。

**为什么不用 `rstrip`**：`sequence.py:748` 早已是 `seg.strip()`；roots 若只剥右侧，会造成**新的两层不一致**（绝对路径在 `:735` 提前 return 绕过 `:748`，直接由 roots 放行）。

#### §19.12.4 证据（主理人独立实测）

```
12 形态一致性     修前 5/12 不一致  →  修后 0/12
正常路径误杀       0/6            →  0/6
既有应拒放水       0/7            →  0/7
tests/test_roots.py   27 passed    →  41 passed
全量                 249/1         →  263/1
```

红测试（云测，`tests/test_roots.py:345-405`）：7 条形参全部 `DID NOT RAISE`（**完整放行**，不是被 `PATH_OUTSIDE_ROOTS` 兜底误拦）；断言锁死 `code == "PATH_UNSAFE"` 并**显式排除** `PATH_OUTSIDE_ROOTS`。空白族补齐后共 10 条形参。

**红窗口已不可复现**（见 §19.12.8）——红的三条独立证据：① 云测落改动前的 pytest 输出；② 主理人落改动前的探针（`probe/s10_inconsistency.py` 输出 5/12）；③ 复核用**内存重建旧判据 + monkeypatch** 复跑 7 条入参，7/7 ALLOW。

#### §19.12.5 已知代价：这是**真的**误杀，不是零成本

整段 `strip()` 会把「**前导**空格 + 设备名」判成设备名。主理人独立复现：

```
root/ con/f.txt   目录真实存在 = True    文件真实可读 = OK-READABLE
roots 判定        = REJECT PATH_UNSAFE     （修前 ALLOW）
```

**保留这个代价的理由**：与 #24 / #32 同口径——fail-closed 偏严兜底，不追求为病态目录名做精确白名单；若为此改成「只对 `..` 全 strip、设备名只 rstrip」，会把同一个循环拆成两套归一化，认知成本高于收益。**docstring 必须写明这条代价，不许写成「不误杀」。**（#24 原来那句「真实文件叫不到这些名字」的前提，在本条上已被证伪。）

段**内**空格不受影响：`a b/c.png` 修后仍 ALLOW（`strip()` 只动两端）。

#### §19.12.6 已知未收口（本轮明确定为不动）

| 项 | 现状 | 为什么不动 |
|---|---|---|
| `\u200b`(ZWSP) / `\ufeff`(BOM) | 两侧仍 ALLOW | 不属 `str.isspace()`；四套 Win32 API 实测均不可穿越，不构成洞；收口会**扩大** §19.12.5 的误杀面 |
| `_sanitize_roots`（`roots.py:257`）不跑段判 | `C:/x/..` 可被当 root 接受 | `_is_within` 是文本比对、请求侧仍拒 `..`，该 root 实际不可用，**无新增可达面** |
| `...` / `.. .` / `foo.`（尾随点族） | 仍 ALLOW | 无契约依据 + 实测不可穿越；钉住等于把实现细节冻成契约 |

#### §19.12.7 与 B1「新通道从严」的收敛（§19.9 的悬空引用已由本节补上）

| 拼写 | 修前 | 修后 |
|---|---|---|
| `\\server\share\art\.. \x.png`（原生 UNC） | **ALLOW** | **`PATH_UNSAFE`** |
| `file://server/share/art/.. /x.png`（B1） | `POLICY_VIOLATION` | `POLICY_VIOLATION`（`:714` 先触发） |

**两侧都 REJECT ⇒ 收敛方向是「都变严」，护栏没被拆。** `file:` 分支自带那条 `seg.strip()==".."` 判据**保留未删**——删了会把错误码从 `POLICY_VIOLATION` 改成 `PATH_UNSAFE` 并打红 `test_b1_file_scheme_whitespace_dotdot_rejected_in_all_branches`；现在的重复是**保守的重复**（一层值层挡、一层段层挡）。

#### §19.12.8 流程教训（两条，都记在主理人名下）

**（一）我把红窗口毁掉了。** 云测交付红测试后约 **1 分钟**我就落了生产改动；复核方到场时 `roots.py` mtime 已是 13:20，看不到红了，只能用内存重建旧判据补证。

> **规则**：落生产改动**之前**先确认独立复核方已经看过红（或明确同意跳过红）。红是**一次性证据**，窗口关掉就再也开不回来。返工同理——先发「暂停改文件」，等复核方取证完再放开。

**（二）我第三次把「严重性」判错。** 本轮我先判「可利用漏洞」，被自己的探针推翻；上一轮 S12-b 我先判「B 版够硬」，被 `runner_plus_inner` 推翻。

> **规则**：给缺陷定严重性之前，先写出「能证伪它的最小实验」并真的跑；跑不出来就只能标 `[GUESS]`，不许直接写「漏洞」。

#### §19.12.9 宿主部署与实机验证（2026-11-09 13:30，用户明确授权「部署」）

**部署对象**：`%LOCALAPPDATA%/N.E.K.O/.neko-plugin-installations/plugins/artcraft_ops/roots.py`（N.E.K.O 运行时**实际加载**的副本）。

**备份**：`%TEMP%/artcraft_host_backup_20261109-133035/`，含
- `roots.py`（旧版 `3e3461e4…` / 325 行 / 13313 B，**回滚只需覆盖回去**）
- `before_hashes.txt`（部署前 18 个文件的 sha256 + 字节数）
- `before_pycache.txt`

**部署动作**：`cp 工作区/roots.py → 宿主/roots.py`，并删除 `__pycache__/roots.cpython-311.pyc` 强制重编译。

| 核对项 | 结果 |
|---|---|
| 宿主新 `roots.py` | `329422fb…` / 359 行 / **15912 B** |
| 与工作区 `cmp` | **完全一致（无差异）** |
| 全目录哈希对比 | **变更文件数 = 1**（只有 `roots.py`，其余 17 个逐字节未变） |
| pyc 重编译 | `roots.cpython-311.pyc` mtime `13:32` > `roots.py` mtime `13:30:52` ✅ |
| 插件重载 | `POST /plugin/artcraft_ops/reload` → `{"success":true,"message":"Plugin started successfully"}`；状态 `running` / `ready` |

**因果链验证（真宿主 HTTP + 真 CLI，非推断）**

| 阶段 | 输入 | 结果 |
|---|---|---|
| 重载**前**（旧代码） | `steps[].params.file = <DATA>/.. /x.png` | `success: true`，job `job-ef62fffcc767` 排队 → 终态 `failed` / `CLI_USAGE_ERROR`（**已放行到 CLI 构建阶段**，缺主输入文件才失败） |
| —— | `POST /plugin/artcraft_ops/reload` | `Plugin started successfully` |
| 重载**后**（新代码） | 同一条 `.. ` 输入 | `success: false`，`run_status: failed`，**无 job 产生**，message 含 `PATH_UNSAFE: 路径不安全，已拒绝` |
| 重载**后** | 空白族 9 条（`..` + SP/TAB/CR/NBSP/LF/VT/EM-SPACE，`con` + SP/TAB） | **9/9 全部 `PATH_UNSAFE`** |
| 重载**后** | 5 步图层流水线（`plugin_smoke.py` §6.1 那条） | **`succeeded` / `ok: true` / `exit_code: 0`**，输出 PNG **2054 B** |

**「旧版放行、新版拦截」是同一份输入、同一台宿主、同一个入口**，中间只插入一次 `reload` —— 这是 `reload` 让新代码生效的直接证据，不是靠文件时间推断。

**两条对照（证明没有放宽）**

| 输入 | 旧版也拒 | 新版结果 |
|---|---|---|
| 字面 `..`（`<DATA>/../x.png`） | 是 | `PATH_UNSAFE` ✅ |
| 完全越界（`C:/Windows/win.ini`） | 是 | `PATH_OUTSIDE_ROOTS` ✅ |

**顺带发现（pre-existing，不在 S10 范围，记为 S16）**

宿主把入口层返回的 `ArtcraftError` 统一包装成 `error.code = "INTERNAL"`（**HTTP 状态是 200**，`500` 只是 envelope 的 `code` 字段值），真正的码只能从 `error.message` 里 grep（如 `PATH_UNSAFE: 路径不安全，已拒绝`）。**字面 `..`、`PATH_OUTSIDE_ROOTS` 表现完全一致 ⇒ 是宿主 SDK 与插件返回值契约的共性问题，与 S10 无关**。影响：LLM 工具调用拿不到结构化 `error_code`，只能读 message。`run_sequence` 的 `except BaseException → _error_from(exc)` 返回 `errors.error_for(exc)`，该返回值未被宿主识别为业务错误。（2026-11-09 S16 已修：`errors.err()` 改返回宿主 `is_envelope()` 认的裸 dict，`error.code` 原样落 `RunError.code`；上句描述的是修复前的行为。）

**部署残留（如实记）**

- 宿主 `N.E.K.O/plugins/artcraft_ops/data/jobs.json` 多出 3 条测试记录（`job-ef62fffcc767` failed / `job-b10e0e6051be` failed / `job-9c621d1a2621` succeeded）。该文件是**有上限的历史列表**（3 条即当前全部），不影响功能；**未清理**（插件正在运行，改文件会与内存状态不一致）。
- 测试产物 `s10_deploy_good.png` 已删除；`s10_deploy_probe.png` 从未生成（旧版放行那条也因 `CLI_USAGE_ERROR` 未产出）。
- 工作区 `probe/` 下新增的 `s10_*.py` 四个探针**有意保留**为可复跑证据。

**回滚方法（一条命令）**

```
cp "$TEMP/artcraft_host_backup_20261109-133035/roots.py" \
   "$LOCALAPPDATA/N.E.K.O/.neko-plugin-installations/plugins/artcraft_ops/roots.py"
# 然后 POST /plugin/artcraft_ops/reload
```

**本轮第 4 次「判据写错」的记录**：验证负例时我用 `payload["error_code"]` 取码，宿主返回的是 `500`，于是 9 条全被判成「未拒绝」；实际码在 `payload["error"]["message"]`。**改判据后 9/9 命中**。规则同前：**结果全红/全绿时先怀疑自己的判据，再怀疑实现**。

---

### §19.13 工程化收口：清理、打包、版本控制（2026-11-09，**零测试数变化**）

**性质**：不是功能轮次，是「让它像个工程」的收口。判据只有一条 —— **测试数不变、行为不变、静态检查归零**。

```
校验前  263 passed / 1 skipped
校验后  263 passed / 1 skipped     （无增无减）
静态检查 ruff F+E9+B              从 8 条 → 0 条
打包     19 文件 / 98368 B / sha256 39d4fe55…
```

#### §19.13.1 真正清掉的（每条都有依据，不是"看着多余"）

| 项 | 位置 | 依据 |
|---|---|---|
| `_to_long_form`（19 行） | `roots.py` | 全仓零调用；docstring 自称「只用于 data_path 派生」但该派生路径从未实现 |
| `dumps_safe`（3 行） | `bridge.py` | 零调用，且不在 `__all__`；随之移除 `import json` |
| `_lock()` + `_LOCK`（6 行） | `jobs.py` | 零调用。**留着比删掉更危险**：它会让读者以为 `_JOBS` 有并发保护，实际没有 |
| `from . import result as result_mod` | `__init__.py` | 导入后零使用（`result` 的真使用者是 `jobs.py`） |
| `from .i18n import t` | `jobs.py` | 零使用 |
| `import os` / 未用变量 `spec` | `tests/test_roots.py` / `test_families17.py` | ruff F401 / F841 |
| `spec/ARCHITECTURE.md`（43 KB） | `spec/` | FINAL 文首已明写「本文件是唯一有效版本，ARCHITECTURE.md 已作废」。**作废文档与有效文档共存是事故源**（有人照着旧稿改） |
| `.pytest_cache` ×3 / `__pycache__` ×4 | 源码树 | 生成物，可再生 |

#### §19.13.2 修掉的三个「静默失效」（比死代码严重）

**（一）`pytest.ini` 放错目录 → 裸跑 pytest 根本不加载它**

`tests/pytest.ini` 在裸跑 `pytest`（rootdir = 插件根）时**不会被读取**，于是：

- `asyncio_default_fixture_loop_scope` 没生效 → 每轮喷 `PytestDeprecationWarning`
- `markers` 没注册 → `-m integration` 的有效性依赖 pytest 的宽松行为

已搬到 `artcraft_ops/pytest.ini`，并加 `--strict-markers`（marker 拼错直接报错）+ `testpaths = tests`。
实测：裸跑与 `pytest tests/` 两种方式结果一致、警告消失、`-m integration` 精确命中 1 条 skip。

**（二）`_fixtures.py` 的数据来源指向已作废文档，且相对路径少一级**

```
改前  ../spec/ARCHITECTURE.md          （文件不存在 + 已作废）
改后  ../../spec/ARCHITECTURE-FINAL.md （tests/ 到项目根是两级）
```

**（三）两处文档与实现相反**

| 位置 | 原文 | 实际 |
|---|---|---|
| `errors.redact_text` | 「命中任一凭证形态 → **整段替换**为 `***`」 | `pattern.sub` = **局部替换**，未命中部分原样保留 |
| `_debug_records_naming` | 「既接受 `logger.debug(..., exc_info=True)` 的标准形态」 | `locator.py:36` **明文禁止** `exc_info` —— 它会把未脱敏的原始 traceback 挂到 record 上，绕过 `redact_text` |

第二条不只是文字问题：**后人照这句去"修正"实现，就会引入脱敏绕过**。已在 docstring 里写明「这个分支只是兼容兜底，生产必须走预格式化参数分支」。

#### §19.13.3 评估后**决定保留**的（附理由，避免下一轮重复讨论）

| 项 | 为什么留 |
|---|---|
| `bridge.py`（131 行）+ `studio_channel.py`（55 行） | spec §2/§6 明文要求「v1 只留接口不实现」。**删了是推翻设计，不是清理**；建议由产品侧另行裁决，本轮不动 |
| `validate_tool_kwargs`（零生产调用） | 它的测试 docstring 写明「当前是死代码，但契约要守住……将来一接线就是同一个漏洞」。删它等于拆掉一条**已登记的安全契约** |
| `build_root_flags`（零生产调用，spec:1661 曾称死代码） | 它的测试里编码了两条实测事实：`mcp --bridge` 传 root 会 exit 2、`PHOTOCRAFT_AUTOMATION_*` 只作用桌面进程。**15 行代码换两条实测结论，不删** |
| `roots.resolve_and_check(..., direction)` 形参 | spec:890 已定义为「语义标签，不参与判定」。删它要改 17 处且无行为收益；改为在定义处把「不参与判定」写实，消掉误导即可 |
| `probe/*.json`（1.1 MB 命令目录） | `sequence.py` 两处 docstring 以它为证据底座（993/933 条命令全量扫描）。**删证据 = 让结论失去可复核性** |

#### §19.13.4 唯一的行为变更（如实记）

`command_catalog.py` 的 `except (asyncio.TimeoutError, TimeoutError)` 里补了 `as exc` + `raise ... from exc`（ruff B904）。
原先靠 Python 隐式链接（`__context__`），现在显式声明因果。**对外错误码、消息文本、控制流全部不变**，只有 traceback 的措辞从「During handling of the above exception」变成「The above exception was the direct cause」。

#### §19.13.5 版本控制（本仓库此前**没有 git**）

这是本轮最大的工程缺口：所有改动不可追溯、不可回滚、不可 bisect。已落地：

- `git init -b main`，首次提交 `38d1533`（110 个文件）
- `.gitattributes` 锁 `eol=lf` —— **因为宿主部署校验是逐字节 sha256**，git 若把 LF 转 CRLF，部署校验会全部失配
- `.gitignore` 覆盖 `__pycache__` / `.pytest_cache` / `dist/` / 运行期 `data|cache|config` / `*.orig|*.rej`
- 清理前全量快照在**项目外**：`%USERPROFILE%\WorkBuddy\_snapshot_2026-11-07-22-33-50_20261109-135145`（113 文件 / 2.2 MB）。放项目内会被自己 ignore，等于没有

#### §19.13.6 发布包（可复现）

`tools/build_release.py`，三条硬约束：

1. **只打运行时必需**：16 个 `.py` + `plugin.toml` + `config.example.toml` + `README.md` = 19 个文件。`tests/` `probe/` 不进包
2. **固定时间戳**（git 提交时间，可用 `SOURCE_DATE_EPOCH` 覆盖）→ 同内容重复打包得到**同一份 sha256**。已实测两次打包哈希一致
3. **打包后回读自检**：解压 → 校验条目集合/顶层目录名 → 逐文件 sha256 → 全模块 `py_compile` → 版本一致性

```
dist/artcraft_ops-1.0.0.zip   19 文件 / 98368 B
sha256  39d4fe550929771cc067e7535ed0bcd6402e6a4188abaf39a83be008a6869f6f
dist/SHA256SUMS.txt           包 + 每个源文件的哈希
```

**功能级验证（不是只看 zip 能解开）**：把 `tests/` 与 `pytest.ini` 搬进**解压后的包**再跑全量 →
`263 passed, 1 skipped`。即「打出来的这份东西，本身就能通过完整测试」。

#### §19.13.7 未做（明确留白，不是遗忘）

| 项 | 原因 |
|---|---|
| 拆 `test_params_roots.py`（1732 行 / 87 KB） | 结构本身是按编号分段的，拆分纯属搬运；在无 VCS 历史时做大范围移动，diff 会失去可读性。等有 git 历史后再做 |
| 引入 mypy / bandit | 未安装；且 `mypy` 对该项目的 `Any` 密集签名会产出大量噪声，需要单独一轮定 baseline |
| `bridge.py` / `studio_channel.py` 去留 | 见 §19.13.3，属产品裁决 |

#### §19.13.8 换行归一与公开发布（2026-11-09 14:0x，用户选定「推 GitHub 公开仓库」）

**（一）先发现一个会让「逐字节一致」失效的坑**

仓库里 **6 个模块是纯 CRLF**（`__init__` `app_registry` `command_catalog` `locator` `sequence` `spec`），其余 14 个是 LF，`tests/test_app_registry.py` 也是 CRLF。

`.gitattributes` 声明 `eol=lf` 后，**索引里是 LF、我本机工作区是 CRLF** —— `git status` 显示干净，但别人 clone 下来得到的是 LF，与我部署到宿主的字节**不一致**。也就是说「宿主 19 个文件逐字节一致」这句话只在**我这台机器上**成立，一旦换机器全部失配。

**这是本轮唯一一个"差点没发现"的问题**：它不报错、不影响测试、不影响运行，只在你真的去核对哈希时才咬人。

处置：全部归一为 LF（顶层 6 个 + 测试 1 个）。Python / TOML / Markdown 均不敏感，`263 passed / 1 skipped` 不变。重打包哈希随之变化，已重部署 + 重载 + 重冒烟。

**（二）敏感信息扫描（公开前的强制步骤）**

| 类别 | 结果 |
|---|---|
| 密钥/令牌赋值 | **0 处** |
| 内网 IP | **0 处** |
| 64 位 hex | 14 处，**全部是源码 sha256**（在 memory 里），不是凭证 |
| 邮箱 | 2 处，是文件名 `foo@2x.png` / `Icon@2x.png`，误报 |
| 绝对用户路径 | 22 处，去掉 `spec/` `probe/` `.workbuddy/` 后，源码里只剩 1 处占位名 `D:/Users/Alice`（测试夹具） |
| 宿主内部文件名 | 28 处，其中 22 处在 `spec/`；源码内 6 处是解释「为什么这么写」的注释 |

`config.example.toml:4` 原本写死 `C:/Users/<本机用户名>/AppData/...`，已改为 `%LOCALAPPDATA%\...` —— **示例配置不该带任何人的用户名**。

**（三）公开范围（有意收窄）**

| 内容 | 公开 | 理由 |
|---|---|---|
| `artcraft_ops/`（源码 + 测试） | ✅ | 插件本体，43 文件中的 38 个 |
| `tools/build_release.py` | ✅ | 可复现打包器 |
| `spec/` | ❌ | 190 KB 内部设计日志，含宿主实现细节与逐轮决策 |
| `probe/` | ❌ | 1.6 MB，含厂商 CLI 命令目录（pc/vc/ca）与实测证据 |
| `.workbuddy/memory/` | ❌ | 个人工作日志 |

**公开树自足性已验证**：去掉 `probe/` 与 `spec/` 后，在公开树内跑全量仍是 `263 passed / 1 skipped`（测试用 `tests/_fixtures.py` 自带数据，不读那些目录）。

**（四）发布结果**

```
仓库   https://github.com/addsas222/artcraft_ops        （PUBLIC，43 文件）
Release https://github.com/addsas222/artcraft_ops/releases/tag/v1.0.0
资产   artcraft_ops-1.0.0.zip   98083 B
sha256 6837333df59f734869d0339571df0cbcc5620c8de609b327486b00a3d485adbe
```

**发布资产回验**：把 Release 上的 zip **下载回来**重新算 sha256，与本地构建**完全相同**（不是只看上传成功）。

**（五）两个如实说明**

1. **本仓库未附开源许可证**（All rights reserved）。公开 ≠ 授权，别人不能合法使用/再分发。要真正开源需单独选许可证（MIT / Apache-2.0 等），这是产品裁决，本轮不做。
2. 源码里少数 docstring 仍引用 `../spec/` 与 `../probe/` —— 那些是内部路径，在公开仓库里是悬空的。**没有改它们**：改了会让公开源码与「已部署、已测、已打包」的字节不一致。已在顶层 README 的「仓库范围说明」里讲清。

---

### §19.14 许可证落地：GPL-3.0-only（2026-11-09，**零代码改动**）

§19.13（五）第 1 条把「无许可证」列为待裁决项。用户裁决为 **GPLv3**，本节记录落地与验证。

**（一）裁决与口径**

| 项 | 取值 | 说明 |
|---|---|---|
| 许可证 | GNU GPL v3.0 | 用户明确选择 |
| SPDX | `GPL-3.0-only` | 未写 "or any later version"，按字面取「仅此版本」 |
| 版权人 | `neko-artcraft-ops` | 项目名而非自然人或法人；如需改为个人/公司，只改 README 署名行 |
| 覆盖范围 | 私有仓全部（含 `spec/`、`probe/`）+ 公开仓全部 | 两仓根各一份 `LICENSE` |

**（二）落盘物**

```
LICENSE                     35149 B / 674 行 / 纯 LF  sha256 3972dc97…
artcraft_ops/LICENSE        同上（逐字节一致）
```

来源：`https://www.gnu.org/licenses/gpl-3.0.txt` 原样下载，未做任何字符级修改（GPL 要求不得改动正文）。
两份副本是**必要重复**：根目录供平台识别许可证，插件目录那份随发布包分发。

`tools/build_release.py`：`LICENSE` 进入 `REQUIRED_EXTRA`。GPLv3 §4 明确要求「向其他接收者一并提供本许可证副本」，所以这不是可选文件——漏了打包自检直接失败，而不是打出一个合规性靠运气的包。

**（三）版本与内容边界**

`plugin.toml` 版本 `1.0.0 → 1.0.1`。`v1.0.0` 的 Release 资产保持不可变（不改 tag、不覆盖资产）。
`git diff --name-only v1.0.0 v1.0.1 -- 'artcraft_ops/*.py'` → **0 个文件**：本次只动许可、README 与打包器，插件逻辑逐字节不变。

**（四）验证证据（每项都可复跑）**

| 检查 | 结果 |
|---|---|
| 全量测试（工作区） | `263 passed / 1 skipped` |
| 全量测试（解包目录内重跑） | `263 passed / 1 skipped` |
| ruff `--select F,E9,B` | All checks passed |
| 连续两次打包 | sha256 均 `cf0a6e46…`（110745 B / 20 条目） |
| 宿主 20 文件逐字节比对 | 不一致 0 个 |
| 宿主加载 | `version=1.0.1` / `status=running` / `runtime_startup_state=ready`，6 入口 + 6 LLM 工具 |
| 实机冒烟（5 步图层流水线） | `succeeded` / exit 0 / PNG 2054 B 640×400 |
| 平台许可证识别 | API `license.spdx_id = "GPL-3.0"`（不是靠肉眼说"我加了"） |
| Release 资产回验 | 从公网下载 110745 B，sha256 与本地一致；包内 `LICENSE` 哈希 `3972dc97…` |

**（五）三件如实说的事**

1. **没有给 30+ 个 `.py` 加逐文件 SPDX 头。** 不是遗漏：`spec/` 里存在 85 处 `文件:行号` 引用，统一插两行注释会让这些引用集体偏移两行，而本项目已经因为行号/行数漂移吃过三次亏。版权声明以两份 `LICENSE` + 两份 README 的许可段为准；若将来要加逐文件头，必须同批修正所有行号引用。
2. **版权人写的是项目名。** `Copyright (C) 2026 neko-artcraft-ops` 不是自然人或法人，严格说不构成完整的版权归属声明。要主张权利或走商业化，得换成真实主体名。
3. **未做法律评估。** GPLv3 是强著佐权：分发修改版必须同样开源。插件通过子进程调用厂商 CLI、通过 HTTP 与宿主交互，「是否构成宿主的衍生作品」这类问题没有专业意见，本节只记录技术事实，不作法律结论。

**（六）遗留**

- ~~S16~~ **已修**，见 §19.15（错误出口改 envelope，`error.code` 原样落 `RunError.code`）；顺带订正：原记录的「HTTP 500」是误记，实测状态码恒 200
- `bridge.py` / `studio_channel.py` 两个 v1 预留桩的去留仍未定（§19.13 已评估：保留）

---

### §19.15 S16 修复：错误码改由 envelope 承载（2026-11-09，**生产改动 1 文件**）

**（一）先订正一条误记**

§19.14（六）与旧 README 都写「宿主把入口失败包装成 HTTP 500 / `error.code=INTERNAL`」。**`HTTP 500` 是错的**：`POST /runs` 与 `GET /runs/{id}` **恒返回 200**，`500` 只是回包里的 `code` 字段值。真正的病根是另一件事：

> 插件当年把错误码塞进 `SdkError(message)` 的**文本**里（`"<CODE>: <文案>"`），而 `SdkError.code` 从未被填充。宿主四条链路都只能取 `str()`，于是码变成不可解析的散文。

**（二）四个压平点（`[FACT]`，逐行核过）**

| # | 位置 | 行为 | 丢掉什么 |
|---|---|---|---|
| D1 | `entry_executor.py:169-173` | `Err` 且 `is_ok()` 假 → 抛 `PluginExecutionError(str(result.error))` | `SdkError.code`/`details` |
| D2 | `entry_executor.py:220-229` | `except Exception` → `PLUGIN_EXECUTION_FAILED` | 任何异常类型与码 |
| D3 | `communication.py:334-335` | `RpcRemoteError → PluginExecutionError(exc.message)` | `RpcRemoteError.code`（`rpc.py:150-158` 明明有） |
| D4 | `trigger_service.py:410-420` | `except PluginError → fail(ErrorCode.INTERNAL)` | **`INTERNAL` 的产生点** |

⚠️ D4 的 `PluginError` 分支在 `:433-443` 的 `ValueError/TypeError/KeyError → VALIDATION_ERROR` 特判**之前**，所以「抛对异常类型」对插件永不生效——异常路线整条堵死。

**（三）唯一活路：返回 envelope**

`is_envelope()`（`plugin/core/responses.py:146-173`）只查三件事：`dict` + `success ∈ {True,False}` + 有 `error` 与 `time`。命中即被 `_normalize_plugin_response`（`trigger_service.py:457-474`）原样保留，再由 `runs/manager.py:695-711` 读 `error.code` 落成 `RunError.code`（`str`，无白名单）。

`errors.err()` 因此改为返回裸 dict：

```
success: False   code: 500(int)   data: None   message: ""   time: ISO8601
error: {code, message:"<CODE>: <脱敏文案>", details: 标量≤8键|None, retriable: false}
is_error: True                       # @llm_tool 通道靠它透传（llm_tools.py:243-262）
output: {error_code, error_message}  # 模型可见的干净字段
```

**（四）三条反向护栏（改回去就等于撤销 S16）**

1. **不要试图把外层 `reply["success"]` 改成 `False`**。`entry_executor.py:201` 是无条件置 `True`，插件控制不到；而外层一旦为假，`RpcRemoteError → PluginError → fail(INTERNAL)` 会把码**重新抹平**。「外层 True + `data` 里包 envelope」不是疏忽，是现有宿主下唯一能保码的形状。
2. **envelope 的键必须是字面量**，`kwargs` 只许进 `details`。禁止 `err(code, **payload)` 透传，否则白名单整体失效。
3. **成功路径继续走 `Ok`**，不许也套 envelope（`_normalize_plugin_response` 对普通 Mapping 会 `ok(data=...)`，套错就是把成功包成失败）。

**（五）验证证据**

| 检查 | 结果 |
|---|---|
| 全量测试 | `291 passed / 1 skipped`（基线 263 → 新增 28 条错误面用例） |
| ruff `--select F,E9,B` | All checks passed |
| 变异有牙（副本内故意破坏 3 处） | 去键名脱敏 → 仅对应 1 条红；模板插 `{token}` → 仅 i18n 护栏红；删 `time` → 3 条红 |
| 真宿主·未注册命令 | `error.code="POLICY_VIOLATION"`，`details.reason` 齐（**修复前为 `INTERNAL`**） |
| 真宿主·越界路径 | `error.code="PATH_OUTSIDE_ROOTS"`，`details.path` 齐 |
| 真宿主·成功入口 | `succeeded`，无 `error` 字段 |
| 真宿主·真实 CLI 流水线 | `succeeded`，PNG 2054 B（成功路径零回归） |
| 宿主文件一致性 | 20 文件逐字节 0 不一致；`version=1.0.1 / running / ready` |

**（六）审查捞出的真问题（已修）**

`details` 的**键名脱敏从未生效**：`_details_from` 只对裸值调 `redact(value)`，而凭证键规则 `_is_secret_key` 只在 `redact({k:v})` 的 dict 分支触发。实测 `err("X", control_token="plain-token-value")` 原样回包。今日无调用点传此类 kwarg（不可达），但 `details` 是**新增的机器可读出口**，一行插值即泄露。已改为 `_MASK if _is_secret_key(key) else redact(value)`，并补 7 键负向用例 + 19 条 i18n 模板静态护栏（0 命中）。

**（七）遗留与已知代价**

| 项 | 状态 |
|---|---|
| legacy socket `TRIGGER` 回包外层 `success=True`（失败藏在 `data`） | 仓内唯一消费者 `communication.py:701` 已核安全；仓外老客户端会误读。属宿主协议层，插件侧无解 |
| `@llm_tool` 通道的 `error` 字段是 `str(dict)` | `llm_tools.py:253` 决定的，插件侧无解；干净信息在 `output` |
| `time` 只有秒级 | Windows `strftime` 不支持 `%f`，且宿主不解析该字段 |
| `details` 未脱家目录/用户名 | 归 **S17**：要给 `redact_text()` 加规则，属全局改动，本轮刻意不做 |
| 沙盒把 dict 判 `RAW` 并打印「the host only understands Ok/Err」 | **假红**：`sandbox/runner.py:499-505` 少一个 Mapping 分支，宿主无此限制。A/B 实测 `check` 退出码改动前后都是 1 |
