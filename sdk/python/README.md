# a2a-control-plane (Python SDK)

Reference Python SDK for the [a2a-control-plane specification](../../spec/). Pre-alpha.

This package is independent of, and not affiliated with, the Linux Foundation Agent2Agent protocol or its `a2a-sdk`.

## Layers

| Layer | Modules | Status |
|-------|---------|--------|
| Core (pure logic, no I/O) | `tokens`, `identity`, `subjects`, `state`, `heartbeat`, `delta`, `tasks`, `control`, `reports`, `backoff` | implemented |
| Roles over an in-memory bus | `Registry`, `Aggregator`, `WorkerClient`, `InMemoryBus` | implemented |
| Registration relay (CCR as root, Aggregator as intermediary), version and feature negotiation, Aggregator attachment with epochs | spec 04, sections 2, 3, 5 | implemented in process |
| Sequence numbers, gap detection, snapshot recovery (Worker to Aggregator, Aggregator to CCR), result fencing | spec 05, sections 2, 3 | implemented |
| Task submission through the CCR, selection by `required_capabilities`, revocation | spec 04, sections 4, 6 | implemented in process |
| Limits (message size, capability map, in-flight), self-reported health, registration timeout, reconnect backoff | spec 03, 5.2; spec 05, sections 5, 6 | implemented |
| Dev tooling | `DevCA` (Ed25519, not SPIRE), `ManualClock`, `DevCluster`, `InMemoryBus.drop_filter` for loss injection | implemented |
| A2A bridge, worker side | `bridge` (run an `a2a-sdk` agent as a worker; extra `a2a`) | implemented |
| gRPC service definitions (`Registration`, `Admin`, `TaskSubmission`, `ZoneControl`) | `schemas/v1/services.proto` ([#2](https://github.com/dev-rb-hub/a2a-control-plane/issues/2)) | schema only; no server or client yet |
| `recovery.replay` (durable bus), CCR replication and election, Aggregator health checks, `Admin.ListWorkers` over RPC, task queueing, session resumption, metrics and audit export | spec 04, 05 | specified, not implemented |
| TLS-bound registration (RFC 9266 channel binding) and ECDSA P-256 SVID signatures | spec 04, section 2.3 | not implemented: the in-memory transport has no TLS, so the channel binding is empty, and `DevCA` signs with Ed25519 |
| Bus protocol, X.509 dev certificates, async design | [#3](https://github.com/dev-rb-hub/a2a-control-plane/issues/3), [#4](https://github.com/dev-rb-hub/a2a-control-plane/issues/4), [#5](https://github.com/dev-rb-hub/a2a-control-plane/issues/5) | planned |
| Transports | gRPC + mTLS ([#6](https://github.com/dev-rb-hub/a2a-control-plane/issues/6)), NATS ([#7](https://github.com/dev-rb-hub/a2a-control-plane/issues/7)) | planned |
| A2A gateway, multi-turn tasks | [#8](https://github.com/dev-rb-hub/a2a-control-plane/issues/8), [#9](https://github.com/dev-rb-hub/a2a-control-plane/issues/9) | planned |
| CrewAI integration | `integrations/crewai` (extra `crewai`) | scaffold only, methods raise `NotImplementedError` |

See [CONTRIBUTING.md](../../CONTRIBUTING.md) and [AGENTS.md](../../AGENTS.md) to pick something up.

## Try it

```powershell
.venv\Scripts\python examples\cluster_demo.py        # two-zone cluster: register, dispatch, isolate, fail, recover
.venv\Scripts\python examples\traffic_benchmark.py   # mesh vs hub vs delta + adaptive heartbeats
```

Tasks, results, control commands, registration messages and zone reports use the messages in `schemas/v1` (`task.proto`, `control.proto`, `zone_report.proto`, `registration.proto`, `submission.proto`, `admin.proto`; spec 03 sections 7-9 and spec 04). The aggregator reassigns retryable failures and tasks of lost workers, supports cancellation and heartbeat configuration, and reports zone state to the registry over the bus. Clients submit tasks with `Registry.submit_task`, which routes by `required_capabilities` and returns the final outcome through a callback. Registration and the CCR-to-Aggregator calls are direct method calls that mirror the gRPC services in `services.proto`; no gRPC transport exists yet ([#6](https://github.com/dev-rb-hub/a2a-control-plane/issues/6)).

## A2A bridge

```python
from a2a_control_plane.bridge import A2AWorkerBridge

bridge = A2AWorkerBridge(my_agent_executor, my_agent_card)  # any a2a-sdk AgentExecutor
worker = cluster.add_worker("agent-1", capabilities=bridge.capabilities())
bridge.attach(worker)
```

Control-plane tasks become A2A messages (text, raw bytes or a serialized `Message`), Agent Card skills become capabilities, and control-plane cancellation calls the agent's `cancel`. A2A `INPUT_REQUIRED` and `AUTH_REQUIRED` are reported as failures because control-plane tasks are single-turn ([#9](https://github.com/dev-rb-hub/a2a-control-plane/issues/9)).

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python scripts/gen_proto.py   # regenerate protobuf bindings from ../../schemas/v1
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy src examples
```
