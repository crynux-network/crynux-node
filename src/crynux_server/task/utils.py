import hashlib
import os
import re
from typing import List, Literal

import imhash
from anyio import get_cancelled_exc_class
from crynux_server.models import (
    InferenceTaskInput,
    TaskInput,
    TaskType,
    ModelConfig,
    DownloadTaskInput,
)
from crynux_server.worker_manager import get_worker_manager


def get_image_hash(filename: str) -> bytes:
    return bytes.fromhex(imhash.getPHash(filename)[2:])  # type: ignore


def get_gpt_resp_hash(filename: str) -> bytes:
    with open(filename, mode="rb") as f:
        return hashlib.sha256(f.read()).digest()


async def run_inference_task(
    task_id_commitment: bytes,
    task_type: TaskType,
    models: List[ModelConfig],
    task_args: str,
    task_dir: str,
):
    worker_manager = get_worker_manager()
    task_input = TaskInput(
        task=InferenceTaskInput(
            task_name="inference",
            task_type=task_type,
            task_id=task_id_commitment.hex(),
            models=models,
            task_args=task_args,
            output_dir=task_dir,
        )
    )
    task_result = await worker_manager.send_task(task_input)
    try:
        await task_result.get()
    except get_cancelled_exc_class():
        # Drop the queued task so it is never sent to the worker
        # after the awaiting runner has been cancelled
        if not task_result.done():
            task_result.cancel()
        raise

    files: List[str] = []
    hashes: List[bytes] = []
    checkpoint: str | None = None
    if task_type == TaskType.SD:
        files = [f for f in os.listdir(task_dir) if re.match(r"[0-9]+\.png", f)]
        files.sort(key=lambda f: int(f.split(".")[0]))
        files = [os.path.join(task_dir, f) for f in files]
        hashes = [get_image_hash(filename) for filename in files]
    elif task_type == TaskType.LLM:
        files = [f for f in os.listdir(task_dir) if re.match(r"[0-9]+\.json", f)]
        files.sort(key=lambda f: int(f.split(".")[0]))
        files = [os.path.join(task_dir, f) for f in files]
        hashes = [get_gpt_resp_hash(filename) for filename in files]
    elif task_type == TaskType.SD_FT_LORA:
        img_dir = os.path.join(task_dir, "validation")
        files = [f for f in os.listdir(img_dir) if re.match(r"[0-9]+\.png", f)]
        files.sort(key=lambda f: int(f.split(".")[0]))
        files = [os.path.join(img_dir, f) for f in files]
        hashes = [get_image_hash(filename) for filename in files]
        checkpoint = os.path.join(task_dir, "checkpoint")

    return files, hashes, checkpoint


async def run_download_task(
    task_id: str,
    task_type: TaskType,
    model: ModelConfig,
):
    worker_manager = get_worker_manager()
    task_input = TaskInput(
        task=DownloadTaskInput(
            task_name="download",
            task_type=task_type,
            task_id=task_id,
            model=model,
        )
    )
    task_result = await worker_manager.send_task(task_input)
    await task_result.get()


def validate_score(score: bytes) -> bool:
    return len(score) > 0 and len(score) % 8 == 0 and not all(b==0 for b in score)