# CrewAI Integration Roadmap

This directory contains architectural placeholders for adapting CrewAI agents
to the a2a-control-plane roles. The Python methods are intentionally
unimplemented; this roadmap records the specification dependencies and the
implementation sequence.

## Implementation phases

1. **Identity and admission — `agent.py`**
   - Bind each Worker to a validated `SpiffeId` in the form
     `spiffe://<trust-domain>/zone/<zone_id>/agent/<agent_id>`.
   - Implement SVID-backed challenge signing and the proxied registration
     exchange, including nonce expiry, failure handling, and the initial
     capability delta.
   - Follow [spec 01, identity](../../../../../../spec/01-identity.md)
     (SPIFFE IDs, mTLS, credential rotation, and registration challenge) and
     [spec 02, topology](../../../../../../spec/02-topology.md)
     (Worker states and transitions T1–T4).

2. **Worker liveness and task delivery — `agent.py`**
   - Implement adaptive heartbeat scheduling and liveness recovery in
     accordance with [spec 03, protocols](../../../../../../spec/03-protocols.md)
     (delta-state streaming and adaptive heartbeats) and [spec 02, topology](../../../../../../spec/02-topology.md)
     (Worker lifecycle and permitted subjects).
   - Adapt CrewAI execution to asynchronous, JetStream-backed task delivery
     and protobuf task results. Preserve the standard's boundary: Workers
     connect only to their own Aggregator, which maps the authenticated stream
     to zonal NATS subjects. NATS broker addresses are not permission for a
     Worker to connect directly to the regional bus.
   - Use [spec 03, task messages](../../../../../../spec/03-protocols.md)
     and the existing task schema in
     [`schemas/v1/task.proto`](../../../../../../schemas/v1/task.proto).

3. **Regional aggregation and policy — `flow.py`**
   - Aggregate authorized telemetry only from child agents in the Flow's zone;
     reject identity/zone mismatches, apply deltas in timestamp order, and keep
     heartbeats local.
   - Enforce subject-level authorization as an application check, and rely on
     infrastructure policy for network isolation. See [spec 02, interaction
     boundaries and subject rules](../../../../../../spec/02-topology.md) and
     [`reference/k8s-network-isolation.yaml`](../../../../../../reference/k8s-network-isolation.yaml).

4. **Coalesced registry updates — `flow.py`**
   - Batch child changes and stream micro-deltas to the Central Cluster
     Registry over the Aggregator-to-CCR mTLS gRPC channel; do not forward
     Worker heartbeats.
   - Serialize the existing
     [`AgentStateDelta`](../../../../../../schemas/v1/state_delta.proto)
     protobuf schema and follow [spec 03, delta-state streaming](../../../../../../spec/03-protocols.md).

## Stub map

| Python placeholder | Planned responsibilities | Specification anchor |
| --- | --- | --- |
| `A2AWorkerAgent.register` | Proxied challenge-response admission | spec 01 §6; spec 02 §5 |
| `A2AWorkerAgent.heartbeat_loop` | Adaptive liveness and recovery | spec 03 §6; spec 02 §5 |
| `A2AWorkerAgent.consume_tasks` | Authorized async task execution and results | spec 02 §§4, 6; spec 03 §7 |
| `A2ARegionalAggregatorFlow.aggregate_child_telemetry` | Zone-local validation, aggregation, and coalescing | spec 02 §§4, 6; spec 03 §5 |
| `A2ARegionalAggregatorFlow.enforce_network_policy_boundaries` | Zone and subject boundary coordination | spec 02 §§4, 6 |
| `A2ARegionalAggregatorFlow.flush_registry_deltas` | Batched protobuf deltas to CCR | spec 03 §§2, 5; `schemas/v1/state_delta.proto` |

CrewAI is an optional integration dependency and is not added to the core SDK
dependency set by these scaffolds. Production implementation should add its
supported optional dependency and integration-specific tests when that work is
scheduled.
