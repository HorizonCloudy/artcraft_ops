"""artcraft_ops —— 基础设施层：CLI 定位 / 版本 token / 长名路径 / instance.json。

§16.1 裁决 E 的定位顺序（命中即止）：
  1. 运行时配置显式覆盖（``[artcraft.apps.<id>].cli_path``，最高优先；填了就必须可用）
  2. 上游默认路径族：``%LOCALAPPDATA%\\Programs\\ArtCraft\\<App>\\``、
     ``C:\\Program Files\\ArtCraft\\<App>\\``、``C:\\Program Files (x86)\\ArtCraft\\<App>\\``
  3. 本机实测安装位置兜底（`E:\\Program Files\\Artcraft\\<App>\\`，注意 ``Artcraft`` 小写 c）
  4. 注册表 Uninstall ``InstallLocation``：**仅可选兜底**（实机除 Studio 外全为空）
  5. ``--version`` 探针；失败回退 exe sha256 前 12 位
  6. 全未命中 → ``APP_NOT_FOUND``

[FACT] 三个 CLI **都不在 PATH**；目录名大小写不统一（``Programs\\ArtCraft`` vs
``Program Files\\Artcraft``），不能硬编码任一个。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import traceback
from typing import Any

from .errors import AppNotFound, redact_text
from .spec import CONTROL_CALL_TIMEOUT_S

CLI_LOCALAPPDATA_TPL = r"%LOCALAPPDATA%\Programs\ArtCraft\<AppDir>\<app>-cli.exe"

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")
TOKEN_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# 三处降级路径（GetLongPathNameW / --version 探针 / sha256 回退）刻意**不抛异常**
# （long_path 仍返原路径、探针失败仍回退 sha256、sha256 失败仍返回 "unknown"），
# 但不能再被裸 except 全吞。按 errors.py:8 铁律 3：进 logger 的值先经
# redact_text()，只用预格式化参数，禁 f-string / logger.exception / exc_info。
# 这里把类型名 + redact_text(traceback.format_exc()) 作为两个 %s 参数：既保留
# 完整栈帧（「线程池调度失败 / open 失败 / read 失败」可区分 —— 此前
# detect_version 偶发回退 "unknown" 却查不到根因的缺口），又确保异常 message
# 里的凭证不会原样进日志。read_instance_json 的 token 只入内存，这三处 except
# 也不接触凭证文件。
logger = logging.getLogger(__name__)


def _registry_spec(app_id: str) -> Any:
    """从 ``app_registry`` 取 spec（局部导入，避免顶层循环依赖）。"""
    from . import app_registry  # 局部导入：locator 是叶子层，不能被注册表反向依赖

    return app_registry.BUILTIN_SPECS.get(app_id)


def _iter_candidates(app_id: str) -> list[str]:
    """候选 CLI 路径，**唯一真相源是 app_registry**。

    以前这里自己维护 ``_CLI_NAMES`` / ``_LOCAL_OBSERVED`` 两张表；注册表从 3 个
    app 扩到 17 个时忘了同步 → 14 个 app 全部 ``APP_NOT_FOUND``（2026-11-08 实机踩到）。
    现在只读注册表，物理上不可能再脱节；环境变量那几条「没实测过的可能安装位」
    仍然保留，作为 registry 路径失配时的兜底探测。
    """
    spec = _registry_spec(app_id)
    out: list[str] = []
    if spec is not None:
        # 本机实装位置（注册表里的 cli_candidates）优先
        out.extend(str(p) for p in (spec.cli_candidates or ()))
        app_dir = os.path.basename(str(getattr(spec, "install_root", "") or "")) or app_id
        exe_name = os.path.basename(str((spec.cli_candidates or ("",))[0])) or f"{app_id}-cli.exe"
    else:
        app_dir = app_id
        exe_name = f"{app_id}-cli.exe"

    for env_key in ("LOCALAPPDATA",):
        base = os.environ.get(env_key)
        if base:
            out.append(os.path.join(base, "Programs", "ArtCraft", app_dir, exe_name))
    for pf in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(pf)
        if base:
            out.append(os.path.join(base, "ArtCraft", app_dir, exe_name))
            out.append(os.path.join(base, "Artcraft", app_dir, exe_name))
    return out


def _explicit_from_cfg(app_id: str, cfg: Any) -> str:
    """从运行时配置里取显式覆盖路径：支持 ``{app_id: path}`` 与 ``{"cli_path": path}``。"""
    if not isinstance(cfg, dict):
        return ""
    direct = cfg.get(app_id)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    nested = cfg.get("cli_path")
    if isinstance(nested, str) and nested.strip():
        return nested.strip()
    apps = cfg.get("apps")
    if isinstance(apps, dict):
        node = apps.get(app_id)
        if isinstance(node, dict):
            value = node.get("cli_path")
            if isinstance(value, str) and value.strip():
                return value.strip()
        elif isinstance(node, str) and node.strip():
            return node.strip()
    return ""


def long_path(p: str) -> str:
    """把路径展开成长名 + 正斜杠绝对路径（**只用于我们自己生成的路径**）。

    [FACT] 禁止拼 ``sys.executable`` / ``os.getcwd()``：插件 cwd 是 ``sys._MEIPASS``，
    且后端日志里出现过 ``E:\\PROGRA~2\\...`` 短名，会被 roots 直接拒。
    """
    text = str(p or "").strip().strip('"')
    text = os.path.expandvars(text)
    if text[:4].lower() in ("\\\\?\\", "//?/"):
        text = text[4:]
        if text[:4].lower() in ("unc\\", "unc/"):
            text = "\\\\" + text[4:]
    text = text.replace("\\", "/")
    if os.name == "nt":
        try:  # pragma: no cover - Windows API
            import ctypes

            get_long = ctypes.windll.kernel32.GetLongPathNameW  # type: ignore[attr-defined]
            buf = ctypes.create_unicode_buffer(32768)
            if get_long(text, buf, 32768):
                text = buf.value.replace("\\", "/")
        except Exception as exc:
            logger.debug(
                "long_path: GetLongPathNameW failed; keeping normalized path (%s): %s",
                type(exc).__name__,
                redact_text(traceback.format_exc()),
            )
    if len(text) > 3:
        text = text.rstrip("/")
    return text


def resolve_cli(app_id: str, cfg: Any = None) -> str:
    """返回可用 CLI 的绝对路径；全未命中 → ``APP_NOT_FOUND``。

    显式配置优先且**不做静默回退**：填了错路径就直接报错，避免"以为用了自定义版本
    其实跑的是兜底 exe"这类不可观测行为。
    """
    explicit = _explicit_from_cfg(app_id, cfg)
    if explicit:
        normalized = long_path(explicit)
        if os.path.isfile(normalized):
            # 对外返回**原生分隔符**绝对路径（roots 内部比对仍用 long_path 的正斜杠版）
            return os.path.normpath(normalized)
        raise AppNotFound("APP_NOT_FOUND", app=app_id)

    # 不再用硬编码名单把门：只要注册表里有 cli_candidates（或环境探测能命中）就算候选。
    # 名单式门禁是上一版 14 个 app 全 NOT_FOUND 的直接原因。
    if not _iter_candidates(app_id):
        raise AppNotFound("APP_NOT_FOUND", app=app_id)

    for candidate in _iter_candidates(app_id):
        normalized = long_path(candidate)
        if os.path.isfile(normalized):
            return os.path.normpath(normalized)
    raise AppNotFound("APP_NOT_FOUND", app=app_id)


def known_cli_paths() -> dict[str, str]:
    """返回本机实测路径表（只作诊断/自检，不保证存在）。"""
    out: dict[str, str] = {}
    for app_id in _registry_all_ids():
        cands = _iter_candidates(app_id)
        if cands:
            out[app_id] = cands[0]
    return out


def _registry_all_ids() -> list[str]:
    from . import app_registry

    return sorted(app_registry.BUILTIN_SPECS)


def _sha256_12(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


async def detect_version(cli_path: str, spec: Any = None) -> str:
    """优先 ``--version``；失败回退 exe sha256 前 12 位（**绝不抛异常**）。

    [INFERENCE] ``--version`` 未被实机验证 → 必须有回退，且回退要稳定可复现
    （缓存 key 依赖它）。
    """
    import asyncio  # 局部导入：本模块其余函数保持同步、可被 CLI/工具直接调用

    from . import runner  # 局部导入：避免 runner ↔ locator 顶层循环

    if cli_path and os.path.isfile(cli_path):
        try:
            kwargs = runner.build_subprocess_kwargs()
            proc = await asyncio.create_subprocess_exec(cli_path, "--version", **kwargs)
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=CONTROL_CALL_TIMEOUT_S,
            )
            text = (stdout or b"").decode("utf-8", "replace") + (
                stderr or b""
            ).decode("utf-8", "replace")
            match = _VERSION_RE.search(text)
            if match:
                return match.group(1)
        except Exception as exc:
            # 探针失败静默降级，绝不阻塞发现流程；但留下 DEBUG + 脱敏栈，别全吞。
            logger.debug(
                "detect_version: --version probe failed; falling back to sha256 (%s): %s",
                type(exc).__name__,
                redact_text(traceback.format_exc()),
            )

    if cli_path and os.path.isfile(cli_path):
        try:
            return await runner.blocking(_sha256_12, cli_path)
        except Exception as exc:
            logger.debug(
                "detect_version: sha256 fallback failed; returning 'unknown' (%s): %s",
                type(exc).__name__,
                redact_text(traceback.format_exc()),
            )
            return "unknown"
    return "unknown"


def read_instance_json(path: str) -> dict[str, Any]:
    """读 ``{pid, port, token:<hex>}``。内容**只入内存**，绝不进日志/回包。

    坏 JSON / 结构不符 → 直接抛（不静默放过一个畸形凭证文件，§10 #22 错误输入）。
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("instance.json must be a JSON object")
    port = data.get("port")
    if not isinstance(port, int) or port <= 0:
        raise ValueError("instance.json: invalid 'port'")
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("instance.json: missing 'token'")
    return data


def build_cli_env() -> dict[str, str]:
    """完整继承 ``os.environ``（PATH / LOCALAPPDATA / ProgramFiles 都必需），返回独立副本。

    日志副本必须经 ``errors.redact()`` —— 这里返回的是**原始**环境，
    因为 CLI 需要真实代理与路径变量。
    """
    return dict(os.environ)


__all__ = [
    "CLI_LOCALAPPDATA_TPL", "TOKEN_RE",
    "resolve_cli", "known_cli_paths", "long_path",
    "detect_version", "read_instance_json", "build_cli_env",
]
