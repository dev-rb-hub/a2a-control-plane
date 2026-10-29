"""Reconnect delay with full jitter (spec 05, section 6).

After an Aggregator restart every Worker reconnects at once. Spreading the attempts over the whole
backoff window keeps that burst from becoming the noise the control plane exists to avoid.
"""

from __future__ import annotations

import random
from collections.abc import Callable

__all__ = ["reconnect_delay"]


def reconnect_delay(
    attempt: int,
    *,
    initial: float = 1.0,
    factor: float = 2.0,
    maximum: float = 60.0,
    rng: Callable[[], float] = random.random,
) -> float:
    """Seconds to wait before reconnect ``attempt`` (0 for the first retry).

    The delay is uniform in ``[0, min(maximum, initial * factor ** attempt)]``.
    """
    if attempt < 0 or initial <= 0 or factor < 1 or maximum < initial:
        raise ValueError("require attempt >= 0, initial > 0, factor >= 1, maximum >= initial")
    try:
        ceiling = min(maximum, initial * factor**attempt)
    except OverflowError:
        ceiling = maximum
    return rng() * ceiling
