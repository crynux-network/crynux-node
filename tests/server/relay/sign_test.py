from crynux_server.relay.sign import Signer


def test_sign():
    signer = Signer("0x420fcabfd5dbb55215490693062e6e530840c64de837d071f0d9da21aaac861e")
    timestamp, signature = signer.sign(
        {"task_id": 1},
        timestamp=1692446475
    )

    expected = "0xdd78a14f5dcef6a57c5cfba8466baa1ac0ad2767e52eaf5a409895742e0475b4402acacaed2a2d7f158eac2f39849d653b45f207b0204858114cd38c415de5c700"
    assert signature == expected


def test_node_task_error_six_field_golden_vector():
    signer = Signer(
        "0x420fcabfd5dbb55215490693062e6e530840c64de837d071f0d9da21aaac861e"
    )
    input = {
        "node_address": "0xd075aB490857256e6fc85d75d8315e7c9914e008",
        "task_id_commitment": "0xabc123",
        "task_args": "prompt=hello;steps=20",
        "error_type": "TaskExecutionError",
        "message": "worker failed",
        "stack_trace": "Traceback: boom",
    }

    timestamp, signature = signer.sign(input, timestamp=1784851234)

    assert timestamp == 1784851234
    assert signature == (
        "0xd8d68035ca69ed86c90b76e6d44b01cc3ff559d8ab69b338c3f0c3f1a2b9894c"
        "6bc45c82db8f118f2e604adb12624e0ab4c1c7e4b51c08c0d3830719753bc0af00"
    )
