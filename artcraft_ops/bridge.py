"""artcraft_ops —— 基础设施层【v1 预留，不实现】：回环控制通道。

为什么 v1 关闭（三条 [FACT]）：
  1. **#9777 仍未修**（priority: critical, 仍 Open）：首帧 auth ``{"method":"auth",
     "params":{"token":"<64hex>"}}`` 明文传输 → token 必进日志/回包面；
  2. Windows 上 ``localhost`` 解析到 ``[::1]``，而 app 只绑 ``127.0.0.1``
     → 必须用**字面** ``127.0.0.1``（RESULTS §9.2）；
  3. 宿主代理可热切换（``HTTP_PROXY`` 等）→ HTTP 客户端必须 ``trust_env=False``。

本模块只落地常量与校验函数，接口面留给后续版本；调用即 ``BRIDGE_NOT_IMPLEMENTED``。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .errors import ArtcraftError, redact_text
from .locator import TOKEN_RE, read_instance_json

LOOPBACK_HOST: str = "127.0.0.1"        # 字面量，绝不用 localhost（#9777）
LOOPBACK_SCHEME = "http"
_CONTROL_CALL_TIMEOUT_S = 60.0


class BridgeNotImplemented(ArtcraftError):
    code = "BRIDGE_NOT_IMPLEMENTED"


def _mask_url(url: str) -> str:
    return redact_text(str(url or ""))


def assert_no_localhost(url: str) -> None:
    """只允许 ``http://127.0.0.1:<port>/...``；任何 localhost/非回环 → 抛错。

    用 ``AssertionError`` 语义但抛 ``ValueError`` 亦可（测试两者都接受）；
    这里抛 ``ValueError``，避免 ``python -O`` 下 assert 被优化掉。
    """
    text = str(url or "")
    lowered = text.lower()
    if "localhost" in lowered:
        raise ValueError(
            f"localhost is forbidden (resolves to [::1] on Windows); use {LOOPBACK_HOST}: "
            f"{_mask_url(text)}"
        )
    if not lowered.startswith(f"{LOOPBACK_SCHEME}://{LOOPBACK_HOST}"):
        raise ValueError(
            f"only the literal loopback host {LOOPBACK_HOST} is allowed: {_mask_url(text)}"
        )


class _NoProxyClient:
    """httpx 不可用时的最小替身：**必须**暴露 ``trust_env = False``。"""

    trust_env = False

    def __init__(self, timeout: float = _CONTROL_CALL_TIMEOUT_S) -> None:
        self.timeout = float(timeout)

    async def aclose(self) -> None:  # pragma: no cover - v1 不发起真实请求
        return None


def build_http_client_no_proxy(timeout: float = _CONTROL_CALL_TIMEOUT_S) -> Any:
    """返回一个 ``trust_env is False`` 的客户端（绕过系统代理，绝不读 env 代理）。

    优先 httpx；未安装时回落最小替身（**不新增第三方依赖**）。
    """
    try:  # pragma: no cover - 取决于环境是否装 httpx
        import httpx

        return httpx.AsyncClient(trust_env=False, timeout=timeout)
    except Exception:
        return _NoProxyClient(timeout=timeout)


def read_token_user_only(instance_json: str) -> str:
    """读 ``instance.json`` 的 token 并**强制** 64 hex 校验。

    token 只入内存 → 返回给调用方后立刻作为首帧 auth 使用，**绝不进日志/回包**。
    """
    data = read_instance_json(instance_json)
    token = str(data.get("token") or "")
    if not TOKEN_RE.match(token):
        raise ValueError("instance.json: invalid 'token' (expected 64 hex characters)")
    return token


@runtime_checkable
class ControlChannel(Protocol):
    """回环控制通道协议（v1 只留接口，无实现）。"""

    async def connect(self, port: int, token: str) -> None: ...

    async def call(self, method: str, params: dict, timeout: float = 60.0) -> dict: ...

    async def cancel(self, remote_job_id: str) -> None: ...   # 显式 jobs.cancel

    async def close(self) -> None: ...


class LoopbackControlChannel:
    """占位实现：任何调用都 ``BRIDGE_NOT_IMPLEMENTED``（绝不静默成功）。"""

    def __init__(self, instance_json: str = "") -> None:
        self.instance_json = instance_json

    async def connect(self, port: int = 0, token: str = "") -> None:
        raise BridgeNotImplemented("BRIDGE_NOT_IMPLEMENTED")

    async def call(self, method: str = "", params: dict | None = None,
                   timeout: float = 60.0) -> dict:
        raise BridgeNotImplemented("BRIDGE_NOT_IMPLEMENTED")

    async def cancel(self, remote_job_id: str = "") -> None:
        raise BridgeNotImplemented("BRIDGE_NOT_IMPLEMENTED")

    async def close(self) -> None:
        return None


__all__ = [
    "LOOPBACK_HOST", "assert_no_localhost", "build_http_client_no_proxy",
    "read_token_user_only", "ControlChannel", "LoopbackControlChannel",
    "BridgeNotImplemented",
]
