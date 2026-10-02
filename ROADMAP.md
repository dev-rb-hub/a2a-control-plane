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

- Specification drafts 01-03, wire schemas for state, tasks, control and zone reports, and a Kubernetes NetworkPolicy reference.
- Python SDK: the full protocol runs in-process (registry, aggregators, workers, ACLs, reassignment, cancellation) with 125 tests and CI. See [sdk/python/README.md](sdk/python/README.md).
- An `a2a-sdk` bridge runs A2A agents as workers. A CrewAI integration exists only as a scaffold.
- Nothing runs over a real network yet. That is the main gap between "specified and simulated" and "usable".

## Milestones

Tracked as GitHub milestones; each issue names the files to change and what done means.

| Milestone | Outcome | Issues |
|-----------|---------|--------|
| **M1 Wire-complete** | Every message the protocol needs has a schema and spec text, enforced by CI | [#2](https://github.com/dev-rb-hub/a2a-control-plane/issues/2), [#10](https://github.com/dev-rb-hub/a2a-control-plane/issues/10), [#11](https://github.com/dev-rb-hub/a2a-control-plane/issues/11), [#12](https://github.com/dev-rb-hub/a2a-control-plane/issues/12) |
| **M2 Real transports** | The same behavior over gRPC with mTLS and over NATS | [#3](https://github.com/dev-rb-hub/a2a-control-plane/issues/3), [#4](https://github.com/dev-rb-hub/a2a-control-plane/issues/4), [#5](https://github.com/dev-rb-hub/a2a-control-plane/issues/5), [#6](https://github.com/dev-rb-hub/a2a-control-plane/issues/6), [#7](https://github.com/dev-rb-hub/a2a-control-plane/issues/7) |
| **M3 Ecosystem adoption** | Existing agent stacks join without rewrites | [#8](https://github.com/dev-rb-hub/a2a-control-plane/issues/8), [#9](https://github.com/dev-rb-hub/a2a-control-plane/issues/9), [#16](https://github.com/dev-rb-hub/a2a-control-plane/issues/16) |
| **M4 Production readiness** | Outside users can deploy and extend it | [#13](https://github.com/dev-rb-hub/a2a-control-plane/issues/13), [#14](https://github.com/dev-rb-hub/a2a-control-plane/issues/14), [#15](https://github.com/dev-rb-hub/a2a-control-plane/issues/15) |

Dependency order: M1 and the M2 groundwork (#3, #4, #5) can proceed in parallel; the transports (#6, #7) need all of them. M3 items only need the in-memory bus, so they can start at any time.

## How success is judged

- **Correct.** Each normative requirement in `spec/` is covered by a test, and CI is green on every supported Python and protobuf version.
- **Secure.** Isolation claims are backed by denial tests (lateral movement, cross-zone join, forged identity), and no check is relaxed to make a test pass.
- **Efficient.** The traffic claim is reproducible: `sdk/python/examples/traffic_benchmark.py` compares mesh, hub and delta strategies using the SDK's real encodings. Its figures come from a simulation under stated assumptions, not a production measurement.
- **Easy to join.** A new contributor can pick an issue, run four commands, and know from CI whether they are done.

## Decisions that need the maintainer

| Decision | Why it matters | Where |
|----------|----------------|-------|
| Async model for the roles (event-loop facade, async-native, or locks) | Blocks both real transports | [#5](https://github.com/dev-rb-hub/a2a-control-plane/issues/5) |
| License and copyright holder | `LICENSE` is MIT and was edited by hand; confirm it is what you intend before outside contributions arrive | `LICENSE` |
| Relationship to the Linux Foundation A2A project | The README states independence. Whether to propose this as a complement or contribute upstream is a strategic call | README note |
| Governance with a single maintainer | `GOVERNANCE.md` names one maintainer and requires code-owner approval. Adding co-maintainers changes the review rules | `GOVERNANCE.md`, `.github/CODEOWNERS` |
| PyPI name and first release | Publishing is hard to undo | [#15](https://github.com/dev-rb-hub/a2a-control-plane/issues/15) |

## Non-goals

- Replacing the A2A application protocol, MCP or any agent framework.
- Replacing SPIFFE/SPIRE. The SDK's dev CA exists only so tests can run without infrastructure.
- Defining task payload semantics. Payloads are opaque to the control plane.
