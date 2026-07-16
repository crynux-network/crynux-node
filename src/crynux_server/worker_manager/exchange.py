from collections import deque
from typing import Deque, Tuple

from anyio import Condition
from crynux_server.models import TaskInput

from .task import TaskFuture


class TaskExchange(object):
    def __init__(self) -> None:
        self._condition = Condition()
        self._task_queue: Deque[Tuple[TaskInput, TaskFuture]] = deque()

    async def send_task(self, task_input: TaskInput):
        task_result = TaskFuture()

        async with self._condition:
            self._task_queue.append((task_input, task_result))
            self._condition.notify(1)
        return task_result

    async def get_task(self) -> Tuple[TaskInput, TaskFuture]:
        async with self._condition:
            while len(self._task_queue) == 0:
                await self._condition.wait()
            return self._task_queue.popleft()

    async def clear(self):
        async with self._condition:
            while len(self._task_queue) > 0:
                _, task_result = self._task_queue.popleft()
                if not task_result.done():
                    task_result.cancel()
