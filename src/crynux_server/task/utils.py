import array
import hashlib
import os
import re
import sys
from typing import List, Optional, Tuple

import av
import imhash
from anyio import get_cancelled_exc_class
from crynux_server.models import (
    InferenceTaskInput,
    TaskInput,
    TaskType,
    ModelConfig,
    ModelFileEntry,
    DownloadTaskInput,
    resolve_model_files,
)
from crynux_server.worker_manager import TaskCancellationType, get_worker_manager


MAX_VIDEO_SCORE_FRAMES = 8


def get_image_hash(filename: str) -> bytes:
    return bytes.fromhex(imhash.getPHash(filename)[2:])  # type: ignore


def get_gpt_resp_hash(filename: str) -> bytes:
    with open(filename, mode="rb") as f:
        return hashlib.sha256(f.read()).digest()


def sampled_video_frame_indices(
    num_frames: int, max_samples: int = MAX_VIDEO_SCORE_FRAMES
) -> List[int]:
    if num_frames <= 0:
        raise ValueError(f"num_frames must be positive, got {num_frames}")
    count = min(max_samples, num_frames)
    if count == 1:
        return [0]
    return [(i * (num_frames - 1)) // (count - 1) for i in range(count)]


def _extract_raw_yuv420p_luma_plane(frame: av.VideoFrame) -> Tuple[bytes, int, int]:
    # Read plane 0 of yuv420p directly without gray pixel-format conversion
    # so libswscale limited-to-full range expansion is never invoked.
    if frame.format.name != "yuv420p":
        frame = frame.reformat(format="yuv420p")
    plane = frame.planes[0]
    width = int(frame.width)
    height = int(frame.height)
    stride = int(plane.line_size)
    if width <= 0 or height <= 0 or stride < width:
        raise ValueError(
            f"Invalid yuv420p luma plane geometry: width={width}, height={height}, stride={stride}"
        )
    view = memoryview(plane)
    if stride == width:
        return bytes(view[: height * width]), width, height
    buf = bytearray(height * width)
    for row in range(height):
        src_offset = row * stride
        dst_offset = row * width
        buf[dst_offset : dst_offset + width] = view[src_offset : src_offset + width]
    return bytes(buf), width, height


def get_video_hash(filename: str) -> bytes:
    with av.open(filename, mode="r") as container:
        if not container.streams.video:
            raise ValueError("Video file contains no video stream")
        video_stream = container.streams.video[0]
        has_audio = len(container.streams.audio) > 0

        luma_frames: List[Tuple[bytes, int, int]] = []
        for frame in container.decode(video_stream):
            luma_frames.append(_extract_raw_yuv420p_luma_plane(frame))

    total_frames = len(luma_frames)
    if total_frames == 0:
        raise ValueError("Video file contains zero decodable video frames")

    indices = sampled_video_frame_indices(total_frames)
    blocks: List[bytes] = []
    for idx in indices:
        luma_bytes, width, height = luma_frames[idx]
        blocks.append(imhash.getVideoFramePHash(luma_bytes, width, height))  # type: ignore
    del luma_frames

    if has_audio:
        with av.open(filename, mode="r") as audio_container:
            audio_stream = audio_container.streams.audio[0]
            channels = len(audio_stream.layout.channels)
            if channels <= 0:
                raise ValueError(f"Invalid audio channel count: {channels}")
            sample_rate = int(
                audio_stream.codec_context.sample_rate or audio_stream.rate or 0
            )
            pcm_buf = bytearray()
            if sample_rate > 0:
                # Preserve native channel layout and sample rate so channel averaging
                # is performed as an arithmetic mean in Go rather than libswresample -ac 1.
                resampler = av.audio.resampler.AudioResampler(
                    format="dbl",
                    layout=audio_stream.layout,
                    rate=sample_rate,
                )
                bytes_per_sample_frame = channels * 8
                for aframe in audio_container.decode(audio_stream):
                    for rframe in resampler.resample(aframe):
                        pcm_buf.extend(
                            memoryview(rframe.planes[0])[
                                : rframe.samples * bytes_per_sample_frame
                            ]
                        )
                for rframe in resampler.resample(None):
                    pcm_buf.extend(
                        memoryview(rframe.planes[0])[
                            : rframe.samples * bytes_per_sample_frame
                        ]
                    )
                if sys.byteorder != "little" and pcm_buf:
                    arr = array.array("d")
                    arr.frombytes(pcm_buf)
                    arr.byteswap()
                    pcm_buf = bytearray(arr.tobytes())
            blocks.append(
                imhash.getVideoAudioPHash(bytes(pcm_buf), channels)  # type: ignore
            )

    return b"".join(blocks)


async def run_inference_task(
    task_id_commitment: bytes,
    task_type: TaskType,
    models: List[ModelConfig],
    task_args: str,
    task_dir: str,
    deadline: Optional[float] = None,
    execution_model_files: Optional[List[ModelFileEntry]] = None,
    requested_model_cache_hash: Optional[str] = None,
):
    worker_manager = get_worker_manager("inference")
    task_input = TaskInput(
        task=InferenceTaskInput(
            task_name="inference",
            task_type=task_type,
            task_id=task_id_commitment.hex(),
            models=models,
            task_args=task_args,
            output_dir=task_dir,
            execution_model_files=execution_model_files or [],
            requested_model_cache_hash=requested_model_cache_hash,
        )
    )
    task_result = await worker_manager.send_task(task_input, deadline=deadline)
    try:
        success_result = await task_result.get()
    except get_cancelled_exc_class():
        # Drop the queued task so it is never sent to the worker
        # after the awaiting runner has been cancelled
        if not task_result.done():
            task_result.cancel(
                TaskCancellationType.CALLER_CANCELLED,
                "task_reconciler",
                "the task runner stopped waiting before Worker dispatch",
            )
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
    elif task_type == TaskType.VIDEO:
        files = [f for f in os.listdir(task_dir) if re.match(r"[0-9]+\.mp4", f)]
        files.sort(key=lambda f: int(f.split(".")[0]))
        files = [os.path.join(task_dir, f) for f in files]
        if not files:
            raise ValueError("Video task produced no output MP4 file")
        hashes = [get_video_hash(filename) for filename in files]
    elif task_type == TaskType.SD_FT_LORA:
        img_dir = os.path.join(task_dir, "validation")
        files = [f for f in os.listdir(img_dir) if re.match(r"[0-9]+\.png", f)]
        files.sort(key=lambda f: int(f.split(".")[0]))
        files = [os.path.join(img_dir, f) for f in files]
        hashes = [get_image_hash(filename) for filename in files]
        checkpoint = os.path.join(task_dir, "checkpoint")

    return (
        files,
        hashes,
        checkpoint,
        success_result.execution_dtype,
        success_result.model_cache_hash,
    )


async def run_download_task(
    task_id: str,
    task_type: TaskType,
    model_files: List[ModelFileEntry],
) -> List[ModelFileEntry]:
    """Download the required model files and return the exact entry of each."""
    worker_manager = get_worker_manager("download")
    task_input = TaskInput(
        task=DownloadTaskInput(
            task_name="download",
            task_type=task_type,
            task_id=task_id,
            model_files=model_files,
        )
    )
    task_result = await worker_manager.send_task(task_input)
    success_result = await task_result.get()
    return resolve_model_files(model_files, success_result.model_files)


def validate_score(score: bytes) -> bool:
    return len(score) > 0 and len(score) % 8 == 0 and not all(b == 0 for b in score)
