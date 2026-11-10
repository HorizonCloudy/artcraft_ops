# artcraft_ops

N.E.K.O 插件：用一句话操控本机 **ArtCraft 系列软件** —— 检索命令 → 编排序列 → 无头执行 → 回传图片。

```
search_commands  →  run_sequence  →  get_job（轮询）→  图片
```

- **零第三方依赖**（纯标准库 `asyncio`），不需要 `pip install` 任何运行时包
- **fail-closed 安全模型**：目录穿越、设备名、备用数据流、8.3 短名一律拒绝
- **入口秒回 `job_id`**，长任务后台跑，不占宿主入口超时

## 安装

把 `artcraft_ops/` 整个目录放进宿主的插件安装目录：

```
%LOCALAPPDATA%\N.E.K.O\.neko-plugin-installations\plugins\artcraft_ops\
```

目录名必须等于 `plugin.toml` 里的 `[plugin].id`。宿主用 `root.glob("*/plugin.toml")` 扫描，没有注册文件门控。

首次安装时宿主会把 `config.example.toml` 复制到状态根：

```
%LOCALAPPDATA%\N.E.K.O\plugins\artcraft_ops\config\plugin.toml
```

## 六个入口

| 入口 id | 作用 | 超时 |
|---|---|---|
| `artcraft_ops_search_commands` | 按关键词/应用检索命令注册表 | 15s |
| `artcraft_ops_run_sequence` | 执行命令序列，立即返回 `job_id` | 10s |
| `artcraft_ops_get_job` | 查询状态 / 进度 / 预览图 | 15s |
| `artcraft_ops_cancel_job` | 取消任务（终止子进程） | 15s |
| `artcraft_ops_list_apps` | 列出本机已安装的 App | 10s |
| `artcraft_ops_open_in_studio` | 启动 Studio 并交接文件（不做 UI 自动化） | 10s |

支持的 App：`photocraft` / `vectorcraft` / `craft_anything` 走 CLI 子进程；`artcraft`（Studio）只做「启动 + 交文件」。

## 安全模型

`roots` 是目录级读写授权，默认只授权插件自己的 `data/` 与 `cache/`。

放行用户目录需要**两个开关同时打开**（`allow_user_dirs = true` + `user_read_roots`），改完需 stop → start。

> ⚠️ 这是真实的放宽：列进来的目录，模型就能读/写。`["C:\\"]` 等价于放开整块盘。

路径判据是**逐段纯文本比对**：`..`（含空白变体整族）、Windows 设备名、ADS（`file.png:hidden`）、8.3 短名、越界符号链接。

## 开发

```bash
cd artcraft_ops
python -m pytest -q                    # 313 passed, 1 skipped
python -m pytest -q -m integration     # 真机 CLI 端到端（默认 skip）
```

打包（可复现，固定时间戳）：

```bash
python tools/build_release.py
```

产物 20 个文件（含 `LICENSE`），同内容重复打包得到同一份 sha256。

## 仓库范围说明

**本仓库只含插件源码。** 设计规格、逐轮决策记录与厂商 CLI 的实测证据不在公开范围，因此源码里少数 docstring 会引用 `../spec/` 或 `../probe/` —— 那些是内部路径，不影响运行与测试（测试自带 `tests/_fixtures.py` 数据，不读这些目录）。

## 免责

本项目**与 ArtCraft 官方无关**，不是官方插件。它通过调用本机已安装软件的 CLI 工作，不修改、不反编译那些软件。使用前请自行确认符合你所持软件许可的条款。

## 许可

Copyright (C) 2026 neko-artcraft-ops

本项目以 **GNU General Public License v3.0（仅此版本）** 发布，SPDX 标识 `GPL-3.0-only`，全文见 [`LICENSE`](LICENSE)。

- 允许使用、修改、再分发，含商业用途
- 分发修改版或衍生作品时，**必须**同样以 GPLv3 开放源码，并保留版权与许可声明
- 不提供任何担保（GPLv3 §15–§17）；因使用本程序导致的软件故障、数据损坏或产出物权属争议，作者不承担责任
- 需要别的条款（GPLv2 / Apache-2.0 / 商业授权）请先联系作者

发布包 `artcraft_ops-1.0.3.zip` 内附同一份 `LICENSE`——GPLv3 §4 要求向接收者一并提供许可证副本，所以打包器把它列为必需项，缺了自检直接失败。

各源文件不带逐文件的版权头，是有意选择：内部规格文档里有 85 处 `文件:行号` 引用，统一插两行注释会让这些引用集体偏移。版权声明以本文件与 `LICENSE` 为准。
