"""artcraft_ops —— 叶子层：错误码 / CLI 错误映射 / 脱敏 / 唯一 Err 出口。

三条铁律（§13.0 / §13.2 / §14.1，全部 [FACT 宿主侧]）：
  1. **业务代码禁止直接 ``SdkError(...)``**。宿主把 ``Err.code`` 抹平为 ``INTERNAL``
     （`entry_executor.py:169-173,220-229`），关键码只能写进 message 前缀。
  2. **异常对象绝不插值进对外 message**（白名单结构为主防线）；
     ``redact()`` 只是第二层兜底，且不依赖 64hex。
  3. 进 logger 的值先 ``redact_text()``，只用预格式化参数，禁 f-string / ``logger.exception``。

红阶段/绿阶段均可 import：``plugin.sdk.plugin`` 在 ``err()`` 内部惰性导入，
未装 SDK 时本模块依然可被 ``compileall`` / ``ast`` 扫描。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import i18n  # noqa: F401  (保留模块引用，便于运行时切换语言表)
from .i18n import ERROR_CODES, t  # noqa: F401  (ERROR_CODES 对外暴露)

logger = logging.getLogger("artcraft_ops")

# ---------------------------------------------------------------------------
# 码表（§8，19 条）
# ---------------------------------------------------------------------------
ERROR_CODE_ORDER: tuple[str, ...] = tuple(ERROR_CODES.keys())

# ---------------------------------------------------------------------------
# 脱敏（§14.1 D1：白名单结构为主，下列规则仅兜底第二层）
# ---------------------------------------------------------------------------
REDACT_KEY_RE = r"(?i)(token|authorization|cookie|\.token|control[_-]?file|secret|api[_-]?key)"
REDACT_VALUE_RES: tuple[str, ...] = (
    r"\b[0-9a-fA-F]{32,}\b",                              # 32+ 位十六进制（不止 64）
    r'(?i)["\']?method["\']?\s*:\s*["\']auth["\']',        # 整段 auth 首帧
    r"(?i)--control-token-file\s+\S+",                    # token 文件路径
    r"(?i)(bearer\s+|basic\s+)[A-Za-z0-9+/=._-]{16,}",    # base64 / URL 编码凭证
)
# 子集常量：仅用于兼容既有测试口径，**不是**主防线（§14.0 裁决 1）
REDACT_TOKEN_RE = r"\b[0-9a-fA-F]{64}\b"
# §1 旧口径字段名，保留对外兼容
REDACT_FIELDS: tuple[str, ...] = (
    "token", "authorization", "control_token", "cookie", "api_key",
    "instance_json", "control-token", "auth.params.token", "secret",
)

_REDACT_KEY_PATTERN = re.compile(REDACT_KEY_RE)
_REDACT_VALUE_PATTERNS = tuple(re.compile(p) for p in REDACT_VALUE_RES)
_MASK = "***"
_MAX_STRING_SCAN = 256 * 1024   # 超长字符串只扫前 256 KiB（避免 redact 本身 OOM）


def redact_text(s: str) -> str:
    """对单段文本做兜底脱敏：逐个模式**局部替换**命中的子串为 ``***``。

    注意是局部替换（``pattern.sub``），不是整段替换 —— 未命中的文本原样保留；
    这也是「脱敏后仍可读」的前提（整段抹掉会让错误信息失去诊断价值）。
    """
    if not s:
        return s
    text = s if isinstance(s, str) else str(s)
    if len(text) > _MAX_STRING_SCAN:
        text = text[:_MAX_STRING_SCAN]
    for pattern in _REDACT_VALUE_PATTERNS:
        text = pattern.sub(_MASK, text)
    return text


def _is_secret_key(key: str) -> bool:
    return bool(_REDACT_KEY_PATTERN.search(str(key)))


def redact(obj: Any) -> Any:
    """递归脱敏任意 dict / list / tuple / str。

    覆盖 §13.1 的四个出口：错误消息、日志、``@llm_tool`` output、``push_message`` 文本。
    键名命中凭证词 → 值替 ``***``（不做任何保留）；
    字符串值再走 ``REDACT_VALUE_RES`` 兜底。
    """
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and _is_secret_key(key):
                out[key] = _MASK
            else:
                out[key] = redact(value)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact(v) for v in obj)
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


# ---------------------------------------------------------------------------
# 异常类型（携带固定码，message 前缀 = <CODE>: ）
# ---------------------------------------------------------------------------
class ArtcraftError(Exception):
    """本插件所有业务异常的基类：``message`` 恒为 ``<CODE>: <脱敏文案>``。"""

    code: str = "POLICY_VIOLATION"

    def __init__(self, code: str = "", message: str = "", **fields: Any) -> None:
        self.code = code or type(self).code
        self.fields = fields
        text = message or t(self.code, **fields)
        super().__init__(f"{self.code}: {redact_text(text)}")


class PolicyViolation(ArtcraftError):
    """命令白名单 / params / 路径策略违规。"""

    code = "POLICY_VIOLATION"


class AppNotFound(ArtcraftError):
    """app 未注册或 CLI 定位失败。"""

    code = "APP_NOT_FOUND"


class ToolReplyError(Exception):
    """宿主 @llm_tool 回包是错误（HTTP 200 + is_error=true，§13.3）。"""


# ---------------------------------------------------------------------------
# 唯一对外错误出口（§13.2 P1）
# ---------------------------------------------------------------------------
def _sdk_err():
    """惰性取 SDK 的 ``Err`` / ``SdkError``（未装 SDK 时不阻塞模块 import）。"""
    from plugin.sdk.plugin import Err, SdkError  # noqa: PLC0415 - 惰性导入是刻意设计

    return Err, SdkError


def err(code: str, **kwargs: Any) -> Any:
    """构造 ``Err(SdkError("<CODE>: <脱敏文案>"))``。

    ``code`` 为**固定枚举码**（未知码也不崩，仍带前缀）；``kwargs`` 只用于模板插值，
    **不得**传异常对象。
    """
    Err, SdkError = _sdk_err()
    message = f"{code}: {redact_text(t(code, **kwargs))}"
    return Err(SdkError(message))


def safe_error(code: str, exc: BaseException | None = None, **kwargs: Any) -> Any:
    """唯一对外错误出口。``exc`` 详情只进日志（先脱敏），绝不进对外 message。"""
    if exc is not None:
        logger.error("artcraft_ops %s failed: %s", code, redact_text(str(exc)))
    return err(code, **kwargs)


def safe_err(code: str, **kwargs: Any) -> Any:
    """``safe_error`` 的薄别名（保住既有测试断言，§13.0 裁决 1）。"""
    return safe_error(code, None, **kwargs)


def error_for(exc: BaseException) -> Any:
    """把内部异常统一转成对外 ``Err``：只保留固定码，丢弃异常详情。"""
    if isinstance(exc, ArtcraftError):
        return err(exc.code, **_reason_fields(exc))
    if isinstance(exc, ToolReplyError):
        return safe_error("CLI_TASK_FAILED", exc)
    return safe_error("POLICY_VIOLATION", exc)


def _reason_fields(exc: ArtcraftError) -> dict[str, Any]:
    fields = dict(getattr(exc, "fields", {}) or {})
    if "reason" not in fields and exc.code == "POLICY_VIOLATION":
        fields["reason"] = exc.fields.get("reason", "") or "unspecified"
    return fields


# ---------------------------------------------------------------------------
# CLI → 宿主错误映射（§8 末段）
# ---------------------------------------------------------------------------
_ROOTS_MARKERS = (
    "automation filesystem access is not granted",
    "read authority is absent",
    "write authority is absent",
)
_NAMESPACE_MARKERS = ("did you mean", "unknown command")


def _summarize(stderr: str, limit: int = 200) -> str:
    """把多行 stderr 拍平成一行摘要（先脱敏，再截断）。"""
    text = redact_text(" ".join(str(stderr or "").split()))
    return text[:limit]


def map_cli_error(
    returncode: int, stderr: str, spec: Any = None,
) -> tuple[str, str]:
    """把 CLI 退出码 + stderr 映射成 ``(code, hint)``。

    优先级（顺序本身是契约）：
      1. roots 未授权 → ``ROOTS_NOT_GRANTED``
      2. 命名空间陷阱（``did you mean`` / ``unknown command``）→ ``NAMESPACE_ERROR``
      3. ``returncode == 0`` → ``("", "")``（拍平警告不算错误）
      4. ``2`` → ``CLI_USAGE_ERROR``；``1`` → ``CLI_TASK_FAILED``；``<0`` → ``JOB_CRASHED``
    """
    text = str(stderr or "")
    lowered = text.lower()

    if any(marker in lowered for marker in _ROOTS_MARKERS):
        return "ROOTS_NOT_GRANTED", t("ROOTS_NOT_GRANTED")
    if any(marker in lowered for marker in _NAMESPACE_MARKERS):
        return "NAMESPACE_ERROR", t("NAMESPACE_ERROR")

    try:
        rc = int(returncode)
    except (TypeError, ValueError):
        rc = -1

    if rc == 0:
        return "", ""
    if rc == 2:
        return "CLI_USAGE_ERROR", t("CLI_USAGE_ERROR", stderr=_summarize(text))
    if rc == 1:
        return "CLI_TASK_FAILED", t("CLI_TASK_FAILED", stderr=_summarize(text))
    return "JOB_CRASHED", t("JOB_CRASHED")


def build_error_message(code: str, **kwargs: Any) -> str:
    """拼「``<CODE>: <脱敏文案>``」，供 ExecResult.error_message 使用。"""
    return f"{code}: {redact_text(t(code, **kwargs))}"


# ---------------------------------------------------------------------------
# 宿主 @llm_tool 回包解包（§14.2 D2）
# ---------------------------------------------------------------------------
def unwrap_tool_reply(resp: Any) -> dict:
    """解包宿主工具回包：**HTTP 200 也可能是错误**（`llm_tools.py:214-223`）。

    先看 ``is_error`` / ``output.error``，再看正常 output；禁止用 status_code==200 判成功。
    抛出的 ``ToolReplyError`` message 会经 ``ToolResult.output_as_json_string()``
    以 ``role=tool`` 进模型上下文（`tool_calling.py:204-212,248-256,494-525`），
    所以这里**必须**先 ``redact_text()``。
    """
    body = resp if isinstance(resp, dict) else {}
    out = body.get("output")
    if out is None:
        out = {}
    if body.get("is_error") is True or (
        isinstance(out, dict) and out.get("error")
    ):
        detail = out.get("error") if isinstance(out, dict) else out
        raise ToolReplyError(redact_text(str(detail or "is_error")))
    if isinstance(out, dict):
        return out
    try:
        parsed = json.loads(str(out))
    except (TypeError, ValueError):
        return {"value": out}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


__all__ = [
    "ERROR_CODES", "ERROR_CODE_ORDER", "t",
    "REDACT_KEY_RE", "REDACT_VALUE_RES", "REDACT_TOKEN_RE", "REDACT_FIELDS",
    "redact", "redact_text",
    "ArtcraftError", "PolicyViolation", "AppNotFound", "ToolReplyError",
    "err", "safe_error", "safe_err", "error_for",
    "map_cli_error", "build_error_message", "unwrap_tool_reply",
]
