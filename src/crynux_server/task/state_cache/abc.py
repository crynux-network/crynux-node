from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List

from crynux_server import models


class InferenceTaskStateCache(ABC):
    @abstractmethod
    async def load(self, task_id_commitment: bytes) -> models.InferenceTaskState: ...

    @abstractmethod
    async def dump(self, task_state: models.InferenceTaskState): ...

    @abstractmethod
    async def has(self, task_id_commitment: bytes) -> bool: ...

    @abstractmethod
    async def find(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        status: Optional[List[models.InferenceTaskStatus]] = None,
    ) -> List[models.InferenceTaskState]: ...

    @abstractmethod
    async def get_latest_created(
        self,
    ) -> Optional[models.InferenceTaskState]: ...


class DownloadTaskStateCache(ABC):
    @abstractmethod
    async def load(self, task_id: str) -> models.DownloadTaskState: ...

    @abstractmethod
    async def dump(self, task_state: models.DownloadTaskState): ...

    @abstractmethod
    async def has(self, task_id: str) -> bool: ...

    @abstractmethod
    async def find(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        status: Optional[List[models.DownloadTaskStatus]] = None,
    ) -> List[models.DownloadTaskState]: ...

