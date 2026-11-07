#!/usr/bin/env python3
"""Crynux + Pearl dual mining controller (same container as crynux-node)."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Optional

LOG = logging.getLogger("dual_controller")

CRYNUX_BASE = os.environ.get("DUAL_MINING_CRYNUX_URL", "http://127.0.0.1:7412").rstrip(
    "/"
)
POLL_INTERVAL_SEC = float(os.environ.get("DUAL_MINING_POLL_INTERVAL_SEC", "0.1"))
GPU_INDEX = os.environ.get("DUAL_MINING_GPU_INDEX", "0")
WILDRIG_BIN = os.environ.get("WILDRIG_BIN", "/usr/local/bin/wildrig")
WILDRIG_API_PORT = os.environ.get("WILDRIG_API_PORT", "4068")
WILDRIG_WORKDIR = os.environ.get("WILDRIG_WORKDIR", "/tmp/wildrig")

DEFAULT_POOL = "stratum+tcp://pool.pearlhash.xyz:9000"


def _http_json(method: str, path: str, body: Optional[dict] = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{CRYNUX_BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def get_task_stats() -> dict:
    return _http_json("GET", "/manager/v1/tasks")


def get_dual_status() -> dict:
    return _http_json("GET", "/manager/v1/dual_mining/status")


def open_gate() -> None:
    _http_json("POST", "/manager/v1/dual_mining/gate", {"state": "open"})


def close_gate() -> None:
    _http_json("POST", "/manager/v1/dual_mining/gate", {"state": "closed"})


def unload_worker() -> None:
    _http_json("POST", "/manager/v1/dual_mining/unload")


def free_vram_mib() -> Optional[int]:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                f"--id={GPU_INDEX}",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=10,
        )
        return int(out.strip().splitlines()[0].strip())
    except Exception as e:
        LOG.warning("Failed to read free VRAM: %s", e)
        return None


class PearlMiner(object):
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._started_with: Optional[tuple] = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, dual: dict) -> None:
        address = (dual.get("pearl_address") or "").strip()
        worker = (dual.get("pearl_worker") or "crynux-dual").strip()
        pool = (dual.get("pearl_pool_url") or DEFAULT_POOL).strip()
        kernel = str(dual.get("pearlhash_kernel") or "2")
        key = (address, worker, pool, kernel)
        if self.running:
            if self._started_with == key:
                return
            self.stop()
        if not address:
            LOG.error("dual_mining.pearl_address is empty; cannot start WildRig")
            return
        if not os.path.isfile(WILDRIG_BIN):
            LOG.error("WildRig binary not found: %s", WILDRIG_BIN)
            return
        os.makedirs(WILDRIG_WORKDIR, exist_ok=True)
        user_login = f"{address}.{worker}"
        cmd = [
            WILDRIG_BIN,
            "--algo",
            "pearlhash",
            "--url",
            pool,
            "--user",
            user_login,
            "--pass",
            "x",
            "--pearlhash-kernel",
            kernel,
            "--api-port",
            WILDRIG_API_PORT,
            "--print-time",
            "10",
            "--log-file",
            os.path.join(WILDRIG_WORKDIR, "wildrig.log"),
        ]
        LOG.info("Starting WildRig: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            cwd=WILDRIG_WORKDIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        self._started_with = key

    def stop(self) -> float:
        if self._proc is None:
            return 0.0
        if self._proc.poll() is not None:
            self._proc = None
            self._started_with = None
            return 0.0
        started = time.monotonic()
        LOG.info("Stopping WildRig pid=%s", self._proc.pid)
        self._proc.send_signal(signal.SIGTERM)
        try:
            self._proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            LOG.warning("WildRig did not exit on SIGTERM; killing")
            self._proc.kill()
            self._proc.wait(timeout=10)
        elapsed = time.monotonic() - started
        self._proc = None
        self._started_with = None
        LOG.info("WildRig stopped in %.2fs", elapsed)
        return elapsed


def stop_leftover_wildrig() -> None:
    try:
        out = subprocess.check_output(["pgrep", "-f", WILDRIG_BIN], text=True)
    except subprocess.CalledProcessError:
        return
    except Exception as e:
        LOG.warning("Failed to look for leftover WildRig: %s", e)
        return
    for pid_s in out.split():
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        LOG.info("Stopping leftover WildRig pid=%s", pid)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as e:
            LOG.warning("Failed to stop leftover WildRig pid=%s: %s", pid, e)


def wait_crynux_ready(timeout_sec: float = 600.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            get_dual_status()
            LOG.info("Crynux dual_mining API is reachable")
            return
        except Exception:
            time.sleep(1.0)
    raise RuntimeError("Timed out waiting for Crynux dual_mining API")


def ensure_pearl_for_idle(miner: PearlMiner, dual: dict) -> None:
    threshold = int(dual.get("free_vram_threshold_mib") or 1024)
    free = free_vram_mib()
    if free is None:
        LOG.warning("Skipping Pearl start: free VRAM unknown")
        return
    if free > threshold:
        LOG.info(
            "Free VRAM %s MiB > %s; keeping Crynux worker loaded",
            free,
            threshold,
        )
    else:
        LOG.info(
            "Free VRAM %s MiB <= %s; unloading Crynux worker",
            free,
            threshold,
        )
        unload_worker()
        for _ in range(100):
            status = get_dual_status()
            if not status.get("unloading") and status.get("in_memory_model") is None:
                break
            time.sleep(0.2)
        free_after = free_vram_mib()
        LOG.info("After unload free VRAM=%s MiB", free_after)
        dual = get_dual_status()
    LOG.info("Crynux idle; starting Pearl")
    miner.start(dual)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [dual_controller] %(levelname)s %(message)s",
    )

    wait_crynux_ready()
    stop_leftover_wildrig()
    miner = PearlMiner()
    idle_since: Optional[float] = None
    last_mode: Optional[str] = None
    gate_opened_for_busy = False

    while True:
        try:
            dual = get_dual_status()
            if not dual.get("enabled"):
                if miner.running:
                    miner.stop()
                gate_opened_for_busy = False
                if last_mode != "disabled":
                    LOG.info("dual_mining disabled in config; controller idle")
                    last_mode = "disabled"
                time.sleep(1.0)
                continue

            if not (dual.get("pearl_address") or "").strip():
                if miner.running:
                    miner.stop()
                if last_mode != "no_address":
                    LOG.error(
                        "dual_mining.pearl_address is empty in config.yml; not mining"
                    )
                    last_mode = "no_address"
                time.sleep(1.0)
                continue

            idle_grace = float(dual.get("idle_grace_sec") or 2.0)
            tasks = get_task_stats()
            status = tasks.get("status")
            # Only Crynux inference busy needs the GPU. idle and stopped both mine.
            if status == "running":
                idle_since = None
                if miner.running:
                    miner.stop()
                if not gate_opened_for_busy:
                    open_gate()
                    gate_opened_for_busy = True
                    LOG.info("Opened inference gate for Crynux task")
                last_mode = "busy"
            else:
                if gate_opened_for_busy or dual.get("gate_open"):
                    close_gate()
                    gate_opened_for_busy = False
                now = time.monotonic()
                if idle_since is None:
                    idle_since = now
                if now - idle_since >= idle_grace and not miner.running:
                    ensure_pearl_for_idle(miner, dual)
                    last_mode = f"mining_{status}"
                elif miner.running:
                    last_mode = f"mining_{status}"
                else:
                    last_mode = status
        except urllib.error.URLError as e:
            LOG.warning("Crynux API error: %s", e)
        except Exception:
            LOG.exception("Controller loop error")
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
