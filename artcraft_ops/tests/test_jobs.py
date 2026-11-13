"""§10 #11 / #12 / #15 / #21 — job 状态机、进度节流、句柄回收。

覆盖：正常路径（create_job QUEUED、get_job、persist/restore、shutdown terminate）
      + 边界（进度节流 2s/10%、512 KiB payload、编辑类/只读分类）
      + 错误输入（未知 job、spawn 前取消）。

说明：``jobs.execute(job, deps)`` 的 ``Deps`` 结构在 ARCHITECTURE §2 未定义
（[INFERENCE]），故断线重发的**真实驱动路径**放到 test_entries_e2e.py 用 stub-SDK
入口跑；本文件只测 §1 明确列签名、不需要 Deps 的纯逻辑函数。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from _fixtures import FakeCtx, FakeProcess, PUSH_MAX_BYTES, fake_handle

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------
def test_create_job_starts_queued(impl):
    """#11 正常：create_job 生成 QUEUED 状态、argv 归档、字段初值精确。"""
    jobs = impl("jobs")
    j = jobs.create_job("photocraft", ["cli", "run", "--cmd", "edit.fill"], False)

    assert j.status == "queued"
    assert j.app_id == "photocraft"
    assert tuple(j.argv) == ("cli", "run", "--cmd", "edit.fill")
    assert j.is_edit_sequence is False
    assert j.retried_readonly is False
    assert j.attempts == 0
    assert j.cancel_requested is False
    assert j.progress == 0.0
    assert isinstance(j.job_id, str) and j.job_id
    assert jobs.get_job(j.job_id) is j


def test_job_status_enum_values(impl):
    """正常：§7 六个状态值精确。"""
    spec = impl("spec")
    assert {s.value for s in spec.JobStatus} == {
        "queued", "running", "succeeded", "failed", "cancelled", "crashed",
    }


def test_persist_restore_jobs(impl, tmp_path):
    """正常：persist_jobs -> restore_jobs 往返能找回同一 job_id 与 inspect。"""
    jobs = impl("jobs")
    saved = dict(jobs._JOBS)
    try:
        jobs._JOBS.clear()
        j = jobs.create_job("effectcraft", ["run"], False)
        j.inspect = {"name": "ProbeComp", "width": 320}
        j.result_handle = "C:/tmp/out.ecproj.spill.json"   # 句柄必须跟着落盘
        path = str(tmp_path / "jobs.json")

        jobs.persist_jobs(path)
        assert os.path.isfile(path)

        jobs._JOBS.clear()
        jobs.restore_jobs(path)
        restored = jobs.get_job(j.job_id)
        assert restored is not None
        assert restored.inspect == {"name": "ProbeComp", "width": 320}
        assert restored.result_handle == "C:/tmp/out.ecproj.spill.json"
    finally:
        jobs._JOBS.clear()
        jobs._JOBS.update(saved)


def test_restore_ignores_non_dict_inspect(impl, tmp_path):
    """边界：磁盘上的 inspect 不是对象 -> 恢复为 None，不把坏数据带进内存。"""
    jobs = impl("jobs")
    saved = dict(jobs._JOBS)
    try:
        jobs._JOBS.clear()
        j = jobs.create_job("effectcraft", ["run"], False)
        path = tmp_path / "jobs.json"
        path.write_text(json.dumps([{
            "job_id": j.job_id, "app_id": j.app_id, "status": "succeeded",
            "inspect": "not-a-dict",
        }]), encoding="utf-8")

        jobs.restore_jobs(str(path))
        restored = jobs.get_job(j.job_id)
        assert restored is not None
        assert restored.inspect is None
    finally:
        jobs._JOBS.clear()
        jobs._JOBS.update(saved)


def test_shutdown_terminates_all_handles(impl):
    """#15 正常：shutdown_all 后 _PROC_HANDLES=={} 且每个 Process.terminate 被调。"""
    jobs = impl("jobs")
    saved = dict(jobs._PROC_HANDLES)
    try:
        jobs._PROC_HANDLES.clear()
        p1, p2 = FakeProcess(), FakeProcess()
        jobs._PROC_HANDLES["j1"] = fake_handle("j1", p1)
        jobs._PROC_HANDLES["j2"] = fake_handle("j2", p2)

        asyncio.run(jobs.shutdown_all())

        assert jobs._PROC_HANDLES == {}
        assert p1.terminate_calls >= 1
        assert p2.terminate_calls >= 1
    finally:
        jobs._PROC_HANDLES.clear()
        jobs._PROC_HANDLES.update(saved)


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_progress_throttle(impl):
    """#12 正常：fake clock 每步 +2s 且 +1% 循环 100 次，push 次数 = 跨 10% 次数（11~12）。

    §7 公式：``crossed_ten = (new_pct//10) > (last_progress_pct//10)``
    且 ``now - last_push_at >= 2.0s``。0/10/20/.../100 共 11 个跨十边界，
    加上可能的状态变化推送 -> 允许 11~12。
    """
    jobs = impl("jobs")
    spec = impl("spec")
    job = spec.JobState(
        job_id="j-throttle", app_id="photocraft", status="running",
        last_push_at=0.0, last_progress_pct=-1,
    )
    now = 0.0
    pushes = 0
    for pct in range(101):
        if jobs.should_push(job, now, pct):
            pushes += 1
            job.last_push_at = now
            job.last_progress_pct = pct
        now += 2.0                       # 每步 2s，满足 PUSH_MIN_INTERVAL_S
    assert 11 <= pushes <= 12, f"节流后 push 次数异常: {pushes}"


def test_progress_throttle_suppresses_fast_updates(impl):
    """#12 边界：每步只 +0.02s（100 步共 2s）时，跨 10% 也必须被 2s 间隔抑制。"""
    jobs = impl("jobs")
    spec = impl("spec")
    job = spec.JobState(
        job_id="j-fast", app_id="photocraft", status="running",
        last_push_at=0.0, last_progress_pct=0,
    )
    now = 0.0
    pushes = 0
    for pct in range(101):
        if jobs.should_push(job, now, pct):
            pushes += 1
            job.last_push_at = now
            job.last_progress_pct = pct
        now += 0.02
    assert pushes <= 3, f"密集更新未被节流: {pushes}"


def test_push_payload_512kib(impl, tmp_path):
    """#21 边界：摘要 >512 KiB 时只发摘要 + 产物路径，整条 payload 不超 512 KiB。"""
    jobs = impl("jobs")
    spec = impl("spec")
    huge = "x" * (PUSH_MAX_BYTES + 4096)
    job = spec.JobState(
        job_id="j-big", app_id="photocraft", status="succeeded",
        progress_label=huge, result_path="probe/e2e_layers.png",
    )
    ctx = FakeCtx(storage_dir=tmp_path, plugin_dir=PLUGIN_ROOT)

    asyncio.run(jobs.push_progress(job, ctx))

    assert ctx.pushes, "终态必须推送至少一条进度/结果"
    payload = json.dumps(ctx.pushes[-1], default=str, ensure_ascii=False)
    assert len(payload.encode("utf-8")) <= PUSH_MAX_BYTES
    assert huge not in payload
    assert "e2e_layers.png" in payload or "result_path" in payload


def test_edit_sequence_classification_blocks_retry(impl):
    """#13 边界（纯逻辑层）：含任一编辑命令即整体判编辑类 -> 绝不进入重发分支。"""
    sequence = impl("sequence")
    spec = impl("app_registry").get_spec("photocraft")
    jobs = impl("jobs")
    steps = ["layer.new.layer", "edit.fill", "document.inspect"]
    is_edit = any(not sequence.is_readonly(c, spec) for c in steps)

    assert is_edit is True
    state = jobs.create_job("photocraft", ("cli", "run"), is_edit_sequence=is_edit)
    assert state.is_edit_sequence is True
    assert state.retried_readonly is False


def test_readonly_sequence_classification_allows_retry_gate(impl):
    """#14 边界（纯逻辑层）：全只读序列 is_edit_sequence=False，retry 门初始关闭。

    [INFERENCE / 规格冲突] §4 与 §7 尾部注明「v1 CLI_RUN 不触发只读重发，仅预留
    bridge 通道生效」，而 §10 #14 期望「重发一次」。本测试只锁定**分类决策**这一
    确定性部分；实际 respawn 行为见 test_entries_e2e.py，冲突已上报主理人裁决。
    """
    sequence = impl("sequence")
    spec = impl("app_registry").get_spec("photocraft")
    jobs = impl("jobs")
    steps = ["document.inspect", "session.list", "jobs.list"]
    is_edit = any(not sequence.is_readonly(c, spec) for c in steps)

    assert is_edit is False
    state = jobs.create_job("photocraft", ("cli", "run"), is_edit_sequence=is_edit)
    assert state.is_edit_sequence is False
    assert state.retried_readonly is False


# ---------------------------------------------------------------------------
# 错误输入
# ---------------------------------------------------------------------------
def test_cancel_terminal_job_keeps_status(impl):
    """错误/边界：已成功的任务再取消 -> 状态不变，不覆盖结果。"""
    jobs = impl("jobs")
    j = jobs.create_job("photocraft", ["cli", "run"], False)
    jobs._finish(j, jobs.JobStatus.SUCCEEDED, 0)
    got = asyncio.run(jobs.cancel_job(j.job_id))
    assert got.status == "succeeded"
    assert j.error_code == ""


def test_cancel_before_spawn_goes_cancelled(impl):
    """#11 错误/边界：spawn 前到达的 cancel -> QUEUED→CANCELLED 终态。"""
    jobs = impl("jobs")
    j = jobs.create_job("photocraft", ["cli", "run"], False)
    got = asyncio.run(jobs.cancel_job(j.job_id))
    assert got.status == "cancelled"


def test_cancel_during_run_stays_cancelled(impl, monkeypatch):
    """运行中取消：被杀 CLI 的退出码不得把 CANCELLED 覆盖成 CLI_TASK_FAILED。"""
    jobs = impl("jobs")

    class BlockingProc:
        def __init__(self) -> None:
            self.pid = 424243
            self.returncode = None
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()

        def terminate(self) -> None:
            self.returncode = -15
            self.stdout.feed_eof()
            self.stderr.feed_eof()

        def kill(self) -> None:
            self.terminate()

        async def wait(self) -> int:
            return int(self.returncode or 0)

    async def fake_exec(*args, **kwargs):
        return BlockingProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec, raising=False)
    job = jobs.create_job("photocraft", ["cli", "run"], is_edit_sequence=True)
    job.step_total = 40

    async def scenario():
        task = asyncio.create_task(jobs.execute(job))
        for _ in range(100):
            await asyncio.sleep(0)
            if jobs._PROC_HANDLES.get(job.job_id):
                break
        assert jobs.status_value(job) == "running"
        await jobs.cancel_job(job.job_id)
        return await task

    result = asyncio.run(scenario())
    assert jobs.status_value(job) == "cancelled"
    assert job.error_code == "JOB_CANCELLED"
    assert result.error_code == "JOB_CANCELLED"


def test_get_job_unknown_returns_none(impl):
    """错误：未知 job_id 返回 None（不臆造状态，供入口转 JOB_NOT_FOUND）。"""
    jobs = impl("jobs")
    assert jobs.get_job("no-such-job-9999") is None
