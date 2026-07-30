import time
from collections import Counter
from datetime import datetime
from types import SimpleNamespace
from typing import Dict, Optional

import httpx
import pytest
from anyio import create_task_group, sleep

from crynux_server import models
from crynux_server.relay.exceptions import RelayError
from crynux_server.task import MemoryInferenceTaskStateCache, TaskReconciler
from crynux_server.worker_manager import (TaskCancelled, TaskDownloadError,
                                          TaskExecutionError, TaskInvalid,
                                          TaskCancellation,
                                          TaskCancellationType, TaskPhase)
from crynux_server.task import reconciler as reconciler_module

TASK_ID_1 = bytes([1] * 32)
TASK_ID_2 = bytes([2] * 32)

VALID_SCORE = bytes([1] * 8)
RESULT_FILES = ["nonexistent_task_results_dir/0.png"]

TASK_NOT_FOUND_MSG = (
    '{"field_name": "task_id_commitment", "field_message": "Task not found"}'
)


def make_relay_task(
    task_id_commitment: bytes, status: models.InferenceTaskStatus
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
        timeout=300,
        min_vram=0,
        required_gpu="",
        required_gpu_vram=0,
        task_fee="0",
        task_size=1,
        model_ids=["test/model"],
        score="0x",
        qos_score=0,
        selected_node="0x0000000000000000000000000000000000000002",
        create_time=datetime.now(),
        start_time=datetime.now(),
        score_ready_time=None,
        validated_time=None,
        result_uploaded_time=None,
    )


# Fake relay whose current-task pointer and per-task statuses are mutable
# test state. Any method can be told to fail on its next call.
class FakeRelay:
    def __init__(self):
        self.pointer: Optional[bytes] = None
        self.statuses: Dict[bytes, models.InferenceTaskStatus] = {}
        self.calls = Counter()
        self._next_errors: Dict[str, Exception] = {}
        # When True, a state-changing request applies its effect before
        # raising the injected error (response lost after a successful write)
        self.apply_before_error = False

    def fail_next(self, method: str, exc: Exception):
        self._next_errors[method] = exc

    def _enter(self, method: str, applied=None):
        self.calls[method] += 1
        exc = self._next_errors.pop(method, None)
        if exc is not None:
            if self.apply_before_error and applied is not None:
                applied()
            raise exc

    async def node_get_current_task(self) -> bytes:
        self._enter("node_get_current_task")
        return self.pointer if self.pointer is not None else bytes(32)

    async def get_task(self, task_id_commitment: bytes) -> models.RelayTask:
        self._enter("get_task")
        task_id = bytes(task_id_commitment)
        if task_id not in self.statuses:
            raise RelayError(400, "getTask", TASK_NOT_FOUND_MSG)
        return make_relay_task(task_id, self.statuses[task_id])

    async def report_task_error(self, task_id_commitment: bytes, task_error):
        task_id = bytes(task_id_commitment)

        def apply():
            self.statuses[task_id] = models.InferenceTaskStatus.ErrorReported

        self._enter("report_task_error", apply)
        apply()

    async def submit_task_score(self, task_id_commitment: bytes, score: bytes):
        task_id = bytes(task_id_commitment)

        def apply():
            self.statuses[task_id] = models.InferenceTaskStatus.ScoreReady

        self._enter("submit_task_score", apply)
        apply()

    async def upload_task_result(
        self, task_id_commitment: bytes, file_paths, checkpoint_dir=None
    ):
        task_id = bytes(task_id_commitment)

        def apply():
            if self.statuses[task_id] != models.InferenceTaskStatus.EndInvalidated:
                self.statuses[task_id] = models.InferenceTaskStatus.EndSuccess

        self._enter("upload_task_result", apply)
        apply()

    async def get_checkpoint(self, task_id_commitment: bytes, checkpoint_dir: str):
        self._enter("get_checkpoint")


# Reconciler whose worker execution follows a scripted result: a
# (files, score, checkpoint) tuple or an exception to raise
class ScriptedReconciler(TaskReconciler):
    def __init__(self, relay, state_cache, worker_result=None):
        super().__init__(relay=relay, state_cache=state_cache, config=object())
        self.execute_calls = 0
        self.worker_result = worker_result or (RESULT_FILES, VALID_SCORE, None)

    async def _run_task_on_worker(self, state, task, deadline):
        self.execute_calls += 1
        if isinstance(self.worker_result, Exception) or (
            isinstance(self.worker_result, type)
            and issubclass(self.worker_result, Exception)
        ):
            raise self.worker_result
        return self.worker_result


class FakeDownloadResult:
    def __init__(self, error=None):
        self.error = error

    async def get(self):
        if self.error is not None:
            raise self.error


class FakeDownloadManager:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def send_task(self, task_input, deadline=None):
        self.calls.append((task_input, deadline))
        return FakeDownloadResult(self.error)


class FakeErrorReporter:
    def __init__(self):
        self.reports = []

    async def capture(
        self, task_id, task_args, error_type, message, stack_trace, gpu_count=0
    ):
        self.reports.append(
            {
                "task_id": task_id,
                "task_args": task_args,
                "error_type": error_type,
                "message": message,
                "stack_trace": stack_trace,
                "gpu_count": gpu_count,
            }
        )
        return True


def make_reconciler(relay, worker_result=None, cache=None):
    if cache is None:
        cache = MemoryInferenceTaskStateCache()
    return ScriptedReconciler(
        relay=relay, state_cache=cache, worker_result=worker_result
    )


# One reconcile cycle with the same exception containment as the loop
async def cycle(reconciler: TaskReconciler):
    try:
        await reconciler._reconcile_once()
    except Exception:
        pass


async def test_happy_path():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    reconciler = make_reconciler(relay)

    # Started + no score -> execute
    await cycle(reconciler)
    assert reconciler.execute_calls == 1
    state = await reconciler.cache.load(TASK_ID_1)
    assert state.score == VALID_SCORE
    assert state.files == RESULT_FILES

    # Started + valid score -> submit
    await cycle(reconciler)
    assert relay.calls["submit_task_score"] == 1
    assert relay.statuses[TASK_ID_1] == models.InferenceTaskStatus.ScoreReady

    # ScoreReady -> no action
    await cycle(reconciler)
    assert relay.calls["submit_task_score"] == 1
    assert relay.calls["upload_task_result"] == 0

    # Validated + no marker -> upload
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Validated
    await cycle(reconciler)
    assert relay.calls["upload_task_result"] == 1
    state = await reconciler.cache.load(TASK_ID_1)
    assert state.result_uploaded

    # EndSuccess -> close locally
    await cycle(reconciler)
    state = await reconciler.cache.load(TASK_ID_1)
    assert state.status == models.InferenceTaskStatus.EndSuccess

    # A closed task produces no further actions
    get_task_calls = relay.calls["get_task"]
    await cycle(reconciler)
    assert relay.calls["get_task"] == get_task_calls
    assert reconciler.execute_calls == 1
    assert relay.calls["report_task_error"] == 0


async def test_execution_error_closes_task_silently():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    reconciler = make_reconciler(relay, worker_result=TaskExecutionError("boom"))

    await cycle(reconciler)
    assert reconciler.execute_calls == 1

    # Started + execution error -> close locally, no error report
    await cycle(reconciler)
    state = await reconciler.cache.load(TASK_ID_1)
    assert state.status == models.InferenceTaskStatus.EndAborted
    assert relay.calls["report_task_error"] == 0
    assert relay.calls["submit_task_score"] == 0

    # No further actions in later cycles, and no re-execution
    get_task_calls = relay.calls["get_task"]
    await cycle(reconciler)
    await cycle(reconciler)
    assert relay.calls["get_task"] == get_task_calls
    assert reconciler.execute_calls == 1


async def test_worker_traceback_is_preserved_in_diagnostic():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    worker_traceback = "Traceback (most recent call last):\nworker.py:1\nCUDA error"
    reconciler = make_reconciler(
        relay, worker_result=TaskExecutionError(worker_traceback, gpu_count=2)
    )
    reporter = FakeErrorReporter()
    reconciler.error_reporter = reporter

    await cycle(reconciler)

    assert reporter.reports[0]["error_type"] == "TaskExecutionError"
    assert reporter.reports[0]["stack_trace"] == worker_traceback
    assert reporter.reports[0]["gpu_count"] == 2
    assert relay.calls["report_task_error"] == 0


async def test_download_worker_traceback_and_server_chain_are_preserved():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    worker_traceback = (
        "Traceback (most recent call last):\n"
        '  File "download.py", line 7\n'
        "RuntimeError: download failed"
    )
    error = TaskExecutionError("Task auxiliary model download failed")
    error.__cause__ = TaskDownloadError(worker_traceback)
    reconciler = make_reconciler(relay, worker_result=error)
    reporter = FakeErrorReporter()
    reconciler.error_reporter = reporter

    await cycle(reconciler)

    report = reporter.reports[0]
    assert report["error_type"] == "TaskDownloadError"
    assert report["stack_trace"].startswith(worker_traceback)
    assert "Server exception chain:" in report["stack_trace"]
    assert "Task auxiliary model download failed" in report["stack_trace"]


async def test_reasoned_cancellation_reports_timeout_context():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    cancellation = TaskCancellation(
        cancellation_type=TaskCancellationType.WORKER_TASK_TIMEOUT,
        initiated_by="worker_manager.watchdog",
        reason="deadline reached",
        worker_role="inference",
        worker_id=7,
        phase=TaskPhase.SENT,
        task_id=TASK_ID_1.hex(),
        deadline=time.time() - 1,
        cancelled_at=time.time(),
    )
    reconciler = make_reconciler(relay, worker_result=TaskCancelled(cancellation))
    reporter = FakeErrorReporter()
    reconciler.error_reporter = reporter

    await cycle(reconciler)

    report = reporter.reports[0]
    assert report["error_type"] == "WorkerTaskTimeout"
    assert report["gpu_count"] == 0
    assert "Task phase: sent" in report["stack_trace"]
    assert "No Worker result was received" in report["stack_trace"]


async def test_runner_sync_cancellation_is_not_reported():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    cancellation = TaskCancellation(
        cancellation_type=TaskCancellationType.RUNNER_VERSION_SYNC,
        initiated_by="node_manager",
        reason="runner version synchronization",
        worker_role="download",
        worker_id=3,
        phase=TaskPhase.SENT,
        task_id=TASK_ID_1.hex(),
        deadline=time.time() + 30,
        cancelled_at=time.time(),
    )
    reconciler = make_reconciler(relay, worker_result=TaskCancelled(cancellation))
    reporter = FakeErrorReporter()
    reconciler.error_reporter = reporter

    await cycle(reconciler)

    assert reporter.reports == []


async def test_download_restart_cancellation_reports_worker_restarted():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    cancellation = TaskCancellation(
        cancellation_type=TaskCancellationType.WORKER_RESTARTED,
        initiated_by="worker_manager.restart",
        reason="download worker health recovery",
        worker_role="download",
        worker_id=4,
        phase=TaskPhase.SENT,
        task_id=f"{TASK_ID_1.hex()}:aux:0",
        deadline=time.time() + 30,
        cancelled_at=time.time(),
    )
    error = TaskExecutionError("Task auxiliary model download failed")
    error.__cause__ = TaskCancelled(cancellation)
    reconciler = make_reconciler(relay, worker_result=error)
    reporter = FakeErrorReporter()
    reconciler.error_reporter = reporter

    await cycle(reconciler)

    report = reporter.reports[0]
    assert report["error_type"] == "WorkerRestarted"
    assert "Cancellation type: worker_restarted" in report["stack_trace"]
    assert "Worker role: download" in report["stack_trace"]


async def test_auxiliary_models_download_before_inference(monkeypatch):
    relay = FakeRelay()
    reconciler = make_reconciler(relay)
    manager = FakeDownloadManager()
    monkeypatch.setattr(
        reconciler_module, "get_worker_manager", lambda role: manager
    )
    task = make_relay_task(TASK_ID_1, models.InferenceTaskStatus.Started)
    task.model_ids = [
        "base:crynux-network/sdxl",
        "lora:crynux-network/style",
        "controlnet:lllyasviel/canny",
    ]
    deadline = task.start_time.timestamp() + task.timeout

    await reconciler._download_auxiliary_models(
        TASK_ID_1.hex(), task, deadline
    )

    assert len(manager.calls) == 2
    assert [
        call[0].task.model.type for call in manager.calls
    ] == ["lora", "controlnet"]
    assert all(call[1] == deadline for call in manager.calls)


async def test_auxiliary_download_failure_skips_inference(monkeypatch, tmp_path):
    relay = FakeRelay()
    reconciler = TaskReconciler(
        relay=relay,
        state_cache=MemoryInferenceTaskStateCache(),
        config=SimpleNamespace(
            task_config=SimpleNamespace(output_dir=str(tmp_path))
        ),
    )
    manager = FakeDownloadManager(TaskDownloadError("network error"))
    monkeypatch.setattr(
        reconciler_module, "get_worker_manager", lambda role: manager
    )
    inference_started = False

    async def fake_run_inference_task(**kwargs):
        nonlocal inference_started
        inference_started = True

    monkeypatch.setattr(
        reconciler_module, "run_inference_task", fake_run_inference_task
    )
    task = make_relay_task(TASK_ID_1, models.InferenceTaskStatus.Started)
    task.model_ids = ["base:crynux-network/sdxl", "lora:crynux-network/style"]
    state = models.InferenceTaskState(
        task_id_commitment=TASK_ID_1,
        timeout=0,
        status=models.InferenceTaskStatus.Started,
        task_type=models.TaskType.SD,
    )

    with pytest.raises(TaskExecutionError):
        await reconciler._run_task_on_worker(state, task, time.time() + 60)
    assert not inference_started


async def test_expired_auxiliary_download_deadline_skips_worker(monkeypatch):
    relay = FakeRelay()
    reconciler = make_reconciler(relay)
    manager = FakeDownloadManager()
    monkeypatch.setattr(
        reconciler_module, "get_worker_manager", lambda role: manager
    )
    task = make_relay_task(TASK_ID_1, models.InferenceTaskStatus.Started)
    task.model_ids = ["base:crynux-network/sdxl", "lora:crynux-network/style"]

    with pytest.raises(TaskExecutionError):
        await reconciler._download_auxiliary_models(
            TASK_ID_1.hex(), task, time.time() - 1
        )
    assert manager.calls == []


async def test_invalid_auxiliary_model_id_is_execution_error(monkeypatch):
    relay = FakeRelay()
    reconciler = make_reconciler(relay)
    manager = FakeDownloadManager()
    monkeypatch.setattr(
        reconciler_module, "get_worker_manager", lambda role: manager
    )
    task = make_relay_task(TASK_ID_1, models.InferenceTaskStatus.Started)
    task.model_ids = ["base:crynux-network/sdxl", "unsupported:model"]

    with pytest.raises(TaskExecutionError):
        await reconciler._download_auxiliary_models(
            TASK_ID_1.hex(), task, time.time() + 60
        )
    assert manager.calls == []


async def test_cancelled_execution_closes_task_silently():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    reconciler = make_reconciler(relay, worker_result=TaskCancelled)

    await cycle(reconciler)
    await cycle(reconciler)

    state = await reconciler.cache.load(TASK_ID_1)
    assert state.status == models.InferenceTaskStatus.EndAborted
    assert reconciler.execute_calls == 1
    assert relay.calls["report_task_error"] == 0


async def test_invalid_task_reports_error():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    reconciler = make_reconciler(relay, worker_result=TaskInvalid("Task args invalid"))

    await cycle(reconciler)
    assert reconciler.execute_calls == 1

    # Started + TaskInvalid outcome -> report the task error
    await cycle(reconciler)
    assert relay.calls["report_task_error"] == 1
    assert relay.calls["submit_task_score"] == 0
    assert relay.statuses[TASK_ID_1] == models.InferenceTaskStatus.ErrorReported

    # ErrorReported observed -> close locally
    await cycle(reconciler)
    state = await reconciler.cache.load(TASK_ID_1)
    assert state.status == models.InferenceTaskStatus.ErrorReported


async def test_submit_transport_failure_rederives_submit():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    reconciler = make_reconciler(relay)

    await cycle(reconciler)

    # The submit request fails in transport without being applied
    relay.fail_next("submit_task_score", httpx.ConnectError("connection failed"))
    await cycle(reconciler)
    assert relay.calls["submit_task_score"] == 1
    assert relay.statuses[TASK_ID_1] == models.InferenceTaskStatus.Started

    # The next cycle re-derives the submit from fresh state
    await cycle(reconciler)
    assert relay.calls["submit_task_score"] == 2
    assert relay.statuses[TASK_ID_1] == models.InferenceTaskStatus.ScoreReady


async def test_submit_applied_but_response_lost_converges():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    relay.apply_before_error = True
    reconciler = make_reconciler(relay)

    await cycle(reconciler)

    # The submit is applied on the relay but the response is lost
    relay.fail_next("submit_task_score", httpx.ReadError("response lost"))
    await cycle(reconciler)
    assert relay.calls["submit_task_score"] == 1
    assert relay.statuses[TASK_ID_1] == models.InferenceTaskStatus.ScoreReady

    # The next cycle observes ScoreReady and does not resubmit
    await cycle(reconciler)
    assert relay.calls["submit_task_score"] == 1


async def test_duplicate_write_rejection_converges():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    relay.apply_before_error = True
    reconciler = make_reconciler(relay)

    await cycle(reconciler)

    # Submit applied, then the duplicate is rejected with 4xx: the fresh
    # status already shows ScoreReady, so the loop proceeds
    relay.fail_next(
        "submit_task_score", RelayError(400, "submitTaskScore", "Illegal task state")
    )
    await cycle(reconciler)
    assert relay.statuses[TASK_ID_1] == models.InferenceTaskStatus.ScoreReady
    await cycle(reconciler)
    assert relay.calls["submit_task_score"] == 1

    # Upload applied, then the duplicate is rejected with 4xx: the fresh
    # status already shows EndSuccess, so the task is closed
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Validated
    relay.fail_next(
        "upload_task_result", RelayError(400, "uploadTaskResult", "Illegal task state")
    )
    await cycle(reconciler)
    assert relay.statuses[TASK_ID_1] == models.InferenceTaskStatus.EndSuccess
    await cycle(reconciler)
    state = await reconciler.cache.load(TASK_ID_1)
    assert state.status == models.InferenceTaskStatus.EndSuccess
    assert relay.calls["upload_task_result"] == 1


async def test_pointer_moves_to_new_task():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    reconciler = make_reconciler(relay)

    await cycle(reconciler)
    assert reconciler.execute_calls == 1

    # The old task ends on the relay and the pointer moves to a new task
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.EndSuccess
    relay.statuses[TASK_ID_2] = models.InferenceTaskStatus.Started
    relay.pointer = TASK_ID_2
    get_task_calls = relay.calls["get_task"]

    await cycle(reconciler)
    # One status fetch closed the old task, one served the new task's cycle
    assert relay.calls["get_task"] == get_task_calls + 2
    state1 = await reconciler.cache.load(TASK_ID_1)
    assert state1.status == models.InferenceTaskStatus.EndSuccess
    # The new task is picked up in the same cycle
    assert reconciler.execute_calls == 2


async def test_pointer_cleared_closes_old_task():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    reconciler = make_reconciler(relay)

    await cycle(reconciler)

    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.EndAborted
    relay.pointer = None
    get_task_calls = relay.calls["get_task"]

    await cycle(reconciler)
    assert relay.calls["get_task"] == get_task_calls + 1
    state = await reconciler.cache.load(TASK_ID_1)
    assert state.status == models.InferenceTaskStatus.EndAborted

    # Subsequent empty-pointer cycles derive no action
    await cycle(reconciler)
    await cycle(reconciler)
    assert relay.calls["get_task"] == get_task_calls + 1


async def test_task_not_found_closes_task_as_aborted():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    # No status entry: the relay authoritatively answers task not found
    reconciler = make_reconciler(relay)

    await cycle(reconciler)
    state = await reconciler.cache.load(TASK_ID_1)
    assert state.status == models.InferenceTaskStatus.EndAborted
    assert reconciler.execute_calls == 0


async def test_poll_failure_derives_no_action():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    reconciler = make_reconciler(relay)

    relay.fail_next("node_get_current_task", httpx.ConnectError("connection failed"))
    await cycle(reconciler)
    assert relay.calls["get_task"] == 0
    assert reconciler.execute_calls == 0

    # The loop continues on the next cycle
    await cycle(reconciler)
    assert reconciler.execute_calls == 1


async def test_run_loop_contains_cycle_exceptions():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    reconciler = make_reconciler(relay)
    reconciler.interval = 0.01

    relay.fail_next("node_get_current_task", RuntimeError("unexpected"))
    relay.fail_next("get_task", httpx.ConnectError("connection failed"))

    async with create_task_group() as tg:
        tg.start_soon(reconciler.run)
        while reconciler.execute_calls == 0:
            await sleep(0.01)
        tg.cancel_scope.cancel()

    assert relay.calls["node_get_current_task"] >= 3
    assert reconciler.execute_calls == 1


async def test_restart_recovery_with_persisted_score_skips_execution():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    cache = MemoryInferenceTaskStateCache()
    await cache.dump(
        models.InferenceTaskState(
            task_id_commitment=TASK_ID_1,
            timeout=0,
            status=models.InferenceTaskStatus.Started,
            task_type=models.TaskType.SD,
            files=RESULT_FILES,
            score=VALID_SCORE,
        )
    )
    reconciler = make_reconciler(relay, cache=cache)

    await cycle(reconciler)
    assert reconciler.execute_calls == 0
    assert relay.calls["submit_task_score"] == 1


async def test_restart_recovery_without_score_reexecutes():
    relay = FakeRelay()
    relay.pointer = TASK_ID_1
    relay.statuses[TASK_ID_1] = models.InferenceTaskStatus.Started
    cache = MemoryInferenceTaskStateCache()
    await cache.dump(
        models.InferenceTaskState(
            task_id_commitment=TASK_ID_1,
            timeout=0,
            status=models.InferenceTaskStatus.Started,
            task_type=models.TaskType.SD,
        )
    )
    reconciler = make_reconciler(relay, cache=cache)

    await cycle(reconciler)
    assert reconciler.execute_calls == 1
