"""artcraft_ops —— 叶子层：错误码 → zh/en 文案（§8 错误码表 19 条）。

约定：
  * **固定枚举码 + 脱敏摘要**。`t()` 只做模板插值，绝不插值异常对象（§13.0 核心原则）。
  * 拍平警告（flatten warning）**不翻译、不截断**，原样透传英文原文（§8 末段）。
  * 未知码不崩：返回通用兜底文案，保证 ``err()`` 仍能产出 ``<CODE>: <text>``。
"""
from __future__ import annotations

from typing import Any

DEFAULT_LANG = "zh"
SUPPORTED_LANGS = ("zh", "en")

# code -> {"zh": 文案, "en": 文案, "redact_required": bool}
# redact_required=True 的码：文案里可能出现用户路径 / 外部文本，必须过 redact_text。
MESSAGES: dict[str, dict[str, Any]] = {
    "APP_NOT_FOUND": {
        "zh": "未找到 {app} 的命令行程序，请确认已安装",
        "en": "the {app} command line tool was not found; please check the installation",
        "redact_required": False,
    },
    "APP_VERSION_FAILED": {
        "zh": "无法读取 {app} 版本，命令缓存已禁用",
        "en": "could not read the {app} version; the command cache is disabled",
        "redact_required": False,
    },
    "COMMANDS_DISCOVERY_FAILED": {
        "zh": "命令清单读取失败，请重试",
        "en": "reading the command catalog failed; please retry",
        "redact_required": False,
    },
    "NO_COMMAND_MATCH": {
        "zh": "没有找到与「{query}」匹配的命令，换个说法试试",
        "en": "no command matched \"{query}\"; try different wording",
        "redact_required": True,
    },
    "POLICY_VIOLATION": {
        "zh": "该操作被安全策略拒绝：{reason}",
        "en": "the operation was rejected by the safety policy: {reason}",
        "redact_required": True,
    },
    "PATH_OUTSIDE_ROOTS": {
        "zh": "文件不在已授权目录内：{path}",
        "en": "the file is outside the granted roots: {path}",
        "redact_required": True,
    },
    "PATH_UNSAFE": {
        "zh": "路径不安全，已拒绝",
        "en": "the path is unsafe and was rejected",
        "redact_required": False,
    },
    "NAMESPACE_ERROR": {
        "zh": "命令名已自动从 doc.* 归一化为 document.*",
        "en": "the command name was normalized from doc.* to document.*",
        "redact_required": False,
    },
    "LIMIT_STEPS": {
        "zh": "命令序列超过 {max_steps} 步上限，请拆分",
        "en": "the command sequence exceeds the {max_steps} step limit; please split it",
        "redact_required": False,
    },
    "LIMIT_REPLY": {
        "zh": "输出过大已截断（完整结果见句柄）",
        "en": "the output was truncated (see the result handle for the full result)",
        "redact_required": False,
    },
    "JOB_NOT_FOUND": {
        "zh": "任务不存在或已过期",
        "en": "the job does not exist or has expired",
        "redact_required": False,
    },
    "JOB_TIMEOUT": {
        "zh": "任务超时（{seconds}s），已终止子进程",
        "en": "the job timed out after {seconds}s; the child process was terminated",
        "redact_required": False,
    },
    "JOB_CANCELLED": {
        "zh": "任务已取消（已转发 ArtCraft 侧取消）",
        "en": "the job was cancelled (the ArtCraft side cancellation was forwarded)",
        "redact_required": False,
    },
    "JOB_CRASHED": {
        "zh": "子进程异常退出，编辑类任务不会自动重发，请用检视命令核对文档状态",
        "en": (
            "the child process exited abnormally; edit jobs are never re-sent "
            "automatically, please verify the document state with an inspect command"
        ),
        "redact_required": False,
    },
    "CLI_USAGE_ERROR": {
        "zh": "命令用法错误：{stderr}；整批未保存任何产物",
        "en": "command line usage error: {stderr}; nothing in the batch was saved",
        "redact_required": True,
    },
    "CLI_TASK_FAILED": {
        "zh": "命令执行失败：{stderr}",
        "en": "the command failed: {stderr}",
        "redact_required": True,
    },
    "ROOTS_NOT_GRANTED": {
        "zh": "文件系统访问未授权：缺少 read/write 权限（headless 必须显式传 root 参数）",
        "en": (
            "filesystem access was not granted: read/write authority is absent "
            "(headless runs must pass the root flags explicitly)"
        ),
        "redact_required": False,
    },
    "BRIDGE_NOT_IMPLEMENTED": {
        "zh": "当前版本未启用回环控制通道",
        "en": "the loopback control channel is not enabled in this version",
        "redact_required": False,
    },
    "STUDIO_NOT_IMPLEMENTED": {
        "zh": "当前版本不控制 ArtCraft Studio 本地 GUI；云端生成通道未实现",
        "en": (
            "this version does not drive the ArtCraft Studio local GUI; "
            "the cloud generation channel is not implemented"
        ),
        "redact_required": False,
    },
}

# ERROR_CODES 是 MESSAGES 的对外别名（§8：`ERROR_CODES[code] = {zh, en, redact_required}`）
ERROR_CODES: dict[str, dict[str, Any]] = MESSAGES

_FALLBACK = {
    "zh": "操作失败（未分类错误）",
    "en": "the operation failed (unclassified error)",
}


class _SafeDict(dict):
    """模板缺键时不抛 KeyError，原样保留 ``{name}``。"""

    def __missing__(self, key: str) -> str:  # pragma: no cover - 防御式
        return "{" + key + "}"


def t(code: str, lang: str = DEFAULT_LANG, **kwargs: Any) -> str:
    """取 ``code`` 在 ``lang`` 下的用户文案并做模板插值。

    未知码 / 未知语言不抛异常：未知码走兜底文案，未知语言回落 ``zh``。
    插值值一律先 ``str()``，**不**在这里做脱敏（脱敏由 ``errors.safe_error`` 统一做）。
    """
    entry = MESSAGES.get(code)
    if not entry:
        template = _FALLBACK.get(lang) or _FALLBACK[DEFAULT_LANG]
        return template
    template = entry.get(lang) or entry.get(DEFAULT_LANG) or ""
    try:
        return str(template).format_map(_SafeDict({k: v for k, v in kwargs.items()}))
    except (ValueError, IndexError):  # pragma: no cover - 模板自身坏掉时兜底
        return str(template)


__all__ = ["DEFAULT_LANG", "SUPPORTED_LANGS", "MESSAGES", "ERROR_CODES", "t"]
