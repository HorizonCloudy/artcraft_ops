"""§10 #26 / #28 / #31 — 错误码表、CLI→宿主映射、Err 构造。

覆盖：正常路径（exit 1/2/roots stderr 精确映射、19 个错误码齐全）
      + 边界（did-you-mean -> NAMESPACE_ERROR、拍平警告不算错误、未知码仍加前缀）
      + 错误输入（未知码、空 stderr）。

[FACT] ARCHITECTURE §8：宿主把 Err.code 抹平为 INTERNAL，
故关键码必须写进 message，errors.err() 强制 ``f"{code}: {t(code)}"`` 前缀。
"""
from __future__ import annotations

import pytest

from _fixtures import (
    EXPECTED_ERROR_CODES,
    FLATTEN_WARNING_FULL_LINE,
    NAMESPACE_TRAP_DID_YOU_MEAN_STDERR,
    ROOTS_NOT_GRANTED_STDERR_A,
    ROOTS_NOT_GRANTED_STDERR_B,
)


def _spec(impl):
    return impl("app_registry").get_spec("photocraft")


def _err_message(e) -> str:
    """取错误 envelope 的 ``error.message``。

    Phase 2 契约（S16 error surface）：``errors.err()`` 不再返回 SDK 的 ``Err``，
    而是宿主可直接消费的裸 dict envelope，固定码在 ``error.code``、
    带 ``<CODE>: `` 前缀的人读文案在 ``error.message``。
    """
    assert isinstance(e, dict), f"期望错误 envelope dict，实际 {e!r}"
    error = e.get("error")
    assert isinstance(error, dict), f"envelope.error 必须是 dict，实际 {error!r}"
    return str(error.get("message") or "")


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", [
    "LIMIT_STEPS", "APP_NOT_FOUND", "CLI_USAGE_ERROR", "ROOTS_NOT_GRANTED",
    "POLICY_VIOLATION", "JOB_CRASHED",
])
def test_err_code_in_message(impl, code):
    """#31 P0 正常：每个 Err 的 message 必须以 ``<CODE>: `` 开头。"""
    errors = impl("errors")
    e = errors.err(code)
    msg = _err_message(e)
    assert msg.startswith(f"{code}: "), f"关键码必须写进 message，实际: {msg!r}"
    assert len(msg) > len(code) + 2, "message 还需带用户可读文案"


def test_map_cli_error_exit1(impl):
    """正常：exit 1 -> CLI_TASK_FAILED，hint 非空。"""
    errors = impl("errors")
    code, hint = errors.map_cli_error(1, "command execution failed", _spec(impl))
    assert code == "CLI_TASK_FAILED"
    assert isinstance(hint, str)


def test_map_cli_error_exit2(impl):
    """正常：exit 2 -> CLI_USAGE_ERROR（整批未保存任何产物）。"""
    errors = impl("errors")
    code, hint = errors.map_cli_error(2, "usage error: --out == FILE", _spec(impl))
    assert code == "CLI_USAGE_ERROR"


@pytest.mark.parametrize("stderr", [
    ROOTS_NOT_GRANTED_STDERR_A,
    ROOTS_NOT_GRANTED_STDERR_B,
])
def test_roots_not_granted_stderr(impl, stderr):
    """#28 P0 正常：两种 roots 未授权 stderr 原文都映射到 ROOTS_NOT_GRANTED。"""
    errors = impl("errors")
    code, hint = errors.map_cli_error(2, stderr, _spec(impl))
    assert code == "ROOTS_NOT_GRANTED", f"实际: {code}"


def test_error_codes_table_complete(impl):
    """正常/边界：§8 的 19 个码齐全，且每个都有 zh/en 两套文案。"""
    errors = impl("errors")
    assert set(errors.ERROR_CODES) == set(EXPECTED_ERROR_CODES)
    for code, texts in errors.ERROR_CODES.items():
        assert "zh" in texts and "en" in texts, f"{code} 缺 zh/en 文案"
        assert texts["zh"] and texts["en"]


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_namespace_hint_maps_to_namespace_error(impl):
    """边界/§8：stderr 含 `did you mean document.inspect` -> NAMESPACE_ERROR。"""
    errors = impl("errors")
    code, hint = errors.map_cli_error(2, NAMESPACE_TRAP_DID_YOU_MEAN_STDERR, _spec(impl))
    assert code == "NAMESPACE_ERROR"
    assert isinstance(hint, str) and hint, "NAMESPACE_ERROR 必须带可读提示"


def test_flatten_warning_is_not_an_error(impl):
    """边界/§8：拍平警告不是错误，不得映射成任何 CLI_* / ROOTS_* 错误码。"""
    errors = impl("errors")
    code, _hint = errors.map_cli_error(0, FLATTEN_WARNING_FULL_LINE, _spec(impl))
    assert str(code).upper() not in {
        "CLI_USAGE_ERROR", "CLI_TASK_FAILED", "ROOTS_NOT_GRANTED",
        "NAMESPACE_ERROR", "POLICY_VIOLATION",
    }, f"拍平警告被误判为错误: {code}"


def test_err_with_kwargs_interpolates(impl):
    """边界：err(code, path=...) 把占位值插进文案。"""
    errors = impl("errors")
    msg = _err_message(errors.err("PATH_OUTSIDE_ROOTS", path=r"C:\secret.png"))
    assert msg.startswith("PATH_OUTSIDE_ROOTS: ")
    assert r"C:\secret.png" in msg


# ---------------------------------------------------------------------------
# 错误输入
# ---------------------------------------------------------------------------
def test_err_unknown_code_still_prefixes(impl):
    """错误：未知码不得崩溃，仍以 ``<CODE>: `` 开头（防御式）。"""
    errors = impl("errors")
    msg = _err_message(errors.err("TOTALLY_UNKNOWN_CODE_X"))
    assert msg.startswith("TOTALLY_UNKNOWN_CODE_X: ")


def test_map_cli_error_empty_stderr_exit0(impl):
    """错误/边界：exit 0 + 空 stderr 不应产生任何错误码。"""
    errors = impl("errors")
    code, _hint = errors.map_cli_error(0, "", _spec(impl))
    assert str(code).upper() not in {
        "CLI_USAGE_ERROR", "CLI_TASK_FAILED", "ROOTS_NOT_GRANTED",
        "NAMESPACE_ERROR", "POLICY_VIOLATION", "LIMIT_REPLY",
    }, f"exit 0 被误判为错误: {code}"
