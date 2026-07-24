import json
import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from tempfile import mkdtemp
from typing import BinaryIO, Dict, List, Optional

from anyio import Condition, get_cancelled_exc_class, to_thread
from web3 import Web3

from crynux_server.models import InferenceTaskStatus, RelayTask, TaskType

from .abc import Relay
from .exceptions import RelayError


class MockRelay(Relay):
    def __init__(self) -> None:
        super().__init__()

        self.tasks: Dict[bytes, RelayTask] = {}

        self.task_input_checkpoint: Dict[bytes, str] = {}

        self.task_results: Dict[bytes, List[str]] = {}
        self.task_result_checkpoint: Dict[bytes, str] = {}
        self.task_diagnostics = []

        self._conditions: Dict[bytes, Condition] = {}

        self._tempdir = mkdtemp()

        self._closed = False

    def get_condition(self, task_id_commitment: bytes) -> Condition:
        if task_id_commitment not in self._conditions:
            self._conditions[task_id_commitment] = Condition()
        return self._conditions[task_id_commitment]

    @contextmanager
    def wrap_error(self, method: str):
        try:
            yield
        except KeyboardInterrupt:
            raise
        except get_cancelled_exc_class():
            raise
        except Exception as e:
            raise RelayError(status_code=500, method=method, message=str(e))

    async def create_task(
        self,
        task_id_commitment: bytes,
        task_args: str,
        checkpoint_dir: Optional[str] = None,
    ) -> RelayTask:
        with self.wrap_error("createTask"):
            task_args_dict = json.loads(task_args)
            model_id = ""
            if "base_model" in task_args_dict:
                if isinstance(task_args_dict["base_model"], str):
                    model_name = task_args_dict["base_model"]
                    model_id = f"base:{model_name}"
                else:
                    model_name = task_args_dict["base_model"]["id"]
                    variant = task_args_dict["base_model"]["variant"]
                    model_id = f"base:{model_name}"
                    if variant:
                        model_id += f"+{variant}"
            elif "model" in task_args_dict:
                if isinstance(task_args_dict["model"], str):
                    model_name = task_args_dict["model"]
                    model_id = f"base:{model_name}"
                else:
                    model_name = task_args_dict["model"]["id"]
                    variant = task_args_dict["model"]["variant"]
                    model_id = f"base:{model_name}"
                    if variant:
                        model_id += f"+{variant}"

            t = RelayTask(
                sequence=1,
                task_id_commitment=task_id_commitment,
                creator=Web3.to_checksum_address("0x00000000000000000000"),
                sampling_seed=bytes([0] * 32),
                nonce=bytes([0] * 32),
                task_args=task_args,
                status=InferenceTaskStatus.Started,
                task_type=TaskType.SD,
                task_version="2.5.0",
                timeout=300,
                min_vram=4,
                required_gpu="",
                required_gpu_vram=0,
                task_fee=Web3.to_wei(1, "wei"),
                task_size=1,
                model_ids=[model_id],
                score="",
                qos_score=1,
                selected_node=Web3.to_checksum_address("0x00000000000000000000"),
                create_time=datetime.now(),
                start_time=datetime.now(),
                score_ready_time=datetime.now(),
                validated_time=datetime.now(),
                result_uploaded_time=datetime.now(),
            )
            self.tasks[task_id_commitment] = t
            if checkpoint_dir is not None:
                condition = self.get_condition(task_id_commitment)
                async with condition:
                    task_dir = os.path.join(self._tempdir, task_id_commitment.hex())
                    if not os.path.exists(task_dir):
                        os.makedirs(task_dir, exist_ok=True)

                    dst_path = os.path.join(task_dir, "input_checkpoint")
                    await to_thread.run_sync(shutil.copytree, checkpoint_dir, dst_path)
                    self.task_input_checkpoint[task_id_commitment] = dst_path
                    condition.notify()

            return t

    async def get_checkpoint(
        self, task_id_commitment: bytes, result_checkpoint_dir: str
    ):
        with self.wrap_error("getCheckpoint"):
            condition = self.get_condition(task_id_commitment)
            async with condition:
                while task_id_commitment not in self.task_input_checkpoint:
                    await condition.wait()

                src_path = self.task_input_checkpoint[task_id_commitment]
                await to_thread.run_sync(
                    shutil.copytree, src_path, result_checkpoint_dir
                )

    async def get_task(self, task_id_commitment: bytes) -> RelayTask:
        with self.wrap_error("getTask"):
            return self.tasks[task_id_commitment]

    async def report_task_diagnostic(self, report):
        self.task_diagnostics.append(report)

    async def upload_task_result(
        self,
        task_id_commitment: bytes,
        file_paths: List[str],
        checkpoint_dir: Optional[str] = None,
    ):
        with self.wrap_error("uploadTaskResult"):
            condition = self.get_condition(task_id_commitment)
            async with condition:
                self.task_results[task_id_commitment] = []

                task_dir = os.path.join(self._tempdir, task_id_commitment.hex())
                if not os.path.exists(task_dir):
                    os.makedirs(task_dir, exist_ok=True)

                for src_path in file_paths:
                    filename = os.path.basename(src_path)
                    dst_path = os.path.join(task_dir, filename)

                    await to_thread.run_sync(shutil.copyfile, src_path, dst_path)
                    self.task_results[task_id_commitment].append(dst_path)

                if checkpoint_dir is not None:
                    dst_path = os.path.join(task_dir, "result_checkpoint")

                    await to_thread.run_sync(shutil.copytree, checkpoint_dir, dst_path)
                    self.task_result_checkpoint[task_id_commitment] = dst_path

                condition.notify()

    async def get_result(self, task_id_commitment: bytes, index: int, dst: BinaryIO):
        with self.wrap_error("getResult"):
            condition = self.get_condition(task_id_commitment)
            async with condition:
                while task_id_commitment not in self.task_results:
                    await condition.wait()

            src_file = self.task_results[task_id_commitment][index]

            def _copy_file_obj():
                with open(src_file, mode="rb") as src:
                    shutil.copyfileobj(src, dst)

            await to_thread.run_sync(_copy_file_obj)

    async def get_result_checkpoint(
        self, task_id_commitment: bytes, result_checkpoint_dir: str
    ):
        with self.wrap_error("getResultCheckpoint"):
            condition = self.get_condition(task_id_commitment)
            async with condition:
                while task_id_commitment not in self.task_result_checkpoint:
                    await condition.wait()

            src_dir = self.task_result_checkpoint[task_id_commitment]

            await to_thread.run_sync(shutil.copytree, src_dir, result_checkpoint_dir)

    async def now(self) -> int:
        return int(time.time())

    async def close(self):
        if not self._closed:
            self.tasks = {}
            self.task_results = {}
            self._conditions = {}

            def _rm_tempdir():
                if os.path.exists(self._tempdir):
                    shutil.rmtree(self._tempdir)

            await to_thread.run_sync(_rm_tempdir)
            self._closed = True
