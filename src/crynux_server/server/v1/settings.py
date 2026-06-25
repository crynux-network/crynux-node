import logging
from typing import Optional

from anyio import to_thread
from fastapi import APIRouter
from pydantic import BaseModel

from crynux_server.config import (
    ensure_staking_amount,
    get_staking_amount,
    set_staking_amount,
)
from crynux_server.contracts import get_contracts

from ..depends import ContractsDep
from .utils import CommonResponse

router = APIRouter(prefix="/settings")
_logger = logging.getLogger(__name__)


class SettingsResponse(BaseModel):
    staking_amount: Optional[int]


class MinStakingAmountResponse(BaseModel):
    min_staking_amount: str


@router.get("", response_model=SettingsResponse)
async def get_settings():
    try:
        staking_amount = get_staking_amount()
    except ValueError:
        try:
            contracts = get_contracts()
        except AssertionError:
            staking_amount = None
        else:
            try:
                min_staking_amount = (
                    await contracts.node_staking_contract.get_min_stake_amount()
                )
                staking_amount = await to_thread.run_sync(
                    ensure_staking_amount, min_staking_amount
                )
            except Exception as e:
                _logger.warning(
                    "Cannot initialize staking amount from chain: %s", e, exc_info=True
                )
                staking_amount = None
    return SettingsResponse(staking_amount=staking_amount)


@router.get("/min-staking-amount", response_model=MinStakingAmountResponse)
async def get_min_staking_amount(*, contracts: ContractsDep):
    min_staking_amount = await contracts.node_staking_contract.get_min_stake_amount()
    return MinStakingAmountResponse(min_staking_amount=str(min_staking_amount))


class SetSettingsInput(BaseModel):
    staking_amount: int


@router.post("", response_model=CommonResponse)
async def set_settings(input: SetSettingsInput):
    await to_thread.run_sync(set_staking_amount, input.staking_amount)
    return CommonResponse(success=True)
