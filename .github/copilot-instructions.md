# a2a-control-plane

Open-standard repository with a reference Python SDK. See [AGENTS.md](../AGENTS.md) for architecture, rules and commands, and [CONTRIBUTING.md](../CONTRIBUTING.md) for the workflow. Contents:

- `spec/`: normative specification (RFC 2119 language). `01-identity.md`, `02-topology.md`, `03-protocols.md`.
- `schemas/v1/`: Protobuf v3 wire schemas. Keep backward compatible; never renumber or retype fields.
- `reference/`: non-normative deployment examples (Kubernetes manifests).
- `sdk/python/`: reference SDK. Generated code in `_proto/` comes from `scripts/gen_proto.py`; do not edit it by hand.
- `GOVERNANCE.md`: RFC change process.

## Conventions

- Use MUST / MUST NOT / SHOULD / MAY only in capitals and only for normative statements.
- Zone and agent IDs follow the token grammar in `spec/02-topology.md` Section 6.1.
- Keep ports, subjects, and state names consistent across spec, schemas, and reference files.
- No build step; validate proto files with `buf lint` if available.

## Setup checklist

- [x] copilot-instructions.md created
- [x] Project requirements clarified (repository blueprint supplied by user)
- [x] Repository scaffolded (spec, schemas, reference, governance, templates)
- [x] Content drafted (specs, state_delta.proto, NetworkPolicy)
- [x] Extensions: none required
- [x] Compile: not applicable (documentation repository)
- [x] Tasks: not needed
- [x] Launch: not applicable
- [x] Documentation complete (README.md)
