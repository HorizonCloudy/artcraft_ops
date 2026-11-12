"""S17 红测试 —— 家目录 / 用户名去标识化（`redact_text` 的家目录规则）。

背景：S16 之后 `details` 成为**机器可读的对外出口**（宿主 `RunError.details`），
而 `PATH_OUTSIDE_ROOTS` 等码的 `path` 字段会原样带出本机用户名。
S16 收尾时已记录为 S17（`spec/ARCHITECTURE-FINAL.md` §19.15 遗留表）。

契约（实现前先钉死，实现侧不许改断言；细则见各用例 docstring 与 §19.17）::

  1. 家目录**前缀**归一 ``~``（不是整段 ``***``）。
  2. 只认路径形状：Win 现行 / XP 旧布局 / ``/home`` / ``/Users`` / ``/root``；裸词不脱。
  3. 幂等：已归一结果再跑一遍不变。
  4. 不误吞同形词：``/roots`` / ``/rootfs`` / ``C:\\UsersData\\x`` / ``C:\\Users`` /
     ``C:\\Users\\`` 一律原样。
  5. 非家目录路径与 UNC 原样保留。
  6. 与凭证规则共存：家目录归一是去标识化，凭证掩码仍是 ``***``。
  7. 划界：``redact()`` 服务功能性句柄，**不得**重写家目录。
  8. 终止性：用户名段在分隔符/引号/空白/中英文标点处停住（复核 P1）。
  9. 中文紧贴：lookbehind 一律用 ASCII 集（复核 P2-1）。
"""
from __future__ import annotations

import json
import types

# 断言里用的“本机真实家目录形状”：用真实用户名是为了让「裸词不脱」这条有牙齿
# （若实现偷偷按 env 用户名做整词替换，本文件立刻变红）。
HOME_WIN = r"C:\Users\54301\WorkBuddy\shot.png"
HOME_WIN_FWD = "c:/users/54301/WorkBuddy/shot.png"
HOME_DNS = r"C:\Documents and Settings\54301\a.doc"
HOME_POSIX = "/home/54301/report.pdf"
HOME_MACOS = "/Users/54301/Library/x.plist"
HOME_ROOT = "/root/.ssh/id_rsa"
USERNAME = "54301"


def _dump(value) -> str:
    """把任意结构摊平成可 `in` 判定的字符串（dict 用 json 稳序）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# 1) 各平台家目录前缀归一
# ---------------------------------------------------------------------------
def test_windows_home_prefix_becomes_tilde(impl):
    """正常：``C:\\Users\\<名>\\...`` 只去掉用户名，目录形状与文件名保留。"""
    errors = impl("errors")

    out = errors.redact_text(HOME_WIN)

    assert out == r"~\WorkBuddy\shot.png", f"实际 {out!r}"
    assert USERNAME not in out, f"用户名仍存在：{out!r}"


def test_windows_forward_slashes_and_case_insensitive(impl):
    """边界：Windows 接受 ``/`` 分隔且大小写不敏感（资源管理器/JSON 两种写法都有）。"""
    errors = impl("errors")

    out = errors.redact_text(HOME_WIN_FWD)

    assert out == "~/WorkBuddy/shot.png", f"实际 {out!r}"
    assert USERNAME not in out, f"用户名仍存在：{out!r}"


def test_windows_documents_and_settings(impl):
    """边界：XP 旧布局 ``Documents and Settings`` 同样是家目录。"""
    errors = impl("errors")

    out = errors.redact_text(HOME_DNS)

    assert out == r"~\a.doc", f"实际 {out!r}"


def test_posix_home_prefix(impl):
    """正常：``/home/<名>/...`` -> ``~/...``。"""
    errors = impl("errors")

    out = errors.redact_text(f"越界：{HOME_POSIX}")

    assert out == "越界：~/report.pdf", f"实际 {out!r}"


def test_macos_users_prefix(impl):
    """正常：macOS 的 ``/Users/<名>`` 与 Linux 的 ``/home`` 同等对待。"""
    errors = impl("errors")

    out = errors.redact_text(HOME_MACOS)

    assert out == "~/Library/x.plist", f"实际 {out!r}"


def test_posix_root_user(impl):
    """边界：``/root`` 本身就是家目录（无用户名段可留）。"""
    errors = impl("errors")

    out = errors.redact_text(HOME_ROOT)

    assert out == "~/.ssh/id_rsa", f"实际 {out!r}"


# ---------------------------------------------------------------------------
# 2) 不误吞同形词（负向控制：这几条是「规则太宽」的探针）
# ---------------------------------------------------------------------------
def test_root_word_not_swallowed(impl):
    """负向：``/roots``、``/rootfs``、``/home`` 都不是家目录路径，原样保留。

    这条专钉 ``/root`` 规则必须有「后接 ``/`` 或串尾」的边界断言；否则
    ``the path is outside the granted roots`` 这类文案会被咬掉半截。
    """
    errors = impl("errors")

    for text in (
        "the path is outside the granted roots: /roots/a.png",
        "/rootfs is not a home dir",
        "changedir /home refused",
    ):
        assert errors.redact_text(text) == text, f"误伤：{text!r}"


def test_users_word_not_swallowed(impl):
    """负向：``UsersData`` / 裸 ``C:\\Users`` / 结尾分隔符，都不构成家目录前缀。"""
    errors = impl("errors")

    for text in (
        r"C:\UsersData\x.png",
        r"C:\Users",
        "C:\\Users\\",
    ):
        assert errors.redact_text(text) == text, f"误伤：{text!r}"


def test_non_home_paths_untouched(impl):
    """负向：普通绝对路径与 UNC 共享路径不含用户名，必须原样保留（诊断价值）。"""
    errors = impl("errors")

    for text in (
        r"PATH_OUTSIDE_ROOTS: 文件不在已授权目录内：C:\secret.png",
        r"D:\state\artcraft_ops\data\out.png",
        r"\\fileserver\share\a.png",
    ):
        assert errors.redact_text(text) == text, f"误伤：{text!r}"


# ---------------------------------------------------------------------------
# 2b) 终止性（复核 P1 回归）：路径后面没有分隔符时，不许把散文一起吃进用户名
# ---------------------------------------------------------------------------
def test_terminates_at_prose_punctuation(impl):
    """P1 回归：真实模板里路径后面直接跟标点/中文，贪婪字符类曾把整句吞掉。

    修复前实测：``"命令用法错误：C:\\Users\\bob；整批未保存任何产物"`` -> ``"命令用法错误：~"``，
    尾巴「整批未保存任何产物」整段消失（`i18n.py` 的 `CLI_USAGE_ERROR` 模板）。
    """
    errors = impl("errors")

    cases = {
        r"路径 C:\Users\bob，不在白名单": r"路径 ~，不在白名单",
        r"CLI_USAGE_ERROR: 命令用法错误：C:\Users\bob；整批未保存任何产物":
            r"CLI_USAGE_ERROR: 命令用法错误：~；整批未保存任何产物",
        r"\path C:\Users\bob, roots=[C:\ok]": r"\path ~, roots=[C:\ok]",
        "/home/bob，不在白名单": "~，不在白名单",
        "/Users/bob)": "~)",
    }
    for text, expected in cases.items():
        assert errors.redact_text(text) == expected, f"{text!r} -> {errors.redact_text(text)!r}"


def test_cjk_adjacent_path_still_matches(impl):
    """漏网回归：Python 的 ``\\w``/``\\b`` 含中日韩字符，「中文紧贴路径」曾整条不匹配。"""
    errors = impl("errors")

    assert errors.redact_text(r"文件在C:\Users\bob\x.png") == r"文件在~\x.png"
    assert errors.redact_text("路径/home/bob/x") == "路径~/x"
    assert errors.redact_text("路径/Users/bob/x") == "路径~/x"


def test_space_in_username(impl):
    """边界：用户名含空格/逗号/中文—— 后接分隔符时整段归一（复核 P2-1/P2-2 后半）。

    歧义是本质的：``C:\\Users\\bob is outside`` 与 ``C:\\Users\\bob smith\\x`` 字面不可区分，
    所以含空格分支**只在后接分隔符时**生效，且 token 限定为名字安全字符（``=`` ``[`` 会打断）。
    """
    errors = impl("errors")

    cases = {
        r"C:\Users\John Doe\AppData\Local": r"~\AppData\Local",
        r"C:\Users\Smith, John\Documents\x": r"~\Documents\x",        # "姓, 名" 型 profile 目录
        r"C:\Users\John Ronald Reuel Tolkien\x": r"~\x",             # 4 段：{1,6} 上界
        "C:\\Users\\张三 李四\\Docs\\x": "~\\Docs\\x",                # 中文名 + 空格
    }
    for text, expected in cases.items():
        assert errors.redact_text(text) == expected, f"{text!r} -> {errors.redact_text(text)!r}"


def test_space_in_username_does_not_eat_prose(impl):
    """负向控制：含空格分支不许跨过散文去吃后面的分隔符。"""
    errors = impl("errors")

    text = r"C:\Users\bob is outside /roots/x"

    assert errors.redact_text(text) == r"~ is outside /roots/x", \
        f"实际 {errors.redact_text(text)!r}"


def test_username_with_paren_stays_partial(impl):
    """已知边界（如实钉住，不是期望行为）：用户名含括号时只掩第一段，残留 `` (Work)``。

    ``C:\\Users\\John (Work)`` 与 ``C:\\Users\\bob (see note)`` 无法区分；括号在散文里
    远比在 profile 目录名里常见，所以选择「不吞散文」、接受这里的半掩。见 §19.17（五）。
    """
    errors = impl("errors")

    assert errors.redact_text(r"C:\Users\John (Work)\x") == r"~ (Work)\x"
    # 全角括号同理：右括号在终止符集里、左括号曾漏掉，导致多吃掉 ``（Work``。
    assert errors.redact_text(r"C:\Users\John（Work）\x") == r"~（Work）\x"


# ---------------------------------------------------------------------------
# 3) 端到端：details / message 两个出口都不许带用户名
# ---------------------------------------------------------------------------
def test_details_path_is_redacted(impl):
    """P0：``err(...).error.details.path`` 是 S16 新增的机器可读出口，必须去标识化。"""
    errors = impl("errors")

    env = errors.err("PATH_OUTSIDE_ROOTS", path=HOME_WIN)
    details = env["error"]["details"] or {}

    assert details.get("path") == r"~\WorkBuddy\shot.png", f"实际 {details!r}"
    assert USERNAME not in _dump(env), f"用户名经 details 泄露：{_dump(env)}"


def test_message_carries_redacted_path(impl):
    """P0：``error.message`` 同源模板插值，必须一起去标识化，且码前缀不变。"""
    errors = impl("errors")

    env = errors.err("PATH_OUTSIDE_ROOTS", path=HOME_WIN)
    message = env["error"]["message"]

    assert message.startswith("PATH_OUTSIDE_ROOTS: "), f"码前缀被破坏：{message!r}"
    assert r"~\WorkBuddy\shot.png" in message, f"路径未归一：{message!r}"
    assert USERNAME not in _dump(env), f"用户名经 message 泄露：{_dump(env)}"


def test_structured_payload_keeps_functional_paths(impl):
    """边界（S17 划界）：``redact()`` **不**重写家目录 —— 它服务功能性载荷。

    ``result_handle`` / ``output_files`` 会被 ``os.path.isfile()`` 直接消费；
    归一成 ``~`` 会让句柄失效（``test_result.py::test_spill_summary_keeps_command_step_count``
    曾因此真红一次）。去标识化只在自由文本出口 ``redact_text`` 上做。
    """
    errors = impl("errors")

    out = errors.redact({
        "result_handle": HOME_WIN,
        "output_files": [HOME_WIN],
        "note": "token=" + "a" * 64,
    })

    assert out["result_handle"] == HOME_WIN, f"功能句柄被改写：{out['result_handle']!r}"
    assert out["output_files"] == [HOME_WIN], f"输出路径被改写：{out['output_files']!r}"
    assert "***" in out["note"], f"凭证掩码在 redact() 里失效：{out['note']!r}"


# ---------------------------------------------------------------------------
# 4) 幂等与共存
# ---------------------------------------------------------------------------
def test_redact_is_idempotent(impl):
    """边界：已归一文本再跑一次不变（``redact_text`` 会被多层重复调用）。"""
    errors = impl("errors")

    once = errors.redact_text(HOME_WIN)
    twice = errors.redact_text(once)

    assert once == twice == r"~\WorkBuddy\shot.png", f"非幂等：{once!r} -> {twice!r}"


def test_credential_rules_still_mask(impl):
    """共存：家目录规则插在凭证规则之前，但不得改变凭证的 ``***`` 语义。"""
    errors = impl("errors")

    out = errors.redact_text(r"token=deadbeefdeadbeefdeadbeefdeadbeef file=C:\Users\54301\x")

    assert "***" in out, f"凭证规则失效：{out!r}"
    assert USERNAME not in out, f"用户名未归一：{out!r}"
    assert r"~\x" in out, f"家目录未归一：{out!r}"


def test_bare_username_not_redacted(impl):
    """诚实边界：裸用户名（非路径上下文）**不脱**。

    这是刻意取舍，不是漏网：裸词规则会误伤数字与普通单词，收益远小于代价。
    若将来要收紧，必须新增独立规则并同步本断言。
    """
    errors = impl("errors")

    text = "user 54301 uploaded 3 files to 54301-archive"

    assert errors.redact_text(text) == text, f"裸词规则不该存在：{errors.redact_text(text)!r}"


# ---------------------------------------------------------------------------
# 5) 出口级：消息面 metadata 与工具出口的字段划界
# ---------------------------------------------------------------------------
def test_push_message_metadata_redacted(impl):
    """P2（复核）：``push_message`` 的 metadata 是展示字段，没有 isfile 消费者，必须归一。"""
    jobs = impl("jobs")
    job = types.SimpleNamespace(
        job_id="j1", progress=1.0, status="succeeded",
        result_path=HOME_WIN, result_handle=HOME_WIN,
    )

    payload = jobs._message_payload(job, "任务完成", ["chat"], "blind")

    assert payload["metadata"]["result_path"] == r"~\WorkBuddy\shot.png", \
        f"实际 {payload['metadata']!r}"
    assert payload["metadata"]["result_handle"] == r"~\WorkBuddy\shot.png", \
        f"实际 {payload['metadata']!r}"
    assert USERNAME not in _dump(payload), f"消息面泄露用户名：{_dump(payload)}"


def test_tool_output_text_fields_redacted(load_plugin_init):
    """划界的出口侧：工具 output 的**文本字段**归一，**功能性字段**保持可用。

    ``result_handle`` / ``output_files`` 会被宿主的 isfile / 下载路径直接消费，
    改成 ``~`` 等于把句柄变成不存在的路径；``error_message`` / ``exe_path`` 只给人看。
    """
    module = load_plugin_init()

    out = module.tool_output({
        "exe_path": HOME_WIN,
        "error_message": f"越界：{HOME_WIN}",
        "hint": f"可在 {HOME_WIN} 查看",
        "note": f"输出目录：{HOME_WIN}",
        "progress_label": f"写入 {HOME_WIN}",
        "warnings": [f"flattened: wrote {HOME_WIN}"],
        "result_handle": HOME_WIN,
        "output_files": [HOME_WIN],
        "job_id": "j1",
    })

    assert out["exe_path"] == r"~\WorkBuddy\shot.png", f"实际 {out['exe_path']!r}"
    assert out["hint"] == r"可在 ~\WorkBuddy\shot.png 查看", f"实际 {out['hint']!r}"
    assert out["note"] == r"输出目录：~\WorkBuddy\shot.png", f"实际 {out['note']!r}"
    assert out["progress_label"] == r"写入 ~\WorkBuddy\shot.png", f"实际 {out['progress_label']!r}"
    assert out["error_message"] == r"越界：~\WorkBuddy\shot.png", f"实际 {out['error_message']!r}"
    assert out["warnings"] == [r"flattened: wrote ~\WorkBuddy\shot.png"], \
        f"warnings 是自由文本，必须归一：{out['warnings']!r}"
    assert out["result_handle"] == HOME_WIN, f"功能句柄被改写：{out['result_handle']!r}"
    assert out["output_files"] == [HOME_WIN], f"功能路径被改写：{out['output_files']!r}"
