# a2a-control-plane: Open Standard Specification for High-Scale Agentic Architectures (A2A)

[![Spec Version](https://img.shields.io/badge/spec-v0.1.0--draft-blue)](spec/)
[![License](https://img.shields.io/badge/license-Apache--2.0%20%2B%20CC%20BY--ND%204.0-green)](LICENSE)
[![Status](https://img.shields.io/badge/status-draft-orange)](GOVERNANCE.md)

> An open standard for **zero-trust agent-to-agent (A2A) control planes**: secure, low-noise orchestration of large multi-agent AI systems using SPIFFE/SPIRE identity, mTLS, regional aggregators, NATS messaging, gRPC, and Protocol Buffers.

> **Note:** this project is independent of, and not affiliated with, the Linux Foundation [Agent2Agent (A2A) Protocol](https://github.com/a2aproject/A2A). That protocol defines application-level agent communication (Agent Cards, tasks, JSON-RPC); this standard defines the infrastructure layer underneath (identity, topology, and state streaming) and is designed to complement it.

## ✨ Key Features

*   **Zero-trust identity**: SPIFFE/SPIRE X.509-SVIDs, TLS 1.3 mTLS, and challenge-response registration for every agent.
*   **Control-and-Worker topology**: Central Cluster Registry, Regional Aggregators, and Worker Agents with no worker-to-worker paths.
*   **Low network noise**: delta-state streaming and adaptive heartbeats replace O(N²) mesh chatter and polling.
*   **Formal worker lifecycle**: a normative state machine (`Unregistered`, `Challenged`, `Registered`, `Idle`, `Executing`, `Degraded`).
*   **Wire schemas**: backward-compatible Protobuf v3 definitions with OpenTelemetry trace propagation.
*   **Deployable reference**: Kubernetes NetworkPolicy that enforces the topology at the network layer.

## 📌 Abstract
Current multi-agent frameworks rely heavily on noisy peer-to-peer (P2P) mesh networking or naive polling, which scales quadratically (O(N²)) in network chatter and poses massive zero-trust security risks. 

**a2a-control-plane** is an open-standard specification designed to harden enterprise agent clusters. It enforces a strict **Control-and-Worker (Primary/Replica) topology**, isolates trust to a cryptographically secure **Cluster Registry**, and leverages **delta-state streaming** to eliminate background network noise.

## Why this exists
Agent frameworks do the work: they run the agents and their tools. Someone still has to govern the fleet around them. This standard is that control plane, in the way a service mesh's control plane handles identity, topology and policy for services. It shares building blocks with those meshes (SPIFFE identities, mTLS). For an organisation running many agents across several teams or zones, it answers four questions:

*   **Who may join?** Admission and identity for every agent, so a spoofed or rogue agent cannot register.
*   **Who may reach whom?** A topology where agents talk only through their zone's aggregator, enforced at the network layer.
*   **How is fleet state tracked at scale?** Deltas and adaptive heartbeats instead of quadratic polling.
*   **What can be audited?** Records of admission, revocation and refusals that never contain task payloads.

It is built on open standards (SPIFFE/SPIRE, mTLS, NATS, gRPC, Protobuf), so the control layer does not depend on one cloud or agent framework.

It is a draft. Task reassignment and result fencing are specified and work in process; the in-memory ledger is implemented. Not yet specified or built: a limit on how long a chain of delegated tasks may grow (it would bound chains the control plane can see, not detect loops inside an agent), durable hand-off state across providers, discovery of agents across organisational boundaries, and attribution of worker-reported token usage. [ROADMAP.md](ROADMAP.md) lists each evaluation category and what exists today.

## 🏗️ Repository Roadmap & Architecture

*   **[`/spec`](spec/)**: The formal technical standard (RFC 2119 compliant).
    *   [`01-identity`](spec/01-identity.md): SPIFFE/SPIRE identity bootstrapping & mTLS lifecycle.
    *   [`02-topology`](spec/02-topology.md): Regional Aggregator routing and message bus layouts.
    *   [`03-protocols`](spec/03-protocols.md): Wire protocol optimization (Protobuf & Adaptive Heartbeats).
    *   [`04-control-api`](spec/04-control-api.md): Worker admission, Aggregator attachment, task vouchers and intake, administration, version negotiation.
    *   [`05-operations`](spec/05-operations.md): Delivery and recovery, fencing, high availability, limits, reconnection, observability.
    *   [`06-threat-model`](spec/06-threat-model.md): Adversaries, trust boundaries, and residual risks (informative).
*   **[`/schemas`](schemas/)**: Official Protobuf (`.proto`) definition files for data exchange.
*   **[`/reference`](reference/)**: Implementation blueprints including Kubernetes manifests, local test environments, and Harness CD integrations.
*   **[`/sdk/python`](sdk/python/)**: Reference Python SDK (pre-alpha). The core layer (identity, subjects, worker state machine, adaptive heartbeats, delta streaming) is implemented and tested against the spec.

## 🤝 Contributing & Governance
This is an open standard. Changes to the core specification follow an RFC process detailed in our [GOVERNANCE.md](GOVERNANCE.md). 

To propose an update:
1. Open an issue describing the scaling or security bottleneck.
2. Submit a Pull Request modifying the specific `.md` document in `/spec`.
3. Ensure all reference schemas are backward compatible.

## 🔎 Related Topics

multi-agent systems, agentic AI architecture, agent-to-agent (A2A) protocol, AI agent orchestration, agent control plane, zero-trust networking, SPIFFE, SPIRE, mTLS, NATS, gRPC, Protobuf, Kubernetes NetworkPolicy, distributed systems, open standard, RFC 2119.

## ⚖️ License & Attribution

The specification text in [`spec/`](spec/) is released under [CC BY 4.0](LICENSE-CC-BY). The code, schemas and reference material (`sdk/`, `schemas/`, `reference/`) are released under the [Apache License 2.0](LICENSE-APACHE). [LICENSE](LICENSE) explains the split. Created by **Rendu Barkhuizen** ([@dev-rb-hub](https://github.com/dev-rb-hub)), who is the original author and maintainer. Please keep the copyright notice and [NOTICE](NOTICE) in copies and derivatives, and cite the project using [CITATION.cff](CITATION.cff).

The names "a2a-control-plane" and "A2A Control Plane" are covered by the [trademark policy](TRADEMARKS.md). Contribution terms are in [CONTRIBUTING.md](CONTRIBUTING.md).

---
*Created and maintained by Rendu Barkhuizen ([@dev-rb-hub](https://github.com/dev-rb-hub)) and contributors.*
