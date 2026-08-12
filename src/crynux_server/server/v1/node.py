import math
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from pydantic import BaseModel
from typing_extensions import Annotated

from crynux_server import models, utils
from crynux_server.relay import get_relay
from crynux_server.relay.exceptions import RelayError

from ..depends import (ManagerStateCacheDep, NodeStateManagerDep,
                       WorkerManagerDep)
from .utils import CommonResponse, http_exception_from_relay_error

router = APIRouter(prefix="/node")


class State(BaseModel):
    status: models.NodeStatus
    message: str
    tx_status: models.TxStatus
    tx_error: str
    init_message: str = ""
    slashed: bool = False


@router.get("", response_model=State)
async def get_node_state(*, state_cache: ManagerStateCacheDep) -> State:
    node_state = await state_cache.get_node_state()
    tx_state = await state_cache.get_tx_state()
    return State(
        status=node_state.status,
        message=node_state.message,
        tx_status=tx_state.status,
        tx_error=tx_state.error,
        init_message=node_state.init_message,
        slashed=node_state.slashed,
    )


ControlAction = Literal["start", "stop", "pause", "resume"]


class ControlNodeInput(BaseModel):
    action: ControlAction


@router.post("", response_model=CommonResponse)
async def control_node(
    input: Annotated[ControlNodeInput, Body()],
    *,
    state_manager: NodeStateManagerDep,
    worker_manager: WorkerManagerDep,
    background: BackgroundTasks,
):
    if state_manager is None:
        raise HTTPException(400, detail="Private key has not been set.")
    if input.action == "start":
        gpu_info = await utils.get_gpu_info()
        platform = utils.get_platform()
        version = worker_manager.version
        if version is None:
            raise HTTPException(400, detail="Worker has not been started.")
        version_list = [int(v) for v in version.split(".")]
        assert len(version_list) == 3
        wait = await state_manager.start(
            gpu_name=utils.apply_gpu_name_executor_marker(gpu_info) + "+" + platform,
            gpu_vram=math.ceil(gpu_info.vram_total_mb / 1024),
            version=version_list
        )
    elif input.action == "pause":
        wait = await state_manager.pause()
    elif input.action == "resume":
        wait = await state_manager.resume()
    else:
        wait = await state_manager.stop()

    background.add_task(wait)

    return CommonResponse()


class RunnerVersionResponse(BaseModel):
    version: str


@router.get("/runner/version", response_model=RunnerVersionResponse)
async def get_runner_version(
    *, worker_manager: WorkerManagerDep
) -> RunnerVersionResponse:
    return RunnerVersionResponse(version=worker_manager.version or "")


class NodeScoresResponse(BaseModel):
    staking: float
    qos: float
    prob_weight: float


@router.get("/scores", response_model=NodeScoresResponse)
async def get_node_scores(*, state_cache: ManagerStateCacheDep) -> NodeScoresResponse:
    node_score_state = await state_cache.get_node_score_state()
    return NodeScoresResponse(
        staking=node_score_state.staking_score,
        qos=node_score_state.qos_score,
        prob_weight=node_score_state.prob_weight,
    )


class QosTraceEvent(BaseModel):
    timestamp: int = 0
    node_address: str = ""
    task_id_commitment: str = ""
    event_type: str = ""
    task_qos_score: Optional[int] = None
    validation_rank: Optional[int] = None
    qos_long_before: float = 0
    qos_long_after: float = 0
    qos_short_before: float = 0
    qos_short_after: float = 0
    qos_before: float = 0
    qos_after: float = 0


class NodeQosTracingResponse(BaseModel):
    node_address: str
    max_task_events: int
    events: List[QosTraceEvent]


@router.get("/qos/tracing", response_model=NodeQosTracingResponse)
async def get_qos_tracing() -> NodeQosTracingResponse:
    try:
        relay = get_relay()
        data = await relay.get_qos_tracing()
        return NodeQosTracingResponse.model_validate(data)
    except AssertionError:
        raise HTTPException(400, detail="Private key has not been set.")
    except RelayError as e:
        raise http_exception_from_relay_error(e)
