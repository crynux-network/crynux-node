import json
import logging
import os.path
import random
import re
import shutil
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Awaitable, Callable, List, Optional

from anyio import (create_memory_object_stream, create_task_group, fail_after,
                   get_cancelled_exc_class, move_on_after, sleep, to_thread)
from anyio.streams.memory import (MemoryObjectReceiveStream,
                                  MemoryObjectSendStream)
from hexbytes import HexBytes
from tenacity import retry, stop_after_delay, wait_chain, wait_fixed
from web3 import Web3

from crynux_server import models
from crynux_server.config import Config, get_config
from crynux_server.contracts import Contracts, get_contracts
from crynux_server.download_model_cache import (DownloadModelCache,
                                                get_download_model_cache)
from crynux_server.relay import Relay, get_relay
from crynux_server.relay.exceptions import RelayError
from crynux_server.worker_manager import TaskExecutionError, TaskInvalid

from .state_cache import (DownloadTaskStateCache, InferenceTaskStateCache,
                          get_download_task_state_cache,
                          get_inference_task_state_cache)
from .utils import run_download_task, run_inference_task, validate_score

_logger = logging.getLogger(__name__)


OkCallback = Callable[[bool], Awaitable[None]]
ErrCallback = Callable[[Exception], Awaitable[None]]


# Manage the lifestyle of one task
class InferenceTaskRunnerBase(ABC):
    @abstractmethod
    def __init__(
        self,
        task_id_commitment: bytes,
        state_cache: Optional[InferenceTaskStateCache] = None,
        contracts: Optional[Contracts] = None,
    ):
        self.task_id_commitment = HexBytes(task_id_commitment)
        if state_cache is None:
            state_cache = get_inference_task_state_cache()
        self.cache = state_cache
        if contracts is None:
            contracts = get_contracts()
        self.contracts = contracts

        self._state: Optional[models.InferenceTaskState] = None

    @property
    def state(self) -> models.InferenceTaskState:
        assert self._state is not None, "The task runner's state has not been set."
        return self._state

    @state.setter
    def state(self, state: models.InferenceTaskState):
        assert self._state is None, "The task runner's state has already been set."
        self._state = state

    @state.deleter
    def state(self):
        assert self._state is not None, "The task runner's state has not been set."
        self._state = None

    @asynccontextmanager
    async def state_context(self):
        try:
            yield
        finally:
            with fail_after(10, shield=True):
                await self.cache.dump(task_state=self.state)

    async def sync_state(self):
        need_dump = False
        try:
            # If task state is not set, load it from cache
            # If task does not exist in cache, create a new state
            if self._state is None:
                if await self.cache.has(self.task_id_commitment):
                    state = await self.cache.load(self.task_id_commitment)
                    self.state = state
                else:
                    state = models.InferenceTaskState(
                        task_id_commitment=self.task_id_commitment,
                        timeout=0,
                        status=models.InferenceTaskStatus.Queued,
                        task_type=models.TaskType.SD,
                    )
                    self.state = state
                    need_dump = True

            # Get task info and update local record
            task = await self.get_task()
            start_timestamp = 0
            if task.start_time is not None:
                start_timestamp = int(task.start_time.timestamp())
            if start_timestamp == 0:
                start_timestamp = int(time.time())
            timeout = start_timestamp + task.timeout
            if self.state.timeout != timeout:
                self.state.timeout = timeout
                _logger.info(
                    f"task {self.task_id_commitment.hex()} timeout: {self.state.timeout}"
                )
                need_dump = True
            if self.state.status != task.status:
                self.state.status = task.status
                need_dump = True
            if self.state.task_type != task.task_type:
                self.state.task_type = task.task_type
                need_dump = True
        finally:
            if self._state is not None and need_dump:
                await self.cache.dump(self.state)

    @abstractmethod
    async def cleanup(self): ...

    @abstractmethod
    async def get_task(self) -> models.RelayTask: ...

    @abstractmethod
    async def cancel_task(self): ...

    @abstractmethod
    async def execute_task(self): ...

    @abstractmethod
    async def upload_result(self): ...

    # Task in these status is already finished, therefore should stop the worker's process
    def should_stop(self):
        return self.state.status in [
            models.InferenceTaskStatus.EndAborted,
            models.InferenceTaskStatus.EndGroupRefund,
            models.InferenceTaskStatus.EndGroupSuccess,
            models.InferenceTaskStatus.EndSuccess,
            models.InferenceTaskStatus.ErrorReported,
        ]

    # Receive task status from task_status_producer
    # If task is started and not executed, execute it
    # If task is validated or group validated, upload result
    # Ignore task in other status
    async def task_status_consumer(
        self, status_receiver: MemoryObjectReceiveStream[models.InferenceTaskStatus]
    ):
        executed = False
        async with status_receiver:
            async for status in status_receiver:
                _logger.info(
                    f"task {self.task_id_commitment.hex()} status: {status.name}"
                )
                if (
                    status == models.InferenceTaskStatus.Started
                    or status == models.InferenceTaskStatus.ParametersUploaded
                ) and not executed:
                    await self.execute_task()
                    executed = True
                elif (
                    status == models.InferenceTaskStatus.Validated
                    or status == models.InferenceTaskStatus.GroupValidated
                    or status == models.InferenceTaskStatus.EndInvalidated
                ):
                    await self.upload_result()

    # Send task status when it changes
    # task_status_consumer will receive and handle the status
    async def task_status_producer(
        self,
        status_sender: MemoryObjectSendStream[models.InferenceTaskStatus],
        interval: float,
    ):
        async with status_sender:
            await status_sender.send(self.state.status)
            while not self.should_stop():
                last_status = self.state.status
                await self.sync_state()
                if last_status != self.state.status:
                    await status_sender.send(self.state.status)
                await sleep(interval)

    async def run(self, interval: float = 1):
        try:
            await self.sync_state()
            if self.should_stop():
                return
            delay = self.state.timeout - time.time() + 5
            if delay <= 0:
                raise TimeoutError

            # Prepare task status stream
            status_sender, status_receiver = create_memory_object_stream(
                10, item_type=models.InferenceTaskStatus
            )
            with fail_after(delay, shield=False):
                async with create_task_group() as tg:
                    tg.start_soon(self.task_status_consumer, status_receiver)
                    tg.start_soon(self.task_status_producer, status_sender, interval)
        except TimeoutError:
            # cancel task
            if not self.should_stop():
                await self.cancel_task()
                async with self.state_context():
                    self.state.status = models.InferenceTaskStatus.EndAborted
        finally:
            if self.should_stop():
                with move_on_after(5, shield=True):
                    await self.cleanup()


class InferenceTaskRunner(InferenceTaskRunnerBase):
    def __init__(
        self,
        task_id_commitment: bytes,
        state_cache: Optional[InferenceTaskStateCache] = None,
        contracts: Optional[Contracts] = None,
        relay: Optional[Relay] = None,
        config: Optional[Config] = None,
    ) -> None:
        super().__init__(
            task_id_commitment=task_id_commitment,
            state_cache=state_cache,
            contracts=contracts,
        )
        if relay is None:
            self.relay = get_relay()
        else:
            self.relay = relay
        if config is None:
            config = get_config()
        self.config = config

        self._cleaned = False

    # Report task error(ParametersValidationFailed)
    async def _report_error(self):
        async with self.state_context():
            self.state.status = models.InferenceTaskStatus.ErrorReported

        try:
            await self.relay.report_task_error(
                task_id_commitment=self.task_id_commitment,
                task_error=models.TaskError.ParametersValidationFailed,
            )
            _logger.info(
                f"Task {self.task_id_commitment.hex()} error. Report the task error."
            )
        except RelayError as e:
            _logger.error(
                f"Report error of task {self.task_id_commitment.hex()} failed due to {e.message}"
            )

    # Get task info
    async def get_task(self):
        try:
            task = await self.relay.get_task(self.task_id_commitment)
        except RelayError as e:
            _logger.error(
                f"Get task {self.task_id_commitment.hex()} failed due to {e.message}"
            )
            raise ValueError("Task not found")
        # task not exist
        if task.task_id_commitment != self.task_id_commitment:
            _logger.error(
                f"local task id commitment: {self.task_id_commitment.hex()}, remote task id commitment: {task.task_id_commitment.hex()}"
            )
            raise ValueError("Task not found")
        return task

    async def cancel_task(self):
        try:
            await self.relay.abort_task(
                task_id_commitment=self.task_id_commitment,
                abort_reason=models.TaskAbortReason.Timeout,
            )
            _logger.info(
                f"Task {self.task_id_commitment.hex()} timeout. Cancel the task."
            )
        except RelayError as e:
            _logger.error(
                f"Cancel task {self.task_id_commitment.hex()} failed due to {e.message}"
            )
            raise
        except get_cancelled_exc_class():
            raise
        except Exception as e:
            _logger.debug(f"Cancel task {self.task_id_commitment.hex()} failed")
            raise

    async def execute_task(self):
        @retry(
            stop=stop_after_delay(180),
            wait=wait_chain(*[wait_fixed(1) for _ in range(10)] + [wait_fixed(5)]),
            reraise=True,
        )
        async def get_task():
            task = await self.relay.get_task(self.task_id_commitment)
            _logger.debug(f"get task {self.task_id_commitment.hex()} from relay")
            return task

        @retry(
            stop=stop_after_delay(180),
            wait=wait_chain(*[wait_fixed(1) for _ in range(10)] + [wait_fixed(5)]),
            reraise=True,
        )
        async def get_checkpoint(checkpoint_dir: str):
            await self.relay.get_checkpoint(self.task_id_commitment, checkpoint_dir)
            _logger.debug(f"get task {self.task_id_commitment.hex()} from relay")

        async def execute_task_in_worker():
            task_dir = os.path.join(
                self.config.task_config.output_dir, self.task_id_commitment.hex()
            )
            task = await get_task()

            if self.state.task_type == models.TaskType.SD_FT_LORA:
                args = json.loads(task.task_args)
                checkpoint = args.get("checkpoint", None)
                if checkpoint is not None:
                    checkpoint_dir = os.path.join(task_dir, "input_checkpoint")
                    await get_checkpoint(checkpoint_dir)
                    args["checkpoint"] = checkpoint_dir
                    task.task_args = json.dumps(args)

            _logger.info(
                f"task id: {self.task_id_commitment.hex()},"
                f"task type: {self.state.task_type.name},"
                f"task_args: {task.task_args},"
            )
            _logger.info(f"Start executing task {self.task_id_commitment.hex()}")
            if not os.path.exists(task_dir):
                os.makedirs(task_dir, exist_ok=True)
            try:
                task_models = [
                    models.ModelConfig.from_model_id(model_id)
                    for model_id in task.model_ids
                ]

                files, hashes, checkpoint = await run_inference_task(
                    task_id_commitment=self.task_id_commitment,
                    task_type=self.state.task_type,
                    models=task_models,
                    task_args=task.task_args,
                    task_dir=task_dir,
                )
                _logger.info(f"Task {self.task_id_commitment.hex()} execution success")
                score = b"".join(hashes)
                if not validate_score(score):
                    raise TaskExecutionError(
                        f"Task {self.task_id_commitment.hex()} score {score.hex()} is invalid"
                    )
                async with self.state_context():
                    self.state.files = files
                    self.state.score = score
                    self.state.checkpoint = checkpoint
            except TaskInvalid as e:
                # If the task is invalid, report the error
                _logger.exception(e)
                _logger.error(
                    f"Task {self.task_id_commitment.hex()} error, report error."
                )
                with fail_after(delay=60, shield=True):
                    await self._report_error()

        def _illegal_task_state(exc: BaseException):
            return re.search(r"Illegal previous task state", str(exc)) is not None

        # Submit task score for validation
        async def submit_task_score():
            if not validate_score(self.state.score):
                async with self.state_context():
                    self.state.files = []
                    self.state.score = b""
                    self.state.checkpoint = None

                raise TaskExecutionError(
                    f"Task {self.task_id_commitment.hex()} score {self.state.score.hex()} is invalid"
                )
            for _ in range(4):
                try:
                    await self.relay.submit_task_score(
                        task_id_commitment=self.task_id_commitment,
                        score=self.state.score,
                    )
                    _logger.info("Submiting task score success")
                    return
                except Exception as e:
                    if _illegal_task_state(e):
                        if self.state.status == models.InferenceTaskStatus.Started:
                            await sleep(2)
                            continue
                        elif (
                            self.state.status
                            == models.InferenceTaskStatus.ParametersUploaded
                        ):
                            continue
                    raise e

        _logger.debug(f"task {self.task_id_commitment} state: {self.state}")

        if len(self.state.files) == 0 or not validate_score(self.state.score):
            await execute_task_in_worker()

        await submit_task_score()

    # Upload full task result to relay
    async def upload_result(self) -> None:
        _logger.info(f"Task {self.task_id_commitment.hex()} start uploading results")
        await self.relay.upload_task_result(
            self.task_id_commitment, self.state.files, self.state.checkpoint
        )
        _logger.info(f"Task {self.task_id_commitment.hex()} success")

    # Clean up task files when task is finished
    async def cleanup(self):
        if not self._cleaned:

            def delete_result_files(files: List[str]) -> None:
                if len(files) > 0:
                    dirname = os.path.dirname(files[0])
                    if os.path.exists(dirname):
                        shutil.rmtree(dirname)

            if self.state.status != models.InferenceTaskStatus.EndInvalidated:
                with fail_after(10, shield=True):
                    await to_thread.run_sync(delete_result_files, self.state.files)

            del self.state
            self._cleaned = True


# Mock InferenceTaskRunner for testing
class MockInferenceTaskRunner(InferenceTaskRunnerBase):
    def __init__(
        self,
        task_id_commitment: bytes,
        state_cache: Optional[InferenceTaskStateCache] = None,
        contracts: Optional[Contracts] = None,
        timeout: int = 900,
    ):
        super().__init__(
            task_id_commitment=task_id_commitment,
            state_cache=state_cache,
            contracts=contracts,
        )

        self._timeout = timeout

    async def get_task(self):
        return models.RelayTask(
            sequence=1,
            task_id_commitment=self.task_id_commitment,
            creator=Web3.to_checksum_address("0x00000000000000000000"),
            sampling_seed=bytes([0] * 32),
            nonce=bytes([0] * 32),
            task_args="",
            status=models.InferenceTaskStatus.Started,
            task_type=models.TaskType.SD,
            task_version="2.5.0",
            timeout=300,
            min_vram=4,
            required_gpu="",
            required_gpu_vram=0,
            task_fee=Web3.to_wei(1, "wei"),
            task_size=1,
            model_ids=[""],
            score="",
            qos_score=1,
            selected_node=Web3.to_checksum_address("0x00000000000000000000"),
            create_time=datetime.now(),
            start_time=datetime.now(),
            score_ready_time=datetime.now(),
            validated_time=datetime.now(),
            result_uploaded_time=datetime.now(),
        )

    async def cancel_task(self):
        pass

    async def execute_task(self):
        async with self.state_context():
            self.state.files = [""]
            self.state.score = random.randbytes(4)

    async def upload_result(self):
        pass

    async def cleanup(self):
        del self.state


class DownloadTaskRunner(object):
    def __init__(
        self,
        task_id: str,
        state: models.DownloadTaskState,
        state_cache: Optional[DownloadTaskStateCache] = None,
        contracts: Optional[Contracts] = None,
        relay: Optional[Relay] = None,
        download_model_cache: Optional[DownloadModelCache] = None,
    ):
        self.task_id = task_id
        if state_cache is None:
            state_cache = get_download_task_state_cache()
        self.state_cache = state_cache
        if contracts is None:
            contracts = get_contracts()
        self.contracts = contracts
        if relay is None:
            relay = get_relay()
        self.relay = relay
        if download_model_cache is None:
            download_model_cache = get_download_model_cache()
        self.download_model_cache = download_model_cache

        self._state: models.DownloadTaskState = state

    @asynccontextmanager
    async def state_context(self):
        try:
            yield
        finally:
            with fail_after(10, shield=True):
                await self.state_cache.dump(task_state=self._state)

    async def run(self):
        if await self.state_cache.has(self.task_id):
            self._state = await self.state_cache.load(self.task_id)
        else:
            await self.state_cache.dump(self._state)

        if self._state.status == models.DownloadTaskStatus.Success:
            return

        model = models.ModelConfig.from_model_id(self._state.model_id)
        if self._state.status == models.DownloadTaskStatus.Started:
            _logger.info(f"start downloading model {self._state.model_id}")
            await run_download_task(
                task_id=self.task_id, task_type=self._state.task_type, model=model
            )
            async with self.state_context():
                self._state.status = models.DownloadTaskStatus.Executed
            _logger.info(f"Download model {self._state.model_id} successfully")

        if self._state.status == models.DownloadTaskStatus.Executed:
            await self.relay.node_report_model_downloaded(self._state.model_id)
            _logger.info(f"report model {self._state.model_id} is downloaded")
            async with self.state_context():
                self._state.status = models.DownloadTaskStatus.Success

            await self.download_model_cache.save(
                models.DownloadedModel(task_type=self._state.task_type, model=model)
            )

    async def mark_failed(self):
        async with self.state_context():
            self._state.status = models.DownloadTaskStatus.Failed
