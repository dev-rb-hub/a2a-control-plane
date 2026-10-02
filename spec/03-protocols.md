# a2a-control-plane Specification 03: Protocols

**Status:** Draft v0.1.0  
**Scope:** Wire format, transport, delta-state streaming, and adaptive heartbeats.

## 1. Conventions

The key words "MUST", "MUST NOT", "REQUIRED", "SHOULD", "SHOULD NOT", "RECOMMENDED", and "MAY" in this document are to be interpreted as described in BCP 14 ([RFC 2119], [RFC 8174]) when, and only when, they appear in all capitals.

## 2. Transports

| Hop | Transport | Port |
|-----|-----------|------|
| Worker to Aggregator | gRPC (HTTP/2) over mTLS, one long-lived bidirectional stream | 50051 |
| Aggregator to CCR | gRPC over mTLS | 50051 |
| Aggregator regional bus | NATS, subjects defined in [02-topology](02-topology.md) Section 6 | implementation defined |

- Workers MUST NOT connect to the regional bus directly. The Aggregator MUST map each Worker stream to the Worker's subjects on the bus.
- All transports MUST satisfy the mTLS requirements of [01-identity](01-identity.md).

## 3. Message Encoding

- All payloads MUST be [Protocol Buffers v3] messages defined in [`schemas/`](../schemas/).
- JSON encodings MUST NOT be used on the wire. They MAY be used for diagnostics.
- Implementations MUST ignore unknown fields and MUST preserve them when forwarding.

## 4. NATS Framing

When a payload is published on the regional bus:

- The subject MUST follow the grammar in [02-topology](02-topology.md) Section 6.1.
- The message body MUST be exactly one serialized protobuf message with no additional framing.
- A message header `a2a-type` MUST carry the fully qualified protobuf message name, for example `a2a.controlplane.v1.AgentStateDelta`.
- Message bodies SHOULD NOT exceed 64 KiB. Larger payloads SHOULD be referenced by identifier rather than inlined.

## 5. Delta-State Streaming

State is reported as deltas using `AgentStateDelta` ([`schemas/v1/state_delta.proto`](../schemas/v1/state_delta.proto)).

- The first message a Worker sends after registration MUST contain its full capability set, expressed as a delta against an empty set.
- Every later message MUST contain only capabilities that changed. A capability that was removed MUST be represented by an empty value, and the key MUST be present.
- A Worker MUST NOT send a delta whose `changed_capabilities` is empty unless it is serving as a heartbeat (Section 6).
- `timestamp_ns` MUST be the Unix time in nanoseconds at which the change was observed. Receivers MUST apply deltas for a given `agent_id` in `timestamp_ns` order and MUST discard a delta older than the last applied one.
- `zone_id` MUST match the zone in the sender's SPIFFE ID. Receivers MUST reject mismatches.
- `trace_context` carries OpenTelemetry context in the compact binary form: 1 byte version, 16 bytes trace-id, 8 bytes parent span-id, 1 byte trace-flags, totaling 26 bytes. It SHOULD be omitted when there is no active trace.

### 5.1 Aggregator Coalescing

An Aggregator MUST coalesce deltas for the same `agent_id` received within a configurable window before forwarding to the CCR. The window SHOULD default to 1 second. Later values for the same capability key MUST supersede earlier ones.

## 6. Adaptive Heartbeats

A heartbeat is an `AgentStateDelta` with an empty `changed_capabilities` map.

- Any delta from a Worker MUST count as a liveness signal. A Worker that has sent a delta within the current heartbeat interval MUST NOT send a separate heartbeat.
- When a Worker has no deltas to send, it MUST send a heartbeat at the current interval.
- The interval starts at 5 seconds (`base_interval`). After each consecutive heartbeat with no intervening state change, the interval MUST double, up to 60 seconds (`max_interval`). A state change MUST reset the interval to `base_interval`.
- The Aggregator MUST declare a Worker `Degraded` (T7 in [02-topology](02-topology.md)) when no message has been received for 3 times the interval most recently in effect.
- `base_interval`, `max_interval`, and the missed-interval multiplier MUST be configurable. The Aggregator MAY push new values on the `control` subject.
- An Aggregator MUST NOT forward heartbeats to the CCR. It MUST report only liveness state changes.

## 7. Versioning

- The schema package is versioned (`a2a.controlplane.v1`). Backward-incompatible changes MUST use a new package and a new directory under `schemas/`.
- Peers MUST advertise supported versions during registration, and MUST use the highest common version.

## 8. Security Considerations

- Without a size cap, an authenticated Worker could exhaust Aggregator memory. Receivers MUST enforce maximum message size limits.
- Adaptive heartbeat backoff reduces idle traffic but lengthens the failure detection time to at most 3 times `max_interval`. Deployments with tighter detection needs MUST lower `max_interval`.
- Trace context is untrusted input and MUST NOT influence authorization.

[RFC 2119]: https://www.rfc-editor.org/rfc/rfc2119
[RFC 8174]: https://www.rfc-editor.org/rfc/rfc8174
[Protocol Buffers v3]: https://protobuf.dev/programming-guides/proto3/
