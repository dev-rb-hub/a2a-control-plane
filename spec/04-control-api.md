# a2a-control-plane Specification 04: Control API

**Status:** Draft v0.1.0  
**Scope:** Worker admission, Aggregator attachment, task origination, administration, version and feature negotiation, and the error model.

## 1. Conventions

The key words "MUST", "MUST NOT", "REQUIRED", "SHOULD", "SHOULD NOT", "RECOMMENDED", and "MAY" in this document are to be interpreted as described in BCP 14 ([RFC 2119], [RFC 8174]) when, and only when, they appear in all capitals.

The messages are defined in [`schemas/v1/registration.proto`](../schemas/v1/registration.proto), [`submission.proto`](../schemas/v1/submission.proto) and [`admin.proto`](../schemas/v1/admin.proto). The services are defined in [`services.proto`](../schemas/v1/services.proto) and are served over gRPC on port 50051 with the mTLS requirements of [01-identity](01-identity.md).

| Service | Served by | Called by | Purpose |
|---------|-----------|-----------|---------|
| `Registration` | CCR | Aggregator | Attach an Aggregator instance; relay a Worker's admission. |
| `Admin` | CCR | Administrative client | Revoke an identity; list Workers. |
| `TaskSubmission` | CCR | Authorized client | Submit and cancel tasks. |
| `ZoneControl` | Aggregator instance | CCR | Dispatch, cancel, snapshot, revoke. |

## 2. Worker Admission

### 2.1 Roles

Admission follows the shape of a certificate authority with a registration authority in front of it:

- The **CCR is the root.** It alone decides whether a Worker is admitted. It generates and validates the challenge.
- The **Aggregator is an intermediary.** It terminates the Worker's mTLS connection, vouches for what it observed on that connection (the Worker's SPIFFE ID and the TLS channel binding), and relays messages. It MUST NOT generate a nonce, MUST NOT evaluate a signature, and MUST NOT admit a Worker on its own authority.

The analogy has one limit: the CCR does not issue the Worker's credential. The X.509-SVID comes from SPIRE ([01-identity](01-identity.md) Section 3). The CCR is the root of trust for admission, not for certificate issuance.

### 2.2 Exchange

```mermaid
sequenceDiagram
    participant W as Worker
    participant A as Aggregator (relay)
    participant C as CCR (root)
    W->>A: mTLS handshake (SVID)
    W->>A: RegisterRequest
    A->>C: BeginRegistration(spiffe_id, request bytes, svid_chain, channel_binding)
    C->>C: validate chain, zone, request, policy; negotiate
    C-->>A: RegistrationChallenge(nonce, timeout, version, features)
    A-->>W: RegistrationChallenge
    W->>W: sign payload (Section 2.3) with SVID key
    W->>A: signature
    A->>C: CompleteRegistration(spiffe_id, signature)
    C->>C: verify signature, consume nonce
    C-->>A: RegistrationAdmission(session_id, selected, admitted caps)
    A-->>W: RegistrationAdmission
    W->>A: first state message: snapshot of the admitted set
```

### 2.3 Challenge Payload

The Worker signs the following byte string with the private key of its current X.509-SVID. Each field is encoded as a 4-byte big-endian length followed by the field bytes, and the encoded fields are concatenated in this order:

| # | Field | Content |
|---|-------|---------|
| 1 | label | The ASCII bytes `a2a-cp/v1/register`. |
| 2 | nonce | `RegistrationChallenge.nonce`. |
| 3 | identity | The Worker's SPIFFE ID, as UTF-8. |
| 4 | request digest | SHA-256 of the exact serialized `RegisterRequest` the Worker sent. |
| 5 | channel binding | The TLS exporter value for the Worker's connection (RFC 9266, label `EXPORTER-a2a-cp-register`, empty context, 32 bytes). Empty only on a transport that has no TLS. |

- The signature algorithm is the one of the SVID's key. Implementations MUST support ECDSA P-256 and Ed25519.
- Length prefixes prevent one field from being confused with its neighbour.
- Binding the request digest means an intermediary cannot change the requested capabilities, versions or features without invalidating the signature.
- Binding the channel prevents a signature produced for one connection from being used on another.
- An Aggregator MUST forward `RegisterRequest` to the CCR byte for byte, as `BeginRegistrationRequest.register_request`.

### 2.4 CCR Requirements

On `BeginRegistration` the CCR:

1. MUST verify that the calling Aggregator instance is attached (Section 3) and that its zone equals the zone of `worker_spiffe_id`.
2. MUST validate `svid_chain` against the trust bundle and MUST verify that its URI SAN equals `worker_spiffe_id` and that the identity's trust domain is the CCR's own.
3. MUST reject a request that exceeds the message size limit, checking the size before it parses the bytes, and MUST reject a request that does not parse or contains an invalid capability map, including one with more capabilities than the limit ([03-protocols](03-protocols.md) Section 5.2).
4. MUST evaluate the requested capabilities against its capability policy and reject the request if the policy denies them. The policy itself is deployment defined.
5. MUST negotiate the version and features (Section 5).
6. MUST generate a nonce of at least 128 bits from a cryptographically secure source, bind it to the Worker's SPIFFE ID, the relaying instance and the request, and keep at most one outstanding nonce per Worker. A new `BeginRegistration` for the same Worker replaces the earlier nonce.

On `CompleteRegistration` the CCR:

1. MUST consume the nonce whether or not verification succeeds. A nonce MUST NOT be used twice.
2. MUST reject the call if the relaying instance differs from the instance that began the registration, if the nonce has expired (default 10 seconds, [05-operations](05-operations.md) Section 1), or if the signature does not verify against the SVID public key over the payload of Section 2.3.
3. On success MUST record `agent_id`, `zone_id`, the admitted capabilities, the `session_id` and the owning instance, MUST durably record the admission before acknowledging it ([05-operations](05-operations.md) Section 4.1), and MUST return `RegistrationAdmission`.

The admitted capabilities are the requested capabilities that passed policy. The CCR MUST NOT admit a capability set different from the one requested; it either admits the request or rejects it.

Every failure caused by identity, signature, nonce or policy MUST be reported to the caller as `PERMISSION_DENIED` with no detail that distinguishes the cause, so that the exchange does not reveal whether an identity or capability is known.

### 2.5 Worker and Aggregator Requirements

- The Worker MUST verify that `selected_version` is one it offered and that every selected feature is one it offered, and MUST abort the registration otherwise.
- After admission the Worker's first state message MUST be a snapshot ([03-protocols](03-protocols.md) Section 5) whose capability map equals `admitted_capabilities` exactly.
- The Aggregator MUST discard any other first message from a Worker in state `Registered`, and MUST count the discard.
- If the snapshot does not arrive within the registration timeout (default 30 seconds), the Aggregator MUST apply T11 ([02-topology](02-topology.md) Section 5.2).
- After a failure the Aggregator MUST close the Worker's connection.

### 2.6 Replacement Sessions

- A Worker with an established session MAY register again. The new registration creates a new session and replaces the old one.
- If the old session is held by the same instance, the instance MUST end the old session (T10) before the new registration can be admitted.
- If the old session is held by another instance, the CCR MUST call `ZoneControl.RevokeWorker` on that instance as part of admitting the new session, and MUST ignore any report from the old instance about that Worker afterwards ([05-operations](05-operations.md) Section 2.3).
- Tasks in flight on the old session are reassigned or failed as in T10. Session resumption is not defined in this version ([05-operations](05-operations.md) Section 6).

## 3. Aggregator Attachment

An Aggregator instance joins the cluster by calling `Registration.Attach`.

- Every instance of a zone shares one SPIFFE ID ([01-identity](01-identity.md) Section 2.1), so each instance MUST choose an `instance_id` that is unique among the instances of its zone and MUST present the same value for its lifetime.
- The CCR MUST accept `Attach` only from a peer whose SPIFFE ID has the Aggregator form and belongs to the CCR's trust domain, and only for a zone the operator has provisioned. It MUST reject any other call with `PERMISSION_DENIED`.
- The CCR MUST grant an `epoch` that is greater than any epoch it granted before for that zone, and MUST negotiate the version and features (Section 5).
- After a successful `Attach` the instance MUST send a snapshot report ([05-operations](05-operations.md) Section 2.3) as its first report, and MUST stamp `instance_id` and `epoch` on every report.
- An attach by an `instance_id` that is already attached replaces the earlier attachment. Before granting the new epoch the CCR MUST end the earlier attachment as described below, and MUST instruct the superseded instance, if it is still reachable, to end every Worker session it holds (`ZoneControl.RevokeWorker`). The superseded epoch is stale ([05-operations](05-operations.md) Section 4.2).
- An attachment ends when the instance calls `Detach`, when its identity is revoked, when it is replaced, or when the CCR cannot reach the instance's `ZoneControl` service for the attachment timeout (default 30 seconds). The CCR SHOULD use the gRPC health checking protocol for the last case.
- When an attachment ends, the CCR MUST remove the Worker records the instance owned and MUST fail the tasks in flight on it with error code `aggregator_lost` (Section 4.4).

## 4. Task Origination

### 4.1 Submission

An authorized client submits a task with `TaskSubmission.SubmitTask`. The CCR relays the submission to a zone Aggregator and returns the final outcome on the same call.

- The client MUST authenticate over mTLS, and the CCR MUST authorize it against a policy that is deployment defined. Authorization decisions MUST use the client's authenticated identity and MUST NOT use any field of the task.
- `Task.task_id` is chosen by the client and MUST satisfy the token grammar of [02-topology](02-topology.md) Section 6.1. The CCR MUST reject a `task_id` that is already in flight in the cluster with `ALREADY_EXISTS`. A `task_id` MAY be reused after its outcome was returned.
- `Task.attempt` MUST be 1 or unset. The Aggregator owns it afterwards ([03-protocols](03-protocols.md) Section 7.1). The CCR MUST reject a larger value with `INVALID_ARGUMENT`.
- If `deadline_ns` has already passed, the CCR MUST answer with `TASK_STATUS_REJECTED` and error code `deadline_exceeded`, and MUST NOT dispatch the task.
- If `zone_id` is set, the CCR MUST offer the task only to that zone. If it is empty, the CCR chooses the zone; the order in which zones are tried is implementation defined.
- The CCR relays the task to an attached instance with `ZoneControl.Dispatch`. If the instance has no eligible Worker ([03-protocols](03-protocols.md) Section 7.1), the CCR MAY try another instance or zone. If none accepts, the CCR MUST answer with `TASK_STATUS_REJECTED`, error code `no_eligible_worker` and `retryable` set.
- The CCR MUST NOT interpret `payload`, `kind` or `content_type`, MUST NOT keep a task or its result after the outcome has been returned, and MUST NOT log payloads.

The CCR is a relay in this path and is the only component besides the Aggregator that sees task payloads. The Worker's data path never includes the CCR ([02-topology](02-topology.md) Section 4.1).

### 4.2 Outcome

- The call returns exactly one `TaskResult` that is the task's final outcome: the result that completed the task, or a failure produced under Section 4.4. Results that led to a reassignment are not returned ([03-protocols](03-protocols.md) Section 7.3).
- A client that stops waiting does not cancel the task. It MUST use `CancelTask`.

### 4.3 Cancellation

`TaskSubmission.CancelTask` asks the CCR to cancel an in-flight task. The CCR relays it with `ZoneControl.Cancel`. The response states whether a cancellation was requested for a task that was in flight; the outcome still arrives on the original `SubmitTask` call, normally as `TASK_STATUS_CANCELLED` ([03-protocols](03-protocols.md) Section 7.4).

### 4.4 Errors Produced by the Control Plane

When the control plane, rather than a Worker, ends a task, it MUST use these codes:

| `TaskError.code` | Status | `retryable` | Produced when |
|------------------|--------|-------------|---------------|
| `deadline_exceeded` | `REJECTED` | false | The deadline had passed before dispatch or on receipt. |
| `no_eligible_worker` | `REJECTED` | true | No Worker satisfied Section 4.1. |
| `worker_lost` | `FAILED` | true | The Worker holding the task was lost and the task could not be reassigned. |
| `aggregator_lost` | `FAILED` | true | The Aggregator instance holding the task was lost. |
| `cancelled` | `CANCELLED` | false | The Worker stopped the task on request. |
| `result_too_large` | `FAILED` | false | The Worker's result exceeded the size limit. |

`retryable` is a hint to the originator. An originator that resubmits after `worker_lost` or `aggregator_lost` MUST accept that the earlier attempt may have run in part ([05-operations](05-operations.md) Section 3).

## 5. Version and Feature Negotiation

- A peer advertises the schema major versions it supports and the optional features it supports. A Worker does so in `RegisterRequest`, an Aggregator instance in `AttachRequest`.
- The CCR MUST select the highest major version supported by both sides. If there is none, it MUST reject the call: `FAILED_PRECONDITION` for an Aggregator, and `PERMISSION_DENIED` for a Worker ([Section 2.4](#24-ccr-requirements)).
- The CCR MUST select the features that both sides support, and MUST always include `recovery.snapshot`.
- An unknown feature MUST be ignored. A feature name is 1 to 64 characters from `a-z`, `0-9`, `.`, `_` and `-`. A peer MUST NOT advertise more than 32 features.
- Selection is made once, at admission or attachment, and does not change for the session or epoch.

Defined features:

| Feature | Status | Meaning |
|---------|--------|---------|
| `recovery.snapshot` | REQUIRED of every implementation | Recovery from lost state by full snapshot ([05-operations](05-operations.md) Section 2). |
| `recovery.replay` | OPTIONAL | Recovery of zone reports by replay from a durable bus ([05-operations](05-operations.md) Section 2.4). |

Features that change message semantics MUST be introduced as a new feature name; they MUST NOT be signalled by changing the meaning of existing fields.

## 6. Administration

`Admin.RevokeIdentity` revokes a Worker or Aggregator SPIFFE ID, and `Admin.ListWorkers` returns the Workers the CCR knows.

- The CCR MUST authenticate administrative clients over mTLS and MUST authorize them by policy. Only the Primary MAY execute `RevokeIdentity`. Replicas MAY serve `ListWorkers`, and MUST mark the response as potentially stale when their replication lag exceeds the configured bound ([05-operations](05-operations.md) Section 4.1).
- Revoking a Worker MUST remove its record, MUST discard any pending challenge, MUST call `ZoneControl.RevokeWorker` on the owning instance so that the Aggregator closes the connection immediately, and MUST cause the SPIRE registration entry to be removed ([01-identity](01-identity.md) Section 7).
- Revoking an Aggregator identity MUST detach every instance of that zone (Section 3).
- Revocation is idempotent. `RevokeResponse.found` is false when the identity was not known.
- `ZoneControl.RevokeWorker` MUST end the session as T10 and MUST be acted on immediately.
- Every revocation MUST be written to the audit log ([05-operations](05-operations.md) Section 7).

## 7. Error Model

Calls on the services return gRPC status codes with these meanings. An implementation MUST NOT use them for other conditions.

| Status | Used for |
|--------|----------|
| `INVALID_ARGUMENT` | A malformed message, an invalid `task_id`, a `attempt` greater than 1, a size or capability limit exceeded. |
| `ALREADY_EXISTS` | A `task_id` already in flight. |
| `NOT_FOUND` | A `CancelTask` or administrative lookup for an unknown task or identity, where the response has no `found` field. |
| `PERMISSION_DENIED` | The caller is not authorized, or a Worker registration failed (Section 2.4). |
| `FAILED_PRECONDITION` | No common version; an instance that is not attached; a write sent to a CCR that is not the Primary. |
| `UNAVAILABLE` | No Primary is reachable, or the CCR cannot reach any instance of the zone. The caller SHOULD retry with the backoff of [05-operations](05-operations.md) Section 6. |
| `RESOURCE_EXHAUSTED` | A rate or concurrency limit ([05-operations](05-operations.md) Section 5). |

Errors produced for a task are carried in `TaskResult`, not as a status (Section 4.4). A `SubmitTask` call that reaches a Worker's outcome returns status `OK` even when the task failed.

## 8. Security Considerations

- The registration exchange tolerates an untrusted relay: the Aggregator can drop or delay messages and can refuse to relay, but it cannot admit a Worker, cannot alter a request undetected, and cannot reuse a signature on another connection.
- A compromised Aggregator can still lie about what it observed on the Worker's connection. The CCR therefore validates the SVID chain itself and binds the admission to the Worker's SPIFFE ID, and does not rely on the Aggregator's assertion of identity alone. See [06-threat-model](06-threat-model.md).
- Uniform `PERMISSION_DENIED` responses prevent the exchange from being used to probe which identities or capabilities exist.
- The CCR's role in task submission makes it a high-value target for payload exposure. Deployments that cannot accept this SHOULD encrypt payloads end to end at the application layer, since the control plane treats payloads as opaque.

[RFC 2119]: https://www.rfc-editor.org/rfc/rfc2119
[RFC 8174]: https://www.rfc-editor.org/rfc/rfc8174
