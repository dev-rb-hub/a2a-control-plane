# Roadmap

## Why this exists

Large multi-agent systems tend to coordinate over peer-to-peer meshes or polling. Network chatter then grows with the square of the number of agents (O(N²)), and every agent can reach every other agent, which is a zero-trust problem. a2a-control-plane specifies a different shape:

- **Control-and-worker topology.** Workers talk only to a regional Aggregator; Aggregators report to a central Registry. There is no worker-to-worker path.
- **Identity-derived authorization.** Everything a participant may publish or read follows from its SPIFFE identity.
- **Delta-state streaming with adaptive heartbeats.** Idle agents cost almost nothing.

It is the infrastructure layer *underneath* agent-to-agent application protocols such as the Linux Foundation [A2A protocol](https://github.com/a2aproject/A2A). It is independent of that project and designed to complement it.

## Who it is for

| Audience | What they need from this repo |
|----------|------------------------------|
| Platform and security teams | A normative, reviewable spec and a Kubernetes reference that enforces the topology |
| Agent framework authors | An SDK and bridges so their agents can run as workers (A2A, CrewAI) |
| Contributors | Small, well-specified issues with tests and CI that tell them when they are done |

## Where it stands

- Specification drafts 01-06 (identity, topology, protocols, control API, operations, threat model), wire schemas for state, tasks, control, zone reports, registration, submission and administration plus the gRPC service definitions, and a Kubernetes NetworkPolicy reference.
- Python SDK: the full protocol runs in-process (registry, aggregators, workers, ACLs, registration relay and negotiation, snapshot recovery, task vouchers and selection, reassignment, cancellation, fencing) with 313 tests and CI. See [sdk/python/README.md](sdk/python/README.md).
- An `a2a-sdk` bridge runs A2A agents as workers. A CrewAI integration exists only as a scaffold.
- Nothing runs over a real network yet. That is the main gap between "specified and simulated" and "usable".
- Planned, not started: requirement IDs and a generated spec-to-test traceability matrix (`spec/07-traceability.md`), designed in [#25](https://github.com/dev-rb-hub/a2a-control-plane/issues/25).

## Milestones

Tracked as GitHub milestones; each issue names the files to change and what done means.

| Milestone | Outcome | Issues |
|-----------|---------|--------|
| **M1 Wire-complete** | Every message the protocol needs has a schema and spec text, enforced by CI | [#2](https://github.com/dev-rb-hub/a2a-control-plane/issues/2) (done in PR #20), [#10](https://github.com/dev-rb-hub/a2a-control-plane/issues/10), [#11](https://github.com/dev-rb-hub/a2a-control-plane/issues/11), [#12](https://github.com/dev-rb-hub/a2a-control-plane/issues/12) |
| **M2 Real transports** | The same behavior over gRPC with mTLS and over NATS | [#3](https://github.com/dev-rb-hub/a2a-control-plane/issues/3), [#4](https://github.com/dev-rb-hub/a2a-control-plane/issues/4), [#5](https://github.com/dev-rb-hub/a2a-control-plane/issues/5), [#6](https://github.com/dev-rb-hub/a2a-control-plane/issues/6), [#7](https://github.com/dev-rb-hub/a2a-control-plane/issues/7) |
| **M3 Ecosystem adoption** | Existing agent stacks join without rewrites | [#8](https://github.com/dev-rb-hub/a2a-control-plane/issues/8), [#9](https://github.com/dev-rb-hub/a2a-control-plane/issues/9), [#16](https://github.com/dev-rb-hub/a2a-control-plane/issues/16) |
| **M4 Production readiness** | Outside users can deploy and extend it | [#13](https://github.com/dev-rb-hub/a2a-control-plane/issues/13), [#14](https://github.com/dev-rb-hub/a2a-control-plane/issues/14), [#15](https://github.com/dev-rb-hub/a2a-control-plane/issues/15) |

Dependency order: M1 and the M2 groundwork (#3, #4, #5) can proceed in parallel; the transports (#6, #7) need all of them. M3 items only need the in-memory bus, so they can start at any time.

### Near-term engineering

Follow-ups to the voucher-based task origination in PR #20, in the order they should land:

| Item | State |
|------|-------|
| **Task ledger.** Every Aggregator records the tasks it accepts and how they ended, and answers `GetTaskStatus`, so a client whose call broke can learn the outcome from its own region. The CCR stays blind to task execution. | Specified (spec 04 section 4.7). The in-memory ledger is implemented. A durable ledger on NATS JetStream, one stream per zone, is the recommended backing and is not built: [#21](https://github.com/dev-rb-hub/a2a-control-plane/issues/21). |
| **Voucher replay protection.** A memory-bounded store of spent voucher ids that holds each id until the voucher expires. | Implemented as a bounded store that fails closed when full. It is deliberately not an LRU: evicting a live entry would let that voucher be replayed. |
| **Overlapping SVIDs.** A grace period in which two credentials are valid, so a rotation does not end long-running tasks. | Sketched as an informational note (spec 04 section 9). Needs a session-handover design before it can be specified: [#22](https://github.com/dev-rb-hub/a2a-control-plane/issues/22). |
| **Spec-to-test traceability.** Stable requirement IDs in `spec/01`-`05`, a pytest marker that links tests to them, and a generated matrix with a CI check. | Design issue open, nothing implemented: [#25](https://github.com/dev-rb-hub/a2a-control-plane/issues/25). |

## How success is judged

- **Correct.** Each normative requirement in `spec/` is covered by a test, and CI is green on every supported Python and protobuf version. Today this is a goal, not a checked fact: requirements have no IDs and tests are not linked to them. [#25](https://github.com/dev-rb-hub/a2a-control-plane/issues/25) is the work that makes it checkable.
- **Secure.** Isolation claims are backed by denial tests (lateral movement, cross-zone join, forged identity), and no check is relaxed to make a test pass.
- **Efficient.** The traffic claim is reproducible: `sdk/python/examples/traffic_benchmark.py` compares mesh, hub and delta strategies using the SDK's real encodings. Its figures come from a simulation under stated assumptions, not a production measurement.
- **Easy to join.** A new contributor can pick an issue, run four commands, and know from CI whether they are done.

## Evaluation categories

A control plane is judged on four areas. This table records what exists today. A row that says "not scoped" or "design only" is a gap, not a feature.

| Category | Metric | State |
|----------|--------|-------|
| Registry and topology | Dynamic topology for short-lived workers | Specified (registration, heartbeats, worker state) and tested in process. No churn-specific test or metric yet |
| Registry and topology | Cross-domain discovery latency | Not measurable: every transport is in-memory. Needs [#6](https://github.com/dev-rb-hub/a2a-control-plane/issues/6), [#7](https://github.com/dev-rb-hub/a2a-control-plane/issues/7) and the benchmark harness in [#30](https://github.com/dev-rb-hub/a2a-control-plane/issues/30) |
| Routing and state | Handoff resilience | Reassignment and result fencing are specified and tested in process. Durable state needs [#21](https://github.com/dev-rb-hub/a2a-control-plane/issues/21); queueing needs [#10](https://github.com/dev-rb-hub/a2a-control-plane/issues/10) |
| Routing and state | Translation overhead in the bridge | Not measured: [#30](https://github.com/dev-rb-hub/a2a-control-plane/issues/30) |
| Observability and governance | Delegation depth limit | Would bound chains of delegated tasks that pass through the control plane. It would not detect loops inside an agent, or agent-to-agent calls that bypass the control plane. Design only: [#27](https://github.com/dev-rb-hub/a2a-control-plane/issues/27) |
| Observability and governance | Usage attribution | Would roll up token usage that workers report, per root task. The figures are self-reported and unverified, and the control plane is not a billing gateway and does not throttle. Design only: [#28](https://github.com/dev-rb-hub/a2a-control-plane/issues/28) |
| Security and trust | Zero-trust identity | SVID admission, vouchers and denial tests are implemented. Rotation overlap: [#22](https://github.com/dev-rb-hub/a2a-control-plane/issues/22). Agent Card signature checks are not specified: [#29](https://github.com/dev-rb-hub/a2a-control-plane/issues/29) |
| Security and trust | Delegation audit trail | Audit events are specified (spec 05 section 7.1) but record no delegation chain. A chain would cover delegations the control plane sees, not what happens inside an agent: [#28](https://github.com/dev-rb-hub/a2a-control-plane/issues/28) |

Coverage per category will come from the traceability matrix ([#25](https://github.com/dev-rb-hub/a2a-control-plane/issues/25)). Performance figures will be reported, not gated, by the benchmark harness ([#30](https://github.com/dev-rb-hub/a2a-control-plane/issues/30)); wall-clock thresholds are never CI gates.

## Decisions that need the maintainer

| Decision | Why it matters | Where |
|----------|----------------|-------|
| Async model for the roles (event-loop facade, async-native, or locks) | Blocks both real transports. A proposal (sync core, single-owner event loop, `grpc.aio` at the edge) is under discussion on the issue and written up as a proposed RFC in PR #24, which is held until the discussion ends. Its benchmarks are incomplete: no 2,000-stream result exists yet | [#5](https://github.com/dev-rb-hub/a2a-control-plane/issues/5) |
| License and copyright holder | Decided: Apache-2.0 for code, schemas and reference, CC BY 4.0 for `spec/` (previously MIT), copyright Rendu Barkhuizen, with `NOTICE`, `TRADEMARKS.md` and DCO sign-off in `CONTRIBUTING.md`. Still open: IP lawyer review, a DCO bot, and a standard CLA if the project ever moves off these licences | `LICENSE`, `NOTICE`, `TRADEMARKS.md` |
| Relationship to the Linux Foundation A2A project | The README states independence. Whether to propose this as a complement or contribute upstream is a strategic call | README note |
| Governance with a single maintainer | `GOVERNANCE.md` names one maintainer and requires code-owner approval. Adding co-maintainers changes the review rules | `GOVERNANCE.md`, `.github/CODEOWNERS` |
| PyPI name and first release | Publishing is hard to undo | [#15](https://github.com/dev-rb-hub/a2a-control-plane/issues/15) |

## Non-goals

- Replacing the A2A application protocol, MCP or any agent framework.
- Replacing SPIFFE/SPIRE. The SDK's dev CA exists only so tests can run without infrastructure.
- Defining task payload semantics. Payloads are opaque to the control plane.
