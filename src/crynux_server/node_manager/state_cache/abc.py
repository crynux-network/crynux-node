from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from crynux_server.models import NodeState, TxState, NodeScoreState

T = TypeVar("T", NodeState, TxState, NodeScoreState)


class StateCache(ABC, Generic[T]):
    @abstractmethod
    async def get(self) -> T: ...

    @abstractmethod
    async def set(self, state: T): ...
