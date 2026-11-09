"""§10 #22 — #9777 token 泄露规避：不用 localhost / trust_env=False。

覆盖：正常路径（字面 127.0.0.1、assert_no_localhost 通过、trust_env False）
      + 边界（大写/混合 localhost 也拒、Studio 通道仅留协议接口）
      + 错误输入（localhost URL 必须抛错、坏 token 文件必须拒）。

[FACT] RESULTS §9.2：Windows 上 localhost 解析到 [::1]，而 app 只绑 127.0.0.1，
修复 PR #9778 仍 Open -> v1 不启用 bridge，但常量与校验函数先落地（ARCHITECTURE §6）。
"""
from __future__ import annotations

import json

import pytest

from _fixtures import (
    INSTANCE_JSON_SAMPLE,
    LOOPBACK_HOST,
    LOOPBACK_URL_LOCALHOST,
    LOOPBACK_URL_OK,
)


def _detail(exc: BaseException) -> str:
    return f"{exc} {type(exc).__name__}"


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
def test_proxy_bypass(impl):
    """#22 P0 正常：回环用字面 127.0.0.1；HTTP client trust_env is False。"""
    bridge = impl("bridge")

    assert bridge.LOOPBACK_HOST == "127.0.0.1"
    assert bridge.assert_no_localhost(LOOPBACK_URL_OK) is None

    client = bridge.build_http_client_no_proxy()
    assert client.trust_env is False, "必须 trust_env=False 绕过系统代理"


def test_loopback_host_is_literal_not_localhost(impl):
    """正常：LOOPBACK_HOST 常量本身不含 localhost 字样。"""
    bridge = impl("bridge")
    assert bridge.LOOPBACK_HOST == LOOPBACK_HOST
    assert "localhost" not in bridge.LOOPBACK_HOST.lower()
    assert bridge.LOOPBACK_HOST.startswith("127.")


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_url", [
    LOOPBACK_URL_LOCALHOST,
    "HTTP://LOCALHOST:52903/",
    "http://LocalHost:52903/mcp",
])
def test_assert_no_localhost_rejects_localhost(impl, bad_url):
    """#22 边界/错误：任何大小写形式的 localhost 都必须被拒。"""
    bridge = impl("bridge")
    with pytest.raises((AssertionError, ValueError)) as ei:
        bridge.assert_no_localhost(bad_url)
    assert "localhost" in _detail(ei.value).lower() or "127.0.0.1" in _detail(ei.value), \
        _detail(ei.value)


def test_read_token_user_only_valid_file(impl, tmp_path):
    """边界：合法的 64hex token 被读入内存并返回。"""
    bridge = impl("bridge")
    p = tmp_path / "instance.json"
    p.write_text(INSTANCE_JSON_SAMPLE, encoding="utf-8")
    token = bridge.read_token_user_only(str(p))
    assert token == "a" * 64
    assert len(token) == 64


def test_studio_channel_protocol_is_reserved_only(impl):
    """边界/§6：Studio 云端通道 v1 只留协议接口，不与本地 GUI 抽象成同一控制面。"""
    studio = impl("studio_channel")
    assert hasattr(studio, "StudioCloudChannel")
    assert hasattr(studio.StudioCloudChannel, "create_session")
    assert hasattr(studio.StudioCloudChannel, "close")


# ---------------------------------------------------------------------------
# 错误输入
# ---------------------------------------------------------------------------
def test_read_token_user_only_rejects_bad_token(impl, tmp_path):
    """错误：token 非 64hex 必须拒绝（绝不把畸形 secret 送进控制通道）。"""
    bridge = impl("bridge")
    p = tmp_path / "instance.json"
    p.write_text(json.dumps({"pid": 1, "port": 52903, "token": "short"}), encoding="utf-8")
    with pytest.raises(Exception) as ei:
        bridge.read_token_user_only(str(p))
    assert ("token" in _detail(ei.value).lower()
            or isinstance(ei.value, (ValueError, AssertionError))), _detail(ei.value)


def test_assert_no_localhost_rejects_wrong_host(impl):
    """错误：非回环地址（含 localhost 别名）不得通过。"""
    bridge = impl("bridge")
    with pytest.raises((AssertionError, ValueError)):
        bridge.assert_no_localhost("http://0.0.0.0:52903/")
