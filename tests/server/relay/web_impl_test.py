import httpx
import pytest

from crynux_server.relay.exceptions import RelayError
from crynux_server.relay.web_impl import WebRelay

PRIVKEY = "0xa627246a109551432ac5db6535566af34fdddfaa11df17b8afd53eb987e209a2"
TASK_ID = bytes([1] * 32)


def make_relay(handler) -> WebRelay:
    relay = WebRelay(base_url="http://testserver", privkey=PRIVKEY)
    relay.client = httpx.AsyncClient(
        base_url="http://testserver", transport=httpx.MockTransport(handler)
    )
    return relay


async def test_http_error_propagates_without_retry():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"message": "internal server error"})

    relay = make_relay(handler)
    try:
        with pytest.raises(RelayError):
            await relay.get_task(TASK_ID)
        assert calls == 1

        with pytest.raises(RelayError):
            await relay.submit_task_score(TASK_ID, bytes([1] * 8))
        assert calls == 2
    finally:
        await relay.client.aclose()


async def test_transport_error_propagates_without_retry():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection failed", request=request)

    relay = make_relay(handler)
    try:
        with pytest.raises(httpx.ConnectError):
            await relay.node_get_current_task()
        assert calls == 1
    finally:
        await relay.client.aclose()
