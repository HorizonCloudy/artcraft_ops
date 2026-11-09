"""§10 #1 — locator 安装探测 / CLI 绝对路径 / 版本 token。

覆盖：正常路径（绝对路径解析、instance.json、env 继承）
      + 边界（--version 失败回退 sha256 前 12 位）
      + 错误输入（路径不存在、未知 app、坏 JSON）。

用 tmp_path 造假的 CLI 文件，不依赖用户本机是否真的装了 PhotoCraft，
更不启动任何 exe。真实路径/端口结构来自 RESULTS §1 / §2.6。
"""
from __future__ import annotations

import asyncio
import builtins
import hashlib
import io
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from _fixtures import INSTANCE_JSON_SAMPLE, KNOWN_PATHS

# instance.json 的凭证只在本文件内当「不许进日志」的哨兵，绝不传给实现代码。
_INSTANCE_TOKEN = json.loads(INSTANCE_JSON_SAMPLE)["token"]


def _detail(exc: BaseException) -> str:
    return f"{exc} {type(exc).__name__}"


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
def test_locator_resolves_absolute_cli_path(impl, tmp_path):
    """#1 正常：resolve_cli 返回绝对路径且该文件确实存在。"""
    locator = impl("locator")
    fake_cli = tmp_path / "photocraft-cli.exe"
    fake_cli.write_bytes(b"MZ-stub-exe")

    got = locator.resolve_cli("photocraft", {"photocraft": str(fake_cli)})

    assert got == str(fake_cli)
    assert os.path.isabs(got)
    assert os.path.isfile(got)


def test_locator_known_paths_are_absolute(impl):
    """正常：§3 三个 app 的已知 CLI 路径都是绝对路径字符串。"""
    assert all(os.path.isabs(p) for p in KNOWN_PATHS.values())
    assert KNOWN_PATHS["photocraft"].endswith("photocraft-cli.exe")
    assert KNOWN_PATHS["vectorcraft"].endswith("vectorcraft-cli.exe")
    assert KNOWN_PATHS["craft_anything"].endswith("craft-anything-cli.exe")


def test_build_cli_env_inherits_os_environ(impl):
    """正常：完整继承 os.environ，返回独立副本（改副本不影响进程环境）。"""
    locator = impl("locator")
    env = locator.build_cli_env()

    assert isinstance(env, dict)
    assert env["PATH"] == os.environ["PATH"]
    env["__ARTCRAFT_OPS_TEST_ONLY__"] = "mutated"
    assert "__ARTCRAFT_OPS_TEST_ONLY__" not in os.environ


def test_read_instance_json_returns_structure(impl, tmp_path):
    """正常：instance.json 的 {pid,port,token:<64hex>} 结构被正确读出。"""
    locator = impl("locator")
    p = tmp_path / "instance.json"
    p.write_text(INSTANCE_JSON_SAMPLE, encoding="utf-8")

    data = locator.read_instance_json(str(p))

    assert data["pid"] == 11664
    assert data["port"] == 52903
    assert data["token"] == "a" * 64
    assert len(data["token"]) == 64


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_detect_version_falls_back_to_sha256(impl, tmp_path, monkeypatch):
    """边界/§1 [INFERENCE]：`--version` 未验证 -> 探测失败必须回退 exe sha256 前 12 位，不抛。"""
    locator = impl("locator")
    spec = impl("app_registry").get_spec("photocraft")
    fake_cli = tmp_path / "photocraft-cli.exe"
    payload = b"MZ-stub-for-version-token"
    fake_cli.write_bytes(payload)

    async def failing_exec(*args, **kwargs):
        raise FileNotFoundError("--version unsupported")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec, raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", failing_exec, raising=False)

    got = asyncio.run(locator.detect_version(str(fake_cli), spec))

    expected = hashlib.sha256(payload).hexdigest()[:12]
    assert isinstance(got, str) and got, "回退必须返回非空 version_token，不得抛异常"
    assert got == expected, "回退 token 必须是 exe 文件 sha256 前 12 位"
    assert len(got) == 12


# ---------------------------------------------------------------------------
# 错误输入
# ---------------------------------------------------------------------------
def test_locator_missing_path_raises(impl, tmp_path):
    """错误：known_paths 指向不存在的文件 -> APP_NOT_FOUND，不得返回该路径。"""
    locator = impl("locator")
    missing = str(tmp_path / "gone" / "cli.exe")
    with pytest.raises(Exception) as ei:
        locator.resolve_cli("photocraft", {"photocraft": missing})
    assert "APP_NOT_FOUND" in _detail(ei.value) or "AppNotFound" in _detail(ei.value), \
        _detail(ei.value)


def test_locator_unknown_app_raises(impl):
    """错误：app_id 不在 known_paths -> APP_NOT_FOUND。"""
    locator = impl("locator")
    with pytest.raises(Exception) as ei:
        locator.resolve_cli("no_such_app_9999", {})
    assert "APP_NOT_FOUND" in _detail(ei.value) or "AppNotFound" in _detail(ei.value), \
        _detail(ei.value)


def test_read_instance_json_malformed_raises(impl, tmp_path):
    """错误：坏 JSON 必须拒绝（#9777 token 文件不可静默放过一个畸形文件）。"""
    locator = impl("locator")
    p = tmp_path / "instance.json"
    p.write_text("{broken json", encoding="utf-8")
    with pytest.raises(Exception) as ei:
        locator.read_instance_json(str(p))
    assert ("JSON" in _detail(ei.value)
            or isinstance(ei.value, (ValueError, json.JSONDecodeError))), _detail(ei.value)


# ---------------------------------------------------------------------------
# C5 / 可观测性（非行为变更）：locator.py 三处裸 ``except Exception`` 全吞
#
# [FACT] 三处位置（当前 locator.py 行号）：
#   126-131 long_path 的 GetLongPathNameW、210-216 --version 探针、
#   221-226 sha256 回退（失败直接返回 "unknown"）。该文件已有
#   ``import logging`` / ``import traceback``，模块级 ``logger`` 在 :42。
# [背景] ``test_detect_version_falls_back_to_sha256`` 曾 flaky 一次，报
#   ``assert 'unknown' == 'ca0abd46f7ab'`` —— sha256 回退抛了异常却被裸 except
#   吞掉，**根因至今不可见**。C5 的目的就是让这类失败可见。
#
# 契约（最终写法，errors.py:8 铁律 3）：三处统一
#   ``logger.debug("<上下文> (%s): %s", type(exc).__name__,``
#                  ``redact_text(traceback.format_exc()))``
#   * 完整栈帧整串过脱敏后作预格式化 ``%s`` 参数，帧级信息保留
#     （``runner.blocking`` 的线程池调度 / ``open(`` / ``fh.read`` 可区分）；
#   * **禁** ``logger.exception`` / ``exc_info=True`` / f-string：异常 message
#     原文会整段进日志，把 64hex 凭证原样带出（见下方主动泄密探测用例）；
#   * ``redact_text`` 是第二层兜底，只覆盖 64hex 等已列形态，**不可外推为
#     「日志不泄密」的一般结论**（spec §19.9 口径）；
#   * 不改变任何返回行为：detect_version 仍绝不抛异常、绝不阻塞发现流程；
#   * 日志不得泄露 read_instance_json 里的 token（凭证只入内存）；
#   * 断言用 caplog 查「DEBUG 记录存在 + 能定位到异常类型」，
#     **不写死日志消息文本**（易碎），也不写死 logger 名。
#
# [未核验] 那次 flaky 的具体触发源本轮没有复现，不做猜测；只在报告里标注。
# ---------------------------------------------------------------------------
def _debug_records_naming(caplog, exc_type):
    """筛出「DEBUG 级 + 能定位到 ``exc_type`` 类型」的日志记录。

    刻意不写死日志消息文本，只认「异常类名出现在 record 里」这一结构特征，
    实现方改措辞不会把测试打红。

    [S15 订正] 下面 ``rec.exc_info`` 分支**只是兼容性兜底，不是推荐形态**。
    ``locator.py:36`` 明确禁止 ``exc_info`` / ``logger.exception``：那会把
    **未脱敏**的原始 traceback 挂到 record 上，绕过 ``redact_text``。
    生产实现必须用「预格式化参数把类名写进消息」，即走第二个分支。
    不要照这条兜底分支去改实现。
    """
    found = []
    for rec in caplog.records:
        if rec.levelno != logging.DEBUG:
            continue
        if rec.exc_info and rec.exc_info[0] is exc_type:
            found.append(rec)
            continue
        parts = (rec.getMessage(), str(rec.args or ""))
        if any(exc_type.__name__ in part for part in parts):
            found.append(rec)
    return found


# ---------------------------------------------------------------------------
# S12 / 帧级区分断言（补 C5 缺口）
#
# [FACT] C5 既有断言只验两件事：``_debug_records_naming``（能定位异常类型）+
# ``secret not in caplog.text``（64hex 已脱敏）。实现若退化成「只打一行
# ``RuntimeError``、丢掉整个栈帧」，这两条仍会全绿 —— 而 ``runner.blocking``
# 的三种失败面（线程池调度 / ``open(`` / ``fh.read``）就再也无法区分，
# 那次 ``test_detect_version_falls_back_to_sha256`` flaky 的根因会重新不可见。
#
# 判据只认**结构性特征**（traceback 帧行标记 / 源码文件名 / 行号 / 调用函数名），
# 不写死日志措辞、不写死换行数或行数、不钉死任何具体函数名常量（如 ``to_thread``）。
# 因此既接受当前多行 traceback 形态，也接受把帧信息换成别的单行表达的实现；
# 但会拒绝「只有一行 ``RuntimeError``」的退化形态。
#
# [S12-b 剩余边界，主理人裁决本轮不扩范围] 用上面 5 个正则逐条实测命中情况：
#   全丢(只一行类名)  命中 []          任一命中=否  -> 断言红（本轮钉住的退化）
#   只留最外一帧      命中 [1,3,4,5]   任一命中=是  -> 断言仍绿
#   压平成单行        命中 [1,3,4,5]   任一命中=是  -> 断言仍绿
#   完整栈帧          命中 [1,3,4,5]   任一命中=是  -> 断言绿
# 即「只留最外一帧 / 压平成单行」仍能过断言，但它们丢掉深层帧
# （``to_thread`` / ``open(`` / ``fh.read``），三种失败面重新不可区分。
# 不扩范围理由：补强需验「>=2 帧行」或「深层帧函数名」，前者帧数依赖注入
# 方式（实测三条路径为 2 / 2 / 4），后者与本判据「不钉死实现形态」的契约冲突。
# **本断言只验「至少一个帧定位信息」，不外推为三失败面逐一可区分。**
# ---------------------------------------------------------------------------
_FRAME_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r'File "'),                   # 栈帧行标记（CPython traceback 标准形态）
    re.compile(r"File '"),                   # 同上，单引号变体
    re.compile(r"\bline\s+\d+", re.I),      # 行号：line 124 / at line 124
    re.compile(r"\b[A-Za-z_]\w*\.py\b"),    # 源码文件名：locator.py / runner.py
    re.compile(r",\s+in\s+[A-Za-z_]\w*"),  # 调用函数名：, in long_path
)


def _debug_records_with_frame(caplog, exc_type):
    """在**真实 DEBUG 记录**上筛出「能定位异常类型 + 含至少一个栈帧定位信息」的记录。

    复用 ``_debug_records_naming``（无需改动它）先按运行态类名收窄，再用
    ``rec.getMessage()`` —— 它会把预格式化的 ``%s`` 参数拼进消息，因此完整
    traceback 在运行态可见 —— 验「可定位到调用位置」这一性质。只看模块里有没有
    ``traceback`` import 属于静态形状，不构成运行态证据，这里不采用。
    """
    found = []
    for rec in _debug_records_naming(caplog, exc_type):
        msg = rec.getMessage()
        if any(pattern.search(msg) for pattern in _FRAME_MARKERS):
            found.append(rec)
    return found


@pytest.mark.skipif(
    os.name != "nt",
    reason="GetLongPathNameW 分支仅在 os.name == 'nt' 时进入",
)
def test_locator_long_path_win32_failure_is_logged(impl, monkeypatch, caplog):
    """C5 #1（locator.py:113）：GetLongPathNameW 失败不再被裸 except 全吞。

    单独钉住 long_path 那处，并顺带钉住「返回行为不变」——失败时仍返回归一
    后的路径、不抛异常。用 monkeypatch 注入一个会抛的 ctypes.windll，
    不依赖本机是否真的装了 Windows SDK / 目标文件是否存在。
    """
    import ctypes

    locator = impl("locator")

    class _ExplodingKernel32:
        @property
        def GetLongPathNameW(self):
            raise RuntimeError("GetLongPathNameW unavailable (test stub)")

    class _ExplodingWinDLL:
        @property
        def kernel32(self):
            return _ExplodingKernel32()

    monkeypatch.setattr(ctypes, "windll", _ExplodingWinDLL(), raising=False)
    # 自检：确认注入真的生效，避免把「测试自身没触发异常」误报成「实现没补日志」
    with pytest.raises(RuntimeError):
        _ = ctypes.windll.kernel32.GetLongPathNameW

    caplog.set_level(logging.DEBUG)
    got = locator.long_path(r"C:\state\artcraft_ops\data\ok.png")

    assert isinstance(got, str) and got.endswith("ok.png"), \
        f"long_path 失败路径不得改变返回行为，实际 {got!r}"
    assert _debug_records_naming(caplog, RuntimeError), \
        "GetLongPathNameW 失败时没有产生能定位 RuntimeError 的 DEBUG 记录"
    assert _INSTANCE_TOKEN not in caplog.text, \
        "C5 日志泄露了 read_instance_json 的 token"


def test_detect_version_probe_failure_is_logged(impl, tmp_path, monkeypatch, caplog):
    """C5 #2（locator.py:193）：--version 探针失败必须留下可观测 DEBUG 记录。

    返回行为不变：探针失败后仍回退到 exe 的 sha256 前 12 位，绝不抛异常。
    ``create_subprocess_exec`` / ``create_subprocess_shell`` 全被替身替换，
    不 spawn 任何进程；fake_cli 沿用本文件既有 tmp_path 假文件惯例。
    """
    locator = impl("locator")
    spec = impl("app_registry").get_spec("photocraft")
    fake_cli = tmp_path / "photocraft-cli.exe"
    payload = b"MZ-stub-for-probe-logging"
    fake_cli.write_bytes(payload)

    async def failing_exec(*args, **kwargs):
        raise RuntimeError("--version probe exploded")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec, raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", failing_exec, raising=False)

    caplog.set_level(logging.DEBUG)
    got = asyncio.run(locator.detect_version(str(fake_cli), spec))

    assert got == hashlib.sha256(payload).hexdigest()[:12], \
        f"探针失败后仍须回退 sha256，实际 {got!r}"
    assert _debug_records_naming(caplog, RuntimeError), \
        "--version 探针失败时没有产生能定位 RuntimeError 的 DEBUG 记录"
    assert _INSTANCE_TOKEN not in caplog.text, \
        "C5 日志泄露了 read_instance_json 的 token"


def test_detect_version_sha256_fallback_failure_is_logged(
    impl, tmp_path, monkeypatch, caplog,
):
    """C5 #3（locator.py:199）：sha256 回退失败必须留下可观测 DEBUG 记录。

    用**两种不同**异常类型分别注入探针与回退，断言两处 except 各自都留下了
    能定位异常类型的 DEBUG 记录 —— 这正是那次 flaky（``'unknown' ==
    'ca0abd46f7ab'``）当时看不到的东西。返回行为不变：回退失败仍返回
    ``"unknown"``，绝不抛异常、绝不阻塞发现流程。
    """
    locator = impl("locator")
    runner = impl("runner")
    spec = impl("app_registry").get_spec("photocraft")
    fake_cli = tmp_path / "photocraft-cli.exe"
    fake_cli.write_bytes(b"MZ-stub-for-fallback-logging")

    async def failing_exec(*args, **kwargs):
        raise RuntimeError("--version probe down")

    async def failing_blocking(fn, *args):
        raise ValueError("sha256 fallback exploded")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec, raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", failing_exec, raising=False)
    monkeypatch.setattr(runner, "blocking", failing_blocking, raising=False)

    caplog.set_level(logging.DEBUG)
    got = asyncio.run(locator.detect_version(str(fake_cli), spec))

    assert got == "unknown", \
        f"sha256 回退失败仍须返回 'unknown'，实际 {got!r}"
    assert _debug_records_naming(caplog, RuntimeError), \
        "--version 探针的 except 没有产生能定位 RuntimeError 的 DEBUG 记录"
    assert _debug_records_naming(caplog, ValueError), \
        "sha256 回退的 except 没有产生能定位 ValueError 的 DEBUG 记录"
    assert _INSTANCE_TOKEN not in caplog.text, \
        "C5 日志泄露了 read_instance_json 的 token"


def test_locator_debug_logs_redact_credentials_from_exception(
    impl, tmp_path, monkeypatch, caplog,
):
    r"""必修 3：进 logger 的异常详情必须先 ``redact_text()``，不得泄露凭证原文。

    [背景] ``errors.py`` 铁律 3：进 logger 的值先 ``redact_text()``，只用
    预格式化参数，禁 ``logger.exception`` / ``exc_info=True`` —— 后者会把
    ``exc`` 原文（含凭证）整段写进 traceback。云严实测
    ``ValueError("bad token <64hex>")`` 会被原样打出。裁决写法（C5 最终版，
    为保留 ``to_thread`` / ``open(`` / ``fh.read`` 三帧区分能力）::

        logger.debug("<上下文> (%s): %s",
                     type(exc).__name__,
                     redact_text(traceback.format_exc()))

    完整栈帧整串过脱敏后作预格式化 ``%s`` 参数，帧级区分原样保留。

    本条是**主动泄密探测**，补上原报告诚实标注的缺口：此前
    ``_INSTANCE_TOKEN not in caplog.text`` 只是护栏（三处 except 不接触
    instance.json，天然绿）。这里把 64hex 主动塞进注入异常的 message，
    断言 ``caplog.text`` **不含该 hex 原文**；同时断言仍能定位到异常类型，
    证明脱敏没有把可观测性一起抹掉。不写死日志消息文本。

    [范围限定，云严 / spec §19.9] 本条只验 **64hex 形态**被 ``redact_text``
    兜底；``redact_text`` 是第二层兜底，不覆盖任意凭证形态，**不可外推为
    「日志不泄密」的一般结论**。

    [FACT 本轮实测] 同一组测试只改生产代码的日志写法：``exc_info=True`` 时
    本条红（完整 traceback 原样带出 64hex）；改成
    ``redact_text(traceback.format_exc())`` 后转绿（脱敏尾部
    ``...bad token ***``，多行栈帧仍保留）。新写法下 traceback 确实会带出
    异常 message，所以这条探测正打在真风险上。
    """
    locator = impl("locator")
    spec = impl("app_registry").get_spec("photocraft")
    fake_cli = tmp_path / "photocraft-cli.exe"
    payload = b"MZ-stub-for-redaction-probe"
    fake_cli.write_bytes(payload)

    # 哨兵取自 _fixtures.INSTANCE_JSON_SAMPLE 的 token（64hex），塞进异常 message
    secret = _INSTANCE_TOKEN
    assert len(secret) == 64

    async def failing_exec(*args, **kwargs):
        raise RuntimeError("probe failed: bad token " + secret)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec, raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", failing_exec, raising=False)

    caplog.set_level(logging.DEBUG)
    got = asyncio.run(locator.detect_version(str(fake_cli), spec))

    assert got == hashlib.sha256(payload).hexdigest()[:12], \
        f"探针失败后仍须回退 sha256，实际 {got!r}"
    assert secret not in caplog.text, \
        "C5 日志把异常 message 里的 64hex 凭证原样打出了（需 redact_text）"
    assert _debug_records_naming(caplog, RuntimeError), \
        "脱敏后仍须能定位到 RuntimeError（可观测性不许被一起抹掉）"


# ---------------------------------------------------------------------------
# S12 / 三处降级路径的帧级区分断言
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "downgrade_path",
    [
        pytest.param(
            "long_path",
            marks=pytest.mark.skipif(
                os.name != "nt",
                reason="GetLongPathNameW 分支仅在 os.name == 'nt' 时进入",
            ),
        ),
        "version_probe",
        "sha256_fallback",
    ],
)
def test_c5_debug_log_records_frame_level_location(
    impl, tmp_path, monkeypatch, caplog, downgrade_path,
):
    """S12：三处降级路径的 DEBUG 日志必须保留**帧级定位信息**，不能只留类名。

    [FACT] C5 既有断言只验「类名可定位 + 64hex 脱敏」；实现若退化成「只打一行
    ``RuntimeError``、丢掉整个栈帧」仍会全绿，而 ``runner.blocking`` 的三种
    失败面（线程池调度 / ``open(`` / ``fh.read``）就再也无法区分 —— 那次
    ``test_detect_version_falls_back_to_sha256`` flaky 的根因会重新不可见。

    本例用同一个 helper 覆盖 long_path / --version 探针 / sha256 回退三处，断言
    落在真实 DEBUG 记录（``rec.getMessage()``）上，只认结构性特征，不写死日志
    措辞、行数、换行数，也不钉死任何具体函数名常量。第一条断言是**注入锚点**：
    确认降级路径真的触发了 DEBUG 记录且仍能定位异常类型（即旧断言的能力）；
    第二条才是 S12 新增的帧级断言 —— 变异下第一条仍绿、第二条转红，缺口可见。

    [S12-b 范围限定] 本例只验「至少一个帧定位信息」；「只留最外一帧 / 压平
    成单行」仍会放行，**不外推为三失败面逐一可区分**。详见上方 S12 注释块。
    """
    locator = impl("locator")

    if downgrade_path == "long_path":
        import ctypes

        class _ExplodingKernel32:
            @property
            def GetLongPathNameW(self):
                raise RuntimeError("GetLongPathNameW unavailable (S12 stub)")

        class _ExplodingWinDLL:
            @property
            def kernel32(self):
                return _ExplodingKernel32()

        monkeypatch.setattr(ctypes, "windll", _ExplodingWinDLL(), raising=False)
        caplog.set_level(logging.DEBUG)
        locator.long_path(r"C:\state\artcraft_ops\data\ok.png")
        exc_type = RuntimeError

    elif downgrade_path == "version_probe":
        spec = impl("app_registry").get_spec("photocraft")
        fake_cli = tmp_path / "photocraft-cli.exe"
        fake_cli.write_bytes(b"MZ-stub-for-s12-probe")

        async def failing_exec(*args, **kwargs):
            raise RuntimeError("--version probe exploded (S12)")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec, raising=False)
        monkeypatch.setattr(asyncio, "create_subprocess_shell", failing_exec, raising=False)
        caplog.set_level(logging.DEBUG)
        asyncio.run(locator.detect_version(str(fake_cli), spec))
        exc_type = RuntimeError

    else:  # sha256_fallback
        spec = impl("app_registry").get_spec("photocraft")
        runner = impl("runner")
        fake_cli = tmp_path / "photocraft-cli.exe"
        fake_cli.write_bytes(b"MZ-stub-for-s12-fallback")

        async def failing_exec(*args, **kwargs):
            raise RuntimeError("--version probe down (S12)")

        async def failing_blocking(fn, *args):
            raise ValueError("sha256 fallback exploded (S12)")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec, raising=False)
        monkeypatch.setattr(asyncio, "create_subprocess_shell", failing_exec, raising=False)
        monkeypatch.setattr(runner, "blocking", failing_blocking, raising=False)
        caplog.set_level(logging.DEBUG)
        asyncio.run(locator.detect_version(str(fake_cli), spec))
        exc_type = ValueError

    # ① 注入锚点：降级真的发生、且仍有能定位异常类型的 DEBUG 记录（旧断言的能力）。
    #    变异下这条仍绿 —— 正是「旧断言没牙」的证据。
    assert _debug_records_naming(caplog, exc_type), (
        f"[锚点] {downgrade_path}: 没有产生能定位 {exc_type.__name__} 的 DEBUG 记录，"
        "注入未生效，下面的帧级断言无意义"
    )
    # ② S12 新增：同一条记录里还必须有至少一个「可定位到调用位置」的栈帧信息。
    assert _debug_records_with_frame(caplog, exc_type), (
        f"S12: {downgrade_path} 的 DEBUG 日志退化成「只打一行类名、丢掉整个栈帧」；"
        "必须保留至少一个栈帧定位信息（帧行标记 / 源码文件名 / 行号 / 调用函数名），"
        "否则 runner.blocking 的三种失败面（线程池调度 / open( / fh.read）无法区分"
    )


# ===========================================================================
# S12-b / sha256 回退分支内部「跨失败面」帧签名区分
#
# 堵 S12 遗留的两条边界（上方 S12 注释块已如实标注「不外推为三失败面逐一可区分」）：
#
#   边界 1 `first_frame_only`：实现若退化成「只保留最外一帧、丢掉内层帧」，
#     S12 的 5 个标记仍命中 → 仍绿。
#   边界 2 伪帧假绿：S12 后 3 个标记（`\.py` / `line \d+` / `, in fn`）是弱标记，
#     一行自然语言「见 locator.py 第 12 行」就能独立满足 → 假绿。
#
# [FACT 为什么不跨三条降级路径比] 三条路径的 except 块分别在 locator.py 的
#   127 / 212 / 222 行：只留最外一帧后签名依然互不相同（各是那三行之一），
#   集合大小仍是 3 —— 跨路径比较**挡不住** first_frame_only。
#   ⇒ 判据必须锚在**同一个 except 块内部**的多种失败面。
#
# [FACT sha256 回退分支内部存在三种失败面] `_sha256_12` 经 `runner.blocking`
#   (= `asyncio.to_thread`) 调度，失败可发生在：
#     ① 线程池调度层（executor 已 shutdown → submit 抛错）
#     ② 文件打开层（`open(path, "rb")`）
#     ③ 读取层（`fh.read`）
#   三者完整帧签名互不相同；退化成「只留最外一帧」后三者塌缩成同一个。
#
# 判据（抽什么 / 比什么 / 阈值为什么这么定）：
#   * 抽：从该失败面的 DEBUG 记录文本（`rec.getMessage()`，展开 %s 参数）里，
#     用**结构化帧头** `File "<path>", line <N>` 抽出有序签名
#     `((basename, lineno), ...)`；只取 basename，跨机器可比。
#   * **剔除测试套件的帧**（`_production_frames`，按 `tests/` 递归下全部 `.py`
#     的 basename 集合判）：注入桩（`_guarded_open` / 假 `fh`）也出现在栈里。
#     不剔除的话，注入桩会给 `open` / `read` 一份**免费的「每面唯一性」**——
#     只要留下任意一个桩帧，判据就绿，而真正的失败行 `locator.py:181` / `:182`
#     早已丢失。剔**整个套件**而不只是本文件：桩搬到别的测试模块时，
#     只剔本文件会重新假绿（`outer_inner` / `runner_plus_inner` 就是这种形态）。
#     [FACT] 这也正是 `runner_plus_inner`（保留 `runner.py` 帧 + 最内帧）能绕过
#     「每面必须含 runner.py 帧」那条老判据的根因。剔除后签名只描述**生产栈**。
#   * **② 生产线深度**：对**每个**失败面，`len(set(生产帧)) >= 2`。
#     语义是「调用链至少跨过两层生产帧」，即异常不是只从某一层原地冒出、
#     也不是被截成单帧。
#   * **③ 跨面差异**：三种失败面的生产帧签名两两**不相等**。
#
# [为什么 ② 的「>= 2」不是被禁的那种「绝对帧数阈值」]
#   被禁的是「在完整文本里数 `File "` 出现次数 / 数物理行数」——那种阈值随
#   注入方式变（直接 `await runner.blocking(...)` 与经 `detect_version` 驱动，
#   最外帧数量不同）。`>= 2 个**不重复的生产**帧` 不同：
#     - 测试帧已剔除，注入方式、桩放在哪一行都不影响计数；
#     - [FACT] 两种驱动方式下都远超 2：经 `detect_version` 驱动三面各 5 个生产帧
#       （`read` 的重复 `locator.py:182` 去重后算 1 个）；直接 `await blocking(...)`
#       驱动各 4 个 —— 余量充足，不是脆弱常量。
#   语义是结构性的「至少跨两层生产栈」，不是绝对帧数。
#
# [实测覆盖的退化形态，7 种对撞（剔除测试帧后的生产帧数）]
#   形态                 schedule / open / read     ②深度  ③差异  现判据
#   full（当前实现）      5 / 5 / 5                  过     过     绿 ✅
#   first1  只留最外      1 / 1 / 1                  红     红     红 ✅
#   inner1  只留最内      1 / 0 / 0                  红     红     红 ✅
#   outer_inner 首尾     2 / 1 / 1                  红     红     红 ✅
#   runner_plus_inner    2 / 1 / 1                  红     红     红 ✅
#   prod_last_only       1 / 1 / 1                  红     红     红 ✅
#   pseudo  伪帧文本      0 / 0 / 0                  红     红     红 ✅
#   （`inner1` / `outer_inner` / `runner_plus_inner` / `prod_last_only` 四种的
#     「旧判据绿」是主理人独立实测结论；成因是当时签名未剔除注入桩帧，
#     由测试自身行号提供区分力 —— 本版剔除此依赖，并把「深度 >= 2」补上。）
#
# [形态依赖声明，必须明写] 本判据锚在 `traceback.format_exc()` 的
#   `File "<path>", line <N>` **文本结构**上。若将来实现改用非 traceback 的
#   结构化日志（例如 `frame=locator.py:181 fn=open`），本条会红 —— 那是**日志
#   形态变更、不是退化**，需人工确认后调整判据，不要直接判定实现坏了。
#
# [契约内有意放行，防后来者误当漏洞] 「把多帧压进一行」——即 `File "` 在物理
#   单行里出现多次（日志系统把换行吃掉、或实现主动 flatten）——**本判据不会打红**：
#   帧信息一个没少，生产帧数与签名都不变。这属于契约内合法形态，
#   **不要**为了堵它去加「换行数」「物理行数」之类的阈值。
#   （注意区分：`>= 2 个生产帧` 约束的是**帧的个数**，不是**物理行数**；
#     压平只改后者，不影响前者 —— 已实测 `flatten` 形态 3 passed。）
#
# [范围限定] 本判据只钉「sha256 回退分支内部的失败面可区分」。它**不**声称
#   long_path / --version 探针两处也有多种失败面，也**不**外推为「任意深层故障
#   都可定位」。`redact_text` 对帧文本的脱敏保真度同样不在本轮范围内。
# ===========================================================================

# CPython traceback 的标准帧头。单引号变体一并接受（部分格式化器如此输出）。
# 刻意**不用** S12 的 5 个宽松标记：其中 `\bline\s+\d+` / `\.py` / `, in fn`
# 都能被一行自然语言伪帧文本独立满足，会让「实现根本没打栈帧」假绿通过。
_FRAME_HEADER_RE = re.compile(r'File\s+["\']([^"\']+)["\'],\s*line\s+(\d+)')

# sha256 回退分支内部的三种失败面。名字只描述「注入在哪一层失败」，
# 不含任何实现侧函数名/行号常量。
_SHA256_FAILURE_SURFACES: tuple[str, ...] = ("executor_schedule", "open", "read")

# 每种失败面注入后真实抛出的异常类型。三者两两不同名，且都与下面用于让
# `--version` 探针失败的 FileNotFoundError 不同名 —— 这样按类型名收窄
# DEBUG 记录时，不会把探针那一条也算进来（探针记录与本失败面记录同在 caplog）。
_SHA256_SURFACE_EXC: dict[str, type[BaseException]] = {
    "executor_schedule": RuntimeError,
    "open": PermissionError,
    "read": OSError,
}


# 测试套件的全部模块 basename（`tests/` 目录下所有 .py，递归）：
# 注入桩帧一律从生产帧里剔除，避免「判据在测自己」。
#
# 为什么剔**整个套件**而不只是本文件：桩一旦被搬到别的测试模块（`tests/_s12b_inject.py`
# 或 `tests/s12b/xxx.py`），只剔本文件就会让它重新变成「免费的每面唯一性」——
# `outer_inner` / `runner_plus_inner` 两种形态会因此假绿。
# 集合里包含 `conftest.py` / `_fixtures.py` / 其他 `test_*.py` 是**正确的**：
# 它们都是测试代码，都不属于生产栈。
#
# [A3 构建时机与前提] 在模块 import 时用 `__file__` 推出的绝对目录扫一次，
# 不依赖 cwd / pytest rootdir。`os.walk` 若因权限/IO 异常返回空集，
# 剔除会失效并**静默假绿**，所以拆成两层：
#   * `_TEST_MODULE_WALK_OK` 记录扫描是否真的拿到了文件；
#   * 无论如何都把本文件 basename 并进集合（集合恒非空，桩帧至少在本文件场景仍被剔）。
#
# [A3-b 按 basename 剔除的固有弱点与显式防护]
# `_production_frames` 收到的是 `(basename, lineno)` —— 路径在 `_frame_signature`
# 就丢了。所以若 `tests/` 下存在与生产模块**同名**的 .py（典型将来风险：
# 给 tests 加 `__init__.py`，而 `artcraft_ops/__init__.py` 是生产模块），
# 生产栈里的同名帧会被**误剔** → 生产线深度少算 → 假红。
# 当前 `tests/` 无同名文件（实测），但这是「将来没人放同名文件」的假设，
# 不能只写在注释里，所以下面把**前提钉成运行期检查**（`_COLLIDING_MODULE_BASENAMES`）。
# 生产模块名**从 `artcraft_ops/*.py` 实际文件实时推导**，不手写清单 ——
# 将来新增生产模块会自动纳入检查，不会漏。
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.dirname(_TESTS_DIR)
_SELF_TEST_BASENAME = os.path.basename(__file__)
_SCANNED_TEST_MODULES = frozenset(
    name
    for _root, _dirs, _files in os.walk(_TESTS_DIR)
    for name in _files
    if name.endswith(".py")
)
_PRODUCTION_MODULE_BASENAMES = frozenset(
    name for name in os.listdir(_PLUGIN_ROOT) if name.endswith(".py")
) if os.path.isdir(_PLUGIN_ROOT) else frozenset()
_TEST_MODULE_WALK_OK = bool(_SCANNED_TEST_MODULES)
_TEST_MODULE_BASENAMES = _SCANNED_TEST_MODULES | {_SELF_TEST_BASENAME}
# 非空 = 剔除策略会误剔生产帧，前提被破坏，必须改剔除策略（例如让
# `_frame_signature` 保留相对路径），**不能只删这条检查**。
_COLLIDING_MODULE_BASENAMES = _TEST_MODULE_BASENAMES & _PRODUCTION_MODULE_BASENAMES


def _frame_signature(text: str) -> tuple[tuple[str, int], ...]:
    """从一段日志文本抽**全部**帧签名 ``((basename, lineno), ...)``，保留出现顺序。

    只取 basename：绝对路径前缀随机器/工作区变，不可比。
    刻意**不去重** —— 重复帧本身也是结构信息（读取层失败会在同一文件里
    连续出现两个帧）。去重只在算「生产线深度」时用 ``set()`` 做。
    ``text`` 不含结构化帧头 → 空元组（「伪帧文本」就是这样）。
    """
    return tuple(
        (os.path.basename(path), int(lineno))
        for path, lineno in _FRAME_HEADER_RE.findall(text or "")
    )


def _production_frames(
    signature: tuple[tuple[str, int], ...],
) -> tuple[tuple[str, int], ...]:
    """剔除来自**测试套件任何模块**的帧（注入桩），只留生产 / 标准库帧。

    [FACT 为什么必须剔除] 注入桩帧会给 `open` / `read` 一份**免费的「每面唯一性」**：
    只要保留任意一个桩帧，两面的签名就天然不同，判据随即绿 —— 而真正的失败行
    `locator.py:181` / `:182` 早已丢失。`runner_plus_inner`（保留 `runner.py` 帧 +
    最内帧）正是这么绕过「每面必须含 runner.py 帧」那条老判据的。

    [A1 为什么剔整个套件] 只剔本文件时，桩搬到别的测试模块就重新假绿
    （`outer_inner` / `runner_plus_inner` 尤其明显）。按 basename 集合剔，覆盖
    `tests/` 递归下全部 `.py`。

    [A3 两条前提，都用显式 raise 而不是 assert]
    用 ``raise AssertionError`` 而非 ``assert``：``python -O`` 会删掉 assert，
    正好会让这两条防护**静默消失** —— 那正是它们存在的理由。``AssertionError``
    在 pytest 里仍然算一条失败，语义不变。
      1. 扫描不完整（权限 / IO）→ 剔除失效 → 静默假绿；
      2. tests/ 下出现与生产模块同名的 .py → 生产帧被误剔 → 深度少算 → 假红。
    两者都是**测试自身/环境问题，不是实现退化**，文案必须说清。
    """
    if not _TEST_MODULE_WALK_OK:
        raise AssertionError(
            f"os.walk 没扫到任何 .py（{_TESTS_DIR}），按测试套件剔除的判据失效、"
            "会静默假绿。这是**测试环境问题，不是实现退化**，"
            "请检查目录可读性后重跑"
        )
    if _COLLIDING_MODULE_BASENAMES:
        raise AssertionError(
            f"tests/ 下出现了与生产模块同名的 .py：{sorted(_COLLIDING_MODULE_BASENAMES)}；"
            "按 basename 剔除会把生产帧误剔 → 生产线深度少算 → 假红。"
            "必须改剔除策略（例如让 _frame_signature 保留相对路径），"
            "**不能只删这条检查**。这是**测试环境/结构问题，不是实现退化**"
        )
    return tuple(f for f in signature if f[0] not in _TEST_MODULE_BASENAMES)


def _production_depth(signature: tuple[tuple[str, int], ...]) -> int:
    """生产线深度 = 不重复的生产帧个数（`read` 面同一行的重复帧只算 1）。"""
    return len(set(_production_frames(signature)))


def _debug_records_of_type(caplog, exc_type: type[BaseException]) -> list[logging.LogRecord]:
    """筛出「DEBUG 级 + 消息里点名了 ``exc_type``」的记录。

    本文件自用，**不复用** ``_debug_records_naming`` / ``_debug_records_with_frame``：
    那两条是 S12/C5 的判据，本轮要原样保留它们、不与其耦合（也不弱化它们）。
    用 ``rec.getMessage()`` 而非 ``rec.msg``：三处 except 都只传预格式化 ``%s``
    参数（未传 ``exc_info``），栈帧信息全在那个参数里，``rec.msg`` 只是模板。
    """
    return [
        rec
        for rec in caplog.records
        if rec.levelno == logging.DEBUG and exc_type.__name__ in rec.getMessage()
    ]


def _open_arg_basename(file: object) -> str:
    """把 ``open()`` 的 file 参数尽力还原成 basename；还原不了的形态返回 ``""``（永不匹配）。

    用 ``os.fspath`` 而不是 ``str()``：将来 ``open()`` 若收到 ``Path`` 或实现了
    ``__fspath__`` 的对象，``str()`` 不一定等于真实路径 —— 条件匹配会失手 →
    注入不命中 → 报「注入未生效」白红一次（不会假绿，但没必要）。
    ``os.fsdecode`` 把 ``bytes`` 路径统一解码成 ``str``，否则 ``bytes`` 与
    ``cli_name``(str) 比较恒为 False。
    ``int`` 文件描述符（``open(fd)`` 合法）没有路径语义，返回 ``""`` 让它原样
    走真 ``open``，避免在这里抛 ``TypeError`` 造出一条新的失败路径。
    """
    try:
        return os.path.basename(os.fsdecode(os.fspath(file)))  # type: ignore[arg-type]
    except TypeError:
        return ""


def _trigger_sha256_failure(impl, tmp_path, monkeypatch, caplog, surface):
    """触发 sha256 回退分支里的指定失败面，返回该失败面的 DEBUG 记录列表。

    前置条件（注入锚点，失败即「注入没生效」而非「实现退化」）：
      探针被强制失败 → 一定会走到 sha256 回退；再让回退本身在指定层失败，
      于是 ``detect_version`` 必须返回 ``"unknown"``。若返回了 12 位 hex，
      说明注入根本没命中（回退成功了），此时任何帧签名断言都无意义。
    """
    locator = impl("locator")
    spec = impl("app_registry").get_spec("photocraft")
    cli_name = f"s12b-{surface}-cli.exe"
    cli_path = tmp_path / cli_name
    cli_path.write_bytes(b"MZ-stub-for-s12b-" + surface.encode())

    # 让 `--version` 探针必然失败，才会进入 sha256 回退分支。
    async def failing_exec(*args, **kwargs):
        raise FileNotFoundError("--version unsupported (S12-b)")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec, raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", failing_exec, raising=False)

    real_open = builtins.open

    if surface == "open":
        def _guarded_open(file, *args, **kwargs):
            # 条件式：只对本用例的假 CLI 文件生效，其余（含 pytest / logging 自身
            # 的 open）原样转给真 open。`monkeypatch` 负责自动还原。
            if _open_arg_basename(file) == cli_name:
                raise PermissionError(13, "Permission denied (S12-b: open)")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _guarded_open)

    elif surface == "read":
        class _BadRead(io.BytesIO):
            def read(self, *args, **kwargs):
                raise OSError(5, "I/O error (S12-b: read)")

        def _guarded_open(file, *args, **kwargs):
            if _open_arg_basename(file) == cli_name:
                return _BadRead(b"")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _guarded_open)

    caplog.set_level(logging.DEBUG)
    exc_type = _SHA256_SURFACE_EXC[surface]

    if surface == "executor_schedule":
        async def _run():
            # 真实现注入：把默认 executor 换成**已 shutdown** 的实例，
            # `to_thread` → `run_in_executor` → `submit` 会真抛 RuntimeError。
            # 不 patch 任何实现函数，因此拿到的栈帧是真实调度栈。
            loop = asyncio.get_running_loop()
            dead = ThreadPoolExecutor(max_workers=1)
            dead.shutdown(wait=True)
            loop.set_default_executor(dead)
            try:
                return await locator.detect_version(str(cli_path), spec)
            finally:
                # 探针实测 `set_default_executor(None)` 会抛 TypeError，
                # 只能换一个新实例；asyncio.run 收尾会去 shutdown 它。
                loop.set_default_executor(ThreadPoolExecutor(max_workers=2))

        got = asyncio.run(_run())
    else:
        got = asyncio.run(locator.detect_version(str(cli_path), spec))

    assert got == "unknown", (
        f"S12-b 注入未生效：{surface} 失败面下 detect_version 返回了 {got!r} 而不是 "
        "'unknown'，说明 sha256 回退其实成功了 —— 注入没命中，"
        "下面的帧签名对比无意义（这是测试自身的问题，不是实现退化）"
    )

    records = _debug_records_of_type(caplog, exc_type)
    assert records, (
        f"S12-b 注入锚点失败：{surface} 失败面确实发生了（返回值是 'unknown'），"
        f"但没有任何 DEBUG 记录点名 {exc_type.__name__}；"
        "要么日志降级了，要么注入的异常类型与实现捕获后记的类型不一致"
        "（先查这里，不要直接判定实现退化）"
    )
    return records


@pytest.mark.parametrize(
    "left,right",
    [
        ("executor_schedule", "open"),
        ("executor_schedule", "read"),
        ("open", "read"),
    ],
    ids=[
        "executor_schedule-vs-open",
        "executor_schedule-vs-read",
        "open-vs-read",
    ],
)
def test_c5_sha256_failure_surfaces_have_distinguishable_frame_signatures(
    impl, tmp_path, monkeypatch, caplog, left, right,
):
    """S12-b：每个失败面的生产栈深度必须 >= 2，且两个失败面的生产帧签名**不相等**。

    断言分三层，失败信息刻意指向不同原因，四种成因互不混淆：

    ① 注入锚点（在 ``_trigger_sha256_failure`` 内，`got == "unknown"`）：
       红在这里 = **注入没命中，测试自身的问题**，不是实现退化。
    ② 生产线深度（每个失败面独立）：`len(set(生产帧)) >= 2`。文案按原因分叉：
       * 深度 0 = **帧结构整体丢失**（伪帧文本 / 栈帧被整个换掉）；
       * 深度 1 = **生产栈深度不足**（只留最外 / 最内 / 首尾 / 只留失败行），
         文案会点名是哪个失败面、剩了哪一帧。
    ③ 跨面差异：两个失败面的**生产帧**签名元组不相等 —— 塌缩时文案给出两面的值。

    [③ 独立承重，不是 ② 的冗余] `first2`（只留最外两帧）形态：三面生产线深度都是
    2，**② 通过**；但三面签名都塌成 `(locator.py:220, runner.py:259)`，靠 ③ 才打红。
    **只做 ② 的话 `first2` 会假绿** —— ② 管「够不够深」，③ 管「三面是不是同一个」，
    两者测的是不同性质，都不要删。

    [A1 剔除范围是整个测试套件，不只是本文件] 桩一旦搬到别的测试模块
    （`tests/_s12b_inject.py`、`tests/s12b/xxx.py`），只剔本文件就会重新假绿：
    `outer_inner` 会得到 `(locator.py:220, <桩>)`（深度 2 过 ②、三面互不相同过 ③）、
    `runner_plus_inner` 会得到 `(runner.py:259, <桩>)`（同样双过）。所以按
    `tests/` 递归下全部 `.py` 的 basename 集合剔除。`conftest.py` / `_fixtures.py`
    也在集合里，这是对的 —— 它们是测试代码，不是生产栈。
    [A3-b 同名弱点] 按 basename 剔除有个固有弱点：`tests/` 下若出现与生产模块
    同名的 `.py`（将来给 tests 加 `__init__.py` 就是典型），生产帧会被误剔 → 深度
    少算 → 假红。这条前提被钉成运行期检查（`_COLLIDING_MODULE_BASENAMES`，
    生产模块名从 `artcraft_ops/*.py` 实时推导），不是只写在注释里。

    [为什么必须剔除测试套件帧 —— 主理人独立实测结论] 注入桩（`_guarded_open` /
    假 `fh`）也在栈里，它会给 `open` / `read` 一份**免费的「每面唯一性」**：
      * `runner_plus_inner`（保留 `runner.py` 帧 + 最内帧）：
        调度面 `(runner.py:259, thread.py:171)`、
        打开/读取面 `(runner.py:259, <注入桩>)` → 三面「两两不等」✅、
        每面都含 `runner.py` ✅ —— **两条老断言全过，但真正的失败行
        `locator.py:181` / `:182` 已丢失**。
      * `inner1`（只留最内一帧）：打开/读取面各剩一个注入桩帧 → 同理假绿。
      * `outer_inner`（首尾各留一帧）：首帧相同、尾帧是注入桩 → 同理假绿。
      ⇒ 只靠「含 runner.py」或「两两不等」都不够：**必须先剔除注入桩帧**，
        再要求「生产栈至少两层」。剔除后 `open` vs `read` 只能靠
        `locator.py:181` vs `:182` 区分，注入桩不再提供任何区分力。

    [为什么「>= 2」不是被禁的绝对帧数阈值] 见上方设计注释：被禁的是数
    `File "` 出现次数 / 数物理行数（随注入方式变）；`>= 2 个不重复的生产帧`
    已剔除测试帧，两种驱动方式下实测各 5 / 4 个，余量充足，语义是
    「异常不是从某一层原地冒出、也不是被截成单帧」。

    [形态依赖声明] 本条锚在 `format_exc()` 的 `File "<path>", line <N>` 文本
    结构上：若实现改用非 traceback 的结构化日志（`frame=... fn=...`），本条会红
    —— 那是形态变更、不是退化，需人工确认后调整判据。

    [契约内有意放行的形态 —— 不要为显得严格而加断言挡它们]
      * `flatten`：多帧压进一行（物理行 1 行、`File "` 出现多次）。帧个数没变，
        签名不变，见设计注释 `:576`。
      * `runner_inner_prod`（保留 `runner.py:259` 的 blocking 边界 + 失败行
        `locator.py:181` / `:182`，丢掉调用点 `locator.py:220` 与全部 asyncio
        中间帧）—— **有意放行**（复核实测 18 passed）。它是「信息量足够且不绑
        驱动方式」的最小形态：剔除测试桩帧后 `open` 与 `read` 的栈只差失败行
        `181` vs `182`（`locator:220 / runner:259 / threads:26 / thread:59`
        完全相同），所以「open ≠ read」本来就等价于「必须保留失败行」。
        丢掉的调用点与 asyncio 中间帧都是冗余噪声，三面仍两两可分。若为挡它而
        要求「必须保留 `locator.py:220`」，等于绑死「必须经 detect_version 驱动」，
        直接 `runner.blocking(...)` 驱动时会假红。
    [强度上限声明] 本判据已到顶。继续收紧（要求 `locator.py:220`、要求 ≥3 帧、
    钉具体行号、绑驱动方式）只会绑死实现形态并引入假红，**不要再加**；
    要提升可观测性请改生产代码的日志内容，不要加断言。
    """
    frames: dict[str, tuple[tuple[str, int], ...]] = {}

    for surface in (left, right):
        # 每个失败面独立作用域：`open` 的 patch 只在自己那段里生效，
        # 退出即还原，不会串到另一个失败面。caplog 手动清空，避免两条记录互污。
        with monkeypatch.context() as scoped:
            caplog.clear()
            records = _trigger_sha256_failure(
                impl, tmp_path, scoped, caplog, surface,
            )
        text = "\n".join(rec.getMessage() for rec in records)

        production = _production_frames(_frame_signature(text))
        depth = len(set(production))

        # ② 生产线深度：文案按「深度 0」与「深度 1」分叉，原因一眼可分。
        if depth == 0:
            reason = (
                "帧结构整体丢失：抽不到任何生产栈帧 "
                "`File \"<path>\", line <N>`（已剔除本测试文件的注入桩帧）。"
                "实现把栈帧换成了没有帧结构的纯文本（伪帧），或只留下落在注入桩上的帧。"
                "这与「注入未生效」不同 —— 注入锚点已单独通过。"
            )
        else:
            reason = (
                f"生产栈深度不足：只剩 {depth} 个不重复生产帧 {production!r}。"
                "说明帧被截在最外/最内/首尾，或只剩失败行本身 —— 调用链跨不过两层生产栈，"
                "「失败发生在阻塞调用的哪一侧」不可见。"
                "这与「注入未生效」不同 —— 注入锚点已单独通过。"
            )
        assert depth >= 2, f"S12-b: {surface} 失败面 {reason}"
        frames[surface] = production

    # ③ 跨面差异：两个失败面的**生产帧**签名必须不同。
    assert frames[left] != frames[right], (
        f"S12-b: {left} 与 {right} 两个失败面的生产帧签名塌缩成同一个 "
        f"{frames[left]!r}（生产线深度 {len(set(frames[left]))} / "
        f"{len(set(frames[right]))}）；"
        "说明两面的 DEBUG 日志在内层已无结构差异，"
        "sha256 回退的多种失败面重新不可区分"
    )
