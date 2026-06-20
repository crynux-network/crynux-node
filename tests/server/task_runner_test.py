import time
from datetime import datetime, timedelta

from web3 import Web3

from crynux_server import models
from crynux_server.task import InferenceTaskRunnerBase, MemoryInferenceTaskStateCache


class EndInvalidatedRunner(InferenceTaskRunnerBase):
    def __init__(self, task_id_commitment: bytes):
        super().__init__(
            task_id_commitment=task_id_commitment,
            state_cache=MemoryInferenceTaskStateCache(),
            contracts=object(),
        )
        self.upload_calls = 0
        self.cancel_calls = 0
        self.cleaned = False
        self._status_index = 0
        # Keep delay short so the old timeout/cancel path is deterministic in tests.
        self._start_time = datetime.now() - timedelta(seconds=4.8)
        self._statuses = [
            models.InferenceTaskStatus.Queued,
            models.InferenceTaskStatus.EndInvalidated,
            models.InferenceTaskStatus.EndInvalidated,
        ]

    async def get_task(self) -> models.RelayTask:
        status = self._statuses[min(self._status_index, len(self._statuses) - 1)]
        self._status_index += 1
        return models.RelayTask(
            sequence=1,
            task_args="{}",
            task_id_commitment=self.task_id_commitment,
            creator=Web3.to_checksum_address("0x0000000000000000000000000000000000000001"),
            sampling_seed=bytes([0] * 32),
            nonce=bytes([0] * 32),
            status=status,
            task_type=models.TaskType.SD,
            task_version="3.0.0",
            timeout=0,
            min_vram=0,
            required_gpu="",
            required_gpu_vram=0,
            task_fee=0,
            task_size=1,
            model_ids=["test/model"],
            score="0x",
            qos_score=0,
            selected_node=Web3.to_checksum_address(
                "0x0000000000000000000000000000000000000002"
            ),
            create_time=self._start_time,
            start_time=self._start_time,
            score_ready_time=None,
            validated_time=None,
            result_uploaded_time=None,
        )

    async def cancel_task(self):
        self.cancel_calls += 1

    async def execute_task(self):
        return

    async def upload_result(self):
        self.upload_calls += 1

    async def cleanup(self):
        self.cleaned = True
        del self.state


async def test_end_invalidated_upload_exits_without_cancel():
    runner = EndInvalidatedRunner(task_id_commitment=bytes([1] * 32))

    start = time.monotonic()
    await runner.run(interval=0.01)
    elapsed = time.monotonic() - start

    assert runner.upload_calls == 1
    assert runner.cancel_calls == 0
    assert runner.cleaned
    assert elapsed < 0.5
