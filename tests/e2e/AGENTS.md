## Build the Docker image for E2E testing

- Goal: Build the local image `crynux-node:e2e` using the e2e config template.

- Workflow:
  1. Ensure the current working directory is the repository root.
  2. Choose one target network: `near` or `dymension`.
  3. Copy the node config template by network:
     - If target network is `near`, copy `tests/e2e/config.yml.e2e_near` to `build/docker/config.yml.example`.
     - If target network is `dymension`, copy `tests/e2e/config.yml.e2e_dymension` to `build/docker/config.yml.example`.
  4. Copy the WebUI config by network:
     - If target network is `near`, copy `src/webui/src/config.near.json` to `src/webui/src/config.json`.
     - If target network is `dymension`, copy `src/webui/src/config.dymension.json` to `src/webui/src/config.json`.
  5. Build the image from `build/docker/Dockerfile` with tag `crynux-node:e2e`.
  6. Verify `crynux-node:e2e` exists locally.

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
