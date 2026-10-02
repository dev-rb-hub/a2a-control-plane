"""Traffic comparison for N agents over one hour, using the SDK's real wire encodings.

    mesh   - every agent polls every other agent for full state every 5s (O(N^2))
    hub    - every agent reports full state to a hub every 5s (O(N))
    delta  - spec behavior: delta on change + adaptive heartbeats (5s doubling to 60s)

Run from sdk/python:  python examples/traffic_benchmark.py
"""

from __future__ import annotations

from a2a_control_plane.delta import make_delta, make_heartbeat
from a2a_control_plane.heartbeat import AdaptiveHeartbeat

DURATION = 3600.0  # seconds
POLL_INTERVAL = 5.0
CHANGE_EVERY = 600.0  # one capability change per agent per 10 minutes
CAPABILITIES = {f"capability-{i}": f"value-{i}" for i in range(8)}

FULL = len(make_delta("agent-0001", "zone-1", CAPABILITIES, timestamp_ns=1).SerializeToString())
CHANGE = len(make_delta("agent-0001", "zone-1", {"slots": "3"}, timestamp_ns=1).SerializeToString())
HEARTBEAT = len(make_heartbeat("agent-0001", "zone-1", timestamp_ns=1).SerializeToString())


def mesh(n: int) -> tuple[int, int]:
    messages = int(n * (n - 1) * DURATION / POLL_INTERVAL)
    return messages, messages * FULL


def hub(n: int) -> tuple[int, int]:
    messages = int(n * DURATION / POLL_INTERVAL)
    return messages, messages * FULL


def delta_adaptive(n: int) -> tuple[int, int]:
    heartbeat = AdaptiveHeartbeat()
    messages, size = 1, FULL  # initial full capability set
    heartbeat.record_state_change(0.0)
    next_change = CHANGE_EVERY
    while True:
        due = heartbeat.next_due()
        if min(due, next_change) > DURATION:
            break
        if next_change <= due:
            heartbeat.record_state_change(next_change)
            messages, size = messages + 1, size + CHANGE
            next_change += CHANGE_EVERY
        else:
            heartbeat.record_heartbeat(due)
            messages, size = messages + 1, size + HEARTBEAT
    return messages * n, size * n


def human(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} TiB"


def main() -> None:
    print(f"message sizes: full={FULL}B  change={CHANGE}B  heartbeat={HEARTBEAT}B")
    print(
        f"\n{'agents':>7} | {'strategy':<6} | {'messages/hour':>15} | {'bytes/hour':>12} | vs mesh"
    )
    for n in (10, 100, 1000):
        base_messages, _ = mesh(n)
        for name, fn in (("mesh", mesh), ("hub", hub), ("delta", delta_adaptive)):
            messages, size = fn(n)
            ratio = f"{base_messages / messages:,.0f}x fewer msgs" if name != "mesh" else ""
            print(f"{n:>7} | {name:<6} | {messages:>15,} | {human(size):>12} | {ratio}")
        print("-" * 70)


if __name__ == "__main__":
    main()
