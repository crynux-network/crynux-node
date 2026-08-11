import json
from typing import Literal

from pydantic import BaseModel, Field

from .common import AddressFromStr, BytesFromHex
from .task import (
    InferenceTaskStatus,
    SoftTaskAbortReason,
    TaskError,
    TaskType,
)

EventType = Literal[
    "TaskStarted",
    "DownloadModel",
    "TaskScoreReady",
    "TaskErrorReported",
    "TaskValidated",
    "TaskEndInvalidated",
    "TaskEndGroupRefund",
    "TaskEndAborted",
    "TaskEndSuccess",
    "TaskEndGroupSuccess",
    "NodeKickedOut",
    "NodeSlashed",
    "NodeJoin",
    "NodeQuit",
    "NodeStaking",
    "DelegatorStaking",
    "DelegatorUnstaking",
    "NodeDelegatorShareChanged",
    "DelegatedStakingSlashed",
]


class Event(BaseModel):
    id: int
    type: EventType = Field(init_var=False)


class TaskStarted(Event):
    type: EventType = Field(default="TaskStarted", init_var=False, frozen=True)
    selected_node: AddressFromStr
    task_id_commitment: BytesFromHex


class DownloadModel(Event):
    type: EventType = Field(default="DownloadModel", init_var=False, frozen=True)
    node_address: AddressFromStr
    model_id: str
    task_type: TaskType


class TaskScoreReady(Event):
    type: EventType = Field(default="TaskScoreReady", init_var=False, frozen=True)
    task_id_commitment: BytesFromHex
    selected_node: AddressFromStr
    score: BytesFromHex


class TaskErrorReported(Event):
    type: EventType = Field(default="TaskErrorReported", init_var=False, frozen=True)
    task_id_commitment: BytesFromHex
    selected_node: AddressFromStr
    task_error: TaskError


class TaskValidated(Event):
    type: EventType = Field(default="TaskValidated", init_var=False, frozen=True)
    task_id_commitment: BytesFromHex
    selected_node: AddressFromStr


class TaskEndInvalidated(Event):
    type: EventType = Field(default="TaskEndInvalidated", init_var=False, frozen=True)
    task_id_commitment: BytesFromHex
    selected_node: AddressFromStr


class TaskEndGroupRefund(Event):
    type: EventType = Field(default="TaskEndGroupRefund", init_var=False, frozen=True)
    task_id_commitment: BytesFromHex
    selected_node: AddressFromStr


class TaskEndAborted(Event):
    type: EventType = Field(default="TaskEndAborted", init_var=False, frozen=True)
    task_id_commitment: BytesFromHex
    abort_issuer: AddressFromStr
    last_status: InferenceTaskStatus
    abort_reason: SoftTaskAbortReason


class TaskEndSuccess(Event):
    type: EventType = Field(default="TaskEndSuccess", init_var=False, frozen=True)
    task_id_commitment: BytesFromHex
    selected_node: AddressFromStr


class TaskEndGroupSuccess(Event):
    type: EventType = Field(default="TaskEndGroupSuccess", init_var=False, frozen=True)
    task_id_commitment: BytesFromHex
    selected_node: AddressFromStr


class NodeKickedOut(Event):
    type: EventType = Field(default="NodeKickedOut", init_var=False, frozen=True)
    node_address: AddressFromStr
    task_id_commitment: BytesFromHex


class NodeSlashed(Event):
    type: EventType = Field(default="NodeSlashed", init_var=False, frozen=True)
    node_address: AddressFromStr
    task_id_commitment: BytesFromHex


class NodeJoin(Event):
    type: EventType = Field(default="NodeJoin", init_var=False, frozen=True)
    node_address: AddressFromStr


class NodeQuit(Event):
    type: EventType = Field(default="NodeQuit", init_var=False, frozen=True)
    node_address: AddressFromStr
    blockchain_transaction_id: int


class NodeStaking(Event):
    type: EventType = Field(default="NodeStaking", init_var=False, frozen=True)
    node_address: AddressFromStr
    staking_amount: str


class DelegatorStaking(Event):
    type: EventType = Field(default="DelegatorStaking", init_var=False, frozen=True)
    delegator_address: AddressFromStr
    node_address: AddressFromStr
    amount: str
    network: str


class DelegatorUnstaking(Event):
    type: EventType = Field(default="DelegatorUnstaking", init_var=False, frozen=True)
    delegator_address: AddressFromStr
    node_address: AddressFromStr
    amount: str
    network: str


class NodeDelegatorShareChanged(Event):
    type: EventType = Field(
        default="NodeDelegatorShareChanged", init_var=False, frozen=True
    )
    node_address: AddressFromStr
    share: int
    network: str


class DelegatedStakingSlashed(Event):
    type: EventType = Field(
        default="DelegatedStakingSlashed", init_var=False, frozen=True
    )
    delegator_address: AddressFromStr
    node_address: AddressFromStr
    amount: str
    network: str


def load_event(id: int, type: EventType, args: str) -> Event:
    try:
        cls = globals()[type]
        assert issubclass(cls, Event)
        e = json.loads(args)
        e["id"] = id
        return cls.model_validate(e)
    except (KeyError, AssertionError):
        raise ValueError(f"unknown event type {type} from json")
