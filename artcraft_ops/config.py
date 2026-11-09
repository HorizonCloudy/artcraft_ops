"""artcraft_ops —— 叶子层：运行时配置加载（``await self.config.dump()``）。

[FACT 链 2] 差异表 / roots / limits / i18n **不放进程 env**：
宿主代理热切换会改 ``HTTP_PROXY`` / ``NO_PROXY`` / ``ALL_PROXY``
（`config_router/proxy.py:82-101`），只有安装定位用的 ``LOCALAPPDATA`` / ``ProgramFiles``
才允许直读 ``os.environ``（见 `locator.py`）。

状态根配置：``<state root>\\plugins\\artcraft_ops\\config\\plugin.toml``
（**不含** ``[plugin]`` 段；宿主首次安装时把 ``config.example.toml`` 逐字节复制过来）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .spec import CONTROL_CALL_TIMEOUT_S, DEFAULT_MAX_STEPS

_DEFAULT_SECURITY: dict[str, Any] = {
    "user_read_roots": [],
    "user_write_roots": [],
    "deny_prefixes": ["prefs.", "plugin.", "updates."],
    "require_registry_match": True,
    "require_confirm_on_write": False,
    "token_file_acl_check": True,
    "loopback_host": "127.0.0.1",
    "allow_user_dirs": False,
}

_DEFAULT_LIMITS: dict[str, Any] = {
    "max_steps": DEFAULT_MAX_STEPS,
    "max_request_line_bytes": 1_048_576,
    "max_reply_bytes": 8_388_608,
    "message_plane_max_bytes": 512 * 1024,
    "control_call_timeout_s": CONTROL_CALL_TIMEOUT_S,
    "push_min_interval_s": 2.0,
    "job_ttl_s": 3600,
}

_DEFAULT_I18N: dict[str, Any] = {
    "default_lang": "zh",
    "supported": ["zh", "en"],
    "passthrough_warnings": ["flattened; layers, masks and blend modes are not kept"],
}


@dataclass
class Settings:
    """运行时配置快照（全部有默认值 → 零配置也能跑）。"""

    apps: dict[str, Any] = field(default_factory=dict)
    default_app: str = "photocraft"
    security: dict[str, Any] = field(default_factory=lambda: dict(_DEFAULT_SECURITY))
    limits: dict[str, Any] = field(default_factory=lambda: dict(_DEFAULT_LIMITS))
    i18n: dict[str, Any] = field(default_factory=lambda: dict(_DEFAULT_I18N))
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def max_steps(self) -> int:
        try:
            return int(self.limits.get("max_steps", DEFAULT_MAX_STEPS))
        except (TypeError, ValueError):
            return DEFAULT_MAX_STEPS

    @property
    def lang(self) -> str:
        value = str(self.i18n.get("default_lang", "zh") or "zh")
        return value if value in ("zh", "en") else "zh"


def _artcraft_section(raw: dict[str, Any]) -> dict[str, Any]:
    node = raw.get("artcraft") if isinstance(raw, dict) else None
    return node if isinstance(node, dict) else {}


async def load_runtime_config(plugin: Any) -> dict[str, Any]:
    """``await self.config.dump()`` 读状态根配置；任何异常 → ``{}``（绝不阻塞加载）。

    [FACT] ``config.dump(*, timeout=5.0)`` 是 **async**（`shared/core/config.py:177`），
    漏 ``await`` 会拿到协程对象。
    """
    cfg = getattr(plugin, "config", None) or getattr(
        getattr(plugin, "ctx", None), "config", None,
    )
    if cfg is None or not hasattr(cfg, "dump"):
        return {}
    try:
        raw = cfg.dump()
        if hasattr(raw, "__await__"):
            raw = await raw
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def settings_from_dict(raw: dict[str, Any] | None) -> Settings:
    node = _artcraft_section(raw or {})
    apps = node.get("apps") if isinstance(node.get("apps"), dict) else {}
    security = dict(_DEFAULT_SECURITY)
    if isinstance(node.get("security"), dict):
        security.update(node["security"])
    limits = dict(_DEFAULT_LIMITS)
    if isinstance(node.get("limits"), dict):
        limits.update(node["limits"])
    i18n_cfg = dict(_DEFAULT_I18N)
    if isinstance(node.get("i18n"), dict):
        i18n_cfg.update(node["i18n"])
    default_app = str(
        apps.get("default") or security.get("default_app") or "photocraft"
    )
    overrides = apps.get("specs") if isinstance(apps.get("specs"), dict) else {}
    merged_apps: dict[str, Any] = {}
    for key, value in apps.items():
        if key in ("default", "specs"):
            continue
        if isinstance(value, dict):
            merged_apps[str(key)] = value
    merged_apps.update(overrides)
    return Settings(
        apps=merged_apps, default_app=default_app, security=security,
        limits=limits, i18n=i18n_cfg, raw=raw or {},
    )


async def load_settings(plugin: Any) -> Settings:
    """对外唯一入口：读配置 → ``Settings``（失败回落全默认值）。"""
    try:
        raw = await load_runtime_config(plugin)
    except Exception:
        raw = {}
    return settings_from_dict(raw)


def get_limits(settings: Settings | None) -> dict[str, Any]:
    return dict(_DEFAULT_LIMITS if settings is None else settings.limits)


__all__ = [
    "Settings", "load_runtime_config", "load_settings",
    "settings_from_dict", "get_limits",
]
