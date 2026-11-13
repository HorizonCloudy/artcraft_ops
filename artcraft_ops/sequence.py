"""artcraft_ops —— 领域层：命名空间归一化 / 命令策略 / argv 构造。

[FACT 链 1] §5.5 真实报错（命名空间陷阱）：
``error: `doc.inspect`: unknown command `doc.inspect`; did you mean `document.inspect`?``
→ CLI `run --cmd` 用 **engine 命名空间**（``document.*``），serve method 用 **doc.***。
本机 PhotoCraft 全表 **0 个 `doc.*`、11 个 `document.*`**。

防注入（§5）：
  * argv 是 list，每个值都是**独立元素**；文件名/params 绝不拼 shell 字符串；
  * ``params`` 一律 ``json.dumps`` 成单个 argv 元素；
  * 命令 id 先归一化再走白名单校验。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Iterable, Literal

from . import roots as roots_mod
from .errors import PolicyViolation
from .spec import (
    STATIC_ALLOWED_EXACT,
    STATIC_ALLOWED_PREFIXES,
    CommandPolicy,
    RootsPolicy,
    Source,
    Step,
)

_CMD_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_HOST_PORT_KEYS = ("host", "hostname", "endpoint", "bind", "address")
_FORBIDDEN_PARAM_KEYS = ("token", "authorization", "cookie", "api_key", "secret",
                         "password", "control_file", "control-token")

# [真缺陷补丁，2026-11-08] `validate_params` 早就写好 roots 分支，但**没有任何调用点
# 把 roots 递进来** —— `validate_steps` 只调 `validate_params(step.params or {})`。
# 于是模型可以 `{"cmd":"edit.fill","params":{"path":"C:\\Windows\\win.ini"}}`
# 一路过 P5，让 CLI 读系统文件再导出。下面几组常量是补这条旁路时需要的判据：
#   * 键名判定必须 **token 化**（驼峰/下划线/短横都拆开），不能小写精确匹配：
#     CLI 参数普遍写成 `outPath` / `srcFile` / `output_path`，精确匹配等于把
#     这三类键全部放行 —— 那还是没修。但也**不能**用裸 substring：`direction`
#     含 `dir`、`south` 含 `out`，会把正常参数误判成路径字段。（`profile` 曾用来
#     举这个例子，2026-11-08 起它本身是**词表项**，不再承担反例角色。）
#   * 绝对判据 `^[A-Za-z]:(?![0-9])`：`C:secret.png`（盘符相对，Windows 上落到
#     C 盘 cwd）必须当绝对路径送 roots；负向前瞻挡掉 `R:1` 这类通道/曲线记号。
#     仍不误伤 `layer:3`（第二个字符不是 `:`）与 SVG path data。
_PATH_KEY_DIRECTION: dict[str, str] = {
    "input": "read", "src": "read", "source": "read",
    "output": "write", "out": "write", "dest": "write", "save": "write",
    "export": "write",
    "path": "any", "file": "any", "target": "any",
    # 目录类：`{"dir": "C:\\Windows"}` 曾经放行（`{dir}` 与键集无交集）。
    # 方向一律 "any"：目录既可被读也可被判为写目标，按 key 名分不出来。
    "dir": "any", "directory": "any", "folder": "any", "dst": "any",
    # 全小写复合词：驼峰拆分拆不出来（`fileName`→{file,name} 命中，但
    # `filename`→{filename} 不命中），只能作为词表项单独收。词表项一律**逐条
    # 实证**后再收，不靠前缀/后缀启发式：`profile` 同样以 `file` 结尾，但它进
    # 词表的原因见下一条，不是因为「以 file 结尾」。
    "filename": "any", "filepath": "any",
    # `profile`（colour profile）：schema 里 6 条命令明写收 `.icc` 文件路径 ——
    # `file.new`（"path to an .icc file"）、`edit.profileInfo`、
    # `image.mode.rgb|grayscale|cmyk|lab`（后 5 条字面写 `/path/to/profile.icc`）。
    # 方向 any：既可是读入的 ICC，也可被判为写目标。
    # 关键：**值判据只对「绝对 / 含 `..`」生效**，所以 `sRGB` / `working` /
    # `Default` / `document` 这类预设名天然放行 —— 不会为了补这条漏判把最常见
    # 用法打掉。可复现口径（替代早前不可复现的「扫过 84 个」）：拿
    # `probe/pc_commands.json` + `probe/vc_commands.json` 全量扫 `profile` 的文档
    # 取值，被值判据拒掉的**唯一一类**是 schema 里 5 处 `/path/to/profile.icc`
    # **占位示例**（占位符不是真实取值）；所有预设名 / id 全部 PASS。
    "profile": "any",
    # 路径形参实证（名字不像 path 却收路径）。前四项 any、后两项 read：
    #   `file.automate.batch` 的 `errorLog`（写原语）、`graphics.template.apply`
    #   的 `template`（收 id|name|path）、`file.new` 的 `mesh`（收 .glb/.gltf 路径）、
    #   `folder.move` 的 `into`（目标目录）；
    #   `file.automate.runDroplet` 的 `droplet`（**执行原语**：让 CLI 去跑一个
    #   droplet 文件，越界 = 执行任意脚本，按 read 判）、`references.sources` 的
    #   `import`（导入源 file|folder，只读）。`file.exportFrame` 的 `import` 是
    #   bool，`isinstance(str)` 分支天然跳过，不会误伤。
    # 仍故意**不收** `captured`/`videoPreviews`/`audioPreviews`（单命令私有、太窄）
    # 与 `from`/`to`（太泛，会把普通字段当路径）。见 validate_params 的已知边界。
    "log": "any", "template": "any", "mesh": "any", "into": "any",
    "droplet": "read", "import": "read",
}
# 由映射表派生，避免两张表各自漂移（判据集合天然等于映射的键集）。
_PATH_KEY_TOKENS: frozenset[str] = frozenset(_PATH_KEY_DIRECTION)
# 深度上限走 fail-closed：静默「到上限就停」等于把「上限 + 1」变成下一条旁路。
# 取 16 而不是 8：真实关键帧骨架 `animation→tracks→[dict]→keys→[dict]→k`
# 本身就吃掉 6 层容器，外面再套两层无关包装就到 8 层 —— 旧上限 8 把这种
# 正常参数直接误杀（可用性 bug）。
_PATH_DEPTH_MAX = 16
_ABS_PATH_RE = re.compile(r"^[A-Za-z]:(?![0-9])")
# `file:` 指向**本地文件**，是路径的另一种拼写：剥前缀后按路径判据继续走。
# 不能当 URL 放过 —— `file:///C:/Windows/win.ini` 既不是盘符开头也不含 `..`，
# 旧实现一路 ALLOW（同一个目标的另一种拼写被漏掉，闸门就只挡得住照字面抄的例子）。
# [依据限定，2026-11-08 云严真 CLI 实测] 本机 CLI 对四种 `file:` 拼写
# （`file:///C:/…` / `file:///Windows/…` / `file:C:\…` / `file://server/share/…`）
# 全部返回 `os error 123`（参数不合法）—— **本机 CLI 根本不解析这种拼写**。
# 所以本分支不能算「修掉了一个本机可用漏洞」，属**跨平台保守**：若某个平台的 CLI
# 接受该拼写，这里就是唯一防线。别据本条宣称「本机已修复的真实洞」。
_FILE_SCHEME_RE = re.compile(r"(?i)^file:")
# 其余 `scheme://` 的形状（**不**等于「是 URL」）：`..` 是否跳过由下面的白名单决定。
# 要求 `://` 而非裸 `scheme:`，才不会把 `layer:3` / `R:1` / 内联 `x:y` 当 URL。
# 带捕获组是为了取 scheme 名去查白名单。
_URL_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://")
# 只对**真实 URL scheme** 跳过 `..`：白名单，不是「任何 `scheme://`」。
# 反例（2026-11-08 云严真 CLI 实测）：Windows 会把 `xyz://` 折叠成目录名 `xyz`、
# 并把 `..` 照常消解 —— `{"path":"xyz://../../../out.ase"}` 闸门放行、CLI 在
# roots 外落盘 2296 B。所以「任何 scheme:// 都当 URL」等于把 `..` 判据整体作废。
# 单字母 `x://` 不在此列也安全：它先命中 `_ABS_PATH_RE` 走 roots 分支。
# 白名单之外默认**当作路径**（fail-closed）：`ftp://` 等宁可偏严，也不开同族旁路。
# 本机只实测到 `http` / `https` 是「CLI 会当 URL 下载」的形态；**新增 scheme 必须
# 先真 CLI 实测**（能下载 / 不落盘到 roots 外）再加，别按「应该是 URL」推测。
_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})
# `--new` / `--sample` 的 dict payload 是 CLI 固定窄 schema（width/height/
# background/colorMode/name/profile…）。[2026-11-08 变更] `profile` **已进通用
# 词表**（见 `_PATH_KEY_DIRECTION`），所以这张表与 `extra_path_keys` 形参**当前
# 冗余** —— 保留不删，留给「按通道 / 按命令 id 补键」的未来用途（`script` 在
# `file.scripts.browse` 是内联正文、在 `file.scripts.scriptEventsManager` 是路径，
# 这种相反语义只能按命令粒度补）。**不要**对这条通道改用「任何字符串都按路径判」
# 的严判据：那会把 `{"name": "A: study"}` 这种文档名按盘符判据误杀，报错还谎称
# 路径越界。其余键名仍走常规 token 判据 —— `--sample` 若收 `template`，
# `template` 本就在词表里。
NEW_DOC_PATH_KEYS: frozenset[str] = frozenset({"profile"})


def _violate(reason: str) -> None:
    raise PolicyViolation("POLICY_VIOLATION", "", reason=reason)


# ---------------------------------------------------------------------------
# 命名空间归一化（§5.5 核心回归）
# ---------------------------------------------------------------------------
def normalize_command_id(
    cmd: str, spec: Any, target: Literal["engine", "serve"] = "engine",
) -> str:
    """``doc.*`` ↔ ``document.*`` 双向归一化；非该前缀的命令原样保留（幂等）。

    ``target="engine"``：``doc.inspect`` → ``document.inspect``（`run --cmd`）
    ``target="serve"`` ：``document.inspect`` → ``doc.inspect``（serve method）
    """
    name = str(cmd or "").strip()
    if not name:
        return name
    engine_ns = str(getattr(spec, "engine_namespace", "document") or "document")
    serve_ns = str(getattr(spec, "serve_namespace", "doc") or "doc")
    if target == "engine":
        if name == serve_ns:
            return engine_ns
        if name.startswith(serve_ns + "."):
            return engine_ns + name[len(serve_ns):]
        return name
    if name == engine_ns:
        return serve_ns
    if name.startswith(engine_ns + "."):
        return serve_ns + name[len(engine_ns):]
    return name


def is_readonly(cmd: str, spec: Any) -> bool:
    """按 ``spec.readonly_prefixes`` 判定只读（供 jobs 区分编辑类，绝不重发）。"""
    name = str(cmd or "").strip()
    prefixes = getattr(spec, "readonly_prefixes", ()) or ()
    return any(name == str(p) or name.startswith(str(p) + ".") for p in prefixes)


def static_is_allowed(cmd: str, spec: Any = None) -> bool:
    """静态命名空间白名单（fail-closed 兜底，无 discovery 缓存时使用）。

    全局表只覆盖 PhotoCraft 系；``spec.static_allowed_prefixes`` 追加该 app
    实测的顶层命名空间（EffectCraft 的 ``comp.``/``layer.`` 等否则会被误拒）。
    """
    name = str(cmd or "").strip()
    if not name or not _CMD_ID_RE.match(name):
        return False
    if name in STATIC_ALLOWED_EXACT:
        return True
    extra = tuple(getattr(spec, "static_allowed_prefixes", ()) or ())
    return any(name.startswith(prefix) for prefix in STATIC_ALLOWED_PREFIXES + extra)


def check_command_allowed(
    cmd: str,
    spec: Any,
    policy: CommandPolicy | None = None,
    catalog_ids: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
) -> None:
    """命令白名单校验：空 id / 黑名单 / 注册表未命中 → ``POLICY_VIOLATION``。

    ``catalog_ids`` 非空 = 有 discovery 缓存，按**全量注册表**判；
    为空 = 缓存不可用（且 entry 内禁止 spawn），回退静态命名空间白名单。
    """
    rules = policy or CommandPolicy()
    name = str(cmd or "").strip()
    if not name:
        _violate("命令 id 为空")
    if not _CMD_ID_RE.match(name):
        _violate(f"命令 id 含非法字符：{name}")

    if name in (rules.deny_exact or ()):
        _violate(f"命令 {name} 在拒绝清单中")
    for prefix in rules.deny_prefixes or ():
        token = str(prefix)
        if token and (name == token.rstrip(".") or name.startswith(token)):
            _violate(f"命令 {name} 命中拒绝前缀 {token}")

    if rules.require_registry_match:
        ids = set(catalog_ids or ())
        if ids:
            if name not in ids:
                _violate(f"命令 {name} 未在命令注册表中")
        elif not static_is_allowed(name, spec):
            _violate(f"命令 {name} 未在已知命名空间白名单内")
    return None


# ---------------------------------------------------------------------------
# 序列规划
# ---------------------------------------------------------------------------
def plan_inspect(steps: list[Step], spec: Any, enabled: bool = True) -> list[Step]:
    """``inspect=True`` 时在末尾追加检查命令（幂等，不重复追加）。

    默认 `{engine_namespace}.inspect`；``spec.inspect_command`` 非空时用它
    （EffectCraft 实测是 `comp.info`，没有 `document.inspect`）。
    """
    out = list(steps or ())
    if not enabled:
        return out
    engine_ns = str(getattr(spec, "engine_namespace", "document") or "document")
    inspect_id = str(getattr(spec, "inspect_command", "") or "") or f"{engine_ns}.inspect"
    if out and normalize_command_id(out[-1].cmd, spec, "engine") == inspect_id:
        return out
    out.append(Step(cmd=inspect_id, params={}))
    return out


# ---------------------------------------------------------------------------
# argv 构造
# ---------------------------------------------------------------------------
def _append_source(argv: list[str], spec: Any, source: Source) -> list[str]:
    input_file = str(getattr(source, "input_file", "") or "")
    if input_file:
        flag = str(getattr(spec, "input_flag", "") or "")
        if flag:
            argv.extend([flag, input_file])
        else:
            argv.append(input_file)      # positional（PhotoCraft）
    new_doc = getattr(source, "new_doc", None)
    new_flag = str(getattr(spec, "new_flag", "") or "")
    if new_doc is not None and new_flag:
        argv.extend([new_flag, json.dumps(new_doc, ensure_ascii=False)])
    return argv


def _append_steps(argv: list[str], spec: Any, steps: list[Step]) -> list[str]:
    cmd_flag = str(getattr(spec, "cmd_flag", "--cmd") or "--cmd")
    params_flag = str(getattr(spec, "params_flag", "--params") or "--params")
    for step in steps or ():
        argv.extend([cmd_flag, str(step.cmd)])
        if params_flag:
            argv.extend([params_flag, json.dumps(step.params or {}, ensure_ascii=False)])
    return argv


def build_argv_run_cmd(
    spec: Any, steps: list[Step], source: Source, out_file: str,
    policy: RootsPolicy | None = None,      # noqa: ARG001 - 保持 builder 签名统一
    script_path: str = "",                  # noqa: ARG001 - 本族不需要脚本
) -> list[str]:
    """``<cli> run [FILE|--in FILE] [--new JSON] (--cmd ID --params JSON)* --out FILE``。

    PhotoCraft / VectorCraft 共用；**不带 root flag**（`run` 不接受，实测 exit 2）。
    """
    cli = str(getattr(spec, "cli_path", "") or "")
    if not cli:
        _violate("CLI 路径为空，无法构造 argv")
    argv: list[str] = [cli, "run"]
    argv = _append_source(argv, spec, source or Source())
    argv = _append_steps(argv, spec, steps)
    # ⚠️ 真机实测（RESULTS §6）：`photocraft-cli run` **不接受** `--automation-read-root`
    # / `--automation-write-root` → exit 2 `unexpected argument`。这两个 flag 只存在于
    # `serve` / `mcp` 子命令。v1 主通道 CLI_RUN 不带 root flag，
    # roots 由插件侧 `roots.resolve_and_check()` 强制（defense in depth 仍在）。
    output_flag = str(getattr(spec, "output_flag", "") or "")
    if out_file and output_flag:
        argv.extend([output_flag, str(out_file)])
    return argv


def build_argv_tool_subcmd(
    spec: Any, steps: list[Step], source: Source, out_file: str,
    policy: RootsPolicy | None = None,      # noqa: ARG001 - 保持 builder 签名统一
    script_path: str = "",                  # noqa: ARG001 - 本族不需要脚本
) -> list[str]:
    """Craft Anything：``<cli> <tool> [--params JSON] --json --project <PROJECT>``。

    三处实机差异（§15.2）：
      * **无** ``--cmd`` / ``--out`` / ``--new``；
      * ``--json`` 无值 = 输出格式（带值 = 传 params），故这里只作输出开关；
      * ``--project`` 必须指向**尚不存在**的路径（jobs 侧用 ``<data>/<job_id>.cany``）。
    另：一次只支持一个 tool 子命令，多步序列必须拆成多次调用。
    """
    cli = str(getattr(spec, "cli_path", "") or "")
    if not cli:
        _violate("CLI 路径为空，无法构造 argv")
    ordered = list(steps or ())
    if not ordered:
        _violate("Craft Anything 需要一个 tool 子命令")
    if len(ordered) > 1:
        _violate("Craft Anything 一次只支持一个 tool 子命令，请拆成多次 run_sequence")
    tool = str(ordered[0].cmd or "").strip()
    if not tool:
        _violate("tool 子命令为空")

    argv: list[str] = [cli, tool]
    params = ordered[0].params or {}
    if params:
        params_flag = str(getattr(spec, "params_flag", "--params") or "--params")
        argv.extend([params_flag, json.dumps(params, ensure_ascii=False)])
    json_flag = str(getattr(spec, "commands_json_flag", "--json") or "--json")
    argv.append(json_flag)
    input_file = str(getattr(source, "input_file", "") or "")
    if input_file:
        argv.extend([str(getattr(spec, "input_flag", "--project") or "--project"), input_file])
    return argv



# ---------------------------------------------------------------------------
# §19 新增 argv 族（把 v1 的 3 个 app 扩到全 17 个）
#
# 为什么不是「加配置行」：实机逐个摸下来，17 个 app 分成 6 个 argv 族，
# 差异在**位置 vs 具名、内联 vs 分离、脚本 vs 命令行**三个维度上，
# 不是同一套 flag 的不同取值。所以每族一个 builder，禁止猜测。
# ---------------------------------------------------------------------------
def _cli_of(spec: Any) -> str:
    cli = str(getattr(spec, "cli_path", "") or "")
    if not cli:
        _violate("CLI 路径为空，无法构造 argv")
    return cli


def _save_pair(spec: Any, out_file: str) -> list[str]:
    """保存/导出 flag：``save_flag`` 优先，退回 ``output_flag``。

    17 个 app 的保存 flag 实际有 4 种：``--out`` / ``--export`` / ``--save`` / ``-o``。
    都没有的（LightCraft）只能靠命令自己导出，此时 ``out_file`` 会被忽略。
    """
    if not out_file:
        return []
    flag = str(getattr(spec, "save_flag", "") or "") or str(getattr(spec, "output_flag", "") or "")
    if not flag:
        return []
    return [flag, str(out_file)]


def _source_pair(spec: Any, source: Source) -> list[str]:
    """输入源 → argv 片段（族 B/C/D 用）。

    与族 A 的 :func:`_append_source` 区别：``--sample`` 类 flag **不吃 JSON**
    （GridCraft 的 ``--sample`` 要模型名，DesignCraft 的 ``--sample`` 是开关），
    所以 ``new_doc`` 在这里只接受字符串；传 dict 直接报错，不发出错误 argv。
    """
    argv: list[str] = []
    input_file = str(getattr(source, "input_file", "") or "")
    if input_file:
        flag = str(getattr(spec, "input_flag", "") or "")
        argv.extend([flag, input_file] if flag else [input_file])
    new_doc = getattr(source, "new_doc", None)
    new_flag = str(getattr(spec, "new_flag", "") or "")
    if new_doc is not None and new_flag:
        if isinstance(new_doc, str):
            argv.extend([new_flag, new_doc])
        elif new_flag == "--new":
            argv.extend([new_flag, json.dumps(new_doc, ensure_ascii=False)])
        else:
            _violate(
                f"{getattr(spec, 'app_id', '')!r} 的 {new_flag} 不接受 JSON 参数，"
                "请传字符串（或省略 new_doc）"
            )
    return argv


def build_argv_cmd_inline(
    spec: Any, steps: list[Step], source: Source, out_file: str,
    policy: RootsPolicy | None = None,   # noqa: ARG001 - 保持 builder 签名统一
    script_path: str = "",              # noqa: ARG001 - 本族不需要脚本
) -> list[str]:
    """族 B：``<cli> run [input] (--cmd 'ID={json}')* [save F]``。

    实机差异：CADCraft 的 ``--cmd`` 取值是「id + **空格** + JSON」
    （``--cmd <id {json}>``），其余 5 个是「id=JSON」。
    """
    argv: list[str] = [_cli_of(spec), "run"]
    argv += _source_pair(spec, source or Source())
    cmd_flag = str(getattr(spec, "cmd_flag", "--cmd") or "--cmd")
    sep = " " if str(getattr(spec, "cmd_join", "")) == "inline_space" else "="
    for step in steps or ():
        params = json.dumps(step.params or {}, ensure_ascii=False)
        argv.extend([cmd_flag, f"{step.cmd}{sep}{params}"])
    argv += _save_pair(spec, out_file)
    return argv


def build_argv_cmd_positional(
    spec: Any, steps: list[Step], source: Source, out_file: str,
    policy: RootsPolicy | None = None,   # noqa: ARG001
    script_path: str = "",              # noqa: ARG001
) -> list[str]:
    """族 C：``<cli> run [input] (<cmd> <json>)* [save F]``（位置参数、无 flag）。

    TextureCraft / MaterialCraft 一个 ``run`` 只吃**一条**命令
    （``single_step=True``）——多步直接报错，不静默丢步。
    """
    ordered = list(steps or ())
    if not ordered:
        _violate("位置参数族需要一个命令")
    # TextureCraft / MaterialCraft 的签名是 `run <input> <command-id> [params]`：
    # 没有 input 时照常拼 argv，命令 id 会被当成 <input>，CLI 报
    # `missing <command-id>`（实机踩到 texturecraft）。这种必须当场拒绝，
    # 而不是把错 argv 发出去再让用户看一句天书。
    if bool(getattr(spec, "requires_input", False)):
        if not str(getattr(source, "input_file", "") or ""):
            _violate(
                f"{getattr(spec, 'app_id', '')!r} 的 run 必须带输入文件"
                "（`run <input> <command-id>`），请提供 input_file"
            )
    if bool(getattr(spec, "single_step", False)) and len(ordered) > 1:
        _violate(
            f"{getattr(spec, 'app_id', '')!r} 一次只接受一条命令；"
            f"收到 {len(ordered)} 步，请拆成多次 run_sequence"
        )
    argv: list[str] = [_cli_of(spec), "run"]
    argv += _source_pair(spec, source or Source())
    for step in ordered:
        argv.append(str(step.cmd))
        # EffectCraft 的 `run <id> <json>` 必填；空步骤也要发 "{}"。
        argv.append(json.dumps(step.params or {}, ensure_ascii=False))
    argv += _save_pair(spec, out_file)
    return argv


def serialize_script(
    spec: Any, steps: list[Step], source: Source, out_file: str,
) -> str:
    """把步骤序列序列化成该 app 认的脚本文本（纯函数，便于单测）。

    5 种实机格式，全部来自各自 ``--help`` 原文：
      * ``array_cmd``  ``[{"command": id, "params": {}}]``  FigCraft / LightCraft / EffectCraft
      * ``array_id``   ``[{"id": id, "params": {}}]``        （保留位，暂无 app 使用）
      * ``jsonl_cmd``  每行 ``{"command": id, "params": {}}`` LightCraft（只要 JSONL，不认数组）
      * ``jsonl_id``   每行 ``{"id": id, "params": {}}``     FilmCraft
      * ``array_tool`` ``[{"tool": id, "args": {}}]``        PdfCraft
      * ``batch_dict`` ``{"open":…, "steps":[…], "save":…}`` TextureCraft
    """
    fmt = str(getattr(spec, "script_format", "") or "")
    ordered = list(steps or ())
    if fmt == "array_cmd":
        return json.dumps(
            [{"command": s.cmd, "params": s.params or {}} for s in ordered],
            ensure_ascii=False, indent=1,
        )
    if fmt == "array_id":
        return json.dumps(
            [{"id": s.cmd, "params": s.params or {}} for s in ordered],
            ensure_ascii=False, indent=1,
        )
    if fmt == "array_tool":
        return json.dumps(
            [{"tool": s.cmd, "args": s.params or {}} for s in ordered],
            ensure_ascii=False, indent=1,
        )
    if fmt == "jsonl_cmd":
        return "".join(
            json.dumps({"command": s.cmd, "params": s.params or {}}, ensure_ascii=False) + "\n"
            for s in ordered
        )
    if fmt == "jsonl_id":
        return "".join(
            json.dumps({"id": s.cmd, "params": s.params or {}}, ensure_ascii=False) + "\n"
            for s in ordered
        )
    if fmt == "batch_dict":
        body: dict[str, Any] = {
            "steps": [{"id": s.cmd, "params": s.params or {}} for s in ordered],
        }
        input_file = str(getattr(source, "input_file", "") or "")
        if input_file:
            body["open"] = input_file
        if out_file:
            body["save"] = out_file
        return json.dumps(body, ensure_ascii=False, indent=1)
    _violate(f"未知 script_format：{fmt or '(空)'}")
    return ""  # 不可达，_violate 必抛


def build_argv_script_file(
    spec: Any, steps: list[Step], source: Source, out_file: str,
    policy: RootsPolicy | None = None,   # noqa: ARG001
    script_path: str = "",
) -> list[str]:
    """族 D：``<cli> run <script>`` 或 ``<cli> run --script <file>``。

    ⚠️ 这是**唯一有副作用**的 builder：它把脚本写到 ``script_path``。
    原因：argv 必须引用一个已存在的脚本文件，而 ``build_argv`` 的返回类型
    是纯 argv，没法把内容带回去。调用方（jobs）负责给出唯一路径。
    """
    ordered = list(steps or ())
    if not ordered:
        _violate("脚本族需要一个命令")
    path = str(script_path or "")
    if not path:
        _violate("脚本族需要 script_path：调用方必须提供 <data>/<job_id>.script.* ")
    text = serialize_script(spec, ordered, source or Source(), out_file)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        _violate(f"写脚本失败：{exc}")

    argv: list[str] = [_cli_of(spec)]
    subcmd = str(getattr(spec, "script_subcmd", "") or "")
    flag = str(getattr(spec, "script_flag", "") or "")
    if subcmd:
        argv.extend([subcmd, path])
    elif flag:
        argv.extend(["run", flag, path])
    else:
        _violate("脚本族缺少 script_subcmd 或 script_flag")
    src = source or Source()
    input_file = str(getattr(src, "input_file", "") or "")
    inflag = str(getattr(spec, "input_flag", "") or "")
    if input_file and inflag:
        argv.extend([inflag, input_file])
    argv += _save_pair(spec, out_file)
    return argv


ARGV_BUILDERS: dict[str, Callable[..., list[str]]] = {
    "run_cmd": build_argv_run_cmd,
    "tool_subcmd": build_argv_tool_subcmd,
    "cmd_inline": build_argv_cmd_inline,
    "cmd_positional": build_argv_cmd_positional,
    "script_file": build_argv_script_file,
}


def build_argv(
    spec: Any, steps: list[Step], source: Source, out_file: str,
    policy: RootsPolicy | None = None,
    script_path: str = "",
) -> list[str]:
    """按 ``spec.builder`` 派发；未知 builder → ``POLICY_VIOLATION``（不猜）。

    ``script_path`` 只对 ``script_file`` 族有意义（它要落脚本），其余族忽略。
    统一传参而不是按族改签名，是为了让 jobs 侧只有一条调用路径。
    """
    builder = ARGV_BUILDERS.get(str(getattr(spec, "builder", "run_cmd") or "run_cmd"))
    if builder is None:
        _violate(f"未知的 argv builder：{getattr(spec, 'builder', '')}")
    return builder(
        spec, list(steps or ()), source or Source(), out_file, policy,
        script_path=script_path,
    )


# ---------------------------------------------------------------------------
# params 内层校验（§13.2 P5：@llm_tool arguments 是模型可控 kwargs）
# ---------------------------------------------------------------------------
def _key_tokens(key: Any) -> frozenset[str]:
    """把键名拆成词元集合：`outPath` → {out, path}，`output_path` → {output, path}。

    先按「小写/数字 → 大写」的驼峰断点插空格，再按非字母数字切分。
    只做词元级比对（`tokens & _PATH_KEY_TOKENS`），**不做** substring：
    `profile` → {profile}、`south` → {south}，都不该被当成路径字段。

    另外对**复数**键补一个去尾 `s` 的形式（`files`→`file`、`paths`→`path`、
    `outputs`→`output`）：批量参数写复数是最自然的命名，只认单数必然漏一批。
    阈值 `len > 3` 防 `bus`/`its` 这类三字母词被砍成两字母；这里**只做去尾 s**
    这一条规则，不做通用词形还原（`class`→`clas`、`status`→`statu` 都落不到键集上，
    零影响）。

    最后补一个**整键压缩**候选（去所有非字母数字后整段小写）：`pAtH`→`path`、
    `paTh`→`path`。这类乱序大小写靠驼峰断点拆不出来（`pAtH` 只在 `t`→`H` 处断，
    得到 {p, at, h}），但整键压缩一眼就是路径键。它不是裸 substring：
    `profile`→`profile`、`south`→`south`，整段压缩后仍与键集无交集。
    """
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(key or ""))
    tokens = {tok.lower() for tok in re.split(r"[^A-Za-z0-9]+", text) if tok}
    squeezed = re.sub(r"[^A-Za-z0-9]+", "", str(key or "")).lower()
    if squeezed:
        tokens.add(squeezed)
    return frozenset(
        tokens | {tok[:-1] for tok in tokens if len(tok) > 3 and tok.endswith("s")}
    )


def _direction_of_key(tokens: frozenset[str]) -> str:
    """命中 token 的读写方向：唯一方向就用它，冲突或只有 "any" → 并集（"any"）。"""
    hits = {
        _PATH_KEY_DIRECTION[tok] for tok in tokens if tok in _PATH_KEY_DIRECTION
    }
    hits.discard("any")
    return hits.pop() if len(hits) == 1 else "any"


def _param_roots_for(
    roots: RootsPolicy, direction: Literal["read", "write", "any"],
    tokens: frozenset[str],
) -> tuple[tuple[str, ...], Literal["read", "write"]]:
    """按「显式方向 > 按键映射 > 并集」决定这条值该跟哪套 roots 比。

    显式 ``direction="read"/"write"`` **覆盖**映射：调用方明确知道语义时（比如
    `input_file` 一定是读）不该被 key 名带偏。
    """
    if direction == "read":
        return tuple(roots.read_roots or ()), "read"
    if direction == "write":
        return tuple(roots.write_roots or ()), "write"
    want = _direction_of_key(tokens)
    if want == "read":
        return tuple(roots.read_roots or ()), "read"
    if want == "write":
        return tuple(roots.write_roots or ()), "write"
    # 认知不明或无方向的 key（path/file/target 等 "any" 命中，以及 `inputOutput`
    # 这类读写 token 真冲突）：取读根 ∪ 写根，不误伤「用户只用 allow_user_dirs
    # 授权了读目录」的正常情形。注意 `outPath` 不属此例 —— 它的 `path` 是 "any"
    # 被 discard 后只剩 write 一个方向，走上面那条 write 分支。
    return (tuple(roots.read_roots or ()) + tuple(roots.write_roots or ()), "read")


def _check_one_param_path(
    roots: RootsPolicy, direction: Literal["read", "write", "any"],
    key: str, tokens: frozenset[str], raw: str,
) -> None:
    """单个字符串值：像绝对路径 → 交给 roots；相对路径含 `..` 段 → 直接拒。

    `..` 在这里拒而不是只交给 roots，是**两套基准 + 错误码契约**，不是「roots 查不出」：
      * [FACT 2026-11-08 实测] `roots.resolve_and_check("../../secret.png",
        (data, cache), "read")` 实际返回 `PATH_UNSAFE` —— 所以「交给 roots 也
        压根不会经过比对口径」是错的说法，改掉；
      * 但 roots 是**拿第一个 root 当 base** 做归一，语义上不等于 CLI 的 cwd
        （`jobs._cwd_for()` 返回 `E:\\Program Files\\Artcraft\\PhotoCraft` 一类安装
        目录）。同一个 `..` 串在两套 base 下指向不同位置，插件层自己拒才不依赖
        「CLI 的 cwd 恰好也在 roots 里」这个未被保证的前提；
      * 错误码也刻意分开：`..` → `POLICY_VIOLATION`（本模块的输入契约），
        越界 → `PATH_OUTSIDE_ROOTS` / `PATH_UNSAFE`（roots 的落盘判据）。
    """
    text = str(raw or "").strip()
    if not text:
        return
    # `file:` 指向本地文件：剥前缀后按路径判据继续走 —— **不能**当 URL 放过
    # （`file:///C:/Windows/win.ini` 既不是盘符也不含 `..`，曾一路 ALLOW）。
    #
    # [性质：一致性 / 可用性缺陷，**不是越界洞**] 默认策略（只授权 data/cache）
    # 下，UNC 原生拼写与 RFC 8089 的 file://host/... 拼写**两种都 fail-closed
    # 拒绝**，没有任何越界读写被放开。缺陷是：用户显式授权了一个 UNC 读根后，
    # 旧实现把**任何** authority 都折叠成「当前盘根」并丢掉远程主机名，于是
    # 同一个资源写成 UNC 拼写 ALLOW、写成 file://host 拼写却 REJECT。这里修的
    # 是这个不一致，不是「补越界 / 修越权」（见 tests/test_params_roots.py B1 段）。
    #
    # RFC 8089 三分支（authority = file: 后紧跟两个分隔符起的 host 段）：
    #   1. 空 authority 或 localhost（大小写不敏感）→ 等价**本地路径**：
    #      path_body 命中 _ABS_PATH_RE 就原样用；否则补一个反斜杠变当前盘根
    #      （保持 file:///Windows/win.ini 既有 REJECT 行为）。
    #   2. 其它 authority → 拼成 UNC 拼写（双反斜杠 + host + 反斜杠 + path_body，
    #      正斜杠换反斜杠），交给下面「单分隔符开头 → roots.resolve_and_check」
    #      分支比对。roots 已支持 UNC：normalize_win_path 保留前导双斜杠，
    #      _is_within 能比对 UNC 根；字面 `..` 由 _assert_safe_segments 拒，
    #      空白族由本分支末尾的统一判据拒（见下）。
    #   3. 无 authority 的相对形（file:draft.png，§2 允许、按 cwd 解析）→
    #      不补分隔符，继续走后面的 is_url / `..` 判据。
    # 刻意不做端口 / userinfo / 百分号解码：形状不认识就保守 fail-closed。
    # file://C:/Windows/win.ini 这种「盘符被当 authority」的形状拼出的 UNC
    # 含 `C:` 段，`_assert_safe_segments` 按 ADS 判据拒掉，不会被放行。
    #
    # [裁决 2 · 前导分隔符计数] 剥分隔符**之前**先数前导 [\\/] 连续个数 n：
    # n>=4 不是合法 RFC 8089 形状，直接 _violate（沿用现有 POLICY_VIOLATION，
    # 不新增错误码）；n==3 保持空 authority 处理 —— 注意 n==3 的 body 也以
    # 分隔符开头，绝不能拿「body 首字符是分隔符」当 n>=4 的判据，否则打红
    # file:///C:/... 正向；n==2 走三分支；n<=1 相对形保持现状。旧实现会把
    # n>=4 拼成 `\/server/...`，被 normalize_win_path 洗成 UNC `//server/...`，
    # 语义从「当前盘根」错位成「远程 UNC」，授权 UNC 根时会被误放行。
    # [裁决 1 · 新通道从严] file: 是 B1 新开的输入通道，不继承原生 UNC 历史
    # 通道的宽松面：roots._assert_safe_segments 只认**字面** ".."，而本分支
    # 产出的 UNC / 本地绝对路径会在下面「单分隔符开头」分支提前 return 送
    # roots，绕过后面 seg.strip()==".." 判据。故对**所有**归一后的 text（含
    # 相对形）统一再查一遍，命中即 _violate。与原生 UNC 通道的有意不一致由
    # 下一轮 S10 在 roots 层统一加 strip() 收敛；`%2e%2e` 不加解码判据
    # （CLI 解码语义未核验，记已知边界第 10 条）。
    if _FILE_SCHEME_RE.match(text):
        rest = text[len("file:"):]
        n_sep = len(rest) - len(rest.lstrip("/\\"))
        if n_sep >= 4:
            _violate(f"params.{key} 不允许 >=4 个前导分隔符的 file: URI：{text}")
        if n_sep >= 2:
            # 去掉**恰好**两个前导分隔符，再按分隔符切**一次**取 authority 与
            # 路径本体。注意 file:///C:/... 去两个后剩 /C:/...，首段是空串
            # （= 空 authority），别把 C: 误当主机名。
            body = rest[2:]
            parts = re.split(r"[\\/]", body, maxsplit=1)
            authority = parts[0]
            path_body = parts[1] if len(parts) > 1 else ""
            if authority == "" or authority.lower() == "localhost":
                # 分支 1：空 authority / localhost 等价本地路径
                if path_body and _ABS_PATH_RE.match(path_body):
                    text = path_body
                else:
                    text = "\\" + path_body
            else:
                # 分支 2：远程 authority → UNC 拼写（交给下面 roots 比对）
                text = "\\\\" + authority + "\\" + path_body.replace("/", "\\")
        else:
            # 分支 3：无 authority 的相对形，按 cwd 解析；不补分隔符。
            rest = rest.lstrip("/\\")
            if not rest:
                return
            text = rest
        # 统一空白族 `..` 判据：覆盖 UNC / 本地绝对 / 相对形三种产出，避免
        # 归一带出的绝对路径提前送 roots、绕过下面的 seg.strip() 判据。
        if any(seg.strip() == ".." for seg in re.split(r"[\\/]", text)):
            _violate(f"params.{key} 不允许出现 `..`：{text}")
    # 只有白名单内的真实 URL（`http`/`https`）才跳过 `..` 段判据（`..` 由服务端按
    # RFC 3986 归一，`https://x/a/../b.png` 合法）。**其余 `scheme://` 一律按路径走**：
    # Windows 把 `xyz://` 折成目录名 `xyz` 并照常消解 `..`（实测 CLI 在 roots 外落盘），
    # 放宽成「任何 scheme://」等于把 `..` 判据整体作废。必须在剥掉 `file:` 之后判。
    _m = _URL_SCHEME_RE.match(text)
    is_url = bool(_m) and _m.group(1).lower() in _URL_SCHEMES
    # 单分隔符开头（`\Windows\win.ini` / `/Windows/win.ini`）在 Windows 上是
    # **当前盘根**，不是相对路径：放行等于让 CLI 从安装盘根目录读任意文件。
    # 绝对**判据形状**与 roots.normalize_win_path 的 is_abs 同口径（盘符正则 +
    # 首字符分隔符）。但 roots 在判 is_abs **之前**还会 `.strip('"')` /
    # `.strip("'")` + `os.path.expandvars`，本函数**不做** —— 因此带引号的
    # `"\\Windows\\win.ini"` 与 `%SYSTEMROOT%\\win.ini` 在这里不被识别为绝对路径
    # 而放行，属已声明边界（见 validate_params 已知边界；可利用性取决于 CLI 自己
    # 是否 strip 引号 / 展开变量）。另一半仍要记住：只改这里会是假闸门 ——
    # normalize 会把 `\x` 当相对路径拼进 root 返回 ALLOWED，那半边已在 roots 修掉。
    if _ABS_PATH_RE.match(text) or text[:1] in ("\\", "/"):
        allowed, label = _param_roots_for(roots, direction, tokens)
        # 失败语义沿用 roots：PATH_OUTSIDE_ROOTS / PATH_UNSAFE（不新增错误码）
        roots_mod.resolve_and_check(text, allowed, label)
        return
    # `".."` 的**空白变体**一并拒，这是**超额拦截**（fail-closed），不是 Windows 语义：
    # [FACT 2026-11-08 实测] 只有字面 `".."` 落到父目录 —— `".. "` / `" .."` / `".. ."`
    # 经 os.stat 都落到**自身**；`"..\t"` 是**非法名**（`winerror 123`），stat 直接失败
    # （不是「不存在」）；`ntpath.abspath('C:\a\.. \x')` 也不折叠。
    # 这些段在 NTFS 上本来就打不开，拒掉没有可用性代价；反向的正常相对路径
    # `a\b\x.png` 没有 `strip()` 后等于 `..` 的段，不受影响。
    # 空白族必须收全（` \t\r\n\v\f\xa0\u3000` 这类 Unicode 空白）：用**不带参数**的
    # `str.strip()`（按 `str.isspace()` 剥）。写成 `strip(" \t")` 只挡住两种，
    # `"..\r"` / `"..\u00a0"` / `"..\u2003"` 全都 strip 不掉 → 与 `".."` 不等 → 放行。
    # 后手注意两条：① 别拿这段当依据去补更多拼写判据 —— 本闸门要挡的只有字面 `..`；
    # ② 别用 isdir 证明「越界」：末尾带空格的中间目录会让 isdir 返回 True，它不证明
    #    解析结果（本闸门第一版复核时就出现过这种误判）。
    if not is_url and any(seg.strip() == ".." for seg in re.split(r"[\\/]", text)):
        _violate(f"params.{key} 不允许出现 `..`：{text}")
    # 其余（普通相对文件名 / SVG path data / 白名单 URL / layer:3 / R:1）一律放行：
    # 「一律送 roots 检查」会把 `report~1.png`（8.3 短名）与 `layer:3`（ADS）误杀。


def _check_params_paths(
    params: dict[str, Any], roots: RootsPolicy,
    direction: Literal["read", "write", "any"] = "any",
    extra_path_keys: Iterable[str] = (),
) -> None:
    """递归下降查 params 里的路径字段（嵌套 = 等价旁路，不能只查顶层）。

    `params` 是 `json.dumps` **整段**传给 CLI 的，所以 `{"options":{"path":...}}`
    与顶层 `path` 到达 CLI 的效果完全一样。这里对 dict 的所有 value、list 的所有
    元素递归，深度超过 ``_PATH_DEPTH_MAX`` 直接拒（fail-closed）。

    ``extra_path_keys``：为**特定通道**额外认定为路径键的名字，并进通用词表。
    只给 ``--new`` 通道用（入口传 ``NEW_DOC_PATH_KEYS``）。[2026-11-08 变更]
    `profile` 已进 `_PATH_KEY_DIRECTION`，所以这张补键表**当前冗余**（保留不删，
    用途见 `validate_params` 已知边界 3）。仍用「补键」而不是「这条通道任何字符串
    都当路径」的严判据，是因为后者会把 `{"name": "A: study"}` 这种文档名按盘符
    判据误杀（报错还谎称路径越界）。

    凭证类键与 host/port 的检查**只在顶层做，嵌套层不查**（走 paths 递归时不带这两
    项判据）。口径要说准：**顶层** `{"tokens": 12}` 本来就会被拒 ——
    `_FORBIDDEN_PARAM_KEYS` 是子串匹配，`tokens` 含 `token`；不被误杀的只有
    **嵌套层**（`{"x": {"tokens": 12}}`、`{"meta": {"api_key_rotated_at": 1}}`）。
    这样切分是因为这两项是顶层调用契约，下推到任意深度会把正常统计数据一起杀掉。
    """
    key_set = _PATH_KEY_TOKENS | frozenset(extra_path_keys)

    def walk(value: Any, depth: int, key: str, tokens: frozenset[str]) -> None:
        if isinstance(value, (dict, list, tuple)) and depth > _PATH_DEPTH_MAX:
            # 只对**容器**计深度：把字符串叶子也算一层会让「上限 16」在 16 层容器处
            # 就拒（消息还说 >16，名不副实）。fail-closed 依旧：第 17 层容器直接拒，
            # 静默停止会把「上限 + 1 层」变成下一条旁路。
            _violate(f"params 嵌套过深（>{_PATH_DEPTH_MAX} 层）")
        if isinstance(value, dict):
            for child_key, child in value.items():
                child_tokens = _key_tokens(child_key)
                if tokens & key_set:
                    # 父 key 本身是路径键 → 子节点**继承**父词元：
                    # `{"path": {"x": "C:\\Windows\\win.ini"}}` 里子节点 key 是 `x`，
                    # 只按自己的 key 重算就与键集无交集 → 整棵子树放行（等价旁路）。
                    child_tokens = child_tokens | tokens
                walk(child, depth + 1, str(child_key), child_tokens)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    # list 元素本身没有 key，沿用父 key 的词元方向；非 str 元素跳过。
                    # 此处**不看父 key 是否为路径键**：批量命令的 list 键名无法穷举
                    # （files/images/layers/inputs…），只认映射表必然漏掉一批。
                    _check_one_param_path(roots, direction, key, tokens, item)
                else:
                    walk(item, depth + 1, key, tokens)
            return
        if isinstance(value, str) and tokens & key_set:
            _check_one_param_path(roots, direction, key, tokens, value)

    walk(params, 0, "", frozenset())


def validate_params(
    params: dict[str, Any], roots: RootsPolicy | None = None,
    direction: Literal["read", "write", "any"] = "any",
    extra_path_keys: Iterable[str] = (),
) -> None:
    """params 里禁止凭证类字段；path 类字段走 roots；host/port 必须字面 127.0.0.1。

    ``roots=None`` 时**整段路径检查跳过**（保持旧行为）：这条闸门让只给
    `validate_params(params)` 的历史调用零变化，roots 必须是显式递进来的责任。

    ``extra_path_keys`` 只给入口层的 ``--new`` payload 用（传 ``NEW_DOC_PATH_KEYS``）。
    [2026-11-08 变更] `profile` **已进通用词表**（方向 any，见 `_PATH_KEY_DIRECTION`
    里 profile 那段注释），所以 ``NEW_DOC_PATH_KEYS`` 与这条形参**当前冗余** ——
    保留不删，留给「按通道 / 按命令 id 补键」的未来用途：例 `script` 在
    `file.scripts.browse` 是**内联正文**、在 `file.scripts.scriptEventsManager` 是
    **路径**，这种相反语义只能按命令粒度补，不能进通用词表（见已知边界 4）。
    **不要**因为冗余就改成「这条通道任何字符串都按路径判」：那会把
    `{"name": "A: study"}` 这类文档名按盘符判据误杀，错误还会谎称路径越界。

    覆盖范围：
      * 检查的字符串值来自三处：① key 词元命 ``_PATH_KEY_TOKENS``（或调用方补的
        ``extra_path_keys``）的 dict 值（`outPath`/`srcFile`/`output_path`/`pAtH`
        都算，token 化 + 整键压缩而非 substring）；② **list 里的 str 元素一律
        检查**，不看父 key —— 批量命令的 list 键名（`files`/`images`/`layers`/
        `inputs`）无法穷举，只认映射必然漏一批；③ 父 key 是路径键时，其下
        **整棵 dict 子树继承**父词元。
      * 判为「像路径」才送 roots：盘符（含 `C:secret.png` 这种盘符相对）、单分隔符
        开头的当前盘根（`\\Windows\\win.ini` / `/Windows/win.ini`）、UNC。普通相对
        文件名 / SVG path data / `layer:3` / `R:1` 一律放行，只有相对路径里
        出现 `..` 段（含尾随/前导 Unicode 空白的变体：`".. "` / `"..\\r"` /
        `"..\\u00a0"` / `"..\\u2003"`）才直接拒。URL 是唯一例外，且**只认白名单**
        （`http` / `https`）：它们的 `..` 由服务端归一，不套用这条判据。

    已知边界（主理人 2026-11-08 逐条裁决「不修」，改动前请先回到裁决理由）：
      1. `{"options": {"name": "C:\\Windows\\win.ini"}}` 放行 —— 非路径 key 的字符串
         dict 值不查（`options.name` 是合成示例，实名命令清单见第 4 条）。这是刻意
         的代价：把任意字符串都当路径会把 SVG path data、`layer:3`、URL、8.3 短名
         一起误杀。若某命令的路径参数用了映射外的 key 名，应当往
         `_PATH_KEY_DIRECTION` 补 token，而不是在这里放宽判据。
      2. `R:abc`（字母通道名）被当盘符相对拒 —— **不放宽**绝对判据：改成
         `(?![0-9A-Za-z])` 会把 `C:secret.png` 一起放行，等于把刚补的洞开回去。
         宁可对极少数字母通道名偏严。
      3. 读写 token 冲突（`inputOutput` → {input, output}）走读根 ∪ 写根 —— 只会
         更严不会更松。
      4. 词表对「key 名不像路径、schema 却明写收路径」的实证 —— 口径一律是**命令
         条数**，不是 key 个数（同一个 key 可能被多条命令收）。本轮在
         `probe/pc_commands.json`（photocraft **993 条命令**）与
         `probe/vc_commands.json`（vectorcraft **933 条命令**）上逐条复核；
         `probe/ca_commands.json` 的 321 行是 **tool 形**（`{tool, summary}`，无
         params schema 字段），口径不同，**不参与计数**。
         * 已收进 `_PATH_KEY_DIRECTION`：
           `profile` —— photocraft **6 条命令**：`file.new`（写 "path to an .icc
           file"）+ `edit.profileInfo` + `image.mode.rgb|grayscale|cmyk|lab`
           （后 5 条字面写 `/path/to/profile.icc`）。若按更宽口径「profile 字段
           接受 .icc 路径」则 10 条（另含 `edit.assignProfile` /
           `edit.convertToProfile` / `edit.colorSettings` / `view.proofSetup`）；
           `droplet` —— `file.automate.runDroplet` **1 条**（执行原语）；
           `import` —— **可复核条数 = 0**：依据来源是 2026-11-08 的 N2 复核记录
           （`references.sources`），**未从命令表复核** —— 本地 pc/vc 两表都没有
           `references.*` 命名空间，也没有 `file.exportFrame`，所以这一条是「按
           记录收、留待补证」，不是「表里核到 1 条」。它的误伤面已核：两表里唯一
           以 import 开头的形参是 `edit.keyboardShortcuts.importKys`，取值
           `<.kys XML>` / `{path: .kys}` 全是相对值，零误杀；
           `template`（`graphics.template.apply` / `.thumbnail` /
           `file.newProjectFromTemplate`、`cadcraft new`）、`mesh`（`texturecraft
           file.new`）、`into`（`lightcraft folder.move`）—— 这三项来自此前 17-app
           扫，本轮**未重扫**（那 3 张表不在本机 `probe/` 里）。
         * **仍不进通用词表**（刻意，附理由）：
           `filmcraft file.projectSettings.scratchDisks` 的 `captured` /
           `videoPreviews` / `audioPreviews`（太窄：单命令私有字段，收进来等于为
           一条命令扩词表）、`filmcraft media.autoRelink` 的 `from` / `to`
           （太泛：这两个词在大量命令里是普通字段，收进来会把非路径值当路径）、
           `file.scripts.scriptEventsManager` 的 `script`（同一个 key 在
           `file.scripts.browse` 是**内联正文**：`{"script": "x:y"}` 会命中盘符
           判据 `^[A-Za-z]:(?![0-9])`、`"body: {color: red}"` 同理，收进词表立刻
           误杀正常脚本 —— 只能按命令 id 补键）。
           这些 key 的字符串值不会被判为路径，**除非**它挂在路径词元的父键下
           （第 ③ 条继承规则会兜住），或同批出现在 list 里（第 ② 条兜住）。
      5. `roots.resolve_and_check` 的 `direction` 形参目前**不参与**判定（只作语义
         标签），本模块传并集时标 "read"；将来它若开始按方向做额外判定，并集分支
         需要重新审。
      6. `..` / 深度超限走 `POLICY_VIOLATION`（`_violate`），路径越界走
         `PATH_OUTSIDE_ROOTS`/`PATH_UNSAFE`（`roots.resolve_and_check`）。错误码是
         19 条固定集合，不再细分。
      7. `file:` 协议是**本地文件的另一种拼写**，不是 URL：`_check_one_param_path`
         会剥掉 `file:` 前缀与 URI 的 authority 斜杠后**按路径判据继续走**
         （`file:///C:/Windows/win.ini` → `C:/Windows/win.ini` → 送 roots，拒），
         所以它不会被当 URL 放过；无盘符的 `file:///Windows/win.ini` 按 Windows
         语义补回一个分隔符当**当前盘根**拒掉。
         **依据强度要说准**：本机真 CLI 对四种 `file:` 拼写全部返回 `os error 123`
         （不解析该拼写），所以这条分支是**跨平台保守**，不是「本机已修复的真实
         洞」；若某平台 CLI 接受该拼写，它就是唯一防线（详见 `_FILE_SCHEME_RE`
         上方注释）。
         `..` 豁免**只给白名单**（`_URL_SCHEMES` = `http` / `https`）：其余
         `scheme://`（含 `ftp`，以及 `xyz://` 这类根本不是协议的串）一律按路径判据
         走 —— 这是 2026-11-08 的定论。依据是云严真 CLI 实测：Windows 把 `xyz://`
         折叠成目录名 `xyz` 并照常消解 `..`，旧写法
         `{"path":"xyz://../../../out.ase"}` 闸门 ALLOW、CLI 在 roots 外落盘
         2296 B。「任何 `scheme://` 都当 URL」等于把 `..` 判据整体作废。
         （`x://` 单字母 scheme 不必进白名单也安全：它先命中 `_ABS_PATH_RE` 走
         roots 分支，实测 `PATH_UNSAFE`。）
         本函数**不做**去引号与 `os.path.expandvars`（roots 的
         `normalize_win_path` 做），所以带引号的 `"\\Windows\\win.ini"` 与
         `%SYSTEMROOT%\\win.ini` 在这里不被识别为绝对路径而放行 —— 属**已声明
         边界**，可利用性取决于 CLI 自己是否 strip 引号 / 展开环境变量。
      8. 其余 5 个 `@llm_tool`（`search_commands`/`get_job`/`cancel_job`/
         `list_apps`/`open_in_studio`）仍各自解析入参，未收敛成统一前置校验；
         本轮只保证 `steps` 与 `--new` 两条模型可控 JSON 通道过校验
         （`run_sequence` 是已收敛的那条，不在「未收敛」之列）。
      9. `{"path": "con://a/b.png"}`（不含 `..`）**放行** —— 不额外识别设备名。
         理由：Windows 把 `scheme://` 折叠成「scheme 当前置目录」，`con://a/b.png`
         落到 `<cwd>/con/a/b.png` 这个相对路径；没有 `..` 段就退不回 roots 外，
         放行无可利用面（该拼写在 Win32 上打不开，但打不开不是越界）。
         **不要**因此往判据里加 `con`/`prn`/`aux`/`nul` 设备名黑名单：本闸门的契约
         是「不许出 roots」，不是「不许碰设备」，加了是零收益；而按 token / 前缀
         判设备名会误杀 `concept/`、`console/`、`constraint/` 这类正常路径 —— 本轮
         已经因 `profile` 含 `file`、`south` 含 `out` 吃过一次裸 substring 的亏。
         真正的设备访问长成 `C:\\x\\CON` 这种盘符绝对路径，已由 `_ABS_PATH_RE` →
         roots 分支处理，不靠关键词。
         同理别把「白名单外一律拒」当补丁：现行判据**只拒含 `..` 的白名单外值**
         （`ftp://h/a/b.png` 无 `..` → 放行；`ftp://x/a/../b.png` → 拒）。改成
         「白名单外全拒」会把正常远程资源一起杀掉。这一正一反两条对照钉在
         `tests/test_params_roots.py::test_url_with_parent_segments_allowed_but_relative_still_rejected`
         （`probe/` 下**没有** ftp 样本文件，别去那里找）。
    """
    if not isinstance(params, dict):
        _violate("params 必须是对象")
    for key, value in params.items():
        lowered = str(key).lower()
        if any(bad in lowered for bad in _FORBIDDEN_PARAM_KEYS):
            _violate(f"params 不允许出现凭证类字段：{key}")
        if lowered in _HOST_PORT_KEYS and isinstance(value, str):
            if value.strip() not in ("127.0.0.1", ""):
                _violate(f"params 中 {key} 只允许字面 127.0.0.1（#9777）")
    if roots is not None:
        _check_params_paths(params, roots, direction, extra_path_keys)


def validate_steps(
    steps: list[Step], spec: Any, policy: CommandPolicy,
    catalog_ids: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
    roots: RootsPolicy | None = None,
) -> None:
    """整批校验：步数上限 + 每条命令白名单 + params 内层（含 roots 路径检查）。

    ``roots`` 追加在**末尾**且带默认值：现有 3 参 / 4 位置参调用点必须原样可用，
    否则接线一次就 TypeError（「形参吞掉」族缺陷的另一半）。
    """
    rules = policy or CommandPolicy()
    items = list(steps or ())
    if len(items) > int(rules.max_steps):
        raise PolicyViolation("LIMIT_STEPS", "", max_steps=rules.max_steps)
    for step in items:
        check_command_allowed(step.cmd, spec, rules, catalog_ids)
        # [真缺陷] 这里曾经是 `validate_params(step.params or {})` —— 不传 roots，
        # 等于 step 参数里的路径字段完全没有校验。
        validate_params(step.params or {}, roots)


def validate_tool_kwargs(
    spec: Any, policy: CommandPolicy, kwargs: dict[str, Any],
    roots: RootsPolicy, allowed: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
) -> None:
    """``@llm_tool`` 入参的整批校验（P5）：steps ∈ allowed + params 内层 + 步数上限。"""
    raw_steps = (kwargs or {}).get("steps") or ()
    steps: list[Step] = []
    for item in raw_steps:
        if isinstance(item, Step):
            steps.append(item)
        elif isinstance(item, dict):
            steps.append(Step(cmd=str(item.get("cmd") or ""), params=item.get("params") or {}))
        else:
            _violate("steps 元素必须是对象")
    # [真缺陷] 签名收了 roots 却在转手时吞掉：一旦接线就是同一个旁路
    validate_steps(steps, spec, policy, allowed, roots=roots)


def steps_from_payload(payload: Any, spec: Any) -> list[Step]:
    """把 LLM 传来的 ``[{cmd, params}]`` 归一化成 ``Step`` 列表（命令 id 就地归一化）。"""
    if payload is None:
        return []
    if not isinstance(payload, (list, tuple)):
        _violate("steps 必须是数组")
    out: list[Step] = []
    for item in payload:
        if isinstance(item, Step):
            cmd = item.cmd
            params = dict(item.params or {})
        elif isinstance(item, dict):
            cmd = str(item.get("cmd") or item.get("command") or "")
            raw_params = item.get("params") or {}
            if not isinstance(raw_params, dict):
                _violate("params 必须是对象")
            params = dict(raw_params)
        else:
            _violate("steps 元素必须是对象")
        cmd = normalize_command_id(cmd, spec, "engine")
        if not cmd:
            _violate("steps 里存在空命令 id")
        out.append(Step(cmd=cmd, params=params))
    return out


def cached_catalog_ids(app_id: str, spec: Any) -> frozenset[str]:
    """读 discovery **缓存**取全量 id（不 spawn，可在 entry handler 内安全调用）。"""
    from . import command_catalog  # 局部导入：避免 sequence ↔ catalog 顶层循环

    version = str(getattr(spec, "version", "") or "")
    parser = str(getattr(spec, "commands_parser", "") or "")
    try:
        items = command_catalog.load_cache(
            command_catalog.cache_key(app_id, version, parser=parser),
            command_catalog.current_cache_dir(),
        )
    except Exception:
        items = None
    return frozenset(it.id for it in (items or ()))


def is_edit_sequence(steps: list[Step], spec: Any) -> bool:
    """含任一编辑命令 → 整体编辑类（断线绝不重发）。"""
    return any(not is_readonly(step.cmd, spec) for step in steps or ())


__all__ = [
    "normalize_command_id", "is_readonly", "static_is_allowed",
    "check_command_allowed", "plan_inspect",
    "build_argv", "build_argv_run_cmd", "build_argv_tool_subcmd", "ARGV_BUILDERS",
    "build_argv_cmd_inline", "build_argv_cmd_positional", "build_argv_script_file",
    "serialize_script",
    "validate_params", "validate_steps", "validate_tool_kwargs",
    "steps_from_payload", "cached_catalog_ids", "is_edit_sequence",
]
