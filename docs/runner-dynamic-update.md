# Runner Dynamic Update: What Actually Gets Updated

This document explains how the node's "Runner version" works and what is dynamically updated.

## 1) Where the final Runner version comes from

The final source of truth is the worker package version string:

- `crynux_worker.__version__` in the worker runtime files

Version flow is:

1. The inference worker launcher (`crynux_worker_process.py`) reads version from `crynux_worker.version()` (which returns `__version__`).
2. If patches are found, patch files are applied to local worker runtime files. These patches can update `crynux_worker/__init__.py`, including `__version__`.
3. After patch apply, the launcher restarts the inner worker process.
4. The restarted worker connects to server WebSocket `/manager/v1/worker/` and sends `{"version": "x.y.z", "role": "inference"}` in the first message.
5. Server sets this value into the in-memory `version` of the inference worker manager.
6. WebUI `GET /node/runner/version` reads the inference worker manager's `version` and displays it.

So the version is not persisted in node manager state. It is reported by the currently running inference worker process after restart.

When the node observes that the inference worker reconnected with a new version, it reports the new version to the relay and restarts the download worker process, so both workers run the same patched code. The download worker loads the already-patched `script_dir` at startup; it performs no patching of its own.

## 2) What component performs dynamic update

Dynamic update is implemented by the worker process launcher:

- `crynux-worker/crynux_worker_process.py`

The node starts one launcher process per worker role (inference and download) from `src/crynux_server/worker_manager/manager.py`, passing:

- `CRYNUX_WORKER_PATCH_URL` (from `task_config.worker_patch_url`)
- `cw_worker_role` (the role of the worker process)
- runtime directories and node/relay URLs

The patch update loop runs only in the inference worker launcher, so two processes never patch the same `script_dir` concurrently. The download worker launcher never checks for patches.

Default patch source (from `config/config.yml`) is:

- `https://raw.githubusercontent.com/crynux-ai/crynux-worker/main`

## 3) How update is performed at runtime

Inside `crynux_worker_process.py`, update loop is:

1. Read current worker version (`crynux_worker.__version__`).
2. Fetch `patches.txt`.
3. Keep only versions after current version.
4. Keep only patches with the same **major** version.
5. Fetch each patch file from `patches/<platform>/<version>.patch`.
6. Apply patch text to local files with `whatthepatch`.
7. If any patch is applied, terminate and restart the inner worker process.
8. Repeat every 60 seconds.

Important details:

- Cross-major updates are blocked by design (`major` must match).
- Patch apply is file-level diff patching, not package-manager install.
- After restart, worker reconnects and reports new version; WebUI then shows updated runner version.

## 4) Which files are actually updated

The updater patches files referenced by each `.patch` diff header path.

In other words, it updates files inside the worker runtime package area, not `src/crynux_server` node backend code.

Examples:

- Source mode patch path example:
  - `worker/venv/lib/python3.10/site-packages/crynux_worker/...`
  - `worker/venv/lib/python3.10/site-packages/gpt_task/...`
- Windows packaged patch path example:
  - `crynux_worker_process/_internal/crynux_worker/...`
  - `crynux_worker_process/_internal/gpt_task/...`

This shows that dynamic update can modify:

- worker framework code (`crynux_worker`)
- task implementation packages embedded in worker runtime (for example `gpt_task`)

It does **not** directly patch node server modules under `src/crynux_server`.
