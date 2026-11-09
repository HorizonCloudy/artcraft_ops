"""step params 里的路径字段必须过 roots —— `validate_steps` 透传 roots 的守卫。

[真缺陷，2026-11-08 · 实测复现路径]
入口层拿到的是模型可控的 ``steps=[{cmd, params}]``；``validate_steps`` 里对每条 step
只调 ``validate_params(step.params or {})`` —— **不传 roots**。于是：

    {"cmd": "edit.fill", "params": {"path": "C:\\\\Windows\\\\win.ini"}}

一路 P5 校验全绿，模型可以让无头 CLI 读任意系统文件再导出成 PNG。
``validate_params`` 自己其实早就写好了 roots 分支（形参 default ``None`` 兜底旧行为），
只是**没有一个调用点把 roots 递进去**。同一族缺陷还在 ``validate_tool_kwargs``：
它签名收了 ``roots``，转手调 ``validate_steps`` 时却把它吞掉。

本文件钉住五件契约（实现方照此实现，测试方照此断言；主理人 2026-11-08 裁决后定稿）：
  1. ``validate_params(params, roots, direction)``：只对「像绝对路径」的值查 roots，
     key ∈ {path,file,input,output,out,src,dest,target,source,save,export}（小写比较）；
     相对路径里出现 ``..`` 段直接拒（cwd 是 CLI 安装目录，相对 ``..`` 会逃出 roots）；
     普通相对文件名 / SVG path data 一律放行；``roots=None`` = 旧行为零变化。
     [2026-11-08 更新] URL **不再**「一律放行」，只有白名单内的真实 scheme
     （``http`` / ``https``，大小写不敏感）才跳过 ``..`` 判据；其余 ``scheme://``
     按路径走（`xyz://../../x.png` 在 Windows 上会被折成目录名 `xyz` 并照常消解
     ``..``，实测 CLI 在 roots 外落盘 —— 见 #31）。另 ``file:`` **不是** URL，
     是本地路径的另一种拼写，剥协议后照常过 roots / ``..`` 判据（见 #30）。
  2. **递归下降**（裁决 1）：任意深度的 dict / list 都要走同一套规则 —— 非路径键的
     dict/list 值必须递归（否则 ``{"options":{"path":...}}`` 是等价旁路），list 里
     逐个 str 元素检查、非 str 元素跳过。**深度上限 16，超过直接拒**（fail-closed，
     消息含「嵌套过深」）：静默停止等于把下一层变成下一条旁路。16 而不是 8 ——
     真实关键帧结构 ``tracks→[dict]→keys→[dict]→k`` 本身就吃 8 层容器，
     上限 8 会把正常命令判死（实测过）。
  3. **键名 token 化**（裁决 1 变更）：键名先拆驼峰再按非字母数字切分、小写，得到
     token 集合，与键集取交集，非空即路径键。     键集 =
     ``{path,file,input,output,out,src,source,dest,target,save,export}``
     ``∪ {dir,directory,folder,dst,filename,filepath}``（后者方向全 any）
     ``∪ {log,template,mesh,into}``（#22 实证，方向全 any）
     ``∪ {profile,droplet,import}``（#27/#28 实证：schema 明写收路径；
     方向按 key 语义由实现定，`droplet`/`import` 现为 read —— 本文件**不锁**方向）。
     复数标量：`len > 3 且 endswith("s")` 的 token 额外追加 `token[:-1]`
     （`files`→{files,file}）—— **仅此一条**，禁止通用词形还原（`status` 不得变 `statu`
     之外的任何东西，实际就放行）。
     **禁止前后缀启发式**：`direction`（含子串 `dir`）、`south`（含子串 `out`）都必须放行，
     所以实现只能是词元集合交集，不能是字符串包含。
     （`profile` **不再**承担这个反例角色：schema 里 6 条命令明写收 `/path/to/profile.icc`
     —— `file.new`、`edit.profileInfo`、`image.mode.rgb|grayscale|cmyk|lab` —— 它必须
     进词表，见 #27。反例载体换成 `direction` / `south`，目标「不许裸 substring」不变。）
  4. **绝对判据 ``^[A-Za-z]:(?![0-9])``**（裁决 3 变更）：盘符相对 ``C:secret.png``
     算绝对（冒号后是字母），必须送 roots；``R:1``/``A:0`` 这类通道记号放行。
     仍不误伤 ``layer:3``（第二字符不是 ``:``）与 ``report~1.png``（相对，不送检）。
  5. **按键分方向**（裁决 3，仅 ``direction="any"`` 时生效）：``input/src/source`` →
     read_roots；``output/out/dest/save/export`` → write_roots；``path/file/target`` →
     read_roots ∪ write_roots。显式 ``direction="read"``/``"write"`` **覆盖**该映射；
     键名 token 化后出现多个方向时，只采纳非 ``any`` 的那个，冲突则取并集。
  6. ``validate_steps(steps, spec, policy, catalog_ids=..., roots=None)`` 透传 roots
     且旧的 3/4 个位置参数调法仍可用；``validate_tool_kwargs(spec, policy, kwargs,
     roots, allowed=...)`` 的 roots 不得被吞掉（「形参吞掉」族缺陷）。

纪律：全部是纯函数级断言，不 mock ``os.path``、不 monkeypatch 文件系统、不 spawn
任何进程、不读写真实文件。用的路径一律是**不存在的盘符/目录**（``C:\\state\\...``），
``resolve_and_check`` 里的 ``os.path.exists`` 恒为假，所以不会因本机文件布局而抖动。

[FACT] 命令白名单选 ``edit.fill`` —— 它在 ``spec.STATIC_ALLOWED_EXACT`` 里，
``check_command_allowed`` 在无 discovery 缓存时会放行它；这样「路径被拒」的断言
才不会被更早的 ``POLICY_VIOLATION: 命令未在已知命名空间白名单内`` 抢先触发。
另：本文件多数用例显式传 ``catalog_ids={\"edit.fill\"}``，让白名单判定**不依赖**
静态表内容，将来静态表调整也不会把路径断言掩盖掉。
"""
from __future__ import annotations

import pytest

# 一律用不存在的假目录：resolve_and_check 的 os.path.exists() 恒 False，
# 因此这些用例永远不会碰真实磁盘，也不需要 tmp 目录。
DATA_DIR = r"C:\state\artcraft_ops\data"
CACHE_DIR = r"C:\state\artcraft_ops\cache"
USER_READ = "D:/Users/Alice/Pictures"          # 用户显式授权：只读
USER_WRITE = "D:/Users/Alice/Export"           # 用户显式授权：只写
WIN_INI = r"C:\Windows\win.ini"                # 经典越界目标（模型最想读的文件）

SAFE_CMD = "edit.fill"                          # 白名单内、参数无副作用
CATALOG = frozenset({SAFE_CMD, "image.imageSize"})


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _detail(exc: BaseException) -> str:
    """断言失败时把「固定码 + 类名 + 实际字段」一次带出来，diff 一眼定位。"""
    fields = getattr(exc, "fields", None)
    return f"{exc!r} [{type(exc).__name__}] fields={fields!r}"


def _cmd_policy(impl):
    return impl("spec").CommandPolicy()


def _spec(impl):
    return impl("app_registry").get_spec("photocraft")


def _step(impl, params: dict):
    return impl("spec").Step(cmd=SAFE_CMD, params=params)


def _policy(impl):
    """默认策略：只授权 data/ 与 cache/（fail-closed，与线上入口一致）。"""
    return impl("roots").default_policy(DATA_DIR, CACHE_DIR, {})


def _policy_with_user_dirs(impl):
    """双开关打开：读方向多一个用户目录，写方向多另一个用户目录。"""
    return impl("roots").default_policy(DATA_DIR, CACHE_DIR, {
        "allow_user_dirs": True,
        "user_read_roots": [USER_READ],
        "user_write_roots": [USER_WRITE],
    })


def _expect_rejected(errors_mod, call, *, arg: str, code: str = "") -> None:
    """跑一次调用并要求抛 ``PolicyViolation``；``code`` 非空时错误消息必须含它。

    [为什么不用 `pytest.raises`] 它「根本没抛」时只报一句 `DID NOT RAISE`，
    **不带入参**。循环里的断言（一个 `for` 跑 4~7 条入参）全靠这句才能定位到
    具体哪条漏了 —— 变异验证时尤其明显：同一用例被不同变异打红，只报用例名
    分不清是谁干的。异常路径仍用 `pytest.raises`（那里语义更直白）。
    """
    try:
        call()
    except errors_mod.PolicyViolation as exc:
        if code:
            assert code in str(exc), \
                f"入参 {arg!r} 被拒但错误码不含 {code}：{_detail(exc)}"
        return
    pytest.fail(
        f"入参 {arg!r} 未被拦下（期望抛 PolicyViolation，错误码 {code or '不限'}）",
        pytrace=False,
    )


# ---------------------------------------------------------------------------
# 1. 正常：绝对路径落在 roots 内 -> 放行
# ---------------------------------------------------------------------------
def test_step_params_abs_path_inside_roots_allowed(impl):
    """正常：step.params.path 是 data root 内的绝对路径 -> validate_steps 不抛。

    守的是这条链本身（若实现把 roots 透传后**误伤**合法路径，用户立刻不能干活）。
    """
    sequence = impl("sequence")
    steps = [_step(impl, {"path": DATA_DIR + r"\layers.png"})]

    sequence.validate_steps(
        steps, _spec(impl), _cmd_policy(impl), CATALOG, roots=_policy(impl),
    )


# ---------------------------------------------------------------------------
# 2. 错误：绝对路径落在 roots 外 -> PATH_OUTSIDE_ROOTS
# ---------------------------------------------------------------------------
def test_step_params_abs_path_outside_roots_rejected(impl):
    """错误（本缺陷的正身）：params.path 指向 roots 外 -> 抛且带 PATH_OUTSIDE_ROOTS。

    `C:\\Windows\\win.ini` 是这条缺陷最具体的利用方式：读系统文件 + 导出。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    steps = [_step(impl, {"path": WIN_INI})]

    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            steps, _spec(impl), _cmd_policy(impl), CATALOG, roots=_policy(impl),
        )
    assert "PATH_OUTSIDE_ROOTS" in str(ei.value), _detail(ei.value)


# ---------------------------------------------------------------------------
# 3. 边界：roots=None -> 与旧行为逐字节一致（不做任何路径检查）
# ---------------------------------------------------------------------------
def test_step_params_roots_none_keeps_legacy_behavior(impl):
    """边界：roots=None 时同样的非法绝对路径**不抛**（显式钉住旧行为）。

    这条**故意**是绿的：它是向后兼容闸门。删掉它，将来有人把 roots 变成必填参数
    就会静默炸掉所有只给 3 个位置参数的调用点。
    """
    sequence = impl("sequence")
    sequence.validate_steps(
        [_step(impl, {"path": WIN_INI})], _spec(impl), _cmd_policy(impl), CATALOG,
    )
    # 直接调 validate_params 也一样：roots 缺省 = 不做路径检查
    sequence.validate_params({"path": WIN_INI})


# ---------------------------------------------------------------------------
# 4. 边界：SVG path data 不是文件路径 -> 放行
# ---------------------------------------------------------------------------
def test_svg_path_data_not_treated_as_file_path(impl):
    """边界：`M0 0 L10 10 Z` 是 SVG path data，不是文件路径 -> 不得抛。

    `path` 这个 key 在矢量/绘图命令里天然装的是路径指令串；过度拦截会把
    「画一个三角形」判成安全违规。
    """
    sequence = impl("sequence")
    for svg in ("M0 0 L10 10 Z", "M 10.5,20.25 C 30 40 50 60 70 80 Z"):
        sequence.validate_steps(
            [_step(impl, {"path": svg})], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=_policy(impl),
        )


# ---------------------------------------------------------------------------
# 5. 错误：相对路径含 `..` -> 拒（cwd 是 CLI 安装目录，会逃出 roots）
# ---------------------------------------------------------------------------
def test_relative_parent_traversal_in_params_rejected(impl):
    """错误：`../../secret.png` 必须拒 —— CLI 的 cwd 是**安装目录**不是 data 目录。

    [FACT] ``jobs._cwd_for()`` 返回 `E:\\Program Files\\Artcraft\\PhotoCraft` 一类
    安装路径。所以相对路径既不能按 roots 解析、也不能放过：`..` 拼出去就是
    Program Files 上两级（通常整个盘）。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    for bad in (r"../../secret.png", r"..\..\secret.png", r"sub\..\..\secret.png"):
        with pytest.raises(errors.PolicyViolation) as ei:
            sequence.validate_steps(
                [_step(impl, {"path": bad})], _spec(impl), _cmd_policy(impl), CATALOG,
                roots=_policy(impl),
            )
        msg = str(ei.value)
        assert ".." in msg or "PATH_UNSAFE" in msg or "PATH_OUTSIDE_ROOTS" in msg, \
            f"入参 {bad!r} 被拒了但消息没说明 `..` 不允许：{_detail(ei.value)}"


# ---------------------------------------------------------------------------
# 6. 边界：URL 不是文件路径 -> 放行
# ---------------------------------------------------------------------------
def test_url_in_params_not_treated_as_file_path(impl):
    """边界：`https://example.com/a.png` 不得被当成绝对路径拦下。

    远程取图是正常需求；把 URL 判成越界会让「下载一张图再编辑」直接不可用。
    """
    sequence = impl("sequence")
    for url in ("https://example.com/a.png", "http://127.0.0.1:8080/x.svg"):
        sequence.validate_steps(
            [_step(impl, {"path": url})], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=_policy(impl),
        )


# ---------------------------------------------------------------------------
# 7. 正常：direction="any" 下按键分方向，读/写根各就各位
# ---------------------------------------------------------------------------
def test_direction_any_unions_read_and_write_roots(impl):
    """正常：读目录文件走 `input`、写目录文件走 `out`，direction="any" 下都不抛。

    守的是「读根与写根是两套」：裁决 3 按键映射后，`input` 只能走 read_roots
    （若被拿去比 write_roots，用户授权了只读目录却连读都不行）、`out` 只能走
    write_roots（若被拿去比 read_roots，导出直接不可用）。两种错法都会让本函数红。
    """
    sequence = impl("sequence")
    policy = _policy_with_user_dirs(impl)
    steps = [
        _step(impl, {"input": USER_READ + "/a.png"}),
        _step(impl, {"out": USER_WRITE + "/b.png"}),
    ]

    sequence.validate_steps(
        steps, _spec(impl), _cmd_policy(impl), CATALOG, roots=policy,
    )
    # 单条直接调 validate_params，direction 缺省值必须等价于 "any"
    sequence.validate_params({"out": USER_WRITE + "/b.png"}, policy)
    sequence.validate_params({"input": USER_READ + "/a.png"}, policy)


def test_direction_read_does_not_accept_write_only_root(impl):
    """错误（裁决 3 明示保留）：显式 direction="read" **覆盖**按键映射。

    `input` 若能用只写目录里的文件，`allow_user_dirs` 的读写分权就形同虚设；
    按键映射只在 direction="any" 时生效，显式方向永远压过映射。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_params(
            {"input": USER_WRITE + "/b.png"}, _policy_with_user_dirs(impl), "read",
        )
    msg = str(ei.value)
    assert "PATH_OUTSIDE_ROOTS" in msg or "PolicyViolation" in _detail(ei.value), \
        _detail(ei.value)


def test_non_absolute_value_never_sent_to_roots(impl):
    """边界（裁决 2 明示保留）：只有「像绝对路径」的值才送 roots 检查。

    守的是契约里最容易实现走样的那一半：`{"file": "report~1.png"}` 会被
    `roots._assert_safe_segments` 判成 8.3 短名（含 `~`）而 `PATH_UNSAFE`，
    `{"target": "layer:3"}` 会被判成 ADS（含 `:`）。它们都是**相对**值，
    按契约根本不该进 `resolve_and_check`。若实现图省事「一律送检」，
    上面两条会被误杀 —— 这正是本函数要抓的过度拦截。
    """
    sequence = impl("sequence")
    for params in ({"file": "report~1.png"}, {"target": "layer:3"}):
        sequence.validate_steps(
            [_step(impl, params)], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=_policy(impl),
        )


# ---------------------------------------------------------------------------
# 8. 兼容：旧的三位置参数调法仍然可用
# ---------------------------------------------------------------------------
def test_validate_steps_three_positional_args_still_works(impl):
    """兼容：`validate_steps(steps, spec, policy)` 只给 3 个参数不得 TypeError。

    [FACT] 现有调用点里既有 3 参也有 4 参写法；新增 `roots` 只能追加在末尾
    （带默认值），绝不能插到 `catalog_ids` 前面。
    """
    sequence = impl("sequence")
    sequence.validate_steps(
        [_step(impl, {"path": "layers.png"})], _spec(impl), _cmd_policy(impl),
    )
    # 4 位置参（线上 __init__.py 的写法）同样必须继续可用
    sequence.validate_steps(
        [_step(impl, {"path": "layers.png"})], _spec(impl), _cmd_policy(impl), CATALOG,
    )


# ---------------------------------------------------------------------------
# 9. 错误：validate_tool_kwargs 收了 roots 就必须真的用上（形参吞掉）
# ---------------------------------------------------------------------------
def test_validate_tool_kwargs_passes_roots_through(impl):
    """错误：`validate_tool_kwargs(..., roots)` 里的 roots 不得被吞掉。

    它当前是死代码（无调用点），但契约要守住：签名里既然声明了 roots，
    `steps[i].params.path` 越界就必须被拒，否则将来一接线就是同一个漏洞。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    kwargs = {"steps": [{"cmd": SAFE_CMD, "params": {"path": WIN_INI}}]}

    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_tool_kwargs(
            _spec(impl), _cmd_policy(impl), kwargs, _policy(impl),
            frozenset({SAFE_CMD}),
        )
    assert "PATH_OUTSIDE_ROOTS" in str(ei.value), _detail(ei.value)


# ---------------------------------------------------------------------------
# 12-15. 裁决 1/2 的守卫：递归下降 + 深度 fail-closed + 盘符相对
# ---------------------------------------------------------------------------
def _nest(levels: int, leaf):
    """造 ``levels`` 层 ``{"lvl": ...}`` 嵌套，最内层放 ``leaf``。"""
    value = leaf
    for _ in range(levels):
        value = {"lvl": value}
    return value


def test_nested_dict_path_is_checked(impl):
    """错误（裁决 1）：`{"options": {"path": ...}}` 不得因为「不在顶层」就逃逸。

    [真旁路] argv 侧 `params` 是 `json.dumps` **整段**传给 CLI 的，
    所以嵌套层里的路径真的会到 CLI —— 只查顶层的实现等于没修。
    先钉住「浅层嵌套的**合法**路径不得被误杀」，再钉「浅层嵌套的越界路径必须拒」，
    两半都在，递归才不会退化成「一律拒」或「一律放行」。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    # 合法：嵌套一层的相对文件名 —— 放行
    sequence.validate_steps(
        [_step(impl, {"options": {"path": "ok.png"}})], _spec(impl),
        _cmd_policy(impl), CATALOG, roots=_policy(impl),
    )
    # 越界：同一个位置换成 win.ini —— 必须拒
    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"options": {"path": WIN_INI}})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=_policy(impl),
        )
    assert "PATH_OUTSIDE_ROOTS" in str(ei.value), _detail(ei.value)


def test_nested_list_paths_are_checked(impl):
    """错误（裁决 1）：`{"files": [win.ini, "ok.png"]}` —— list 里逐个查，越界即拒。

    list 是批量命令最自然的形状（多文件编辑/批量导出）。只要其中一个元素越界
    就必须整批拒：放过去等于让模型用「混一个合法文件」的方式夹带系统文件。

    [契约澄清 2026-11-08] list 里的 str 元素**一律**按路径检查，不看父 key 是否在
    映射表里（`files`/`images`/`layers`/`inputs` 这类批量键名无法穷举）；父 key
    未映射时方向取 read ∪ write —— 按契约，`files` 里放**只读目录**与**只写目录**
    的绝对路径都必须放行，下面两半分别钉住。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    # 合法：全是相对文件名 —— 放行
    sequence.validate_steps(
        [_step(impl, {"files": ["ok.png", "b.png"]})], _spec(impl),
        _cmd_policy(impl), CATALOG, roots=_policy(impl),
    )
    # 越界：第一个元素就是 win.ini —— 整批拒
    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"files": [WIN_INI, "ok.png"]})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=_policy(impl),
        )
    assert "PATH_OUTSIDE_ROOTS" in str(ei.value), _detail(ei.value)

    # 非 str 元素跳过：`[None, 3]` 不是路径，不能因为类型不对就炸（不该误杀）
    sequence.validate_steps(
        [_step(impl, {"files": [None, 3, "ok.png"]})], _spec(impl),
        _cmd_policy(impl), CATALOG, roots=_policy(impl),
    )

    # 父 key 未映射 → 方向取并集：只读目录 与 只写目录 的文件都必须放行
    user_policy = _policy_with_user_dirs(impl)
    sequence.validate_steps(
        [_step(impl, {"files": [USER_READ + "/a.png", USER_WRITE + "/b.png"]})],
        _spec(impl), _cmd_policy(impl), CATALOG, roots=user_policy,
    )


def test_drive_relative_path_treated_as_absolute(impl):
    """错误（裁决 2）：`C:secret.png` 是盘符相对（= C 盘 cwd），必须按绝对路径查。

    它不匹配 `^[A-Za-z]:[\\\\/]`（`C:` 后面紧跟的不是分隔符），旧判据会把它当
    普通相对值放行 —— 而 Windows 上它会落到 C 盘当前目录，绕过 roots。

    **口径提醒（别去"对齐"）**：闸门用的是 `^[A-Za-z]:(?![0-9])`，比
    `roots.normalize_win_path` 的 `_DRIVE_RE`（`^[A-Za-z]:`）**多一个负向前瞻**，
    这是**故意不同口径**：#17 要求 `R:1`/`A:0` 这类通道记号在闸门处放行，
    而 `R:1` 一旦真的进 roots 会被 `_DRIVE_RE` 当盘符。见 #17。

    注意仍不得误伤 `layer:3`（第二个字符不是 `:`）。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"path": "C:secret.png"})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=_policy(impl),
        )
    assert "PATH_OUTSIDE_ROOTS" in str(ei.value), _detail(ei.value)


def test_deeply_nested_params_rejected_fail_closed(impl):
    """错误 + 可用性回归（裁决 2 变更：深度上限 8 → 16）。

    两半都在：
      * **20 层 → 拒**，消息含「嵌套过深」（fail-closed，静默停止等于把下一层变旁路）。
        用 20 层而不是 17 层：「上限 16」的计数起点（根算 0 还是 1、最内层那个装
        `path` 的 dict 算不算）契约没定义，20 层在任何口径下都超出 —— 上一轮
        `nest=8 DENY` 的 off-by-one 就是没这么做才踩到的。
      * **真实关键帧结构 → 放行**。实测在旧上限 8 下 `{"wrap":{"wrap":{"animation":
        {"tracks":[{"keys":[{"k":[...],"v":[...]}]}]}}}}` 是被**误杀**的（可用性 bug）：
        `tracks→[dict]→keys→[dict]→k` 本身就吃掉 8 层容器，任何正常包装都超限。
        上限提到 16 后这条必须绿。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    deep = _nest(20, {"path": "ok.png"})
    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, deep)], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=_policy(impl),
        )
    msg = str(ei.value)
    assert "嵌套过深" in msg, f"期望消息含「嵌套过深」，实际 {_detail(ei.value)}"

    # 真实形态：关键帧 + 两层无关包装 —— 必须放行（旧上限 8 下实测被拒）
    realistic = {"wrap": {"wrap": {"animation": {"tracks": [
        {"keys": [{"k": [0, 0], "v": [1, 1]}]},
    ]}}}}
    sequence.validate_steps(
        [_step(impl, realistic)], _spec(impl), _cmd_policy(impl), CATALOG,
        roots=_policy(impl),
    )
    # 极浅的嵌套同样不得被深度规则误伤
    sequence.validate_steps(
        [_step(impl, _nest(2, {"path": "ok.png"}))], _spec(impl),
        _cmd_policy(impl), CATALOG, roots=_policy(impl),
    )


# ---------------------------------------------------------------------------
# 16-17. 裁决 1/3 的变更：键名 token 化 + 绝对判据负向前瞻
# ---------------------------------------------------------------------------
def test_camel_and_snake_case_path_keys_are_checked(impl):
    """错误（裁决 1 变更）：键名必须 token 化，`outPath`/`srcFile`/`output_path` 都得查。

    [FACT · 实测旧实现] 小写精确匹配下这三个键**全部放行**：
        ALLOW {'outPath': 'C:/Windows/win.ini'}
        ALLOW {'srcFile': 'C:/Windows/win.ini'}
        ALLOW {'output_path': 'C:/Windows/win.ini'}
    键集命中是闸门，递归救不回来 —— CLI 参数用 `outPath`/`srcFile` 是极常见写法，
    这条旁路比原缺陷更现实。

    反向一半钉住「**不许裸 substring**」：`direction` 含子串 `dir`、`south` 含子串
    `out`，但两者的 token 集合分别只有 `{direction}` / `{south}`，与词表无交集 →
    必须放行；用裸 substring 的实现会把它们误杀。

    [2026-11-08 变更] 这一半原来用 `profile` 承载，**已换载体**：`profile` 现在必须
    被查（见 #27 与 `test_profile_key_three_states`），继续拿它当反例会把这轮修法
    直接打红。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    for params in (
        {"outPath": WIN_INI},      # tokens {out, path}  -> out=write
        {"srcFile": WIN_INI},      # tokens {src, file}  -> src=read
        {"output_path": WIN_INI},  # tokens {output, path} -> output=write
    ):
        with pytest.raises(errors.PolicyViolation) as ei:
            sequence.validate_steps(
                [_step(impl, params)], _spec(impl), _cmd_policy(impl), CATALOG,
                roots=_policy(impl),
            )
        assert "PATH_OUTSIDE_ROOTS" in str(ei.value), \
            f"键 {list(params)[0]!r} 未被识别为路径键：{_detail(ei.value)}"

    # 反向：`direction` / `south` 不是路径键 —— token 集合只有 {direction} / {south}，
    # 与词表无交集。裸 substring（`dir` / `out`）的实现会在这里误杀。
    for params in ({"direction": WIN_INI}, {"south": WIN_INI}):
        sequence.validate_steps(
            [_step(impl, params)], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=_policy(impl),
        )


def test_drive_relative_lookahead_keeps_channel_notation(impl):
    """错误/边界（裁决 3 变更）：绝对判据加负向前瞻 `^[A-Za-z]:(?![0-9])`。

    两半都在：
      * **放行** `{"target": "R:1"}` / `{"target": "A:0"}` —— 单字母 + 冒号 + 数字是
        通道/曲线记号，不是盘符。旧判据 `^[A-Za-z]:` 实测把它们判成盘符相对并拒
        （`DENY {'target': 'R:1'} -> PATH_OUTSIDE_ROOTS`），是可用性 bug。
      * **仍拒** `{"path": "C:secret.png"}` —— 冒号后是字母，盘符相对语义不变
        （与第 14 条同一个字面量，故意重复一次做交叉校验：前瞻不得把它一起放掉）。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    for value in ("R:1", "A:0"):
        sequence.validate_steps(
            [_step(impl, {"target": value})], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=_policy(impl),
        )

    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"path": "C:secret.png"})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=_policy(impl),
        )
    assert "PATH_OUTSIDE_ROOTS" in str(ei.value), _detail(ei.value)


def test_dir_suffixed_keys_follow_token_direction(impl):
    """错误/正常（#18）：带方向前缀的目录复合键 `dest_dir` / `input_dir` 必须查，且方向正确。

    `dest_dir` → tokens {dest, dir} → dest=write；`input_dir` → {input, dir} →
    input=read —— 方向来自**方向词**，不是来自 `dir`（`dir` 本身方向 any）。

    四半都钉住，防止实现「只看 token 命中、方向一律并集」：
      * `dest_dir` = `C:\\Windows` → 拒（越界）
      * `dest_dir` = 写根内文件 → 放行（write 方向被正确采用）
      * `input_dir` = 只读目录内 → 放行（read 方向被正确采用）
      * `input_dir` = 只写目录内 → 拒（read 方向没被当成并集）
    最后一条是这套断言里最关键的一半：若方向退化成并集，它会变绿而漏洞复现。

    裸目录词（`dir`/`folder`/`filename`/`dst`…）在 #19 单独钉。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    user_policy = _policy_with_user_dirs(impl)

    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"dest_dir": r"C:\Windows"})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=_policy(impl),
        )
    assert "PATH_OUTSIDE_ROOTS" in str(ei.value), _detail(ei.value)

    sequence.validate_steps(
        [_step(impl, {"dest_dir": USER_WRITE + "/x.png"})], _spec(impl),
        _cmd_policy(impl), CATALOG, roots=user_policy,
    )
    sequence.validate_steps(
        [_step(impl, {"input_dir": USER_READ + "/in"})], _spec(impl),
        _cmd_policy(impl), CATALOG, roots=user_policy,
    )
    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"input_dir": USER_WRITE + "/in"})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=user_policy,
        )
    assert "PATH_OUTSIDE_ROOTS" in str(ei.value), _detail(ei.value)


def test_bare_directory_words_are_checked(impl):
    """错误/正常（#19）：裸目录词必须被查，且「不许前后缀启发式」。

    [契约 2026-11-08] `_PATH_KEY_DIRECTION` 增设 `dir` / `directory` / `folder` /
    `dst` / `filename` / `filepath`（方向全 any）。修掉的正是最后一个开口旁路：
    这些键 token 化后只有一个词元、且旧词表里没有 → `{"dir": "C:\\Windows"}`
    曾经**完全放行**。

    反向一半钉住实现方式必须是**词元集合交集**，不能是字符串包含/前后缀匹配：
    `direction` token 化是 `{direction}`，既不是 `dir` 也不以 `dir` 为词元 ——
    启发式实现会把 `{"direction": ...}` 误杀（同理 `profile` 以 `file` 结尾，见 #16）。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    for params in (
        {"dir": WIN_INI},
        {"directory": WIN_INI},
        {"folder": WIN_INI},
        {"filename": WIN_INI},
        {"dst": WIN_INI},
        {"filepath": WIN_INI},
    ):
        with pytest.raises(errors.PolicyViolation) as ei:
            sequence.validate_steps(
                [_step(impl, params)], _spec(impl), _cmd_policy(impl), CATALOG,
                roots=_policy(impl),
            )
        assert "PATH_OUTSIDE_ROOTS" in str(ei.value), \
            f"键 {list(params)[0]!r} 未被识别为路径键：{_detail(ei.value)}"

    # 反向：`direction` 不是 `dir`（词元级比对，不是前后缀启发式）
    sequence.validate_steps(
        [_step(impl, {"direction": WIN_INI})], _spec(impl), _cmd_policy(impl), CATALOG,
        roots=_policy(impl),
    )


def test_plural_scalar_path_keys_are_checked(impl):
    """错误（#20）：复数标量键也必须查 —— `files` 也可以装**单个** str。

    `_key_tokens` 对 `len > 3 且 endswith("s")` 的 token 额外追加 `token[:-1]`。
    于是 `files`→{files,file}、`paths`→{paths,path}、`outputs`→{outputs,output}
    （write）、`inputs`→{inputs,input}（read）全部命中键集。

    [为什么必须有这一条] #13 只钉了「`files` 装 list」；模型可以换成
    `{"files": "C:\\Windows\\win.ini"}`（标量）—— 若只做 list 分支检查，
    标量形态就是完整旁路。

    反向一半钉住「**不是通用词形还原**」：`status` 去掉尾 `s` 得 `statu`，
    不在键集 → 必须放行。若实现用了 `inflect`/`stem` 之类的一般化还原，
    这条会红。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    for params in (
        {"files": WIN_INI},      # -> {files, file}   any
        {"paths": WIN_INI},      # -> {paths, path}   any
        {"outputs": WIN_INI},    # -> {outputs, output} write
        {"inputs": WIN_INI},     # -> {inputs, input}  read
    ):
        with pytest.raises(errors.PolicyViolation) as ei:
            sequence.validate_steps(
                [_step(impl, params)], _spec(impl), _cmd_policy(impl), CATALOG,
                roots=_policy(impl),
            )
        assert "PATH_OUTSIDE_ROOTS" in str(ei.value), \
            f"复数键 {list(params)[0]!r} 未被识别：{_detail(ei.value)}"

    # 反向：`status` → 去尾 s = `statu`，不是路径键 → 放行（禁止通用词形还原）
    sequence.validate_steps(
        [_step(impl, {"status": WIN_INI})], _spec(impl), _cmd_policy(impl), CATALOG,
        roots=_policy(impl),
    )


# ---------------------------------------------------------------------------
# 21-25. 云严审查发现的 critical 与同类旁路
# ---------------------------------------------------------------------------
def test_root_relative_single_separator_rejected(impl):
    """错误（#21 · **critical**）：`\\Windows\\win.ini` / `/Windows/win.ini` 必须拒。

    [真缺陷] 前半段的绝对判据只认 `\\\\`（双反斜杠 UNC）与 `//`，**单**个前导
    分隔符被漏掉：`\\Windows\\win.ini` 在 Windows 上是「当前盘根相对」——
    落到 `C:\\Windows\\win.ini`，与直接写绝对路径等价；`/Windows/win.ini` 同理
    （CLI 接受正斜杠）。旧实现把它当普通相对值（不含 `..`）直接放行 = 完整旁路。

    反向一半同样重要：`sub/ok.png` 是**真正**的普通相对子路径，必须放行 ——
    不能为了堵这条路把正常的相对文件名一起打红。

    roots 侧的同一条防线在 `tests/test_roots.py::
    test_root_relative_single_separator_rejected_at_roots_layer`（21b）：两层是串联的，
    只改一边就是「看起来有闸门、其实空转」。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    for bad in (r"\Windows\win.ini", "/Windows/win.ini", r"\??\C:\Windows\win.ini"):
        with pytest.raises(errors.PolicyViolation) as ei:
            sequence.validate_steps(
                [_step(impl, {"path": bad})], _spec(impl), _cmd_policy(impl), CATALOG,
                roots=_policy(impl),
            )
        msg = str(ei.value)
        assert "PATH_OUTSIDE_ROOTS" in msg or "PATH_UNSAFE" in msg, \
            f"入参 {bad!r} 被拒但消息不含固定码：{_detail(ei.value)}"

    # 反向：普通相对子路径必须放行（含分隔符但无前导分隔符、无 `..`）
    for ok in ("sub/ok.png", r"sub\ok.png"):
        sequence.validate_steps(
            [_step(impl, {"path": ok})], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=_policy(impl),
        )


def test_new_vocabulary_path_keys_are_checked(impl):
    """错误（#22）：云严补的第二批词表 `log`/`template`/`mesh`/`into` 也必须是路径键。

    [FACT] 这些键在真实 CLI 里就是路径字段（日志文件、模板文件、网格缓存、
    「导入到某目录」），但 token 化后与旧词表无交集 → 旧实现全部放行。

    反向一半：`{"log": "err.txt"}`（相对值）必须放行 —— 词表扩张不能变成
    「见到这些键就拒」。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    for params in (
        {"log": WIN_INI},
        {"errorLog": WIN_INI},      # 驼峰：{error, log} -> log
        {"template": WIN_INI},
        {"mesh": WIN_INI},
        {"into": WIN_INI},
    ):
        with pytest.raises(errors.PolicyViolation) as ei:
            sequence.validate_steps(
                [_step(impl, params)], _spec(impl), _cmd_policy(impl), CATALOG,
                roots=_policy(impl),
            )
        assert ("PATH_OUTSIDE_ROOTS" in str(ei.value)
                or "PATH_UNSAFE" in str(ei.value)), \
            f"键 {list(params)[0]!r} 未被识别为路径键：{_detail(ei.value)}"

    # 反向：相对值是正常用法
    sequence.validate_steps(
        [_step(impl, {"log": "err.txt"})], _spec(impl), _cmd_policy(impl), CATALOG,
        roots=_policy(impl),
    )


def test_nested_dict_inherits_parent_path_tokens(impl):
    """错误（#23）：`{"path": {"x": ...}}` —— 子节点没有键名，必须继承父路径词元。

    [真缺陷] 递归时子 dict 的每个节点用**自己的**键名算 token，`{"x": ...}` 落空
    → 整棵子树放行。于是 `{"path": {"x": "C:\\\\Windows\\\\win.ini"}}` 是完整旁路，
    而 `json.dumps` 会让它原样到 CLI。

    反向一半：`{"path": {"points": [[0,0],[1,1]]}}` 是几何对象（`path` 键装的是
    路径形状数据），非 str 元素必须跳过，不能误杀 —— 这也是当初「`path` 键不能
    一律当文件路径」的同一个理由。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"path": {"x": WIN_INI}})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=_policy(impl),
        )
    assert ("PATH_OUTSIDE_ROOTS" in str(ei.value)
            or "PATH_UNSAFE" in str(ei.value)), _detail(ei.value)

    # 同一机制对**短语复合键**也必须生效：`outPaths` → {out, paths, path}，
    # 子节点 `a` 自己与键集无交集，只有继承父词元才能拦（云严指出这形态无断言）。
    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"outPaths": {"a": WIN_INI}})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=_policy(impl),
        )
    assert ("PATH_OUTSIDE_ROOTS" in str(ei.value)
            or "PATH_UNSAFE" in str(ei.value)), _detail(ei.value)

    # 反向：几何对象不得被误杀
    sequence.validate_steps(
        [_step(impl, {"path": {"points": [[0, 0], [1, 1]]}})], _spec(impl),
        _cmd_policy(impl), CATALOG, roots=_policy(impl),
    )


def test_parent_segment_with_trailing_space_rejected(impl):
    """错误（#24）：`a\\.. \\x` —— `..` 段带尾空格仍要拒。

    [真缺陷] 段比对是精确 `seg == ".."`，`".. "` 落空 → 放行。
    [诚实标注] 关于 Win32 语义，我和云严**各错过一半**，结论以实测为准：
      * `ntpath.abspath`（GetFullPathName）**不**折叠 `".. "`：原样返回，
        所以「Windows 会剥段尾空格、因此 `".. "` 等价 `..`」这个推导**不成立**
        （云严已撤回）；
      * 云严用 `os.path.realpath` + `listdir` 实测：只有**字面** `..` 落父目录，
        `".. "` / `".. ."` / `" .."` / `"... "` 这一族全部落**当前目录**
        （`listdir` 报 PATH_NOT_FOUND）—— 即它们根本逃不出去，「能逃出去」也不成立；
      * 实现用**无参数** `seg.strip() == ".."`（两侧剥、按 `isspace()` 剥），因此
        整个空白族都拒，不只空格与制表符。[S10 订正] 这里原来写的是
        `seg.strip(" \t")`，与实现不符 —— 实现从来就是无参数 `str.strip()`。
        主理人裁决**保留**这个偏严行为：它是**偏严兜底，不是 Windows 语义推导**；
        拒它既不是漏判、也不是误杀真实文件（真实文件叫不到这些名字），成本为零，
        属 **fail-closed 副作用**，**不要**据此去改实现；
      * **验证方式的红线**：别用 `os.path.isdir` 判这类路径 —— 它会误报 `True`，
        不证明解析结果（本闸门第一版复核就踩过）。要用 `realpath`/`st_ino`。
      * 我这条断言**当时只钉尾随空格**这一形态（`a\\.. \\x` / `a/.. /\\x` /
        `a\\..  \\x`）。[S10 订正] 「前导/制表符没有契约依据」这句已过时：空白族由
        #32（`test_parent_segment_with_mixed_whitespace_rejected`）统一收全，
        `roots._assert_safe_segments` 也在 S10 改为整段 `str.strip()`，
        前导与制表符一并拒。

    反向一半：`a\\b\\x.png` 正常相对路径必须放行。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    for bad in (r"a\.. \x", r"a/.. /\x", r"a\..  \x"):
        with pytest.raises(errors.PolicyViolation) as ei:
            sequence.validate_steps(
                [_step(impl, {"path": bad})], _spec(impl), _cmd_policy(impl), CATALOG,
                roots=_policy(impl),
            )
        # 固定码允许三种：`..` 段族走 POLICY_VIOLATION（`_violate`），送 roots 的
        # 才有 PATH_OUTSIDE_ROOTS / PATH_UNSAFE。契约只要求「拒」，不锁具体码。
        msg = str(ei.value)
        assert ("PATH_OUTSIDE_ROOTS" in msg or "PATH_UNSAFE" in msg
                or ".." in msg), \
            f"入参 {bad!r} 被拒但消息看不出 `..` 语义：{_detail(ei.value)}"

    # 反向：正常相对路径
    sequence.validate_steps(
        [_step(impl, {"path": r"a\b\x.png"})], _spec(impl), _cmd_policy(impl), CATALOG,
        roots=_policy(impl),
    )


def test_mixed_case_path_key_is_checked(impl):
    """错误（#25）：混写大小写键 `pAtH` 必须被查，同时不许打翻 #16 的反向一半。

    键名比对必须先小写再分词（`_key_tokens` 已经 lower），所以 `pAtH`/`PATH`/`Path`
    都等价于 `path`；模型换个大小写就能绕过的话，整条链等于没修。

    反向一半：`direction` / `south` 仍必须放行 —— 这条与 #16 重复是**故意的**：
    任何新增的大小写/词形（含「整键压缩」`pAtH`→`path`）处理都最容易在这里把
    `direction`（含 `dir`）/ `south`（含 `out`）误杀，重复断言能让回归立刻定位。
    与 #16 同理，反例载体己从 `profile` 换成这两个键（`profile` 本轮进词表，见 #27）。
    """
    sequence = impl("sequence")
    errors = impl("errors")

    for key in ("pAtH", "PATH", "Path", "paTh", "FiLe"):
        with pytest.raises(errors.PolicyViolation) as ei:
            sequence.validate_steps(
                [_step(impl, {key: WIN_INI})], _spec(impl), _cmd_policy(impl), CATALOG,
                roots=_policy(impl),
            )
        assert ("PATH_OUTSIDE_ROOTS" in str(ei.value)
                or "PATH_UNSAFE" in str(ei.value)), \
            f"键 {key!r} 未被识别为路径键：{_detail(ei.value)}"

    for params in ({"direction": WIN_INI}, {"south": WIN_INI}):
        sequence.validate_steps(
            [_step(impl, params)], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=_policy(impl),
        )


# ---------------------------------------------------------------------------
# 26（纯函数侧）. `--new` 通道用显式路径键（`extra_path_keys`），不是全局严判
# ---------------------------------------------------------------------------
def test_new_doc_channel_uses_explicit_path_keys(impl):
    """正/边界/错误（#26 配套）：路径键按通道补，不是「字符串一律按路径判」。

    [为什么需要这条] `profile` 这个 key 在两个通道里语义**不同**：
      * step params 里它是**预设名**（`{"profile": "Default"}` / `sRGB` / `working`）；
      * `--new` payload 里它是**ICC 文件路径**（`photocraft file.new` 的 schema）。

    [归因澄清 2026-11-08 · 云严终审] **别把「相对值放行」误归因成「`profile` 不是
    路径键」**。本文件旧注释就是这么写的，与 #27 正面打脸：`profile` 现在**已经进了
    通用词表**（`_PATH_KEY_DIRECTION`），#27 断言 `{"profile": WIN_INI}` 越界**必须拒**。
    第 1 半之所以绿，靠的是**与 key 名无关的两条独立判据**：
      ① 预设名是**相对值** → 不命中绝对判据 `^[A-Za-z]:(?![0-9])`、也不含 `..` 段
         → 压根不进 `resolve_and_check`；
      ② 相对值并非免检 —— 相对值里的 `..` 仍被 #24 / #32 钉死必须拒。
    换个说法：#26 第 1 半钉的是「**相对值放行**」，#27 钉的是「**绝对路径形态受
    roots 约束**」。两半合起来才是完整口径，单看任何一半都会被读成另一个意思。
    `NEW_DOC_PATH_KEYS` 仍保留为「按通道补键」的**机制钉子**（证明补键接口能被调用方
    用到；当前它对 `profile` 与通用词表冗余，但机制本身要被守住）。

    原设计 `all_strings: bool` 用「任何字符串都按路径判」的严判据去做 `--new`，
    结果 `new_doc={"name": "A: study"}` 命中盘符判据被拒（云严 S5 误杀，错误信息
    还谎称路径越界）。真修法是 `validate_params(..., extra_path_keys=...)`：只把
    **确知的**路径键补进词表，其余键名仍走常规 token 判据。

    三半，缺一不可：
      1. **放行**：`{"profile": "Default"}` —— 预设名是**相对值**，不送 roots → 不抛。
         （**不是**「因为 `profile` 不是路径键」；绝对形态见 #27。）
      2. **放行**：`--new` 通道补键后，`{"name": "A: study"}` 仍不抛 ——
         钉死云严 S5 那个误杀，防止有人为了"省事"退回 all_strings。
      3. **拒**：同一次调用换 `{"profile": WIN_INI}` 必须抛 —— 证明补键真的生效，
         不是摆设。

    变异自检（两条都实测过，非恒绿）：
      * `NEW_DOC_PATH_KEYS = frozenset()` → 第 3 半红。
      * `extra_path_keys` 默认值改成 `frozenset({"name"})` → 第 2 半红
        （`A: study` 被当盘符）。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    policy = _policy(impl)
    new_doc_keys = sequence.NEW_DOC_PATH_KEYS

    # 第一半：预设名是**相对值** → 不命中绝对判据、也不含 `..` → 不进 roots → 放行。
    # ⚠️ 别写成「`profile` 不是路径键」：它已在通用词表里，#27 钉的是**绝对形态必须
    # 拒**。这一半绿的原因是「相对值不送 roots」，与 key 叫什么**无关** —— 归因写错
    # 会让后手以为可以靠「把 key 移出词表」来实现放行，那正是本轮修掉的那个洞。
    # `{"name": "A: study"}` 一并放行：它钉的是「默认 extra_path_keys 必须是空」，
    # 否则有人把文档名塞进默认值（云严 S5 的回潮形态）这里会立刻红。
    for params in ({"profile": "Default"}, {"name": "A: study"}):
        sequence.validate_steps(
            [_step(impl, params)], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=policy,
        )

    # 第二半：`--new` 通道补键后，文档名即使像盘符也不得被误杀（云严 S5）
    sequence.validate_params(
        {"name": "A: study"}, policy, extra_path_keys=new_doc_keys,
    )

    # 第三半：同一通道里 profile 是文件路径 → 越界必须拒
    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_params(
            {"profile": WIN_INI}, policy, extra_path_keys=new_doc_keys,
        )
    assert ("PATH_OUTSIDE_ROOTS" in str(ei.value)
            or "PATH_UNSAFE" in str(ei.value)), _detail(ei.value)


# ---------------------------------------------------------------------------
# 27-32. 词表第三批（schema 明写收路径的键）+ `file:` 协议 + URL / `..` 空白族
#
# 这一批的共同来由：[FACT] 逐条核 8539 条命令的 schema，有一批键「名字不像 path、
# 却明写收路径」，它们不在 `_PATH_KEY_DIRECTION` 里 → step params 通道整段放行。
# 每一条都配一个**反向半边**（相对值/roots 内绝对路径必须放行），因为「见到这个键
# 就拒」是另一种错法：词表扩张绝不能变成可用性回归。
# ---------------------------------------------------------------------------
def test_profile_key_three_states(impl):
    """错误/正常（#27）：`profile` 在 step params 里**也是路径键**，三态都要钉。

    [FACT] schema 里 6 条命令明写收 `/path/to/profile.icc`：`file.new`、
    `edit.profileInfo`、`image.mode.rgb|grayscale|cmyk|lab`。它此前不在
    `_PATH_KEY_DIRECTION`，于是 `{"profile": "C:\\Windows\\win.ini"}` 在 step params
    通道**整段放行**（`--new` 通道因为 `NEW_DOC_PATH_KEYS` 才拦住）—— 同一个 key
    两条通道口径不一致，本身就是完整旁路：模型把 `file.new` 拆成
    `edit.profileInfo` 一步就能带着任意 ICC 路径进 CLI。

    三半缺一不可：
      * `sRGB` / `working` / `Default` 放行 —— 预设名是**最常见**用法；收进词表后
        若把相对值也拒，`image.mode.rgb` 立刻不可用（这是本条的可用性半边）；
      * `WIN_INI` 拒 —— 本条的缺陷正身，也是「不许裸 substring」换载体后腾出来的
        位置必须被填上的一半；
      * `<data>/x.icc` 放行 —— 证明实现是「进词表 + 照常比 roots」，不是
        「见到 profile 就拒」（`roots` 内的绝对路径本来就该放行）。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    policy = _policy(impl)

    for preset in ("sRGB", "working", "Default"):
        sequence.validate_steps(
            [_step(impl, {"profile": preset})], _spec(impl), _cmd_policy(impl),
            CATALOG, roots=policy,
        )

    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"profile": WIN_INI})], _spec(impl), _cmd_policy(impl),
            CATALOG, roots=policy,
        )
    assert ("PATH_OUTSIDE_ROOTS" in str(ei.value)
            or "PATH_UNSAFE" in str(ei.value)), _detail(ei.value)

    # roots 内的绝对 ICC 路径：放行（反证「不是一律拒」）
    sequence.validate_steps(
        [_step(impl, {"profile": DATA_DIR + r"\x.icc"})], _spec(impl),
        _cmd_policy(impl), CATALOG, roots=policy,
    )


def test_droplet_and_import_keys_are_checked(impl):
    """错误/正常（#28）：`droplet` / `import` 是路径键，且放行相对值。

    [FACT] `droplet`（Photoshop 动作文件 `.droplet`）与 `import`（批处理导入源）
    在 schema 里都收文件/目录路径，但 token 化后与旧词表无交集 →
    `{"droplet": "C:\\Windows\\win.ini"}` / `{"import": "C:\\Windows\\win.ini"}`
    旧实现**全部放行**。

    反向一半是**必须**有的：`Auto Tone.droplet` / `batch1` 是这两个键最常见的
    正常取值（相对文件名与预设名），词表扩张不能把它们判死。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    policy = _policy(impl)

    for key in ("droplet", "import"):
        with pytest.raises(errors.PolicyViolation) as ei:
            sequence.validate_steps(
                [_step(impl, {key: WIN_INI})], _spec(impl), _cmd_policy(impl),
                CATALOG, roots=policy,
            )
        assert ("PATH_OUTSIDE_ROOTS" in str(ei.value)
                or "PATH_UNSAFE" in str(ei.value)), \
            f"键 {key!r} 未被识别为路径键：{_detail(ei.value)}"

    # 反向：相对值 / 预设名是正常用法
    for params in ({"droplet": "Auto Tone.droplet"}, {"import": "batch1"}):
        sequence.validate_steps(
            [_step(impl, params)], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=policy,
        )


def test_script_key_must_stay_allowed(impl):
    """正常（#29 · 边界钉子，防回潮）：`script` **不进**词表，必须放行。

    [为什么必须单独钉住] `file.scripts.browse` 的 `script` 收的是**内联脚本正文**，
    不是磁盘文件路径（落盘脚本走 `--script <file>` argv 位，不经 params）。
    把 `script` 收进 `_PATH_KEY_DIRECTION` 会立刻踩两个雷：
      * `{"script": "x:y"}` —— 绝对判据 `^[A-Za-z]:(?![0-9])` 命中（`x` + `:` +
        非数字），会被当盘符相对送 roots → `PATH_OUTSIDE_ROOTS`，而 `x:y` 是
        完全正常的内联记号；
      * 一旦验收口径变成「schema 里收字符串就当路径」，下一批 `B:` / `L:` 开头的
        label 也会被同样误杀，而脚本正文里这类写法遍地都是。

    所以这是**反向闸门**：它变红说明有人把内联正文字段当成文件路径 —— 那是
    可用性回归，不是安全修复。
    """
    sequence = impl("sequence")
    policy = _policy(impl)

    for body in ("body: {color: red}", "label: loop", "x:y"):
        sequence.validate_steps(
            [_step(impl, {"script": body})], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=policy,
        )


def test_file_scheme_url_is_checked(impl):
    """错误/正常（#30）：`file:` 协议是**文件路径的另一种拼写**，必须过 roots。

    [真缺陷] `file:///C:/Windows/win.ini` 既不以盘符开头、也不以分隔符开头，
    旧判据把它当普通相对值（不含 `..`）→ 放行。而 CLI 侧接受 `file:` URI，
    等价于直接读 `C:\\Windows\\win.ini` —— 与 #21 的单分隔符是同一类漏判：
    **同一个目标的另一种拼写**被漏掉，闸门就只挡得住照字面抄的例子。

    反向一半：`file:///<data>/x.png` 必须放行 —— 证明实现是「剥协议后照常比
    roots」，不是「见 `file:` 就拒」；否则用户从浏览器拖进来的本地文件全不可用。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    policy = _policy(impl)

    bad = "file:///C:/Windows/win.ini"
    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"path": bad})], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=policy,
        )
    assert ("PATH_OUTSIDE_ROOTS" in str(ei.value)
            or "PATH_UNSAFE" in str(ei.value)), _detail(ei.value)

    ok = "file:///" + DATA_DIR.replace("\\", "/") + "/x.png"
    sequence.validate_steps(
        [_step(impl, {"path": ok})], _spec(impl), _cmd_policy(impl), CATALOG,
        roots=policy,
    )

    # --- 无盘符的 authority 形式 → **当前盘根**，必须拒（fail-closed）
    # [测试缺口，2026-11-08 云严变异实测] `rest = "\\" + rest` 那行此前**零覆盖**：
    # 删掉它 231 全绿，而 `file:///Windows/win.ini` 从 REJECT 变 ALLOW ——
    # `file:///<无盘符>` 去斜杠后剩 `Windows/win.ini`，会被当普通相对值放行，
    # 但 Windows 语义里它的路径部分是 `\Windows\win.ini`（当前盘根）。
    # `file://server/share/x.png` 是 UNC authority，同样 fail-closed。
    for bad_authority in (
        "file:///Windows/win.ini",        # 空 authority + 无盘符 → 当前盘根
        "file://server/share/x.png",      # UNC authority
    ):
        _expect_rejected(
            errors,
            lambda v=bad_authority: sequence.validate_steps(
                [_step(impl, {"target": v})], _spec(impl), _cmd_policy(impl),
                CATALOG, roots=policy,
            ),
            arg=bad_authority,
            code="PATH_OUTSIDE_ROOTS",
        )

    # 反向：RFC 8089 允许的**相对形** `file:draft.png`（无 authority，按 cwd 解析）
    # 不补分隔符 → 按普通相对值放行。补分隔符补过头会把这种正常写法判死。
    sequence.validate_steps(
        [_step(impl, {"target": "file:draft.png"})], _spec(impl), _cmd_policy(impl),
        CATALOG, roots=policy,
    )

    # 反向的另一半：协议后面带 `..` 的相对形照常拒（剥完 `file:` 之后仍要过 `..` 判据）
    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"target": "file:../../../secret.png"})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=policy,
        )
    msg = str(ei.value)
    assert (".." in msg or "POLICY_VIOLATION" in msg
            or "PATH_UNSAFE" in msg), _detail(ei.value)


def test_url_with_parent_segments_allowed_but_relative_still_rejected(impl):
    """正常/错误（#31）：**白名单内** scheme 的 `..` 不是路径穿越；其余照旧要拒。

    [真缺陷（可用性）] `{"target": "https://x/a/../b.png"}` 当前**被拒** —— `..`
    段检查只按 `[\\\\/]` 切分，看不出「这是 URL 的 path 部分，由 HTTP 客户端按
    RFC 3986 自己解析」。远程取图是正常需求（#6 已经钉了「URL 不送 roots」），
    这里补的是同一口径的**另一半**：HTTP(S) URL 也**不做** `..` 段检查。

    反向一半是本条的真正价值：不能因为放过 URL，就把**相对路径**的 `..` 一起放了
    —— `{"path": "a/../x.png"}` 仍然要拒，那是真的会从 CLI 安装目录（`jobs._cwd_for()`
    = Program Files 下的安装路径）逃出去。实现必须是「**先判 is_url 再跳 `..`**」，
    不是「删掉 `..` 检查」；后者会让本条变红。

    [🔴 行为错，2026-11-08 云严实测 · 多字母 scheme 是真旁路]
    `{"path": "xyz://../../x.png"}` 曾 **ALLOW**：旧实现「任何 `scheme://` 都豁免
    `..`」，而 Windows 把 `xyz://` 折叠成**目录名** `xyz`、`..` 照常消解 →
    实际落盘 `xyz/../../x.png`，在 roots 外。所以豁免必须收成**白名单**
    `{"http", "https"}`（大小写不敏感），不是「凡是 URL 形状就放过」：
      * `xyz://` / `a+b://` / `con://` → 不是 URL，按相对路径查 `..` → 拒；
      * `ftp://` → 白名单外 → **含 `..` 时**拒。**这是行为变更，不是误杀**：`ftp` 的
        `..` 同样由远端解析，本地闸门无从验证，fail-closed 才是对的；
      * `x://`（单字母）→ 先命中盘符判据 `^[A-Za-z]:(?![0-9])` 走 roots
        （`PATH_UNSAFE`）—— 这解释了它为什么「天生安全」，也钉住这个顺序。

    [口径澄清 2026-11-08 主理人裁决] 白名单**只关掉 `..` 豁免**，不是「禁用 ftp」，
    也不是「把裸 `scheme:` 提升成 URL」。所以下面还有一组**必须放行**的入参：
      * `ftp://h/a/b.png` —— 白名单外，但**不含 `..`** → 走完全部判据后放行。
        缺了这半句，将来有人把 `ftp` 塞进白名单、或反过来把白名单外一律禁掉，
        都不会有测试说话；
      * `mailto:a@b` / `data:image/png;base64,AAA` —— 无 `://`，压根不参与 URL 判定
        （与 `layer:3` 同族：裸 `scheme:` 后面不是 `//`，不是 URL 形状）。

    [已裁决为**已知边界**，故意不钉断言] `con://a/b.png`（不含 `..`）放行 ——
    Windows 把 `scheme://` 折叠成「scheme 当前置目录」，不含 `..` 就退不回 roots 外；
    主理人已把它写进 `spec/ARCHITECTURE-FINAL.md §19.8` 已知边界第 9 条。
    **钉成断言会让人以为那是「正确语义」而不是「待观察的边界」**，所以这里留白。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    policy = _policy(impl)

    # --- 白名单内：放行（含**大写** scheme，大小写不敏感）
    for url in (
        "https://x/a/../b.png",
        "http://127.0.0.1:8080/a/../c.svg",
        "HTTPS://x/a/../b.png",
    ):
        sequence.validate_steps(
            [_step(impl, {"target": url})], _spec(impl), _cmd_policy(impl), CATALOG,
            roots=policy,
        )

    # --- 白名单外 / 非 URL 的 `scheme://`：`..` 照常拒（真旁路正身）
    for bad_scheme in (
        "xyz://../../x.png",       # 多字母 scheme → 折叠成目录名 `xyz`
        "a+b://../../x.png",       # scheme 允许 `+`
        "con://../../x.png",       # 设备名当 scheme
        "ftp://x/a/../b.png",      # 白名单外（行为变更，见 docstring）
    ):
        _expect_rejected(
            errors,
            lambda v=bad_scheme: sequence.validate_steps(
                [_step(impl, {"path": v})], _spec(impl), _cmd_policy(impl),
                CATALOG, roots=policy,
            ),
            arg=bad_scheme,
            code="POLICY_VIOLATION",
        )

    # --- 口径澄清：白名单**只关掉 `..` 豁免**，不是「禁用 ftp」也不是「裸 scheme: 算 URL」
    # 这三条都不含 `..` → 走完全部判据后放行。这是上面那组的**另一半**：
    # 只钉「含 `..` 的 ftp 被拒」的话，将来有人把 `ftp` 塞进白名单、或反过来把
    # 白名单外一律禁掉，都不会有测试说话。
    for not_a_traversal in (
        "ftp://h/a/b.png",              # 白名单外，但无 `..` → 放行（≠「ftp 全禁」）
        "mailto:a@b",                   # 无 `://`，不参与 URL 判定
        "data:image/png;base64,AAA",    # 裸 `scheme:` 同样不算 URL（`layer:3` 同族）
    ):
        sequence.validate_steps(
            [_step(impl, {"target": not_a_traversal})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=policy,
        )

    # --- 单字母 `x://`：走盘符判据 → roots → PATH_UNSAFE（顺序钉子）
    _expect_rejected(
        errors,
        lambda: sequence.validate_steps(
            [_step(impl, {"path": "x://../../x.png"})], _spec(impl),
            _cmd_policy(impl), CATALOG, roots=policy,
        ),
        arg="x://../../x.png",
        code="PATH_UNSAFE",
    )

    # --- 反向：普通相对路径的 `..` 仍拒
    with pytest.raises(errors.PolicyViolation) as ei:
        sequence.validate_steps(
            [_step(impl, {"path": "a/../x.png"})], _spec(impl), _cmd_policy(impl),
            CATALOG, roots=policy,
        )
    msg = str(ei.value)
    assert (".." in msg or "PATH_UNSAFE" in msg
            or "PATH_OUTSIDE_ROOTS" in msg), _detail(ei.value)


def test_parent_segment_with_mixed_whitespace_rejected(impl):
    """错误（#32）：`..` 段的**空白族**必须收全 —— `strip(" \\t")` 只挡住两种。

    [真缺口] #24 只钉了 `".. "`（尾随空格）与制表符；实现若写成
    `seg.strip(" \\t")`，则 `"..\\r"` / `"..\\u00a0"` / `"..\\x0b"` / `"..\\u2003"`
    这些段 strip 后仍是原样 → 与 `".."` 不等 → **放行**。`str.strip()`（不带参数）
    按 `str.isspace()` 剥，才覆盖整族。

    口径沿用 #24 的裁决：这些段在 NTFS 上本来就打不开，拒掉是**偏严兜底 /
    fail-closed**，不是 Windows 语义推导 —— 别据本条去补更多拼写判据，本闸门要挡的
    只有 `..` 这一族。

    反向一半：`a\\b\\x.png` 是正常相对路径，不得被误杀。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    policy = _policy(impl)

    bad_cases = (
        "a\\..\r\\x.png",      # 尾随 CR（真回车）
        "a/..\u00a0/x.png",    # 尾随 NBSP
        "a\\.. \\x.png",       # 尾随空格（#24 已覆盖，这里做族内回归）
        "a\\..\x0b\\x.png",    # 尾随垂直制表
        "a\\..\u2003\\x.png",  # 尾随 EM SPACE
        "a\\..\n\\x.png",      # 尾随 LF
        "a\\\r..\\x.png",      # **前导** CR
    )
    for bad in bad_cases:
        with pytest.raises(errors.PolicyViolation) as ei:
            sequence.validate_steps(
                [_step(impl, {"path": bad})], _spec(impl), _cmd_policy(impl),
                CATALOG, roots=policy,
            )
        msg = str(ei.value)
        assert ("PATH_OUTSIDE_ROOTS" in msg or "PATH_UNSAFE" in msg
                or ".." in msg), \
            f"入参 {bad!r} 被拒但消息看不出 `..` 语义：{_detail(ei.value)}"

    # 反向：正常相对路径（含分隔符、无全空白段）必须放行
    sequence.validate_steps(
        [_step(impl, {"path": r"a\b\x.png"})], _spec(impl), _cmd_policy(impl),
        CATALOG, roots=policy,
    )


# ---------------------------------------------------------------------------
# B1 / RFC 8089：file: URI 的 authority 三分支（红阶段，2026-11-08 云测）
#
# [性质，务必说准] B1 **不是安全洞**。现状下 UNC 拼写与 file://host 拼写
# **两种都 fail-closed**（默认策略一律拒），没有任何越界读写被放开。真正的缺陷是
# **一致性 / 可用性**：用户显式授权了一个 UNC 读根之后，RFC 8089 的
# ``file://host/share/...`` 拼写仍被 ``_check_one_param_path`` 当成「空 authority
# + 当前盘根」，补一个前导分隔符后丢掉远程主机名，于是同一资源两种写法结论不同
# （UNC 拼写 ALLOW / URI 拼写 REJECT）。别让后人把这一轮读成「修越界」——
# 修的是「授权 UNC 根后，RFC 8089 的 file://host/... 拼写仍被误判为本地当前盘根」。
#
# [FACT · 主理人实测 + 本轮 validate_steps 复核] 当前实现（sequence.py 约 645-661）：
#   rest = text[len("file:"):]
#   had_authority = rest[:2] in ("//", "\\\\")
#   rest = rest.lstrip("/\\")
#   if had_authority and not _ABS_PATH_RE.match(rest): rest = "\\" + rest
# 即**任何** authority 都被折叠成当前盘根，远程主机名直接丢失。
#
# 目标契约（RFC 8089 三分支）：
#   file:///C:/...            空 authority + 有盘符   -> C:/...            （保持）
#   file:///Windows/...       空 authority + 无盘符   -> 当前盘根 \Windows  （保持）
#   file://localhost/C:/...   localhost 等价本地       -> C:/...            （现状错）
#   file://server/share/...   远程 authority           -> UNC 拼写          （现状错）
#   file:draft.png            无 authority 相对形      -> 保持相对、放行     （保持）
#
# 钉住五条不变量（缺任何一半都白写）：
#   1. 默认策略下六种形态的 REJECT/ALLOW 结果全部不变；尤其
#      ``test_file_scheme_url_is_checked`` 已把 "file://server/share/x.png" 锁在
#      code="PATH_OUTSIDE_ROOTS"，修完不许变成别的结果或别的码。
#   2. **只有**授权 UNC 根的策略下，UNC 拼写与 file://host 拼写才必须等价且都 ALLOW；
#      越界的两种拼写必须都 REJECT。这一正一反是 B1 核心。
#   3. file://localhost/... 有自己的断言：含授权根时与本地路径 / 空 authority 等价。
#   4. 不新增错误码（ERROR_CODES 固定 19 条）；roots.py 零改动。
#   5. 任何现有拒绝路径不得被放宽（另列一组「旧写法会拒、修完也必须拒」的锚点）。
#
# 纪律：UNC 根用不存在的假主机名，全部走 ``impl`` / ``_policy`` / ``_step`` /
# ``_expect_rejected`` 纯函数路径；不读写真实文件、不 spawn 进程。
# ---------------------------------------------------------------------------
B1_UNC_ROOT = r"\\server\share\art"           # 不存在的假主机名 / 假共享
B1_UNC_OK = B1_UNC_ROOT + r"\x.png"            # 授权根内的资源（UNC 拼写）
B1_UNC_OK_URI = "file://server/share/art/x.png"  # 同一资源（RFC 8089 拼写）
B1_UNC_OUTSIDE = r"\\server\share\other\x.png"  # 越界资源（UNC 拼写）
B1_UNC_OUTSIDE_URI = "file://server/share/other/x.png"
B1_UNC_TRAVERSAL = B1_UNC_ROOT + r"\..\..\other\x.png"
B1_UNC_TRAVERSAL_URI = "file://server/share/art/../../other/x.png"

B1_LOCAL_ROOT = "C:/allowed"                   # 不存在的假本地目录
B1_LOCAL_OK = "C:/allowed/x.png"
B1_LOCAL_OK_URI_EMPTY = "file:///C:/allowed/x.png"        # 空 authority
B1_LOCAL_OK_URI_LOCALHOST = "file://localhost/C:/allowed/x.png"


def _policy_with_unc_read_root(impl):
    """只授权一个 UNC **读**根（假主机名），其余与线上默认策略逐字段一致。"""
    return impl("roots").default_policy(DATA_DIR, CACHE_DIR, {
        "allow_user_dirs": True,
        "user_read_roots": [B1_UNC_ROOT],
        "user_write_roots": [],
    })


def _policy_with_local_read_root(impl):
    """只授权一个本地读根，用来验 file://localhost/... 与本地路径等价。"""
    return impl("roots").default_policy(DATA_DIR, CACHE_DIR, {
        "allow_user_dirs": True,
        "user_read_roots": [B1_LOCAL_ROOT],
        "user_write_roots": [],
    })


def _b1_check(impl, sequence, policy, value, key="target"):
    """与 ``test_file_scheme_url_is_checked`` 同口径：走 validate_steps 的 target 键。"""
    sequence.validate_steps(
        [_step(impl, {key: value})], _spec(impl), _cmd_policy(impl), CATALOG,
        roots=policy,
    )


def _expect_allowed(call, *, arg: str) -> None:
    """与 ``_expect_rejected`` 对称：把「应放行却被拒」的失败信息带上入参原文。"""
    try:
        call()
    except Exception as exc:
        pytest.fail(f"入参 {arg!r} 应放行但被拒：{_detail(exc)}", pytrace=False)


def test_b1_default_policy_six_spellings_unchanged(impl):
    """不变量 1：默认策略下六种形态的 REJECT/ALLOW 结果全部不变。

    [FACT · 本轮基线实测，默认策略 = 只授权 data/cache]
      UNC 未授权根            -> REJECT PATH_OUTSIDE_ROOTS
      file://server/share/x.png -> REJECT PATH_OUTSIDE_ROOTS  (1086 行已锁码)
      file:///C:/Windows/win.ini -> REJECT PATH_OUTSIDE_ROOTS
      file:///Windows/win.ini  -> REJECT PATH_OUTSIDE_ROOTS
      file://localhost/C:/x.png -> REJECT PATH_UNSAFE
      file:draft.png           -> ALLOW
    只锁 ``file://server/share/x.png`` 的码：它已被 1086 行钉死，修完不许变成
    别的结果或别的码；其余 REJECT 只锁二值——localhost 归一后码从
    PATH_UNSAFE 变 PATH_OUTSIDE_ROOTS 属合理实现，锁死反而会把实现细节冻成契约。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    policy = _policy(impl)

    rejected = (
        r"\\server\share\x.png",
        "file://server/share/x.png",
        "file:///C:/Windows/win.ini",
        "file:///Windows/win.ini",
        "file://localhost/C:/x.png",
    )
    for bad in rejected:
        code = "PATH_OUTSIDE_ROOTS" if bad == "file://server/share/x.png" else ""
        _expect_rejected(
            errors,
            lambda v=bad: _b1_check(impl, sequence, policy, v),
            arg=bad,
            code=code,
        )

    # 相对形（无 authority）仍放行 —— 修三分支不许把它补成绝对路径
    _expect_allowed(
        lambda: _b1_check(impl, sequence, policy, "file:draft.png"),
        arg="file:draft.png",
    )


def test_b1_unc_uri_matches_unc_path_only_when_root_authorized(impl):
    """不变量 2（B1 核心）：授权 UNC 读根后两种拼写等价且都 ALLOW；越界都 REJECT。

    这一正一反缺一不可：只写「授权根内 URI 应 ALLOW」会漏掉「修法把
    file://host 一律放行」这种把越界也放开的错法；只写「越界都 REJECT」则
    根本抓不到本缺陷。现状（[FACT] 本轮复核）：
      UNC 拼写 -> ALLOW；file://host 拼写 -> REJECT PATH_OUTSIDE_ROOTS。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    unc_policy = _policy_with_unc_read_root(impl)

    # --- 正：同一资源的两种拼写，授权根内都要放行（现状：第二条红）
    for ok in (B1_UNC_OK, B1_UNC_OK_URI):
        _expect_allowed(
            lambda v=ok: _b1_check(impl, sequence, unc_policy, v),
            arg=ok,
        )

    # --- 反：越界的两种拼写都要拒（防止修法把 file://host 一律放行）
    for bad in (B1_UNC_OUTSIDE, B1_UNC_OUTSIDE_URI):
        _expect_rejected(
            errors,
            lambda v=bad: _b1_check(impl, sequence, unc_policy, v),
            arg=bad,
            code="PATH_OUTSIDE_ROOTS",
        )

    # --- 含 ``..`` 的 UNC 两种拼写仍拒（fail-closed 锚点，不锁码）
    for bad in (B1_UNC_TRAVERSAL, B1_UNC_TRAVERSAL_URI):
        _expect_rejected(
            errors,
            lambda v=bad: _b1_check(impl, sequence, unc_policy, v),
            arg=bad,
        )

    # --- 「只有授权根策略下」：默认策略下两种拼写都不得 ALLOW
    default_policy = _policy(impl)
    for bad in (B1_UNC_OK, B1_UNC_OK_URI):
        _expect_rejected(
            errors,
            lambda v=bad: _b1_check(impl, sequence, default_policy, v),
            arg=bad,
        )


def test_b1_localhost_authority_equivalent_to_local_path(impl):
    """不变量 3：file://localhost/... 等价空 authority 本地路径。

    现状把 localhost 折成「当前盘根的 localhost 目录」，段 ``C:`` 被 roots 当
    ADS -> PATH_UNSAFE；而等价的本地路径 / ``file:///C:/...`` 在授权根内是 ALLOW。
    三态一起钉住同一资源的三种拼写结论必须一致（现状第三条红）。
    越界三态只锁二值：localhost 归一后码可能从 PATH_UNSAFE 变
    PATH_OUTSIDE_ROOTS，那是合理实现，不锁死。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    local_policy = _policy_with_local_read_root(impl)

    # --- 授权根内：三种拼写都 ALLOW
    for ok in (B1_LOCAL_OK, B1_LOCAL_OK_URI_EMPTY, B1_LOCAL_OK_URI_LOCALHOST):
        _expect_allowed(
            lambda v=ok: _b1_check(impl, sequence, local_policy, v),
            arg=ok,
        )

    # --- 越界：三种拼写都 REJECT
    for bad in (
        "C:/Windows/win.ini",
        "file:///C:/Windows/win.ini",
        "file://localhost/C:/Windows/win.ini",
    ):
        _expect_rejected(
            errors,
            lambda v=bad: _b1_check(impl, sequence, local_policy, v),
            arg=bad,
        )

    # --- 默认策略下 localhost 仍必须 REJECT（不变量 1 的 localhost 专属半边）
    _expect_rejected(
        errors,
        lambda: _b1_check(impl, sequence, _policy(impl),
                          "file://localhost/C:/x.png"),
        arg="file://localhost/C:/x.png",
    )


def test_b1_existing_reject_paths_never_relaxed(impl):
    """不变量 5：旧写法会拒的入参，修完（且授权了根）也必须拒。

    这组是「不许放宽」锚点：每条都在**默认**与**两种授权**策略下各跑一遍，
    防止修法为了放行 RFC 8089 拼写而把 ``..`` / 当前盘根 / 越界一并放过。
    含 ``..`` 的 UNC 两种拼写也在内 —— 归一成 UNC 之后 ``..`` 仍要被拒。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    policies = (
        ("默认策略", _policy(impl)),
        ("授权 UNC 读根", _policy_with_unc_read_root(impl)),
        ("授权本地读根", _policy_with_local_read_root(impl)),
    )
    legacy_rejects = (
        "file:///Windows/win.ini",                    # 空 authority 无盘符 -> 当前盘根
        "file:///C:/Windows/win.ini",                 # 空 authority 有盘符 -> 越界
        "file:../../../secret.png",                   # 相对形含 ..
        r"\\server\share\art\..\..\other\x.png",     # UNC 含 ..
        "file://server/share/art/../../other/x.png",  # UNC URI 含 ..
        "file:///C:/Windows/../../x.png",             # 空 authority 含 ..
    )
    for label, policy in policies:
        for bad in legacy_rejects:
            _expect_rejected(
                errors,
                lambda v=bad, p=policy: _b1_check(impl, sequence, p, v),
                arg=f"[{label}] {bad}",
            )


def test_b1_error_code_set_stays_fixed_at_19(impl):
    """不变量 4：B1/C5 不得新增错误码（ERROR_CODES 是 19 条固定集合）。

    ``roots.py`` 零改动由红阶段纪律保证（本文件与 test_locator.py 之外一律不碰），
    本条只钉住码表：与独立真相源 ``_fixtures.EXPECTED_ERROR_CODES`` 逐项相等。
    [为什么局部 import] 顶部不新增 import，避免把既有的
    ``test_file_scheme_url_is_checked`` 里 1086 行那条锁码断言整体位移。
    """
    from _fixtures import EXPECTED_ERROR_CODES

    errors = impl("errors")
    assert len(errors.ERROR_CODE_ORDER) == 19, \
        f"错误码表条目数变化：{len(errors.ERROR_CODE_ORDER)}"
    assert frozenset(errors.ERROR_CODES) == EXPECTED_ERROR_CODES, \
        "ERROR_CODES 与 EXPECTED_ERROR_CODES 不一致（新增或删除了码）"
    assert frozenset(errors.ERROR_CODE_ORDER) == EXPECTED_ERROR_CODES


def test_b1_host_matrix_h14_h20_parity(impl):
    r"""与宿主 ``probe/host_gate_check.py`` 的 H14–H20 逐字对齐（默认策略）。

    [FACT · team-lead 宿主级修前基线，2026-11-08] 20 条矩阵 ``19 PASS / 1 FAIL``，
    唯一红是 H20「file://localhost/ + <宿主 data root> + /x.png」→
    ``gate_code=PATH_UNSAFE``；H14–H19 六条全 PASS（5 拒 1 放），是「修完不许
    放宽」的回归锚点。宿主脚本本身只读参考，本轮不改它。

    宿主 ``DATA_DIR`` 是 ``%LOCALAPPDATA%\N.E.K.O\plugins\<id>\data``；纯函数侧
    换成同构的假 ``DATA_DIR``，但**构造式与宿主逐字一致**：
    ``"file://localhost/" + DATA_DIR.replace("\\", "/") + "/x.png"``。
    宿主默认策略只授权 data/cache，这里同样用 ``_policy``（零额外授权）。

    这条比 ``test_b1_localhost_authority_equivalent_to_local_path`` 更强：那条
    靠 ``allow_user_dirs`` 额外授权了一个本地根，这条**不需要任何用户配置**，
    默认策略自己授权的 data 根就该被 localhost 拼写命中。H20 在当前代码下红，
    与宿主唯一红一一对应；三态（裸本地 / 空 authority / localhost）一起钉住，
    前两条绿、第三条红，失败信息精确定位到 localhost 分支。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    policy = _policy(impl)              # 默认策略：只授权 data/cache，与宿主一致

    # --- H14 / H15 / H16 / H17 / H19：5 条「修完不许放宽」的逐字回归锚点
    host_rejects = (
        r"\\server\share\x.png",                     # H14 真 UNC（默认策略无 UNC 根）
        "file://server/share/x.png",                 # H15 远程 authority
        "file:///Windows/win.ini",                   # H16 空 authority 无盘符
        "file://localhost/C:/Windows/win.ini",      # H17 localhost 指向盘外
        "file://server/share/../secret.png",        # H19 远程 authority 带 ..
    )
    for bad in host_rejects:
        # H15 已被本文件 1086 行锁码；其余只锁二值（localhost/``..`` 归一后码可能变）
        code = "PATH_OUTSIDE_ROOTS" if bad == "file://server/share/x.png" else ""
        _expect_rejected(
            errors,
            lambda v=bad: _b1_check(impl, sequence, policy, v),
            arg=bad,
            code=code,
        )

    # --- H18：无 authority 相对形放行
    _expect_allowed(
        lambda: _b1_check(impl, sequence, policy, "file:draft.png"),
        arg="file:draft.png",
    )

    # --- H20：localhost 指向默认策略已授权的 data root 内，三种拼写都应放行
    data_root = DATA_DIR.replace("\\", "/")
    for ok in (
        data_root + "/x.png",                       # 裸本地路径（绿）
        "file:///" + data_root + "/x.png",          # 空 authority（绿）
        "file://localhost/" + data_root + "/x.png",  # H20 正向（现状红）
    ):
        _expect_allowed(
            lambda v=ok: _b1_check(impl, sequence, policy, v),
            arg=ok,
        )


# ---------------------------------------------------------------------------
# 裁决后必修 1 / 2（云严只读预检 + team-lead 真代码实测，2026-11-08）
#
# [FACT · 云雨第一版，授权 UNC 根 \\server\share\art]
#   file://server/share/art/.. /x.png  -> ALLOW（B1 修前是 REJECT）
#   file:////server/share/art/x.png    -> ALLOW（修前 REJECT）
# 必修 1 根因：UNC 串以分隔符开头，在 sequence.py 的绝对分支**提前 return** 送
#   roots；而 roots._assert_safe_segments 只认字面 ``seg == ".."``（不 strip），
#   空白变体全漏。``file://localhost/C:/...`` 走盘符绝对分支同样漏。
# 必修 1 裁决修法：file: 分支产出的**所有** text 在送 resolve_and_check 前统一跑
#   ``any(seg.strip() == ".." for seg in re.split(r"[\\/]", text))``，
#   命中即 ``_violate``（POLICY_VIOLATION）。
# 必修 2 根因：rest="////server/..." 剥 2 个后 body="//server/..."，split 出空
#   authority + "/server/..."，空 authority 分支拼出 "\/server/..."，normalize
#   洗成 //server/... UNC —— 本该「当前盘根」却变成「远程 UNC」。
# 必修 2 裁决修法：剥之前数前导连续 [\\/] 个数 n，``n>=4`` 直接 _violate；
#   n==3 保持现有空 authority 处理；n==2 走三分支；n<=1 相对形。
#
# [已知边界，故意留白、不钉断言 —— 沿用上一轮 con:// 的裁决口径]
#   * ``%2e%2e`` 在授权 UNC 根下 ALLOW：原生 UNC 通道修前即 ALLOW，B1 只是
#     对齐；CLI 是否解码百分号**未核验**，别把它固化成「正确语义」。
#   * ``file:\server\share\...``（单反斜杠）修前修后都 ALLOW：同一留白。
#   * ``file://C:/Windows/win.ini`` 错误码归类变化（-> PATH_UNSAFE）：二值不变
#     即可，不锁精确码（与 S7「未来统一错误码」冲突）。
# ---------------------------------------------------------------------------
# 空白族用 chr() 显式构造，避免转义歧义；与生产代码「不带参数 str.strip()」同口径。
B1_DOTDOT_WS_VARIANTS = tuple(
    ".." + ws for ws in (
        " ",        # U+0020 空格
        chr(9),     # TAB
        chr(13),    # CR
        chr(160),   # NBSP U+00A0
        chr(8195),  # EM SPACE U+2003
    )
)


def test_b1_file_scheme_whitespace_dotdot_rejected_in_all_branches(impl):
    """必修 1：file: 三个分支产出的 text 都要过空白族 ``..`` 判据。

    远程 UNC（授权 UNC 根）/ localhost 绝对（授权本地根）/ 相对形（默认策略）
    各跑一遍 5 个空白变体；正向锚点证明新判据没误杀合法入参。
    锁 ``POLICY_VIOLATION``：裁决明确要求命中即 ``_violate``，不是交给 roots 拒。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    unc_policy = _policy_with_unc_read_root(impl)
    local_policy = _policy_with_local_read_root(impl)
    default_policy = _policy(impl)

    # ① 远程 UNC 分支（授权 UNC 根）：空白族必须 REJECT
    for variant in B1_DOTDOT_WS_VARIANTS:
        uri = "file://server/share/art/" + variant + "/x.png"
        _expect_rejected(
            errors,
            lambda v=uri: _b1_check(impl, sequence, unc_policy, v),
            arg=uri, code="POLICY_VIOLATION",
        )

    # ② localhost 绝对分支（授权本地根）：同族也必须 REJECT —— 别只测 UNC
    for variant in B1_DOTDOT_WS_VARIANTS:
        uri = "file://localhost/C:/allowed/" + variant + "/x.png"
        _expect_rejected(
            errors,
            lambda v=uri: _b1_check(impl, sequence, local_policy, v),
            arg=uri, code="POLICY_VIOLATION",
        )

    # ③ 相对形（默认策略）：字面 .. 与空白族仍 REJECT（这条当前就绿，是基线锚点）
    relative_bad = ("file:../../../secret.png",) + tuple(
        "file:" + variant + "/x.png" for variant in B1_DOTDOT_WS_VARIANTS
    )
    for bad in relative_bad:
        _expect_rejected(
            errors,
            lambda v=bad: _b1_check(impl, sequence, default_policy, v),
            arg=bad, code="POLICY_VIOLATION",
        )

    # ④ 正向锚点：不含 .. 的合法入参仍 ALLOW（新判据不许误杀）
    _expect_allowed(
        lambda: _b1_check(impl, sequence, unc_policy,
                          "file://server/share/art/x.png"),
        arg="file://server/share/art/x.png",
    )
    _expect_allowed(
        lambda: _b1_check(impl, sequence, local_policy,
                          "file://localhost/C:/allowed/x.png"),
        arg="file://localhost/C:/allowed/x.png",
    )


def test_b1_file_scheme_leading_separator_count(impl):
    r"""必修 2：前导连续分隔符 ``n>=4`` 直接 ``_violate``，``n==3`` 不误伤。

    n 按 ``file:`` 之后连续的 ``[\\/]`` **逐字符**计数；混合分隔符与纯斜杠
    同结论（这是本轮给混合形态下的结论，不是留白）：
      * ``file://\\server/...``  -> n=4（2 斜杠 + 2 反斜杠），当前 ALLOW（错位）
      * ``file://\\\\server/...`` -> n=6（2 斜杠 + 4 反斜杠），当前 PATH_OUTSIDE_ROOTS
      裁决后两者都必须是 REJECT + POLICY_VIOLATION。

    ``n==3`` 三态是最容易打红的一脚：data root 内仍 ALLOW；两条越界仍 REJECT
    且码仍是 ``PATH_OUTSIDE_ROOTS``（不许被新判据改成 POLICY_VIOLATION）。
    """
    sequence = impl("sequence")
    errors = impl("errors")
    unc_policy = _policy_with_unc_read_root(impl)
    default_policy = _policy(impl)
    data_root = DATA_DIR.replace("\\", "/")
    bs = chr(92)

    # ① n>=4（纯斜杠 n=4/n=5、混合 n=4/n=6）：两种策略下都 REJECT + POLICY_VIOLATION
    over_four = (
        "file:////server/share/art/x.png",               # n=4 纯斜杠
        "file://///server/share/art/x.png",              # n=5 纯斜杠
        "file://" + bs * 2 + "server/share/art/x.png",   # n=4 混合 // + 2 反斜杠
        "file://" + bs * 4 + "server/share/art/x.png",   # n=6 混合 // + 4 反斜杠
    )
    for label, policy in (("授权 UNC 读根", unc_policy), ("默认策略", default_policy)):
        for bad in over_four:
            _expect_rejected(
                errors,
                lambda v=bad, p=policy: _b1_check(impl, sequence, p, v),
                arg=f"[{label}] {bad}", code="POLICY_VIOLATION",
            )

    # ② n==3 正向不误伤：两种策略逐条钉
    for label, policy in (("授权 UNC 读根", unc_policy), ("默认策略", default_policy)):
        ok = "file:///" + data_root + "/x.png"
        _expect_allowed(
            lambda v=ok, p=policy: _b1_check(impl, sequence, p, v),
            arg=f"[{label}] {ok}",
        )
        for bad in ("file:///C:/Windows/win.ini", "file:///Windows/win.ini"):
            _expect_rejected(
                errors,
                lambda v=bad, p=policy: _b1_check(impl, sequence, p, v),
                arg=f"[{label}] {bad}", code="PATH_OUTSIDE_ROOTS",
            )
