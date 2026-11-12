import time
from datetime import datetime
from typing import Literal, cast

from fastapi import APIRouter
from pydantic import BaseModel

from crynux_server.models import InferenceTaskStatus, NodeStatus
from crynux_server.task import get_task_system

from ..depends import ManagerStateCacheDep, TaskStateCacheDep

router = APIRouter(prefix="/tasks")

TaskStatsStatus = Literal[
    "stopped",
    "unavailable",
    "none",
    "Queued",
    "Started",
    "ParametersUploaded",
    "ErrorReported",
    "ScoreReady",
    "Validated",
    "GroupValidated",
    "EndInvalidated",
    "EndSuccess",
    "EndAborted",
    "EndGroupRefund",
    "EndGroupSuccess",
]


class TaskStats(BaseModel):
    status: TaskStatsStatus
    num_today: int
    num_total: int


@router.get("", response_model=TaskStats)
async def get_task_stats(
    *, task_state_cache: TaskStateCacheDep, state_cache: ManagerStateCacheDep
):
    node_status = (await state_cache.get_node_state()).status
    num_today = 0
    num_total = 0
    if node_status not in [
        NodeStatus.Running,
        NodeStatus.PendingPause,
        NodeStatus.PendingStop,
    ]:
        status: TaskStatsStatus = "stopped"
    else:
        try:
            view = get_task_system().get_current_task_view()
        except AssertionError as e:
            if "TaskSystem has not been set" not in str(e):
                raise
            view = None

        if view is None or not view.synced:
            status = "unavailable"
        elif view.task_id is None:
            status = "none"
        elif task_state_cache is None or not await task_state_cache.has(view.task_id):
            status = "Started"
        else:
            state = await task_state_cache.load(view.task_id)
            status = cast(TaskStatsStatus, state.status.name)

    if task_state_cache is not None:
        now = datetime.now().astimezone()
        date = now.date()
        today_ts = time.mktime(date.timetuple())
        today = datetime.fromtimestamp(today_ts)

        success_status = [
            InferenceTaskStatus.EndSuccess,
            InferenceTaskStatus.EndGroupRefund,
            InferenceTaskStatus.EndGroupSuccess,
        ]

        today_states = await task_state_cache.find(start=today, status=success_status)

        total_states = await task_state_cache.find(status=success_status)

        num_today = len(today_states)
        num_total = len(total_states)

    return TaskStats(status=status, num_today=num_today, num_total=num_total)
