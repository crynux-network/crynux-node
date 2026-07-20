import os.path
import platform
import re
import subprocess
from collections import OrderedDict
from typing import Any, Dict, List

import psutil
from anyio import run_process, to_thread
from eth_account import Account
from pydantic import BaseModel
from web3 import Web3

from crynux_server.config import load_env_file

__all__ = [
    "sort_dict",
    "get_os",
    "get_platform",
    "get_task_hash",
    "GpuInfo",
    "NvidiaGpuCard",
    "parse_nvidia_smi_gpus",
    "select_gpu_cards",
    "aggregate_gpu_info",
    "get_gpu_info",
    "get_selected_gpu_device_uuids",
    "resolve_gpt_executor",
    "apply_gpu_name_executor_marker",
    "CpuInfo",
    "get_cpu_info",
    "MemoryInfo",
    "get_memory_info",
    "DiskInfo",
    "get_disk_info",
]


def sort_dict(input: Dict[str, Any]) -> Dict[str, Any]:
    keys = sorted(input.keys())

    res = OrderedDict()
    for key in keys:
        value = input[key]
        if isinstance(value, dict):
            value = sort_dict(value)
        res[key] = value

    return res


def get_task_hash(task_args: str):
    res = Web3.keccak(task_args.encode("utf-8"))
    return res.hex()


def get_os():
    return platform.system()


def is_running_in_docker():
    if os.path.exists("/.dockerenv"):
        return True

    try:
        with open("/proc/self/cgroup", "r") as f:
            if any("docker" in line for line in f):
                return True
    except IOError:
        pass

    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True

    return False


def get_platform() -> str:
    if is_running_in_docker():
        return "docker"
    return get_os()


class MemoryInfo(BaseModel):
    available_mb: int = 0
    total_mb: int = 0


async def _get_memory_info() -> MemoryInfo:
    info = MemoryInfo()

    svmem = await to_thread.run_sync(psutil.virtual_memory)

    info.total_mb = svmem.total // (2**20)
    info.available_mb = svmem.available // (2**20)

    return info


async def _get_osx_memory_info() -> MemoryInfo:
    info = MemoryInfo()
    res = await run_process(["sysctl", "hw.memsize"])
    output = res.stdout.decode()
    total_mem = re.match(r"hw.memsize: (\d+)", output)
    if total_mem:
        info.total_mb = round(int(total_mem.group(1)) / 1024 / 1024)

    usage_cmd = (
        "vm_stat | perl -ne '/page size of (\\d+)/ and $size=$1; "
        '/Pages\\s+free[^\\d]+(\\d+)/ and printf("%.2f",  $1 * $size / 1048576);\''
    )
    res = await run_process(usage_cmd)
    output = res.stdout.decode()
    info.available_mb = int(float(output))

    return info


async def get_memory_info() -> MemoryInfo:
    if get_os() == "Darwin":
        return await _get_osx_memory_info()
    else:
        return await _get_memory_info()


class GpuInfo(BaseModel):
    usage: int = 0
    model: str = ""
    vram_used_mb: int = 0
    vram_total_mb: int = 0
    device_uuids: List[str] = []


class NvidiaGpuCard(BaseModel):
    index: int
    uuid: str
    name: str
    usage: int = 0
    vram_used_mb: int = 0
    vram_total_mb: int = 0


_NVIDIA_SMI_GPU_QUERY_CMD = (
    "nvidia-smi --query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total"
    " --format=csv"
)


def parse_nvidia_smi_gpus(output: str) -> List[NvidiaGpuCard]:
    number_pattern = re.compile(r"(\d+)")

    def parse_number(field: str) -> int:
        m = number_pattern.search(field)
        return int(m.group(1)) if m is not None else 0

    cards: List[NvidiaGpuCard] = []
    for line in output.strip().splitlines()[1:]:
        line = line.strip()
        if len(line) == 0:
            continue
        fields = [field.strip() for field in line.split(",")]
        assert len(fields) == 6
        cards.append(
            NvidiaGpuCard(
                index=int(fields[0]),
                uuid=fields[1],
                name=fields[2],
                usage=parse_number(fields[3]),
                vram_used_mb=parse_number(fields[4]),
                vram_total_mb=parse_number(fields[5]),
            )
        )
    return cards


def select_gpu_cards(cards: List[NvidiaGpuCard]) -> List[NvidiaGpuCard]:
    """Select the largest identical-model group of GPU cards.

    Ties are broken by larger per-card VRAM, then by the group containing
    the lowest card index.
    """
    assert len(cards) > 0

    groups: Dict[str, List[NvidiaGpuCard]] = OrderedDict()
    for card in cards:
        groups.setdefault(card.name, []).append(card)

    def group_key(group: List[NvidiaGpuCard]):
        return (
            len(group),
            max(card.vram_total_mb for card in group),
            -min(card.index for card in group),
        )

    return max(groups.values(), key=group_key)


def aggregate_gpu_info(cards: List[NvidiaGpuCard]) -> GpuInfo:
    selected = select_gpu_cards(cards)
    name = selected[0].name
    model = name if len(selected) == 1 else f"{len(selected)}x {name}"
    return GpuInfo(
        usage=max(card.usage for card in selected),
        model=model,
        vram_used_mb=sum(card.vram_used_mb for card in selected),
        vram_total_mb=sum(card.vram_total_mb for card in selected),
        device_uuids=[card.uuid for card in selected],
    )


async def _get_nvidia_gpu_info() -> GpuInfo:
    res = await run_process(_NVIDIA_SMI_GPU_QUERY_CMD)
    output = res.stdout.decode()
    cards = parse_nvidia_smi_gpus(output)
    return aggregate_gpu_info(cards)


async def _get_osx_gpu_info() -> GpuInfo:
    mem_info = await _get_osx_memory_info()
    info = GpuInfo(
        vram_used_mb=mem_info.total_mb - mem_info.available_mb,
        vram_total_mb=mem_info.total_mb,
    )

    res = await run_process("system_profiler SPDisplaysDataType")
    output = res.stdout.decode()
    m = re.search(r"Chipset Model:([\w\s]+)", output)
    if m is not None:
        info.model = m.group(1)
    return info


async def get_gpu_info() -> GpuInfo:
    if get_os() == "Darwin":
        return await _get_osx_gpu_info()
    else:
        return await _get_nvidia_gpu_info()


def get_selected_gpu_device_uuids() -> List[str]:
    """Synchronously enumerate NVIDIA GPUs and return the device UUIDs of the
    selected identical-model group. Returns an empty list on macOS, where GPU
    selection does not apply."""
    if get_os() == "Darwin":
        return []
    res = subprocess.run(
        _NVIDIA_SMI_GPU_QUERY_CMD, shell=True, capture_output=True, check=True
    )
    output = res.stdout.decode()
    cards = parse_nvidia_smi_gpus(output)
    return aggregate_gpu_info(cards).device_uuids


GPT_EXECUTOR_TENSOR_PARALLEL = "tensor_parallel"
GPT_EXECUTOR_CLASSIC = "classic"

_TP_PLATFORMS = ("docker", "Linux")


def resolve_gpt_executor(platform: str, gpu_count: int) -> str:
    """Resolve the effective GPT executor mode for this node.

    Tensor parallelism is the default on eligible machines: the
    WORKER_GPT_EXECUTOR value in the config .env is tensor_parallel or
    unset, the platform is docker or Linux, and the selected identical-model
    GPU group has at least 2 cards. Any other .env value opts out.
    """
    env_value = load_env_file("WORKER_").get("GPT_EXECUTOR")
    if env_value is not None and env_value != GPT_EXECUTOR_TENSOR_PARALLEL:
        return GPT_EXECUTOR_CLASSIC
    if platform not in _TP_PLATFORMS:
        return GPT_EXECUTOR_CLASSIC
    if gpu_count < 2:
        return GPT_EXECUTOR_CLASSIC
    return GPT_EXECUTOR_TENSOR_PARALLEL


def apply_gpu_name_executor_marker(gpu_info: GpuInfo) -> str:
    """Return the aggregated GPU name with a ' TP' marker appended when the
    effective executor is tensor parallel. The marker separates TP nodes
    from non-TP nodes in relay validation pools; the WebUI and the relay
    name both go through this helper so the two can never disagree."""
    executor = resolve_gpt_executor(get_platform(), len(gpu_info.device_uuids))
    if executor == GPT_EXECUTOR_TENSOR_PARALLEL:
        return gpu_info.model + " TP"
    return gpu_info.model


class CpuInfo(BaseModel):
    usage: int = 0
    num_cores: int = 0
    frequency_mhz: int = 0
    description: str = ""


async def _get_cpu_info() -> CpuInfo:
    info = CpuInfo()

    info.usage = int(await to_thread.run_sync(psutil.cpu_percent, 0.1))
    info.num_cores = await to_thread.run_sync(psutil.cpu_count)
    info.frequency_mhz = int((await to_thread.run_sync(psutil.cpu_freq)).current)

    return info


async def _get_osx_cpu_info() -> CpuInfo:
    info = CpuInfo()
    usage_cmd = r"ps -A -o %cpu | awk '{s+=$1} END {print s}'"
    res = await run_process(usage_cmd)
    output = res.stdout.decode()
    info.usage = round(float(output) * 100)

    res = await run_process(["sysctl", "-n", "machdep.cpu.brand_string"])
    output = res.stdout.decode()
    info.description = output

    res = await run_process(["sysctl", "hw.logicalcpu"])
    output = res.stdout.decode()
    ids = re.match(r"hw.logicalcpu: (\d+)", output)
    if ids:
        info.num_cores = int(ids.group(1))
    return info


async def get_cpu_info() -> CpuInfo:
    if get_os() == "Darwin":
        return await _get_osx_cpu_info()
    else:
        return await _get_cpu_info()


class DiskInfo(BaseModel):
    hf_models: int = 0
    external_models: int = 0
    logs: int = 0
    temp_files: int = 0


def _get_dir_size(path: str) -> int:
    size = 0
    for dirpath, _, filenames in os.walk(path):
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            if os.path.isfile(file_path) and not os.path.islink(file_path):
                size += os.path.getsize(file_path)
    return size


async def get_disk_info(
    hf_model_dir: str,
    external_model_dir: str,
    log_dir: str,
    temp_dir: str,
) -> DiskInfo:
    key_dirs = {
        "hf_models": hf_model_dir,
        "external_models": external_model_dir,
        "logs": log_dir,
        "temp_files": temp_dir,
    }
    result = {}
    for key, path in key_dirs.items():
        if os.path.exists(path):
            size = await to_thread.run_sync(_get_dir_size, path)
            result[key] = size // 1024
    return DiskInfo(**result)


def get_address_from_privkey(privkey: str):
    addrLowcase = Account.from_key(privkey).address
    return Web3.to_checksum_address(addrLowcase)
