import asyncio
import logging
from typing import Dict, Optional

from anyio import create_task_group, get_cancelled_exc_class
from anyio.abc import TaskGroup
from tenacity import (retry, stop_after_attempt, stop_never, wait_exponential,
                      wait_fixed)

from crynux_server.contracts import Contracts
from crynux_server.models import (DownloadTaskState, DownloadTaskStatus,
                                  TaskType)
from crynux_server.relay.abc import Relay

from .download_task import DownloadTaskRunner
from .reconciler import TaskReconciler
from .state_cache import DownloadTaskStateCache, InferenceTaskStateCache

_logger = logging.getLogger(__name__)

DOWNLOAD_TASK_MAX_ATTEMPTS = 3
DOWNLOAD_TASK_BACKOFF_MULTIPLIER_SECONDS = 30
DOWNLOAD_TASK_BACKOFF_MAX_SECONDS = 300


# Manage all tasks distributed to the node: the inference task reconcile loop
# and the download task runners
class TaskSystem(object):
    def __init__(
        self,
        inference_state_cache: InferenceTaskStateCache,
        download_state_cache: DownloadTaskStateCache,
        contracts: Contracts,
        relay: Relay,
        retry: bool = True,
        reconciler: Optional[TaskReconciler] = None,
    ) -> None:
        self._inference_state_cache = inference_state_cache
        self._download_state_cache = download_state_cache
        self._contracts = contracts
        self._relay = relay
        self._retry = retry

        if reconciler is None:
            reconciler = TaskReconciler(
                relay=relay, state_cache=inference_state_cache
            )
        self._reconciler = reconciler

        self._tg: Optional[TaskGroup] = None

        self._download_runners: Dict[str, DownloadTaskRunner] = {}

        self._task_queue = asyncio.Queue()

    # Run download task with the given task_id
    async def _run_download_task(self, task_id: str):
        try:
            runner = self._download_runners[task_id]

            @retry(
                stop=(
                    stop_after_attempt(DOWNLOAD_TASK_MAX_ATTEMPTS)
                    if self._retry
                    else stop_after_attempt(1)
                ),
                wait=wait_exponential(
                    multiplier=DOWNLOAD_TASK_BACKOFF_MULTIPLIER_SECONDS,
                    min=DOWNLOAD_TASK_BACKOFF_MULTIPLIER_SECONDS,
                    max=DOWNLOAD_TASK_BACKOFF_MAX_SECONDS,
                ),
                reraise=True,
            )
            async def _run_task_with_retry():
                try:
                    await runner.run()
                except get_cancelled_exc_class():
                    raise
                except Exception as e:
                    _logger.exception(e)
                    _logger.error(f"Download task {task_id} error: {str(e)}")
                    raise

            try:
                await _run_task_with_retry()
            except get_cancelled_exc_class():
                raise
            except Exception as e:
                await runner.mark_failed()
                _logger.error(
                    f"Download task {task_id} is marked as Failed after retry exhaustion: {str(e)}"
                )

        finally:
            # When task is finished, remove it from the task list
            del self._download_runners[task_id]

    async def _recover_download_task(self, tg: TaskGroup):
        running_status = [DownloadTaskStatus.Started, DownloadTaskStatus.Executed]
        running_states = await self._download_state_cache.find(status=running_status)
        for state in running_states:
            runner = DownloadTaskRunner(
                task_id=state.task_id,
                state=state,
                state_cache=self._download_state_cache,
                contracts=self._contracts,
                relay=self._relay,
            )
            self._download_runners[state.task_id] = runner
            tg.start_soon(self._run_download_task, state.task_id)
            _logger.debug(f"Rerun download task {state.task_id}")

    # Create download task with the given task_id
    async def create_download_task(
        self, task_id: str, task_type: TaskType, model_id: str
    ):
        if task_id not in self._download_runners:
            if await self._download_state_cache.has(task_id):
                old_state = await self._download_state_cache.load(task_id)
                if old_state.status == DownloadTaskStatus.Failed:
                    old_state.status = DownloadTaskStatus.Started
                    await self._download_state_cache.dump(old_state)
            state = DownloadTaskState(
                task_id=task_id,
                task_type=task_type,
                model_id=model_id,
                status=DownloadTaskStatus.Started,
            )
            runner = DownloadTaskRunner(
                task_id=task_id,
                state=state,
                state_cache=self._download_state_cache,
                contracts=self._contracts,
                relay=self._relay,
            )
            self._download_runners[task_id] = runner
            await self._task_queue.put(("download", task_id))

    async def start(self):
        @retry(
            stop=stop_never if self._retry else stop_after_attempt(1),
            wait=wait_fixed(30),
            reraise=True,
        )
        async def _start():
            assert self._tg is None, "The TaskSystem has already been started."

            try:
                async with create_task_group() as tg:
                    self._tg = tg
                    tg.start_soon(self._reconciler.run)
                    await self._recover_download_task(tg)
                    while True:
                        task_name, task_id = await self._task_queue.get()
                        if task_name == "download":
                            assert isinstance(task_id, str)
                            tg.start_soon(self._run_download_task, task_id)

            except get_cancelled_exc_class():
                raise
            except Exception as e:
                _logger.error(f"Some error occurs when running task system, retrying")
                _logger.exception(e)
                raise
            finally:
                self._tg = None

        await _start()

    def stop(self):
        if self._tg is not None and not self._tg.cancel_scope.cancel_called:
            self._tg.cancel_scope.cancel()


_default_task_system: Optional[TaskSystem] = None


def get_task_system() -> TaskSystem:
    assert _default_task_system is not None, "TaskSystem has not been set."

    return _default_task_system


def set_task_system(task_system: TaskSystem):
    global _default_task_system

    _default_task_system = task_system
