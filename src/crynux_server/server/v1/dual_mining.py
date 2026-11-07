from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from crynux_server.config import get_config
from crynux_server.dual_mining import get_dual_mining_control
from crynux_server.in_memory_model_tracker import get_in_memory_model_tracker

router = APIRouter(prefix="/dual_mining")


class DualMiningStatus(BaseModel):
    enabled: bool
    gate_open: bool
    unloading: bool
    in_memory_model: Optional[str] = None
    pearl_address: str = ""
    pearl_worker: str = "crynux-dual"
    pearl_pool_url: str = "stratum+tcp://pool.pearlhash.xyz:9000"
    pearlhash_kernel: str = "2"
    free_vram_threshold_mib: int = 1024
    idle_grace_sec: float = 2.0


class GateBody(BaseModel):
    state: Literal["open", "closed"]


def _status() -> DualMiningStatus:
    control = get_dual_mining_control()
    cfg = get_config().dual_mining
    current = get_in_memory_model_tracker().current
    return DualMiningStatus(
        enabled=control.enabled(),
        gate_open=control.gate_open,
        unloading=control.unloading,
        in_memory_model=None if current is None else current.model_cache_hash,
        pearl_address=cfg.pearl_address,
        pearl_worker=cfg.pearl_worker,
        pearl_pool_url=cfg.pearl_pool_url,
        pearlhash_kernel=cfg.pearlhash_kernel,
        free_vram_threshold_mib=cfg.free_vram_threshold_mib,
        idle_grace_sec=cfg.idle_grace_sec,
    )


@router.get("/status", response_model=DualMiningStatus)
async def get_status():
    return _status()


@router.post("/gate", response_model=DualMiningStatus)
async def set_gate(body: GateBody):
    control = get_dual_mining_control()
    if not control.enabled():
        raise HTTPException(status_code=400, detail="dual_mining is not enabled")
    if body.state == "open":
        await control.open_gate()
    else:
        await control.close_gate()
    return _status()


@router.post("/unload", response_model=DualMiningStatus)
async def unload():
    control = get_dual_mining_control()
    if not control.enabled():
        raise HTTPException(status_code=400, detail="dual_mining is not enabled")
    await control.unload()
    return _status()
