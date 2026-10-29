# a2a-control-plane Specification 03: Protocols

**Status:** Draft v0.1.0  
**Scope:** Wire format, transport, delta-state streaming, and adaptive heartbeats.

## 1. Conventions

The key words "MUST", "MUST NOT", "REQUIRED", "SHOULD", "SHOULD NOT", "RECOMMENDED", and "MAY" in this document are to be interpreted as described in BCP 14 ([RFC 2119], [RFC 8174]) when, and only when, they appear in all capitals.

## 2. Transports

| Hop | Transport | Port |
|-----|-----------|------|
| Worker to Aggregator | gRPC (HTTP/2) over mTLS, one long-lived bidirectional stream | 50051 |
| Aggregator to CCR, CCR to Aggregator | gRPC over mTLS for the `Registration`, `Admin` and `ZoneControl` services ([04-control-api](04-control-api.md)); zone state reports travel on the regional bus (Section 9) | 50051 |
| Client to CCR | gRPC over mTLS: `TaskSubmission` (task vouchers) and `Admin` | 50051 |
| Client to Aggregator | gRPC over mTLS: `TaskIntake`, with a CCR voucher ([04-control-api](04-control-api.md) Section 4) | 50051 |
| Aggregator regional bus | NATS, subjects defined in [02-topology](02-topology.md) Section 6 | implementation defined |

- Workers MUST NOT connect to the regional bus directly. The Aggregator MUST map each Worker stream to the Worker's subjects ([02-topology](02-topology.md) Section 6.3).
- All transports MUST satisfy the mTLS requirements of [01-identity](01-identity.md).
- The delivery guarantee each hop needs, and how loss is detected and repaired, are in [05-operations](05-operations.md) Section 2.5.

## 3. Message Encoding

- All payloads MUST be [Protocol Buffers v3] messages defined in [`schemas/`](../schemas/).
- JSON encodings MUST NOT be used on the wire. They MAY be used for diagnostics.
- Implementations MUST ignore unknown fields and MUST preserve them when forwarding.
- Receivers MUST enforce a maximum message size and MUST discard a larger message without parsing it ([05-operations](05-operations.md) Section 5).

## 4. NATS Framing

When a payload is published on the regional bus:

- The subject MUST follow the grammar in [02-topology](02-topology.md) Section 6.1.
- The message body MUST be exactly one serialized protobuf message with no additional framing.
- A message header `a2a-type` MUST carry the fully qualified protobuf message name, for example `a2a.controlplane.v1.AgentStateDelta`.
- Message bodies MUST NOT exceed the configured maximum message size. The default is 64 KiB. Larger payloads SHOULD be referenced by identifier rather than inlined.

## 5. Delta-State Streaming

State is reported as deltas using `AgentStateDelta` ([`schemas/v1/state_delta.proto`](../schemas/v1/state_delta.proto)).

- The first message a Worker sends after admission MUST be a snapshot: `snapshot` set, `changed_capabilities` equal to the admitted capability set ([04-control-api](04-control-api.md) Section 2.5), and `sequence` 1.
- Every later message MUST contain only capabilities that changed, with `snapshot` unset. A capability that was removed MUST be represented by an empty value, and the key MUST be present. A capability therefore cannot have an empty value.
- A Worker MUST NOT send a delta whose `changed_capabilities` is empty unless it is serving as a heartbeat (Section 6).
- `sequence` numbers the Worker's messages within the session and is the only basis for ordering and loss detection. Receivers classify each message and recover from loss as defined in [05-operations](05-operations.md) Section 2.
- `timestamp_ns` is the Unix time in nanoseconds at which the change was observed. It is informational and MUST NOT be used to order or discard messages.
- `zone_id` MUST match the zone in the sender's SPIFFE ID. Receivers MUST reject mismatches.
- `trace_context` carries OpenTelemetry context in the compact binary form: 1 byte version, 16 bytes trace-id, 8 bytes parent span-id, 1 byte trace-flags, totaling 26 bytes. It SHOULD be omitted when there is no active trace.

### 5.1 Aggregator Coalescing

An Aggregator MUST coalesce deltas for the same `agent_id` received within a configurable window before forwarding to the CCR. The window SHOULD default to 1 second. Later values for the same capability key MUST supersede earlier ones. A snapshot from a Worker replaces everything that was pending for it, and later deltas merge into that snapshot, so the forwarded delta keeps `snapshot` set. The forwarded delta carries the highest Worker `sequence` it covers.

### 5.2 Capability Maps

- A capability key MUST be 1 to 64 characters from `A-Z`, `a-z`, `0-9`, `.`, `_` and `-`. A value MUST NOT exceed 256 bytes of UTF-8.
- A Worker MUST NOT advertise more than 64 capabilities.
- Keys that begin with `cp.` are reserved for the control plane. The only reserved key defined in this version is `cp.health`, whose value is `ok` or `fault`. A Worker sets `cp.health` to `fault` to report that it is unhealthy (T7 in [02-topology](02-topology.md)) and sets it to `ok` to report recovery (T8). Any other `cp.` key is invalid.
- A message that breaks these rules is invalid ([05-operations](05-operations.md) Section 2.1) and MUST be discarded and counted.
- The CCR evaluates the requested set against its capability policy at admission, and the Aggregator applies the same policy to every later change. A change the policy denies is invalid and MUST be discarded.
- Other namespaces, such as `a2a.` for capabilities that a bridge to the A2A protocol derives from an Agent Card, are defined by their users and are opaque to the control plane.

## 6. Adaptive Heartbeats

A heartbeat is an `AgentStateDelta` with an empty `changed_capabilities` map and `snapshot` unset. It carries the next `sequence` number like any other message.

- Any message from a Worker counts as a liveness signal, except those that [05-operations](05-operations.md) Section 2.1 excludes. A Worker that has sent a delta within the current heartbeat interval MUST NOT send a separate heartbeat.
- When a Worker has no deltas to send, it MUST send a heartbeat at the current interval.
- The interval starts at 5 seconds (`base_interval`). After each consecutive heartbeat with no intervening state change, the interval MUST double, up to 60 seconds (`max_interval`). A state change MUST reset the interval to `base_interval`.
- The Aggregator MUST declare a Worker `Degraded` (T7 in [02-topology](02-topology.md)) when no message has been received for `miss_multiplier` (default 3) times the current interval. The current interval is the wait that applies after the most recently received message, that is, after the doubling or reset caused by that message.
- `base_interval`, `max_interval`, and the missed-interval multiplier MUST be configurable. The Aggregator MAY push new values to a Worker with a `HeartbeatConfig` control message (Section 8).
- An Aggregator MUST NOT forward heartbeats to the CCR. It MUST report only liveness state changes.

## 7. Task Messages

Tasks and results use the messages in [`schemas/v1/task.proto`](../schemas/v1/task.proto). The `a2a-type` header MUST be `a2a.controlplane.v1.Task` or `a2a.controlplane.v1.TaskResult` respectively.

### 7.1 Dispatch

- An Aggregator MUST publish a `Task` only on the `tasks` subject of a Worker in its own zone that is eligible. A Worker is eligible when it is `Idle` or `Executing` (see [02-topology](02-topology.md) Section 5.3), holds fewer than the maximum number of in-flight tasks ([05-operations](05-operations.md) Section 5), and advertises every entry of `required_capabilities` with exactly the required value.
- The Aggregator MUST select the Worker for a task using only `required_capabilities`, the Worker's state and its load. Among eligible Workers the choice is implementation defined; the Aggregator SHOULD prefer the one with the fewest in-flight tasks.
- If no Worker is eligible, the Aggregator MUST NOT dispatch the task. For a task submitted by a client the outcome is `TASK_STATUS_REJECTED` with error code `no_eligible_worker` ([04-control-api](04-control-api.md) Section 4.6).
- `task_id` MUST satisfy the token grammar of [02-topology](02-topology.md) Section 6.1. For a task submitted by a client the CCR assigns it, and it is unique by construction ([04-control-api](04-control-api.md) Section 4.1). An Aggregator MUST NOT dispatch a `task_id` that is already in flight.
- An Aggregator MUST accept a task from a client only under a valid CCR voucher ([04-control-api](04-control-api.md) Section 4.4).
- `attempt` MUST start at 1 and MUST be incremented when the Aggregator reassigns the task.
- `payload`, `kind`, and `content_type` are opaque to the control plane. Implementations MUST NOT interpret them for routing or authorization decisions.

### 7.2 Results

- A Worker MUST publish exactly one `TaskResult` on its `results` subject for each `Task` it receives, with `task_id` and `attempt` copied from the `Task`.
- A Worker that cannot run a task MUST reply with `TASK_STATUS_REJECTED`. If `deadline_ns` is non-zero and has passed on receipt, the Worker MUST NOT run the task and MUST reply `TASK_STATUS_REJECTED` with error code `deadline_exceeded`. `deadline_ns` is an absolute Unix time in nanoseconds.
- When `status` is not `TASK_STATUS_SUCCEEDED`, `error` MUST be set and `payload` SHOULD be empty. `TaskError.retryable` is a hint; the Aggregator decides whether to reassign.
- An Aggregator MUST ignore a `TaskResult` whose `task_id` is not in flight for that Worker, or whose `attempt` is not the current attempt ([05-operations](05-operations.md) Section 3). Any other `TaskResult` for an in-flight task, whatever its status, completes that task.
- A receiver MUST treat `TASK_STATUS_UNSPECIFIED` and any status it does not recognize as `TASK_STATUS_FAILED` with `retryable` unset.
- When the last in-flight task of a Worker completes, the Worker returns to `Idle` (T6 in [02-topology](02-topology.md)).

### 7.3 Reassignment

An Aggregator MAY reassign a task to another Worker in its zone. It MAY do so when:

- the Worker returned `TASK_STATUS_REJECTED` or `TASK_STATUS_FAILED` with `error.retryable` set; or
- the Worker was removed while the task was in flight (T9 and T10 in [02-topology](02-topology.md)).

When reassigning, the Aggregator:

- MUST NOT reassign a task for which cancellation was requested, or whose `deadline_ns` has passed;
- MUST NOT send a task to a Worker that has already had it, and MUST NOT exceed a configurable maximum number of attempts (default 3);
- MUST choose only among eligible Workers (Section 7.1), and only among those that match `required_capabilities`;
- MUST increment `attempt` and keep every other `Task` field unchanged;
- MUST have at most one Worker whose result it will accept for a given `task_id`, and MUST fence out results from superseded attempts ([05-operations](05-operations.md) Section 3);
- MUST report only the final outcome to the task's originator, not results that led to a reassignment; and
- if it cannot reassign, MUST report the last result, or a failure if the Worker was lost.

### 7.4 Cancellation

- An Aggregator requests cancellation with a `CancelTask` control message (Section 8) to the Worker holding the task.
- A Worker that receives `CancelTask` for an active task MUST stop it and reply with `TASK_STATUS_CANCELLED` with error code `cancelled`. This is the one `TaskResult` for that task (Section 7.2).
- A Worker that receives `CancelTask` for a task it has already completed or does not hold MUST ignore it. A Worker MUST NOT send a result for a cancelled task after its `CANCELLED` result.
- If the Worker is lost before replying, the Aggregator MUST NOT reassign the task.

## 8. Control Messages

Commands to a Worker use `ControlMessage` ([`schemas/v1/control.proto`](../schemas/v1/control.proto)) on the Worker's `control` subject. The `a2a-type` header MUST be `a2a.controlplane.v1.ControlMessage`.

- Only the Aggregator of the Worker's zone MAY publish on a `control` subject ([02-topology](02-topology.md) Section 6.3).
- A Worker MUST ignore a `ControlMessage` whose command it does not recognize.
- `CancelTask`: see Section 7.4.
- `ResyncRequest`: the Aggregator sends it when it detects a gap in a Worker's sequence numbers. The Worker MUST answer with a snapshot as its next state message ([05-operations](05-operations.md) Section 2.2).
- `HeartbeatConfig`: a zero field means that parameter is unchanged. A Worker MUST ignore the message if the resulting `base_interval` would exceed `max_interval`, or if `miss_multiplier` would be below 1. The Worker applies the new values from its next message. The Aggregator MUST apply the same values to its own liveness tracking when it sends the message.
- The commands `drain` and `shutdown` listed in earlier drafts are not defined in this version.

## 9. Zone State Reports

An Aggregator informs the CCR of its zone's state with `ZoneStateReport` ([`schemas/v1/zone_report.proto`](../schemas/v1/zone_report.proto)) on `a2a.zone.<zone_id>.aggregator.state`. The `a2a-type` header MUST be `a2a.controlplane.v1.ZoneStateReport`.

- `zone_id` MUST equal the zone in the subject. The CCR MUST discard a report where they differ, or where a contained delta's `zone_id` differs.
- `instance_id`, `epoch` and `sequence` identify the reporting instance and number its reports. The CCR MUST classify every report with them, and MUST recover from a gap by requesting a snapshot ([05-operations](05-operations.md) Section 2.3). A report with `snapshot` set lists every Worker the instance serves and replaces the CCR's records for that instance's Workers.
- A report MUST change only Workers whose session is owned by the reporting instance. The CCR MUST NOT create a record for a Worker it did not admit.
- `deltas` MUST contain at most one coalesced delta per `agent_id` (Section 5.1) and MUST NOT contain heartbeats.
- `state_changes` lists Worker lifecycle changes in the order they occurred. An Aggregator MUST NOT delay a lifecycle change to wait for the coalescing window, and MAY include pending deltas in the same report.
- A report carrying only deltas MUST be sent when the coalescing window expires.
- The CCR MUST apply a state change only for a registered Worker, MUST ignore one older than the last applied for that Worker, and MUST ignore an unrecognized `WorkerState`. It MUST remove a Worker's record when the state is `WORKER_STATE_UNREGISTERED`.
- Only the Aggregator of a zone MAY publish on its `aggregator.state` subject, and only the CCR MAY subscribe to it ([02-topology](02-topology.md) Section 6.3).

## 10. Versioning

- The schema package is versioned (`a2a.controlplane.v1`). Backward-incompatible changes MUST use a new package and a new directory under `schemas/`.
- Peers advertise supported major versions and optional features during registration and attachment, and use the highest common version ([04-control-api](04-control-api.md) Section 5).
- Within a major version, schemas are append-only: a field MUST NOT be renumbered, retyped or removed. New behavior that older peers cannot ignore MUST be introduced as a negotiated feature.

## 11. Security Considerations

- Task payloads are untrusted input to the Worker and result payloads are untrusted input to the Aggregator.
- A compromised Worker can cause repeated reassignment only up to the maximum number of attempts, and only to Workers in its own zone.
- Without a size cap, an authenticated Worker could exhaust Aggregator memory. Receivers MUST enforce maximum message size limits ([05-operations](05-operations.md) Section 5).
- Adaptive heartbeat backoff reduces idle traffic but lengthens the failure detection time to at most 3 times `max_interval`. Deployments with tighter detection needs MUST lower `max_interval`.
- Trace context is untrusted input and MUST NOT influence authorization.
- Capability values are advertised by the Worker. The CCR's policy and the Aggregator's policy check, not the Worker's claim, decide what a Worker is allowed to advertise (Section 5.2).
- The threat model is in [06-threat-model](06-threat-model.md).

[RFC 2119]: https://www.rfc-editor.org/rfc/rfc2119
[RFC 8174]: https://www.rfc-editor.org/rfc/rfc8174
[Protocol Buffers v3]: https://protobuf.dev/programming-guides/proto3/
