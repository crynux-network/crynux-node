from collections import Counter
from datetime import datetime
from typing import Dict, Optional

import httpx
from anyio import create_task_group, sleep

from crynux_server import models
from crynux_server.relay.exceptions import RelayError
from crynux_server.task import MemoryInferenceTaskStateCache, TaskReconciler
from crynux_server.worker_manager import (TaskCancelled, TaskExecutionError,
                                          TaskInvalid)

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
