"""artcraft_ops —— 基础设施层【v1 预留，不实现】：ArtCraft Studio 云端生成通道。

[FACT] 三条形态硬约束：
  1. ArtCraft Studio 本地 GUI 是 **L4** 应用，v1 不做 UIAutomation；
  2. 云端生成走 ``POST /v1/mcp/session/create`` + 第三方 ``artcraft-mcp`` 的 API key
     （``%LOCALAPPDATA%\\ai.artcraft.app\\.cookies``），与本机 CLI 控制面**不抽象成同一层**
     （issue #2003：Scene state can't be read back）；
  3. 不能做成 PhotoCraft 内嵌 WASM 插件（ABI v1 沙箱禁 fs/网络导入、不支持面板/UI）
     → 只能外部子进程 + 回环控制通道。

本模块只留协议接口；任何真实调用都 ``STUDIO_NOT_IMPLEMENTED``。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .errors import ArtcraftError

STUDIO_API_BASE = "https://api.artcraft.app"
STUDIO_SESSION_PATH = "/v1/mcp/session/create"
STUDIO_COOKIES_PATH = r"%LOCALAPPDATA%\ai.artcraft.app\.cookies"


class StudioNotImplemented(ArtcraftError):
    code = "STUDIO_NOT_IMPLEMENTED"


@runtime_checkable
class StudioCloudChannel(Protocol):
    """云端生成通道协议（v1 只留接口）。"""

    async def create_session(self) -> dict: ...

    async def close(self) -> None: ...


class StudioCloudChannelStub:
    """占位实现：任何调用都 ``STUDIO_NOT_IMPLEMENTED``（绝不静默成功）。"""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key          # 只存内存，绝不进日志/回包
        self.session: dict[str, Any] = {}

    async def create_session(self) -> dict:
        raise StudioNotImplemented("STUDIO_NOT_IMPLEMENTED")

    async def close(self) -> None:
        self.session.clear()
        return None


__all__ = [
    "STUDIO_API_BASE", "STUDIO_SESSION_PATH", "STUDIO_COOKIES_PATH",
    "StudioCloudChannel", "StudioCloudChannelStub", "StudioNotImplemented",
]
