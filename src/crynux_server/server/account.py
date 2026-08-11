import logging

from anyio import create_task_group, get_cancelled_exc_class, sleep, ExceptionGroup
from pydantic import BaseModel

from crynux_server.config import wait_privkey
from crynux_server.utils import get_address_from_privkey
from crynux_server.contracts import get_contracts
from crynux_server.relay import get_relay

_logger = logging.getLogger(__name__)


class AccountInfo(BaseModel):
    address: str = ""
    balance: str = "0"
    relay_balance: str = "0"
    staking: str = "0"
    delegator_staking: str = "0"
    delegator_share: int = 0
    delegator_num: int = 0
    today_delegator_earnings: str = "0"
    total_delegator_earnings: str = "0"
    is_loaded: bool = False


_account_info = AccountInfo()


async def update_account_info(interval: int):
    privkey = await wait_privkey()
    _account_info.address = get_address_from_privkey(privkey)

    while True:
        try:
            contracts = get_contracts()
            relay = get_relay()

            async def _update_relay_balance():
                relay_balance = await relay.get_balance()
                _account_info.relay_balance = str(relay_balance)
                _logger.debug(f"relay_balance: {_account_info.relay_balance}")

            async def _update_balance():
                balance = await contracts.get_balance(contracts.account)
                _account_info.balance = str(balance)
                _logger.debug(f"balance: {_account_info.balance}")

            async def _update_staking():
                staking_info = await contracts.node_staking_contract.get_staking_info(
                    contracts.account
                )
                _account_info.staking = str(staking_info.staked_balance)
                _logger.debug(f"staking: {_account_info.staking}")

            async def _update_node_info():
                node_info = await relay.node_get_node_info()
                _account_info.delegator_staking = node_info.delegator_staking
                _account_info.delegator_share = node_info.delegator_share
                _account_info.delegator_num = node_info.delegators_num
                _account_info.today_delegator_earnings = (
                    node_info.today_delegator_earnings
                )
                _account_info.total_delegator_earnings = (
                    node_info.total_delegator_earnings
                )
                _logger.debug(f"delegator_staking: {_account_info.delegator_staking}")
                _logger.debug(f"delegator_share: {_account_info.delegator_share}")
                _logger.debug(f"delegator_num: {_account_info.delegator_num}")
                _logger.debug(
                    f"today_delegator_earnings: {_account_info.today_delegator_earnings}"
                )
                _logger.debug(
                    f"total_delegator_earnings: {_account_info.total_delegator_earnings}"
                )

            async with create_task_group() as tg:
                tg.start_soon(_update_relay_balance)
                tg.start_soon(_update_balance)
                tg.start_soon(_update_staking)
                tg.start_soon(_update_node_info)
            _account_info.is_loaded = True
        except AssertionError:
            pass
        except get_cancelled_exc_class():
            _logger.debug("update account info cancelled")
            raise
        except Exception as e:
            _logger.error("update account info error")
            _logger.exception(e)
        except ExceptionGroup as e:
            _logger.error("update account info error")
            _logger.exception(e)
        await sleep(interval)


def get_account_info():
    return _account_info
