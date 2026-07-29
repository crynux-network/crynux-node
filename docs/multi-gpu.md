# Multi-GPU Support

## Execution Model

Multi-GPU nodes execute language and vision-language inference in one of two modes:

- Layer sharding (the classic executor): gpt-task loads models with `device_map="auto"`, which places whole layers on the visible GPUs. Layer sharding involves no cross-GPU floating-point reduction, so the computed results are bitwise identical to those of a single-GPU node with the same GPU model, regardless of card count or split points.
- Tensor parallelism (the `tensor_parallel` executor): gpt-task loads models with `tp_plan="auto"`, which shards weight tensors across a persistent NCCL process group with one rank per visible GPU. Each rank runs the full forward pass on its shard and partial results are combined with all-reduce collectives.

Tensor parallel results differ numerically from layer-sharded results because all-reduce changes the floating-point summation order. The two modes MUST therefore never share a relay validation pool; pool separation is enforced through the reported GPU name (see Reporting).

Non-GPT task types always execute on the classic path. Multi-GPU nodes MUST use identical GPU models only. Cards of other models MUST be excluded from both reporting and execution.

## Executor Mode Resolution

The node is the single decision point for the executor mode. The effective mode is `tensor_parallel` when all of the following hold, and `classic` otherwise:

1. The `WORKER_GPT_EXECUTOR` value in the config `.env` file is `tensor_parallel` or unset. Tensor parallelism is the default; the operator opts out by setting `WORKER_GPT_EXECUTOR=classic`.
2. The platform is `docker` or `Linux`.
3. The selected identical-model GPU group has at least 2 cards.

When the effective mode is `tensor_parallel`, the node injects `GPT_EXECUTOR=tensor_parallel` and `GPT_TP_FALLBACK=<task_config.tp_fallback>` into the worker process environment; otherwise the node force-removes both `GPT_EXECUTOR` and `GPT_TP_FALLBACK` from the worker environment regardless of the `.env` contents. The worker obeys `GPT_EXECUTOR` without re-checking the platform: when `GPT_EXECUTOR=tensor_parallel` is present, the worker dispatches LLM inference to `run_task_tp` in gpt-task; when absent, it calls the classic `run_task` unchanged. gpt-task reads `GPT_TP_FALLBACK` directly from the process environment; it does not receive this value through the worker or gpt-task config objects.

### Tensor Parallel Execution Path

The worker MUST dispatch GPT tasks to gpt-task `run_task_tp` when the Node injects `GPT_EXECUTOR=tensor_parallel`. gpt-task MUST own per-task capability resolution, AutoClass selection, plan validation, rank lifecycle, input processing, caching, and classic fallback.

The node-owned `task_config.tp_fallback` value MUST select the fallback policy passed to gpt-task:

- `device_map` (default): fall back to the classic `run_task` path with `device_map="auto"`.
- `reduce_gpus`: permit gpt-task to select a smaller compatible TP world size of at least two; use classic execution when gpt-task finds no compatible size.

`task_config.tp_fallback` is declared in the node `config.yml`. When the effective executor mode is `tensor_parallel`, the node injects it as `GPT_TP_FALLBACK`. `GPT_TP_FALLBACK` has exactly two behaviors: `device_map` and `reduce_gpus`. Values other than `reduce_gpus`, including `classic`, MUST be treated as `device_map`. Nodes in the same TP pool MUST use the same `tp_fallback` value so every node makes the identical choice and results stay consistent across the pool.

Model capability, AutoClass, remote `auto_map`, effective-plan, model-specific dimension, prompt, image, tools, thinking, and verified coverage rules MUST follow the standalone gpt-task `docs/model-compatibility/` specification. The Node MUST NOT maintain or infer model-family allowlists. The Node-owned executor environment and fallback value MUST pass transparently to gpt-task, which MUST make the per-task compatibility decision.

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

When the effective executor mode is `tensor_parallel`, a ` TP` marker is appended to the aggregated name (e.g. `2x NVIDIA GeForce RTX 4090 TP`). The relay groups validation tasks by exact GPU name, so TP nodes form a separate matching pool from non-TP nodes with zero relay changes. The marker is applied through one shared helper at every composition site — the relay-registered name (`<name>+<platform>`, e.g. `2x NVIDIA GeForce RTX 4090 TP+docker`), the node start parameters, and the WebUI `/system` endpoint — so the relay name and the WebUI display can never disagree.

The aggregated name and summed VRAM flow unchanged into these composition sites. The relay treats the aggregate name as a distinct GPU type in its existing matching tuple; no relay-side changes exist.

## Worker Isolation

The reported card set and the executed card set MUST be identical for reporting and worker isolation. At worker process spawn, the worker manager enumerates GPUs with the same selection rule and sets `CUDA_VISIBLE_DEVICES` in the worker environment to the comma-joined device UUIDs of the selected group. Cards excluded by the selection rule are invisible to the worker process, so neither `device_map="auto"` nor the tensor parallel rank group can place computation on cards outside the selected group. Under `tp_fallback: device_map`, the tensor parallel rank count equals the visible GPU count when a task runs under TP. Under `tp_fallback: reduce_gpus`, a task MAY run with a smaller TP world size K that divides the model dimensions; unused visible GPUs remain idle for that task and MUST NOT be used by other tasks.

Device UUIDs are used instead of numeric indices because nvidia-smi numbers devices in PCI bus order while CUDA defaults to fastest-first enumeration, so numeric indices can disagree between the two; `CUDA_VISIBLE_DEVICES` accepts `GPU-<uuid>` values, which are unambiguous.

`CUDA_VISIBLE_DEVICES` is set before `WORKER_`-prefixed overrides from the config `.env` file are applied, so an operator-provided `WORKER_CUDA_VISIBLE_DEVICES` value wins. `GPT_EXECUTOR` and `GPT_TP_FALLBACK` are resolved after the `WORKER_` overrides are applied and are node-owned: the `.env` file cannot inject them directly. If GPU enumeration fails at spawn, the worker starts without `CUDA_VISIBLE_DEVICES` and sees all GPUs, the executor mode resolves to `classic`, and the failure is logged as a warning.

GPU info is re-read on every node start, and the worker inherits the selection at spawn. Reported name, executor mode, and executed card set are derived from the same selection rule, so they cannot drift apart across restarts or hardware changes.

## Determinism Constraints

- The selected cards MUST be of one identical GPU model; the selection rule enforces this.
- TP pool nodes are homogeneous by construction: exact GPU name match implies same model, same count (`Nx`), same platform, and same executor (marker).
- The Node MUST provide the same selected GPU set, executor marker, and fallback value to every task in one validation pool.
- gpt-task backend determinism, offload, rank, collective, and plan-validation requirements MUST follow the standalone gpt-task `docs/model-compatibility/` specification.
