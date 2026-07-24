import asyncio
import time
from typing import Any, Callable

from .error import (
    TaskCancellation,
    TaskCancellationType,
    TaskCancelled,
    TaskPhase,
)


class TaskFuture(object):
    def __init__(
        self, task_id: str, worker_role: str, deadline: float | None = None
    ) -> None:
        loop = asyncio.get_running_loop()
        self._future = loop.create_future()
        self.task_id = task_id
        self.worker_role = worker_role
        self.worker_id: int | None = None
        self.deadline = deadline
        self.phase = TaskPhase.QUEUED

    def set_result(self, result):
        if not self._future.cancelled():
            self._future.set_result(result)

    def set_error(self, exc: Exception):
        if not self._future.cancelled():
            self._future.set_exception(exc)

    def mark_dispatched(self, worker_id: int):
        self.worker_id = worker_id
        self.phase = TaskPhase.DISPATCHED

    def mark_sent(self):
        self.phase = TaskPhase.SENT

    def cancel(
        self,
        cancellation_type: TaskCancellationType,
        initiated_by: str,
        reason: str,
    ):
        if not self._future.done():
            cancellation = TaskCancellation(
                cancellation_type=cancellation_type,
                initiated_by=initiated_by,
                reason=reason,
                worker_role=self.worker_role,
                worker_id=self.worker_id,
                phase=self.phase,
                task_id=self.task_id,
                deadline=self.deadline,
                cancelled_at=time.time(),
            )
            self._future.set_exception(TaskCancelled(cancellation))

    def add_done_callback(self, callback: Callable[[asyncio.Future[Any]], None]):
        self._future.add_done_callback(callback)

    async def get(self):
        return await self._future

    def done(self):
        return self._future.done()

    def cancelled(self):
        return self._future.cancelled()
