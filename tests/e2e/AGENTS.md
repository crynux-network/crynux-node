## Build the Docker image for E2E testing

- Goal: Build the local image `crynux-node:e2e` using the e2e config template.

- Workflow:
  1. Ensure the current working directory is the repository root.
  2. Use target network `base` (`crynux-on-base-sepolia`).
  3. Copy `tests/e2e/config.yml.e2e_base` to `build/docker/config.yml.example`.
  4. Copy `src/webui/src/config.base.json` to `src/webui/src/config.json`.
  5. Ensure `build/docker/Dockerfile` pins a published `ghcr.io/crynux-network/crynux-node-base:<commit>` in its worker `FROM` line.
  6. Build with `docker build -t crynux-node:e2e -f build/docker/Dockerfile .`.
  7. Verify `crynux-node:e2e` exists locally.

- After rebuilding base via the manual GitHub Action, update that `FROM` tag to the new commit.

- Contract addresses in `tests/e2e/config.yml.e2e_base` MUST match `crynux-contracts/deployments/primary/testnet/crynux-on-base-sepolia/contracts.json` `nodeContracts`.
- Node `preloaded_models` in that config MUST be empty. Base models for e2e MUST come from Relay `DownloadModel` events (`required_models_by_vram` and demand-driven distribution).

## Prepare the mount folder for the Docker image

- Goal: Prepare node host-side files in a Docker mount workspace for local e2e network runs.

- Workspace rule:
  - Choose one mount workspace root directory and use it consistently in compose volume mappings.
  - Reuse existing files in that workspace when possible instead of recreating everything.

- Required node structure:

```text
<mount-root>/
  config/
```

## Prepare the node wallet account

- Goal: Prepare the node wallet private key file used by the node container.

- Required wallet file:

```text
<mount-root>/config/private_key.txt
```

- Wallet account requirements:
  - `NODE_PRIVATE_KEY` must be provided as a single-line private key value with `0x` prefix.
  - The key material must be persisted at `<mount-root>/config/private_key.txt` with no trailing whitespace.


## Top up the node wallet account

The balance of the node wallet account must be greater than `500 CNX` to cover the minimum staking amount and node join tx gas fee.

## Heartbeat model and quantization coverage

Bridge drives these cases through `heartbeat_tasks.json`. Pass means at least one task for that row reaches Relay `TaskEndSuccess` / Bridge `InferenceTaskResultDownloaded` on a clean `crynux-node:e2e` image with no in-container package installs.

Quantization source:

- TaskArgs: heartbeat row sets `quantization_config`; the Hub checkpoint MUST NOT embed its own `quantization_config`.
- Embedded: heartbeat row MUST omit `quantization_config`; the Hub checkpoint owns the method.

Do not treat Hub model names as the method. Example: `rtj1/Qwen2.5-0.5B-AWQ-FP8-Dynamic` embeds `quant_method: compressed-tensors`, not TaskArgs `fp8` and not AWQ. TaskArgs `quant_method: fp8` is Transformers fine-grained FP8 and is a different path.

### Baseline (required for every e2e run)

These match `crynux-bridge/tests/e2e/heartbeat_tasks.e2e.json`.

| # | Type | Model | Quantization | Source | Notes |
|---|------|-------|--------------|--------|-------|
| B1 | sd | `Qwen/Qwen-Image-2.1` | BitsAndBytes NF4 on `transformer` + `text_encoder` | TaskArgs | Same `quant_kwargs` as Bridge e2e JSON |
| B2 | llm | `Qwen/Qwen3.5-27B` | BitsAndBytes NF4 | TaskArgs | Same `quant_kwargs` as Bridge e2e JSON |

Node `preloaded_models` in `tests/e2e/config.yml.e2e_base` MUST be empty. After join, the node receives base models through Relay `DownloadModel` events. Heartbeat models above are the pass criteria.

### Extended LLM coverage (Docker / Linux CUDA node)

Run after baseline passes. Prefer one active heartbeat model group at a time on a single node so in-memory affinity does not starve other rows.

| # | Type | Model | Quantization | Source | Notes |
|---|------|-------|--------------|--------|-------|
| E1 | llm | `Qwen/Qwen3-8B` | BitsAndBytes NF4 | TaskArgs | Same NF4 `quant_kwargs` as B2 |
| E2 | llm | `Qwen/Qwen3-8B` | BitsAndBytes INT8 | TaskArgs | `load_in_8bit: true` |
| E3 | llm | `Qwen/Qwen2.5-0.5B-Instruct` | none | none | Plain bf16 / auto dtype; no `quantization_config` |
| E4 | llm | `Qwen/Qwen2.5-7B-Instruct` | fine-grained FP8 | TaskArgs | `quant_method: fp8` with `activation_scheme: dynamic`, `weight_block_size: [128, 128]` |
| E5 | llm | `Qwen/Qwen2.5-0.5B-Instruct-AWQ` | AWQ INT4 | Embedded | Linux/Docker only; omit TaskArgs `quantization_config` |
| E6 | llm | `Qwen/Qwen2.5-0.5B-Instruct-GPTQ-Int4` | GPTQ INT4 | Embedded | Linux/Docker only; omit TaskArgs `quantization_config` |
| E7 | llm | `Qwen/Qwen2.5-VL-3B-Instruct` | none | none | Multimodal; heartbeat prompt MUST include an image input |

### Embedded compressed-tensors coverage

Hub checkpoints with `quantization_config.quant_method: compressed-tensors`. Heartbeat MUST omit TaskArgs `quantization_config` (same rule as AWQ/GPTQ). The worker image MUST include `compressed-tensors` (pinned in gpt-task `requirements_cuda.txt`). Classic single-GPU FP8-dynamic smoke (C1 or C2) is the primary pass criterion after a clean `crynux-node:e2e` rebuild.

Primary e2e candidates (fit a single 24 GB node more easily):

| # | Type | Model | Scheme (from Hub) | Notes |
|---|------|-------|-------------------|-------|
| C1 | llm | `rtj1/Qwen2.5-0.5B-AWQ-FP8-Dynamic` | FP8 W8A8 dynamic (`float-quantized`) | Small smoke; name contains AWQ but method is compressed-tensors |
| C2 | llm | `RedHatAI/Llama-3.2-1B-Instruct-FP8-dynamic` | FP8 W8A8 dynamic | Small Red Hat AI production-style checkpoint |
| C3 | llm | `RedHatAI/Llama-3.2-3B-Instruct-FP8-dynamic` | FP8 W8A8 dynamic | High Hub download among RedHatAI FP8 text models |
| C4 | llm | `RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic` | FP8 W8A8 dynamic | Common Qwen FP8 size for 24 GB |
| C5 | llm | `RedHatAI/Meta-Llama-3.1-8B-Instruct-FP8` | FP8 | Alternate 8B FP8 checkpoint |
| C6 | llm | `RedHatAI/Qwen2.5-VL-7B-Instruct-FP8-Dynamic` | FP8 W8A8 dynamic | Multimodal; prompt MUST include an image |

Mainstream larger checkpoints (document for later smoke; not default single-node heartbeat):

| # | Type | Model | Scheme | Notes |
|---|------|-------|--------|-------|
| C7 | llm | `RedHatAI/Qwen3.5-9B-FP8-dynamic` | FP8 dynamic | Mid-size Qwen3.5 FP8 |
| C8 | llm | `RedHatAI/Qwen3-32B-FP8-dynamic` | FP8 dynamic | Large FP8; needs enough VRAM or multi-GPU |
| C9 | llm | `RedHatAI/Meta-Llama-3.1-70B-Instruct-FP8` | FP8 | Large; multi-GPU / high VRAM |
| C10 | llm | `RedHatAI/Llama-3.3-70B-Instruct-FP8-dynamic` | FP8 dynamic | Large; multi-GPU / high VRAM |
| C11 | llm | `RedHatAI/Qwen3.6-35B-A3B-NVFP4` | NVFP4 | Very high Hub traffic; may need newer GPU / kernel support |
| C12 | llm | `RedHatAI/Qwen3-32B-NVFP4` | NVFP4 | Same class as C11 |
| C13 | llm | `RedHatAI/Qwen2.5-1.5B-quantized.w8a8` | INT8 W8A8 | compressed-tensors INT8, not FP8 |

NVFP4 rows are listed because they are widely downloaded under the same Hub method. They MAY fail on current worker GPUs until platform and kernel requirements are confirmed; record the failure mode rather than treating the row as required pass.

### Out of scope for this e2e checklist

| Item | Reason |
|------|--------|
| Full-size AWQ/GPTQ samples such as `Qwen2.5-32B-Instruct-AWQ` | Valid for gpt-task Linux smoke; too large for the default single-node 24 GB e2e heartbeat loop |
| TaskArgs `quant_method: compressed-tensors` | Not a TaskArgs adapter; configuration belongs to the checkpoint, same as AWQ/GPTQ |

### Pass / fail recording

For each row, record: model id, quantization method and source, task id commitment, final Relay status, and whether the image was an unmodified `crynux-node:e2e` build.
