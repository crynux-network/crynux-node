import json
import logging
import os.path
import shutil
import time
from typing import Dict, List, Literal, Optional

from anyio import fail_after, get_cancelled_exc_class, sleep, to_thread
from hexbytes import HexBytes

from crynux_server import models
from crynux_server.config import Config, get_config
from crynux_server.relay import Relay, get_relay
from crynux_server.relay.exceptions import RelayError
from crynux_server.worker_manager import (TaskCancelled, TaskExecutionError,
                                          TaskInvalid)

from .state_cache import (InferenceTaskStateCache,
                          get_inference_task_state_cache)
from .utils import run_inference_task, validate_score

_logger = logging.getLogger(__name__)

# Extra seconds after the relay-side task timeout before the worker
# manager's watchdog restarts a hung worker
EXECUTION_DEADLINE_DELAY = 10

ExecutionOutcome = Literal["success", "invalid", "failed", "cancelled"]

_CLOSED_STATUSES = frozenset(
    [
        models.InferenceTaskStatus.EndSuccess,
        models.InferenceTaskStatus.EndGroupSuccess,
        models.InferenceTaskStatus.EndGroupRefund,
        models.InferenceTaskStatus.EndAborted,
        models.InferenceTaskStatus.ErrorReported,
    ]
)

_RUNNING_STATUSES = frozenset(
    [
        models.InferenceTaskStatus.Started,
        models.InferenceTaskStatus.ParametersUploaded,
    ]
)


def _is_task_id_empty(task_id_commitment: bytes) -> bool:
    return all(v == 0 for v in task_id_commitment)


def _is_task_not_found(e: RelayError) -> bool:
    return e.status_code == 400 and "Task not found" in e.message


# The single driver of all inference task behavior. Each cycle polls the
# node's current task pointer, fetches the fresh task status, derives at most
# one action from the status plus the local artifacts, and awaits it.
# There is no retry layer below the loop: every relay request is a single
# attempt and the loop cadence is the only retry mechanism.
class TaskReconciler(object):
    def __init__(
        self,
        relay: Optional[Relay] = None,
        state_cache: Optional[InferenceTaskStateCache] = None,
        config: Optional[Config] = None,
        interval: float = 1,
    ):
        if relay is None:
            relay = get_relay()
        self.relay = relay
        if state_cache is None:
            state_cache = get_inference_task_state_cache()
        self.cache = state_cache
        if config is None:
            config = get_config()
        self.config = config
        self.interval = interval

        # The task the loop is currently tracking as open
        self._tracked_task_id: Optional[bytes] = None
        # Process-local execution outcome per task, valid for one worker lifetime
        self._execution_outcomes: Dict[bytes, ExecutionOutcome] = {}

    async def run(self):
        _logger.info("Task reconciler started")
        while True:
            try:
                await self._reconcile_once()
            except get_cancelled_exc_class():
                raise
            except Exception as e:
                _logger.exception(e)
                _logger.error("Task reconcile cycle failed")
            await sleep(self.interval)

    async def _reconcile_once(self):
        pointer = await self.relay.node_get_current_task()
        current: Optional[bytes] = (
            None if _is_task_id_empty(pointer) else bytes(pointer)
        )

        tracked = self._tracked_task_id
        if tracked is not None and tracked != current:
            await self._close_departed_task(tracked)
            self._tracked_task_id = None

        if current is None:
            return

        self._tracked_task_id = current
        state = await self._load_state(current)
        if self._is_closed(state):
            return

        try:
            task = await self.relay.get_task(current)
        except RelayError as e:
            if _is_task_not_found(e):
                _logger.error(
                    f"Task {current.hex()} does not exist on the relay, "
                    "close it locally as aborted"
                )
                await self._close_task(state, models.InferenceTaskStatus.EndAborted)
                return
            raise

        await self._sync_state(state, task)
        await self._apply_action(state, task)

    # The pointer no longer refers to this task: fetch its status once by id
    # and close it locally
    async def _close_departed_task(self, task_id: bytes):
        if not await self.cache.has(task_id):
            return
        state = await self.cache.load(task_id)
        if self._is_closed(state):
            return

        try:
            task = await self.relay.get_task(task_id)
            final_status = task.status
        except RelayError as e:
            if not _is_task_not_found(e):
                raise
            final_status = models.InferenceTaskStatus.EndAborted
        await self._close_task(state, final_status)

    async def _load_state(self, task_id: bytes) -> models.InferenceTaskState:
        if await self.cache.has(task_id):
            return await self.cache.load(task_id)
        state = models.InferenceTaskState(
            task_id_commitment=task_id,
            timeout=0,
            status=models.InferenceTaskStatus.Queued,
            task_type=models.TaskType.SD,
        )
        await self.cache.dump(state)
        return state

    async def _sync_state(
        self, state: models.InferenceTaskState, task: models.RelayTask
    ):
        timeout = self._task_deadline(task)
        changed = False
        if state.timeout != timeout:
            state.timeout = timeout
            changed = True
        if state.status != task.status:
            state.status = task.status
            changed = True
        if state.task_type != task.task_type:
            state.task_type = task.task_type
            changed = True
        if changed:
            await self.cache.dump(state)

    def _task_deadline(self, task: models.RelayTask) -> int:
        start_timestamp = 0
        if task.start_time is not None:
            start_timestamp = int(task.start_time.timestamp())
        if start_timestamp == 0:
            start_timestamp = int(time.time())
        return start_timestamp + task.timeout

    def _is_closed(self, state: models.InferenceTaskState) -> bool:
        if state.status in _CLOSED_STATUSES:
            return True
        return (
            state.status == models.InferenceTaskStatus.EndInvalidated
            and state.result_uploaded
        )

    # Derive at most one action from the fresh relay status and the local
    # task record, per the condition-to-action table in docs/task-lifecycle.md
    async def _apply_action(
        self, state: models.InferenceTaskState, task: models.RelayTask
    ):
        status = task.status
        task_id = bytes(state.task_id_commitment)

        if status in _RUNNING_STATUSES:
            if validate_score(state.score):
                await self._submit_score(state)
                return
            outcome = self._execution_outcomes.get(task_id)
            if outcome is None:
                await self._execute(state, task)
            elif outcome == "invalid":
                await self._report_error(state)
            elif outcome in ("failed", "cancelled"):
                # Stay silent: the relay's timeout processor aborts the task
                await self._close_task(state, models.InferenceTaskStatus.EndAborted)
        elif status == models.InferenceTaskStatus.ScoreReady:
            # No action, wait for validation
            pass
        elif status in (
            models.InferenceTaskStatus.Validated,
            models.InferenceTaskStatus.GroupValidated,
            models.InferenceTaskStatus.EndInvalidated,
        ):
            if not state.result_uploaded:
                await self._upload_result(state)
            elif status == models.InferenceTaskStatus.EndInvalidated:
                await self._close_task(state, models.InferenceTaskStatus.EndInvalidated)
        elif status in (
            models.InferenceTaskStatus.EndSuccess,
            models.InferenceTaskStatus.EndGroupSuccess,
            models.InferenceTaskStatus.EndGroupRefund,
            models.InferenceTaskStatus.EndAborted,
            models.InferenceTaskStatus.ErrorReported,
        ):
            await self._close_task(state, status)

    async def _execute(
        self, state: models.InferenceTaskState, task: models.RelayTask
    ):
        task_id = bytes(state.task_id_commitment)
        task_id_hex = HexBytes(task_id).hex()
        deadline = self._task_deadline(task) + EXECUTION_DEADLINE_DELAY

        _logger.info(f"Start executing task {task_id_hex}")
        try:
            files, score, checkpoint = await self._run_task_on_worker(
                state, task, deadline
            )
            if not validate_score(score):
                raise TaskExecutionError(
                    f"Task {task_id_hex} score {score.hex()} is invalid"
                )
            # Persist the result files and score before any submission attempt
            state.files = files
            state.score = score
            state.checkpoint = checkpoint
            await self.cache.dump(state)
            self._execution_outcomes[task_id] = "success"
            _logger.info(f"Task {task_id_hex} execution success")
        except TaskInvalid as e:
            _logger.exception(e)
            _logger.error(f"Task {task_id_hex} is invalid, will report the task error")
            self._execution_outcomes[task_id] = "invalid"
        except TaskCancelled:
            _logger.error(f"Task {task_id_hex} execution is cancelled")
            self._execution_outcomes[task_id] = "cancelled"
        except TaskExecutionError as e:
            _logger.exception(e)
            _logger.error(f"Task {task_id_hex} execution failed")
            self._execution_outcomes[task_id] = "failed"

    # Run the task on the worker through the worker manager and return
    # (files, score, checkpoint)
    async def _run_task_on_worker(
        self,
        state: models.InferenceTaskState,
        task: models.RelayTask,
        deadline: float,
    ):
        task_id = bytes(state.task_id_commitment)
        task_dir = os.path.join(
            self.config.task_config.output_dir, HexBytes(task_id).hex()
        )
        task.task_args = models.normalize_task_args_model_names(
            task.task_args, state.task_type
        )

        if state.task_type == models.TaskType.SD_FT_LORA:
            args = json.loads(task.task_args)
            checkpoint = args.get("checkpoint", None)
            if checkpoint is not None:
                checkpoint_dir = os.path.join(task_dir, "input_checkpoint")
                await self.relay.get_checkpoint(task_id, checkpoint_dir)
                args["checkpoint"] = checkpoint_dir
                task.task_args = json.dumps(args)

        if not os.path.exists(task_dir):
            os.makedirs(task_dir, exist_ok=True)

        task_models = [
            models.ModelConfig.from_model_id(model_id) for model_id in task.model_ids
        ]
        files, hashes, checkpoint = await run_inference_task(
            task_id_commitment=task_id,
            task_type=state.task_type,
            models=task_models,
            task_args=task.task_args,
            task_dir=task_dir,
            deadline=deadline,
        )
        return files, b"".join(hashes), checkpoint

    async def _report_error(self, state: models.InferenceTaskState):
        task_id = bytes(state.task_id_commitment)
        await self.relay.report_task_error(
            task_id_commitment=task_id,
            task_error=models.TaskError.ParametersValidationFailed,
        )
        _logger.info(f"Reported the error of task {HexBytes(task_id).hex()}")

    async def _submit_score(self, state: models.InferenceTaskState):
        task_id = bytes(state.task_id_commitment)
        await self.relay.submit_task_score(
            task_id_commitment=task_id, score=state.score
        )
        _logger.info(f"Submitted the score of task {HexBytes(task_id).hex()}")

    async def _upload_result(self, state: models.InferenceTaskState):
        task_id = bytes(state.task_id_commitment)
        await self.relay.upload_task_result(task_id, state.files, state.checkpoint)
        state.result_uploaded = True
        await self.cache.dump(state)
        _logger.info(f"Uploaded the result of task {HexBytes(task_id).hex()}")

    # Persist the final status and release the task's local resources.
    # A closed task produces no further actions
    async def _close_task(
        self,
        state: models.InferenceTaskState,
        final_status: models.InferenceTaskStatus,
    ):
        task_id = bytes(state.task_id_commitment)

        if final_status != models.InferenceTaskStatus.EndInvalidated:

            def delete_result_files(files: List[str]) -> None:
                if len(files) > 0:
                    dirname = os.path.dirname(files[0])
                    if os.path.exists(dirname):
                        shutil.rmtree(dirname)

            with fail_after(10, shield=True):
                await to_thread.run_sync(delete_result_files, state.files)

        state.status = final_status
        await self.cache.dump(state)
        self._execution_outcomes.pop(task_id, None)
        _logger.info(
            f"Task {HexBytes(task_id).hex()} is closed locally "
            f"with status {final_status.name}"
        )
