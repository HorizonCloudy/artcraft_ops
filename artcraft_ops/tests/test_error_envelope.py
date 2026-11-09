"""Phase 2 红测试 —— 错误 envelope 出口（S16 error surface）。

契约（team-lead 定稿，Phase 3 实现；**实现前不许改本文件的断言**）::

    err() / safe_err() / safe_error() / error_for()  ->  裸 dict

    {
      "success": False,
      "code": 500,                        # int，与宿主 fail() 同形；自定义码默认 500
      "data": None,
      "message": "",                      # 与宿主 fail() 同形
      "error": {
          "code": "<CODE>",               # 固定枚举码，如 "PATH_UNSAFE"
          "message": "<CODE>: <脱敏文案>", # 保留 "<CODE>: " 前缀，便于人读与既有 grep
          "details": {...} | None,        # 仅标量、已脱敏、<=8 键；无字段则 None
          "retriable": False,
      },
      "time": "<ISO8601>",
      "is_error": True,                   # LLM 工具通道需要
      "output": {"error_code": "<CODE>", "error_message": "<脱敏文案>"},
    }

为什么必须是这个形状（[FACT 宿主侧]，本文件**不 import 宿主模块**，插件测试要能
脱离宿主独立跑；依据来自 `neko-src/` 源码副本的逐条核对）：

  * ``plugin/core/responses.py`` ``is_envelope()`` —— 只硬性要求三件事：
    ``isinstance(v, dict)`` + ``v.get("success") in (True, False)`` +
    ``"error" in v`` + ``"time" in v``；
  * ``plugin/runs/manager.py`` —— ``error.code`` 直接落 ``RunError.code``（str，无白名单），
    ``success`` 为假则 run 记 ``failed``；
  * ``plugin/server/routes/llm_tools.py`` —— handler 返回的 dict **只有**含 ``is_error``
    才走错误透传，否则被包成 ``{"output": result, "is_error": False}`` 当成成功。

覆盖口径（正常路径 + 边界 + 错误输入）：
  1. envelope 形状（含 int/bool 类型守卫，防 ``500`` 写成 ``"500"``、防 falsy 冒充 False）
  2. 宿主判定器等价（内联**冻结副本** + 负向控制，证明副本可被证伪）
  3. 入口层真实调用（257 步 / 未注册命令 / 越界路径），且都断言「未 spawn 子进程」
  4. 成功路径不回归（成功 payload 不得含 ``success`` 键）
  5. details 脱敏（仅标量、<=8 键、无字段为 None、hex token 被抹；家目录/用户名脱敏
     已按 team-lead 裁决移出本轮，归 S17）
  6. ``error_for()`` 三分支
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from _fixtures import FakeCtx, FakeProcess

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# 越界路径的两种 fail-closed 码：具体命中哪个由 roots 判据顺序决定，
# 断言**不许**把某一个钉死（否则实现侧把 deny-list 提前就会误红）。
OUT_OF_ROOT_CODES = ("PATH_OUTSIDE_ROOTS", "PATH_UNSAFE")

_ISO_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


# ---------------------------------------------------------------------------
# 0) 宿主判定器的**冻结副本**（不 import 宿主任何模块）
# ---------------------------------------------------------------------------
def is_envelope(value):
    """冻结自 plugin/core/responses.py:146-173 —— 判据语义逐行等价，禁止下游改动。"""
    if not isinstance(value, dict):
        return False
    if value.get("success") not in (True, False):
        return False
    if "error" not in value:
        return False
    if "time" not in value:
        return False
    return True


def _assert_iso8601(value) -> None:
    """``time`` 必须是可解析的 ISO8601 字符串（容忍 ``Z`` / ``+00:00`` / 无时区）。"""
    assert isinstance(value, str), f"time 必须是字符串，实际 {type(value).__name__}"
    text = value.strip()
    assert _ISO_PREFIX_RE.match(text), f"time 不是 ISO8601 开头：{value!r}"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    datetime.fromisoformat(text)          # 解析失败会抛 ValueError -> 测试红


def _error(env) -> dict:
    """取 envelope 的 error 子字典；形状不对时给出可读失败原因。"""
    assert isinstance(env, dict), f"失败必须返回裸 dict envelope，实际 {env!r}"
    err = env.get("error")
    assert isinstance(err, dict), f"error 必须是 dict，实际 {err!r}"
    return err


def _code(env) -> str:
    code = _error(env).get("code")
    assert isinstance(code, str) and code, f"error.code 必须是非空 str，实际 {code!r}"
    return code


def _dump(value) -> str:
    """序列化 envelope 供「不得包含敏感串」类断言用。

    非 dict（红阶段的 ``Err``）退回 ``repr``：这样红阶段仍能在**同类断言**上红，
    而不是抛 ``TypeError: 'Err' object is not subscriptable`` 掩盖真正的缺口。
    """
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return repr(value)


def _load_plugin(load_plugin_init, tmp_path):
    """按 ``plugin.plugins.artcraft_ops`` 名字加载真实 ``__init__.py`` 并实例化插件。"""
    module = load_plugin_init()
    plugin = module.ArtcraftOpsPlugin(
        FakeCtx(storage_dir=tmp_path, plugin_dir=PLUGIN_ROOT),
    )
    return module, plugin


def _no_spawn(monkeypatch, calls: list) -> None:
    """拦住真实子进程创建并记录 argv（入口失败路径必须零调用）。"""

    async def fake_exec(*args, **kwargs):
        calls.append(tuple(args))
        return FakeProcess(stdout_lines=(), returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec, raising=False)


async def _settle(res):
    """等待可观测条件：取消入口内起的所有后台 task，避免 pending-task 噪音。"""
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return res


def _run(coro):
    """统一 ``asyncio.run`` 包装（本套件不用 pytest-asyncio）。"""

    async def scenario():
        return await _settle(await coro)

    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 1) envelope 形状
# ---------------------------------------------------------------------------
def test_err_returns_plain_dict_envelope(impl):
    """P0 正常：``err("PATH_UNSAFE")`` 必须是裸 dict，且必填键齐全。"""
    errors = impl("errors")
    env = errors.err("PATH_UNSAFE")

    assert isinstance(env, dict), f"每次失败都必须出 envelope dict，实际 {env!r}"
    missing = (
        {"success", "code", "data", "message", "error", "time", "is_error", "output"}
        - set(env)
    )
    assert not missing, f"envelope 缺必填键：{sorted(missing)}"


def test_err_envelope_field_semantics(impl):
    """P0 正常：每个字段的**类型与取值**都钉死（防 '500' / 0 / '' 冒充）。"""
    errors = impl("errors")
    env = errors.err("PATH_UNSAFE")
    err = _error(env)

    assert env["success"] is False, "success 必须是布尔 False（不是 0 / None）"
    assert env["code"] == 500, f"自定义码默认 500，实际 {env['code']!r}"
    assert isinstance(env["code"], int) and not isinstance(env["code"], bool), \
        f"code 必须是 int（宿主 fail() 同形），实际 {type(env['code']).__name__}"
    assert env["data"] is None, "data 必须是 None"
    assert env.get("message") == "", "message 必须是空串（宿主 fail() 同形）"
    assert env["is_error"] is True, "is_error 必须是布尔 True（llm_tools.py 靠它透传）"

    assert err["code"] == "PATH_UNSAFE", f"error.code 必须是固定枚举码，实际 {err['code']!r}"
    assert err["message"].startswith("PATH_UNSAFE: "), \
        f"error.message 必须保留 '<CODE>: ' 前缀，实际 {err['message']!r}"
    assert len(err["message"]) > len("PATH_UNSAFE: "), "message 还需带用户可读文案"
    assert err.get("retriable") is False, "retriable 必须是布尔 False"

    _assert_iso8601(env["time"])


def test_err_output_channel_matches_error(impl):
    """P0 正常：``output.error_code/error_message`` 与 ``error`` 子字典一致。

    llm_tools.py 只把 ``output`` 透给模型；两个通道若不一致，模型看到的东西
    与 run 记录（``error.code``）就会互相打架。
    """
    errors = impl("errors")
    env = errors.err("LIMIT_STEPS", max_steps=256)
    out = env.get("output")

    assert isinstance(out, dict), f"output 必须是 dict，实际 {out!r}"
    assert out.get("error_code") == "LIMIT_STEPS", f"实际 {out.get('error_code')!r}"
    message = out.get("error_message")
    assert isinstance(message, str) and message, "output.error_message 必须是非空 str"
    # 容忍带/不带 "<CODE>: " 前缀，但必须是 error.message 的一部分（同源同文案）
    assert message in _error(env)["message"], \
        f"output.error_message 必须与 error.message 同源，实际 {message!r}"


def test_err_unknown_code_still_envelopes(impl):
    """错误输入：未知码不得崩，仍出 envelope 且保留自身前缀（防御式）。"""
    errors = impl("errors")
    env = errors.err("TOTALLY_UNKNOWN_CODE_X")

    assert _code(env) == "TOTALLY_UNKNOWN_CODE_X"
    assert _error(env)["message"].startswith("TOTALLY_UNKNOWN_CODE_X: ")
    assert env["success"] is False and env["is_error"] is True


# ---------------------------------------------------------------------------
# 2) 宿主判定器等价（冻结副本 + 负向控制）
# ---------------------------------------------------------------------------
def test_frozen_is_envelope_copy_is_falsifiable():
    """冻结副本必须能被证伪，否则它只是恒真桩，等价性毫无意义。"""
    assert is_envelope({"success": False}) is False, "缺 error/time 不得判为 envelope"
    assert is_envelope("not a dict") is False
    assert is_envelope({"success": "no", "error": {}, "time": "t"}) is False


@pytest.mark.parametrize("code", [
    "PATH_UNSAFE", "POLICY_VIOLATION", "LIMIT_STEPS", "PATH_OUTSIDE_ROOTS",
])
def test_envelope_passes_host_is_envelope(impl, code):
    """P0 等价：``is_envelope(err(cm))`` 为 True —— 宿主才认这是结构化错误。"""
    errors = impl("errors")
    env = errors.err(code)

    assert is_envelope(env) is True, \
        f"err({code}) 过不了宿主 is_envelope，错误会被压平成成功：{env!r}"


@pytest.mark.parametrize("key", ["success", "error", "time"])
def test_real_envelope_missing_required_key_becomes_success(impl, key):
    """P0 最脆处（云严 P2#4）：**真实** ``err()`` 输出少一个键，错误就被当成功。

    上面 ``test_envelope_passes_host_is_envelope`` 只证明真实输出能过判定器；
    ``test_frozen_is_envelope_copy_is_falsifiable`` 只证伪**冻结副本**输入。
    这里把两者接起来：拿真实 envelope 逐键删，证明「少一个键」不是无害的形状差异，
    而是语义反转 —— 宿主 ``plugin/runs/trigger_service.py`` 的
    ``_normalize_plugin_response()``（同文件 :457-465）对
    ``is_envelope(v) is False`` 且是 ``Mapping`` 的返回值走 ``ok(data=dict(v))``：
    整个错误被包成**成功**数据透传，``RunError`` 根本不会生成、``error.code`` 丢失。
    （宿主不在本仓库依赖内，故此处只断言判定器结果，宿主行为用本注释钉住依据。）
    """
    errors = impl("errors")
    env = errors.err("POLICY_VIOLATION")
    assert is_envelope(env) is True, "前置条件：完整输出的判定结果必须是 True"

    broken = {name: value for name, value in env.items() if name != key}

    assert key not in broken, f"构造失败：{key} 没被删掉"
    assert len(broken) == len(env) - 1, "只允许少这一个键，不得连带丢别的"
    assert is_envelope(broken) is False, (
        f"缺 {key} 的错误 envelope 仍被判为 envelope —— 判定器口径变了；"
        f"若真如此，宿主会把它当结构化错误，本断言需随宿主契约一起改"
    )


# ---------------------------------------------------------------------------
# 3) details：标量 / <=8 键 / 脱敏
# ---------------------------------------------------------------------------
def test_details_keeps_scalar_fields(impl):
    """P0 正常：插值字段必须进 details（调用方要能机器读 ``max_steps``）。"""
    errors = impl("errors")
    env = errors.safe_err("LIMIT_STEPS", max_steps=256)

    details = _error(env).get("details")
    assert isinstance(details, dict), f"有字段时 details 必须是 dict，实际 {details!r}"
    assert details.get("max_steps") == 256, f"实际 {details!r}"


def test_details_without_fields_is_none(impl):
    """边界：无字段的码 -> ``details is None``（不是 ``{}``，宿主侧形状一致性）。"""
    errors = impl("errors")
    env = errors.safe_err("JOB_NOT_FOUND")

    assert _error(env).get("details") is None, \
        f"无字段时必须 None，实际 {_error(env).get('details')!r}"


def test_details_only_scalars_and_at_most_8_keys(impl):
    """边界：details 只允许标量且 <=8 键（防有人把整段 payload 塞进去）。"""
    errors = impl("errors")
    env = errors.safe_err(
        "POLICY_VIOLATION",
        reason="bad",
        nested={"deep": "value"},
        seq=[1, 2, 3],
        **{f"k{i}": i for i in range(12)},
    )

    details = _error(env).get("details") or {}
    assert isinstance(details, dict)
    assert len(details) <= 8, f"details 键数必须 <=8，实际 {sorted(details)}"
    bad = {
        key: type(value).__name__ for key, value in details.items()
        if not isinstance(value, (str, int, float, bool, type(None)))
    }
    assert not bad, f"details 只允许标量，出现非标量：{bad}"


# 家目录/用户名脱敏：redact_text 无此规则，S17 独立处理，本轮不钉
# （不改写成 xfail：那会让人以为「测过了」。本条已按 team-lead 裁决移出 S16 范围，
#   details 的脱敏口径只钉 redact_text 现有能力 —— 见下一条 hex token 用例。）


# 云严 P2#6 + 已落地的 P1：键名命中凭证词时，值必须整值 ``***``（不是靠值模式兜底）。
# 用**未知码**是为了排除模板插值干扰：未知码文案无占位符，明文只可能经 details 泄露。
CREDENTIAL_DETAIL_KEYS = (
    "control_token", "api_key", "secret", "token",
    "authorization", "cookie", "control_file",
)


def test_details_masks_credential_keys(impl):
    """P0 安全（云严 P2#6）：凭证类键名的值必须 ``***``，且明文不得出现在整个 envelope。"""
    errors = impl("errors")

    for key in CREDENTIAL_DETAIL_KEYS:
        plaintext = f"plain-{key}-value-9f3a"
        env = errors.err("TOTALLY_UNKNOWN_CODE_X", **{key: plaintext})
        details = _error(env).get("details") or {}

        assert details.get(key) == "***", \
            f"details[{key!r}] 必须整值脱敏成 '***'，实际 {details.get(key)!r}"
        dumped = _dump(env)
        assert plaintext not in dumped, f"明文经 details 泄露（key={key}）：{dumped}"


def test_details_skips_none_values(impl):
    """边界（云严 P2#6）：``None`` 值不产生 details 键；全为 None 时 details 为 None。"""
    errors = impl("errors")

    env = errors.err("POLICY_VIOLATION", reason="bad", missing=None)
    details = _error(env).get("details") or {}
    assert "missing" not in details, f"None 值不得造出 details 键：{details!r}"
    assert details.get("reason") == "bad", f"非 None 标量必须保留：{details!r}"

    env_all_none = errors.err("POLICY_VIOLATION", missing=None, also_missing=None)
    assert _error(env_all_none).get("details") is None, \
        f"字段全为 None 时 details 必须是 None，实际 {_error(env_all_none).get('details')!r}"


def test_details_never_leaks_hex_token(impl):
    """P0 安全：details 里的长十六进制凭证必须被 ``***`` 掉。"""
    errors = impl("errors")
    token = "a" * 64

    env = errors.safe_err("POLICY_VIOLATION", reason=f"token={token}", control_token=token)
    dumped = _dump(_error(env).get("details"))

    assert token not in dumped, f"details 泄露 token：{dumped}"


# ---------------------------------------------------------------------------
# 4) error_for() 三分支
# ---------------------------------------------------------------------------
def test_error_for_artcraft_error_keeps_code(impl):
    """正常：``ArtcraftError`` 子类 -> 保留原码，异常对象不进对外文本。"""
    errors = impl("errors")
    env = errors.error_for(errors.PolicyViolation(reason="命令不在白名单"))

    assert _code(env) == "POLICY_VIOLATION"
    assert _error(env)["message"].startswith("POLICY_VIOLATION: ")
    details = _error(env).get("details") or {}
    assert details.get("reason"), f"POLICY_VIOLATION 的 details 应带 reason，实际 {details!r}"


def test_error_for_tool_reply_error_maps_cli_task_failed(impl):
    """正常：宿主工具回包错误 -> ``CLI_TASK_FAILED``，原始 detail 不进 message。"""
    errors = impl("errors")
    env = errors.error_for(errors.ToolReplyError("CLI said: boom"))

    assert _code(env) == "CLI_TASK_FAILED"
    assert "boom" not in _dump(env), \
        "异常详情只能进日志，绝不能进对外 envelope"


def test_error_for_unknown_exception_maps_policy_violation(impl):
    """错误输入：未知异常 -> ``POLICY_VIOLATION``，且异常文本不外泄。"""
    errors = impl("errors")
    env = errors.error_for(RuntimeError("TOP-SECRET-LEAK"))

    assert _code(env) == "POLICY_VIOLATION"
    assert env["success"] is False and env["is_error"] is True
    assert "TOP-SECRET-LEAK" not in _dump(env), \
        f"未知异常文本泄露：{env!r}"


# ---------------------------------------------------------------------------
# 5) 入口层（真实 entry 调用，走既有 stub-SDK 夹具）
# ---------------------------------------------------------------------------
def test_entry_limit_steps_returns_envelope(load_plugin_init, tmp_path, monkeypatch):
    """P0 边界：257 步 -> 不再抛/不再返回 Err，而是 envelope + LIMIT_STEPS，且不 spawn。"""
    module, plugin = _load_plugin(load_plugin_init, tmp_path)
    calls: list = []
    _no_spawn(monkeypatch, calls)

    steps = [{"cmd": "edit.fill"} for _ in range(257)]
    assert len(steps) == 257
    res = _run(plugin.artcraft_ops_run_sequence(
        app="photocraft", steps=steps, out_file="o.png",
    ))

    assert isinstance(res, dict), f"入口失败必须是 envelope dict，实际 {res!r}"
    assert _code(res) == "LIMIT_STEPS", f"实际 error={res.get('error')!r}"
    assert res["error"]["message"].startswith("LIMIT_STEPS: ")
    assert res["success"] is False and res["is_error"] is True
    assert is_envelope(res) is True, "入口返回值必须过宿主 is_envelope"
    assert calls == [], f"超限时不得启动子进程，实际 spawn {calls!r}"


def test_entry_unregistered_command_returns_envelope(load_plugin_init, tmp_path, monkeypatch):
    """P0 错误：未注册命令 -> envelope + POLICY_VIOLATION，且不 spawn。"""
    module, plugin = _load_plugin(load_plugin_init, tmp_path)
    calls: list = []
    _no_spawn(monkeypatch, calls)

    res = _run(plugin.artcraft_ops_run_sequence(
        app="photocraft", steps=[{"cmd": "evil.inject"}], out_file="o.png",
    ))

    assert isinstance(res, dict), f"入口失败必须是 envelope dict，实际 {res!r}"
    assert _code(res) == "POLICY_VIOLATION", f"实际 error={res.get('error')!r}"
    assert is_envelope(res) is True
    assert calls == [], f"命令未命中注册表时不得 spawn，实际 {calls!r}"


def test_entry_out_of_root_path_returns_envelope(load_plugin_init, tmp_path, monkeypatch):
    """P0 安全：越界路径 -> envelope + fail-closed 码（两种都接受），且不 spawn。"""
    module, plugin = _load_plugin(load_plugin_init, tmp_path)
    calls: list = []
    _no_spawn(monkeypatch, calls)

    res = _run(plugin.artcraft_ops_run_sequence(
        app="photocraft",
        steps=[{"cmd": "edit.fill", "params": {"color": "#fff"}}],
        input_file="C:/Windows/win.ini",
        out_file="o.png",
    ))

    assert isinstance(res, dict), f"入口失败必须是 envelope dict，实际 {res!r}"
    assert _code(res) in OUT_OF_ROOT_CODES, \
        f"越界路径必须回 fail-closed 码 {OUT_OF_ROOT_CODES}，实际 {_code(res)!r}"
    assert is_envelope(res) is True
    assert calls == [], f"路径被拒时不得 spawn，实际 {calls!r}"


def test_entry_missing_steps_returns_envelope(load_plugin_init, tmp_path, monkeypatch):
    """错误输入：缺 steps（LLM 少传字段）-> envelope + POLICY_VIOLATION，不抛 TypeError。"""
    module, plugin = _load_plugin(load_plugin_init, tmp_path)
    calls: list = []
    _no_spawn(monkeypatch, calls)

    res = _run(plugin.artcraft_ops_run_sequence())

    assert isinstance(res, dict), f"不得抛异常，必须返回 envelope dict，实际 {res!r}"
    assert _code(res) == "POLICY_VIOLATION", f"实际 error={res.get('error')!r}"
    assert calls == [], f"参数校验失败不得 spawn，实际 {calls!r}"


# ---------------------------------------------------------------------------
# 6) 成功路径不回归（护栏：防有人图省事把成功也套 envelope）
# ---------------------------------------------------------------------------
def test_success_entry_payload_has_no_success_key(load_plugin_init, tmp_path):
    """P0 护栏：成功入口的 payload **不含** ``success`` 键（成功仍走 Ok）。"""
    module, plugin = _load_plugin(load_plugin_init, tmp_path)

    res = asyncio.run(plugin.artcraft_ops_list_apps())

    # 成功必须**仍走 Ok**：不加这条，实现改回裸 dict 也绿（getattr 兜底会掩盖回归）
    assert hasattr(res, "value"), f"成功必须返回 Ok(dict)，实际 {res!r}"
    payload = res.value
    assert isinstance(payload, dict), f"成功应返回 Ok(dict)，实际 {res!r}"
    assert "success" not in payload, \
        f"成功 payload 被套上了 envelope（不要把 Ok 也 envelope 化）：{payload!r}"
    assert "is_error" not in payload, f"成功 payload 不得带 is_error：{payload!r}"
    assert "apps" in payload, f"成功 payload 应保留业务字段，实际 {payload!r}"


def test_success_run_sequence_payload_has_no_success_key(
    load_plugin_init, tmp_path, monkeypatch,
):
    """P0 护栏：主路径 ``run_sequence`` 秒回 queued，payload 亦不得含 ``success``。"""
    module, plugin = _load_plugin(load_plugin_init, tmp_path)
    calls: list = []
    _no_spawn(monkeypatch, calls)

    res = _run(plugin.artcraft_ops_run_sequence(
        app="photocraft",
        steps=[{"cmd": "edit.fill", "params": {"color": "#fff"}}],
        out_file="out.png", inspect=True,
    ))
    assert hasattr(res, "value"), f"成功必须返回 Ok(dict)，实际 {res!r}"
    payload = res.value
    assert isinstance(payload, dict), f"成功应返回 Ok(dict)，实际 {res!r}"
    assert "success" not in payload, f"成功 payload 被套上了 envelope：{payload!r}"
    assert payload.get("status") == "queued", f"实际 {payload!r}"


# ---------------------------------------------------------------------------
# 7) i18n 模板静态护栏：错误文案模板里**永不**出现凭证类占位符
# ---------------------------------------------------------------------------
#: 凭证类占位符（模板里出现即等于「把凭证渲染进 error.message」）。
CREDENTIAL_PLACEHOLDER_RE = re.compile(
    r"\{\s*"
    r"(?:access[_-]?token|refresh[_-]?token|control[_-]?token|token"
    r"|api[_-]?key|apikey|secret|client[_-]?secret"
    r"|authorization|cookie|password|passwd|credential"
    r"|control[_-]?file|instance[_-]?json|bearer)"
    r"\s*[!:}]",
    re.IGNORECASE,
)


def test_i18n_templates_have_no_credential_placeholders(impl):
    """P0 护栏：19 条错误文案模板里不得出现凭证类占位符（防未来静默泄露）。

    为什么必须静态钉住：``error.message`` 走 ``f"{code}: {redact_text(t(code, **kwargs))}"``
    —— 模板**先渲染、后脱敏**。值模式兜底只认 32+ 位十六进制 / ``method:auth`` /
    ``--control-token-file`` / ``bearer|basic``，**短 token 完全抓不到**
    （``{token}`` 渲染出 8 位明文就照原样进 message 与 output.error_message，
    进而进模型上下文与 run 记录）。
    今天 19 条模板干净（占位符只有 app / path / query / reason / max_steps /
    seconds / stderr / name），但没人钉住的话，加一个 ``{token}`` 不会有任何测试变红。
    """
    i18n = impl("i18n")
    templates = i18n.ERROR_CODES

    assert len(templates) == 19, f"§8 的错误码表应为 19 条，实际 {len(templates)}"

    hits: list[str] = []
    for code, texts in templates.items():
        for lang in ("zh", "en"):
            template = texts.get(lang)
            if not isinstance(template, str):
                continue
            for match in CREDENTIAL_PLACEHOLDER_RE.finditer(template):
                hits.append(f"{code}.{lang}: {match.group(0)!r}")

    assert not hits, (
        "错误文案模板里出现凭证类占位符（渲染后 redact_text 抓不到短值，会静默泄露）：\n"
        + "\n".join(hits)
    )
