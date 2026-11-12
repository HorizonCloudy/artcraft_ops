"""artcraft_ops —— 领域层：命令目录发现（lazy）+ 缓存 + 检索。

三条硬约束：
  1. **绝不在 startup 调用**：`on_startup` 走瞬时 `asyncio.run`，起后台 task 会立刻被取消
     （`game_agent_minecraft/__init__.py:192-200`），成熟范式是首次 entry lazy-start。
  2. **首次 entry 才 discovery**，且 per-app ``asyncio.Lock`` 保证并发只 spawn 一次。
  3. 缓存 key = app 版本 + commands 表 hash（**不只是版本号**），字段级 per-app 解析器分离
     （PhotoCraft camelCase 8-key / Craft Anything 扁平 6-key，§16.4 schema 方言 4 种）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from typing import Any

from . import locator, runner
from .errors import ArtcraftError
from .spec import CONTROL_CALL_TIMEOUT_S, CommandSpec

_DISCOVERY_LOCKS: dict[str, asyncio.Lock] = {}
_CACHE_DIR: str = os.path.join(tempfile.gettempdir(), "artcraft_ops_cache")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_.]+|[\u4e00-\u9fff]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# 中文操作词 → 英文检索词（命令表只有英文）；只收常用词，缺了再加
_ZH_WORDS = {
    "调亮": "brightness contrast exposure levels",
    "提亮": "brightness contrast exposure levels",
    "亮度": "brightness",
    "对比度": "contrast",
    "曝光": "exposure",
    "色阶": "levels",
    "曲线": "curves",
    "饱和度": "saturation",
    "导出": "export",
    "锐化": "sharpen",
    "模糊": "blur",
    "裁剪": "crop",
    "旋转": "rotate",
    "图像大小": "image size",
    "撤销": "undo",
}


def configure(cache_dir: str) -> None:
    """由 startup 注入真实 cache 目录（``plugin.cache_path()``），默认落临时目录。"""
    global _CACHE_DIR
    if cache_dir:
        _CACHE_DIR = str(cache_dir)


def current_cache_dir() -> str:
    return _CACHE_DIR


def cache_key(
    app_id: str, version_token: str, commands_hash: str = "", parser: str = "",
) -> str:
    """``sha256(app | version | commands_hash | parser)[:20]`` —— 决定性、跨版本/跨 app 必不同。

    ``parser`` 隔离解析器换代：旧解析器落盘的同版本缓存不可复用（PdfCraft 99→195）。
    """
    payload = f"{app_id or ''}|{version_token or ''}|{commands_hash or ''}|{parser or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------------------
# 解析：per-app 分离（§15.1 / §16.3）
# ---------------------------------------------------------------------------
def _readonly(cmd_id: str, spec: Any) -> bool:
    prefixes = getattr(spec, "readonly_prefixes", ()) or ()
    return any(cmd_id == p or cmd_id.startswith(str(p) + ".") for p in prefixes)


def _parse_photocraft(items: list[dict], spec: Any) -> list[CommandSpec]:
    """camelCase 8-key（PhotoCraft）/ 6-key（VectorCraft）共用同一读取器。"""
    out: list[CommandSpec] = []
    has_reason = bool(getattr(spec, "has_disabled_reason", False))
    has_needs_doc = bool(getattr(spec, "has_needs_document", False))
    for item in items:
        if not isinstance(item, dict):
            continue
        cmd_id = str(item.get("id") or item.get("command") or "").strip()
        if not cmd_id:
            continue
        out.append(CommandSpec(
            id=cmd_id,
            label=str(item.get("label") or item.get("title") or ""),
            menu=tuple(str(m) for m in (item.get("menu") or ())),
            params_schema=str(item.get("params") or ""),
            enabled=bool(item.get("enabled", True)),
            disabled_reason=(item.get("disabledReason") if has_reason else None),
            needs_document=(item.get("needsDocument") if has_needs_doc else None),
            shortcut=str(item.get("shortcut") or ""),
            readonly=_readonly(cmd_id, spec),
        ))
    return out


def _parse_craft_anything(items: list[dict], spec: Any) -> list[CommandSpec]:
    """扁平 6-key：``command``→id、``title``→label、``summary``→params_schema、``tool``→alias。"""
    out: list[CommandSpec] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cmd_id = str(item.get("command") or item.get("tool") or "").strip()
        if not cmd_id:
            continue
        toolset = str(item.get("toolset") or "")
        out.append(CommandSpec(
            id=cmd_id,
            label=str(item.get("title") or cmd_id),
            menu=(toolset,) if toolset else (),
            params_schema=str(item.get("summary") or ""),
            enabled=bool(item.get("available", True)),
            disabled_reason=None,
            needs_document=(bool(item.get("needs_target")) if "needs_target" in item else None),
            shortcut="",
            readonly=_readonly(cmd_id, spec),
            alias=str(item.get("tool") or ""),
        ))
    return out


def _parse_pdfcraft(items: list[dict], spec: Any) -> list[CommandSpec]:
    """``tools --json``：``name`` 是 ``--script`` 唯一认的 tool 名，``command`` 只是别名。"""
    out: list[CommandSpec] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("name") or "").strip()
        if not tool:
            continue
        schema = item.get("input_schema")
        out.append(CommandSpec(
            id=tool,
            label=str(item.get("description") or tool),
            params_schema=(
                json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
                if isinstance(schema, dict) else ""
            ),
            enabled=True,
            readonly=bool(item.get("read_only", _readonly(tool, spec))),
            alias=str(item.get("command") or ""),
        ))
    return out


def _as_items(payload: Any) -> list[dict]:
    """把 ``[...]`` / ``{"commands": [...]}`` / ``{"result": ...}`` 信封统一拍平成 list。"""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("result", "commands", "tools", "items", "data"):
            node = payload.get(key)
            flat = _as_items(node)
            if flat:
                return flat
        if payload.get("id") or payload.get("command"):
            return [payload]
    return []


def parse_commands_json(raw: str, spec: Any = None) -> tuple[CommandSpec, ...]:
    """解析 ``commands --json`` 输出：支持整段 JSON 与 JSON-lines 两种形态。

    坏 JSON **必须显式失败**（不得静默返回空表，否则「检索不到命令」无法定位）。
    """
    text = str(raw or "").strip()
    if not text:
        return ()

    payload: Any = None
    try:
        payload = json.loads(text)
    except ValueError:
        # JSON-lines：CLI 也可能逐行输出
        collected: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or not line.startswith(("{", "[")):
                continue
            try:
                collected.extend(_as_items(json.loads(line)))
            except ValueError:
                continue
        if not collected:
            raise
        payload = collected

    items = _as_items(payload)
    parser = getattr(spec, "commands_parser", "photocraft") if spec is not None else "photocraft"
    if parser == "craft_anything":
        return tuple(_parse_craft_anything(items, spec))
    if parser == "pdfcraft":
        return tuple(_parse_pdfcraft(items, spec))
    return tuple(_parse_photocraft(items, spec))


def parse_capabilities_json(raw: str) -> dict[str, bool]:
    """解析 ``capabilities --json``：``{tool: available}``（CA 的权威可用性来源）。"""
    try:
        payload = json.loads(str(raw or "") or "{}")
    except ValueError:
        return {}
    result: dict[str, bool] = {}
    toolsets = payload.get("toolsets") if isinstance(payload, dict) else None
    if isinstance(toolsets, list):
        for toolset in toolsets:
            if not isinstance(toolset, dict):
                continue
            for tool in toolset.get("tools") or ():
                if isinstance(tool, dict):
                    name = str(tool.get("tool") or "").strip()
                    if name:
                        result[name] = bool(tool.get("available", False))
    return result


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
def _cache_path(key: str, cache_dir: str) -> str:
    return os.path.join(str(cache_dir or _CACHE_DIR), f"{key}.json")


def load_cache(key: str, cache_dir: str) -> tuple[CommandSpec, ...] | None:
    """读缓存；不存在 / 坏文件 → ``None``（触发重新发现，绝不崩）。"""
    path = _cache_path(key, cache_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    out: list[CommandSpec] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            out.append(CommandSpec(
                id=str(item.get("id") or ""),
                label=str(item.get("label") or ""),
                menu=tuple(str(m) for m in (item.get("menu") or ())),
                params_schema=str(item.get("params_schema") or ""),
                enabled=bool(item.get("enabled", True)),
                disabled_reason=item.get("disabled_reason"),
                needs_document=item.get("needs_document"),
                shortcut=str(item.get("shortcut") or ""),
                readonly=bool(item.get("readonly", False)),
                alias=str(item.get("alias") or ""),
            ))
        except (TypeError, ValueError):
            continue
    return tuple(out) if out else None


def save_cache(key: str, items: tuple[CommandSpec, ...], cache_dir: str) -> None:
    """写缓存（best-effort：写失败只影响下次重新发现，绝不影响主流程）。"""
    path = _cache_path(key, cache_dir)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        payload = [
            {
                "id": it.id, "label": it.label, "menu": list(it.menu),
                "params_schema": it.params_schema, "enabled": it.enabled,
                "disabled_reason": it.disabled_reason,
                "needs_document": it.needs_document, "shortcut": it.shortcut,
                "readonly": it.readonly, "alias": it.alias,
            }
            for it in items
        ]
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        return


# ---------------------------------------------------------------------------
# 发现
# ---------------------------------------------------------------------------
def _commands_argv(app_id: str, spec: Any, cli: str) -> list[str]:
    """按 ``commands_mode`` 拼命令目录请求；``none`` 返回空列表（无可发现通道）。

    实机 4 种形态（§19.2）：
      * ``json_flag``    ``commands --json``   —— 12 个 app
      * ``json_default`` ``commands``          —— CADCraft / MaterialCraft（默认就吐 JSON，加 --json 反被 clap 拒）
      * ``plain``        ``commands``          —— DeckCraft / SoundCraft / TextureCraft / EffectCraft（人类可读文本）
      * ``none``         无 ``commands`` 子命令 —— PdfCraft（`unknown command 'commands'`）
    """
    mode = str(getattr(spec, "commands_mode", "json_flag") or "json_flag")
    if mode == "none":
        return []
    argv = [cli, str(getattr(spec, "commands_subcmd", "commands") or "commands")]
    if mode == "json_flag":
        json_flag = str(getattr(spec, "commands_json_flag", "--json") or "")
        if json_flag:
            argv.append(json_flag)
    return argv


def parse_commands_plain(raw: str) -> tuple[CommandSpec, ...]:
    """解析人类可读的 ``commands`` 文本（DeckCraft / TextureCraft / SoundCraft）。

    两种实机版式：
      * ``file.new — New Presentation: {theme?: name}``   （破折号分隔）
      * ``edit.undo                    Undo               {steps?: 1}``（多空格分栏）

    解析不了的行走跳过分支——宁可少几条，也不要凭空造命令 id。
    """
    out: list[CommandSpec] = []
    for line in (raw or "").splitlines():
        text = line.rstrip()
        if not text.strip() or text.lstrip().startswith(("#", "//")):
            continue
        m = re.match(r"^(?P<id>[A-Za-z_][\w.\-]*)\s+—\s+(?P<rest>.+)$", text.strip())
        label = ""
        params = ""
        if m:
            cmd_id = m.group("id")
            rest = m.group("rest").strip()
            if ":" in rest:
                label, _, params = rest.partition(":")
            else:
                label = rest
        else:
            m = re.match(r"^(?P<id>[A-Za-z_][\w.\-]*)\s{2,}(?P<rest>.+)$", text.strip())
            if not m:
                continue
            cmd_id = m.group("id")
            rest = m.group("rest").rstrip()
            parts = re.split(r"\s{2,}", rest, maxsplit=1)
            label = parts[0].strip()
            params = parts[1].strip() if len(parts) > 1 else ""
        if not cmd_id:
            continue
        out.append(CommandSpec(
            id=cmd_id,
            label=label.strip(),
            params_schema=params.strip(),
            enabled=True,
        ))
    return tuple(out)


async def _fetch(app_id: str, spec: Any) -> tuple[CommandSpec, ...]:
    cli = str(getattr(spec, "cli_path", "") or "")
    if not cli:
        cli = locator.resolve_cli(app_id, {})
    argv = _commands_argv(app_id, spec, cli)
    if not argv:
        raise ArtcraftError(
            "COMMANDS_DISCOVERY_FAILED",
            f"{app_id} 没有 commands 子命令（commands_mode=none），无法枚举命令目录",
        )
    cwd = os.path.dirname(cli) or os.getcwd()
    handle = await runner.spawn(
        argv, cwd=cwd, env=locator.build_cli_env(),
        job_id=f"discover-{app_id}", timeout=CONTROL_CALL_TIMEOUT_S,
    )
    try:
        raw = await asyncio.wait_for(
            runner.stream_lines(handle, None), timeout=CONTROL_CALL_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        await runner.terminate(handle)
        raise ArtcraftError("COMMANDS_DISCOVERY_FAILED", "discovery timed out") from exc
    if int(raw.returncode) != 0:
        raise ArtcraftError(
            "COMMANDS_DISCOVERY_FAILED", f"exit code {raw.returncode}",
        )
    text = "\n".join(raw.stdout_lines)
    if str(getattr(spec, "commands_mode", "") or "") == "plain":
        items = parse_commands_plain(text)
        if items:
            return items
        # 纯文本解析不出东西时退回 JSON 解析（有些 app 自称 plain 其实吐 JSON）
    return parse_commands_json(text, spec)


async def discover(
    app_id: str, spec: Any, refresh: bool = False,
) -> tuple[CommandSpec, ...]:
    """lazy、可重入、带缓存的命令目录发现。

    缓存 key 只含 ``app + 版本``（拿到全量 stdout 后再补 ``commands_hash`` 落一份，
    这样版本内命令表变化也能命中第二份缓存）。
    """
    version = str(getattr(spec, "version", "") or "")
    parser = str(getattr(spec, "commands_parser", "") or "")
    key = cache_key(app_id, version, parser=parser)
    cache_dir = _CACHE_DIR

    if not refresh:
        cached = load_cache(key, cache_dir)
        if cached:
            return cached

    lock = _DISCOVERY_LOCKS.setdefault(app_id, asyncio.Lock())
    async with lock:
        if not refresh:
            cached = load_cache(key, cache_dir)
            if cached:
                return cached
        items = await _fetch(app_id, spec)
        if items:
            digest = hashlib.sha256(
                "".join(sorted(it.id for it in items)).encode("utf-8"),
            ).hexdigest()[:16]
            save_cache(key, items, cache_dir)
            save_cache(
                cache_key(app_id, version, digest, parser=parser), items, cache_dir,
            )
        return items


# ---------------------------------------------------------------------------
# 检索（993 条命令绝不逐条暴露给模型）
# ---------------------------------------------------------------------------
def _fields(item: CommandSpec) -> tuple[str, ...]:
    """只查名称类字段：params_schema 里的 this/nothing 这类短词是假命中源。"""
    parts = (item.id, item.label, item.alias, " ".join(item.menu))
    return tuple(_CAMEL_RE.sub(" ", p).lower() for p in parts)


def _hit(text: str, term: str) -> bool:
    """英文按词边界（brightness 不命中参数说明里的短词），中文按子串。"""
    if term.isascii():
        return re.search(
            rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", text,
        ) is not None
    return term in text


def score_item(item: CommandSpec, query: str, tokens: list[str]) -> int:
    fields = _fields(item)
    score = 0
    if query and any(_hit(field, query) for field in fields):
        score += 4
    for token in tokens:
        for weight, field in zip((4, 3, 2, 2), fields):
            if token and _hit(field, token):
                score += weight
                break
    if query and fields[0].startswith(query):
        score += 4
    return score


async def search(
    query: str, app_id: str, top_k: int = 8,
) -> tuple[CommandSpec, ...]:
    """按关键词检索命令（本地打分；不 spawn —— 发现走缓存/lazy）。"""
    from . import app_registry  # 局部导入：避免 app_registry ↔ catalog 顶层循环

    spec = app_registry.get_spec(app_id)
    items = await discover(app_id, spec)
    text = str(query or "").strip().lower()
    if not text:
        return ()
    tokens = [tok.lower() for tok in _TOKEN_RE.findall(text)]
    terms = list(tokens)
    for zh, en in _ZH_WORDS.items():
        if zh in text:
            terms.extend(en.split())
    expanded = terms != tokens
    need = 1 if expanded or len(tokens) < 2 else 2
    scored: list[tuple[int, str, CommandSpec]] = []
    for item in items:
        if not expanded and need > 1:
            fields = _fields(item)
            hits = sum(1 for token in tokens if any(_hit(f, token) for f in fields))
            if hits < need:
                continue
        value = score_item(item, text, terms)
        if value > 0:
            scored.append((value, item.id, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    limit = max(0, int(top_k))
    return tuple(row[2] for row in scored[:limit])


__all__ = [
    "configure", "current_cache_dir", "cache_key",
    "parse_commands_json", "parse_capabilities_json",
    "load_cache", "save_cache", "discover", "search", "score_item",
    "_DISCOVERY_LOCKS",
]
