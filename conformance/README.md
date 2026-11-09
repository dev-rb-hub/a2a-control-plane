# Conformance vectors

Language-neutral test vectors for implementations of the a2a-control-plane specification. They are generated from the Python reference SDK by [`sdk/python/scripts/export_vectors.py`](../sdk/python/scripts/export_vectors.py), and `sdk/python/tests/test_vectors.py` fails if a committed file differs from what the script produces. The Rust SDK checks itself against them in `sdk/rust/tests/vectors.rs`.

The vectors are non-normative. They record what the reference implementation does. If a vector and the specification disagree, the specification wins and the disagreement is a bug to report.

## Format

Each file in [`vectors/`](vectors/) is a JSON object with `"vector"` (the file name without `.json`), `"version"` (currently 1) and the cases. Byte strings are lowercase hex. Every value is deterministic: there is no wall-clock time and no randomness.

| File | Covers | Spec |
|------|--------|------|
| `tokens.json` | Token grammar for zone, agent, client and task ids | 02 Section 6.1 |
| `spiffe_ids.json` | Parsing the four role-scoped SPIFFE ID forms, and their canonical string | 01 Section 2.1 |
| `subjects.json` | Parsing concrete subjects | 02 Section 6.1 |
| `subject_matching.json` | `*` and `>` wildcards; `invalid_pattern` marks a pattern that must be rejected | 02 Section 6.3 |
| `acl.json` | `publish` and `subscribe` permission for each identity and subject or pattern | 02 Section 6.3 |
| `state_machine.json` | The target state for every (state, event) pair; `null` means the transition is not allowed | 02 Section 5 |
| `heartbeat.json` | Interval doubling, reset, reconfiguration and liveness deadlines over a sequence of steps | 03 Section 6 |
| `capabilities.json` | Capability map limits and reserved keys, diffs and matching | 03 Section 5.2 |
| `payloads.json` | Byte-exact length-prefixed framing, task digest and registration challenge payload | 04 Sections 2.3, 4.2 |
| `vouchers.json` | Voucher signing payload and Ed25519 signature verification with a fixed test key | 04 Section 4.3 |
| `backoff.json` | Reconnect delay with an injected random sample | 05 Section 6 |

## Updating

After changing behaviour in the Python core, run from `sdk/python`:

```
python scripts/export_vectors.py
```

and commit the regenerated files with the change. Every implementation must then pass the new vectors.
