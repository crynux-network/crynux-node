import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class TaskCancellationType(str, Enum):
    WORKER_TASK_TIMEOUT = "worker_task_timeout"
    WORKER_DISCONNECTED = "worker_disconnected"
    WORKER_RESTARTED = "worker_restarted"
    RUNNER_VERSION_SYNC = "runner_version_sync"
    NODE_INTERNAL = "node_internal"
    PROCESS_SHUTDOWN = "process_shutdown"
    CALLER_CANCELLED = "caller_cancelled"


class TaskPhase(str, Enum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    SENT = "sent"


@dataclass
class TaskCancellation:
    cancellation_type: TaskCancellationType
    initiated_by: str
    reason: str
    worker_role: str
    worker_id: Optional[int]
    phase: TaskPhase
    task_id: str
    deadline: Optional[float]
    cancelled_at: float

    def explanation(self) -> str:
        deadline_text = (
            datetime.fromtimestamp(self.deadline, timezone.utc).isoformat()
            if self.deadline is not None
            else "not set"
        )
        cancelled_text = datetime.fromtimestamp(
            self.cancelled_at, timezone.utc
        ).isoformat()
        deadline_reached = (
            self.deadline is not None and self.cancelled_at >= self.deadline
        )
        return "\n".join(
            [
                "No Worker traceback is available.",
                f"Cancellation type: {self.cancellation_type.value}.",
                f"Initiating component: {self.initiated_by}.",
                f"Reason: {self.reason}.",
                f"Worker role: {self.worker_role}.",
                f"Worker ID: {self.worker_id if self.worker_id is not None else 'unknown'}.",
                f"Task phase: {self.phase.value}.",
                f"Task ID: {self.task_id}.",
                f"Cancellation time: {cancelled_text}.",
                f"Task deadline: {deadline_text}.",
                f"Deadline reached: {'yes' if deadline_reached else 'no'}.",
                "No Worker result was received.",
            ]
        )


class TaskCancelled(Exception):
    def __init__(self, cancellation: TaskCancellation):
        self.cancellation = cancellation
        super().__init__(cancellation.explanation())


class TaskError(Exception):
    error_type = "TaskError"

    def __init__(self, msg: str, gpu_count: int = 0):
        self.msg = msg
        self.gpu_count = gpu_count

    def __str__(self) -> str:
        return f"{self.error_type}, error msg:\n{self.msg}\n"


class TaskInvalid(TaskError):
    error_type = "TaskInvalid"


class TaskExecutionError(TaskError):
    error_type = "TaskExecutionError"


class TaskDownloadError(TaskExecutionError):
    error_type = "TaskDownloadError"


def is_task_invalid(stdout: str) -> bool:
    pattern = re.compile(r"Task args invalid")
    return pattern.search(stdout) is not None


def is_model_not_downloaded(stdout: str) -> bool:
    return "Task model not downloaded" in stdout
