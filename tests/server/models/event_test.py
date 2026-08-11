import json

from crynux_server.models import TaskAbortReason, TaskEndAborted, load_event

ABORT_ISSUER = "0x0000000000000000000000000000000000000001"
TASK_ID_COMMITMENT = "0x" + ("ab" * 32)


def _task_end_aborted_args(abort_reason: int) -> str:
    return json.dumps(
        {
            "task_id_commitment": TASK_ID_COMMITMENT,
            "abort_issuer": ABORT_ISSUER,
            "last_status": 4,
            "abort_reason": abort_reason,
        }
    )


def test_load_event_keeps_known_task_abort_reasons():
    for reason in TaskAbortReason:
        event = load_event(1, "TaskEndAborted", _task_end_aborted_args(reason.value))
        assert isinstance(event, TaskEndAborted)
        assert event.abort_reason == reason


def test_load_event_ignores_unrecognized_task_abort_reasons():
    for reason in (5, 10):
        event = load_event(1, "TaskEndAborted", _task_end_aborted_args(reason))
        assert isinstance(event, TaskEndAborted)
        assert event.abort_reason == TaskAbortReason.NONE
