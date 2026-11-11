import logging
import os
import platform
import sys
from typing import List

_logger = logging.getLogger(__name__)


def _osx_bundle_exe_head() -> List[str]:
    exe = os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.dirname(sys.executable)),
            "Frameworks",
            "crynux_worker_process",
            "crynux_worker_process",
        )
    )
    _logger.debug("Execute Crynux worker from: ", exe)
    return [exe]


def _windows_bundle_exe_head() -> List[str]:
    exe = os.path.abspath(
        os.path.join(
            os.path.dirname(sys.executable),
            "crynux_worker_process",
            "crynux_worker_process.exe",
        )
    )
    _logger.debug("Execute Crynux worker from: ", exe)
    return [exe]


def _linux_bundle_exe_head() -> List[str]:
    exe = os.path.abspath(
        os.path.join(
            os.path.dirname(sys.executable),
            "crynux_worker_process",
            "crynux_worker_process",
        )
    )
    _logger.debug("Execute Crynux worker from: ", exe)
    return [exe]


def worker_venv_executable_dir(script_dir: str) -> str:
    """Directory containing the worker venv interpreter for a script launch.

    Empty for a frozen worker executable and when the worker venv is absent.
    """
    if getattr(sys, "frozen", False) or not script_dir:
        return ""
    worker_venv = os.path.abspath(os.path.join(script_dir, "venv"))
    for executable in (
        os.path.join(worker_venv, "bin", "python"),
        os.path.join(worker_venv, "Scripts", "python.exe"),
    ):
        if os.path.exists(executable):
            return os.path.dirname(executable)
    return ""


def _script_cmd_head(script_dir: str = "") -> List[str]:
    exe_dir = worker_venv_executable_dir(script_dir)
    if not exe_dir:
        exe = "python"
    elif os.path.basename(exe_dir) == "Scripts":
        exe = os.path.join(exe_dir, "python.exe")
    else:
        exe = os.path.join(exe_dir, "python")

    script_file = os.path.abspath(os.path.join(script_dir, "crynux_worker_process.py"))
    return [exe, script_file]


def get_exe_head(script_dir: str = "") -> List[str]:
    if getattr(sys, "frozen", False):
        system_name = platform.system()
        if system_name == "Darwin":
            return _osx_bundle_exe_head()
        elif system_name == "Windows":
            return _windows_bundle_exe_head()
        elif system_name == "Linux":
            return _linux_bundle_exe_head()
        else:
            error = RuntimeError(f"Unsupported platform: {system_name}")
            _logger.error(error)
            raise error

    else:
        return _script_cmd_head(script_dir)
