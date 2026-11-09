# artcraft_ops

用一句话操控本机 **ArtCraft 系列软件**：检索命令 → 编排序列 → 无头执行 → 回传图片。

- **零第三方依赖**（纯标准库 `asyncio`），不需要 `pip install` 任何运行时包
- **fail-closed 安全模型**：路径越界、目录穿越、设备名、ADS、8.3 短名一律拒绝
- **入口秒回 `job_id`**，长任务后台跑，不占用宿主入口超时

> 完整设计规格见 `../spec/ARCHITECTURE-FINAL.md`（唯一有效版本）。
> 实测证据见 `../probe/RESULTS.md`。

---

## 安装

宿主插件目录（**唯一加载源**）：

```
%LOCALAPPDATA%\N.E.K.O\.neko-plugin-installations\plugins\artcraft_ops\
```

目录名必须等于 `plugin.toml` 里的 `[plugin].id`。宿主用 `root.glob("*/plugin.toml")` 扫描，无注册文件门控。

首次安装时宿主会把 `config.example.toml` **逐字节复制**到状态根：

```
%LOCALAPPDATA%\N.E.K.O\plugins\artcraft_ops\config\plugin.toml
```

改完配置需 `stop` → `start`（部分键只在 startup 读一次）。

---

## 六个入口

| 入口 id | 作用 | 超时 |
|---|---|---|
| `artcraft_ops_search_commands` | 按关键词/应用检索命令注册表（服务端过滤，不把 993 条塞进上下文） | 15s |
| `artcraft_ops_run_sequence` | 执行命令序列，**立即**返回 `job_id` | 10s |
| `artcraft_ops_get_job` | 查询任务状态 / 进度 / 预览图 | 15s |
| `artcraft_ops_cancel_job` | 取消任务（终止子进程） | 15s |
| `artcraft_ops_list_apps` | 列出本机已安装的 ArtCraft App | 10s |
| `artcraft_ops_open_in_studio` | 启动 Studio 并交接文件（**不做 UI 自动化**） | 10s |

典型调用链：`search_commands` → `run_sequence` → 轮询 `get_job`。

---

## 支持的 App

`photocraft` / `vectorcraft` / `craft_anything` 走 CLI 子进程；
`artcraft`（Studio）只做「启动 + 交文件」，回包 `handoff_verified` 恒为 `false`（Popen 成功 ≠ 窗口已加载）。

CLI 路径按顺序定位：配置 `cli_path` → 上游默认路径族 → 本机实测 → 探针。留空即自动。

---

## 安全模型

`roots` 是**目录级读写授权**，默认只授权插件自己的 `data/` 与 `cache/`。

放行用户目录需要**两个开关同时打开**：

```toml
[artcraft.security]
allow_user_dirs = true
user_read_roots = ['C:\Users\<你>\Pictures\artcraft']
```

> ⚠️ 这是真实的放宽：列进来的目录，模型就能读/写。`["C:\\"]` 等价于放开整块盘。

路径判据（逐段纯文本，`roots.py`）：

| 拒绝项 | 例 |
|---|---|
| 父目录段 | `..`，以及空白变体 `.. ` / `..\t` / `..\u00a0`（空白族按 `isspace()` 整族剥） |
| Windows 设备名 | `con` / `prn` / `aux` / `nul` / `com1`… |
| 备用数据流 | `file.png:hidden:$DATA` |
| 8.3 短名 | `PROGRA~1` |
| 越界符号链接 | 真实存在时 `realpath` 再比一次 |

---

## 错误码

固定 19 条集合，不动态拼接。高频：

| 码 | 含义 |
|---|---|
| `PATH_UNSAFE` | 路径含不安全段（`..` / 设备名 / ADS / 8.3） |
| `PATH_OUTSIDE_ROOTS` | 路径不在授权根内 |
| `POLICY_VIOLATION` | 命令不在注册表 / 命中 deny 前缀 / 步数超限 |
| `CLI_USAGE_ERROR` | CLI 退出码 2（参数拼错） |
| `JOB_NOT_FOUND` | `job_id` 不存在或已过期（`job_ttl_s`） |

> 错误出口：入口失败返回宿主认的错误 envelope（`success=false` + `error.code` + `time`），
> `error.code` 原样落进 run 记录的 `RunError.code`（`PATH_UNSAFE` / `POLICY_VIOLATION` / …），
> 不再被压成 `INTERNAL`；`error.message` 仍是 `<CODE>: <脱敏文案>`，便于人读与 grep。

---

## 开发

```bash
cd artcraft_ops
python -m pytest -q            # 全量；需要本机真实 CLI 的用例默认 skip
python -m pytest -q -m integration   # 仅在装了真机 CLI 时跑端到端
```

**263 passed / 1 skipped**（Python 3.13 / 3.14，pytest 9.x 均可）。

约定：

- `pytest.ini` 必须在**插件根**，不能放 `tests/` —— 裸跑 `pytest` 的 rootdir 是插件根，放 `tests/` 里配置会静默失效
- 异步测试一律 `asyncio.run(...)` 包装，不依赖 `pytest-asyncio` 的 `asyncio_mode`
- `plugin.toml` 的 `[[plugin.entries]]` 宿主**不校验**，唯一防线是 `tests/test_manifest.py` 的 AST 对账
- 所有异步配置读取必须 `await self.config.dump()`，漏 `await` 会拿到协程对象

---

## 已知边界

| 项 | 说明 |
|---|---|
| 双通道有意的保守重复 | `file:` 分支自带 `seg.strip()==".."` 判据，与 `roots` 段判据重叠；删了会改错误码并打红既有用例 |
| `...` / `.. .` / `foo.` | 不拒。无契约依据、实测在 Win32 上不可穿越，钉住等于把实现细节冻成契约 |
| `\u200b`(ZWSP) / `\ufeff`(BOM) | 不属 `str.isspace()`，两侧放行；实测不可穿越 |
| `root/ con/f.txt` | 会被误判为设备名而拒绝（前导空格 + 设备名）。属 fail-closed 偏严兜底，是有意代价 |
| 宿主 500 包装 | 见上文「错误码」末段 |

---

## 目录

```
artcraft_ops/
├── plugin.toml            # 清单（[plugin] + 6 个 entries + plugin_runtime）
├── config.example.toml    # 运行时配置模板（宿主复制到状态根）
├── LICENSE                # GPLv3 全文（35149 B，随发布包分发）
├── __init__.py            # 应用层：入口 + LLM 工具双注册 + lifecycle
├── sequence.py            # 领域层：argv 构建 + 参数/路径校验
├── roots.py               # 安全：目录授权 + Windows 路径段判据
├── jobs.py                # 任务状态机 + 进度节流 + 持久化
├── runner.py              # 子进程：spawn / stream_lines / terminate
├── locator.py             # CLI 定位 + 版本探测 + 脱敏日志
├── command_catalog.py     # 命令注册表：发现 / 缓存 / 检索
├── result.py              # CLI 输出解析 + 用户可读消息
├── errors.py              # 19 条固定错误码 + 递归脱敏
├── spec.py                # 数据结构与常量（单一事实源）
├── app_registry.py        # per-app 薄 adapter 表
├── i18n.py                # zh / en
├── studio_local.py        # Studio 本地 GUI 交接
├── bridge.py              # [预留] 回环控制通道，v1 不实现
├── studio_channel.py      # [预留] Studio 云端生成通道，v1 不实现
└── tests/                 # 16 个 test_*.py + conftest.py + _fixtures.py
```

---

## 许可

Copyright (C) 2026 neko-artcraft-ops

本项目以 **GNU General Public License v3.0（仅此版本）** 发布，SPDX 标识 `GPL-3.0-only`，全文见同目录 [`LICENSE`](LICENSE)。

- 允许使用、修改、再分发（含商业用途）
- 分发修改版或衍生作品时须同样以 GPLv3 开放源码，并保留版权与许可声明
- 不提供任何担保（GPLv3 §15–§17）

`LICENSE` 会随发布包一起打进去：GPLv3 §4 要求「向其他接收者一并提供本许可证副本」，打包器已把它列入 `REQUIRED_EXTRA`，漏掉即打包脚本自检失败。
