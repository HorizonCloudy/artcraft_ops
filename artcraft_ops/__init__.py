"""artcraft_ops —— 应用层：入口 + LLM 工具双注册 + lifecycle。

三条硬约束（§17.4，真 SDK 实测）：
  1. **全部是类方法**：宿主只从 ``instance.collect_entries()`` 收集
     （`host.py:1204-1226`），模块级函数**永远不注册**（= 入口 404 的根因）；
  2. **全部 ``async def``**：同步入口被 `entry_executor.py:106-109` 直接拒；
  3. 签名必须吃 ``**_``：宿主会注入 ``_ctx`` 等额外 kwarg（`entry_runtime.py:197-202`）。

两条路径 timeout **各自独立**：``@plugin_entry(timeout=)`` 与 ``@llm_tool(timeout=)``
都要显式设（`base.py:517-545`）；``@llm_tool`` 的注册硬上限是 300s。

安全（§13.2）：
  * 所有对外返回先过 ``tool_output()`` 字段白名单 + ``redact()``；
  * 所有失败走 ``errors.safe_error`` 固定码，异常对象**不插值**；
  * ``ctx.images.upload()`` 只在结果阶段调，**lifecycle 里绝不出现**。
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
from typing import Any

from plugin.sdk.plugin import (  # noqa: F401 - SdkError 供骨架/文档对齐
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from . import (
    app_registry,
    command_catalog,
    config as config_mod,
    errors,
    jobs,
    locator,
    roots as roots_mod,
    sequence,
    spec,
    studio_local,
)

# ---------------------------------------------------------------------------
# 手写 JSON Schema（必须是**纯字面量**，AST 对账直接 literal_eval）
# ---------------------------------------------------------------------------
SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "自然语言或关键词，如 调亮 / sharpen / 新建图层；服务端按此过滤命令注册表",
        },
        "app": {
            "type": "string",
            "enum": [
                "photocraft", "vectorcraft", "craft_anything",
                "cadcraft", "soundcraft", "deckcraft", "designcraft",
                "gridcraft", "wordcraft", "texturecraft", "materialcraft",
                "effectcraft", "lightcraft", "figcraft", "filmcraft",
                "solidcraft", "pdfcraft",
            ],
            "description": "目标 app；缺省取运行时配置 default_app（默认 photocraft）",
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "description": "默认 8",
        },
    },
    "required": ["query"],
}

RUN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "app": {
            "type": "string",
            "enum": [
                "photocraft", "vectorcraft", "craft_anything",
                "cadcraft", "soundcraft", "deckcraft", "designcraft",
                "gridcraft", "wordcraft", "texturecraft", "materialcraft",
                "effectcraft", "lightcraft", "figcraft", "filmcraft",
                "solidcraft", "pdfcraft",
            ],
        },
        "steps": {
            "type": "array",
            "maxItems": 256,
            "description": "按序执行的命令；cmd 用引擎命令名（document.* 而非 doc.*）；params 依据 params_schema 填",
            "items": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["cmd"],
            },
        },
        "input_file": {
            "type": "string",
            "description": "绝对路径；须在已授权 read root；与 new_doc 二选一",
        },
        "new_doc": {
            "type": "object",
            "description": "如 {width:640,height:400,background:'white'}",
        },
        "out_file": {
            "type": "string",
            "description": "输出文件名或绝对路径；写入 write root",
        },
        "inspect": {
            "type": "boolean",
            "description": "序列末尾自动追加 document.inspect，默认 true",
        },
    },
    "required": ["steps"],
}

STATUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"job_id": {"type": "string"}},
    "required": ["job_id"],
}

CANCEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"job_id": {"type": "string"}},
    "required": ["job_id"],
}

LIST_APPS_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}

# Studio 本地 GUI 交接：**只有 files**。刻意不暴露 exe 路径参数 ——
# 那等于把「任意 exe + 任意参数」包装成一个可直接调用的工具（= 任意代码执行）。
STUDIO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
            "description": (
                "要交给 ArtCraft Studio 打开的本地文件绝对路径，须在已授权 read root 内；"
                "留空则只启动 Studio"
            ),
        },
    },
    "required": ["files"],
}

# ---------------------------------------------------------------------------
# 入口 id（与 plugin.toml 的 [[plugin.entries]] 必须逐字相等）
# ---------------------------------------------------------------------------
ENTRY_SEARCH = "artcraft_ops_search_commands"
ENTRY_RUN = "artcraft_ops_run_sequence"
ENTRY_STATUS = "artcraft_ops_get_job"
ENTRY_CANCEL = "artcraft_ops_cancel_job"
ENTRY_APPS = "artcraft_ops_list_apps"
ENTRY_STUDIO = "artcraft_ops_open_in_studio"

ALL_ENTRY_IDS = (
    ENTRY_SEARCH, ENTRY_RUN, ENTRY_STATUS, ENTRY_CANCEL, ENTRY_APPS, ENTRY_STUDIO,
)

_DEFAULT_APP = "photocraft"
_BACKGROUND: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# 工具出口白名单（§13.2 P2）
# ---------------------------------------------------------------------------
def tool_output(payload: dict[str, Any]) -> dict[str, Any]:
    """``@llm_tool`` 返回前必过：只留白名单字段 + ``redact()``。

    一律不返回：响应体 / steps 全文 / inspect 全文 / argv / stderr_text /
    RawOutput / 连接串 / token / instance.json 内容。
    """
    if not isinstance(payload, dict):
        return {}
    filtered = {
        key: value for key, value in payload.items()
        if key in spec.TOOL_OUTPUT_ALLOWLIST
    }
    return errors.redact(filtered)


def _ctx_of(plugin: Any, extra: dict[str, Any] | None) -> Any:
    ctx = getattr(plugin, "ctx", None)
    if ctx is not None:
        return ctx
    return (extra or {}).get("_ctx")


def _truthy(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _dir_of(plugin: Any, kind: str) -> str:
    """取 data/cache 目录（``self.data_path()`` / ``self.cache_path()``），失败回落 storage。"""
    getter = getattr(plugin, "data_path" if kind == "data" else "cache_path", None)
    path: Any = None
    if callable(getter):
        try:
            path = getter() if kind == "data" else getter()
        except Exception:
            path = None
    if path is None:
        base = getattr(plugin, "storage_dir", None) or os.getcwd()
        path = os.path.join(str(base), kind)
    return str(path)


def _cli_for(app_id: str, spec_obj: Any) -> str:
    """解析 CLI 路径；解析失败回落 ``cli_candidates[0]``（让错误在 job 里可见）。"""
    configured = str(getattr(spec_obj, "cli_path", "") or "")
    if configured:
        return configured
    try:
        return locator.resolve_cli(app_id, {})
    except Exception:
        candidates = tuple(getattr(spec_obj, "cli_candidates", ()) or ())
        return candidates[0] if candidates else ""


def _spawn_background(coro: Any) -> asyncio.Task:
    """在 **entry handler 内** 创建后台 task（宿主超时只 cancel 协程，句柄自己管）。"""
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


def _error_from(exc: BaseException) -> Any:
    if isinstance(exc, errors.ArtcraftError):
        return errors.error_for(exc)
    return errors.safe_error("POLICY_VIOLATION", exc)


@neko_plugin
class ArtcraftOpsPlugin(NekoPluginBase):
    """ArtCraft 全家桶操控：检索命令 → 编排序列 → 无头执行 → 回传图片。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)          # 必须第一行；末尾自动注册 @llm_tool

    # -- lifecycle ---------------------------------------------------------
    @lifecycle(id="startup")
    async def on_startup(self, **_):
        """只做纯文件读 + 内存登记，**绝不** spawn / warm catalog / upload 图片。

        startup 走瞬时 ``asyncio.run``（`host.py:1327`），在此起的后台 task 返回即被取消。
        """
        global _DEFAULT_APP
        try:
            jobs.init_handles()
            jobs.set_persist_path(str(self.data_path("jobs.json")))
        except Exception:
            pass
        try:
            command_catalog.configure(str(self.cache_path()))
        except Exception:
            pass
        try:
            settings = await config_mod.load_settings(self)
            app_registry.configure(settings.apps)
            _DEFAULT_APP = settings.default_app or _DEFAULT_APP
            # 安全配置只在这里注入一次；入口层只读 active_policy()，不接受调用方传 root
            roots_mod.configure_security(settings.security)
            # Studio 的 exe 覆盖路径只在这里注入一次；入口层**不接受**调用方传 exe
            studio_local.configure(settings.raw)
        except Exception:
            pass
        try:
            jobs.restore_jobs(str(self.data_path("jobs.json")))
        except Exception:
            pass
        # 宿主忽略 lifecycle 返回值（host.py:1322-1336 只看有没有抛异常）；
        # 返回 Ok 只是为了让沙盒 run-entry 不报 "returned None"。
        return Ok({"phase": "startup"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """遍历全部句柄 ``terminate()`` —— 宿主不会替我们杀子进程。"""
        try:
            await jobs.shutdown_all()
        except Exception:
            pass
        return Ok({"phase": "shutdown"})

    # -- 1/5 检索命令 ------------------------------------------------------
    @plugin_entry(
        id="artcraft_ops_search_commands",
        name="检索命令",
        timeout=15.0,
        llm_result_fields=["commands", "app", "version", "total_matched", "hint"],
    )
    @llm_tool(
        name="artcraft_ops_search_commands",
        parameters=SEARCH_SCHEMA,
        description=(
            "当用户想对图片/矢量/文档执行某操作但不知道命令名时，"
            "先按关键词检索命令与人类可读参数 schema"
        ),
        timeout=20.0,
    )
    async def artcraft_ops_search_commands(
        self, query: Any = None, app: Any = None, top_k: Any = None, **_,
    ):
        try:
            text = str(query or "").strip()
            if not text:
                return errors.safe_err("POLICY_VIOLATION", reason="query 不能为空")
            app_id = str(app or _DEFAULT_APP).strip() or _DEFAULT_APP
            spec_obj = app_registry.get_spec(app_id)
            limit = _clamp_int(top_k, 8, 1, 30)
            items = await asyncio.wait_for(
                command_catalog.search(text, app_id, top_k=limit),
                timeout=spec.DISCOVERY_ENTRY_BUDGET_S,
            )
            if not items:
                return errors.safe_err("NO_COMMAND_MATCH", query=text)
            commands = [
                {
                    "id": item.id,
                    "label": item.label,
                    "params_schema": item.params_schema,
                    "menu": list(item.menu),
                    "needs_document": item.needs_document,
                    "enabled": item.enabled,
                }
                for item in items
            ]
            return Ok(tool_output({
                "commands": commands,
                "app": app_id,
                "version": str(getattr(spec_obj, "version", "") or ""),
                "total_matched": len(commands),
                "hint": "params 是**人类可读** mini-schema（非 JSON Schema）：{name:min..max=default}",
            }))
        except (asyncio.TimeoutError, TimeoutError):
            return errors.safe_err("COMMANDS_DISCOVERY_FAILED")
        except BaseException as exc:      # noqa: BLE001 - 统一走固定码出口
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return errors.safe_error("COMMANDS_DISCOVERY_FAILED", exc)

    # -- 2/5 执行序列 ------------------------------------------------------
    @plugin_entry(
        id="artcraft_ops_run_sequence",
        name="执行序列",
        timeout=10.0,
        llm_result_fields=["job_id", "status", "poll_with", "cancel_with"],
    )
    @llm_tool(
        name="artcraft_ops_run_sequence",
        parameters=RUN_SCHEMA,
        description=(
            "当用户要在 ArtCraft 软件里执行一串编辑/导出命令时使用；"
            "秒回 job_id，用 get_job 查结果、cancel_job 取消"
        ),
        timeout=30.0,
    )
    async def artcraft_ops_run_sequence(
        self,
        app: Any = None,
        steps: Any = None,
        input_file: Any = None,
        new_doc: Any = None,
        out_file: Any = None,
        inspect: Any = None,
        **_,
    ):
        ctx = _ctx_of(self, _)
        try:
            app_id = str(app or _DEFAULT_APP).strip() or _DEFAULT_APP
            spec_obj = app_registry.get_spec(app_id)
            policy = spec.CommandPolicy()
            step_list = sequence.steps_from_payload(steps, spec_obj)
            if not step_list:
                return errors.safe_err("POLICY_VIOLATION", reason="steps 不能为空")
            if len(step_list) > policy.max_steps:
                return errors.safe_err("LIMIT_STEPS", max_steps=policy.max_steps)

            # 注册表校验：只读**缓存**（entry 内禁止 spawn 子进程）
            cached_ids = sequence.cached_catalog_ids(app_id, spec_obj)
            # [真缺陷] roots 必须在 validate_steps **之前**算出来：steps 的 params
            # 里也有路径字段（`{"cmd":"edit.fill","params":{"path":...}}`），
            # 校验顺序写反等于 step 参数完全不过 roots。
            data_dir = _dir_of(self, "data")
            cache_dir = _dir_of(self, "cache")
            roots_policy = roots_mod.active_policy(data_dir, cache_dir)
            sequence.validate_steps(
                step_list, spec_obj, policy, cached_ids, roots=roots_policy,
            )
            step_list = sequence.plan_inspect(
                step_list, spec_obj, _truthy(inspect, True),
            )

            raw_input = str(input_file or "")
            if raw_input:
                raw_input = roots_mod.resolve_and_check(
                    raw_input, roots_policy.read_roots, "read",
                )
            raw_out = str(out_file or "")
            if raw_out:
                raw_out = roots_mod.resolve_and_check(
                    raw_out, roots_policy.write_roots, "write",
                )

            cli = _cli_for(app_id, spec_obj)
            if not cli:
                return errors.safe_err("APP_NOT_FOUND", app=app_id)
            if cli != str(getattr(spec_obj, "cli_path", "") or ""):
                spec_obj = dataclasses.replace(spec_obj, cli_path=cli)

            new_doc_value = new_doc if isinstance(new_doc, dict) else None
            # [真缺陷] `--new` 是**第二条** LLM 可控 JSON 通道：new_doc 会被
            # json.dumps 成一个 argv 元素（sequence 的 new_flag 分支），同样能塞
            # `{"profile": "C:\\Windows\\win.ini"}` —— 只校验 steps 等于留了一条
            # 不用 steps 的读文件通道。
            # 校验方式是**补键**而不是「任何字符串都按路径判」：后者会把
            # `{"name": "A: study"}` 这种文档名按盘符判据误杀（报错还谎称路径越界）。
            # `profile` 在本通道是文件路径、在 step params 里是预设名，两处语义相反，
            # 所以只在补键里单独认定，通用词表不动。
            sequence.validate_params(
                new_doc_value or {}, roots_policy,
                extra_path_keys=sequence.NEW_DOC_PATH_KEYS,
            )
            source = spec.Source(input_file=raw_input, new_doc=new_doc_value)

            job = jobs.create_job(
                app_id, (), is_edit_sequence=sequence.is_edit_sequence(step_list, spec_obj),
            )
            job.step_total = len(step_list)
            job.result_path = raw_out
            job.cwd = os.path.dirname(cli) or ""

            target_out = raw_out
            if str(getattr(spec_obj, "builder", "")) == "tool_subcmd":
                # [FACT] CA `--project` 必须指向**尚不存在**的路径
                project = os.path.join(data_dir, f"{job.job_id}.cany")
                source = spec.Source(input_file=project, new_doc=None)
                target_out = ""

            # 脚本族（FigCraft/FilmCraft/SolidCraft/LightCraft/PdfCraft）需要先落一个
            # 脚本文件，argv 只引用它的路径。扩展名按格式走——FilmCraft 只认 .jsonl。
            script_path = ""
            if str(getattr(spec_obj, "builder", "")) == "script_file":
                ext = ".jsonl" if str(getattr(spec_obj, "script_format", "")) == "jsonl_id" else ".json"
                script_path = os.path.join(data_dir, f"{job.job_id}.script{ext}")

            argv = sequence.build_argv(
                spec_obj, step_list, source, target_out, roots_policy,
                script_path=script_path,
            )
            job.argv = tuple(argv)

            _spawn_background(
                jobs.execute(job, ctx=ctx, spec=spec_obj, roots=roots_policy),
            )
            return Ok(tool_output({
                "job_id": job.job_id,
                "status": "queued",
                "poll_with": ENTRY_STATUS,
                "cancel_with": ENTRY_CANCEL,
            }))
        except BaseException as exc:               # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return _error_from(exc)

    # -- 3/5 查询状态 ------------------------------------------------------
    @plugin_entry(
        id="artcraft_ops_get_job",
        name="查询任务状态",
        timeout=15.0,
        llm_result_fields=[
            "job_id", "status", "progress", "progress_label",
            "result_handle", "preview_url", "error_code", "error_message",
        ],
    )
    @llm_tool(
        name="artcraft_ops_get_job",
        parameters=STATUS_SCHEMA,
        description="查询长任务状态、进度与结果句柄",
        timeout=15.0,
    )
    async def artcraft_ops_get_job(self, job_id: Any = None, **_):
        try:
            job = jobs.get_job(str(job_id or ""))
            if job is None:
                return errors.safe_err("JOB_NOT_FOUND")
            status = jobs.status_value(job)
            return Ok(tool_output({
                "job_id": job.job_id,
                "status": status,
                "phase": status,
                "progress": round(float(job.progress or 0.0), 3),
                "progress_label": str(job.progress_label or ""),
                "result_handle": str(job.result_handle or job.result_path or ""),
                "preview_url": str(getattr(job, "preview_url", "") or ""),
                "output_files": [str(job.result_path)] if job.result_path else [],
                "warnings": list(job.warnings or ()),
                "exit_code": job.exit_code,
                "ok": status == spec.JobStatus.SUCCEEDED.value,
                "error_code": str(job.error_code or ""),
                "error_message": str(job.error_message or ""),
            }))
        except BaseException as exc:               # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return errors.safe_error("JOB_NOT_FOUND", exc)

    # -- 4/5 取消任务 ------------------------------------------------------
    @plugin_entry(
        id="artcraft_ops_cancel_job",
        name="取消任务",
        timeout=15.0,
        llm_result_fields=["job_id", "status"],
    )
    @llm_tool(
        name="artcraft_ops_cancel_job",
        parameters=CANCEL_SCHEMA,
        description="取消任务：显式转发 ArtCraft 侧取消（预留通道）并终止本地子进程",
        timeout=15.0,
    )
    async def artcraft_ops_cancel_job(self, job_id: Any = None, **_):
        ctx = _ctx_of(self, _)
        try:
            job = await jobs.cancel_job(str(job_id or ""), ctx=ctx)
            return Ok(tool_output({
                "job_id": job.job_id,
                "status": jobs.status_value(job),
            }))
        except errors.ArtcraftError as exc:
            return errors.error_for(exc)
        except BaseException as exc:               # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return errors.safe_error("JOB_NOT_FOUND", exc)

    # -- 5/5 列出本机 App --------------------------------------------------
    @plugin_entry(
        id="artcraft_ops_list_apps",
        name="列出本机 App",
        timeout=10.0,
        llm_result_fields=["apps"],
    )
    @llm_tool(
        name="artcraft_ops_list_apps",
        parameters=LIST_APPS_SCHEMA,
        description="列出本机已探测到的 ArtCraft App 与版本",
        timeout=10.0,
    )
    async def artcraft_ops_list_apps(self, **_):
        try:
            apps: list[dict[str, Any]] = []
            for app_id, spec_obj in app_registry.APP_SPECS.items():
                cli = _cli_for(app_id, spec_obj)
                apps.append({
                    "app_id": app_id,
                    "display_name": str(getattr(spec_obj, "display_name", "") or app_id),
                    "version": str(getattr(spec_obj, "version", "") or ""),
                    "cli_path_found": bool(cli) and os.path.isfile(cli),
                })
            return Ok(tool_output({"apps": apps}))
        except BaseException as exc:               # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return errors.safe_error("APP_NOT_FOUND", exc)

    # -- 6/6 在 Studio 中打开（本地 GUI 交接，不做 UI 自动化）---------------
    @plugin_entry(
        id="artcraft_ops_open_in_studio",
        name="在 Studio 中打开",
        timeout=10.0,
        llm_result_fields=[
            "launched", "studio_available", "file_count",
            "handoff_verified", "exe_path", "note",
        ],
    )
    @llm_tool(
        name="artcraft_ops_open_in_studio",
        parameters=STUDIO_SCHEMA,
        description=(
            "当用户想把本地文件交给 ArtCraft Studio 的图形界面继续编辑时使用；"
            "只负责启动 GUI 并把文件路径传过去，不做 UI 自动化"
        ),
        timeout=15.0,
    )
    async def artcraft_ops_open_in_studio(self, files: Any = None, **_):
        try:
            exe = studio_local.resolve_studio_exe()
            if not exe:
                # 不是错误，是**状态**：未安装不应报「命令行程序未找到」（Studio 无 CLI）
                return Ok(tool_output({
                    "launched": False,
                    "studio_available": False,
                    "file_count": 0,
                    "handoff_verified": False,
                    "exe_path": "",
                    "note": (
                        "未检测到 ArtCraft Studio（artcraft.exe）；"
                        "可在 [artcraft.studio] exe_path 显式指定安装路径"
                    ),
                }))

            data_dir = _dir_of(self, "data")
            cache_dir = _dir_of(self, "cache")
            roots_policy = roots_mod.active_policy(data_dir, cache_dir)
            checked = studio_local.validate_handoff_files(
                files, roots_policy.read_roots,
            )
            return Ok(tool_output(studio_local.launch(checked, exe=exe)))
        except errors.ArtcraftError as exc:
            return errors.error_for(exc)
        except BaseException as exc:               # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return errors.safe_error("POLICY_VIOLATION", exc)


__all__ = [
    "ArtcraftOpsPlugin", "tool_output",
    "SEARCH_SCHEMA", "RUN_SCHEMA", "STATUS_SCHEMA", "CANCEL_SCHEMA",
    "LIST_APPS_SCHEMA", "STUDIO_SCHEMA",
    "ENTRY_SEARCH", "ENTRY_RUN", "ENTRY_STATUS", "ENTRY_CANCEL", "ENTRY_APPS",
    "ENTRY_STUDIO",
    "ALL_ENTRY_IDS",
]
