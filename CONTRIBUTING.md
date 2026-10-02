# Contributing

This repository holds an open standard (`spec/`, `schemas/`, `reference/`) and a reference Python SDK (`sdk/python/`). Read [GOVERNANCE.md](GOVERNANCE.md) first: every change needs approval from a code owner, and `main` is protected, so work from a fork and open a pull request.

## Where to start

Open issues are the roadmap. Look for the labels `good first issue` and `help wanted`. Each issue lists the files involved and what "done" means. Comment on an issue before starting a large one so work is not duplicated.

## Changing the specification

1. Open an issue with the *Spec Change* template.
2. Normative statements use RFC 2119 words (MUST, SHOULD, MAY) in capitals, and only for requirements.
3. Keep the spec, the schemas, the SDK and the tests consistent in the same pull request. A spec change without matching tests, or a behavior change without a spec change, will be sent back.
4. Schemas are append-only within `v1`: never renumber, retype or reuse a field number. Add new fields and messages instead.

## Working on the Python SDK

```bash
cd sdk/python
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -q && ruff check . && ruff format --check . && mypy src examples
```

- Edit `.proto` files in `schemas/v1/`, then run `python scripts/gen_proto.py`. Never hand-edit `src/a2a_control_plane/_proto/`. CI fails if the generated code differs from the schemas.
- Tests use the in-memory `DevCluster` with a manual clock, so they are deterministic and fast. Prefer testing behavior through it over mocking.
- The core modules (`tokens`, `identity`, `subjects`, `state`, `heartbeat`, `delta`, `tasks`) do no I/O. Keep them that way.

## Pull requests

- One logical change per pull request, with tests.
- All CI checks must pass.
- Describe which spec section a change implements or modifies.

By contributing you agree your work is released under the repository [LICENSE](LICENSE).
