
## Document Index

- `task-system-design.md`: The components involved in task handling on the node (reconcile loop, worker manager, relay client, event watcher, worker process), their responsibilities, the background and foreground model-download paths, and all non-protocol failure handling: worker process health and restarts, relay communication failures, and containment of unexpected exceptions.
- `task-lifecycle.md`: The business-level lifecycle of an inference task: foreground download of non-base models before inference, error classification (`TaskInvalid` vs `TaskExecutionError`), the condition-to-action rules the reconcile loop applies at each relay task status, convergence rules, relay-owned task timeout, and the full task flow to terminal statuses.
- `task_error_report.md`: The diagnostic reporting contract for task execution failures: Worker traceback preservation, reasoned cancellation classification, local JSON persistence, automatic and manual reporting, Relay authentication and storage, Admin filtering, and complete error-to-report rules.
- `state-tracking.md`: How the node stays consistent with the relay through two channels: the authoritative current-state query system, including task-scoped model requirements, and the event system for background base-model pre-download commands and display hints, including delivery guarantees, recovery after a node restart, and rules for adding new behavior on either channel.
- `node-slashed.md`: How the node handles a relay `NodeSlashed` event: the local `slashed` state, status synchronization, WebUI behavior, restart and manual start after slash, and task and worker behavior.
- `model_predownload.md`: How the node handles relay `DownloadModel` events as background base-model pre-download tasks: the download task lifecycle and state machine, retry and backoff policy, deadline behavior, re-trigger behavior, reporting, and persistence and recovery.
- `runner-dynamic-update.md`: How the runner version is determined and dynamically updated: the worker patch update loop, which files are patched, and how the updated version is reported to the WebUI.
- `multi-gpu.md`: How the node supports multiple GPUs: the layer-sharding execution model, the identical-model group selection rule, aggregated `<N>x <model>` reporting with summed VRAM, worker isolation via `CUDA_VISIBLE_DEVICES` UUID injection, and the determinism constraints including the CPU/disk offload prohibition.

## Doc Update Requirements

When updating documentation files:

1. Read the entire document first to understand its structure, sections, and flow
2. Find the most appropriate location to integrate new content based on:
   - Logical relationship with existing sections
   - Document flow and narrative
   - Where readers would naturally expect to find the information
3. Integrate new content naturally into existing sections when possible:
   - Add as a paragraph within a relevant section
   - Extend an existing list or table
   - Add as a subsection under an appropriate parent section
   - Distribute across multiple sections if a feature affects different parts of the document
4. Do NOT simply create a new top-level section and place all new content there
5. Only create a new section if the topic is truly distinct from all existing content

Write documentation as a specification.

Documentation MUST state clear, final decisions and requirements.

Documentation MUST NOT include:
- Recommendations or advice.
- Options or alternatives.
- Speculation or uncertainty.
- Future-facing placeholders.

Documentation MUST use definitive language that can be implemented and tested:
- Requirement keywords: MUST, MUST NOT, SHALL, SHOULD. Use SHOULD only when a requirement level is intended.
- Exact behavior, constraints, and interfaces.

## Chat Content Isolation

Documentation MUST be generated from task requirements and authoritative project sources only.
User chat instructions about removing content are editing actions, not document content.
The final document MUST NOT restate removal instructions.
If a content type is removed, it must be absent from the final document.

Example chat cycle:
- AI draft includes setup commands.
- User says remove setup commands and keep only flow.
- Wrong final doc line: This document does not include setup commands.
- Right final doc line: Run the flow in order: prepare environment, start services, execute deposit and withdraw, then verify results.
