"""Memory-bounded replay protection for task vouchers (spec 04, section 4.4, check 7).

A voucher is accepted once. The store therefore has to remember a ``voucher_id`` until the voucher
can no longer be presented, which is its expiry plus the tolerated clock skew, and not a moment
earlier.

This is deliberately NOT an LRU cache. An LRU evicts the entry that was used least recently, and
for replay protection that is the wrong rule: a live voucher that is evicted can be replayed. The
only entries that may be dropped are expired ones. When the store is full of live entries it fails
closed and refuses new submissions, so an attacker who fills it denies service but never gets a
voucher accepted twice. Memory is bounded by ``max_entries``, and a per-client share keeps one
client from filling the store for everyone else.
"""

from __future__ import annotations

import heapq

__all__ = ["ReplayStoreFull", "VoucherReplayStore"]


class ReplayStoreFull(Exception):
    """No room to remember another voucher; a transport maps it to RESOURCE_EXHAUSTED."""


class VoucherReplayStore:
    def __init__(self, max_entries: int = 100_000, max_per_client: int | None = None) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.max_entries = max_entries
        self.max_per_client = (
            max_per_client if max_per_client is not None else max(1, max_entries // 4)
        )
        self._entries: dict[bytes, tuple[int, str]] = {}  # voucher_id -> (forget_at_ns, client)
        self._expiry: list[tuple[int, bytes]] = []  # min-heap of (forget_at_ns, voucher_id)
        self._per_client: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def consume(self, voucher_id: bytes, client: str, forget_at_ns: int, now_ns: int) -> None:
        """Record ``voucher_id`` as spent.

        Raises PermissionError if it was already spent and ReplayStoreFull if there is no room.
        Nothing is recorded when it raises.
        """
        self._prune(now_ns)
        if voucher_id in self._entries:
            raise PermissionError("voucher was already used")
        if len(self._entries) >= self.max_entries:
            raise ReplayStoreFull("replay store is full of unexpired vouchers")
        if self._per_client.get(client, 0) >= self.max_per_client:
            raise ReplayStoreFull("client has too many unexpired vouchers")
        self._entries[voucher_id] = (forget_at_ns, client)
        heapq.heappush(self._expiry, (forget_at_ns, voucher_id))
        self._per_client[client] = self._per_client.get(client, 0) + 1

    def _prune(self, now_ns: int) -> None:
        """Forget exactly the entries whose voucher can no longer be presented."""
        while self._expiry and self._expiry[0][0] < now_ns:
            forget_at, voucher_id = heapq.heappop(self._expiry)
            entry = self._entries.get(voucher_id)
            if entry is None or entry[0] != forget_at:
                continue
            del self._entries[voucher_id]
            remaining = self._per_client[entry[1]] - 1
            if remaining:
                self._per_client[entry[1]] = remaining
            else:
                del self._per_client[entry[1]]
