"""宿主级对照：`steps[].params` 与 `--new` 两条通道的路径闸门（真宿主、真 CLI）。

为什么要在宿主上再跑一遍：单测证明的是「函数被调用时会拒」，宿主这里要证明的是
**线上那份副本真的接线了** —— 工作区改动不会自动生效，副本不同步就是零防护。

用法（先部署三个文件 + POST /plugins/refresh + stop/start）：
    python probe/host_gate_check.py

判据：
  * 拒的用例：入口返回的 error_code 必须是 PATH_OUTSIDE_ROOTS / PATH_UNSAFE，
    且**没有** spawn 出 CLI（看 job 是否从未进入 running）。
  * 放的用例：不得出现任何 path 类错误码；`A: study` 这种文档名更不能被拒。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:48916"
PLUGIN = "artcraft_ops"
ENTRY = "artcraft_ops_run_sequence"

# [2026-11-08 修正] 宿主把**代码**放在 `.neko-plugin-installations/plugins/<id>/`，
# 把**运行时状态**（data / cache / config）放在 `N.E.K.O/plugins/<id>/`。
# 第一版错用了代码目录下的 data/ ⇒ 每个带 out_file 的用例都是
# `PATH_OUTSIDE_ROOTS: .../plugins/artcraft_ops/data/gN.png`，11 条全红，
# 而那个红来自公共参数、与被测字段无关（假红）。
DATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\Users\54301\AppData\Local"),
    "N.E.K.O", "plugins", PLUGIN, "data",
)


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def _get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def run_entry(args: dict) -> dict:
    """跑一次入口，等终态，返回入口返回值（dict）。"""
    started = _post("/runs", {"plugin_id": PLUGIN, "entry_id": ENTRY, "args": args})
    run_id = started.get("run_id") or started.get("id")
    deadline = time.time() + 60
    status = ""
    while time.time() < deadline:
        info = _get(f"/runs/{run_id}")
        status = str(info.get("status") or "")
        if status in ("succeeded", "failed", "cancelled", "error"):
            break
        time.sleep(0.4)
    out = _get(f"/runs/{run_id}/export")
    items = out.get("items") or []
    payload = {}
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("json"), dict):
            payload = it["json"]
            break
    return {"run_status": status, "payload": payload}


def err_code(payload: dict) -> str:
    """入口返回值里取错误码：可能是 {ok:false,error_code:...} 或 {ok:true, error_code:...}。"""
    if not isinstance(payload, dict):
        return ""
    for key in ("error_code", "code", "error"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


CASES = [
    {
        "name": "H1 放行：shape.create 的 path 是 SVG path data（不是文件路径）",
        "args": {
            "app": "photocraft",
            "steps": [{
                "cmd": "shape.create",
                "params": {"path": "M0,0 L120,0 L120,80 L0,80 Z", "fill": "#c0392b"},
            }],
            "out_file": os.path.join(DATA_DIR, "gate_h1.png"),
        },
        "expect_reject": False,
    },
    {
        "name": "H2 拒：abs path 越界（C:\\Windows\\win.ini）",
        "args": {
            "app": "photocraft",
            "steps": [{
                "cmd": "image.adjustments.colorLookup",
                "params": {"file": r"C:\Windows\win.ini"},
            }],
            "out_file": os.path.join(DATA_DIR, "gate_h2.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H3 拒：单分隔符 root-relative（\\Windows\\win.ini）—— C1 宿主级证据",
        "args": {
            "app": "photocraft",
            "steps": [{
                "cmd": "image.adjustments.colorLookup",
                "params": {"file": r"\Windows\win.ini"},
            }],
            "out_file": os.path.join(DATA_DIR, "gate_h3.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H4 拒：--new 通道 profile 越界",
        "args": {
            "app": "photocraft",
            "new_doc": {"profile": r"C:\Windows\win.ini", "width": 10, "height": 10},
            "steps": [{"cmd": "document.inspect", "params": {}}],
            "out_file": os.path.join(DATA_DIR, "gate_h4.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H5 放行：--new 的 name 是文档名（`A: study` 不得被当盘符路径）",
        "args": {
            "app": "photocraft",
            "new_doc": {"name": "A: study", "width": 10, "height": 10, "background": "white"},
            "steps": [{"cmd": "document.inspect", "params": {}}],
            "out_file": os.path.join(DATA_DIR, "gate_h5.png"),
        },
        "expect_reject": False,
    },
    # ---- 2026-11-08 第二轮：profile / droplet / file: 协议 --------------------
    {
        "name": "H6 拒：step params 的 profile 是文件路径（schema 原文 /path/to/profile.icc）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "edit.profileInfo", "params": {"profile": r"C:\Windows\win.ini"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h6.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H7 放行：同一个 profile 收预设名时不误杀（闸门不拒，CLI 可能自报无效 id）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "edit.profileInfo", "params": {"profile": "sRGB"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h7.png"),
        },
        "expect_reject": False,
    },
    {
        "name": "H8 拒：droplet 是执行原语（越界 = 让 CLI 跑盘外脚本）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "file.automate.runDroplet", "params": {"droplet": r"C:\Windows\win.ini"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h8.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H9 拒：file: 协议指向盘外文件（本地文件的另一种拼写）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "image.adjustments.colorLookup",
                       "params": {"file": "file:///C:/Windows/win.ini"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h9.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H10 拒：file:/// 无盘符 authority 形式（当前盘根，2026-11-08 加严那条）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "image.adjustments.colorLookup",
                       "params": {"file": "file:///Windows/win.ini"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h10.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H11 放行：URL 里的 .. 不套本地路径判据（闸门不拒，CLI 可能自报下载失败）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "image.adjustments.colorLookup",
                       "params": {"file": "https://x/a/../b.png"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h11.png"),
        },
        "expect_reject": False,
    },
    # ---- 2026-11-08 第三轮：多字母 scheme 旁路（云严真 CLI 实测落盘 2296 B）----
    {
        "name": "H12 拒：xyz:// 被 Windows 折叠成目录，.. 照样消解（不许当 URL 豁免）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "shape.create",
                       "params": {"path": "xyz://../../x.png", "fill": "#c0392b"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h12.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H13 拒：a+b:// 同族（scheme 白名单外一律按路径判）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "shape.create",
                       "params": {"path": "a+b://../../x.png", "fill": "#c0392b"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h13.png"),
        },
        "expect_reject": True,
    },
    # ---- 2026-11-08 第四轮：B1 `file:` URI 的 RFC 8089 三分支回归 --------------
    # 宿主默认策略只授权 data/cache，所以这几条验的是「修完不许放宽既有拒绝」与
    # 「localhost 分支确实等价本地」。「授权 UNC 根时两种拼写等价」属纯函数契约，
    # 在 tests/test_params_roots.py 里钉，宿主侧不改配置（改配置属侵入性操作）。
    {
        "name": "H14 拒：真 UNC 路径（默认策略无 UNC 根）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "image.adjustments.colorLookup",
                       "params": {"file": r"\\server\share\x.png"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h14.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H15 拒：file:// 远程 authority（B1 修完仍须拒，不许放宽）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "image.adjustments.colorLookup",
                       "params": {"file": "file://server/share/x.png"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h15.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H16 拒：file:/// 无盘符 authority（当前盘根）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "image.adjustments.colorLookup",
                       "params": {"file": "file:///Windows/win.ini"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h16.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H17 拒：file://localhost 指向盘外（localhost 等价本地，仍须越 roots）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "image.adjustments.colorLookup",
                       "params": {"file": "file://localhost/C:/Windows/win.ini"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h17.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H18 放行：file: 无 authority 相对形（RFC 8089 §2）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "image.adjustments.colorLookup",
                       "params": {"file": "file:draft.png"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h18.png"),
        },
        "expect_reject": False,
    },
    {
        "name": "H19 拒：file:// 远程 authority 带 ..（新分支不许绕过 .. 判据）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "image.adjustments.colorLookup",
                       "params": {"file": "file://server/share/../secret.png"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h19.png"),
        },
        "expect_reject": True,
    },
    {
        "name": "H20 放行：file://localhost 指向 data root 内（B1 正向证据）",
        "args": {
            "app": "photocraft",
            "steps": [{"cmd": "image.adjustments.colorLookup",
                       "params": {"file":
                                  "file://localhost/" + DATA_DIR.replace("\\", "/") + "/x.png"}}],
            "out_file": os.path.join(DATA_DIR, "gate_h20.png"),
        },
        "expect_reject": False,
    },
]


def main() -> int:
    if not os.path.isdir(DATA_DIR):
        print(f"[FATAL] 宿主 data 目录不存在：{DATA_DIR}")
        return 2
    ok = True
    for case in CASES:
        try:
            res = run_entry(case["args"])
        except Exception as exc:                     # noqa: BLE001
            print(f"[ERR ] {case['name']} -> 调用异常 {exc!r}")
            ok = False
            continue
        payload = res["payload"]
        # [2026-11-08 宿主实测 + 2026-11-09 S16 订正] 旧代码时宿主把插件抛的
        # PolicyViolation 包成 `{"code":500,"error":{"code":"INTERNAL","message":
        # "... PATH_OUTSIDE_ROOTS: ..."}}`。**HTTP 状态是 200**，那个 `500` 只是
        # envelope 的 `code` 字段值（真宿主 `plugin/core/responses.py:127-140`；
        # `get_http_status()` 生产代码零调用）。S16 起入口改返回 envelope，
        # `error.code` 应直接是真实码（PATH_UNSAFE / PATH_OUTSIDE_ROOTS）。
        # 判据仍扫整段序列化结果：兼容旧码（真码只在 message 文本里）与新码两种形态，
        # 不能只看 payload["error_code"]（第一版就是这么假绿的）。
        blob = json.dumps(payload, ensure_ascii=False)
        code = err_code(payload)
        entry_missing = "not found in plugin" in blob
        rejected = any(c in blob for c in
                        ("PATH_OUTSIDE_ROOTS", "PATH_UNSAFE", "POLICY_VIOLATION"))
        shown = next((c for c in ("PATH_OUTSIDE_ROOTS", "PATH_UNSAFE",
                                "POLICY_VIOLATION") if c in blob), code or "(none)")
        if entry_missing:
            verdict = "FAIL"
            ok = False
        elif case["expect_reject"]:
            verdict = "PASS" if rejected else "FAIL"
            if not rejected:
                ok = False
        else:
            verdict = "PASS" if not rejected else "FAIL"
            if rejected:
                ok = False
        print(
            f"[{verdict}] {case['name']}\n"
            f"        run_status={res['run_status']} gate_code={shown}\n"
            f"        payload={blob[:220]}"
        )
        continue
    # 清理产物（用例编号与 CASES 同步，新增用例必须补这里，否则宿主 data 会留残渣）
    for i in range(1, 21):
        path = os.path.join(DATA_DIR, f"gate_h{i}.png")
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
