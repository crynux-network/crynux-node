import asyncio
import json
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager, contextmanager, suppress
from typing import Dict, Literal, Optional, Set

import psutil
from anyio import Condition, Lock, fail_after, sleep

from crynux_server import utils
from crynux_server.config import Config, get_config, load_env_file
from crynux_server.models import TaskInput, TaskResult
from crynux_server.utils import get_selected_gpu_device_uuids

from .error import (
    TaskCancellationType,
    TaskDownloadError,
    TaskExecutionError,
    TaskInvalid,
    is_model_not_downloaded,
    is_task_invalid,
)
from .exchange import TaskExchange
from .task import TaskFuture
from .utils import get_exe_head

_logger = logging.getLogger(__name__)

WATCHDOG_CHECK_INTERVAL = 1

WorkerRole = Literal["inference", "download"]


def _derive_role_pid_file(worker_pid_file: str, role: WorkerRole) -> str:
    if role == "inference":
        return worker_pid_file
    base, ext = os.path.splitext(worker_pid_file)
    return f"{base}_download{ext}"


# Manages one worker process of a single role.
#
# The inference manager restarts on execution errors and missed deadlines.
# The download manager uses deadlines only for foreground task-scoped
# downloads; background model downloads remain unbounded.
class WorkerManager(object):
    def __init__(
        self, role: WorkerRole = "inference", config: Optional[Config] = None
    ) -> None:
        if config is None:
            config = get_config()
        self.config = config
        self.role: WorkerRole = role

        self._exchange = TaskExchange()

        self._next_worker_id = 1
        self._task_futures: Dict[str, TaskFuture] = {}
        self._current_worker_id = 0

        # Watchdog entries: task id -> absolute execution deadline (unix time).
        # An entry is cleared only by a worker-reported result or a restart.
        self._task_deadlines: Dict[str, float] = {}
        self._watchdog_task: Optional[asyncio.Task] = None
        self._restart_tasks: Set[asyncio.Task] = set()

        self._worker_process: Optional[subprocess.Popen] = None
        self._worker_pid_file: Optional[str] = None

        self._version: Optional[str] = None

        self._connect_condition = Condition()
        self._restart_lock = Lock()

    @property
    def version(self):
        return self._version

    def _kill_process_tree(self, pid: int):
        try:
            process = psutil.Process(pid)
            processes = process.children(recursive=True)
            for proc in processes:
                with suppress(psutil.NoSuchProcess):
                    proc.kill()
            process.kill()
            psutil.wait_procs(processes + [process], timeout=10)
        except psutil.NoSuchProcess:
            pass

    def _remove_worker_pid_file(self, worker_pid_file: str):
        with suppress(FileNotFoundError):
            os.remove(worker_pid_file)

    def _clear_old_worker_process(self, worker_pid_file: str):
        if not os.path.exists(worker_pid_file):
            return

        with open(worker_pid_file, mode="r", encoding="utf-8") as f:
            pid = int(f.read().strip())

        try:
            process = psutil.Process(pid)
            cmdline = process.cmdline()
        except psutil.NoSuchProcess:
            self._remove_worker_pid_file(worker_pid_file)
            return

        if "crynux_worker_process" in " ".join(cmdline):
            self._kill_process_tree(pid)
            self._remove_worker_pid_file(worker_pid_file)

    def _get_worker_process_args(self):
        if self.config.task_config is not None:
            script_dir = self.config.task_config.script_dir
            patch_url = self.config.task_config.worker_patch_url
            hf_cache_dir = self.config.task_config.hf_cache_dir
            external_cache_dir = self.config.task_config.external_cache_dir
            output_dir = self.config.task_config.output_dir
            worker_pid_file = self.config.task_config.worker_pid_file

            for dirname in [
                script_dir,
                hf_cache_dir,
                external_cache_dir,
                output_dir,
                os.path.dirname(worker_pid_file),
            ]:
                os.makedirs(dirname, exist_ok=True)
        else:
            script_dir = ""
            patch_url = ""
            hf_cache_dir = ""
            external_cache_dir = ""
            output_dir = ""
            worker_pid_file = "crynux_worker.pid"

        worker_pid_file = _derive_role_pid_file(worker_pid_file, self.role)

        cw_worker_url = f"{self.config.relay_url}/v1/worker"
        args = get_exe_head(script_dir)
        envs = os.environ.copy()
        envs.update(
            {
                "CRYNUX_WORKER_PATCH_URL": patch_url,
                "cw_worker_role": self.role,
                "cw_data_dir__models__huggingface": hf_cache_dir,
                "cw_data_dir__models__external": external_cache_dir,
                "cw_output_dir": output_dir,
                "cw_pid_file": worker_pid_file,
                "cw_worker_url": cw_worker_url,
            }
        )
        if (
            self.config.task_config is not None
            and self.config.task_config.preloaded_models is not None
        ):
            envs["cw_preloaded_models"] = (
                self.config.task_config.preloaded_models.model_dump_json()
            )
        if (
            self.config.task_config is not None
            and self.config.task_config.proxy is not None
        ):
            envs["cw_proxy"] = self.config.task_config.proxy.model_dump_json()

        node_url = f"ws://127.0.0.1:{self.config.server_port}/manager/v1/worker/"
        envs["cw_node_url"] = node_url

        log_filename = (
            "crynux-worker.log"
            if self.role == "inference"
            else "crynux-worker-download.log"
        )
        log_config = {
            "dir": self.config.log.dir,
            "level": self.config.log.level,
            "filename": log_filename,
        }
        envs["cw_log"] = json.dumps(log_config)

        # Pin the worker to the selected identical-model GPU group so the set
        # of cards the worker executes on matches the GPU info reported to the
        # relay. UUIDs are used because nvidia-smi (PCI bus order) and CUDA
        # (fastest-first order) may number devices differently.
        try:
            device_uuids = get_selected_gpu_device_uuids()
        except (OSError, subprocess.CalledProcessError) as e:
            _logger.warning(
                "Failed to enumerate GPUs for worker device pinning, "
                "the worker will see all GPUs: %s",
                e,
            )
            device_uuids = []
        if len(device_uuids) > 0:
            envs["CUDA_VISIBLE_DEVICES"] = ",".join(device_uuids)

        worker_envs = load_env_file("WORKER_")
        envs.update(worker_envs)
        if len(worker_envs) > 0:
            _logger.info(
                "Applied worker environment variables from config .env: %s",
                ", ".join(sorted(worker_envs.keys())),
            )

        # The node is the single decision point for the GPT executor mode:
        # GPT_EXECUTOR is injected only when tensor parallelism is effective
        # and force-removed otherwise, regardless of the .env contents. The
        # worker obeys the variable without re-checking the platform.
        executor = utils.resolve_gpt_executor(utils.get_platform(), len(device_uuids))
        if executor == utils.GPT_EXECUTOR_TENSOR_PARALLEL:
            envs["GPT_EXECUTOR"] = utils.GPT_EXECUTOR_TENSOR_PARALLEL
        else:
            envs.pop("GPT_EXECUTOR", None)
        _logger.info("Effective GPT executor mode: %s", executor)

        return args, envs, worker_pid_file

    def _start_worker_process(self):
        args, envs, worker_pid_file = self._get_worker_process_args()
        self._clear_old_worker_process(worker_pid_file)

        p = subprocess.Popen(args=args, env=envs)
        self._worker_process = p
        self._worker_pid_file = worker_pid_file

        if p.poll() is not None:
            raise RuntimeError(
                f"Worker process failed to start. Exit code: {p.returncode}"
            )

    def _stop_worker_process(self):
        if self._worker_process is not None:
            self._kill_process_tree(self._worker_process.pid)
            if self._worker_pid_file is not None:
                self._remove_worker_pid_file(self._worker_pid_file)
            self._worker_process = None
            self._worker_pid_file = None

    @contextmanager
    def start(self):
        self._start_worker_process()
        try:
            yield
        finally:
            if self._watchdog_task is not None:
                self._watchdog_task.cancel()
                self._watchdog_task = None
            self._cancel_all_tasks(
                TaskCancellationType.PROCESS_SHUTDOWN,
                "worker_manager.start",
                "the Node process is shutting down",
            )
            self._stop_worker_process()

    def _cancel_all_tasks(
        self,
        cancellation_type: TaskCancellationType,
        initiated_by: str,
        reason: str,
        timeout_task_ids: Optional[Set[str]] = None,
    ):
        timeout_task_ids = timeout_task_ids or set()
        for task_id, task_result in self._task_futures.items():
            if task_result.done():
                continue
            task_result.cancel(
                (
                    TaskCancellationType.WORKER_TASK_TIMEOUT
                    if task_id in timeout_task_ids
                    else cancellation_type
                ),
                initiated_by,
                reason,
            )

    async def _clear_worker_connection(
        self,
        cancellation_type: TaskCancellationType,
        initiated_by: str,
        reason: str,
        timeout_task_ids: Optional[Set[str]] = None,
    ):
        await self._exchange.clear()
        self._cancel_all_tasks(
            cancellation_type, initiated_by, reason, timeout_task_ids
        )
        self._task_futures.clear()
        self._task_deadlines.clear()

        async with self._connect_condition:
            self._current_worker_id = 0
            self._version = None
            self._connect_condition.notify_all()

    async def restart(
        self,
        reason: Optional[str] = None,
        cancellation_type: TaskCancellationType = TaskCancellationType.WORKER_RESTARTED,
        timeout_task_ids: Optional[Set[str]] = None,
    ):
        async with self._restart_lock:
            if reason is None:
                _logger.warning("Restarting %s worker process", self.role)
            else:
                _logger.warning("Restarting %s worker process: %s", self.role, reason)

            self._stop_worker_process()
            await self._clear_worker_connection(
                cancellation_type,
                "worker_manager.restart",
                reason or f"{self.role} worker process restarted",
                timeout_task_ids,
            )
            self._start_worker_process()
            _logger.info("Worker process restarted")

    def is_worker_process_alive(self) -> bool:
        """
        Check if the worker process is still alive.
        Returns True if process is running, False otherwise.
        """
        if self._worker_process is None:
            return False
        return self._worker_process.poll() is None

    def get_worker_process_exit_code(self) -> Optional[int]:
        """
        Get the exit code of the worker process.
        Returns None if process is still running, otherwise returns the exit code.
        """
        if self._worker_process is None:
            return None
        return self._worker_process.poll()

    async def connect(self, version: str) -> int:
        worker_id = self._next_worker_id
        self._next_worker_id += 1
        async with self._connect_condition:
            self._current_worker_id = worker_id
            self._version = version
            self._connect_condition.notify_all()
        return worker_id

    async def disconnect(self, worker_id: int):
        if worker_id != self._current_worker_id:
            _logger.info("Ignore stale worker %s disconnect", worker_id)
            return
        await self._clear_worker_connection(
            TaskCancellationType.WORKER_DISCONNECTED,
            "worker_websocket",
            f"{self.role} worker {worker_id} disconnected before returning a result",
        )

    async def is_connected(self) -> bool:
        return self._current_worker_id > 0

    @asynccontextmanager
    async def wait_connected(self, timeout: Optional[float] = None):
        with fail_after(timeout):
            async with self._connect_condition:
                while self._current_worker_id == 0:
                    await self._connect_condition.wait()
                yield

    @asynccontextmanager
    async def wait_connection_changed(self):
        async with self._connect_condition:
            await self._connect_condition.wait()
            yield

    def _spawn_restart(self, reason: str):
        # The restart runs as an independent asyncio task so it survives the
        # cancellation of its trigger (e.g. the worker websocket handler dying
        # when the worker process is killed)
        task = asyncio.get_running_loop().create_task(self.restart(reason=reason))
        self._restart_tasks.add(task)
        task.add_done_callback(self._restart_tasks.discard)

    async def _watchdog(self):
        while True:
            await sleep(WATCHDOG_CHECK_INTERVAL)
            now = time.time()
            expired = [
                task_id
                for task_id, deadline in self._task_deadlines.items()
                if deadline <= now
            ]
            if len(expired) > 0:
                await self.restart(
                    reason=(
                        f"tasks {expired} produced no worker-reported result "
                        "by their execution deadline"
                    ),
                    timeout_task_ids=set(expired),
                )

    def _ensure_watchdog_running(self):
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.get_running_loop().create_task(
                self._watchdog()
            )

    async def send_task(self, input: TaskInput, deadline: Optional[float] = None):
        task_id = input.task.task_id
        task_result = TaskFuture(task_id, self.role, deadline)
        self._task_futures[task_id] = task_result
        if deadline is not None:
            self._task_deadlines[task_id] = deadline
            self._ensure_watchdog_running()
        return await self._exchange.send_task(input, task_result)

    async def get_task(self, worker_id: int):
        await sleep(0)
        assert worker_id == self._current_worker_id, (
            f"Worker {worker_id} is disconnected"
        )
        task_input, task_future = await self._exchange.get_task()
        task_future.mark_dispatched(worker_id)

        return task_input, task_future

    def mark_task_sent(self, task_id: str, worker_id: int):
        task_future = self._task_futures.get(task_id)
        if task_future is not None and task_future.worker_id == worker_id:
            task_future.mark_sent()

    async def report_task_result(self, worker_id: int, result: TaskResult):
        assert worker_id == self._current_worker_id, (
            f"Worker {worker_id} is disconnected"
        )
        task_id_commitment = result.task_id_commitment
        assert task_id_commitment in self._task_futures, (
            f"No such task future {task_id_commitment}"
        )

        # A worker-reported result is the only completion that clears
        # the watchdog entry besides a restart
        self._task_deadlines.pop(task_id_commitment, None)

        fut = self._task_futures[task_id_commitment]
        try:
            if fut.cancelled():
                _logger.info(f"Task {task_id_commitment} has been cancelled before")
            elif fut.done():
                _logger.info(f"Task {task_id_commitment} has been done before")
            else:
                if result.result.status == "success":
                    fut.set_result(None)
                elif result.result.status == "error":
                    err_msg = result.result.traceback
                    if result.task_name == "inference":
                        if is_task_invalid(err_msg):
                            fut.set_error(TaskInvalid(err_msg))
                        else:
                            fut.set_error(TaskExecutionError(err_msg))
                    elif result.task_name == "download":
                        fut.set_error(TaskDownloadError(err_msg))
        finally:
            if fut.done():
                del self._task_futures[task_id_commitment]

        # Restart policy applies to the inference worker only. A model
        # not downloaded failure does not indicate an unhealthy worker, so
        # it never triggers a restart
        if (
            self.role == "inference"
            and result.task_name == "inference"
            and result.result.status == "error"
            and not is_task_invalid(result.result.traceback)
            and not is_model_not_downloaded(result.result.traceback)
        ):
            self._spawn_restart(
                reason=f"task {task_id_commitment} failed with an execution error"
            )


_default_worker_managers: Dict[WorkerRole, WorkerManager] = {}


def get_worker_manager(role: WorkerRole = "inference"):
    assert role in _default_worker_managers

    return _default_worker_managers[role]


def set_worker_manager(worker_manager: WorkerManager):
    _default_worker_managers[worker_manager.role] = worker_manager
