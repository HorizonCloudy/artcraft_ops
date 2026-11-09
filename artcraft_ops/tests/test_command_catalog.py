"""§10 #3 / #4 — commands --json 发现、缓存与检索。

覆盖：正常路径（7 字段解析、检索返回 params_schema、缓存命中不 spawn）
      + 边界（6 字段兼容、缓存往返、top_k 截断、无匹配）
      + 错误输入（坏 JSON、不存在的缓存 key）。

用 unittest.mock/monkeypatch 替换 runner.spawn 与 asyncio.create_subprocess_exec，
**绝不启动真实 CLI**。真实 commands --json 样本来自 RESULTS §2.2 / §3 / §6.3。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from _fixtures import (
    COMMANDS_JSON_PHOTOCRAFT_7,
    COMMANDS_JSON_VECTORCRAFT_6,
    SHARPEN_COMMAND_IDS,
    SHARPEN_UNSHARPMASK_PARAMS,
)


def _spec(impl, app_id):
    return impl("app_registry").get_spec(app_id)


def _sharpen_items(impl):
    spec = impl("spec")
    return tuple(
        spec.CommandSpec(
            id=cid,
            label=cid.rsplit(".", 1)[-1],
            menu=("Filter", "Sharpen"),
            params_schema=SHARPEN_UNSHARPMASK_PARAMS,
            enabled=True,
            needs_document=True,
        )
        for cid in SHARPEN_COMMAND_IDS
    )


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
def test_catalog_cache_key_includes_version(impl):
    """#3 正常：缓存 key 带 app 版本（不同版本/不同 app 的 key 必须不同）。"""
    catalog = impl("command_catalog")
    k1 = catalog.cache_key("photocraft", "0.21.0")
    k2 = catalog.cache_key("photocraft", "0.22.0")
    k3 = catalog.cache_key("vectorcraft", "0.21.0")

    assert k1 != k2, "同 app 不同版本必须导致不同缓存 key"
    assert k1 != k3, "不同 app 同版本必须导致不同缓存 key"
    assert catalog.cache_key("photocraft", "0.21.0") == k1, "key 必须确定性可复现"
    assert isinstance(k1, str) and k1


def test_parse_commands_json_photocraft_7_fields(impl):
    """正常：PhotoCraft 7 字段（含 disabledReason / needsDocument）逐项精确解析。"""
    catalog = impl("command_catalog")
    spec = _spec(impl, "photocraft")
    items = catalog.parse_commands_json(json.dumps(COMMANDS_JSON_PHOTOCRAFT_7), spec)

    assert len(items) == 2
    first = items[0]
    assert first.id == "file.new"
    assert first.label == "New…"
    assert tuple(first.menu) == ("File",)
    assert first.params_schema == '{"width":1..300000=1920,"height":1..300000=1080}'
    assert first.enabled is True
    assert first.disabled_reason is None
    assert first.needs_document is False
    assert first.shortcut == "Cmd+N"

    sharp = items[1]
    assert sharp.id == "filter.sharpen.unsharpMask"
    assert sharp.params_schema == SHARPEN_UNSHARPMASK_PARAMS
    assert sharp.needs_document is True
    assert tuple(sharp.menu) == ("Filter", "Sharpen")


def test_search_returns_params_schema(impl, monkeypatch):
    """#4 正常：search('sharpen','photocraft') 含 unsharpMask 且 params_schema 含 amount。"""
    catalog = impl("command_catalog")
    items = _sharpen_items(impl)

    async def fake_discover(app_id, spec, refresh=False):
        return items

    monkeypatch.setattr(catalog, "discover", fake_discover, raising=True)
    monkeypatch.setattr(catalog, "load_cache", lambda key, cache_dir: None, raising=True)
    monkeypatch.setattr(catalog, "save_cache", lambda key, its, cache_dir: None, raising=True)

    got = asyncio.run(catalog.search("sharpen", "photocraft", top_k=8))
    ids = [c.id for c in got]
    assert "filter.sharpen.unsharpMask" in ids
    assert any("amount" in (c.params_schema or "") for c in got)
    assert any(c.params_schema == SHARPEN_UNSHARPMASK_PARAMS for c in got)


def test_save_cache_roundtrip(impl, tmp_path):
    """正常：save_cache -> load_cache 往返不丢字段。"""
    catalog = impl("command_catalog")
    items = _sharpen_items(impl)
    key = catalog.cache_key("photocraft", "0.21.0")

    catalog.save_cache(key, items, str(tmp_path))
    got = catalog.load_cache(key, str(tmp_path))

    assert got is not None
    assert [c.id for c in got] == list(SHARPEN_COMMAND_IDS)
    assert got[3].params_schema == SHARPEN_UNSHARPMASK_PARAMS


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_parse_commands_json_vectorcraft_6_fields(impl):
    """边界：VectorCraft 6 字段（少 disabledReason / needsDocument）兼容，不抛异常。"""
    catalog = impl("command_catalog")
    spec = _spec(impl, "vectorcraft")
    items = catalog.parse_commands_json(json.dumps(COMMANDS_JSON_VECTORCRAFT_6), spec)

    assert len(items) == 1
    c = items[0]
    assert c.id == "file.new"
    assert c.label == "New…"
    assert tuple(c.menu) == ("File",)
    assert c.enabled is True
    assert c.disabled_reason is None
    # §3 [INFERENCE] 6 字段保守设 False/False；允许 None 或 False
    assert c.needs_document in (None, False)


def test_discover_hit_cache_spawn_not_called(impl, monkeypatch):
    """#3 边界：缓存命中时不得启动子进程（runner.spawn.call_count==0）。"""
    catalog = impl("command_catalog")
    runner = impl("runner")
    items = _sharpen_items(impl)
    spec = _spec(impl, "photocraft")
    spawn_calls: list = []

    async def fake_spawn(*args, **kwargs):
        spawn_calls.append(args)
        raise AssertionError("缓存命中时不应 spawn 子进程")

    async def fake_exec(*args, **kwargs):
        spawn_calls.append(args)
        raise AssertionError("缓存命中时不应 create_subprocess_exec")

    monkeypatch.setattr(runner, "spawn", fake_spawn, raising=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec, raising=False)
    monkeypatch.setattr(catalog, "load_cache", lambda key, cache_dir: items, raising=True)

    got = asyncio.run(catalog.discover("photocraft", spec, refresh=False))
    assert [c.id for c in got] == list(SHARPEN_COMMAND_IDS)
    assert spawn_calls == []


def test_search_top_k_limits_results(impl, monkeypatch):
    """边界：top_k=3 时最多返回 3 条（不把 993 条全量塞给模型）。"""
    catalog = impl("command_catalog")
    items = _sharpen_items(impl)
    monkeypatch.setattr(catalog, "discover", lambda *a, **k: _coro(items), raising=True)
    monkeypatch.setattr(catalog, "load_cache", lambda key, cache_dir: None, raising=True)
    monkeypatch.setattr(catalog, "save_cache", lambda key, its, cache_dir: None, raising=True)

    got = asyncio.run(catalog.search("sharpen", "photocraft", top_k=3))
    assert len(got) <= 3


def _coro(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner()


def test_search_no_match_returns_empty(impl, monkeypatch):
    """边界/错误：零命中返回空元组，不抛异常、不返回 993 全量。"""
    catalog = impl("command_catalog")
    monkeypatch.setattr(catalog, "discover", lambda *a, **k: _coro(()), raising=True)
    monkeypatch.setattr(catalog, "load_cache", lambda key, cache_dir: None, raising=True)
    monkeypatch.setattr(catalog, "save_cache", lambda key, its, cache_dir: None, raising=True)

    got = asyncio.run(catalog.search("zzz_no_such_command_9999", "photocraft"))
    assert tuple(got) == ()


# ---------------------------------------------------------------------------
# 错误输入
# ---------------------------------------------------------------------------
def test_parse_commands_json_invalid_json_raises(impl):
    """错误：坏 JSON 必须显式失败（COMMANDS_DISCOVERY_FAILED 或解析异常），不得静默返回空表。"""
    catalog = impl("command_catalog")
    spec = _spec(impl, "photocraft")
    with pytest.raises(Exception) as ei:
        catalog.parse_commands_json("{this is not json", spec)
    detail = f"{ei.value} {type(ei.value).__name__}"
    assert ("COMMANDS_DISCOVERY_FAILED" in detail
            or isinstance(ei.value, (ValueError, TypeError, json.JSONDecodeError))), detail


def test_load_cache_missing_key_returns_none(impl, tmp_path):
    """错误：不存在的 cache key 返回 None（触发重新发现，而不是崩溃）。"""
    catalog = impl("command_catalog")
    assert catalog.load_cache("definitely-missing-key-9999", str(tmp_path)) is None
