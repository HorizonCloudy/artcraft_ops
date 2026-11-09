"""artcraft_ops —— 叶子层：全部 dataclass / Enum / 常量。

设计约束（来源 `spec/ARCHITECTURE-FINAL.md`）：
  * §2 关键数据结构：本文件是唯一权威定义处，其余模块只 import 不重定义。
  * 依赖方向：本文件不 import 任何兄弟模块（真·叶子），避免循环。
  * 全部 ``frozen=True`` 的配置型 dataclass 支持 ``dataclasses.replace()``
    （测试用 ``replace(spec, cli_path=...)`` 注入 CLI 路径）。

[FACT 链 2] 消息面 512 KiB 整条丢弃：`plugin/settings.py:610`
``MESSAGE_PLANE_PAYLOAD_MAX_BYTES = 512 * 1024`` —— 超限连文本一起丢，
故大响应必须落盘只推句柄（§13.2 P3 / §14.1）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional

PLUGIN_ID = "artcraft_ops"

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------
class AppId(str, Enum):
    """v1 三个已实机核验的 app；其余 10+ 个 app 走运行时配置插槽（§3）。"""

    PHOTOCRAFT = "photocraft"
    VECTORCRAFT = "vectorcraft"
    CRAFT_ANYTHING = "craft_anything"


class Transport(str, Enum):
    """调用通道。v1 只实现 ``CLI_RUN``，其余三个是预留接口（不实现）。"""

    CLI_RUN = "cli_run"            # 一次性无头 run；单进程、无远端 job
    CLI_SERVE = "cli_serve"        # 预留：常驻 serve（有 jobs.cancel）
    HTTP_BRIDGE = "http_bridge"    # 预留：回环控制通道（v1 关闭，#9777 未修）
    STUDIO_CLOUD = "studio_cloud"  # 预留：Studio 云端生成（不读写本地）


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CRASHED = "crashed"


class RunMode(str, Enum):
    HEADLESS = "headless"   # 插件自己拉起 CLI：必须显式传 --automation-*-root
    BRIDGE = "bridge"       # roots 配在桌面启动命令，不往 mcp --bridge 传


TERMINAL_STATUSES: frozenset[str] = frozenset({
    JobStatus.SUCCEEDED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
    JobStatus.CRASHED.value,
})


# ---------------------------------------------------------------------------
# per-app adapter（§3 差异表 + §16.3 新增 5 字段）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AppSpec:
    app_id: str
    display_name: str
    version: str
    cli_path: str = ""                       # locator.resolve_cli 运行时填充（绝对路径）
    cli_candidates: tuple[str, ...] = ()     # 本机实测安装位置兜底
    install_root: str = ""
    input_mode: Literal["positional", "flag_in", "flag_project"] = "positional"
    input_flag: str = ""                     # ""=位置参数 FILE；否则 "--in"/"--project"
    new_flag: str = "--new"
    output_flag: str = "--out"
    output_repeatable: bool = False           # VectorCraft --export 可多次
    cmd_flag: str = "--cmd"
    params_flag: str = "--params"
    commands_flag: str = ""                   # VectorCraft "--commands"
    commands_subcmd: str = "commands"         # PdfCraft="tools"
    commands_json_flag: str = "--json"
    filter_flag: str = "--filter"
    has_disabled_reason: bool = True
    has_needs_document: bool = True
    has_serve: bool = True                    # VectorCraft 无 serve
    serve_namespace: str = "doc"
    engine_namespace: str = "document"
    control_port: int = 0                     # 0=动态(instance.json)；VectorCraft=7979
    instance_json: str = ""
    transport: Transport = Transport.CLI_RUN
    builder: str = "run_cmd"
    readonly_prefixes: tuple[str, ...] = (
        "document.inspect", "session.list", "jobs.list", "command.list",
    )
    newline_json: bool = True
    flatten_warning_substr: str = (
        "flattened; layers, masks and blend modes are not kept"
    )
    # --- §15.1 / §16.3 新增（per-app 解析器与能力门控，不能只靠 has_* 布尔）---
    supports_filter: bool = True              # Craft Anything=False（无 --filter）
    commands_parser: str = "photocraft"       # "photocraft" | "craft_anything"
    filter_mode: str = "server"               # "server" | "local"
    capabilities_gate: bool = False           # CA=True：commands.json != 可用命令表
    output_format_flag: str = ""              # CA="--output-format json"（避开 --json 双义）
    params_mode: str = "flag"                 # "flag" | "file" | "stdin"
    # --- §19 全 17 app 覆盖新增（2026-11-08 实机逐个摸出，6 个 argv 族）---
    # cmd_join 决定「命令 + 参数」怎么并进 argv：
    #   "flag"        --cmd ID --params JSON        （PhotoCraft）
    #   "inline"      --cmd 'ID={json}'             （CADCraft/SoundCraft/DeckCraft/DesignCraft/GridCraft/WordCraft）
    #   "positional"  <ID> [<json>]                 （TextureCraft/MaterialCraft/EffectCraft/LightCraft）
    #   "kv"          <tool> [key=value ...]        （PdfCraft）
    cmd_join: Literal["flag", "inline", "positional", "kv"] = "flag"
    # 出图/保存 flag。B 族普遍是 --save 或 --export，与 A 族的 --out 不同。
    # 空字符串 = 退回 output_flag。
    save_flag: str = ""
    # 命令目录获取方式：内置 4 种实机形态（见 §19.2）
    #   "json_flag"     commands --json   （12 个 app，含 GnuCOBOL 风格与 JSON 默认）
    #   "plain"         commands          （纯文本，需按行解析）
    #   "none"          无 commands 子命令（PdfCraft）→ 用 `--help` 的 tool 列表兜底
    commands_mode: Literal["json_flag", "plain", "none"] = "json_flag"
    # 单命令型 app：一个 run 只吃一条命令，多步必须落脚本文件
    single_step: bool = False
    # 位置参数族的 `<input>` 是否**必填**。TextureCraft / MaterialCraft 的签名是
    # `run <input> <command-id> [params]`——没有 input 时若照常拼 argv，命令 id 会
    # 落进 <input> 槽，CLI 报 `missing <command-id>`（实机踩到）。这种必须 fail-closed。
    requires_input: bool = False
    # 脚本通道（single_step 或 D 族用）：子命令与文件格式
    #   script_subcmd: "run" | "batch"
    #   script_format: "array_cmd"  [{"command":id,"params":{}}]
    #                  "array_id"   [{"id":id,"params":{}}]
    #                  "jsonl_id"   每行 {"id":id,"params":{}}
    #                  "batch_dict" {"open":in,"steps":[...],"save":out}
    #                  "array_tool" [{"tool":id,"args":{}}]
    script_subcmd: str = ""
    script_format: str = ""
    # `--commands FILE|-` 这种「从文件读步骤」的 flag（EffectCraft）
    script_flag: str = ""


@dataclass(frozen=True)
class CommandSpec:
    id: str
    label: str = ""
    menu: tuple[str, ...] = ()
    params_schema: str = ""                   # 人类可读 mini-schema，原样喂 LLM
    enabled: bool = True
    disabled_reason: Optional[str] = None     # VectorCraft/CA 无此字段 -> None
    needs_document: Optional[bool] = None
    shortcut: str = ""
    readonly: bool = False
    alias: str = ""                           # CA: 下划线工具名（与连字符 command 并存）


@dataclass(frozen=True)
class RootsPolicy:
    read_roots: tuple[str, ...] = ()
    write_roots: tuple[str, ...] = ()
    mode: RunMode = RunMode.HEADLESS
    read_flag: str = "--automation-read-root"
    write_flag: str = "--automation-write-root"
    allow_user_dirs: bool = False             # 用户显式目录需配置批准、重启生效


# ---------------------------------------------------------------------------
# 运行态
# ---------------------------------------------------------------------------
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
    is_edit_sequence: bool = False            # 含任一编辑命令 -> 整体编辑类，绝不重发
    retried_readonly: bool = False
    error_code: str = ""
    error_message: str = ""
    result_path: str = ""
    result_handle: str = ""                   # 大响应落盘后的句柄（消息面只推它）
    warnings: tuple[str, ...] = ()
    attempts: int = 0
    transport: Transport = Transport.CLI_RUN
    remote_job_id: str = ""                   # serve/bridge 的 ArtCraft 侧 job id
    cancel_method: str = ""                   # 如 "jobs.cancel"；空=主通道无远端取消命令
    step_total: int = 0                       # 计划步数（进度分母）
    step_done: int = 0


@dataclass
class ExecResult:
    ok: bool
    app_id: str
    job_id: str = ""
    exit_code: int = 0
    steps: tuple[dict[str, Any], ...] = ()
    inspect: Optional[dict[str, Any]] = None   # document.inspect 完整结构（LLM 的眼睛）
    warnings: tuple[str, ...] = ()
    output_files: tuple[str, ...] = ()
    error_code: str = ""
    error_message: str = ""
    user_message: str = ""
    elapsed_ms: int = 0
    result_handle: str = ""                    # 落盘 data_path() 的完整结果文件路径
    preview_url: str = ""                      # ctx.images.upload() 的 URL part
    payload_bytes: int = 0
    spilled: bool = False


# ---------------------------------------------------------------------------
# 入参
# ---------------------------------------------------------------------------
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
    deny_prefixes: tuple[str, ...] = ("prefs.", "plugin.", "updates.")
    require_registry_match: bool = True
    require_confirm_on_write: bool = False
    max_steps: int = 256


@dataclass(frozen=True)
class SpillPolicy:
    max_bytes: int = 512 * 1024
    spill_dir: str = ""
    preview_via_upload: bool = True
    inline_base64_forbidden: bool = True
    redact_output: bool = True


# ---------------------------------------------------------------------------
# 常量（§2 / §6 / §13.2 / §14.1）
# ---------------------------------------------------------------------------
MESSAGE_PLANE_MAX_BYTES: int = 512 * 1024        # plugin/settings.py:610
MAX_REQUEST_LINE_BYTES: int = 1_048_576          # 1 MiB 单行
MAX_REPLY_BYTES: int = 8_388_608                 # 8 MiB 累计
PUSH_MIN_INTERVAL_S: float = 2.0
JOB_TTL_S: int = 3600
CONTROL_CALL_TIMEOUT_S: float = 60.0             # 对齐官方 55s 客户端下限
DISCOVERY_ENTRY_BUDGET_S: float = 13.0           # entry 15s 内的发现预算
PREVIEW_MAX_PX: int = 2048
PREVIEW_MAX_PNG_BYTES: int = 5_242_880

DEFAULT_READ_FLAG = "--automation-read-root"
DEFAULT_WRITE_FLAG = "--automation-write-root"
DEFAULT_MAX_STEPS = 256

# §13.2 P3：push_message 双轴（内部进度不注入模型）
INTERNAL_PROGRESS: dict[str, Any] = {"visibility": [], "ai_behavior": "blind"}
USER_COMPLETION: dict[str, Any] = {"visibility": ["chat"], "ai_behavior": "blind"}
# §14.3：这两个值会让内容在下一次 hot swap 作为背景上下文进新 session
FORBIDDEN_AI_BEHAVIORS: tuple[str, ...] = ("read", "respond")

USER_COMPLETION_TEMPLATE = "任务 {job_id} 已完成：{status}；结果句柄 {result_handle}"
FIXED_PROGRESS_TEMPLATE = "任务 {job_id} 进度 {pct}%：{phase}"

# §13.2 P4：status dict 整条会被 logger.info 落盘 -> 只放非密字段
REPORT_STATUS_ALLOWLIST: tuple[str, ...] = ("job_id", "phase", "progress", "error_code")

# §13.2 P2：@llm_tool 返回值字段白名单（宿主原样透传不过滤）
TOOL_OUTPUT_ALLOWLIST: frozenset[str] = frozenset({
    "job_id", "status", "poll_with", "cancel_with", "progress", "progress_label",
    "phase", "error_code", "error_message", "result_handle", "preview_url",
    "app", "version", "total_matched", "hint", "commands", "apps",
    "exit_code", "ok", "warnings", "output_files",
    # Studio 本地 GUI 交接（handoff_verified 恒 False：Popen 成功 != 窗口已加载）
    "launched", "studio_available", "file_count", "handoff_verified",
    "exe_path", "note",
})

# 静态命名空间白名单（fail-closed 兜底）：entry handler 内**禁止**为了做注册表
# 校验而 spawn 子进程（入口必须秒回、测试断言 create_subprocess_exec 零调用），
# 所以入口层只做「形状 + 命名空间白名单」校验；真正的全量注册表校验放到后台 job，
# 那里可读 discovery 缓存（load_cache，不 spawn）。见 §10 #29 / §13.2 P5 的落地说明。
STATIC_ALLOWED_PREFIXES: tuple[str, ...] = (
    "file.", "edit.", "image.", "layer.", "select.", "filter.", "view.",
    "window.", "document.", "session.", "jobs.", "command.", "tools.",
    "script.", "animation.", "timeline.", "measurement.", "symmetry.",
)
STATIC_ALLOWED_EXACT: frozenset[str] = frozenset({
    "document.inspect", "session.inspect",
    "edit.fill", "layer.new.layer", "layer.new.group",
})

__all__ = [
    "PLUGIN_ID",
    "AppId", "Transport", "JobStatus", "RunMode", "TERMINAL_STATUSES",
    "AppSpec", "CommandSpec", "RootsPolicy", "JobState", "ExecResult",
    "Step", "Source", "CommandPolicy", "SpillPolicy",
    "MESSAGE_PLANE_MAX_BYTES", "MAX_REQUEST_LINE_BYTES", "MAX_REPLY_BYTES",
    "PUSH_MIN_INTERVAL_S", "JOB_TTL_S", "CONTROL_CALL_TIMEOUT_S",
    "DISCOVERY_ENTRY_BUDGET_S", "PREVIEW_MAX_PX", "PREVIEW_MAX_PNG_BYTES",
    "DEFAULT_READ_FLAG", "DEFAULT_WRITE_FLAG", "DEFAULT_MAX_STEPS",
    "INTERNAL_PROGRESS", "USER_COMPLETION", "FORBIDDEN_AI_BEHAVIORS",
    "USER_COMPLETION_TEMPLATE", "FIXED_PROGRESS_TEMPLATE",
    "REPORT_STATUS_ALLOWLIST", "TOOL_OUTPUT_ALLOWLIST",
    "STATIC_ALLOWED_PREFIXES", "STATIC_ALLOWED_EXACT",
]
