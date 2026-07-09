import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from anyio import sleep

from crynux_server import models
from crynux_server.relay.exceptions import RelayError
from crynux_server.task import (InferenceTaskRunner, InferenceTaskRunnerBase,
                                MemoryInferenceTaskStateCache,
                                TaskNotFoundError)


def make_relay_task(
    task_id_commitment: bytes,
    status: models.InferenceTaskStatus,
    start_time: datetime,
    timeout: int = 0,
) -> models.RelayTask:
    return models.RelayTask(
        sequence=1,
        task_args="{}",
        task_id_commitment="0x" + bytes(task_id_commitment).hex(),
        creator="0x0000000000000000000000000000000000000001",
        sampling_seed="0x" + bytes(32).hex(),
        nonce="0x" + bytes(32).hex(),
        status=status,
        task_type=models.TaskType.SD,
        task_version="3.0.0",
        timeout=timeout,
        min_vram=0,
        required_gpu="",
        required_gpu_vram=0,
        task_fee="0",
        task_size=1,
        model_ids=["test/model"],
        score="0x",
        qos_score=0,
        selected_node="0x0000000000000000000000000000000000000002",
        create_time=start_time,
        start_time=start_time,
        score_ready_time=None,
        validated_time=None,
        result_uploaded_time=None,
    )


# Test runner whose get_task follows a script of task statuses and exceptions.
# Each get_task call consumes the next script item; the last item repeats.
class StatusScriptRunner(InferenceTaskRunnerBase):
    def __init__(
        self,
        task_id_commitment: bytes,
        script: list,
        start_time_offset: float = 4.8,
        task_timeout: int = 0,
    ):
        super().__init__(
            task_id_commitment=task_id_commitment,
            state_cache=MemoryInferenceTaskStateCache(),
            contracts=object(),
        )
        self.upload_calls = 0
        self.cancel_calls = 0
        self.execute_calls = 0
        self.cleaned = False
        self._script = script
        self._script_index = 0
        self._start_time = datetime.now() - timedelta(seconds=start_time_offset)
        self._task_timeout = task_timeout

    async def get_task(self) -> models.RelayTask:
        item = self._script[min(self._script_index, len(self._script) - 1)]
        self._script_index += 1
        if isinstance(item, Exception):
            raise item
        return make_relay_task(
            self.task_id_commitment, item, self._start_time, self._task_timeout
        )

    async def cancel_task(self):
        self.cancel_calls += 1

    async def execute_task(self):
        self.execute_calls += 1

    async def upload_result(self):
        self.upload_calls += 1

    async def cleanup(self):
        self.cleaned = True
        del self.state


class StuckExecutionRunner(StatusScriptRunner):
    async def execute_task(self):
        self.execute_calls += 1
        await sleep(30)


class FakeRelay:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def get_task(self, task_id_commitment: bytes):
        raise self._exc


def make_inference_runner(exc: Exception) -> InferenceTaskRunner:
    return InferenceTaskRunner(
        task_id_commitment=bytes([9] * 32),
        state_cache=MemoryInferenceTaskStateCache(),
        contracts=object(),
        relay=FakeRelay(exc),
        config=object(),
    )


async def test_end_invalidated_upload_exits_without_cancel():
    runner = StatusScriptRunner(
        task_id_commitment=bytes([1] * 32),
        script=[
            models.InferenceTaskStatus.Queued,
            models.InferenceTaskStatus.EndInvalidated,
        ],
        # Keep delay short so the old timeout/cancel path is deterministic in tests.
        start_time_offset=3.5,
    )

    start = time.monotonic()
    await runner.run(interval=0.01)
    elapsed = time.monotonic() - start

    assert runner.upload_calls == 1
    assert runner.cancel_calls == 0
    assert runner.cleaned
    assert elapsed < 0.5


async def test_transient_relay_errors_do_not_kill_runner():
    runner = StatusScriptRunner(
        task_id_commitment=bytes([2] * 32),
        script=[
            models.InferenceTaskStatus.Queued,
            RelayError(502, "getTask", "Bad Gateway"),
            httpx.ConnectError("connection failed"),
            RelayError(400, "getTask", "Task not ready"),
            models.InferenceTaskStatus.Started,
            models.InferenceTaskStatus.EndSuccess,
        ],
        start_time_offset=0,
        task_timeout=5,
    )

    start = time.monotonic()
    await runner.run(interval=0.01)
    elapsed = time.monotonic() - start

    assert runner.execute_calls == 1
    assert runner.cancel_calls == 0
    assert runner.cleaned
    # The runner must finish normally without hitting the timeout path
    assert elapsed < 5


async def test_task_not_found_on_startup_aborts_runner():
    runner = StatusScriptRunner(
        task_id_commitment=bytes([3] * 32),
        script=[TaskNotFoundError()],
        start_time_offset=0,
        task_timeout=5,
    )

    await runner.run(interval=0.01)

    assert runner.execute_calls == 0
    assert runner.cancel_calls == 0
    assert runner.cleaned
    state = await runner.cache.load(runner.task_id_commitment)
    assert state.status == models.InferenceTaskStatus.EndAborted


async def test_task_not_found_while_polling_aborts_runner():
    runner = StatusScriptRunner(
        task_id_commitment=bytes([4] * 32),
        script=[models.InferenceTaskStatus.Queued, TaskNotFoundError()],
        start_time_offset=0,
        task_timeout=5,
    )

    start = time.monotonic()
    await runner.run(interval=0.01)
    elapsed = time.monotonic() - start

    assert runner.execute_calls == 0
    assert runner.cancel_calls == 0
    assert runner.cleaned
    state = await runner.cache.load(runner.task_id_commitment)
    assert state.status == models.InferenceTaskStatus.EndAborted
    assert elapsed < 5


async def test_get_task_relay_error_mapping():
    not_found_msg = (
        '{"field_name": "task_id_commitment", "field_message": "Task not found"}'
    )
    runner = make_inference_runner(RelayError(400, "getTask", not_found_msg))
    with pytest.raises(TaskNotFoundError):
        await runner.get_task()

    runner = make_inference_runner(RelayError(400, "getTask", "Task not ready"))
    with pytest.raises(RelayError):
        await runner.get_task()

    runner = make_inference_runner(RelayError(502, "getTask", "Bad Gateway"))
    with pytest.raises(RelayError):
        await runner.get_task()


async def test_remote_abort_with_stuck_execution_restarts_worker(monkeypatch):
    worker_manager = SimpleNamespace(restart=AsyncMock())
    monkeypatch.setattr(
        "crynux_server.task.task_runner.get_worker_manager", lambda: worker_manager
    )

    runner = StuckExecutionRunner(
        task_id_commitment=bytes([5] * 32),
        script=[
            models.InferenceTaskStatus.Started,
            models.InferenceTaskStatus.EndAborted,
        ],
        start_time_offset=4,
    )

    await runner.run(interval=0.01)

    assert runner.execute_calls == 1
    assert runner.cancel_calls == 0
    assert runner.cleaned
    worker_manager.restart.assert_awaited_once()
    reason = worker_manager.restart.await_args.kwargs["reason"]
    assert "ended remotely" in reason
