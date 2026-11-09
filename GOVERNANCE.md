# Governance

This document describes how the a2a-control-plane specification is changed.

## Roles

- **Contributors**: anyone who opens an issue or pull request. Contributors have no write access to the repository and submit changes from forks.
- **Maintainer**: [@dev-rb-hub](https://github.com/dev-rb-hub), the sole Editor and repository owner. The Maintainer reviews changes, enforces RFC 2119 language, and merges approved changes.
- **Editors**: additional maintainers MAY be appointed by the Maintainer. They are listed in [`.github/CODEOWNERS`](.github/CODEOWNERS), which is the authoritative list.
- **Working Group**: the Editors and active contributors who discuss normative changes. The Maintainer makes the final decision.

## Change Process (RFC)

1. **Issue**: open an issue (use the *Spec Change* template) describing the scaling or security problem, the affected documents, and the proposed direction.
2. **Discussion**: the issue stays open for at least 14 days for normative changes. Editorial fixes (typos, formatting) skip this step.
3. **Pull Request**: submit a PR that modifies the relevant document in [`spec/`](spec/), and the schemas in [`schemas/`](schemas/) or reference material in [`reference/`](reference/) where affected.
4. **Review**: every pull request MUST be approved by a code owner listed in [`.github/CODEOWNERS`](.github/CODEOWNERS) before merge. The `main` branch is protected: direct pushes and force pushes are not allowed. While the Maintainer is the only code owner, the Maintainer MAY merge their own pull requests once all required checks pass; for normative changes the discussion period of step 2 still applies. This exception ends when a second Editor is appointed.
5. **Merge**: approved changes are merged and recorded in [`CHANGELOG.md`](CHANGELOG.md).

Design decisions that do not change `spec/` or `schemas/` (for example SDK architecture) MAY be recorded as numbered documents in [`rfcs/`](rfcs/). Each states its status (proposed, accepted, rejected or superseded) and links the issue where it was decided. They are not normative and do not need the 14-day discussion window.

## Compatibility Rules

- Schemas under `schemas/v<N>/` MUST remain backward compatible. Fields MUST NOT be renumbered or have their type changed; removed fields MUST be `reserved`.
- A breaking change requires a new schema major version directory (for example `schemas/v2/`) and a new spec major version.
- Security-relevant changes MUST include a *Security Considerations* update in the affected spec document.

## Versioning

The specification uses Semantic Versioning (`MAJOR.MINOR.PATCH`):

- **MAJOR**: breaking wire or behavioral changes.
- **MINOR**: backward-compatible additions.
- **PATCH**: clarifications and editorial fixes.

Versions below `1.0.0` are drafts and MAY change without a major bump.

## Decision Making

The Working Group aims for rough consensus. When consensus cannot be reached, the Maintainer decides and records the rationale in the issue.
