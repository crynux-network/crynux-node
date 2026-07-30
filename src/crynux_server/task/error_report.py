from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from anyio import Condition, Lock, move_on_after, to_thread
from pydantic import BaseModel, model_validator

from crynux_server.config import Config, get_config
from crynux_server.relay.abc import Relay
from crynux_server.utils import (
    GPT_EXECUTOR_TENSOR_PARALLEL,
    get_gpu_info,
    get_platform,
    resolve_gpt_executor,
)

_logger = logging.getLogger(__name__)

REPORT_FILENAME = "task_error_reports.json"

EXECUTOR_MODE_TENSOR_PARALLEL = "tensor_parallel"
EXECUTOR_MODE_DEVICE_MAP = "device_map"


class TaskErrorReport(BaseModel):
    node_address: str
    task_id_commitment: str
    task_args: str
    error_type: str
    message: str
    stack_trace: str
    gpu_count: int = 0
    gpu_model: str = ""
    gpu_vram_mb: int = 0
    executor_mode: str = EXECUTOR_MODE_DEVICE_MAP
    captured_at: int

    @model_validator(mode="before")
    @classmethod
    def migrate_capture_time(cls, data):
        if isinstance(data, dict) and "captured_at" not in data:
            capture_time = data.get("capture_time")
            if capture_time is not None:
                migrated = dict(data)
                if isinstance(capture_time, str):
                    capture_time = datetime.fromisoformat(
                        capture_time.replace("Z", "+00:00")
                    )
                if isinstance(capture_time, datetime):
                    capture_time = int(capture_time.timestamp())
                migrated["captured_at"] = int(capture_time)
                migrated.pop("capture_time", None)
                return migrated
        return data


async def collect_worker_gpu_report_fields() -> tuple[str, int, str]:
    """Collect worker GPU model, per-card VRAM, and executor mode.

    The selected worker GPUs are always of one identical model, so a single
    per-card VRAM value describes every card. The executor mode is the
    Node-effective worker mode resolved at capture time: ``tensor_parallel``
    or ``device_map``. The report ``gpu_count`` field is supplied separately
    from the Worker result (executed card count) or ``0`` when no Worker
    result is available.
    """
    try:
        gpu_info = await get_gpu_info()
    except Exception:
        _logger.exception("Failed to collect GPU info for Task diagnostic")
        return "", 0, EXECUTOR_MODE_DEVICE_MAP

    gpu_count = len(gpu_info.device_uuids)
    if gpu_count == 0 and gpu_info.vram_total_mb > 0:
        gpu_count = 1
    gpu_vram_mb = gpu_info.vram_total_mb // gpu_count if gpu_count > 0 else 0

    executor = resolve_gpt_executor(get_platform(), gpu_count)
    if executor == GPT_EXECUTOR_TENSOR_PARALLEL:
        executor_mode = EXECUTOR_MODE_TENSOR_PARALLEL
    else:
        executor_mode = EXECUTOR_MODE_DEVICE_MAP
    return gpu_info.model, gpu_vram_mb, executor_mode


class FlushResult(BaseModel):
    reported: int
    remaining: int


class TaskErrorReportStore:
    def __init__(self, filename: str):
        self.filename = filename
        self._lock = Lock()

    @classmethod
    def from_config(cls, config: Config | None = None):
        if config is None:
            config = get_config()
        return cls(os.path.join(config.log.dir, REPORT_FILENAME))

    def _read_sync(self) -> list[TaskErrorReport]:
        if not os.path.exists(self.filename):
            return []
        with open(self.filename, mode="r", encoding="utf-8") as f:
            data = json.load(f)
        return [TaskErrorReport.model_validate(item) for item in data]

    def _write_sync(self, reports: list[TaskErrorReport]):
        dirname = os.path.dirname(self.filename)
        os.makedirs(dirname, exist_ok=True)
        fd, tmp_filename = tempfile.mkstemp(
            dir=dirname, prefix=f".{REPORT_FILENAME}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, mode="w", encoding="utf-8") as f:
                json.dump(
                    [report.model_dump(mode="json") for report in reports],
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_filename, self.filename)
        finally:
            if os.path.exists(tmp_filename):
                os.remove(tmp_filename)

    async def list(self) -> list[TaskErrorReport]:
        async with self._lock:
            return await to_thread.run_sync(self._read_sync)

    async def count(self) -> int:
        return len(await self.list())

    async def add(self, report: TaskErrorReport) -> bool:
        async with self._lock:
            reports = await to_thread.run_sync(self._read_sync)
            duplicate = any(
                item.node_address.lower() == report.node_address.lower()
                and item.task_id_commitment.lower() == report.task_id_commitment.lower()
                for item in reports
            )
            if duplicate:
                return False
            reports.append(report)
            await to_thread.run_sync(self._write_sync, reports)
            return True

    async def remove(self, report: TaskErrorReport) -> bool:
        async with self._lock:
            reports = await to_thread.run_sync(self._read_sync)
            remaining = [
                item
                for item in reports
                if not (
                    item.node_address.lower() == report.node_address.lower()
                    and item.task_id_commitment.lower()
                    == report.task_id_commitment.lower()
                )
            ]
            if len(remaining) == len(reports):
                return False
            await to_thread.run_sync(self._write_sync, remaining)
            return True


class TaskErrorReporter:
    def __init__(
        self,
        store: TaskErrorReportStore,
        relay: Relay,
        config: Config | None = None,
    ):
        self.store = store
        self.relay = relay
        self.config = config or get_config()
        self._flush_lock = Lock()
        self._condition = Condition()
        self._generation = 0

    async def capture(
        self,
        task_id_commitment: bytes,
        task_args: str,
        error_type: str,
        message: str,
        stack_trace: str,
        gpu_count: int = 0,
    ) -> bool:
        gpu_model, gpu_vram_mb, executor_mode = (
            await collect_worker_gpu_report_fields()
        )
        report = TaskErrorReport(
            node_address=str(self.relay.node_address),
            task_id_commitment="0x" + bytes(task_id_commitment).hex(),
            task_args=task_args,
            error_type=error_type,
            message=message,
            stack_trace=stack_trace,
            gpu_count=gpu_count,
            gpu_model=gpu_model,
            gpu_vram_mb=gpu_vram_mb,
            executor_mode=executor_mode,
            captured_at=int(datetime.now(timezone.utc).timestamp()),
        )
        created = await self.store.add(report)
        if created:
            await self.notify()
        return created

    async def notify(self):
        async with self._condition:
            self._generation += 1
            self._condition.notify_all()

    async def flush(self) -> FlushResult:
        async with self._flush_lock:
            reported = 0
            reports = await self.store.list()
            for report in reports:
                try:
                    await self.relay.report_task_diagnostic(report)
                except Exception:
                    _logger.exception(
                        "Failed to report task diagnostic for %s",
                        report.task_id_commitment,
                    )
                    break
                await self.store.remove(report)
                reported += 1
            return FlushResult(reported=reported, remaining=await self.store.count())

    async def run(self):
        generation = self._generation
        if self.config.task_error_report.automatic:
            try:
                await self.flush()
            except Exception:
                _logger.exception("Automatic Task diagnostic reporting failed")
        while True:
            if self.config.task_error_report.automatic:
                with move_on_after(30):
                    async with self._condition:
                        while generation == self._generation:
                            await self._condition.wait()
                        generation = self._generation
            else:
                async with self._condition:
                    while generation == self._generation:
                        await self._condition.wait()
                    generation = self._generation
            if self.config.task_error_report.automatic:
                try:
                    await self.flush()
                except Exception:
                    _logger.exception("Automatic Task diagnostic reporting failed")
