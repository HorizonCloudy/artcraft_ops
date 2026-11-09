# ArtCraft 可编程控制面 — 本机实测证据

采集时间：2026-11-07 22:40–22:50 (GMT+8)
采集方式：只读探测 + 无头执行验证
证据等级：**[FACT]** = 命令输出/文件内容可复现；**[INFERENCE]** = 从证据推断

---

## 1. 本机安装清单 [FACT]

| App | 可执行文件 | 大小 | CLI 二进制 |
|---|---|---|---|
| ArtCraft Studio 0.41.1 | `C:\Program Files\ArtCraft\artcraft.exe` | 77.5 MB | **无** |
| PhotoCraft 0.21.0 | `E:\Program Files\Artcraft\PhotoCraft\photocraft.exe` | 153.7 MB | `photocraft-cli.exe` 117.1 MB |
| VectorCraft 0.22.0 | `E:\Program Files\Artcraft\VectorCraft\vectorcraft.exe` | 190.1 MB | `vectorcraft-cli.exe` 128.6 MB |
| Craft Anything 0.5.0 | `E:\Program Files\Artcraft\Craft Anything\craft-anything.exe` | 104.2 MB | `craft-anything-cli.exe` 38.7 MB |
| Launcher | `%LOCALAPPDATA%\ArtCraft Launcher\launcher-update-helper\artcraft-launcher.exe` | — | — |

状态根（每 app 独立）：
- `%APPDATA%\Photocraft\` — `instance.json`, `preferences.json` (360 KB), `ui.ron`, `logs\`
- `%APPDATA%\VectorCraft\` — `ui.json`, `logs\`, `Data Recovery\`
- `%APPDATA%\ai.storyteller.craft-anything\`, `%APPDATA%\craft-anything\`

---

## 2. PhotoCraft CLI 契约 [FACT]

`photocraft-cli.exe --help` 实测子命令：

| 子命令 | 作用 |
|---|---|
| `convert` | 格式互转（.pcraft/.psd/.png/.jpg/.tif/.webp/.exr/…） |
| `info` | 打印文档 JSON（尺寸、模式、位深、图层树） |
| `run` | 打开文件或新建，按序执行命令，保存结果 |
| `batch` | 对目录内每张图套用动作列表 |
| `droplet` | 运行 droplet |
| `commands` | 列出引擎命令注册表 |
| `mcp` | stdio MCP server（无头引擎，或桥接运行中的 app） |
| `serve` | 常驻无头会话，stdio 或 `127.0.0.1:<PORT>` 上答 JSON lines |

### 2.1 `run` 参数 [FACT]
```
photocraft-cli run [FILE | --new <JSON>] <--cmd <ID>|--out <FILE>>
  --cmd <ID>        可重复，按序执行
  --params <JSON>   作用于前一个 --cmd，默认 {}
  --out <FILE>      保存结果
  --in-place        允许 out == in
  --approve-high-memory
  --gpu / --no-gpu
  --format <EXT>    覆盖扩展名推断
  --quality <1-100>
  --tiff-layers
  --tiff-compression <none|lzw|zip>
```
约束：`<FILE>` 与 `--new` 二选一；`--cmd` 与 `--out` 至少一个；`--out == <FILE>` 会被拒绝（除非 `--in-place`）。

### 2.2 `commands` 输出结构 [FACT]
`commands --json` → 993 条，字段：
```json
{
  "disabledReason": null,
  "enabled": true,
  "id": "file.new",
  "label": "New…",
  "menu": ["File"],
  "needsDocument": false,
  "params": "{\"width\":1..300000=1920, ...}",
  "shortcut": "Cmd+N"
}
```
`params` 是**人类可读的 schema 字符串**（非 JSON Schema），含范围、枚举、默认值，可直接喂给 LLM。

### 2.3 命令类别分布 [FACT]（993 条，38 类）
`layer 230 · filter 124 · type 98 · edit 79 · image 66 · file 59 · select 35 · view 31 · paint 28 · path 25 · channel 22 · actions 21 · brush 18 · layerComp 14 · history 14 · tools 13 · gradient 12 · swatches 12 · document 11 · shape 10 · pattern 10 · style 7 · count 7 · slice 7 · timeline 7 · tool 5 · cloneSource 5 · plugin 5 · notes 4 · prefs 3 · measurementLog 3 · jobs 2 · session 1 · command 1 · color 1 · help 1 · updates 1 · variables 1`

菜单分布：无菜单 477 / Layer 159 / Filter 78 / Edit 68 / Image 61 / Type 60 / File 47 / Select 23 / View 19 / Help 1

### 2.4 `serve` 协议 [FACT]
请求：`{"id": <any>, "method": "<str>", "params": {...}}`，一行一条 JSON。
`methods` 方法实测返回：
```
engine.execute, jobs.list, jobs.cancel, engine.commands, session.list,
doc.open, doc.place, doc.replaceSmartObject, doc.new, doc.save,
doc.exportLayers, doc.exportDataSets, doc.importDataSets, doc.inspect,
doc.render, doc.select, doc.close, batch, methods
```
`serve` 选项：`--port` (env `PHOTOCRAFT_CONTROL_PORT`) / `--gpu` / `--no-gpu` / `--control-token <64-HEX>` / `--control-token-file <PATH>`。
stdio 模式无需 token；端口模式需要 token。

### 2.5 `mcp` 选项 [FACT]
`--bridge <127.0.0.1:PORT>`（桥接运行中的 app）、`--gpu`/`--no-gpu`、`--control-token`、`--control-token-file`、`--automation-read-root <DIR>`、`--automation-write-root <DIR>`。
**授权语义**：未授予的目录一律拒绝访问；每个 root 授权一个方向。

### 2.6 控制通道握手 [FACT]
`%APPDATA%\Photocraft\instance.json`：
```json
{"pid":11664,"port":52903,"token":"<64 hex chars>"}
```
photoCraft 运行时写入；可用于 bridge 或直接 HTTP。

### 2.7 冷启动 [FACT]
`commands --filter file.new` 全程 **0.294 s**（real），无需常驻进程。

---

## 3. VectorCraft CLI 契约 [FACT]

`vectorcraft-cli` 子命令：`mcp` / `run` / `commands` / `convert`

- `mcp [--connect ADDR | --headless] [--automation-read-root DIR]... [--automation-write-root DIR]...`
  默认连 `127.0.0.1:7979`（`vectorcraft --control 7979`），失败回退无头；**省略 root 则完全禁止该类访问**；root 参数隐含 `--headless`
- `run [--in FILE] [--cmd ID [--params JSON] | --commands FILE|-]... [--export FILE]... [--scale N] [--ppi N]`
  `--commands` 读 JSON 数组 `[{"command": ID, "params": {...}}]` 或 JSON Lines；≤64 MiB、≤100000 steps；每步打印一个 JSON
- `commands` → 933 条，字段 `[enabled, id, label, menu, params, shortcut]`
- `convert IN OUT [--scale] [--ppi] [--artboard I | --range R] [--outline-text] [--decimals] [--replace-lossy] [--editing-data] [--preserve-editing] [--ai-version 7|pdf] [--hidden-objects] [--transparent-images]`

**与 PhotoCraft 的差异 [FACT]**：
| 项 | PhotoCraft | VectorCraft |
|---|---|---|
| 输入文件参数 | 位置参数 `[FILE]` | `--in FILE` |
| 新建 | `--new <JSON>` | 同（`run` 内） |
| 输出 | `--out` | `--export`（可多次） |
| 批量脚本 | 无（用 `batch`/`actions`） | `--commands FILE\|-` 内联 JSON/JSONL |
| commands 额外字段 | `disabledReason`, `needsDocument` | 无 |
| 默认控制端口 | 动态（见 instance.json） | 7979 |

---

## 4. Craft Anything CLI 契约 [FACT]

子命令：`mcp` / `setup` / `doctor` / `uninstall` / `commands` / `capabilities` / `triage` / `report` / `chat` / `rebuild` / `ai`
全局选项：`--format json|jsonl|md|text|csv|tsv`、`--json`、`--project <*.cany>`、`--allow-run`、`--deep`、`--clean-room`
每个 tool 同时也是子命令。

---

## 5. ArtCraft Studio 控制面 [FACT — 阴性结论]

对 `artcraft.exe` 做二进制 ASCII 串检索，关键词命中数：

| 关键词 | 结果 |
|---|---|
| `--control` | 0 |
| `control-protocol` | 0 |
| `CONTROL_PORT` | 0 |
| `CONTROL_TOKEN` | 0 |
| `automation-read-root` | 0 |
| `automation-write-root` | 0 |
| `instance.json` | 0 |
| `--headless` | 0 |
| `engine.execute` | 0 |
| `commands --json` | 0 |
| `mcp` | 1（`cpA@cpAmcpA`，随机字节误报） |
| `MCP` | 1（`GetOEMCP`，Windows API 误报） |

安装目录仅 `artcraft.exe` + `Uninstall ArtCraft.lnk`，无 CLI 二进制、无 DLL。
**结论：ArtCraft Studio 当前版本无程序化控制面，只能 GUI 自动化。**

---

## 5.5 命名空间陷阱（高优先级）[FACT]

`run --cmd` 与 `serve` 的 `method` **不是同一个命名空间**：

| 场景 | 正确写法 | 错误写法 |
|---|---|---|
| `run --cmd <ID>` | `document.inspect` | `doc.inspect` ❌ |
| `serve` 的 method | `doc.inspect` | `document.inspect` ❌ |

实测错误输出：
```
error: `doc.inspect`: unknown command `doc.inspect`; did you mean `document.inspect` or `session.inspect`?
```
exit code **2**，且**整批命令不保存任何产物**（`--out` 文件不生成）。

同类差异：`session.inspect` vs `session.list`。凡 `serve` 方法表里的 `doc.*`，在引擎命令注册表里一律是 `document.*`。

---

## 6. 端到端验证 [FACT]

```bash
cd "E:/Program Files/Artcraft/PhotoCraft"
./photocraft-cli.exe run \
  --new '{"width":800,"height":600,"background":"#1e3a5f"}' \
  --cmd "edit.fill" --params '{"color":"#e8c547"}' \
  --out probe/e2e_fill.png
```
stdout：`{"command":"edit.fill","result":{"contents":"color"}}`
产物：`probe/e2e_fill.png`，3202 字节，800×600。
视觉复核：整幅纯金黄（#e8c547），`edit.fill` 覆盖了背景色 —— 行为符合"填充当前图层全部内容"。

**结论：无 GUI、无窗口、单进程，0.3 s 内完成"新建 → 执行命令 → 导出"全链路。**

### 6.1 多步图层流水线 [FACT]

```bash
./photocraft-cli.exe run --new '{"width":640,"height":400,"background":"white"}' \
  --cmd "layer.new.layer" --params '{"name":"Accent"}' \
  --cmd "edit.fill"       --params '{"color":"#c0392b"}' \
  --cmd "layer.new.layer" --params '{"name":"Veil","opacity":0.5}' \
  --cmd "edit.fill"       --params '{"color":"#f1c40f"}' \
  --cmd "document.inspect" --params '{}' \
  --out probe/e2e_layers.png
```
逐步 stdout：
```
{"command":"layer.new.layer","result":{"layer":3}}
{"command":"edit.fill","result":{"contents":"color"}}
{"command":"layer.new.layer","result":{"layer":4}}
{"command":"edit.fill","result":{"contents":"color"}}
{"command":"document.inspect","result":{...}}
warning: 3 layer(s) flattened; layers, masks and blend modes are not kept
```
产物：`probe/e2e_layers.png`，2054 字节，640×400，实测色值 **#d87a1d**。
理论混合：#f1c40f(0.5) over #c0392b = (216, 126, 29) = #d87e1d，与实测吻合 → **图层、不透明度、混合模式确有效力，非空转**。

**警告语义 [FACT]**：导出 PNG 会拍平所有图层（`3 layer(s) flattened; layers, masks and blend modes are not kept`）。要保留图层必须导出 `.pcraft` / PSD / `--tiff-layers`。插件必须把该 warning 原样回传给用户。

### 6.2 `document.inspect` 返回结构 [FACT]

这是插件的「眼睛」，`run --cmd document.inspect` 输出（节选真实字段）：
```json
{
  "activeLayer": 4,
  "canUndo": true, "canRedo": false,
  "channels": {"alpha": [], "colors": [{"name":"Red","visible":true}, ...], "composite":"RGB"},
  "depth": 8, "width": 640, "height": 400,
  "mode": "Rgb", "resolution": 72.0, "revision": 5,
  "hasSelection": false, "selectionBounds": null,
  "history": ["Open","New Layer","Fill","New Layer","Fill"],
  "layers": [
    {"id":4,"name":"Veil","kind":"Pixel","blend":"Normal","opacity":0.5,
     "bounds":[0,0,640,400],"visible":true,"hasMask":false,"hasVectorMask":false,
     "clipped":false,"fill":1.0,"labelColor":"none","selected":true},
    {"id":3,"name":"Accent", ...}, {"id":2,"name":"Background", ...}
  ],
  "selectedLayers": [4],
  "snapshots": [{"id":0,"name":"Untitled"}],
  "layerComps": [], "notes": [], "timeline": null, "symmetry": null,
  "measurement": {"count":0,"ruler":null,"scale":"1 pixels = 1.0000 pixels"}
}
```
用途：让 LLM 在操作前/后读取真实文档状态（层名、顺序、可见性、不透明度、选区、历史），不靠猜。

### 6.3 命令检索能力 [FACT]

`commands --json --filter "<text>"` 做服务端过滤，返回同结构数组。实测：
- `--filter layer.new` → 3 条（`layer.new.layer` / `.group` / `layer.newFillLayer.solidColor`）
- `--filter sharpen` → 5 条（`filter.sharpen.sharpen|sharpenMore|sharpenEdges|unsharpMask|smartSharpen`）

样例参数 schema：
- `filter.sharpen.unsharpMask` → `{"amount":1..500=50,"radius":0.1..1000=1,"threshold":0..255=0}`
- `layer.new.layer` → `{"name":str?,"clipToPrevious":bool=false,"color":"none|red|...|gray"="none","mode":"Normal|Multiply|…"="Normal","opacity":0..1=1,...}`

**含义**：993 条命令无需全量塞给模型，可先按关键词检索再执行。

---

---

## 7. 对插件的设计含义 [INFERENCE]

1. **首选调用路径 = 子进程 CLI**。零第三方依赖（Python 标准库 `asyncio.create_subprocess_exec` 即可），无需 vendor，无需 app 运行中。
2. **993 + 933 条命令不可能逐个暴露为 LLM tool**（上下文爆炸）。必须做「检索 + 执行」两段式：`search_commands(query)` → `run(sequence)`。
3. **统一契约存在但非完全一致**：输入/输出参数名不同（`FILE` vs `--in`，`--out` vs `--export`），需一层薄 adapter。
4. **安全边界现成**：`--automation-read-root` / `--automation-write-root` 提供目录级白名单，应默认只授权插件自己的 data 目录 + 用户显式指定目录。
5. **长任务有原生支持**：`serve` 的 `jobs.list` / `jobs.cancel`，以及 `serve --port` 常驻会话。
6. **ArtCraft Studio 需单独降级策略**：GUI 启动 + 文件交接，不做 UI 自动化（脆弱且不可维护）。

---

## 8. 版本时效性核验（用户提醒「仓库不一定最新」后补做）[FACT]

### 8.1 两侧结论必须分开看

| 侧 | 本机 | 上游最新 | 判定 |
|---|---|---|---|
| **ArtCraft / PhotoCraft** | 0.21.0（2026-11-06 构建） | `v0.21.0`，published 2026-11-06T20:22:03Z | ✅ **一致，本机即最新** |
| **ArtCraft Studio** | 0.41.1 | `artcraft-v0.41.1`（launcher releases.json 缓存） | ✅ 一致 |
| **N.E.K.O 宿主仓库** | `8d27877`（2026-11-07 03:18 UTC）#3638 | `645da54`（2026-11-07 14:30 UTC）#3646 | ❌ **落后 4 个 commit** |

PhotoCraft 上游 release asset 实证：`photocraft-0.21.0-windows-x64.msi`（111,988,736 B，sha256 `2147a3b0…`）、`photocraft-0.21.0-windows-x64-portable.zip`（134,361,814 B，sha256 `93b4e90a…`）、`-windows-x64-symbols.zip`、macOS dmg、各 Linux 包。**资产名中无独立 `cli` 包** → CLI 二进制确系打包在 msi/zip 内，与本机实机观察一致。

### 8.2 N.E.K.O 落后的 4 个 commit

| SHA | 时间(UTC) | 说明 |
|---|---|---|
| `645da54e91acf4cc59eeba0f010fcdcee49d96d1` | 2026-11-07 14:30:39 | fix(voice): 恢复取消后的语音回复轮次 (#3646) |
| `1ba45d53ab39fc3928096b1d44f9648edd48cf55` | 2026-11-07 14:23:54 | fix(config): 修复 OpenAI 辅助 API 单 token 连通性测试误报 (#3641) |
| `9565e45edee2a8bf1a968326c3848e549a4b7de6` | 2026-11-07 11:33:06 | feat(plugin-manager): 插件面板支持宿主确认的专注与全屏 (#3643) |
| `8efad237fada198067bbb1fab971536b59cd20a3` | 2026-11-07 11:32:18 | build(plugin-manager): upgrade to Vite 8 (#3642) |

`origin` = `https://v6.gh-proxy.org/https://github.com/Project-N-E-K-O/N.E.K.O`；本地 `origin/main` 缓存为旧值（`git rev-list --left-right --count HEAD...@{u}` 返回 `0 0`），说明未 fetch 过，不能据此判断同步状态。

### 8.3 本地已有的 #3638 含 BREAKING CHANGE

本地 HEAD 就是 #3638（`feat(plugin-manager): 插件制作工作台与 Market 提审发布流程`），其完整提交正文包含：
> 新增插件制作工作台与 Market 提审发布流程（预览、创建、预检查、发布状态、后台发布任务、GitHub 仓库创建、Market 提审）；含 **BREAKING CHANGE：不再读取 `plugin-development.json` 中的开发注册信息**。

但用户级 skill `C:\Users\54301\.workbuddy\skills\neko-plugin-sdk\SKILL.md:35` 仍把 `<N.E.K.O>\plugin-development.json` 列为「开发态注册（`source_dir` 指向源码目录）」。
本地代码中该路径仍有残留引用：
- `plugin/server/application/plugins/development.py:44` → `settings.get_plugin_state_root().parent / "plugin-development.json"`
- `plugin/server/application/plugins/development.py:86` → 临时文件前缀 `.plugin-development-`
- `plugin/tests/unit/server/test_development_plugins.py:596`

**结论**：skill 文档与宿主当前行为存在冲突，开发态注册方式必须由云雨从源码核实后再定，不可照抄 SKILL.md。

### 8.4 本地仓库有未提交内容（写操作红线）

`git status --porcelain` 显示多个 untracked 文件，包括 `.codebuddy/`、`CODEBUDDY.md`、`app/memory_server/theater_loops.py`、`docs/design/plugin-lifecycle-ownership-transactions.md`、`frontend/plugin-manager/src/api/development.ts` 等。
**不得对 `D:\N.E.K.O` 执行 fetch / pull / checkout / reset / clean**，除非用户显式授权。本轮所有上游核对均走 GitHub API / raw 只读完成。

### 8.5 对插件开发的含义 [INFERENCE]
- 插件必须匹配**本地宿主实际契约**（因为要装进本机），上游 4 个 commit 只作前瞻兼容参考，两者需分开报告。
- `plugin-development.json` 注册路径不可假定可用；安装方式要按核实后的新机制或「直接放入 `.neko-plugin-installations/plugins/<id>/`」的稳妥路径设计。
- ArtCraft 侧无需升级，本机 0.21.0 的 CLI 契约就是当前正式契约。

---

## 9. 外部来源交叉核验（云调用 AnySearch + Exa 补做）[FACT]

用户指出「仓库不一定最新」后追加。AnySearch 封装层 `mcp__anysearch__*` 三工具返回 `invalid_api_key`（本地 connector-proxy header 错误）；云调改用**同一服务**的 MCP 端点 `https://api.anysearch.com/mcp` + `x-api-key` 直连 `tools/call`，完成 `batch_search`（3 批 13 查询）、`extract`（12 页）、`score`（10 条）；Exa 走 `https://api.exa.ai/search` POST，10 查询 HTTP 200。**工具与账号都是 AnySearch/Exa，只是绕过封装层；封装层故障未修复，需平台侧处理。**

### 9.1 推翻 / 修正上一轮的 4 条

| # | 修正 | 证据 |
|---|---|---|
| 1 | 「统一契约已既成」→ 降级为 **RFC-0012 正在 rollout** | `cadcraft/pull/2268`（clap 重构 following RFC-0012）、`gridcraft/issues/2688` 仍要求「every Craft CLI behaves the same」 |
| 2 | PdfCraft 从 unknown → **官方确认有 headless CLI**，只是注册表名叫 `tools`（snake_case） | `getartcraft.com/apps/pdfcraft` 发布 `pdfcraft-cli-0.19.0-macos-universal.zip`，原文「The headless CLI for scripts and agents」 |
| 3 | PhotoCraft 认证**不是安全边界** | 官方 `book/src/automation/security-model.md`：「token proves possession of a secret but does not provide general method authorization」；`MCP capability scopes = Partial`、`审计事件 = Proposed` |
| 4 | README「500+ 命令」与独立站「v0.20.1」均过时 | GitHub 已发 v0.21.0（2026-11-06T20:22:03Z）；本机 `commands --json` = 993 / 933 |

### 9.2 最高风险：#9777 critical 未修漏洞 [FACT]

`https://github.com/storytold/photocraft/issues/9777`（开 2026-10-27，label `priority: critical`）：
> MCP bridge sends the control token to whatever process is listening.

Windows 上 `localhost` 会解析到 `[::1]`，而 app 只绑定 `127.0.0.1` → token 可能被发给别的进程。
修复 PR `#9778`（HMAC-SHA256 challenge、token 不过线）**截至 2026-11-06 仍 Open / merged=false**。
`docs/control-protocol.md`（2026-11-07 13:44Z 最新版）仍规定首帧明文 `{"method":"auth","params":{"token":"<64 hex>"}}`，无 `auth.challenge`/nonce → **文档与实际一致，漏洞未修**。

**插件规避（强制）**：连接一律用字面 `127.0.0.1:<port>`，**绝不用 `localhost`**；bridge 启动前确认 app 已 listen；token 文件放用户独占目录；控制端口不长期常开；插件侧自做工具白名单，不把 token 当授权边界。

### 9.3 上一轮完全遗漏的官方文档树 [FACT]

`book/src/SUMMARY.md` 列出（README 一个字未提）：
- `automation/overview.md` `cli.md` `control-protocol.md` `mcp.md` `security-model.md`
- `architecture/command-engine.md` `automation.md`
- `security/threat-model.md` `trust-boundaries.md` `automation-security.md` `vulnerability-reporting.md`

独立站 `photocraft.im/guides/mcp-cli`（**非 GitHub 组织**，自述描述 v0.20.1 / 2026-11-03）。
可信度排序：**GitHub 仓库 docs/book + api.github.com releases > getartcraft.com 官方站 > 第三方站群 > Reddit/博客**。所有命令名以 `--help` / `commands --json` 为准。

### 9.4 roots 的 fail-closed 硬约束 [FACT]（上一轮只写了「有 roots」）

- 不传 root 就 fail-closed；**裸 `photocraft-cli mcp` 只能在内存里建/改文档，不能打开或保存任何文件**，报错 `automation filesystem access is not granted: read authority is absent`。
- `PHOTOCRAFT_AUTOMATION_READ_ROOT` / `..._WRITE_ROOT` **只配置桌面 app**；headless CLI 必须显式传 `--automation-read-root` / `--automation-write-root` flag。
- **bridge 模式 roots 必须配在桌面启动命令**（`photocraft --control ...`）；配到 `photocraft-cli mcp --bridge` 是 usage error（exit 2）。
- Windows 路径：v0.7.0 起接受 `C:/work/...`（root 为 `C:\work`）；v0.18.0 起解析符号链接、逐段忽略大小写、认 `\\?\C:\` / `\\?\UNC\`；拒绝 `..`、设备名（CON）、ADS、8.3 短名、越界 symlink。
- 社区实证坑：Windows `C:/` 绝对路径被 roots 拒绝（`photocraft/issues/2176`）、CLI 静默忽略无效 flag（`#8035`）、FigCraft MCP 文件工具无 roots（`figcraft/issues/481`）。

### 9.5 安全边界量化参数 [FACT]

回环 TCP 绑定 ✅ / stdio ✅ / 256-bit token 前置认证 ✅ / roots fail-closed ✅ / symlink 安全 FS ✅ / **batch 256 步上限** / **请求行 1 MiB** / **回复 8 MiB** / 预览 2048px、67,108,864 源像素、5 MiB PNG / 16 条活跃 TCP / serve 30 分钟空闲退出。
两份官方安全文档冲突：`automation/security-model.md`（2026-10-07）说 MCP stdio 无字节上限，`security/automation-security.md`（2026-10-12，带加固 commit #2269）说 MCP stdio 也限 1 MiB → **采信后者**（更新、更具体）。

### 9.6 v0.21.0 自动化改动 [FACT]（独立站未覆盖）

`#10842` headless GPU 合成后端 · `#10912` CLI 暴露可选 GPU 合成/导出 · `#8132` MCP headless 等待式命令改为 **job（带进度与取消）** · `#8134` MCP bridge 轮询 app 的 job · `#11210` 控制通道暴露 Move 变换控件。
→ 插件**不能**用「一次调用等到底」模型，必须支持 `jobs_list` 轮询 + 进度 + 取消。

bridge 可靠性语义：客户端 55s 放弃（app 自身 60s）；空闲 25s 先重连再发；`ui_inspect`/`doc_inspect`/`command_list`/`jobs_list`/`session_list`/`ui_screenshot` 只读工具断线重试一次，**编辑类绝不重发**。

### 9.7 形态约束 [FACT]

- **WASM 内嵌插件不可用**：ABI v1 沙箱「no file-system or network imports」，不支持面板/UI 插件、`.8BF`/CEP/UXP；官方建议「start with the control channel or MCP」→ 插件只能走**外部子进程 `<app>-cli` + 回环控制通道**。
- **ArtCraft Studio L4 被双向印证**：官方 `issue #2003`（仍 Open，末次 2026-10-31）原文「Today the only way an agent can reach it is desktop computer use: screenshots plus mouse clicks」「Scene state can't be read back」；`ROADMAP.md` 2026 Goals 零 MCP/automation/API 条目。
- **但云端 MCP 另有路径**：`issue #2003` 提到仓库已定义 `POST /v1/mcp/session/create` + `PlatformType::Mcp`；第三方 `theCosmicCrafter/artcraft-mcp` 用 API key / `%LOCALAPPDATA%\ai.artcraft.app\.cookies` 实现 61 工具。→ 本地 GUI 控制与云端生成必须**拆成两条独立通道**，不要抽象成同一个 app 控制面。
- 官方站还发布 `filmcraft-cli-0.18.0-macos-universal.zip`；Windows/Linux 无独立 CLI 下载，CLI 打包在 MSI/zip 内，路径 `%LOCALAPPDATA%\Programs\ArtCraft\PhotoCraft\photocraft-cli.exe`，**not on PATH**；v0.17.0 起有 CLI，v0.18.0 起每个桌面包都含。

### 9.8 可参考的现成实现 [FACT]

- `dsh-artcraft`（社区插件 v0.3.5）：四 agent 工具 + `/artcraft/api/*` 同源 HTTP API + 统一 dataRoot + 全本机 headless，覆盖 PhotoCraft/FilmCraft/EffectCraft — https://dsh-plugin.org/plugins/ninkch/dsh-artcraft
- `theCosmicCrafter/artcraft-mcp`：`/api/mcp/health|start|stop|token` FastAPI 受管子进程生命周期
- Claude Desktop / Cursor 配置：`{"mcpServers":{"photocraft":{"command":"photocraft-cli","args":["mcp"]}}}`（注意：裸启动无文件权限，需补 roots）
- **N.E.K.O ↔ ArtCraft 现成集成：两通道均未发现**（阴性结论 [INFERENCE]）

### 9.9 命令数三种口径（不可混用）[FACT]

| 数字 | 口径 |
|---|---|
| 500+ | README / 独立指南的保守下限，**已过时** |
| 631/631 | `docs/target-app-parity.md` 的 Photoshop 菜单 catalog 项（2026-10-03 为 625，10-10 为 628） |
| **993 / 933** | 本机 `commands --json` 的**注册表总条目**（含 UI/隐藏命令），PhotoCraft / VectorCraft |

**最可信**：本机 `commands --json`（实时、口径明确）+ `docs/target-app-parity.md`（官方、带测量方法）。

### 9.10 对插件架构的 7 条硬约束

1. 命令数与工具名**一律动态发现**（`commands --json` / MCP `tools/list`），缓存 key 带 app 版本；RFC-0012 未落地，工具名分叉（`run_command` vs `execute` vs `tools`）短期不会消失。
2. roots 显式且按模式分置（headless 传 CLI flag / bridge 配桌面启动命令），Windows 路径按 v0.18.0 规则归一化。
3. #9777 规避：字面 `127.0.0.1`、不用 localhost、token 文件用户独占、端口不常开、插件侧工具白名单。
4. 安全边界参数进配置（256 步 / 1 MiB / 8 MiB / 16 连接 / 30 分钟），超限降级不崩。
5. 可靠性按 bridge 语义实现；v0.21.0 起支持 `jobs_list` 轮询 + 进度 + 取消。
6. 形态固定为外部子进程 + 回环控制通道，不做 WASM 内嵌。
7. ArtCraft Studio 拆成本地 GUI（L4）与云端生成 MCP 两条通道。



---

## 10. 复核与纠错（2026-11-08，实现前真机二次核验）[FACT]

> 本节为**对前 9 节的更正**。凡与前文冲突处以本节为准。全部为本机当场跑出的命令输出。

### 10.1 🔴 重大纠错：`run` 子命令不接受 automation root 参数

前文 §6 与架构 §4/§6 写的「headless 拼 `--automation-read-root/--automation-write-root` 到 CLI」**是错的**。

```
$ photocraft-cli run --new '{"width":64,"height":64,"background":"white"}' \
    --automation-read-root 'C:/.../roots_test' \
    --automation-write-root 'C:/.../roots_test' \
    --out 'C:/.../roots_test/a.png'
exit=2
stderr: error: unexpected argument '--automation-read-root' found
        tip: to pass '--automation-read-root' as a value, use '-- --automation-read-root'
        Usage: photocraft-cli run <FILE|--new <JSON>> <--cmd <ID>|--out <FILE>>
```

三种写法全部 exit 2（原生反斜杠 / 正斜杠 / `\\?\C:/` 长前缀），故不是路径格式问题，是**参数不存在**。

`photocraft-cli run --help` 的完整 option 集合：`--new` `--cmd` `--params` `--out` `--in-place`
`--approve-high-memory` `--gpu` `--no-gpu` `--format` `--quality` `--tiff-layers`
`--tiff-compression` `--tiff-transparency` `--tiff-pyramid` `--tga-bits` `--dpx-bits`
`--dds-compression` …（**无任何 automation/root 项**）

逐个 subcommand 扫 `grep -i automation`：

| 子命令 | root 参数 |
|---|---|
| `serve` | ✅ `--automation-read-root <DIR>` / `--automation-write-root <DIR>` |
| `mcp` | ✅ 同上。原文：「Headless: each automation root grants that direction of file access; an omitted root denies it. **Bridge: set the roots on the desktop app; root flags are refused.**」 |
| `run` / `convert` / `info` / `batch` / `droplet` | ❌ 无 |

VectorCraft 同样：`vectorcraft-cli run [--in FILE] [--cmd ...] [--export FILE]...` 无 root；
root 只在 `mcp` 上；**且没有 `serve` 子命令**（印证 `has_serve=False`）。

**结论**：
1. headless `run` **没有 roots 沙箱**——它是普通本地 CLI，读写不受 root 约束。
2. v1 主通道 `CLI_RUN` **必须不带 root flag**（否则每次 exit 2）。
3. roots fail-closed 的强制点**移回插件侧**：`run_sequence` 拼 argv 前对 `input_file`/`out_file`
   各跑一次 `roots.resolve_and_check()`，越界即 `PATH_OUTSIDE_ROOTS` 且不 spawn。这是 v1 唯一防线。
4. `build_root_flags()` 保留，仅作 `serve`/`mcp` 的 helper（v1 不实现那两个 transport）。
5. 「纯文本逐段比对、拒 `..`/设备名/ADS/8.3」这些规则本身仍成立（属协议侧语义），只是**不通过 `run` 的 argv 传递**。

### 10.2 主用户故事端到端跑通：调亮（像素级证明）

```
$ photocraft-cli run --new '{"width":200,"height":200,"background":"#808080"}' --out before.png
exit=0

$ time photocraft-cli run before.png \
    --cmd image.adjustments.brightnessContrast --params '{"brightness":80}' \
    --cmd document.inspect --params '{}' \
    --out after.png
exit=0    real 0m0.486s

before.png: size=(200,200) mean=[128.0,128.0,128.0] pixel(100,100)=#808080
after.png : size=(200,200) mean=[195.0,195.0,195.0] pixel(100,100)=#c3c3c3
```

→ 用户那句「帮我把这张图调亮一点」所需的最小命令链已确认可用：
`image.adjustments.brightnessContrast`，参数 `{"brightness": -150..150 = 0, "contrast": -50..100 = 0, "legacy": bool = false}`。
同族 23 条：`.levels` `.exposure` `.curves` `.vibrance` `.desaturate` 等（`image.adjustments.*`）。

### 10.3 命名空间陷阱复现（整批不保存，二次确认）

```
$ photocraft-cli run before.png --cmd edit.fill --params '{"color":"#ff0000"}' \
    --cmd doc.inspect --params '{}' --out trap_out.png
{"command":"edit.fill","result":{"contents":"color"}}
exit=2
stderr: error: `doc.inspect`: unknown command `doc.inspect`; did you mean `document.inspect` or `session.inspect`?
$ ls trap_out.png  → No such file or directory
```

关键：`edit.fill` **执行成功了**（stdout 有结果），但**产物完全没落盘**。→ `normalize_command_id()`
必须在 `build_argv` 之前跑，且必须是硬错误（不可静默忽略）。

### 10.4 三 app `commands --json` 真实形状（994 条口径修正）

| app | 条数 | 字段键 | 顶层命名空间数 |
|---|---|---|---|
| photocraft | **993** | `disabledReason / enabled / id / label / menu / needsDocument / params / shortcut`（8） | 38 |
| vectorcraft | **933** | `enabled / id / label / menu / params / shortcut`（6，缺前两个） | 64 |
| craft_anything | **321** | `command / needs_target / summary / title / tool / toolset`（6，**完全不同的一套**） | 17 toolset |

- PhotoCraft 全表 **0 个 `doc.*`**；`session.*` **1 条**、`jobs.*` **2 条**、`command.*` 1 条。
  → 架构 §1 `readonly_prefixes` 里的 `session.list` / `jobs.list` / `command.list` 按实际表不存在，
  但作**前缀判定**仍安全（`startswith` 不会误伤），保留即可。
- Craft Anything 的 cmd 是**连字符**形态（`core-target-open`，tool 字段是下划线 `core_target_open`），
  **不是点分**。→ 命名空间门对 CA 必须按 `-` 切分，或按 `spec.builder == "tool_subcmd"` 分支。
- CA 的 `toolset` 只有 17 个：`adb android apple application browser build core database formats
  frida java javascript managed native projects runtime setup`。

### 10.5 环境

`Python 3.13.14` / `pytest 9.2.0` / `Pillow 13.0.0`（可用于像素级验收）。
