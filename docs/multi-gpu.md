# Multi-GPU Support

## Execution Model

Multi-GPU nodes execute LLM inference in one of two modes:

- Layer sharding (the classic executor): gpt-task loads models with `device_map="auto"`, which places whole layers on the visible GPUs. Layer sharding involves no cross-GPU floating-point reduction, so the computed results are bitwise identical to those of a single-GPU node with the same GPU model, regardless of card count or split points.
- Tensor parallelism (the `tensor_parallel` executor): gpt-task loads models with `tp_plan="auto"`, which shards weight tensors across a persistent NCCL process group with one rank per visible GPU. Each rank runs the full forward pass on its shard and partial results are combined with all-reduce collectives.

Tensor parallel results differ numerically from layer-sharded results because all-reduce changes the floating-point summation order. The two modes MUST therefore never share a relay validation pool; pool separation is enforced through the reported GPU name (see Reporting).

Non-LLM task types always execute on the classic path. Multi-GPU nodes MUST use identical GPU models only. Cards of other models MUST be excluded from both reporting and execution.

## Executor Mode Resolution

The node is the single decision point for the executor mode. The effective mode is `tensor_parallel` when all of the following hold, and `classic` otherwise:

1. The `WORKER_GPT_EXECUTOR` value in the config `.env` file is `tensor_parallel` or unset. Tensor parallelism is the default; the operator opts out by setting `WORKER_GPT_EXECUTOR=classic`.
2. The platform is `docker` or `Linux`.
3. The selected identical-model GPU group has at least 2 cards.

When the effective mode is `tensor_parallel`, the node injects `GPT_EXECUTOR=tensor_parallel` into the worker process environment; otherwise the node force-removes `GPT_EXECUTOR` from the worker environment regardless of the `.env` contents. The worker obeys the environment variable without re-checking the platform: when `GPT_EXECUTOR=tensor_parallel` is present, the worker dispatches LLM inference to `run_task_tp` in gpt-task; when absent, it calls the classic `run_task` unchanged.

### Tensor Parallel Execution Path

`run_task_tp` submits tasks to a lazily-spawned persistent executor. On the first TP task, the executor spawns one rank process per visible GPU, binds the NCCL process group to a free localhost port, and keeps the ranks alive across tasks so per-rank model shards stay cached. If any rank dies, the executor tears down the group, the current task fails with a task execution error, and the group is respawned on the next task.

A task falls back to the classic `run_task` in-process when it cannot run under tensor parallelism. The fallback conditions are: fewer than 2 visible GPUs, `quantize_bits` is set, the messages contain image content, the model config lacks `base_model_tp_plan`, or any TP-sharded weight dimension of the model config (attention heads, key-value heads, MLP intermediate size, MoE expert and shared expert intermediate sizes) is not divisible by the visible GPU count. The fallback decision depends only on the task args, the model config, and the visible GPU count; TP pool nodes have identical GPU counts by construction, so every node in a TP pool makes the identical choice and results stay consistent across the pool.

Model caching is split by execution path: TP tasks cache model shards inside the persistent rank processes, and classic-fallback tasks use the worker-level model cache in the worker process, so consecutive tasks on the same path and model reload nothing. At most one of the two caches holds a model at a time: before a TP task is handed to the rank group, `run_task_tp` clears the worker-level cache; before a classic-fallback task runs, `run_task_tp` tears down the rank group, releasing the VRAM held by the cached shards, and the group is respawned lazily by the next TP task.

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

The reported card set and the executed card set MUST be identical. At worker process spawn, the worker manager enumerates GPUs with the same selection rule and sets `CUDA_VISIBLE_DEVICES` in the worker environment to the comma-joined device UUIDs of the selected group. Cards excluded by the selection rule are invisible to the worker process, so neither `device_map="auto"` nor the tensor parallel rank group can place computation on them. The tensor parallel rank count always equals the visible GPU count, which equals the reported `N`; partial-TP configurations cannot exist.

Device UUIDs are used instead of numeric indices because nvidia-smi numbers devices in PCI bus order while CUDA defaults to fastest-first enumeration, so numeric indices can disagree between the two; `CUDA_VISIBLE_DEVICES` accepts `GPU-<uuid>` values, which are unambiguous.

`CUDA_VISIBLE_DEVICES` is set before `WORKER_`-prefixed overrides from the config `.env` file are applied, so an operator-provided `WORKER_CUDA_VISIBLE_DEVICES` value wins. `GPT_EXECUTOR` is resolved after the `WORKER_` overrides are applied and is node-owned: the `.env` file cannot inject it directly. If GPU enumeration fails at spawn, the worker starts without `CUDA_VISIBLE_DEVICES` and sees all GPUs, the executor mode resolves to `classic`, and the failure is logged as a warning.

GPU info is re-read on every node start, and the worker inherits the selection at spawn. Reported name, executor mode, and executed card set are derived from the same selection rule, so they cannot drift apart across restarts or hardware changes.

## Determinism Constraints

- The selected cards MUST be of one identical GPU model; the selection rule enforces this.
- TP pool nodes are homogeneous by construction: exact GPU name match implies same model, same count (`Nx`), same platform, and same executor (marker).
- CPU and disk offload MUST NOT occur during inference: CPU kernels differ numerically from GPU kernels. The classic path enforces this at model load time by passing a `max_memory` map with the CPU budget forced to 0 and providing no offload folder; a model that does not fit the visible GPUs fails to load and the failure is reported as a CUDA out-of-memory error through the established OOM path. The tensor parallel path has no CPU or disk offload by construction — each rank holds its shard on its own GPU — and a model that does not fit raises CUDA OOM through the same error path.
- The tensor parallel rank processes pin the NCCL environment in code before torch is imported: `NCCL_ALGO=Ring`, `NCCL_PROTO=Simple`, `NCCL_NVLS_ENABLE=0`. These values are not operator-configurable. With the algorithm and protocol fixed and NVLS disabled, NCCL collectives are bitwise run-to-run deterministic for a fixed size and topology, so the reduction order does not depend on the machine's interconnect topology.
