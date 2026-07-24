import logging
from typing import Optional

from anyio import to_thread
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from crynux_server.config import (
    ensure_staking_amount,
    get_staking_amount,
    get_config,
    set_staking_amount,
    set_task_error_report_automatic,
)
from crynux_server.contracts import get_contracts
from crynux_server.task import FlushResult, get_task_system

from ..depends import ContractsDep
from .utils import CommonResponse

router = APIRouter(prefix="/settings")
_logger = logging.getLogger(__name__)


class SettingsResponse(BaseModel):
    staking_amount: Optional[int]
    task_error_report_automatic: bool
    pending_task_error_reports: int


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
    try:
        pending = await get_task_system().error_reporter.store.count()
    except AssertionError:
        pending = 0
    return SettingsResponse(
        staking_amount=staking_amount,
        task_error_report_automatic=get_config().task_error_report.automatic,
        pending_task_error_reports=pending,
    )


@router.get("/min-staking-amount", response_model=MinStakingAmountResponse)
async def get_min_staking_amount(*, contracts: ContractsDep):
    min_staking_amount = await contracts.node_staking_contract.get_min_stake_amount()
    return MinStakingAmountResponse(min_staking_amount=str(min_staking_amount))


class SetSettingsInput(BaseModel):
    staking_amount: Optional[int] = None
    task_error_report_automatic: Optional[bool] = None


@router.post("", response_model=CommonResponse)
async def set_settings(input: SetSettingsInput):
    if input.staking_amount is None and input.task_error_report_automatic is None:
        raise HTTPException(status_code=400, detail="No settings were provided")
    if input.staking_amount is not None:
        await to_thread.run_sync(set_staking_amount, input.staking_amount)
    if input.task_error_report_automatic is not None:
        await to_thread.run_sync(
            set_task_error_report_automatic, input.task_error_report_automatic
        )
        try:
            reporter = get_task_system().error_reporter
        except AssertionError:
            pass
        else:
            await reporter.notify()
    return CommonResponse(success=True)


@router.post("/task-error-reports/flush", response_model=FlushResult)
async def flush_task_error_reports():
    try:
        reporter = get_task_system().error_reporter
    except AssertionError as e:
        raise HTTPException(
            status_code=503, detail="Task diagnostic reporter is not available"
        ) from e
    return await reporter.flush()
