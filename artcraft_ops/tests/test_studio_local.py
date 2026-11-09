"""§19 补：ArtCraft Studio **本地 GUI 交接通道**（`studio_local`）的回归测试。

这一层没有 CLI，所以全部靠「廉价但精确」的不变量把住：

  * **依赖面为零**：只允许 ``os`` / ``subprocess`` / ``typing`` —— 没有
    uiautomation / pywinauto / pyautogui 之类的 UI 自动化库（用 AST 查 import，
    不查子串：docstring 里要举例说明为什么不用它们）。
  * **入口不接受 exe 路径**：schema 只有 ``files``，handler 签名没有 ``exe``。
    否则等于把「任意 exe + 任意参数」包装成可直接调用的工具。
  * **文件交接 fail-closed**：root 外 / 不存在 / 目录 / 超上限 一律拒。
  * **诚实字段**：``handoff_verified`` 恒 False（Popen 成功 != 窗口已加载）。
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from _fixtures import FakeCtx

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

#: studio_local 允许 import 的**标准库**顶层模块（刻意极简）
ALLOWED_IMPORTS = frozenset({"__future__", "os", "subprocess", "typing"})

#: 明令禁止的 UI 自动化 / 窗口消息库（出现在 import 里就说明设计跑偏了）
FORBIDDEN_UI_IMPORTS = frozenset({
    "uiautomation", "pywinauto", "pyautogui", "pygetwindow",
    "win32gui", "win32api", "win32con", "win32ui", "pynput", "keyboard", "mouse",
})


def _imports(path: Path) -> set[str]:
    """收集顶层 import 名（相对 import 不算，它们指向本包）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


# ---------------------------------------------------------------------------
# 设计不变量
# ---------------------------------------------------------------------------
def test_studio_local_has_no_ui_automation_dependency():
    """依赖面守卫：只允许 os/subprocess/typing，禁止任何 UI 自动化库。"""
    used = _imports(PLUGIN_ROOT / "studio_local.py")
    assert not (used & FORBIDDEN_UI_IMPORTS), \
        f"studio_local 引入了 UI 自动化库：{sorted(used & FORBIDDEN_UI_IMPORTS)}"
    assert used <= ALLOWED_IMPORTS, \
        f"studio_local 引入了意料之外的依赖：{sorted(used - ALLOWED_IMPORTS)}"


def test_studio_local_is_not_the_cloud_channel():
    """两个通道必须分开：云端 stub 仍全量 STUDIO_NOT_IMPLEMENTED，本地通道不碰云端地址。"""
    cloud = (PLUGIN_ROOT / "studio_channel.py").read_text(encoding="utf-8")
    local = (PLUGIN_ROOT / "studio_local.py").read_text(encoding="utf-8")
    assert "STUDIO_NOT_IMPLEMENTED" in cloud
    assert "api.artcraft.app" not in local, "本地通道不该出现云端 API 地址"
    assert "cookies" not in local.lower(), "本地通道不碰云端凭据"


# ---------------------------------------------------------------------------
# exe 探测
# ---------------------------------------------------------------------------
def test_candidates_cover_program_files(impl):
    studio = impl("studio_local")
    candidates = studio.studio_candidates()
    assert candidates, "候选列表不能为空"
    assert any(
        item.replace("\\", "/").endswith("ArtCraft/artcraft.exe") for item in candidates
    ), f"候选里没有任何 .../ArtCraft/artcraft.exe：{candidates}"


def test_configured_exe_wins(impl, tmp_path):
    """``[artcraft.studio] exe_path`` 覆盖优先级最高（探针/自定义安装目录）。"""
    studio = impl("studio_local")
    fake = tmp_path / "artcraft.exe"
    fake.write_bytes(b"MZ")
    try:
        studio.configure({"artcraft": {"studio": {"exe_path": str(fake)}}})
        assert studio.configured_exe() == str(fake)
        assert studio.studio_candidates()[0] == str(fake)
        assert studio.resolve_studio_exe() == str(fake)
    finally:
        studio.configure({})                      # 复原全局态，避免污染其它用例


def test_configure_ignores_broken_shape(impl):
    """配置缺段/类型错不崩，回落空串（= 走默认探测）。"""
    studio = impl("studio_local")
    try:
        for bad in (None, {}, {"artcraft": None}, {"artcraft": {"studio": "x"}}):
            assert studio.configure(bad) == ""
    finally:
        studio.configure({})


def test_resolve_returns_empty_when_nothing_installed(impl, monkeypatch):
    """一个都不存在 → 返回空串，**不抛异常**（是否安装是状态，不是异常）。"""
    studio = impl("studio_local")
    monkeypatch.setattr(studio, "studio_candidates", lambda: ("C:/nope/a.exe",))
    assert studio.resolve_studio_exe() == ""
    assert studio.is_available() is False


def test_studio_installed_on_this_machine(impl):
    """实机断言：本机装了 Studio 就必须解析到真实文件（缺装则 FAIL 提醒）。"""
    studio = impl("studio_local")
    exe = studio.resolve_studio_exe()
    assert exe, "本机未探测到 artcraft.exe（若确未安装，请调整本用例）"
    assert Path(exe).is_file()
    assert Path(exe).name.lower() == studio.STUDIO_EXE_NAME


# ---------------------------------------------------------------------------
# 文件交接校验（fail-closed）
# ---------------------------------------------------------------------------
@pytest.fixture
def workroot(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    return root


def test_validate_accepts_file_inside_root(impl, workroot):
    studio = impl("studio_local")
    target = workroot / "a.png"
    target.write_bytes(b"x")
    out = studio.validate_handoff_files([str(target)], (str(workroot),))
    assert len(out) == 1
    assert out[0].replace("\\", "/").endswith("/data/a.png")


def test_validate_accepts_empty_list(impl, workroot):
    """空列表合法 = 只启动 GUI、不交文件。"""
    studio = impl("studio_local")
    assert studio.validate_handoff_files([], (str(workroot),)) == ()
    assert studio.validate_handoff_files(None, (str(workroot),)) == ()


def test_validate_rejects_outside_roots(impl, workroot):
    studio = impl("studio_local")
    errors = impl("errors")
    with pytest.raises(errors.ArtcraftError) as ei:
        studio.validate_handoff_files(["C:/Windows/win.ini"], (str(workroot),))
    assert ei.value.code == "PATH_OUTSIDE_ROOTS"


def test_validate_rejects_traversal(impl, workroot):
    studio = impl("studio_local")
    errors = impl("errors")
    with pytest.raises(errors.ArtcraftError) as ei:
        studio.validate_handoff_files(
            [str(workroot) + "/../escape.png"], (str(workroot),),
        )
    assert ei.value.code == "PATH_UNSAFE"


def test_validate_rejects_missing_file(impl, workroot):
    """root 内但不存在 —— 不能把路径丢给 GUI 让它自己报错。"""
    studio = impl("studio_local")
    errors = impl("errors")
    with pytest.raises(errors.ArtcraftError) as ei:
        studio.validate_handoff_files([str(workroot / "nope.png")], (str(workroot),))
    assert ei.value.code == "POLICY_VIOLATION"
    assert "does not exist" in str(ei.value)


def test_validate_rejects_directory(impl, workroot):
    studio = impl("studio_local")
    errors = impl("errors")
    sub = workroot / "folder"
    sub.mkdir()
    with pytest.raises(errors.ArtcraftError) as ei:
        studio.validate_handoff_files([str(sub)], (str(workroot),))
    assert "not a regular file" in str(ei.value)


def test_validate_rejects_too_many(impl, workroot):
    studio = impl("studio_local")
    errors = impl("errors")
    files = []
    for i in range(studio.STUDIO_MAX_FILES + 1):
        p = workroot / f"f{i}.png"
        p.write_bytes(b"x")
        files.append(str(p))
    with pytest.raises(errors.ArtcraftError) as ei:
        studio.validate_handoff_files(files, (str(workroot),))
    assert ei.value.code == "POLICY_VIOLATION"
    assert "too many" in str(ei.value)


# ---------------------------------------------------------------------------
# 启动（绝不真起 GUI）
# ---------------------------------------------------------------------------
class _FakeProc:
    pid = 4242


def test_launch_argv_and_kwargs(impl, tmp_path, monkeypatch):
    """argv = [exe, *files]；固定 shell=False + 三个句柄 DEVNULL。"""
    studio = impl("studio_local")
    fake_exe = tmp_path / "artcraft.exe"
    fake_exe.write_bytes(b"MZ")
    doc = tmp_path / "a.png"
    doc.write_bytes(b"x")

    seen: list[tuple] = []

    def fake_popen(argv, **kwargs):
        seen.append((list(argv), dict(kwargs)))
        return _FakeProc()

    monkeypatch.setattr(studio.subprocess, "Popen", fake_popen)
    out = studio.launch([str(doc)], exe=str(fake_exe))

    assert len(seen) == 1
    argv, kwargs = seen[0]
    assert argv == [str(fake_exe), str(doc)]
    assert kwargs["shell"] is False
    assert kwargs["close_fds"] is True
    assert kwargs["stdin"] is studio.subprocess.DEVNULL
    assert kwargs["stdout"] is studio.subprocess.DEVNULL
    assert kwargs["stderr"] is studio.subprocess.DEVNULL
    assert "shell" in kwargs and kwargs["shell"] is not True

    assert out["launched"] is True
    assert out["file_count"] == 1
    assert out["pid"] == 4242
    # 诚实字段：绝不谎报「窗口里已打开」
    assert out["handoff_verified"] is False
    assert out["note"]


def test_launch_without_files_just_starts_gui(impl, tmp_path, monkeypatch):
    studio = impl("studio_local")
    fake_exe = tmp_path / "artcraft.exe"
    fake_exe.write_bytes(b"MZ")
    seen: list[list[str]] = []
    monkeypatch.setattr(
        studio.subprocess, "Popen",
        lambda argv, **kw: (seen.append(list(argv)), _FakeProc())[1],
    )
    out = studio.launch([], exe=str(fake_exe))
    assert seen[0] == [str(fake_exe)]
    assert out["file_count"] == 0


def test_launch_raises_when_exe_missing(impl, tmp_path, monkeypatch):
    studio = impl("studio_local")
    monkeypatch.setattr(studio.subprocess, "Popen", lambda *a, **k: _FakeProc())
    studio.configure({})
    with pytest.raises(studio.ArtcraftError) as ei:
        studio.launch([], exe=str(tmp_path / "nope.exe"))
    assert ei.value.code == "APP_NOT_FOUND"


def test_launch_survives_oserror(impl, tmp_path, monkeypatch):
    """Popen 抛 OSError（权限/被杀软拦）→ 固定码 APP_NOT_FOUND，不外泄原文。"""
    studio = impl("studio_local")
    fake_exe = tmp_path / "artcraft.exe"
    fake_exe.write_bytes(b"MZ")

    def boom(*_a, **_k):
        raise OSError("access denied by antivirus C:/secret/path")

    monkeypatch.setattr(studio.subprocess, "Popen", boom)
    with pytest.raises(studio.ArtcraftError) as ei:
        studio.launch([], exe=str(fake_exe))
    assert ei.value.code == "APP_NOT_FOUND"
    assert "secret" not in str(ei.value)


# ---------------------------------------------------------------------------
# 入口层：不接受 exe，且未安装时返回状态而非异常
# ---------------------------------------------------------------------------
def test_entry_schema_only_accepts_files(load_plugin_init):
    mod = load_plugin_init()
    schema = mod.STUDIO_SCHEMA
    assert set(schema["properties"]) == {"files"}, \
        f"Studio 入口只应接受 files，实际 {sorted(schema['properties'])}"
    assert schema["properties"]["files"]["maxItems"] == 8
    assert schema["required"] == ["files"]


def test_entry_handler_has_no_exe_parameter(load_plugin_init):
    """签名守卫：handler 若有 exe 参数 = 任意代码执行面。"""
    mod = load_plugin_init()
    fn = mod.ArtcraftOpsPlugin.artcraft_ops_open_in_studio
    params = set(fn.__code__.co_varnames[: fn.__code__.co_argcount])
    assert "exe" not in params and "exe_path" not in params, f"入口暴露了 exe 参数：{params}"


def test_entry_reports_unavailable_as_state(load_plugin_init, tmp_path, monkeypatch):
    """未安装 Studio → Ok(launched=False, studio_available=False)，不是 Err。"""
    mod = load_plugin_init()
    plugin = mod.ArtcraftOpsPlugin(FakeCtx(storage_dir=tmp_path, plugin_dir=PLUGIN_ROOT))
    monkeypatch.setattr(mod.studio_local, "resolve_studio_exe", lambda: "")

    res = asyncio.run(plugin.artcraft_ops_open_in_studio(files=[]))
    payload = getattr(res, "value", None)
    assert isinstance(payload, dict), f"应返回 Ok(dict)，实际 {res!r}"
    assert payload["launched"] is False
    assert payload["studio_available"] is False
    assert payload["handoff_verified"] is False
    assert "artcraft.studio" in payload["note"]


def test_entry_rejects_outside_root_with_fixed_code(load_plugin_init, tmp_path, monkeypatch):
    """入口走同一条 roots 策略：root 外路径必须被拒，且回固定码。"""
    mod = load_plugin_init()
    plugin = mod.ArtcraftOpsPlugin(FakeCtx(storage_dir=tmp_path, plugin_dir=PLUGIN_ROOT))
    fake_exe = tmp_path / "artcraft.exe"
    fake_exe.write_bytes(b"MZ")
    monkeypatch.setattr(mod.studio_local, "resolve_studio_exe", lambda: str(fake_exe))
    monkeypatch.setattr(
        mod.studio_local.subprocess, "Popen",
        lambda *a, **k: pytest.fail("拒绝路径时不该启动进程"),
    )
    res = asyncio.run(plugin.artcraft_ops_open_in_studio(files=["C:/Windows/win.ini"]))
    assert isinstance(res, dict), f"应返回错误 envelope dict，实际 {res!r}"
    # 两种 fail-closed 码都接受：具体命中哪个由 roots 判据顺序决定，
    # 把某一个钉死会在实现侧调整 deny-list 顺序时误红。
    assert res["error"]["code"] in ("PATH_OUTSIDE_ROOTS", "PATH_UNSAFE"), res["error"]


def test_entry_launches_inside_root(load_plugin_init, tmp_path, monkeypatch):
    """正路：data/ 里刚产出的文件交给 Studio（Popen 被拦，不真起 GUI）。"""
    mod = load_plugin_init()
    plugin = mod.ArtcraftOpsPlugin(FakeCtx(storage_dir=tmp_path, plugin_dir=PLUGIN_ROOT))
    fake_exe = tmp_path / "artcraft.exe"
    fake_exe.write_bytes(b"MZ")
    data_dir = Path(tmp_path) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "out.png"
    target.write_bytes(b"x")

    seen: list[list[str]] = []
    monkeypatch.setattr(mod.studio_local, "resolve_studio_exe", lambda: str(fake_exe))
    monkeypatch.setattr(
        mod.studio_local.subprocess, "Popen",
        lambda argv, **kw: (seen.append(list(argv)), _FakeProc())[1],
    )
    res = asyncio.run(plugin.artcraft_ops_open_in_studio(files=[str(target)]))
    payload = getattr(res, "value", None)
    assert isinstance(payload, dict), f"应返回 Ok(dict)，实际 {res!r}"
    assert payload["launched"] is True
    assert payload["file_count"] == 1
    assert len(seen) == 1 and seen[0][0] == str(fake_exe)
    assert seen[0][1].replace("\\", "/").endswith("/data/out.png")
