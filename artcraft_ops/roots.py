"""artcraft_ops —— 领域层：roots 纯文本比对 + Windows 路径白名单。

[FACT 链 1] 官方原文（`photocraft.im/guides/mcp-cli#file-roots`）：
「the part of the request below the root is **never resolved on disk**」
→ roots 是**逐段纯文本比对**，请求路径片段根本不落盘解析。因此：
  * `..` / `.` 之外的相对化简一律不做（出现 `..` 直接拒）；
  * 设备名（CON/NUL/AUX/COM1…LPT9）、ADS（`file.png:hidden:$DATA`）、
    8.3 短名（`PROGRA~1`）一律拒 —— 即便它们可能指向合法目录；
  * 越界 symlink 拒（本机唯一需要落盘解析的情形，只比对 realpath 是否仍在内）；
  * v0.18.0 起官方**接受** `\\\\?\\C:\\…` / `\\\\?\\UNC\\…` → 我们展开成普通拼写后比对。

本模块只做路径策略，不启动任何进程、不读写目标文件内容。
"""
from __future__ import annotations

import os
import re
from typing import Any, Literal

from .errors import PolicyViolation
from .spec import DEFAULT_READ_FLAG, DEFAULT_WRITE_FLAG, RootsPolicy, RunMode

_DEVICE_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _raise(code: str, **fields: Any) -> None:
    raise PolicyViolation(code, "", **fields)


def _expand_long_prefix(raw: str) -> str:
    """展开 `\\\\?\\C:\\…` / `\\\\?\\UNC\\server\\share` 为普通拼写（v0.18.0 起官方接受）。"""
    text = raw
    if text[:4].lower() in ("\\\\?\\", "//?/"):
        text = text[4:]
        if text[:4].lower() in ("unc\\", "unc/"):
            text = "\\\\" + text[4:]
    if text[:4].lower() in ("\\??\\", "/??/"):
        text = text[4:]
    return text


def normalize_win_path(raw: str, base: str = "") -> str:
    """把任意拼写归一到「正斜杠 + 绝对路径」形态（大小写保留，比对时统一 lower）。

    步骤：去引号 → 展开 `\\\\?\\` 前缀 → 反斜杠转正斜杠 → 相对路径拼 base →
    折叠重复分隔符 → 去掉尾部 `/`。**不做** `..` 化简（安全判定另有专门检查）。
    """
    text = str(raw or "").strip().strip('"').strip("'")
    text = os.path.expandvars(text)
    text = _expand_long_prefix(text)
    text = text.replace("\\", "/")
    # 注意：**不**在这里做 8.3 → 长名展开。安全判定必须先拒绝 8.3 拼写；
    # 长名展开只允许作用于我们自己生成的路径（locator.long_path）。
    text = text.replace("\\", "/")

    if not text:
        _raise("PATH_UNSAFE")

    # [真缺陷，2026-11-08] 这里原本只认 `//` 开头：单分隔符的 `/Windows/win.ini`
    # 与 `\Windows\win.ini` 被当**相对路径**拼进 base，于是 `resolve_and_check`
    # 拿「root/Windows/win.ini」去比对、判定合法 —— 假闸门（调用方的绝对判据再严
    # 也会被这一步洗白）。Windows 上单分隔符开头是**当前盘根**，按绝对处理。
    # 上面已把 `\` 统一替换成 `/`，所以一个 startswith("/") 覆盖两种拼写。
    is_abs = bool(_DRIVE_RE.match(text)) or text.startswith("/")
    if not is_abs:
        base_norm = str(base or "").strip().strip('"').replace("\\", "/")
        if not base_norm:
            # 无 base 且非绝对 → 语义不明，fail-closed
            _raise("PATH_UNSAFE")
        text = f"{base_norm.rstrip('/')}/{text.lstrip('/')}"
    elif _DRIVE_RE.match(text):
        # 统一盘符为大写，便于阅读（比对时仍 lower）
        text = text[0].upper() + text[1:]

    # 折叠重复分隔符（但保留 UNC 的前导 //）
    prefix = "//" if text.startswith("//") else ""
    body = text[len(prefix):]
    body = re.sub(r"/{2,}", "/", body)
    text = prefix + body
    if len(text) > 3:
        text = text.rstrip("/")
    return text


def _assert_safe_segments(text: str) -> None:
    """逐段纯文本检查：`..` / 设备名 / ADS / 8.3 短名 一律拒。

    [S10] 判定前先对**每一段**做 ``str.strip()``（无参数，按 ``isspace()`` 剥）。
    原因：``normalize_win_path`` 只对**整串**做 strip，于是同一个逻辑段出现在
    **串尾**时尾随空白被顺手剥掉、出现在**中间**时没人剥 —— 精确等于型判据
    （``seg == ".."`` / 设备名 ``stem``）在串尾命中、在串中落空。
    包含型判据（``":" in seg`` ADS / ``"~" in seg`` 8.3）不受影响：它们不看边界。

    [缺口规模：别拿抽样数当族规模] 最初抽样 12 种写法时有 5 种不一致
    （``.. `` / ``..\\t`` / ``..\\r`` / ``..\\xa0`` / ``con ``）。但按
    ``str.isspace()`` 整族计，``..`` 与 ``con`` 各 9 个空白字符是 **18/18 全不一致**
    —— 精确等于型判据在这个族上是**系统性失效**，不是零星漏 5 种。
    修后 18/18 全为 ``PATH_UNSAFE``。**"5/12" 是抽样结果，不是缺陷规模。**

    [性质：判据一致性，**不是漏洞**] ``".. "`` 在 Win32 上**不会**被解析成 ``..``
    —— Python ``open`` / .NET ``[IO.File]`` / ``cmd dir`` 三套实测均未穿越
    （对照组：普通名 ``inside.txt `` 能读到 ``inside.txt``，说明 OS 确实剥尾随
    空格，但 ``..`` 必须在解析阶段精确匹配）。拒掉属 fail-closed 的**偏严兜底**，
    与 ``tests/test_params_roots.py`` 的 #24 / #32 同口径，不是 Windows 语义推导。

    [已知代价：这是**真的**误杀，别写成"不误杀"] 整段 strip 会把「**前导**空白 +
    设备名」判成设备名：真实磁盘上 ``root/ con/f.txt``（目录名是 ``" con"``）可以
    创建、可以读，修前 ALLOW、修后 ``PATH_UNSAFE``。取舍是刻意的 —— 与 #24 / #32
    一致：这类拼写拒掉不损失真实可用路径（设备名本身就是危险面），成本可接受。
    段**内**空格不受影响（``a b/c.png`` 仍 ALLOW），因为 ``strip()`` 只动两端。

    [只剥空白，不剥点] 不额外把尾随点当分隔符处理：``...`` / ``.. .`` / ``foo.``
    既无契约依据、实测也不可穿越，钉住等于把实现细节冻成契约。

    [已知未收口，本轮不动] ``\\u200b``(ZWSP) / ``\\ufeff``(BOM) 不属 ``str.isspace()``，
    两侧仍放行；四套 Win32 API 实测均不可穿越，不构成洞。要收口须另立判据，
    且会扩大上面那条误杀面 —— 不在 S10 范围内。
    """
    if not text:
        _raise("PATH_UNSAFE")

    # 相对段检查（在归一到绝对之后仍要做，因为 join 之后 `..` 依然出现）
    path_only = text[2:] if _DRIVE_RE.match(text) else text
    segments = [seg for seg in path_only.replace("\\", "/").split("/")]
    for seg in segments:
        # `core` 只用于**判定**；返回给调用方的路径保持原样，不改写用户拼写。
        core = seg.strip()
        if core in ("", "."):
            continue
        if core == "..":
            _raise("PATH_UNSAFE")
        lowered = core.lower()
        stem = lowered.split(".", 1)[0]
        if stem in _DEVICE_NAMES:
            _raise("PATH_UNSAFE")
        if ":" in core:                     # ADS：file.png:hidden:$DATA
            _raise("PATH_UNSAFE")
        if "~" in core:                     # 8.3 短名：PROGRA~1
            _raise("PATH_UNSAFE")


def _canonical(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").lower()


def _is_within(path: str, root: str) -> bool:
    canon_path = _canonical(path)
    canon_root = _canonical(root)
    if not canon_root:
        return False
    if canon_path == canon_root:
        return True
    return canon_path.startswith(canon_root + "/")


def resolve_and_check(
    path: str, roots: tuple[str, ...], direction: Literal["read", "write"] = "read",
) -> str:
    """校验 ``path`` 是否在 ``roots`` 内并返回归一化路径。

    失败语义（fail-closed）：
      * 空 roots / 不在任何 root 内 → ``PATH_OUTSIDE_ROOTS``
      * `..` / 设备名 / ADS / 8.3 / 越界 symlink → ``PATH_UNSAFE``
    """
    root_list = tuple(r for r in (roots or ()) if str(r or "").strip())
    if not root_list:
        _raise("PATH_OUTSIDE_ROOTS", path=str(path or ""))

    base = root_list[0]
    normalized = normalize_win_path(path, base)
    _assert_safe_segments(normalized)

    if not any(_is_within(normalized, r) for r in root_list):
        _raise("PATH_OUTSIDE_ROOTS", path=normalized)

    # 越界 symlink：只有路径真实存在时才需要落盘解析 realpath
    if os.path.exists(normalized):
        try:
            real = os.path.realpath(normalized).replace("\\", "/")
        except OSError:  # pragma: no cover - 极少数无权限场景
            real = normalized
        if _canonical(real) != _canonical(normalized) and not any(
            _is_within(real, r) for r in root_list
        ):
            _raise("PATH_OUTSIDE_ROOTS", path=normalized)

    return normalized


def build_root_flags(policy: RootsPolicy) -> list[str]:
    """headless 模式显式拼 root 参数；bridge 模式返回空（roots 配在桌面启动命令里）。

    [FACT 链 1] `mcp --bridge` 传 root 会 usage error（exit 2），
    且 `PHOTOCRAFT_AUTOMATION_*` 只作用桌面进程。
    """
    if policy is None or policy.mode != RunMode.HEADLESS:
        return []
    flags: list[str] = []
    read_flag = policy.read_flag or DEFAULT_READ_FLAG
    write_flag = policy.write_flag or DEFAULT_WRITE_FLAG
    for root in policy.read_roots or ():
        flags.extend([read_flag, str(root)])
    for root in policy.write_roots or ():
        flags.extend([write_flag, str(root)])
    return flags


# ---------------------------------------------------------------------------
# 运行时安全配置：startup 注入一次，入口层只读快照
# ---------------------------------------------------------------------------
# [真缺陷，2026-11-08] 入口层两处都写 `default_policy(data_dir, cache_dir, {})`，
# 第三个参数是**空字典** → `[artcraft.security] allow_user_dirs` / `user_read_roots`
# 被读了、进了 `Settings.security`、却从未到达任何一次校验。用户配了也不生效。
# 修法不是给两处各补一个 dict（下次新增入口还会漏），而是把「当前生效策略」
# 收敛成一个具名函数 `active_policy()`：入口只准调它，想绕开就得显式写
# `default_policy(..., {})`，一眼能看出来。
_SECURITY_CFG: dict[str, Any] = {}


def _as_bool(value: Any) -> bool:
    """严格的布尔解释：``"false"`` / ``"0"`` / ``"no"`` / ``"off"`` 都是 False。

    `bool("false")` 是 True —— 配置里写成字符串就等于**反向放开**权限，
    所以这里不走内置 `bool()`。
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _sanitize_roots(values: Any) -> list[str]:
    """只保留能归一成**绝对路径**的条目，其余丢弃。

    相对路径在这里是**无效配置**：`_is_within` 拿它跟绝对路径比对永远 False，
    留着只会让人以为「已经授权了」。空串 / 非字符串 / 重复项同样丢弃。
    单个字符串按一条处理（`user_read_roots = "D:/x"` 是最常见的写错）。
    """
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for item in values or ():
        text = str(item or "").strip().strip('"').strip("'")
        if not text:
            continue
        try:
            norm = normalize_win_path(text)
        except Exception:                       # noqa: BLE001 - 归一失败即无效条目
            continue
        if not (_DRIVE_RE.match(norm) or norm.startswith("//")):
            continue
        key = _canonical(norm)
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


def configure_security(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """startup 注入 ``[artcraft.security]``；**非 dict 一律清空**（配置读坏不得放宽）。

    返回实际生效的快照，供 startup 记录与测试断言。调用方是 lifecycle，
    不是入口 —— 入口永远只能读 ``active_policy()``。
    """
    global _SECURITY_CFG
    source = cfg if isinstance(cfg, dict) else {}
    _SECURITY_CFG = {
        "allow_user_dirs": _as_bool(source.get("allow_user_dirs", False)),
        "user_read_roots": _sanitize_roots(source.get("user_read_roots")),
        "user_write_roots": _sanitize_roots(source.get("user_write_roots")),
        "mode": source.get("mode") or RunMode.HEADLESS,
    }
    return active_security()


def active_security() -> dict[str, Any]:
    """当前生效的安全配置**拷贝**（外部改它不会回写全局）。"""
    return {
        "allow_user_dirs": _as_bool(_SECURITY_CFG.get("allow_user_dirs", False)),
        "user_read_roots": list(_SECURITY_CFG.get("user_read_roots", ()) or ()),
        "user_write_roots": list(_SECURITY_CFG.get("user_write_roots", ()) or ()),
        "mode": _SECURITY_CFG.get("mode") or RunMode.HEADLESS,
    }


def active_policy(data_dir: str, cache_dir: str) -> RootsPolicy:
    """**入口层唯一该用的构造函数**：自动带上 startup 注入的安全配置。

    未配置时与 `default_policy(..., {})` 逐字段相等（仍是 data/cache 两个 root，
    fail-closed 不变）。
    """
    return default_policy(data_dir, cache_dir, active_security())


def default_policy(data_dir: str, cache_dir: str, cfg: dict | None = None) -> RootsPolicy:
    """默认策略：**只**授权插件自己的 ``data/`` 与 ``cache/``（fail-closed）。

    用户显式目录必须写进运行时配置 ``[artcraft.security] user_read_roots`` +
    ``allow_user_dirs = true`` 才生效（重启生效，不运行时动态扩权）。
    """
    settings = cfg or {}
    read_roots: list[str] = [str(data_dir), str(cache_dir)]
    write_roots: list[str] = [str(data_dir), str(cache_dir)]
    allow_user = _as_bool(settings.get("allow_user_dirs", False))
    if allow_user:
        for item in settings.get("user_read_roots", ()) or ():
            value = str(item or "").strip()
            if value:
                read_roots.append(value)
        for item in settings.get("user_write_roots", ()) or ():
            value = str(item or "").strip()
            if value:
                write_roots.append(value)
    mode = settings.get("mode") or RunMode.HEADLESS
    if isinstance(mode, str) and not isinstance(mode, RunMode):
        try:
            mode = RunMode(mode)
        except ValueError:
            mode = RunMode.HEADLESS
    return RootsPolicy(
        read_roots=tuple(read_roots),
        write_roots=tuple(write_roots),
        mode=mode,
        allow_user_dirs=allow_user,
    )


__all__ = [
    "normalize_win_path", "resolve_and_check", "build_root_flags",
    "default_policy", "active_policy", "configure_security", "active_security",
    "_is_within",
]
