# a2a-control-plane Specification 05: Operations

**Status:** Draft v0.1.0  
**Scope:** Defaults, state consistency and recovery, task ownership and fencing, high availability, limits, reconnection, observability, and conformance.

## 1. Conventions

The key words "MUST", "MUST NOT", "REQUIRED", "SHOULD", "SHOULD NOT", "RECOMMENDED", and "MAY" in this document are to be interpreted as described in BCP 14 ([RFC 2119], [RFC 8174]) when, and only when, they appear in all capitals.

### 1.1 Defaults

Every value below MUST be configurable. Where the table says "default", an implementation MUST use that value unless configured otherwise.

| Parameter | Default | Defined in |
|-----------|---------|------------|
| Challenge timeout | 10 s | [04-control-api](04-control-api.md) Section 2.4 |
| Registration timeout (admission to first snapshot) | 30 s | [04-control-api](04-control-api.md) Section 2.5 |
| Recovery grace period | 120 s | [02-topology](02-topology.md) Section 5.3 |
| Heartbeat `base_interval` / `max_interval` | 5 s / 60 s | [03-protocols](03-protocols.md) Section 6 |
| Missed-interval multiplier | 3 | [03-protocols](03-protocols.md) Section 6 |
| Coalescing window | 1 s | [03-protocols](03-protocols.md) Section 5.1 |
| Maximum task attempts | 3 | [03-protocols](03-protocols.md) Section 7.3 |
| Maximum in-flight tasks per Worker | 16 | Section 5 |
| Maximum message size | 64 KiB | Section 5 |
| Capabilities per Worker / key length / value length | 64 / 64 characters / 256 bytes | [03-protocols](03-protocols.md) Section 5.2 |
| Aggregator attachment timeout | 30 s | [04-control-api](04-control-api.md) Section 3 |
| Task voucher lifetime | 30 s | [04-control-api](04-control-api.md) Section 4.1 |
| Voucher clock skew tolerance | 5 s | [04-control-api](04-control-api.md) Section 4.4 |
| Reconnect backoff (initial, factor, maximum) | 1 s, 2, 60 s | Section 6 |

## 2. State Consistency and Recovery

The control plane reports state as deltas. A delta stream is only correct if no message is lost, so every hop that can lose a message has a sequence number to detect the loss and a snapshot to repair it.

### 2.1 Worker Session Sequences

- A Worker numbers the state messages it sends within a session, starting at 1 for the first message after admission. Every message it sends on its `state` subject, heartbeats included, increments the number by exactly 1. A new session starts again at 1.
- The Aggregator keeps the highest sequence it applied for the session and classifies each message:

| Class | Condition | Action |
|-------|-----------|--------|
| Applied | The sequence is last + 1, or the message is a snapshot with a sequence above last. | Apply it. |
| Stale | The sequence is not above last. | Discard it without effect. |
| Gap | The sequence is above last + 1 and the message is not a snapshot. | Do not apply it. Start recovery (Section 2.2). |
| Invalid | The sequence is 0, or the content breaks a limit, a reserved key rule or the capability policy. | Discard the content, count it, and, unless the sequence was 0, treat the sequence as consumed. |

- A message that is discarded as invalid consumes its sequence number, so that a Worker that sends bad content does not also cause a resync on every following message.
- `timestamp_ns` is the Unix time in nanoseconds at which the change was observed. It is informational. Receivers MUST NOT use it to order or discard messages.
- Any authenticated message from the Worker MUST count as a liveness signal, whatever its class, except a stale message and a message discarded for its size.

### 2.2 Worker Resync

- On a gap the Aggregator MUST send a `ResyncRequest` on the Worker's `control` subject. It MUST send one request per gap: while a request is outstanding, further gaps MUST NOT cause more requests.
- While the request is outstanding the Aggregator MUST NOT apply non-snapshot messages from that Worker.
- A Worker that receives a `ResyncRequest` MUST send a snapshot as its next state message: an `AgentStateDelta` with `snapshot` set whose `changed_capabilities` is the complete capability set, and the next sequence number.
- A snapshot replaces the Aggregator's view of the Worker's capabilities. Applying it ends the outstanding request.
- A message that the Aggregator discards for exceeding the size limit ([Section 5](#5-limits-and-backpressure)) is a lost message. The gap it leaves is repaired by this procedure.

### 2.3 Zone Reports and CCR Recovery

An Aggregator instance reports to the CCR with `ZoneStateReport` ([03-protocols](03-protocols.md) Section 9).

- Every report carries the instance's `instance_id`, the `epoch` granted by the CCR, and a `sequence` that starts at 1 for each epoch and increases by exactly 1 per report.
- The CCR keeps the highest sequence it accepted for each (instance, epoch) and classifies reports as follows:

| Condition | Action |
|-----------|--------|
| `instance_id` unknown, or `epoch` above the granted epoch | Discard and count. |
| `epoch` below the granted epoch | Stale: discard and count. |
| `sequence` not above the last accepted | Stale: discard and count. |
| `sequence` above last + 1, and not a snapshot | **Gap.** Do not apply. Request a snapshot. |
| Otherwise | Accept and apply. |

- To recover from a gap the CCR MUST call `ZoneControl.RequestZoneSnapshot` on the instance and apply the response as an accepted report. The response is a snapshot report and consumes the instance's next sequence number, so later reports continue without a further gap.
- A snapshot report lists **every** Worker the instance serves: one delta with `snapshot` set, carrying the Worker's complete capability set (omitted only for a Worker that has not yet sent its first snapshot), and one state change with the Worker's current state. The instance MUST discard any pending deltas and state changes that the snapshot makes redundant.
- On applying a snapshot the CCR MUST replace its record of each listed Worker and MUST remove every Worker owned by that instance that is not listed. A snapshot MUST NOT create a record for a Worker the CCR did not admit.
- An instance MUST send a snapshot report as its first report after `Attach`. This recovers the CCR after the instance restarts, after the CCR restarts, and after a CCR failover.
- A new Primary MUST request a snapshot from every attached instance before it treats zone state as current ([Section 4.1](#41-central-cluster-registry)).
- A report MUST change only Workers whose session is owned by the reporting instance. The CCR MUST discard the part of a report that concerns any other Worker and MUST count it.
- A state change older than the last applied for that Worker MUST be ignored, as in [03-protocols](03-protocols.md) Section 9. A snapshot's state changes are applied unconditionally.

### 2.4 Recovery Modes

Recovery is negotiated at attachment ([04-control-api](04-control-api.md) Section 5), so a deployment can use a durable bus where it has one without making the protocol depend on it.

| Feature | Mechanism | Requirement |
|---------|-----------|-------------|
| `recovery.snapshot` | Gap detection by sequence numbers; repair by snapshot (Sections 2.1 to 2.3). | REQUIRED. Always available and always the fallback. |
| `recovery.replay` | The regional bus is durable (for example NATS JetStream). The CCR reads `aggregator.state` through a durable consumer with explicit acknowledgement and receives missed reports in order. | OPTIONAL. Selected only when the CCR and the instance both advertise it. |

- With `recovery.replay` the sequence numbers, the epoch and the classification of Section 2.3 still apply. Replay makes gaps rare; it does not replace gap detection.
- If replay cannot close a gap (for example because the stream's retention has passed), the CCR MUST fall back to a snapshot.
- Replay MUST NOT widen authorization. Only the CCR MAY consume the stream, and only an Aggregator MAY publish to its own zone's subject ([02-topology](02-topology.md) Section 6.3).
- The Worker-to-Aggregator hop does not need replay: it rides a reliable, ordered gRPC stream, and a stream that breaks ends the session ([02-topology](02-topology.md) T10).

### 2.5 Delivery Semantics

| Hop | Guarantee required of the transport | Loss is detected by | Repaired by |
|-----|-------------------------------------|---------------------|-------------|
| Worker to Aggregator state | Ordered within a stream. | Worker sequence. | Worker snapshot. |
| Aggregator to Worker tasks and control | Ordered within a stream. | Stream failure; the Aggregator tracks in-flight tasks. | Reassignment ([03-protocols](03-protocols.md) Section 7.3). |
| Aggregator to CCR reports | None required; at-most-once is sufficient. | Report sequence. | CCR snapshot request. |
| CCR to Aggregator gRPC calls | Request and response. | gRPC status. | Retry with backoff. |

## 3. Task Ownership and Fencing

- At any time a task has at most one **current attempt**, identified by `task_id` and `attempt`. The Aggregator records the Worker that holds it.
- A Worker MUST copy `Task.attempt` into `TaskResult.attempt`.
- The Aggregator MUST ignore, and count, a `TaskResult` whose `task_id` is not in flight, whose sender is not the Worker holding the task, or whose `attempt` differs from the current attempt. This fences out answers from superseded assignments, including those of a Worker that was marked `Degraded` and has recovered.
- The Aggregator cannot stop a partitioned Worker from continuing to run a task after the task was reassigned. The rule that a task is held by at most one Worker ([03-protocols](03-protocols.md) Section 7.3) therefore governs which result is **accepted**; it does not govern which Workers are running it.
- A task whose effects must not repeat MUST be made idempotent by the application, using `task_id`, because a `worker_lost` outcome, a transport error on the client's call, and any reassignment can follow an attempt that ran in part.
- Cancellation follows [03-protocols](03-protocols.md) Section 7.4. A result sent for a cancelled task after its `CANCELLED` result is stale and is discarded.

## 4. High Availability

This version specifies the guarantees that a high-availability mechanism MUST provide. It does not mandate the mechanism, so that an implementation can use a consensus store, a database or a platform lease.

### 4.1 Central Cluster Registry

| # | Guarantee |
|---|-----------|
| G1 | At most one Primary accepts writes at any time, including during a network partition. A mechanism that cannot guarantee this MUST stop accepting writes rather than risk two Primaries. |
| G2 | An admission, a revocation or an attachment that the CCR acknowledged MUST survive a failover. |
| G3 | Promotion of a Replica MUST NOT change any identity ([02-topology](02-topology.md) Section 4.1). |
| G4 | A Replica MUST NOT accept writes. It MAY serve reads. A read served with replication lag beyond a configured bound MUST be marked as potentially stale. |
| G5 | After a failover the new Primary MUST request a snapshot from every attached instance (Section 2.3) before treating zone state as current. |

Further requirements:

- A write sent to a CCR that is not the Primary MUST fail with `FAILED_PRECONDITION` ([04-control-api](04-control-api.md) Section 7). The response MAY name the Primary.
- How Aggregators and clients find the Primary is implementation defined.
- While no Primary is reachable, Aggregators MUST keep serving their admitted Workers and keep their reports' sequence numbers. New registrations fail with `UNAVAILABLE`, and Workers retry with backoff (Section 6). When a Primary returns, G5 repairs any state the CCR missed.
- Only the Primary applies zone reports. Replicas receive the resulting state through replication.
- The CCR reaches each regional bus as a subscriber only, for example through a NATS leaf node or an account import. How it does so is deployment defined. A bus that the CCR cannot reach is covered by snapshot recovery.

### 4.2 Aggregator Instances

| # | Guarantee |
|---|-----------|
| H1 | A Worker session is owned by exactly one instance. Only that instance dispatches tasks to the Worker or processes its messages. |
| H2 | A task has one holder (Section 3). The CCR assigns every submitted `task_id` from a secure random source ([04-control-api](04-control-api.md) Section 4.1), and an Aggregator accepts a voucher once and refuses a `task_id` already in flight ([04-control-api](04-control-api.md) Section 4.4), so no two instances can hold the same `task_id`. |
| H3 | The instance owning a session is recorded by the CCR at admission, and the CCR ignores reports about a Worker from any other instance. |
| H4 | When an instance is lost, every session it owned ends, and every task in flight on it ends with a transport error on the submitting client's call. The CCR keeps no record of tasks and reports nothing about them. Nothing is migrated between instances. A voucher is void at any other instance, so the clients' vouchers cannot be redirected. |

Further requirements:

- Instances of a zone MAY share a regional bus. They MUST NOT share state that would let two instances act on the same session.
- How Workers are spread over the instances of a zone is implementation defined. After an instance is lost, its Workers reconnect to another instance with the backoff of Section 6 and register again.
- A zone with no attached instance serves no tasks. The CCR issues no voucher for it ([04-control-api](04-control-api.md) Section 4.1).

## 5. Limits and Backpressure

- Receivers MUST enforce a maximum message size, with a default of 64 KiB and a configurable maximum of 4 MiB, and MUST discard a larger message without parsing it. The discard MUST be counted. The limit applies to bus messages and to gRPC messages. The effective limit is one value for a cluster: every component that sends or receives a message MUST apply the same limit, so that a Worker never sends a message that its Aggregator will discard.
- A Worker MUST NOT send a result that exceeds the limit. A Worker whose result would do so MUST send `TASK_STATUS_FAILED` with error code `result_too_large` and `retryable` false instead.
- Capability maps are limited as in [03-protocols](03-protocols.md) Section 5.2.
- An Aggregator MUST NOT dispatch a task to a Worker that already holds the maximum number of in-flight tasks. Such a Worker is not eligible.
- An implementation SHOULD apply a per-identity limit on messages and registrations, and SHOULD answer `RESOURCE_EXHAUSTED` when it is exceeded. An Aggregator SHOULD likewise bound the number of vouchers it remembers ([04-control-api](04-control-api.md) Section 4.4) and the submissions per client, and a CCR SHOULD limit the voucher requests per client.
- An Aggregator SHOULD limit the registrations in progress at one time, so that a restart of many Workers does not exhaust it.
- By default a task that has no eligible Worker is rejected, not queued. An implementation MAY queue it until its deadline. A queued task MUST remain subject to every rule of [03-protocols](03-protocols.md) Section 7.

## 6. Reconnection and Credential Rotation

- A component that loses a connection to its peer MUST reconnect with exponential backoff and full jitter: before reconnect attempt *n* (counting from 0) it MUST wait a random time chosen uniformly from `[0, min(maximum, initial × factor^n)]`, with the defaults in Section 1.1. A successful connection resets *n* to 0.
- Jitter is REQUIRED. An Aggregator restart makes every Worker reconnect at the same moment, and without jitter that burst defeats the low-noise design.
- Before its SVID expires a component MUST replace its connections with new ones that use the renewed SVID ([01-identity](01-identity.md) Section 5). A Worker's replacement connection is a new session: it registers again ([04-control-api](04-control-api.md) Section 2.6).
- A Worker SHOULD replace its connection while it holds no tasks. Tasks in flight when the old session ends are reassigned or failed as in T10.
- Session resumption (continuing a session over a new connection without re-registration) is not defined in this version.

## 7. Observability and Audit

### 7.1 Audit Events

An implementation MUST write an audit record for each of the following events and MUST NOT include task payloads in any record:

- admission of a Worker, and rejection of a registration (with the failure class, but without making it available to the caller);
- revocation of an identity, with the requester and the reason;
- attachment, detachment and loss of an Aggregator instance;
- issue of a task voucher (the `voucher_id`, the client, the instance and the digest, but never a payload) and refusal of a request, with the failure class;
- refusal of a submission at an Aggregator, with the failed check of [04-control-api](04-control-api.md) Section 4.4;
- a snapshot requested by the CCR, with the cause;
- an authorization denial on the bus or on a service.

### 7.2 Metrics

An implementation SHOULD expose the following counters and gauges, labelled by `zone` where it makes sense. They are the machine-readable form of the "logged and counted" requirement in [02-topology](02-topology.md) Section 6.4.

| Metric | Type | Counts |
|--------|------|--------|
| `a2a_cp_acl_denied_total` | counter | Publishes and subscriptions refused. |
| `a2a_cp_messages_dropped_total{reason}` | counter | Messages discarded, with the reason: `malformed`, `oversize`, `invalid`, `identity_mismatch`, `stale`. |
| `a2a_cp_resyncs_total{hop}` | counter | Recoveries started, for the hop `worker` or `ccr`. |
| `a2a_cp_results_fenced_total` | counter | Results ignored under Section 3. |
| `a2a_cp_registrations_total{outcome}` | counter | Registrations, by `admitted` or `rejected`. |
| `a2a_cp_vouchers_total{outcome}` | counter | Voucher requests at the CCR, by `issued` or `refused`; and submissions at an Aggregator, by `accepted` or `refused`. |
| `a2a_cp_workers{state}` | gauge | Workers per state. |
| `a2a_cp_tasks_in_flight` | gauge | Tasks in flight. |
| `a2a_cp_tasks_total{status}` | counter | Final task outcomes. |
| `a2a_cp_reassignments_total` | counter | Task reassignments. |

### 7.3 Tracing

`trace_context` is propagated as defined in [03-protocols](03-protocols.md). It is untrusted and MUST NOT influence authorization or routing.

## 8. Conformance

- An implementation conforms to this document if it satisfies every MUST and MUST NOT statement for each role it implements. The SHOULD statements are recommended and are not required to claim conformance.
- An implementation SHOULD publish a conformance statement that lists: the roles it implements, the schema major versions, the optional features it supports ([04-control-api](04-control-api.md) Section 5), and every parameter whose default it changes.
- An implementation that does not support `recovery.replay` is conforming.

[RFC 2119]: https://www.rfc-editor.org/rfc/rfc2119
[RFC 8174]: https://www.rfc-editor.org/rfc/rfc8174
