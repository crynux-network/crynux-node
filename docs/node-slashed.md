# Node Slashed Behavior

This document specifies how `crynux-node` handles a Relay `NodeSlashed` event, how the local node state changes, how the WebUI reacts, and how restart and start behave after the local slashed state is set.

## Event Handling

`crynux-node` receives `NodeSlashed` through the Relay event stream. The node event watcher polls `/v1/events` with its own `node_address`.

When the event address matches the local node address, the node manager MUST set local node state to:

| Field | Value |
|-------|-------|
| `status` | `slashed` |
| `message` | `Node is slashed` |

The local `slashed` state MUST be stored in the local node database table `node_states`. It is a node-local UI and node-manager state. It is not a Relay node status and it is not a blockchain status.

The `NodeSlashed` handler MUST NOT submit transactions, stop the worker process, delete local task records, or clear local model cache entries.

## Status Synchronization

The node manager periodically synchronizes node status from Relay by calling `node_get_node_info`.

During this synchronization, the local `slashed` state MUST be treated as equivalent to `stopped` for comparison. If Relay returns a status that maps to `stopped`, the synchronizer MUST keep the local `slashed` state instead of replacing it with `stopped`.

If Relay later returns a status that maps to a different local state, the synchronizer MUST replace the local state with the Relay-derived state.

The auto-stake loop MUST NOT submit an automatic staking update while local node state is `slashed`.

## WebUI Behavior

When local node state is `slashed`, the WebUI MUST show the `Node was slashed` alert.

The WebUI treats `slashed` as a stopped-like state for controls:

| UI behavior | Requirement |
|-------------|-------------|
| Node status card | MUST display the node as stopped-like. |
| Start button | MUST be shown. |
| Start button disabled state | MUST depend on whether the node wallet has enough native CNX balance and current staking to cover configured staking amount plus minimum gas. |
| Stop, Pause, Resume buttons | MUST NOT be shown for `slashed`. |

The slashed alert MUST NOT by itself block start. The backend start flow performs the authoritative checks.

When local node state is `slashed` and the node wallet does not have enough native CNX balance and current staking to cover the configured staking amount plus minimum gas, the WebUI MUST also show the insufficient-token alert.

## Node Restart Behavior

The local `slashed` state is persisted in the node database. A node manager restart overwrites it during startup.

During startup, node manager MUST set local node state to `initializing`. It then checks Relay node status, chain staking state, and node wallet native CNX balance.

If the node wallet does not have enough native CNX balance and current staking to cover the configured staking amount plus minimum gas, node manager MUST set local node state to `stopped` and wait before joining. In this state the `Node was slashed` alert is not shown.

If the node wallet has enough native CNX balance and current staking, node manager MUST continue into the join flow. A successful join MUST set local node state to `running`.

## Manual Start After Slash

Manual start after slash is allowed only when all backend start requirements pass.

The backend start flow MUST require Relay node status to map to local `stopped`. The backend check uses Relay `node_get_node_info`; it does not use the local `slashed` value as the authoritative start precondition.

The backend start flow MUST also require:

1. The previous local transaction state is not pending.
2. The node wallet native CNX balance plus current staking amount is at least the configured staking amount plus `0.001` CNX for gas.
3. The staking transaction succeeds or no staking transaction is required.
4. Relay accepts `node_join`.
5. Relay returns running node status after join.

If any requirement fails, the node MUST remain not running and MUST expose the error through local transaction or node state.

## Task and Worker Behavior

The local `slashed` state makes task statistics report the node as stopped. Task statistics MUST report `stopped` for any node state other than `running`, `pending_pause`, or `pending_stop`.

Receiving `NodeSlashed` MUST NOT immediately stop the local worker process. The node manager remains responsible for worker lifecycle through its normal run and stop paths.

## State Summary

| Node-local state source | Slash behavior |
|-------------------------|----------------|
| Relay event watcher | Sets local state to `slashed` after receiving `NodeSlashed` for the local node address. |
| Local `node_states` table | Persists `slashed` until overwritten by synchronization, restart, manual start, or another node-manager state transition. |
| Status synchronizer | Preserves `slashed` while Relay status maps to `stopped`; replaces it when Relay status maps to a different local state. |
| WebUI | Shows `Node was slashed` only while local state remains `slashed`. |
| Manual start | Allowed by the UI when the wallet has enough funds; accepted by the backend only when Relay status maps to `stopped` and start requirements pass. |
| Node restart | Startup overwrites local `slashed` with `initializing`, then with `stopped` or `running` according to balance and join result. |
