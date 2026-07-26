from crynux_server.relay.sign import Signer


def test_sign():
    signer = Signer("0x420fcabfd5dbb55215490693062e6e530840c64de837d071f0d9da21aaac861e")
    timestamp, signature = signer.sign(
        {"task_id": 1},
        timestamp=1692446475
    )

    expected = "0xdd78a14f5dcef6a57c5cfba8466baa1ac0ad2767e52eaf5a409895742e0475b4402acacaed2a2d7f158eac2f39849d653b45f207b0204858114cd38c415de5c700"
    assert signature == expected


def test_node_task_error_ten_field_golden_vector():
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
        "gpu_count": 2,
        "gpu_model": "2x NVIDIA GeForce RTX 4090",
        "gpu_vram_mb": 24564,
        "executor_mode": "tensor_parallel",
    }

    timestamp, signature = signer.sign(input, timestamp=1784851234)

    assert timestamp == 1784851234
    assert signature == (
        "0xaa9547f4d769763dfb39d86f5cd199faa7c899788ab7175a3adc6aa60a32cc1b"
        "528196c2d3a764748c99e977e66baa8706503576de45885215ce10185479528101"
    )
