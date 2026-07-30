from typing import List, Literal

from pydantic import BaseModel, Field

from .task import TaskType
from .download_model import ModelConfig


class DownloadTaskInput(BaseModel):
    task_name: Literal["download"]
    task_type: TaskType
    task_id: str
    model: ModelConfig


class InferenceTaskInput(BaseModel):
    task_name: Literal["inference"]
    task_type: TaskType
    task_id: str
    models: List[ModelConfig]
    task_args: str
    output_dir: str


class TaskInput(BaseModel):
    task: DownloadTaskInput | InferenceTaskInput = Field(discriminator="task_name")


class SuccessResult(BaseModel):
    status: Literal["success"]


class ErrorResult(BaseModel):
    status: Literal["error"]
    traceback: str
    # GPUs actually used for this failed task. Absent/0 means the Worker did
    # not report an executed count (legacy workers or no-result cancellations).
    gpu_count: int = 0


class TaskResult(BaseModel):
    task_name: Literal["inference", "download"]
    task_id_commitment: str
    result: SuccessResult | ErrorResult = Field(discriminator="status")
