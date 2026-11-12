from datetime import datetime
from typing import Dict, List, Optional

from crynux_server.models import (DownloadTaskState, DownloadTaskStatus,
                                  InferenceTaskState, InferenceTaskStatus)

from .abc import DownloadTaskStateCache, InferenceTaskStateCache


class MemoryInferenceTaskStateCache(InferenceTaskStateCache):
    def __init__(self) -> None:
        self._states: Dict[bytes, InferenceTaskState] = {}
        self._state_times: Dict[bytes, datetime] = {}
        # Creation order matching the DB autoincrement id sequence
        self._created_ids: List[bytes] = []

    async def load(self, task_id_commitment: bytes) -> InferenceTaskState:
        if task_id_commitment in self._states:
            return self._states[task_id_commitment]
        else:
            raise KeyError(
                f"Inference task state of {task_id_commitment.hex()} not found."
            )

    async def dump(self, task_state: InferenceTaskState):
        task_id = task_state.task_id_commitment
        if task_id not in self._states:
            self._created_ids.append(task_id)
        self._states[task_id] = task_state
        self._state_times[task_id] = datetime.now()

    async def has(self, task_id_commitment: bytes) -> bool:
        return task_id_commitment in self._states

    async def find(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        status: Optional[List[InferenceTaskStatus]] = None,
    ) -> List[InferenceTaskState]:
        states = self._states
        if start is not None:
            states = {
                task_id: state
                for task_id, state in states.items()
                if self._state_times[task_id] >= start
            }
        if end is not None:
            states = {
                task_id: state
                for task_id, state in states.items()
                if self._state_times[task_id] < end
            }
        if status is not None:
            states = {
                task_id: state
                for task_id, state in states.items()
                if state.status in status
            }
        return list(states.values())

    async def get_latest_created(self) -> Optional[InferenceTaskState]:
        if not self._created_ids:
            return None
        return self._states[self._created_ids[-1]]


class MemoryDownloadTaskStateCache(DownloadTaskStateCache):
    def __init__(self) -> None:
        self._states: Dict[str, DownloadTaskState] = {}
        self._state_times: Dict[str, datetime] = {}

    async def load(self, task_id: str) -> DownloadTaskState:
        if task_id in self._states:
            return self._states[task_id]
        else:
            raise KeyError(f"Download task state of {task_id} not found.")

    async def dump(self, task_state: DownloadTaskState):
        self._states[task_state.task_id] = task_state
        self._state_times[task_state.task_id] = datetime.now()

    async def has(self, task_id: str) -> bool:
        return task_id in self._states

    async def find(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        status: Optional[List[DownloadTaskStatus]] = None,
    ) -> List[DownloadTaskState]:
        states = self._states
        if start is not None:
            states = {
                task_id: state
                for task_id, state in states.items()
                if self._state_times[task_id] >= start
            }
        if end is not None:
            states = {
                task_id: state
                for task_id, state in states.items()
                if self._state_times[task_id] < end
            }
        if status is not None:
            states = {
                task_id: state
                for task_id, state in states.items()
                if state.status in status
            }
        return list(states.values())
