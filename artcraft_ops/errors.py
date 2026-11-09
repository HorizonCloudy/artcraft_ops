"""artcraft_ops —— 叶子层：错误码 / CLI 错误映射 / 脱敏 / 唯一错误 envelope 出口。

三条铁律（§13.0 / §13.2 / §14.1，全部 [FACT 宿主侧]）：
  1. **业务失败一律返回错误 envelope dict（S16）**。宿主入口有四个压平点，
     ``Err``/异常四条路都会被压成固定码：
       1. `plugin/core/entry_executor.py:169-173` —— 入口返回 ``Err`` 时只取
          ``str(result.error)``，``SdkError.code``/``details`` 直接丢；
       2. 同文件 `:220-229` —— ``except Exception`` 把任何插件异常压成
          ``{"code": "PLUGIN_EXECUTION_FAILED"}``；
       3. `plugin/core/communication.py:334-335` —— 父进程侧再包一层，
          丢掉 ``RpcRemoteError.code``（`rpc.py:150-158` 里明明有）；
       4. `plugin/runs/trigger_service.py:410-420` —— ``except PluginError``
          直接 ``fail(ErrorCode.INTERNAL, str(exc))``，**这才是 ``INTERNAL`` 的产生点**。
          注意 :410 分支排在 :433-443 的 ``ValueError`` 等类型特判**之前**，
          而插件异常经 RPC 后已是 ``PluginExecutionError``，所以类型特判对插件永不生效。
     **唯一出口**：入口返回宿主 `plugin/core/responses.py::is_envelope()` 认的裸 dict
     （只要求 ``success``/``error``/``time`` 三个键），宿主归一化时原样保留，
     ``error.code`` 会被落成 ``RunError.code``（`runs/manager.py:695-711`，``str`` 无白名单）。
     因此关键码**同时**写进 ``error.code`` 与 ``error.message`` 前缀，两条口径都保住。
  2. **异常对象绝不插值进对外 message**（白名单结构为主防线）；
     ``redact()`` 只是第二层兜底，且不依赖 64hex。
  3. 进 logger 的值先 ``redact_text()``，只用预格式化参数，禁 f-string / ``logger.exception``。

本模块**不依赖**宿主 SDK：``err()`` 只产出纯 dict，未装 SDK 时依然可 import、
可 ``compileall`` / ``ast`` 扫描。SDK 的 ``Err``/``SdkError`` 只有成功路径（``Ok``）
与入口装饰器需要，留在 ``__init__.py``。
"""
from __future__ import annotations

import json
import logging
import re
from time import gmtime, strftime
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
# 唯一对外错误出口（§13.2 P1 / S16）
# ---------------------------------------------------------------------------
#: ``details`` 的键数上限（宿主 ``fail()`` 的 details 是自由 dict，这里自我约束，
#: 防有人把整段 payload / CLI stdout 塞进对外回包）。
MAX_DETAIL_KEYS = 8

_SCALAR_TYPES = (str, int, float, bool)


def _now_iso() -> str:
    """UTC ISO8601 时间戳（宿主 envelope 的 ``time`` 字段同形，秒级 + ``+00:00``）。

    刻意不用 ``datetime.now(timezone.utc)`` / ``datetime.utcnow()``：
    前者要 ``datetime.UTC``（Python 3.11+），后者已废弃（ruff DTZ003）；
    在 Windows 上 ``strftime`` 不支持 ``%f``（C 层库无此扩展），故不输出微秒。
    """
    return strftime("%Y-%m-%dT%H:%M:%S+00:00", gmtime())


def _details_from(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """把模板插值字段收敛成 ``details``：**只保留标量、已脱敏、<=8 键**。

    - ``None`` 值直接丢弃：它不携带任何信息，塞进 details 只会造出无意义键；
    - 非标量（dict / list / 异常对象）一律丢弃 —— 详情只许进日志；
    - 无标量字段时返回 ``None``（不是 ``{}``），与宿主 ``fail(details=None)`` 同形；
    - **键名先过 ``_is_secret_key``**（``token`` / ``api_key`` / ``secret`` /
      ``authorization`` / ``cookie`` / ``control_file`` …）：命中则整值 ``***``。
      这一点必须在这里做：``redact(value)`` 只看得见裸值，键名规则不会触发
      （``redact({key: value})`` 才会，但 details 是一个个键拼的）；
    - 其余值再走 ``redact()``，字符串按值模式兜底
      （长十六进制 / ``method:auth`` / ``--control-token-file`` / ``bearer|basic``）。
    """
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        if not isinstance(value, _SCALAR_TYPES):
            continue
        key_text = str(key)
        out[key_text] = _MASK if _is_secret_key(key_text) else redact(value)
        if len(out) >= MAX_DETAIL_KEYS:
            break
    return out or None


def err(code: str, **kwargs: Any) -> dict[str, Any]:
    """构造宿主可直接消费的**错误 envelope**（裸 dict，不再是 SDK 的 ``Err``）。

    形状（与宿主 ``plugin/core/responses.py::is_envelope`` 对齐）::

        {"success": False, "code": 500, "data": None, "message": "",
         "error": {"code": "<CODE>", "message": "<CODE>: <脱敏文案>",
                   "details": {...} | None, "retriable": False},
         "time": "<ISO8601>",
         "is_error": True,                       # @llm_tool 通道靠它透传
         "output": {"error_code": "<CODE>", "error_message": "<脱敏文案>"}}

    ``code`` 为**固定枚举码**（未知码也不崩，仍带前缀）；``kwargs`` 只用于模板插值，
    **不得**传异常对象。

    **硬不变量（禁止绕过）**：envelope 的所有键必须是**字面量**；``kwargs``
    **只允许**进 ``details``（经 ``_details_from`` 的白名单/脱敏/限键收敛）。
    禁止 ``err(code, **payload)`` 这类整体透传 —— 那会让任意键值绕过白名单
    直接出现在对外回包里；也禁止在别处 "组装" envelope 后再拼 ``kwargs``。
    """
    text = redact_text(t(code, **kwargs))
    return {
        "success": False,            # 宿主 runs/manager.py:695 据此判 failed
        "code": 500,                 # int，与宿主 fail() 同形；自定义码默认 500
        "data": None,
        "message": "",               # 与宿主 fail() 同形（人读文案在 error.message）
        "error": {
            "code": code,            # 固定枚举码：原样进 RunError.code
            "message": f"{code}: {text}",
            "details": _details_from(kwargs),
            "retriable": False,
        },
        "time": _now_iso(),
        "is_error": True,            # llm_tools.py:241-263 见到它才走错误透传
        "output": {"error_code": code, "error_message": text},
    }


def safe_error(code: str, exc: BaseException | None = None, **kwargs: Any) -> dict[str, Any]:
    """唯一对外错误出口。``exc`` 详情只进日志（先脱敏），绝不进对外 message。"""
    if exc is not None:
        logger.error("artcraft_ops %s failed: %s", code, redact_text(str(exc)))
    return err(code, **kwargs)


def safe_err(code: str, **kwargs: Any) -> dict[str, Any]:
    """``safe_error`` 的薄别名（保住既有测试断言，§13.0 裁决 1）。"""
    return safe_error(code, None, **kwargs)


def error_for(exc: BaseException) -> dict[str, Any]:
    """把内部异常统一转成对外 envelope：只保留固定码，丢弃异常详情。"""
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
