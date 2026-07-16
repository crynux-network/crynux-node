# State Tracking: Query System and Event System

The node tracks the state of itself and its tasks through two channels: the relay current-state query system and the relay event system. This document specifies how each channel works, what each channel is used for, and the rules that keep the two channels from being mixed incorrectly.

## 1) Design Principle

- The **query system** is the authoritative channel. Every decision the node makes MUST be derivable from the current state returned by relay query APIs plus locally persisted artifacts. Given any starting point, the node reaches the correct behavior by reading the current state and acting on it.
- The **event system** carries only flows that have no query equivalent (background base-model pre-download commands) and display hints. No correctness-critical inference task behavior depends on event delivery.
- When adding a new behavior, the default channel is the query system. The event system is used only when the relay offers no current-state query for the fact in question, and only for behaviors that remain acceptable when events are lost.

## 2) Query System Mechanics

The query system consists of relay APIs that return current state, polled or called on demand:

- **Node's current task**: `GET /v1/node/:address/task` returns the relay's current-task pointer for the node. The reconcile loop polls this every second; it is the single driver of all inference task behavior, including task discovery. The relay sets the pointer in the same transaction that dispatches a task, and clears it when the task ends on the relay.
- **Task status**: `GET /v1/inference_tasks/:task_id_commitment`. Fetched by the reconcile loop each cycle for the current task, and once for a previously tracked task the pointer no longer refers to (moved to another task or cleared), to close it locally.
- **Task model identifiers**: the `model_ids` field of the authoritative task record returned by the inference-task query. Before inference, the reconciler MUST derive all non-base identifiers from this field and MUST dispatch them sequentially as task-scoped foreground downloads. This correctness-critical preparation MUST NOT depend on a `DownloadModel` event or the local downloaded-model reporting cache.
- **Node status and scores**: `GET` node info. Polled every 5 seconds by the node state manager (`start_sync_node_status` in `src/crynux_server/node_manager/state_manager.py`) to synchronize the node status, QoS score, staking score, and selection probability weight into the local state cache.

A failed query ends the current poll cycle and the next cycle repeats it; query failures never terminate a polling loop and never drive an action from stale data.

## 3) Event System Mechanics

### Relay side

- The relay persists every event as a row in the `events` table with an auto-incrementing `id`, an `event_type`, a `node_address`, a `task_id_commitment`, and a JSON `args` payload.
- Events are exposed through `GET /v1/events` with cursor-based pagination: the caller passes a `start` event id and receives events with `id > start` in id order, optionally filtered by `node_address`, `event_type`, and `task_id_commitment`.

### Node side

The `EventWatcher` (`src/crynux_server/watcher/watcher.py`) implements the consumer:

- On startup, the cursor is initialized to the relay's current event id for the node's address. Events emitted before startup are never delivered.
- The watcher fetches events every second, filtered by the node's own address, and advances the in-memory cursor to the last delivered event id. The cursor is not persisted.
- Fetched events are dispatched to registered event filters. Each event is processed by all matching filters before the next event is processed. A callback exception is logged and swallowed; it does not stop the watcher.
- If the watcher loop fails, the node manager restarts it with the same watcher instance, preserving the cursor. Within one node process lifetime, no event in the cursor range is skipped.

### Delivery guarantees

- While the node process runs: ordered, at-least-once delivery of all events for the node's address.
- Across node restarts: no delivery. All events emitted while the node was offline are silently skipped.

## 4) Division of Responsibilities

### Consumed events and their actions

- `DownloadModel`: create a persistent background download task for a base model. This is the only channel for background base-model pre-download commands because the relay offers no query for pending pre-downloads. These downloads are a best-effort locality optimization, so commands lost while the node is offline are accepted losses. Download task creation and worker execution MUST be idempotent against duplicate delivery.
- `NodeKickedOut`, `NodeSlashed`: update the local node status display. Display-only; the authoritative node status comes from the node info poll.

No other event type is consumed. `TaskStarted` is not consumed: task discovery is the reconcile loop's current-task poll, which has the same one-second latency. Event types defined in `src/crynux_server/models/event.py` without a subscriber exist for schema completeness and MUST NOT be relied upon without applying the rules in this document.

### Decisions owned by the query system

- All inference task behavior: discovery, task-scoped download of non-base models, inference execution, error report, score submission, result upload, and local closure — every action of the reconcile loop.
- Node status, scores, and staking state.

### Decisions owned by neither channel

- Worker process health and restart. The worker manager decides restarts exclusively from worker results and registered execution deadlines. The download manager registers deadlines only for foreground task-scoped jobs; background event-driven jobs have no deadline. See `docs/task-system-design.md`.

## 5) Recovery Semantics

Recovery after a node process restart is the reconcile loop's normal operation, not a separate flow:

1. The local task record (local database) holds the persisted artifacts of every known task, written on every change: status, result files, score, checkpoint, and result-uploaded marker.
2. The first reconcile cycle after startup is identical to every other cycle: it queries the node's current task and derives the action from the fresh status plus the persisted artifacts. A valid persisted score means the worker phase is complete and MUST NOT be re-run; a non-terminal task without a score repeats its task-scoped non-base-model downloads and inference on the new worker processes, as permitted by the one-shot rule in `docs/task-lifecycle.md`. The server MUST dispatch the downloads without inspecting the Hugging Face cache and MUST rely on worker idempotence.
3. The event cursor restarts at the current event id. Recovery MUST NOT expect any event from before the restart.

Persistent recovery of a background base-model download is independent of inference recovery and follows `docs/model_predownload.md`. Foreground task-scoped downloads MUST NOT be persisted or recovered as background download tasks; they are re-derived from the current authoritative `task.model_ids`.

## 6) Interaction Rules

- Both channels write to the local node state cache. The event callbacks for `NodeKickedOut` and `NodeSlashed` set display statuses that the 5-second node status poll would otherwise overwrite; the poll treats these two statuses as equivalent to `Stopped` when comparing against the remote status, so the display status persists until the remote status actually changes.
- An event and a poll may deliver the same fact twice in any order. Every consumer MUST be idempotent against duplicate delivery of the same state change.
- Event callbacks MUST be short and non-blocking with respect to the watcher loop; long-running work triggered by an event MUST be spawned into the owning component (background download task creation registers the task and returns).

## 7) Rules for New Behavior

- A new behavior reacting to relay-side changes MUST first be implemented against the query system: read the current state, act, and persist local progress so the action is resumable.
- An event subscription MAY be added only for facts the query system does not expose, and only when the triggered action is idempotent and the behavior remains acceptable with the event never delivered.
- A new behavior MUST NOT read the event stream as a source of history, because the stream is not replayed across restarts.
