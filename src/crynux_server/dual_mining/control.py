import logging
from typing import Optional

from anyio import Condition, Lock

from crynux_server.config import get_config
from crynux_server.in_memory_model_tracker import get_in_memory_model_tracker
from crynux_server.relay import get_relay
from crynux_server.worker_manager import get_worker_manager

_logger = logging.getLogger(__name__)


class DualMiningControl(object):
    """Coordinates Pearl mining with Crynux inference.

    When dual mining is enabled, inference waits until the controller opens the
    gate after stopping the miner. Unload restarts the inference worker so VRAM
    is cleared and the Relay in-memory model is reported as empty.
    """

    def __init__(self) -> None:
        self._gate_open = False
        self._gate_condition = Condition()
        self._unload_lock = Lock()
        self._unloading = False

    @property
    def gate_open(self) -> bool:
        return self._gate_open

    @property
    def unloading(self) -> bool:
        return self._unloading

    def enabled(self) -> bool:
        return bool(get_config().dual_mining.enabled)

    async def open_gate(self) -> None:
        async with self._gate_condition:
            if not self._gate_open:
                _logger.info("Dual mining inference gate opened")
            self._gate_open = True
            self._gate_condition.notify_all()

    async def close_gate(self) -> None:
        async with self._gate_condition:
            if self._gate_open:
                _logger.info("Dual mining inference gate closed")
            self._gate_open = False

    async def wait_gate_open(self) -> None:
        async with self._gate_condition:
            while not self._gate_open:
                _logger.info("Waiting for dual mining inference gate")
                await self._gate_condition.wait()

    async def consume_gate(self) -> None:
        """Wait until open, then close so the next task needs a fresh open."""
        async with self._gate_condition:
            while not self._gate_open:
                _logger.info("Waiting for dual mining inference gate")
                await self._gate_condition.wait()
            self._gate_open = False
            _logger.info("Dual mining inference gate consumed")

    async def unload(self) -> None:
        async with self._unload_lock:
            self._unloading = True
            try:
                _logger.info("Dual mining unload: restarting inference worker")
                worker = get_worker_manager("inference")
                await worker.restart(reason="dual_mining unload")
                tracker = get_in_memory_model_tracker()
                try:
                    await tracker.sync(get_relay())
                except Exception:
                    _logger.exception(
                        "Dual mining unload: failed to report empty in-memory model"
                    )
                    raise
                _logger.info("Dual mining unload completed")
            finally:
                self._unloading = False


_control: Optional[DualMiningControl] = None


def get_dual_mining_control() -> DualMiningControl:
    global _control
    if _control is None:
        _control = DualMiningControl()
    return _control


async def wait_inference_gate() -> None:
    control = get_dual_mining_control()
    if not control.enabled():
        return
    await control.consume_gate()
