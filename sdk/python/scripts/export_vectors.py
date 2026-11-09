"""Export language-neutral conformance vectors from the Python reference implementation.

Run from ``sdk/python``: ``python scripts/export_vectors.py``. Writes
``conformance/vectors/*.json`` at the repository root. ``tests/test_vectors.py`` fails if the
committed files differ from what this script produces, so the vectors cannot drift from the
reference. Every value is deterministic: no wall clock and no randomness.
"""

from __future__ import annotations

import itertools
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from a2a_control_plane.backoff import reconnect_delay
from a2a_control_plane.delta import capabilities_match, diff_capabilities, validate_capabilities
from a2a_control_plane.framing import length_prefixed
from a2a_control_plane.heartbeat import AdaptiveHeartbeat
from a2a_control_plane.identity import SpiffeId
from a2a_control_plane.registry import challenge_payload
from a2a_control_plane.state import WorkerEvent, WorkerState, next_state
from a2a_control_plane.subjects import (
    REGISTRY_AGGREGATOR_STATE_PATTERN,
    REGISTRY_DIAGNOSTIC_PATTERN,
    Subject,
    may_publish,
    may_subscribe,
    subject_matches,
)
from a2a_control_plane.tasks import make_task, task_digest
from a2a_control_plane.tokens import is_valid_token
from a2a_control_plane.voucher import TaskVoucher, voucher_signing_payload

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "conformance" / "vectors"
TD = "example.org"


_RECEIVING = (WorkerState.IDLE, WorkerState.EXECUTING)


def _heartbeat_valid(params: list[float]) -> bool:
    base, maximum, multiplier = params
    try:
        AdaptiveHeartbeat(base, maximum, int(multiplier))
    except ValueError:
        return False
    return True


def _capabilities_valid(capabilities: dict[str, str], allow_removal: bool) -> bool:
    try:
        validate_capabilities(capabilities, allow_removal=allow_removal)
    except ValueError:
        return False
    return True


def _fixed(value: float) -> Callable[[], float]:
    return lambda: value


def tokens() -> dict[str, Any]:
    values = [
        "a", "Z", "0", "worker-7", "eu_west_1", "A-b_C-9", "x" * 64,
        "", "x" * 65, "a.b", "a*", ">", "a b", "a/b", "ü", "zone:1", "\t",
    ]  # fmt: skip
    return {"cases": [{"value": v, "valid": is_valid_token(v)} for v in values]}


def spiffe_ids() -> dict[str, Any]:
    inputs = [
        "spiffe://example.org/registry",
        "spiffe://example.org/zone/eu-west-1/aggregator",
        "spiffe://example.org/zone/eu-west-1/agent/worker-7",
        "spiffe://example.org/client/batch_1",
        "spiffe://a.b-c_d.0/registry",
        "spiffe://Example.org/registry",
        "spiffe://example.org",
        "spiffe://example.org/",
        "spiffe:///registry",
        "http://example.org/registry",
        "spiffe://example.org/registry/extra",
        "spiffe://example.org/zone/eu-west-1",
        "spiffe://example.org/zone/eu.west/aggregator",
        "spiffe://example.org/zone/z1/agent/",
        "spiffe://example.org/zone/z1/agent/a/b",
        "spiffe://example.org/zone/*/aggregator",
        "spiffe://example.org/client/",
        "spiffe://example.org/admin",
        "spiffe://exa mple.org/registry",
    ]
    cases = []
    for uri in inputs:
        try:
            parsed = SpiffeId.parse(uri)
        except ValueError:
            cases.append({"uri": uri, "valid": False})
            continue
        cases.append(
            {
                "uri": uri,
                "valid": True,
                "trust_domain": parsed.trust_domain,
                "role": parsed.role.value,
                "zone_id": parsed.zone_id,
                "agent_id": parsed.agent_id,
                "client_id": parsed.client_id,
                "canonical": str(parsed),
            }
        )
    return {"cases": cases}


def subjects() -> dict[str, Any]:
    inputs = [
        "a2a.zone.z1.agent.w1.tasks",
        "a2a.zone.z1.agent.w1.state",
        "a2a.zone.z1.agent.w1.results",
        "a2a.zone.z1.agent.w1.control",
        "a2a.zone.z1.aggregator.state",
        "a2a.zone.z1.aggregator.tasks",
        "a2a.zone.z1.agent.w1.bogus",
        "a2a.zone.z1.agent.*.state",
        "a2a.zone.z1.agent.w1",
        "b2b.zone.z1.agent.w1.tasks",
        "a2a.region.z1.agent.w1.tasks",
        "a2a.zone..agent.w1.tasks",
        "a2a.zone.z1.agent.w1.tasks.extra",
    ]
    cases = []
    for value in inputs:
        try:
            s = Subject.parse(value)
        except ValueError:
            cases.append({"subject": value, "valid": False})
            continue
        cases.append(
            {
                "subject": value,
                "valid": True,
                "zone_id": s.zone_id,
                "agent_id": s.agent_id,
                "channel": s.channel.value,
            }
        )
    return {"cases": cases}


def matching() -> dict[str, Any]:
    pairs = [
        ("a2a.zone.z1.agent.w1.state", "a2a.zone.z1.agent.w1.state"),
        ("a2a.zone.z1.agent.*.state", "a2a.zone.z1.agent.w1.state"),
        ("a2a.zone.z1.agent.*.state", "a2a.zone.z2.agent.w1.state"),
        ("a2a.zone.z1.agent.*.state", "a2a.zone.z1.agent.w1.results"),
        ("a2a.zone.*.aggregator.state", "a2a.zone.z9.aggregator.state"),
        ("a2a.zone.*.aggregator.state", "a2a.zone.z9.agent.w1.state"),
        ("a2a.zone.>", "a2a.zone.z1.agent.w1.state"),
        ("a2a.zone.>", "a2a.zone.z1"),
        ("a2a.zone.>", "a2a.zone"),
        ("a2a.>", "a2a.zone.z1.aggregator.state"),
        ("*.zone.z1.aggregator.state", "a2a.zone.z1.aggregator.state"),
        ("a2a.zone.z1.agent.w1.state", "a2a.zone.z1.agent.w1"),
        ("a2a.zone.z1.agent.w1", "a2a.zone.z1.agent.w1.state"),
        ("a2a.zone.>.state", "a2a.zone.z1.state"),
        ("a2a..zone", "a2a..zone"),
        ("a2a.zo ne.z1", "a2a.zo ne.z1"),
    ]
    cases = []
    for pattern, subject in pairs:
        try:
            cases.append(
                {
                    "pattern": pattern,
                    "subject": subject,
                    "matches": subject_matches(pattern, subject),
                }
            )
        except ValueError:
            cases.append({"pattern": pattern, "subject": subject, "invalid_pattern": True})
    return {"cases": cases}


def acl() -> dict[str, Any]:
    identities = [
        SpiffeId.worker(TD, "z1", "w1"),
        SpiffeId.worker(TD, "z2", "w1"),
        SpiffeId.aggregator(TD, "z1"),
        SpiffeId.registry(TD),
        SpiffeId.client(TD, "c1"),
    ]
    targets = [
        *(f"a2a.zone.{z}.agent.{a}.{c}" for z in ("z1", "z2") for a in ("w1", "w2")
          for c in ("tasks", "state", "results", "control")),
        "a2a.zone.z1.aggregator.state",
        "a2a.zone.z2.aggregator.state",
        "a2a.zone.z1.aggregator.tasks",
        "a2a.zone.z1.agent.*.state",
        "a2a.zone.z1.agent.*.results",
        "a2a.zone.z1.agent.*.tasks",
        "a2a.zone.z2.agent.*.state",
        "a2a.zone.*.agent.w1.tasks",
        "a2a.zone.z1.agent.w1.*",
        "a2a.zone.z1.agent.>",
        REGISTRY_AGGREGATOR_STATE_PATTERN,
        REGISTRY_DIAGNOSTIC_PATTERN,
        "a2a.>",
        "a2a.zone.z1.agent.w1.state.>",
        "not a subject",
    ]  # fmt: skip
    return {
        "cases": [
            {
                "identity": str(identity),
                "target": target,
                "publish": may_publish(identity, target),
                "subscribe": may_subscribe(identity, target),
            }
            for identity in identities
            for target in targets
        ]
    }


def state_machine() -> dict[str, Any]:
    cases = []
    for state, event in itertools.product(WorkerState, WorkerEvent):
        target = next_state(state, event)
        cases.append(
            {
                "state": state.value,
                "event": event.value,
                "next": None if target is None else target.value,
            }
        )
    return {
        "states": [s.value for s in WorkerState],
        "events": [e.value for e in WorkerEvent],
        "can_receive_tasks": [s.value for s in WorkerState if s in _RECEIVING],
        "cases": cases,
    }


def heartbeat() -> dict[str, Any]:
    scenarios: list[dict[str, Any]] = [
        {
            "name": "backoff to max then reset",
            "params": [5.0, 60.0, 3],
            "steps": [["state_change", 0.0]]
            + [["heartbeat", t] for t in (5.0, 15.0, 35.0, 75.0, 135.0, 195.0)]
            + [["state_change", 200.0], ["heartbeat", 205.0]],
        },
        {
            "name": "configure clamps the current interval",
            "params": [5.0, 60.0, 3],
            "steps": [
                ["state_change", 0.0],
                ["heartbeat", 5.0],
                ["heartbeat", 15.0],
                ["configure", [1.0, 8.0, 2]],
                ["configure", [10.0, 5.0, 0]],
                ["configure", [None, None, 0]],
                ["heartbeat", 20.0],
                ["configure", [12.0, None, None]],
            ],
        },
    ]
    for scenario in scenarios:
        hb = AdaptiveHeartbeat(*scenario["params"])
        results = []
        for op, arg in scenario["steps"]:
            accepted = None
            if op == "state_change":
                hb.record_state_change(arg)
            elif op == "heartbeat":
                hb.record_heartbeat(arg)
            else:
                accepted = hb.configure(*arg)
            entry: dict[str, Any] = {
                "interval": hb.interval,
                "base_interval": hb.base_interval,
                "max_interval": hb.max_interval,
                "miss_multiplier": hb.miss_multiplier,
            }
            if accepted is not None:
                entry["accepted"] = accepted
            if hb.last_message_at is not None:
                deadline = hb.deadline()
                entry["next_due"] = hb.next_due()
                entry["deadline"] = deadline
                entry["degraded_at_deadline"] = hb.is_degraded(deadline)
                entry["degraded_after_deadline"] = hb.is_degraded(deadline + 0.001)
            results.append(entry)
        scenario["expected"] = results
    invalid = [[0.0, 60.0, 3], [10.0, 5.0, 3], [5.0, 60.0, 0], [-1.0, 60.0, 3]]
    return {
        "default_params": [5.0, 60.0, 3],
        "scenarios": scenarios,
        "invalid_params": [{"params": p, "valid": _heartbeat_valid(p)} for p in invalid],
    }


def capabilities() -> dict[str, Any]:
    maps: list[tuple[dict[str, str], bool]] = [
        ({"skill": "x"}, False),
        ({"a.b-c_D9": "v"}, False),
        ({"k" * 64: "v"}, False),
        ({"k" * 65: "v"}, False),
        ({"": "v"}, False),
        ({"bad key": "v"}, False),
        ({"skill": "v" * 256}, False),
        ({"skill": "v" * 257}, False),
        ({"skill": "é" * 128}, False),
        ({"skill": "é" * 129}, False),
        ({"skill": ""}, False),
        ({"skill": ""}, True),
        ({"cp.health": "ok"}, False),
        ({"cp.health": "fault"}, False),
        ({"cp.health": "degraded"}, False),
        ({"cp.health": ""}, False),
        ({"cp.health": ""}, True),
        ({"cp.other": "x"}, False),
        ({"cp.other": ""}, True),
        ({f"k{i}": "v" for i in range(64)}, False),
        ({f"k{i}": "v" for i in range(65)}, False),
    ]
    validate = [
        {
            "capabilities": m,
            "allow_removal": removal,
            "valid": _capabilities_valid(m, removal),
        }
        for m, removal in maps
    ]
    diffs: list[dict[str, Any]] = []
    for old, new in [
        ({}, {"a": "1"}),
        ({"a": "1"}, {"a": "1"}),
        ({"a": "1", "b": "2"}, {"a": "9", "c": "3"}),
        ({"a": "1"}, {}),
        ({"a": "1"}, {"a": ""}),
    ]:
        try:
            diffs.append({"old": old, "new": new, "changed": diff_capabilities(old, new)})
        except ValueError:
            diffs.append({"old": old, "new": new, "invalid": True})
    matches = [
        {"required": r, "advertised": a, "matches": capabilities_match(r, a)}
        for r, a in [
            ({}, {}),
            ({}, {"a": "1"}),
            ({"a": "1"}, {"a": "1", "b": "2"}),
            ({"a": "1"}, {"a": "2"}),
            ({"a": "1"}, {}),
            ({"a": "1", "b": "2"}, {"a": "1"}),
        ]
    ]
    return {"validate": validate, "diff": diffs, "match": matches}


def payloads() -> dict[str, Any]:
    """Byte-exact signed and hashed payloads: these must match across implementations."""
    framing = [
        {"fields": [f.hex() for f in fields], "encoded": length_prefixed(*fields).hex()}
        for fields in [(), (b"",), (b"a", b"bc"), (b"\x00" * 3, b"", b"\xff")]
    ]
    task_cases = [
        make_task("t1"),
        make_task(
            "t2",
            b"\x00\x01payload",
            kind="summarize",
            content_type="application/json",
            deadline_ns=1_800_000_000_123_456_789,
            required_capabilities={"skill": "summarize", "lang": "en", "cp.health": "ok"},
        ),
        make_task("t3", "é".encode(), kind="ü", required_capabilities={"b": "2", "a": "1"}),
    ]
    digests = [
        {
            "kind": t.kind,
            "content_type": t.content_type,
            "payload": t.payload.hex(),
            "deadline_ns": t.deadline_ns,
            "required_capabilities": dict(t.required_capabilities),
            "digest": task_digest(t).hex(),
        }
        for t in task_cases
    ]
    challenges = [
        {
            "nonce": nonce.hex(),
            "worker": str(worker),
            "register_request": request.hex(),
            "channel_binding": binding.hex(),
            "payload": challenge_payload(nonce, worker, request, binding).hex(),
        }
        for nonce, worker, request, binding in [
            (bytes(range(16)), SpiffeId.worker(TD, "z1", "w1"), b"", b""),
            (b"\xaa" * 32, SpiffeId.worker(TD, "eu-west-1", "worker-7"), b"\x08\x01", b"\x01" * 32),
        ]
    ]
    return {"length_prefixed": framing, "task_digest": digests, "challenge_payload": challenges}


def vouchers() -> dict[str, Any]:
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = key.public_key().public_bytes_raw()
    voucher = TaskVoucher(
        voucher_id=bytes(range(16)),
        task_id="t1",
        client_spiffe_id=f"spiffe://{TD}/client/c1",
        zone_id="z1",
        instance_id="i1",
        epoch=3,
        task_digest=task_digest(make_task("t1")),
        issued_at_ns=1_800_000_000_000_000_000,
        expires_at_ns=1_800_000_030_000_000_000,
    ).SerializeToString()
    payload = voucher_signing_payload(voucher)
    signature = key.sign(payload)
    tampered = bytearray(signature)
    tampered[0] ^= 1
    return {
        "seed": bytes(range(32)).hex(),
        "public_key": public.hex(),
        "voucher": voucher.hex(),
        "signing_payload": payload.hex(),
        "signature": signature.hex(),
        "cases": [
            {"voucher": voucher.hex(), "signature": signature.hex(), "valid": True},
            {"voucher": voucher.hex(), "signature": bytes(tampered).hex(), "valid": False},
            {"voucher": (voucher + b"\x00").hex(), "signature": signature.hex(), "valid": False},
        ],
    }


def backoff() -> dict[str, Any]:
    cases = []
    for attempt, rng, params in [
        (0, 0.0, (1.0, 2.0, 60.0)),
        (0, 0.5, (1.0, 2.0, 60.0)),
        (3, 0.5, (1.0, 2.0, 60.0)),
        (5, 0.999, (1.0, 2.0, 60.0)),
        (6, 1.0, (1.0, 2.0, 60.0)),
        (10_000, 0.25, (1.0, 2.0, 60.0)),
        (2, 0.5, (0.5, 3.0, 4.0)),
        (-1, 0.5, (1.0, 2.0, 60.0)),
        (0, 0.5, (0.0, 2.0, 60.0)),
        (0, 0.5, (1.0, 0.5, 60.0)),
        (0, 0.5, (2.0, 2.0, 1.0)),
    ]:
        initial, factor, maximum = params
        case: dict[str, Any] = {
            "attempt": attempt,
            "rng": rng,
            "initial": initial,
            "factor": factor,
            "maximum": maximum,
        }
        try:
            case["delay"] = reconnect_delay(
                attempt, initial=initial, factor=factor, maximum=maximum, rng=_fixed(rng)
            )
        except ValueError:
            case["invalid"] = True
        cases.append(case)
    return {"cases": cases}


VECTORS: dict[str, Callable[[], dict[str, Any]]] = {
    "tokens": tokens,
    "spiffe_ids": spiffe_ids,
    "subjects": subjects,
    "subject_matching": matching,
    "acl": acl,
    "state_machine": state_machine,
    "heartbeat": heartbeat,
    "capabilities": capabilities,
    "payloads": payloads,
    "vouchers": vouchers,
    "backoff": backoff,
}


def render(name: str) -> str:
    body = {"vector": name, "version": 1, **VECTORS[name]()}
    return json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in VECTORS:
        (OUT / f"{name}.json").write_text(render(name), encoding="utf-8", newline="\n")
    print(f"wrote {len(VECTORS)} vector files to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
