import json
import httpx
import pytest

from crynux_server.relay.exceptions import RelayError
from crynux_server.relay.web_impl import WebRelay
from crynux_server.task.error_report import TaskErrorReport

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


async def test_task_diagnostic_request_contains_signed_fields():
    captured = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json={"message": "success"})

    relay = make_relay(handler)
    report = TaskErrorReport(
        node_address=str(relay.node_address),
        task_id_commitment="0x" + TASK_ID.hex(),
        task_args='{"prompt":"test"}',
        error_type="TaskExecutionError",
        message="worker failed",
        stack_trace="original traceback",
        captured_at=1784851200,
    )
    signed_input = None

    def sign(input, timestamp=None):
        nonlocal signed_input
        signed_input = input
        return 1784851234, "0x" + "01" * 65

    relay.signer.sign = sign
    try:
        await relay.report_task_diagnostic(report)
    finally:
        await relay.client.aclose()

    assert captured is not None
    assert captured.url.path == f"/v2/tasks/{report.task_id_commitment}/node_error"
    body = json.loads(captured.content)
    assert signed_input == {
        "node_address": report.node_address,
        "task_id_commitment": report.task_id_commitment,
        "task_args": report.task_args,
        "error_type": report.error_type,
        "message": report.message,
        "stack_trace": report.stack_trace,
    }
    for field in (
        "node_address",
        "task_id_commitment",
        "task_args",
        "error_type",
        "message",
        "stack_trace",
        "captured_at",
        "timestamp",
        "signature",
    ):
        assert field in body
    assert isinstance(body["captured_at"], int)
    assert "capture_time" not in body


async def test_get_balance_uses_v2_signature_authorization():
    captured = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json={"data": "123"})

    relay = make_relay(handler)
    signed_input = None

    def sign(input, timestamp=None):
        nonlocal signed_input
        signed_input = input
        return 1784851234, "0x" + "01" * 65

    relay.signer.sign = sign
    try:
        balance = await relay.get_balance()
    finally:
        await relay.client.aclose()

    assert balance == 123
    assert captured is not None
    assert captured.url.path == f"/v2/relay_account/{relay.node_address}/balance"
    assert dict(captured.url.params) == {
        "timestamp": "1784851234",
        "signature": "0x" + "01" * 65,
    }
    assert signed_input == {"address": relay.node_address}
