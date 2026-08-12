from typing import Dict, List, Literal

from anyio import get_cancelled_exc_class, to_thread
from eth_account import Account
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field, Json, SecretStr
from typing_extensions import Annotated

from crynux_server.config import set_privkey
from crynux_server.relay import get_relay
from crynux_server.relay.exceptions import RelayError

from .utils import CommonResponse, http_exception_from_relay_error
from ..depends import AccountInfoDep
from ..account import AccountInfo


router = APIRouter(prefix="/account")


@router.get("", response_model=AccountInfo)
async def get_account_info(*, account_info: AccountInfoDep):
    return account_info


class VestingRecord(BaseModel):
    id: int
    created_at: int
    address: str
    total_amount: str
    start_time: int
    duration_days: int
    type: str
    released_amount: str
    remaining_amount: str
    locked_amount: str
    status: int
    slashed: bool


class VestingListResponse(BaseModel):
    total: int
    vesting_records: List[VestingRecord]


@router.get("/vesting/list", response_model=VestingListResponse)
async def get_vesting_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> VestingListResponse:
    try:
        relay = get_relay()
        data = await relay.get_vesting_records(page=page, page_size=page_size)
        return VestingListResponse.model_validate(data)
    except AssertionError:
        raise HTTPException(400, detail="Private key has not been set.")
    except RelayError as e:
        raise http_exception_from_relay_error(e)


PrivkeyType = Literal["private_key", "keystore"]


class PrivkeyInput(BaseModel):
    type: PrivkeyType
    private_key: str = Field("", pattern=r"^0x[0-9a-fA-F]{64}$")
    keystore: Json[Dict] = dict()
    passphrase: SecretStr = SecretStr("")


@router.put("", response_model=CommonResponse)
async def set_account(input: Annotated[PrivkeyInput, Body()]):
    if input.type == "private_key":
        await set_privkey(input.private_key)
        privkey = input.private_key
    else:
        try:
            privkey = (
                await to_thread.run_sync(
                    Account.decrypt, input.keystore, input.passphrase.get_secret_value()
                )
            ).hex()
        except get_cancelled_exc_class():
            raise
        except Exception as e:
            raise HTTPException(400, str(e))
        await set_privkey(privkey)

    return CommonResponse()


class AccountWithKey(BaseModel):
    address: str
    key: str


@router.post("", response_model=AccountWithKey)
async def create_account():
    acct = Account.create()
    address: str = acct.address
    privkey: str = acct.key.hex()
    await set_privkey(privkey=privkey)

    return AccountWithKey(address=address, key=privkey)
