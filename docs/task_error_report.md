# Task Error Diagnostic Reporting

## Purpose

The Task error diagnostic reporting system records failures that prevent a Node from completing a Task accepted from Relay. It applies to all Task types, including image generation, language-model, and fine-tuning Tasks.

Diagnostic reporting is separate from the Task protocol:

- A diagnostic report MUST NOT change Task status, QoS, settlement, validation, refund, invalidation, or slashing behavior.
- The existing protocol error report MUST remain limited to `TaskInvalid`.
- All other failures MUST retain their existing safe Relay timeout behavior.

## Error Reporting Rules

The Node MUST report the following cases:

| Error type | Condition | Stack trace content | Task protocol behavior |
|---|---|---|---|
| `TaskInvalid` | Worker returns an error caused by invalid Task arguments | Original Worker traceback | Keep the existing `report_task_error` protocol action and also create a diagnostic report |
| `TaskExecutionError` | Worker returns any other Task execution error | Original Worker traceback | Do not send a protocol error; allow Relay timeout handling |
| `TaskDownloadError` | A foreground model download for the Task fails | Original download Worker traceback and Server exception chain | Do not start Task execution; allow Relay timeout handling |
| `NodeTaskExecutionError` | Node fails while preparing, dispatching, or processing the Task or its result | Original Node traceback | Preserve the existing safe Task failure behavior |
| `WorkerTaskTimeout` | Worker does not return success or error before the Task deadline | Explanation of the timeout and known execution context | Allow Relay timeout handling |
| `WorkerDisconnected` | Worker disconnects before returning success or error | Explanation of the disconnect and known execution context | Allow Relay timeout handling |
| `NodeTaskExecutionInterrupted` | Node Task processing stops because of an internal Node failure while the process can still persist the report | Node failure traceback when available, plus an explanation of the interruption | Allow Relay timeout handling |
| No report | Worker restart caused by runner version synchronization | None | Preserve existing Task handling |
| No report | The entire Node process is forcibly terminated and cannot reliably persist a report | None | Relay closes the unfinished Task through timeout |
| No error | The operator requests a normal Node Stop | None | The Node enters PendingQuit and finishes its current Task before stopping |

Background model pre-download jobs MUST NOT create Task error reports because they do not belong to a Relay Task.

Warnings and error log messages that do not correspond to a raised exception or an interrupted Relay Task MUST NOT create reports.

## Worker Errors

The Worker already returns Task execution exceptions to the Node as structured error results containing the complete traceback. The Node MUST report that traceback directly and MUST NOT parse Worker log files.

When a foreground download fails, the report MUST use the parent Relay Task ID Commitment rather than the download job's local identifier.

## Task Cancellation

`TaskCancelled` is a Node-local signal. Relay does not create it.

The Node MUST preserve the reason whenever an unfinished Task is cancelled locally. It MUST distinguish:

- Worker deadline expiration;
- Worker disconnection;
- Node internal Task processing failure;
- runner version synchronization;
- whole-Node process shutdown.

Except for whole-Node process shutdown and runner version synchronization, an accepted Relay Task that ends without a Worker success or error result MUST create a diagnostic report.

A normal WebUI or manager API Stop MUST NOT cancel the current Task. Relay and Node MUST allow the current Task to finish before the Node leaves.

## Stack Trace

When a real Worker or Node traceback exists, the report MUST preserve it completely.

When no traceback exists, the `stack_trace` field MUST contain a concise explanation stating:

- that no Worker traceback is available;
- why execution ended;
- which component initiated the interruption;
- the Worker role and identity when known;
- whether the Task was queued or running;
- the interruption time and Task deadline;
- whether the deadline had been reached;
- that no Worker result was received.

The explanation MUST NOT fabricate Python stack frames.

## Report Data

Each diagnostic record MUST contain:

- Node Address;
- Task ID Commitment;
- Task Args used for execution;
- error type;
- diagnostic message;
- stack trace or no-traceback explanation;
- selected worker GPU count;
- selected worker GPU model name as reported by the Node aggregated GPU selection, without the executor marker;
- per-card VRAM total of the selected worker GPUs, in MB, as a single integer;
- worker executor mode at capture time: `tensor_parallel` or `device_map`;
- Node capture time.

The GPU count, model, and per-card VRAM MUST come from the same selected identical-model GPU group used for worker isolation. Because the selected cards are of one identical model, one per-card VRAM value describes every card. The executor mode MUST be the Node-effective worker mode resolved at capture time. It MUST NOT attempt to record a per-task TP-to-classic fallback that occurs inside gpt-task.

Each failed execution attempt MUST produce at most one record for one Node and Task ID Commitment.

## Local Persistence

The Node MUST persist reports in a dedicated JSON file under the configured log directory:

`task_error_reports.json`

The Node MUST persist a report regardless of whether automatic reporting is enabled.

Local persistence MUST:

- survive Node restart;
- update the file atomically;
- prevent concurrent writes from corrupting the file;
- deduplicate by Node Address and Task ID Commitment;
- remove a record only after Relay acknowledges it.

## Reporting

The configuration is:

```yaml
task_error_report:
  automatic: false
```

Automatic reporting is disabled by default.

When automatic reporting is enabled, the Node MUST send pending records one at a time. A successful report MUST be removed locally. A failed report MUST remain pending and MUST stop the current reporting pass.

The WebUI Settings page MUST provide:

- a switch for automatic reporting;
- a manual report button;
- clear success and failure feedback;
- the number of records reported and remaining.

Manual reporting MUST work regardless of the automatic setting and MUST use the same acknowledgement and deletion rules.

## Relay Report API

The Node MUST submit reports to:

`POST /v2/tasks/:task_id_commitment/node_error`

The request MUST be authorized by the existing Node address signature mechanism. The signature MUST cover the Node Address, Task ID Commitment, Task Args, error type, diagnostic message, stack trace, GPU count, GPU model, per-card VRAM, and executor mode.

Relay MUST verify:

- the signature is valid;
- the signer matches the submitted Node Address;
- the Node Address matches the Task selected node;
- the Task exists.

Relay MUST accept delayed reports after the Task reaches a terminal status. Duplicate reports for the same Node Address and Task ID Commitment MUST return success without creating duplicate records.

Receiving a diagnostic report MUST NOT change the Task.

## Relay Storage and Admin API

Relay MUST store reports in a dedicated `node_task_errors` table.

The Admin API is:

`GET /v2/admin/node_task_errors`

The API MUST:

- use existing Admin authentication;
- support exact filtering by Node Address;
- support exact filtering by Task ID Commitment;
- allow either filter, both filters, or no filter;
- return paginated results;
- order results from newest to oldest;
- include complete Task Args and stack trace content;
- include GPU count, GPU model, per-card VRAM, and executor mode.

## Docker Build Configuration

The standalone Docker image manual workflow MUST provide an input that controls the default value of `task_error_report.automatic`.

The workflow MUST apply the selected value to the Docker config template before building the image.
