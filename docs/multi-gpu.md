# Multi-GPU Support

## Execution Model

Inference on multi-GPU nodes uses layer sharding: gpt-task loads models with `device_map="auto"`, which places whole layers on the visible GPUs. Layer sharding involves no cross-GPU floating-point reduction, so the computed results are bitwise identical to those of a single-GPU node with the same GPU model, regardless of card count or split points.

Multi-GPU nodes MUST use identical GPU models only. Cards of other models MUST be excluded from both reporting and execution.

## GPU Enumeration and Selection

The node enumerates all NVIDIA GPUs with `nvidia-smi --query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total --format=csv` and parses every data line of the output.

The selection rule is:

1. Group the cards by model name.
2. Select the group with the most cards.
3. Break ties by larger per-card VRAM, then by the group containing the lowest card index.

All cards outside the selected group MUST be excluded. On macOS, selection does not apply and the single Apple GPU is reported as before.

## Reporting

The selected group is aggregated into a single reported GPU:

- Name: the model name unchanged when one card is selected; `<N>x <model>` when N > 1 cards are selected (e.g. `2x NVIDIA GeForce RTX 4090`). A `1x` prefix MUST NOT appear.
- VRAM total and VRAM used: summed over the selected cards.
- Usage: the maximum over the selected cards.

The aggregated name and summed VRAM flow unchanged into the GPU name registered with the relay (`<name>+<platform>`), the node start parameters, and the WebUI `/system` endpoint. The relay treats the aggregate name as a distinct GPU type in its existing matching tuple; no relay-side changes exist.

## Worker Isolation

The reported card set and the executed card set MUST be identical. At worker process spawn, the worker manager enumerates GPUs with the same selection rule and sets `CUDA_VISIBLE_DEVICES` in the worker environment to the comma-joined device UUIDs of the selected group. Cards excluded by the selection rule are invisible to the worker process, so `device_map="auto"` cannot place layers on them.

Device UUIDs are used instead of numeric indices because nvidia-smi numbers devices in PCI bus order while CUDA defaults to fastest-first enumeration, so numeric indices can disagree between the two; `CUDA_VISIBLE_DEVICES` accepts `GPU-<uuid>` values, which are unambiguous.

`CUDA_VISIBLE_DEVICES` is set before `WORKER_`-prefixed overrides from the config `.env` file are applied, so an operator-provided `WORKER_CUDA_VISIBLE_DEVICES` value wins. If GPU enumeration fails at spawn, the worker starts without the variable and sees all GPUs; the failure is logged as a warning.

GPU info is re-read on every node start, and the worker inherits the selection at spawn. Reported name and executed card set are derived from the same selection rule, so they cannot drift apart across restarts or hardware changes.

## Determinism Constraints

- The selected cards MUST be of one identical GPU model; the selection rule enforces this.
- CPU and disk offload MUST NOT occur during inference: CPU kernels differ numerically from GPU kernels. gpt-task enforces this at model load time by passing a `max_memory` map with the CPU budget forced to 0 and providing no offload folder; a model that does not fit the visible GPUs fails to load and the failure is reported as a CUDA out-of-memory error through the established OOM path.
