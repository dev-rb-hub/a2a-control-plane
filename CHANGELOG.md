# Changelog

Changes to the specification (`spec/`) and the wire schemas (`schemas/`). Versions follow [GOVERNANCE.md](GOVERNANCE.md#versioning). SDK changes are tracked in pull requests.

## 1.0.0-rc.1 (2026-11-09)

First release candidate. From this tag the specification is frozen: until 1.0.0 only fixes found by implementers are merged. 1.0.0 is tagged when the Worker-Aggregator gRPC hop interoperates between two independent SDKs ([ROADMAP.md](ROADMAP.md#milestones)).

### Added

- `Drain` control command ([03-protocols](spec/03-protocols.md) Section 8; `ControlMessage.drain`, field 4). A draining Worker is not eligible for dispatch and keeps its current state; its session ends through T10 when its tasks are done or the drain timeout expires. ([#12](https://github.com/dev-rb-hub/a2a-control-plane/issues/12))
- Drain timeout default of 300 s ([05-operations](spec/05-operations.md) Section 1.1), and a recommendation to drain Workers before a planned instance shutdown (Section 4.2).
- Schema CI: `buf lint` and `buf breaking` (FILE category) on every change to `schemas/`, with the excepted lint rules documented in `buf.yaml`. ([#11](https://github.com/dev-rb-hub/a2a-control-plane/issues/11))

### Changed

- T10 in [02-topology](spec/02-topology.md) Section 5.2 also covers the end of a drain. Section 5.3 forbids dispatch to a draining Worker.
- The `shutdown` command remains undefined; it is deferred to a later minor version.

## 0.1.0 (2026-10-23)

Initial drafts of specifications 01-06, the v1 schemas and the Kubernetes reference. Changes before this changelog are in the Git history.
