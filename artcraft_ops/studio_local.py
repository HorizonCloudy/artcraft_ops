"""artcraft_ops —— 领域层：ArtCraft Studio **本地 GUI 交接通道**（只启动 + 交文件）。

与 ``studio_channel.py`` 是两件事，**不要合并**：
  * ``studio_channel.py``：云端生成通道（``POST /v1/mcp/session/create`` + 第三方 API key），
    v1 只留协议接口，任何调用都 ``STUDIO_NOT_IMPLEMENTED``；
  * **本模块**：本地 GUI 的「拉起 + 把文件路径交过去」，**不做任何 UI 自动化**。

## 为什么不做 UI 自动化

``RESULTS.md`` §5 / §7 第 6 条的结论：ArtCraft Studio 是 **L4** 应用，只能降级成
「GUI 启动 + 文件交接」。UI 自动化（找窗口句柄、点菜单、截图 OCR）在这里既脆弱又
不可维护，且上游明确不支持状态回读（issue #2003 *Scene state can't be read back*）。
所以本模块的依赖面**刻意为零**：只有 ``subprocess`` + ``os``，没有 uiautomation /
pywinauto / pyautogui / win32gui —— ``tests/test_studio_local.py`` 有源码级守卫。

## 交付语义（务必如实）

``handoff_verified`` 恒为 ``False``：**Popen 成功 ≠ 文件已在窗口里打开**。
Electron 单实例锁通常会把第二个进程的 argv 转发给已运行实例再自杀，但这条
**未在本机核验过**（本机只有 exe 本体，无法观测窗口状态），所以绝不谎报「已打开」。

## 安全边界

1. **绝不接受调用方传入 exe 路径**。exe 只来自「运行时配置覆盖」或「本模块探测」，
   否则等于把任意代码执行包装成一个 LLM 工具；
2. 待交接文件必须逐个过 ``roots.resolve_and_check(..., "read")``，且必须是**已存在的常规文件**；
3. ``Popen`` 固定 ``shell=False`` + 三个句柄 ``DEVNULL``，argv 是 list，永不拼字符串。

## 本机实机依据

[FACT] 2026-11-07 实测（``RESULTS.md`` §1）：``C:\\Program Files\\ArtCraft\\artcraft.exe``
共 77,455,360 B；同目录**只有** ``artcraft.exe`` + ``Uninstall ArtCraft.lnk`` ——
**没有 CLI 二进制、没有 DLL**，因此 Studio 无法走 ``app_registry`` 的 ``Transport.CLI_RUN``
通道，只能走本模块。
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Sequence

from .errors import AppNotFound, ArtcraftError, PolicyViolation
from . import roots as roots_mod

# Studio 不是 APP_SPECS 的成员：它没有 CLI，塞进去会让 list_apps 的 cli_path_found 失真
STUDIO_APP_ID = "studio"
STUDIO_DISPLAY_NAME = "ArtCraft Studio"
STUDIO_EXE_NAME = "artcraft.exe"

#: 单次最多交接几个文件（防止一句话把 200 张图甩给 GUI）
STUDIO_MAX_FILES = 8

#: 安装根候选：``(环境变量名, 其下的子目录)``。
#: 只读 ``ProgramFiles`` / ``ProgramW6432`` / ``LOCALAPPDATA`` —— 这三个属于
#: 「安装定位」，是 ``config.py`` 文档里**唯一**允许直读 ``os.environ`` 的用途
#: （宿主代理热切换会改 ``HTTP_PROXY`` 一族，业务配置一律走 ``config.dump()``）。
_STUDIO_ROOTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ProgramFiles", ("ArtCraft",)),
    ("ProgramW6432", ("ArtCraft",)),
    ("ProgramFiles(x86)", ("ArtCraft",)),
    ("LOCALAPPDATA", ("Programs/ArtCraft", "ArtCraft")),
)

# 运行时配置覆盖（``[artcraft.studio] exe_path``），由 startup 注入
_CONFIGURED_EXE = ""


def configure(cfg: dict[str, Any] | None) -> str:
    """从 ``[artcraft.studio]`` 读 exe 覆盖路径；返回生效值（空串 = 走探测）。

    只在 ``lifecycle(startup)`` 调一次；不做运行时动态改路径（与 roots 同策略）。
    """
    global _CONFIGURED_EXE
    node = cfg if isinstance(cfg, dict) else {}
    artcraft = node.get("artcraft") if isinstance(node.get("artcraft"), dict) else {}
    studio = artcraft.get("studio") if isinstance(artcraft.get("studio"), dict) else {}
    value = str(studio.get("exe_path") or "").strip()
    _CONFIGURED_EXE = value
    return _CONFIGURED_EXE


def configured_exe() -> str:
    return _CONFIGURED_EXE


def studio_candidates() -> tuple[str, ...]:
    """返回按优先级排好的 exe 候选（**不**判断存在性）。"""
    out: list[str] = []
    if _CONFIGURED_EXE:
        out.append(_CONFIGURED_EXE)
    for env_name, subdirs in _STUDIO_ROOTS:
        base = str(os.environ.get(env_name) or "").strip()
        if not base:
            continue
        for sub in subdirs:
            out.append(os.path.join(base, sub.replace("/", os.sep), STUDIO_EXE_NAME))
    seen: set[str] = set()
    unique: list[str] = []
    for item in out:
        key = os.path.normcase(os.path.abspath(item))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


def resolve_studio_exe() -> str:
    """返回第一个真实存在的 exe 绝对路径；都没有则 ``""``（**不抛异常**）。"""
    for item in studio_candidates():
        try:
            if os.path.isfile(item):
                return os.path.abspath(item)
        except OSError:  # pragma: no cover - 极少数无权限路径
            continue
    return ""


def is_available() -> bool:
    return bool(resolve_studio_exe())


def _as_list(files: Any) -> list[str]:
    if files is None:
        return []
    if isinstance(files, (list, tuple)):
        return [str(item) for item in files if str(item or "").strip()]
    text = str(files).strip()
    return [text] if text else []


def validate_handoff_files(
    files: Any, read_roots: Sequence[str], *, limit: int = STUDIO_MAX_FILES,
) -> tuple[str, ...]:
    """校验并归一化待交接文件：``root`` 内 + 已存在 + 常规文件 + 不超上限。

    失败语义（fail-closed，全部复用既有 19 个错误码，不新增码）：
      * 超上限 → ``POLICY_VIOLATION``
      * 空列表 → 允许（= 只启动 GUI，不交文件）
      * 不在 root 内 / 含 ``..`` / 设备名 / ADS / 8.3 → 由 ``resolve_and_check`` 抛
      * 存在但不是常规文件（目录、管道）→ ``POLICY_VIOLATION``
    """
    items = _as_list(files)
    if len(items) > max(1, int(limit)):
        raise PolicyViolation(
            "POLICY_VIOLATION", "",
            reason=f"too many files (max {max(1, int(limit))})",
        )
    checked: list[str] = []
    for raw in items:
        normalized = roots_mod.resolve_and_check(raw, tuple(read_roots), "read")
        if not os.path.exists(normalized):
            raise PolicyViolation(
                "POLICY_VIOLATION", "", reason="handoff file does not exist",
            )
        if not os.path.isfile(normalized):
            raise PolicyViolation(
                "POLICY_VIOLATION", "", reason="handoff target is not a regular file",
            )
        checked.append(normalized)
    return tuple(checked)


def _popen_kwargs() -> dict[str, Any]:
    """``Popen`` 固定 kwargs —— **没有** shell；GUI 进程与宿主解耦。

    Windows 用 ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP``：Studio 是用户要看的
    窗口，宿主退出/重启**不该**把它带走（与 runner 里跑无头 CLI 的策略相反）。
    """
    flags = 0
    if os.name == "nt":  # pragma: no cover - 平台分支
        flags = (
            int(getattr(subprocess, "DETACHED_PROCESS", 0) or 0)
            | int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) or 0)
        )
    return {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
        "close_fds": True,
        "creationflags": flags,
    }


def launch(files: Sequence[str] = (), *, exe: str = "") -> dict[str, Any]:
    """拉起 Studio 并把 ``files`` 作为 argv 交过去；**不等待、不读回**。

    ``exe`` 只允许由本模块 ``resolve_studio_exe()`` 产出（或 startup 注入的配置值），
    入口层**不得**把它暴露成参数。

    返回字段全部在 ``spec.TOOL_OUTPUT_ALLOWLIST`` 内，且 ``handoff_verified`` 恒 False。
    """
    target = str(exe or "").strip() or configured_exe() or resolve_studio_exe()
    if not target or not os.path.isfile(target):
        raise AppNotFound("APP_NOT_FOUND", "", app=STUDIO_DISPLAY_NAME)

    file_list = [str(item) for item in files if str(item or "").strip()]
    argv = [target, *file_list]

    try:
        proc = subprocess.Popen(argv, cwd=os.path.dirname(target) or None, **_popen_kwargs())
    except OSError as exc:
        raise ArtcraftError("APP_NOT_FOUND", "") from exc

    return {
        "launched": True,
        "studio_available": True,
        "exe_path": target,
        "file_count": len(file_list),
        "pid": int(getattr(proc, "pid", 0) or 0),
        # 诚实字段：Popen 成功 != 文件已在窗口打开
        "handoff_verified": False,
        "note": "已把文件路径作为启动参数交给 Studio；未做 UI 自动化，无法确认窗口内已加载",
    }


__all__ = [
    "STUDIO_APP_ID", "STUDIO_DISPLAY_NAME", "STUDIO_EXE_NAME", "STUDIO_MAX_FILES",
    "configure", "configured_exe", "studio_candidates", "resolve_studio_exe",
    "is_available", "validate_handoff_files", "launch",
]
