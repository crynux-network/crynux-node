# Node Model Pre-download Flow

This document specifies how `crynux-node` handles relay `DownloadModel` events.

## Overview

`crynux-node` MUST process relay predownload requests through the node manager task system.

The flow has four stages:

1. Receive a `DownloadModel` event from relay.
2. Create a local download task keyed by `{node_address}_{model_id}`.
3. Run model download and report completion to relay.
4. Persist final task state as `Success` or `Failed`.

The node MUST apply exponential backoff retries for download failures and MUST stop retrying after 3 attempts.

## Trigger Point

The event watcher in `NodeManager` MUST subscribe to relay `DownloadModel` events.  
When an event for this node is received, `NodeManager` MUST call `TaskSystem.create_download_task(task_id, task_type, model_id)`.

The task identifier format MUST be:

`{node_address}_{model_id}`

## Download Task Lifecycle

`DownloadTaskRunner` MUST persist and transition download task state in `download_task_states`.

### State Machine

`DownloadTaskStatus` MUST follow this lifecycle:

- `Started`: task created, download not yet completed.
- `Executed`: model downloaded locally, relay report pending.
- `Success`: relay report succeeded and local model cache updated.
- `Failed`: download retries exhausted; task is abandoned.

### Execution Rules

1. If a task already exists in state `Success` or `Failed`, `DownloadTaskRunner.run()` MUST return immediately.
2. In `Started`, the runner MUST execute `run_download_task(...)`.
3. After local download succeeds, the runner MUST set state to `Executed`.
4. In `Executed`, the runner MUST call `relay.node_report_model_downloaded(model_id)`.
5. After reporting succeeds, the runner MUST set state to `Success` and MUST save the model into `download_model_cache`.

## Retry and Backoff Policy

`TaskSystem._run_download_task` MUST use bounded retries with exponential backoff:

- Maximum attempts: `3`
- Backoff type: exponential
- Initial delay: `30s`
- Maximum delay cap: `300s`

If all retry attempts fail, the node MUST mark the task as `Failed` and MUST NOT continue retrying that task instance.

## Re-trigger Behavior

If a new `DownloadModel` event arrives for a task whose persisted state is `Failed`, `TaskSystem.create_download_task` MUST reset the state back to `Started` and enqueue a new execution cycle.

This allows future relay events to re-attempt download after a previous exhausted failure cycle.

## Persistence and Recovery

Download task states MUST be persisted in `download_task_states` through `DownloadTaskStateCache`.

On node restart, `TaskSystem._recover_download_task` MUST only recover tasks in:

- `Started`
- `Executed`

Tasks in `Success` or `Failed` MUST NOT be recovered for automatic execution.

## Relevant Source Files

- `src/crynux_server/node_manager/node_manager.py`: subscribes to `DownloadModel` and creates download tasks
- `src/crynux_server/task/task_system.py`: queueing, retries, backoff, and failed-state handling
- `src/crynux_server/task/task_runner.py`: download runner state transitions and relay reporting
- `src/crynux_server/models/task.py`: `DownloadTaskStatus` definition
- `src/crynux_server/task/state_cache/db_impl.py`: persistent download task state storage
- `src/crynux_server/server/v1/worker.py`: maps worker download errors to `TaskDownloadError`
