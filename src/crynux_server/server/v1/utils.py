import logging
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel

from crynux_server.relay.exceptions import RelayError

_logger = logging.getLogger(__name__)


class CommonResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None


def http_exception_from_relay_error(e: RelayError) -> HTTPException:
    _logger.error("relay request error: %s", e)
    _logger.exception(e)
    return HTTPException(status_code=e.status_code, detail=e.message)
