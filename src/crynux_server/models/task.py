from datetime import datetime
from enum import IntEnum
from typing import List, Optional

from pydantic import BaseModel

from .common import AddressFromStr, BytesFromHex, WeiFromStr


class TaskType(IntEnum):
    SD = 0
    LLM = 1
    SD_FT_LORA = 2


class TaskError(IntEnum):
    NONE = 0
    ParametersValidationFailed = 1


class TaskAbortReason(IntEnum):
    NONE = 0
    Timeout = 1
    ModelDownloadFailed = 2
    IncorrectResult = 3
    TaskFeeTooLow = 4


class InferenceTaskStatus(IntEnum):
    Queued = 0
    Started = 1
    ParametersUploaded = 2
    ErrorReported = 3
    ScoreReady = 4
    Validated = 5
    GroupValidated = 6
    EndInvalidated = 7
    EndSuccess = 8
    EndAborted = 9
    EndGroupRefund = 10
    EndGroupSuccess = 11


class DownloadTaskStatus(IntEnum):
    Started = 0
    Executed = 1
    Success = 2
    Failed = 3


class ChainTask(BaseModel):
    task_type: TaskType
    creator: str
    task_id_commitment: bytes
    sampling_seed: bytes
    nonce: bytes
    sequence: int
    status: InferenceTaskStatus
    selected_node: str
    timeout: int
    score: bytes
    task_fee: int
    task_size: int
    task_model_ids: List[str]
    min_vram: int
    required_gpu: str
    required_gpu_vram: int
    task_version: List[int]
    abort_reason: TaskAbortReason
    error: TaskError
    payment_addresses: List[str]
    payments: List[int]
    create_timestamp: int
    start_timestamp: int
    score_ready_timestamp: int


class TaskConfig(BaseModel):
    image_width: int
    image_height: int
    lora_weight: int
    num_images: int
    seed: int
    steps: int


class PoseConfig(BaseModel):
    pose_weight: int
    preprocess: bool
    data_url: str = ""


class RelayTask(BaseModel):
    sequence: int
    task_args: str
    task_id_commitment: BytesFromHex
    creator: AddressFromStr
    sampling_seed: BytesFromHex
    nonce: BytesFromHex
    status: InferenceTaskStatus
    task_type: TaskType
    task_version: str
    timeout: int
    min_vram: int
    required_gpu: str
    required_gpu_vram: int
    task_fee: WeiFromStr
    task_size: int
    model_ids: List[str]
    score: str
    qos_score: int
    selected_node: AddressFromStr
    create_time: Optional[datetime] = None
    start_time: Optional[datetime] = None
    score_ready_time: Optional[datetime] = None
    validated_time: Optional[datetime] = None
    result_uploaded_time: Optional[datetime] = None


class InferenceTaskState(BaseModel):
    task_id_commitment: bytes
    timeout: int
    status: InferenceTaskStatus
    task_type: TaskType
    files: List[str] = []
    score: bytes = b""
    waiting_tx_hash: bytes = b""
    waiting_tx_method: str = ""
    checkpoint: Optional[str] = None


class DownloadTaskState(BaseModel):
    task_id: str
    task_type: TaskType
    model_id: str
    status: DownloadTaskStatus
