from types import SimpleNamespace

from anyio import create_task_group

from crynux_server.task.error_report import (
    TaskErrorReport,
    TaskErrorReporter,
    TaskErrorReportStore,
)


def make_report(task_id: str) -> TaskErrorReport:
    return TaskErrorReport(
        node_address="0x0000000000000000000000000000000000000001",
        task_id_commitment=task_id,
        task_args='{"prompt":"test"}',
        error_type="TaskExecutionError",
        message="worker failed",
        stack_trace="original worker traceback",
        gpu_count=2,
        gpu_model="2x NVIDIA GeForce RTX 4090",
        gpu_vram_mb=24564,
        executor_mode="tensor_parallel",
        captured_at=1784851200,
    )


class FakeRelay:
    node_address = "0x0000000000000000000000000000000000000001"

    def __init__(self, fail_at=None):
        self.reports = []
        self.fail_at = fail_at

    async def report_task_diagnostic(self, report):
        if self.fail_at == len(self.reports):
            raise RuntimeError("relay unavailable")
        self.reports.append(report)


async def test_store_persists_atomically_and_deduplicates(tmp_path):
    filename = tmp_path / "task_error_reports.json"
    store = TaskErrorReportStore(str(filename))
    report = make_report("0x01")

    async with create_task_group() as tg:
        for _ in range(10):
            tg.start_soon(store.add, report)

    assert await store.count() == 1
    assert (await store.list())[0].stack_trace == "original worker traceback"
    assert not list(tmp_path.glob("*.tmp"))

    recovered = TaskErrorReportStore(str(filename))
    assert await recovered.count() == 1


async def test_store_reads_legacy_capture_time(tmp_path):
    filename = tmp_path / "task_error_reports.json"
    filename.write_text(
        """
[
  {
    "node_address": "0x0000000000000000000000000000000000000001",
    "task_id_commitment": "0x01",
    "task_args": "{}",
    "error_type": "TaskExecutionError",
    "message": "worker failed",
    "stack_trace": "traceback",
    "capture_time": "2026-07-24T01:00:00+00:00"
  }
]
""".strip(),
        encoding="utf-8",
    )

    reports = await TaskErrorReportStore(str(filename)).list()

    assert reports[0].captured_at == 1784854800


async def test_reporter_sends_one_at_a_time_and_retains_failed_record(tmp_path):
    store = TaskErrorReportStore(str(tmp_path / "task_error_reports.json"))
    await store.add(make_report("0x01"))
    await store.add(make_report("0x02"))
    relay = FakeRelay(fail_at=1)
    reporter = TaskErrorReporter(
        store,
        relay,
        config=SimpleNamespace(task_error_report=SimpleNamespace(automatic=False)),
    )

    result = await reporter.flush()

    assert result.reported == 1
    assert result.remaining == 1
    assert (await store.list())[0].task_id_commitment == "0x02"


async def test_capture_always_persists_when_automatic_disabled(tmp_path, monkeypatch):
    store = TaskErrorReportStore(str(tmp_path / "task_error_reports.json"))
    reporter = TaskErrorReporter(
        store,
        FakeRelay(),
        config=SimpleNamespace(task_error_report=SimpleNamespace(automatic=False)),
    )

    async def fake_gpu_fields():
        return 2, "2x NVIDIA GeForce RTX 4090", 24564, "tensor_parallel"

    monkeypatch.setattr(
        "crynux_server.task.error_report.collect_worker_gpu_report_fields",
        fake_gpu_fields,
    )

    assert await reporter.capture(
        bytes.fromhex("01" * 32),
        '{"prompt":"test"}',
        "TaskInvalid",
        "invalid arguments",
        "original traceback",
    )
    reports = await store.list()
    assert len(reports) == 1
    assert reports[0].gpu_count == 2
    assert reports[0].gpu_model == "2x NVIDIA GeForce RTX 4090"
    assert reports[0].gpu_vram_mb == 24564
    assert reports[0].executor_mode == "tensor_parallel"
