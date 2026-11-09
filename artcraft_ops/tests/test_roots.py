"""§10 #16 / #17 / #18 — roots 策略与 Windows 路径白名单（P0 #17）。

覆盖：正常路径（默认只授权 data/cache、headless flag、合法路径）
      + 边界（`..`/设备名/ADS/8.3/越界 symlink、`\\\\?\\C:\\` 长形式、bridge 不传 root）
      + 错误输入（越界路径、方向不匹配）。

[FACT] 依据 ARCHITECTURE §6 / §2 与 RESULTS §9.4：
  roots 是逐段纯文本比对，拒绝 `..`、设备名（CON）、ADS、8.3 短名、越界 symlink；
  v0.18.0 起接受 `\\\\?\\C:\\` / `\\\\?\\UNC\\`。
"""
from __future__ import annotations

import pytest

from _fixtures import DEFAULT_READ_FLAG, DEFAULT_WRITE_FLAG

DATA_DIR = r"C:\state\artcraft_ops\data"
CACHE_DIR = r"C:\state\artcraft_ops\cache"
USER_DIR = r"D:\Users\Alice\Pictures"
# 写进策略的 root 一律是 normalize_win_path 归一后的形态（正斜杠）：日志与错误里
# 只有一种拼写；比对本身走 _canonical，两种拼写等价。
USER_DIR_NORM = "D:/Users/Alice/Pictures"


def _detail(exc: BaseException) -> str:
    return f"{exc} {type(exc).__name__}"


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
def test_roots_default_only_data_cache(impl):
    """#16 正常：默认策略只授权插件自己的 data/ 与 cache/ 作 read+write roots。"""
    roots = impl("roots")
    policy = roots.default_policy(DATA_DIR, CACHE_DIR, {})

    assert DATA_DIR in policy.read_roots
    assert CACHE_DIR in policy.read_roots
    assert DATA_DIR in policy.write_roots
    assert CACHE_DIR in policy.write_roots
    assert len(policy.read_roots) == 2
    assert len(policy.write_roots) == 2
    assert policy.read_flag == DEFAULT_READ_FLAG
    assert policy.write_flag == DEFAULT_WRITE_FLAG
    assert policy.allow_user_dirs is False


def test_roots_user_dir_not_in_default_policy(impl):
    """#16 错误输入：用户目录未写进配置 -> 绝不在默认 roots 里（fail-closed）。"""
    roots = impl("roots")
    policy = roots.default_policy(DATA_DIR, CACHE_DIR, {})
    assert USER_DIR not in policy.read_roots
    assert USER_DIR not in policy.write_roots

    # 不运行时动态扩权：后续一次带用户目录的构造不得改写已返回的策略对象
    roots.default_policy(DATA_DIR, CACHE_DIR, {"user_read_roots": [USER_DIR]})
    assert USER_DIR not in policy.read_roots


def test_build_root_flags_headless(impl):
    """正常：headless 显式拼 --automation-read-root / --automation-write-root。"""
    roots = impl("roots")
    spec = impl("spec")
    r1, r2 = r"C:\state\data", r"C:\state\cache"
    w1, w2 = r"C:\state\out", r"C:\state\cache"
    policy = spec.RootsPolicy(
        read_roots=(r1, r2), write_roots=(w1, w2), mode=spec.RunMode.HEADLESS,
    )
    flags = roots.build_root_flags(policy)

    assert flags == [
        DEFAULT_READ_FLAG, r1,
        DEFAULT_READ_FLAG, r2,
        DEFAULT_WRITE_FLAG, w1,
        DEFAULT_WRITE_FLAG, w2,
    ]
    assert "mcp" not in flags
    assert "--bridge" not in flags


def test_resolve_and_check_returns_path_inside_root(impl):
    """正常：root 内的合法路径返回可用路径字符串，不被拒绝。"""
    roots = impl("roots")
    target = DATA_DIR + r"\sub\ok.png"
    got = roots.resolve_and_check(target, (DATA_DIR,), "read")
    assert isinstance(got, str)
    assert "ok.png" in got


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_build_root_flags_bridge_mode_returns_empty(impl):
    """边界/§6：bridge 模式 roots 配在桌面启动命令，插件不向 mcp --bridge 传 root。"""
    roots = impl("roots")
    spec = impl("spec")
    policy = spec.RootsPolicy(
        read_roots=(DATA_DIR,), write_roots=(CACHE_DIR,), mode=spec.RunMode.BRIDGE,
    )
    assert roots.build_root_flags(policy) == []


@pytest.mark.parametrize("bad", [
    r"..\secret.png",
    r"sub\..\..\secret.png",
    r"C:\state\data\..\..\Windows\evil.png",
])
def test_roots_reject_traversal(impl, bad):
    """#17 P0 边界：任何 `..` 路径段 -> PATH_UNSAFE。"""
    roots = impl("roots")
    with pytest.raises(Exception) as ei:
        roots.resolve_and_check(bad, (DATA_DIR,), "read")
    assert "PATH_UNSAFE" in _detail(ei.value) or "PolicyViolation" in _detail(ei.value), \
        _detail(ei.value)


@pytest.mark.parametrize("bad", [
    r"C:\state\data\CON",                 # 设备名
    r"C:\state\data\NUL.txt",            # 设备名带扩展名
    r"C:\state\data\AUX",                # 设备名
    r"C:\state\data\ok.png:hidden:$DATA",  # ADS 备用数据流
    r"C:\state\data\PROGRA~1\evil.png",  # 8.3 短名
])
def test_roots_reject_device_ads_83(impl, bad):
    """#17 P0 边界：设备名 / ADS / 8.3 短名一律拒绝（不落磁盘、不启动 CLI）。"""
    roots = impl("roots")
    with pytest.raises(Exception) as ei:
        roots.resolve_and_check(bad, (DATA_DIR,), "read")
    assert ("PATH_UNSAFE" in _detail(ei.value)
            or "PATH_OUTSIDE_ROOTS" in _detail(ei.value)
            or "PolicyViolation" in _detail(ei.value)), _detail(ei.value)


def test_roots_reject_out_of_root_symlink(impl, tmp_path):
    """#17 边界：越界 symlink 必须拒绝（RESULTS §9.4）。"""
    roots = impl("roots")
    root = tmp_path / "granted"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"secret")
    link = root / "link.png"
    try:
        link.symlink_to(outside / "secret.png")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"本机不支持创建 symlink（{exc}），跳过该边界用例")

    with pytest.raises(Exception) as ei:
        roots.resolve_and_check(str(link), (str(root),), "read")
    assert ("PATH_UNSAFE" in _detail(ei.value)
            or "PATH_OUTSIDE_ROOTS" in _detail(ei.value)
            or "PolicyViolation" in _detail(ei.value)), _detail(ei.value)


def test_normalize_win_path_accepts_long_form(impl):
    """边界/§9.4：v0.18.0 起 `\\\\?\\C:\\` 形式被接受，且逐段忽略大小写。"""
    roots = impl("roots")
    got = roots.normalize_win_path(r"\\?\C:\Work\File.PNG", r"C:\work")
    assert isinstance(got, str)
    assert "file.png" in got.lower()
    assert "work" in got.lower()


# ---------------------------------------------------------------------------
# 单分隔符 root-relative（云严 C1 的 roots 侧 · sequence 侧见 test_params_roots.py #21）
# ---------------------------------------------------------------------------
def test_root_relative_single_separator_rejected_at_roots_layer(impl):
    """#21b P0：单 `\\` / `/` 开头是**当前盘根**（root-relative），不是相对路径。

    [真缺陷] `normalize_win_path` 的 `is_abs` 只认 `^[A-Za-z]:` 与 `//`，于是
    `\\Windows\\win.ini` 被当成相对路径拼成 `<root>/Windows/win.ini` 并
    **返回 ALLOWED** —— 上层 `sequence` 哪怕乖乖把值交给 roots，这道闸门也是空转
    （「看起来有闸门、其实是空转」比没有闸门更糟）。

    [FACT] `os.path.exists("\\Windows\\win.ini") == True`（Win32 解析成
    `C:\\Windows\\win.ini`），所以这不是理论问题。

    为什么必须单独有这条（不能只靠 sequence 侧那条）：两层是**串联**的，
    只改 `sequence` 的值判据、不改 `roots.is_abs`，值照样会被 roots「洗白」放行。

    反向一半同样重要：`sub\\ok.png` 是真正的相对路径，必须按 root 拼接后放行，
    且返回的归一化路径里含 `sub/ok.png` —— 修 21b 不许把正常相对路径一起判成绝对。
    """
    roots = impl("roots")

    for raw in ("\\Windows\\win.ini", "/Windows/win.ini"):
        with pytest.raises(Exception) as ei:
            roots.resolve_and_check(raw, (DATA_DIR,), "read")
        assert "PATH_OUTSIDE_ROOTS" in _detail(ei.value), \
            f"入参 {raw!r} 未被拒：{_detail(ei.value)}"

    got = roots.resolve_and_check("sub\\ok.png", (DATA_DIR,), "read")
    normalized = got.replace("\\", "/")
    assert "sub/ok.png" in normalized, f"相对路径必须按 root 拼接，实际 {got!r}"
    assert normalized.lower().startswith(DATA_DIR.replace("\\", "/").lower()), \
        f"拼接结果必须落在 root 下，实际 {got!r}"


# ---------------------------------------------------------------------------
# 错误输入
# ---------------------------------------------------------------------------
def test_roots_outside_denied(impl):
    """#18 错误：文件不在已授权 roots -> PATH_OUTSIDE_ROOTS。"""
    roots = impl("roots")
    with pytest.raises(Exception) as ei:
        roots.resolve_and_check(
            r"C:\Windows\System32\evil.png", (DATA_DIR, CACHE_DIR), "read",
        )
    assert "PATH_OUTSIDE_ROOTS" in _detail(ei.value), _detail(ei.value)


def test_roots_write_outside_write_roots_denied(impl):
    """错误：方向分置 —— 写方向用只含 out 的 root 集合，data 下的路径应被拒。"""
    roots = impl("roots")
    with pytest.raises(Exception) as ei:
        roots.resolve_and_check(
            DATA_DIR + r"\result.png", (r"C:\state\out",), "write",
        )
    assert ("PATH_OUTSIDE_ROOTS" in _detail(ei.value)
            or "PolicyViolation" in _detail(ei.value)), _detail(ei.value)


def test_roots_empty_roots_denies_everything(impl):
    """错误/边界：空 roots 等价 fail-closed（RESULTS §9.4「不传 root 就 fail-closed」）。"""
    roots = impl("roots")
    with pytest.raises(Exception) as ei:
        roots.resolve_and_check(DATA_DIR + r"\ok.png", (), "read")
    assert "PATH_OUTSIDE_ROOTS" in _detail(ei.value) or "PolicyViolation" in _detail(ei.value), \
        _detail(ei.value)


# ---------------------------------------------------------------------------
# 运行时配置注入：active_policy() / configure_security()
# ---------------------------------------------------------------------------
# [真缺陷，2026-11-08] 入口层曾一律写 default_policy(data_dir, cache_dir, {})，
# 第三个参数是空字典 ⇒ [artcraft.security] allow_user_dirs / user_read_roots
# 被读进 Settings.security 却从未到达任何一次校验。以下是这条链的守卫。
@pytest.fixture(autouse=True)
def _reset_security(impl):
    """全局快照是**进程级**状态：每个用例前后都清回 fail-closed，防止互相污染。"""
    roots = impl("roots")
    roots.configure_security({})
    yield
    roots.configure_security({})


def test_active_policy_without_config_equals_default(impl):
    """无配置时 active_policy 与 default_policy(...,{}) **逐字段相等**（不放宽）。"""
    roots = impl("roots")
    got = roots.active_policy(DATA_DIR, CACHE_DIR)
    want = roots.default_policy(DATA_DIR, CACHE_DIR, {})
    assert got.read_roots == want.read_roots == (DATA_DIR, CACHE_DIR)
    assert got.write_roots == want.write_roots == (DATA_DIR, CACHE_DIR)
    assert got.allow_user_dirs is False


def test_active_policy_includes_user_root_when_enabled(impl):
    """正常：allow_user_dirs=true + user_read_roots → 入口策略里真的带上该目录。"""
    roots = impl("roots")
    roots.configure_security({
        "allow_user_dirs": True,
        "user_read_roots": [USER_DIR],
    })
    policy = roots.active_policy(DATA_DIR, CACHE_DIR)
    assert USER_DIR_NORM in policy.read_roots
    assert policy.allow_user_dirs is True
    # 写方向未声明 → 仍然只有 data/cache
    assert USER_DIR not in policy.write_roots


def test_active_policy_ignores_user_roots_when_not_allowed(impl):
    """边界：只写 user_read_roots 但 allow_user_dirs=false → 不生效（双开关）。"""
    roots = impl("roots")
    roots.configure_security({"user_read_roots": [USER_DIR]})
    policy = roots.active_policy(DATA_DIR, CACHE_DIR)
    assert USER_DIR not in policy.read_roots


def test_configure_security_rejects_non_dict(impl):
    """错误输入：配置读坏（None / list / 字符串）→ 清空，绝不放宽。"""
    roots = impl("roots")
    for bad in (None, [], ["D:/x"], "allow_user_dirs=true", 42):
        roots.configure_security(bad)                    # type: ignore[arg-type]
        snap = roots.active_security()
        assert snap["allow_user_dirs"] is False, bad
        assert snap["user_read_roots"] == [], bad
        assert roots.active_policy(DATA_DIR, CACHE_DIR).read_roots == (DATA_DIR, CACHE_DIR)


def test_configure_security_drops_relative_and_empty_roots(impl):
    """边界：相对路径 / 空串 / 纯空白 → 丢弃（留着等于「以为授权了」）。"""
    roots = impl("roots")
    snap = roots.configure_security({
        "allow_user_dirs": True,
        "user_read_roots": ["Pictures", "", "   ", USER_DIR, USER_DIR],
    })
    assert snap["user_read_roots"] == [USER_DIR_NORM], snap["user_read_roots"]


def test_configure_security_accepts_single_string_root(impl):
    """边界：`user_read_roots = "D:/x"`（写成字符串而非数组）按一条处理。"""
    roots = impl("roots")
    snap = roots.configure_security({
        "allow_user_dirs": True,
        "user_read_roots": USER_DIR,
    })
    assert snap["user_read_roots"] == [USER_DIR_NORM]


def test_allow_user_dirs_string_false_is_false(impl):
    """错误输入：`allow_user_dirs = "false"` 不得被 `bool()` 当成 True。"""
    roots = impl("roots")
    for cfg in (
        {"allow_user_dirs": "false", "user_read_roots": [USER_DIR]},
        {"allow_user_dirs": "0", "user_read_roots": [USER_DIR]},
        {"allow_user_dirs": "off", "user_read_roots": [USER_DIR]},
        {"allow_user_dirs": "no", "user_read_roots": [USER_DIR]},
    ):
        policy = roots.default_policy(DATA_DIR, CACHE_DIR, cfg)
        assert policy.allow_user_dirs is False, cfg
        assert USER_DIR not in policy.read_roots, cfg


def test_active_security_snapshot_is_a_copy(impl):
    """边界：改返回的快照不得回写全局（避免入口层被"顺手改一下"扩权）。"""
    roots = impl("roots")
    roots.configure_security({"allow_user_dirs": True, "user_read_roots": [USER_DIR]})
    snap = roots.active_security()
    snap["user_read_roots"].append(r"D:/Windows")
    snap["allow_user_dirs"] = False
    after = roots.active_security()
    assert after["user_read_roots"] == [USER_DIR_NORM]
    assert after["allow_user_dirs"] is True


# ---------------------------------------------------------------------------
# S10（红）段判据一致性：`..` / 设备名的**中间段**带空白
# ---------------------------------------------------------------------------
# 与 tests/test_params_roots.py:790（#24）/ :1221（#32）**不是重复**：
# 那两条钉在 sequence.validate_steps 的**值判据**层，本条钉在
# roots.resolve_and_check 的**段判据**层。两层串联 —— sequence 层收严之后，
# 值仍会被 roots 的精确等于判据「洗白」放行，所以必须两层各有一条。
@pytest.mark.parametrize("bad", [
    DATA_DIR + "/.. /x.png",        # `..` + 尾随空格
    DATA_DIR + "/..\t/x.png",       # `..` + 尾随制表
    DATA_DIR + "/..\r/x.png",       # `..` + 尾随 CR
    DATA_DIR + "/..\u00a0/x.png",   # `..` + 尾随 NBSP
    DATA_DIR + "/ ../x.png",        # `..` + 前导空格
    DATA_DIR + "/con /x.png",       # 设备名 + 尾随空格
    DATA_DIR + "/con\t/x.png",      # 设备名 + 尾随制表
    # 后三条补齐 str.isspace() 整族（#32 已把整族列为本闸门的契约族）：
    # 用 chr() 显式构造，避免转义歧义（与 test_params_roots.py 的
    # B1_DOTDOT_WS_VARIANTS 同风格）。ZWSP(U+200B) / BOM(U+FEFF) **不**属
    # isspace()，本轮明确不收口，故不列入。
    DATA_DIR + "/.." + chr(10) + "/x.png",      # `..` + 尾随 LF
    DATA_DIR + "/.." + chr(11) + "/x.png",      # `..` + 尾随 VT (U+000B)
    DATA_DIR + "/.." + chr(8195) + "/x.png",    # `..` + 尾随 EM SPACE (U+2003)
], ids=[
    "dotdot-trailing-space", "dotdot-trailing-tab", "dotdot-trailing-cr",
    "dotdot-trailing-nbsp", "dotdot-leading-space",
    "con-trailing-space", "con-trailing-tab",
    "dotdot-trailing-lf", "dotdot-trailing-vt", "dotdot-trailing-emspace",
])
def test_roots_reject_middle_segment_whitespace_and_device_names(impl, bad):
    """S10（红）：`..` / 设备名的**中间段**带空白时，必须与串尾写法一样被拒。

    [性质：判据一致性缺口，**不是漏洞**]
    `_assert_safe_segments` 逐段做**精确等于**比对（`seg == ".."`、
    `stem in _DEVICE_NAMES`）。串尾写法（`.../data/.. `、`.../data/con `）现在确实
    被拒 —— 但**不是判据在起作用**：`normalize_win_path`（roots.py:74）对**整串**做了
    `text.strip()`，顺手把**最后一段**的尾随空白剥掉了，精确等于判据才恰好命中。
    中间段的空白没人剥 → 判据落空 → 放行。所以「串尾严 / 串中宽」是整串 strip 的
    **偶然覆盖**，不是设计意图。

    [不主张可利用 —— 三套 API 实测结论]
    `".. "` 在 Windows 上**不会**被解析成 `..`：Python `open()` → FileNotFoundError；
    PowerShell `[IO.File]::ReadAllText` → MethodInvocationException；`cmd /c dir` → rc=1。
    三套一致。对照组 `root/inside.txt `（普通名尾随空格）**能**读到 `inside.txt` ——
    Windows 确实会剥尾随空格，但**只对普通名**；`..` 必须在解析阶段精确匹配，
    剥完不再当 `..`。故本条定的是「判据不一致 + 依赖未文档化的 OS 行为」，
    不是「已知可利用的目录穿越」。

    [口径] 拒掉是 fail-closed 的**偏严兜底**，不是 Windows 语义推导。
    只钉空白族，不钉 `...` / `.. .` / 尾随点（无契约依据，钉住等于把实现细节冻成契约）。

    [为什么锁死 PATH_UNSAFE] 本条测的就是**段判据**：若只拿到 `PATH_OUTSIDE_ROOTS`
    兜底，说明路径被别处顺手拒了，判据本身仍是空的 —— 那不算修好。
    """
    roots = impl("roots")
    errors = impl("errors")

    with pytest.raises(errors.PolicyViolation) as ei:
        roots.resolve_and_check(bad, (DATA_DIR,), "read")

    detail = _detail(ei.value)
    assert "PATH_UNSAFE" in detail, f"入参 {bad!r} 未按段判据拒绝：{detail}"
    assert "PATH_OUTSIDE_ROOTS" not in detail, \
        f"入参 {bad!r} 只是被越界兜底拦下，段判据仍落空：{detail}"
    assert getattr(ei.value, "code", "") == "PATH_UNSAFE", \
        f"入参 {bad!r} 错误码不是 PATH_UNSAFE：{getattr(ei.value, 'code', None)!r}"


@pytest.mark.parametrize("good", [
    "a/b.png",
    "a b/c.png",        # 段内空格：合法，修复后必须仍放行（防一刀切 strip）
    "中文/图.png",
    "a-b_c.d.png",
], ids=["plain", "inner-space", "cjk", "dash-underscore-dot"])
def test_roots_allow_normal_paths_after_segment_whitespace_fix(impl, good):
    """S10 反向（现在就该绿）：正常相对路径不得被「一刀切 strip 所有空白」误杀。

    重点在 `a b/c.png`：空格在**段内**，不是段尾 —— 它是合法目录名。修 S10 时若
    图省事把每个段都 `strip()` 再比（甚至把空白段整段丢掉），这条会立刻变红。
    """
    roots = impl("roots")

    got = roots.resolve_and_check(good, (DATA_DIR,), "read")
    assert isinstance(got, str), f"入参 {good!r} 未返回路径字符串：{got!r}"
    normalized = got.replace("\\", "/")
    assert good in normalized, f"入参 {good!r} 归一化后丢了段内容：{got!r}"
    assert normalized.lower().startswith(DATA_DIR.replace("\\", "/").lower()), \
        f"入参 {good!r} 必须落在 root 下，实际 {got!r}"
