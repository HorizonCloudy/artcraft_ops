"""artcraft_ops —— 领域层：逐行 JSON 解析 / 警告透传 / 消息面 materialize。

[FACT 链 2] 消息面 512 KiB **整条丢弃**（`context.py:1203-1210`），连文本一起没了
→ 大响应必须**落盘只推句柄**（一等公民，不是事后兜底）。

[FACT 链 1] 拍平警告（``flattened; layers, masks and blend modes are not kept``）
**不是错误**，必须原样透传英文原文，不翻译、不截断。
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from . import errors
from .errors import redact, redact_text
from .i18n import t
from .spec import (
    MAX_REPLY_BYTES,
    ExecResult,
    SpillPolicy,
)

_FLATTEN_DEFAULT = "flattened; layers, masks and blend modes are not kept"
_ERROR_CODES_WITH_STDERR = frozenset({"CLI_USAGE_ERROR", "CLI_TASK_FAILED"})


def _iter_dicts(lines: Any) -> list[dict]:
    """按顺序抽出连续 JSON 对象（兼容单行包络与多行裸 JSON）。"""
    text = "\n".join(str(line) for line in lines or ())
    decoder = json.JSONDecoder()
    found: list[dict] = []
    pos = 0
    while True:
        start = text.find("{", pos)
        if start < 0:
            break
        try:
            parsed, end = decoder.raw_decode(text, start)
        except ValueError:
            pos = start + 1
            continue
        if isinstance(parsed, dict):
            found.append(parsed)
        pos = end
    return found


def extract_inspect(
    steps: tuple[dict, ...] | list[dict], inspect_command: str = "",
) -> dict | None:
    """从步骤里精确抽出最后一条检查命令的 ``result``（绝不臆造状态）。

    默认识别 ``document.inspect`` / ``doc.inspect``；app 显式给了
    ``spec.inspect_command``（EffectCraft ``comp.info``）时一并识别。
    """
    names = {"document.inspect", "doc.inspect"}
    if inspect_command:
        names.add(inspect_command)
    found: dict | None = None
    items = [s for s in steps or () if isinstance(s, dict)]
    for step in items:
        command = str(step.get("command") or step.get("cmd") or "")
        if command in names:
            result = step.get("result")
            if isinstance(result, dict):
                found = result
    # 裸 JSON app（EffectCraft）不输出 command 包络：检查命令固定最后执行，取最后一个对象
    if found is None and inspect_command and items:
        if not any(step.get("command") or step.get("cmd") for step in items):
            found = items[-1]
    return found


def collect_warnings(stderr_text: str, spec: Any = None) -> tuple[str, ...]:
    """只收「拍平」警告（原样英文），其它 stderr 噪声不误报为警告。"""
    marker = str(getattr(spec, "flatten_warning_substr", "") or _FLATTEN_DEFAULT)
    warnings: list[str] = []
    for line in str(stderr_text or "").splitlines():
        text = line.strip()
        if not text:
            continue
        if marker and marker in text:
            warnings.append(text)
    return tuple(warnings)


def _stderr_summary(stderr_text: str, limit: int = 200) -> str:
    return redact_text(" ".join(str(stderr_text or "").split()))[:limit]


def _error_message(code: str, stderr_text: str, **extra: Any) -> str:
    kwargs: dict[str, Any] = {"stderr": _stderr_summary(stderr_text)}
    kwargs.update(extra)
    return errors.build_error_message(code, **kwargs)


def parse_exec_output(raw: Any, spec: Any = None, job: Any = None) -> ExecResult:
    """把 ``RawOutput`` 解析成 ``ExecResult``（逐行 JSON；坏行跳过；超限截断）。"""
    rc = int(getattr(raw, "returncode", 0) or 0)
    stderr_text = str(getattr(raw, "stderr_text", "") or "")
    app_id = str(getattr(job, "app_id", "") or getattr(spec, "app_id", "") or "")
    job_id = str(getattr(job, "job_id", "") or "")
    result_path = str(getattr(job, "result_path", "") or "")

    warnings: list[str] = list(collect_warnings(stderr_text, spec))

    if rc != 0:
        code, _hint = errors.map_cli_error(rc, stderr_text, spec)
        if not code:
            code = "CLI_TASK_FAILED"
        message = _error_message(code, stderr_text)
        # exit 2 = 用法错误 → 整批不保存任何产物（steps/output_files 一律空）
        failed = ExecResult(
            ok=False,
            app_id=app_id,
            job_id=job_id,
            exit_code=rc,
            steps=(),
            inspect=None,
            warnings=tuple(warnings),
            output_files=(),
            error_code=code,
            error_message=message,
        )
        failed.user_message = compose_user_message(failed, "zh")
        return failed

    lines: list[str] = []
    total = 0
    limit_hit = False
    for line in getattr(raw, "stdout_lines", ()) or ():
        size = len(str(line).encode("utf-8"))
        if total + size > MAX_REPLY_BYTES:
            limit_hit = True
            break
        total += size
        lines.append(str(line))
    steps = _iter_dicts(lines)

    if getattr(raw, "truncated", False) or limit_hit:
        warnings.append(f"LIMIT_REPLY: {t('LIMIT_REPLY')}")

    inspect = extract_inspect(
        tuple(steps), str(getattr(spec, "inspect_command", "") or ""),
    )
    output_files = (result_path,) if result_path else ()
    result = ExecResult(
        ok=True,
        app_id=app_id,
        job_id=job_id,
        exit_code=rc,
        steps=tuple(steps),
        inspect=inspect,
        warnings=tuple(warnings),
        output_files=output_files,
    )
    result.user_message = compose_user_message(result, "zh")
    return result


def command_step_count(steps: Any) -> int:
    """真实下发的命令步数（区别于协议转录条数）。

    VectorCraft 会额外输出 ``step=open`` / ``step=export`` 两条不含 ``command``
    的记录行；Craft Anything 则完全不带 ``command`` 键。故：优先数带 ``command``
    的条目，一条都没有时回退为总条数（CA 形状）。

    ``ExecResult.steps`` 保持完整转录（``export`` 行的 bytes/format/path 对用户有价值），
    本函数只统一**计数口径**。容忍 ``None`` / 非 dict 元素（跳过不抛）。
    """
    items = steps or ()
    count = sum(1 for s in items if isinstance(s, dict) and s.get("command"))
    # ``count or len(items)``：CA 纯 ``{"result": …}`` → 回退 1；全垃圾元素 → 回退 len
    return count or len(items)


def compose_user_message(r: ExecResult, lang: str = "zh") -> str:
    """组装给用户看的摘要。**警告原文一字不改地附在最后**。"""
    lines: list[str] = []
    if getattr(r, "ok", False):
        if lang == "en":
            lines.append(f"Done: {r.app_id}; {command_step_count(r.steps)} step(s) executed")
        else:
            lines.append(f"任务完成：{r.app_id}；共 {command_step_count(r.steps)} 步")
        if r.output_files:
            lines.append(("outputs: " if lang == "en" else "产物：")
                         + ", ".join(str(p) for p in r.output_files))
        inspect = getattr(r, "inspect", None)
        if isinstance(inspect, dict):
            width = inspect.get("width")
            height = inspect.get("height")
            layers = inspect.get("layers") or ()
            active = inspect.get("activeLayer")
            if lang == "en":
                line = f"canvas {width}x{height}, {len(layers)} layer(s)"
                if active is not None:
                    line += f", active layer {active}"
            else:
                line = f"画布 {width}x{height}，{len(layers)} 个图层"
                if active is not None:
                    line += f"，当前图层 {active}"
            lines.append(line)
    else:
        message = str(getattr(r, "error_message", "") or "")
        lines.append(message or ("job failed" if lang == "en" else "任务失败"))

    for warning in getattr(r, "warnings", ()) or ():
        lines.append(str(warning))
    return "\n".join(line for line in lines if line)


def _png_preview_bytes(path: str, max_bytes: int) -> bytes | None:
    try:
        if not os.path.isfile(path):
            return None
        size = os.path.getsize(path)
        if size <= 0 or size > max_bytes:
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _spill(r: ExecResult, policy: SpillPolicy) -> str:
    """把完整结果落盘，返回 handle 路径（写失败返回空串，不影响主流程）。"""
    base = str(policy.spill_dir or "") or os.path.join(
        tempfile.gettempdir(), "artcraft_ops_results",
    )
    name = f"{r.job_id or 'job'}-result.json"
    path = os.path.join(base, name)
    try:
        os.makedirs(base, exist_ok=True)
        payload = {
            "ok": r.ok, "app_id": r.app_id, "job_id": r.job_id,
            "exit_code": r.exit_code, "steps": list(r.steps),
            "inspect": r.inspect, "warnings": list(r.warnings),
            "output_files": list(r.output_files),
            "error_code": r.error_code, "error_message": r.error_message,
        }
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(redact(payload), fh, ensure_ascii=False, default=str)
        os.replace(tmp, path)
        return path
    except OSError:
        return ""


async def materialize(r: ExecResult, policy: SpillPolicy, ctx: Any = None) -> dict[str, Any]:
    """裁剪成 ≤ ``policy.max_bytes`` 的消息面回包（超限 → 落盘只推句柄）。

    预览 PNG 走 ``ctx.images.upload()`` 得 URL part，**禁止 inline base64**。
    """
    payload: dict[str, Any] = {
        "ok": r.ok,
        "app_id": r.app_id,
        "job_id": r.job_id,
        "exit_code": r.exit_code,
        "step_count": command_step_count(r.steps),
        "warnings": list(r.warnings or ()),
        "output_files": list(r.output_files or ()),
        "error_code": r.error_code,
        "error_message": r.error_message,
        "user_message": r.user_message,
        "result_handle": r.result_handle,
        "preview_url": r.preview_url,
    }
    if r.inspect is not None:
        payload["inspect"] = r.inspect

    # 预览图：结果阶段才 upload，绝不在 lifecycle 调
    if (
        getattr(policy, "preview_via_upload", True)
        and not r.preview_url
        and ctx is not None
        and getattr(ctx, "images", None) is not None
    ):
        for path in r.output_files or ():
            if not str(path).lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            data = _png_preview_bytes(str(path), 5 * 1024 * 1024)
            if data is None:
                continue
            try:
                uploaded = await ctx.images.upload(data, mime="image/png")
            except Exception:  # 上传失败不影响文本回执
                break
            if isinstance(uploaded, dict):
                r.preview_url = str(uploaded.get("url") or "")
                payload["preview_url"] = r.preview_url
            break

    blob = json.dumps(redact(payload), ensure_ascii=False, default=str)
    size = len(blob.encode("utf-8"))
    r.payload_bytes = size
    if size <= int(policy.max_bytes):
        return payload

    handle = r.result_handle or _spill(r, policy)
    r.result_handle = handle
    r.spilled = True
    return redact({
        "ok": r.ok,
        "app_id": r.app_id,
        "job_id": r.job_id,
        "status": "spilled",
        "result_handle": handle,
        "preview_url": r.preview_url,
        "error_code": r.error_code,
        "summary": compose_user_message(
            ExecResult(
                ok=r.ok, app_id=r.app_id, exit_code=r.exit_code,
                # steps 必须带全：计数走 command_step_count(r.steps)，缺了就会写「共 0 步」
                steps=r.steps,
                warnings=r.warnings, output_files=r.output_files,
                error_message=r.error_message,
            ),
            "zh",
        )[:2000],
        "hint": t("LIMIT_REPLY"),
    })


__all__ = [
    "parse_exec_output", "extract_inspect", "collect_warnings",
    "compose_user_message", "command_step_count", "materialize",
]
