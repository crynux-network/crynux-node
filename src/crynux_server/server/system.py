import logging

from anyio import fail_after
from pydantic import BaseModel

from crynux_server import utils
from crynux_server.config import Config

_logger = logging.getLogger(__name__)


class SystemInfo(BaseModel):
    gpu: utils.GpuInfo = utils.GpuInfo()
    cpu: utils.CpuInfo = utils.CpuInfo()
    memory: utils.MemoryInfo = utils.MemoryInfo()
    disk: utils.DiskInfo = utils.DiskInfo()


_system_info = SystemInfo()


async def update_system_info(
    hf_model_dir: str,
    external_model_dir: str,
    log_dir: str,
    temp_dir: str,
):
    try:
        with fail_after(5):
            gpu_info = await utils.get_gpu_info()
            gpu_info.model = utils.apply_gpu_name_executor_marker(gpu_info)
            _system_info.gpu = gpu_info
    except TimeoutError:
        _logger.error("cannot get gpu info within 5 seconds")
        raise

    try:
        with fail_after(5):
            _system_info.cpu = await utils.get_cpu_info()
    except TimeoutError:
        _logger.error("cannot get cpu info within 5 seconds")
        raise

    try:
        with fail_after(5):
            _system_info.memory = await utils.get_memory_info()
    except TimeoutError:
        _logger.error("cannot get memory info within 5 seconds")
        raise

    try:
        with fail_after(10):
            _system_info.disk = await utils.get_disk_info(
                hf_model_dir=hf_model_dir,
                external_model_dir=external_model_dir,
                log_dir=log_dir,
                temp_dir=temp_dir,
            )
    except TimeoutError:
        _logger.error("cannot get disk info within 10 seconds")
        raise


def get_system_info():
    return _system_info
