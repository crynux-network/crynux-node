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

`run_task_tp` submits tasks to a lazily-spawned persistent executor. On the first TP task, the executor spawns one rank process per resolved TP world size, binds the NCCL process group to a free localhost port, and keeps the ranks alive across tasks so per-rank model shards stay cached. If any rank dies, the executor tears down the group, the current task fails with a task execution error, and the group is respawned on the next task. If a later task resolves a different world size, the executor MUST tear down the existing rank group and respawn with the new world size before running that task.

A task falls back to the classic `run_task` in-process when it cannot run under tensor parallelism. The forced classic conditions are: fewer than 2 visible GPUs, `quantize_bits` is set, the complete config has no applicable `AutoModelForImageTextToText` or `AutoModelForCausalLM` mapping, or the text config lacks a non-empty `base_model_tp_plan`. Image content is not a forced-classic condition.

The effective TP plan consists of the mapped model class `_tp_plan`, the text config `base_model_tp_plan`, and every sub-config `base_model_tp_plan`. Before rank processes start, gpt-task MUST validate that the visible GPU count divides every known sharded dimension: hidden size, attention heads, key-value heads, dense MLP intermediate size, MoE expert intermediate size, and shared-expert intermediate size. It MUST also validate vocabulary size when the effective plan shards an embedding or `lm_head` on the vocabulary dimension. A vision config with a non-empty plan MUST validate vision hidden size, attention-head count, and intermediate size, plus every architecture-specific dimension used by nonstandard patch-embedding or adapter entries. An unrecognized nonstandard vision plan MUST fall back to classic execution.

When any required dimension is not divisible by the visible GPU count, the node-owned `task_config.tp_fallback` value selects the fallback:

- `device_map` (default): fall back to the classic `run_task` path with `device_map="auto"`.
- `reduce_gpus`: select the largest K such that `2 <= K < visible GPU count` and every present TP-sharded dimension is divisible by K; run tensor parallelism with world size K on the first K visible GPUs. If no such K exists, fall back to the classic `run_task` path.

`task_config.tp_fallback` is declared in the node `config.yml`. When the effective executor mode is `tensor_parallel`, the node injects it as `GPT_TP_FALLBACK`. `GPT_TP_FALLBACK` has exactly two behaviors: `device_map` and `reduce_gpus`. Values other than `reduce_gpus`, including `classic`, MUST be treated as `device_map`. Nodes in the same TP pool MUST use the same `tp_fallback` value so every node makes the identical choice and results stay consistent across the pool.

For a config mapped by `AutoModelForImageTextToText`, each rank MUST load `AutoProcessor` and `AutoModelForImageTextToText` with `tp_plan="auto"`. Image requests MUST be converted through the shared Hugging Face multimodal chat-template helper, and every returned tensor, including `input_ids`, `attention_mask`, and image tensors, MUST be placed on the current rank device. Text-only requests to the same VLM MUST continue through the existing gpt-task prompt adapter and `processor.tokenizer`, preserving tools, `template_args`, and model-specific template behavior. `tp_plan="auto"` and `device_map="auto"` MUST NOT be passed to the same model load.

A VLM can execute under TP even when its vision tower is not sharded. When `vision_config.base_model_tp_plan` is empty, every rank MUST hold a complete vision tower while the language portion follows its TP plan. The replicated vision weights mean total memory use does not decrease in direct proportion to the TP world size. When the vision config has a plan, Transformers merges the prefixed vision entries into the complete model plan and shards those entries. VLM TP eligibility, language sharding, and vision sharding are therefore separate properties.

Under Transformers 5.14.1, representative Image-Text-to-Text families with a native text TP plan include Qwen2-VL, Qwen2.5-VL, Qwen3-VL-MoE, Qwen3.5, Qwen3.6, Gemma 3, Gemma 3n, Gemma 4, Llama 4, GLM-4V, GLM-4V-MoE, GLM-OCR, Mistral 4, Aria, Ovis2, ERNIE 4.5 VL MoE, DeepSeek-OCR2, InternVL, LLaVA, SmolVLM, PaliGemma, Kimi K2.5, and Cohere2 Vision. Representative mapped families without a native text TP plan include Qwen3-VL dense, the original Idefics architecture, mLlama/Llama 3.2 Vision, BLIP, BLIP-2, Chameleon, Florence-2, and Fuyu; these families MUST use classic `device_map="auto"` execution. These versioned families are coverage examples, not a runtime allowlist. Runtime eligibility MUST be derived dynamically from the loaded config, the applicable AutoModel mapping, and the effective TP plan.

`ernie4_5_vl_moe` is the verified sharded-vision reference. Its vision plan shards `blocks.*.attn.qkv`, `blocks.*.attn.proj`, `blocks.*.mlp.fc1`, and `blocks.*.mlp.fc2`; gpt-task MUST validate its vision `hidden_size`, `num_heads`, and `intermediate_size`. Qwen2-VL, Qwen2.5-VL, and Qwen3.6 have no vision plan and MUST replicate their vision towers.

Qwen3.6-35B-A3B uses the Transformers `qwen3_5_moe` architecture. Its effective plan includes attention, MoE experts, the shared expert, linear attention, and a vocabulary-sharded `lm_head`. Its default `num_key_value_heads=2` permits a TP world size of 2. With more than 2 visible GPUs, `reduce_gpus` MUST select K=2 when all other dimensions permit it; `device_map`, or `reduce_gpus` with no valid K, MUST use classic `run_task`.

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

The reported card set and the executed card set MUST be identical for reporting and worker isolation. At worker process spawn, the worker manager enumerates GPUs with the same selection rule and sets `CUDA_VISIBLE_DEVICES` in the worker environment to the comma-joined device UUIDs of the selected group. Cards excluded by the selection rule are invisible to the worker process, so neither `device_map="auto"` nor the tensor parallel rank group can place computation on cards outside the selected group. Under `tp_fallback: device_map`, the tensor parallel rank count equals the visible GPU count when a task runs under TP. Under `tp_fallback: reduce_gpus`, a task MAY run with a smaller TP world size K that divides the model dimensions; unused visible GPUs remain idle for that task and MUST NOT be used by other tasks.

Device UUIDs are used instead of numeric indices because nvidia-smi numbers devices in PCI bus order while CUDA defaults to fastest-first enumeration, so numeric indices can disagree between the two; `CUDA_VISIBLE_DEVICES` accepts `GPU-<uuid>` values, which are unambiguous.

`CUDA_VISIBLE_DEVICES` is set before `WORKER_`-prefixed overrides from the config `.env` file are applied, so an operator-provided `WORKER_CUDA_VISIBLE_DEVICES` value wins. `GPT_EXECUTOR` and `GPT_TP_FALLBACK` are resolved after the `WORKER_` overrides are applied and are node-owned: the `.env` file cannot inject them directly. If GPU enumeration fails at spawn, the worker starts without `CUDA_VISIBLE_DEVICES` and sees all GPUs, the executor mode resolves to `classic`, and the failure is logged as a warning.

GPU info is re-read on every node start, and the worker inherits the selection at spawn. Reported name, executor mode, and executed card set are derived from the same selection rule, so they cannot drift apart across restarts or hardware changes.

## Determinism Constraints

- The selected cards MUST be of one identical GPU model; the selection rule enforces this.
- TP pool nodes are homogeneous by construction: exact GPU name match implies same model, same count (`Nx`), same platform, and same executor (marker).
- CPU and disk offload MUST NOT occur during inference: CPU kernels differ numerically from GPU kernels. The classic path enforces this at model load time by passing a `max_memory` map with the CPU budget forced to 0 and providing no offload folder; a model that does not fit the visible GPUs fails to load and the failure is reported as a CUDA out-of-memory error through the established OOM path. The tensor parallel path has no CPU or disk offload by construction — each rank holds its shard on its own GPU — and a model that does not fit raises CUDA OOM through the same error path.
- The tensor parallel rank processes pin the NCCL environment in code before torch is imported: `NCCL_ALGO=Ring`, `NCCL_PROTO=Simple`, `NCCL_NVLS_ENABLE=0`. These values are not operator-configurable. With the algorithm and protocol fixed and NVLS disabled, NCCL collectives are bitwise run-to-run deterministic for a fixed size and topology, so the reduction order does not depend on the machine's interconnect topology.
- Static config and TP-plan validation proves only that every known sharded dimension is compatible with the selected world size. It does not bypass runtime correctness requirements in Transformers DTensor implementations. Unsupported or incomplete nonstandard vision-plan validation MUST select classic execution.
