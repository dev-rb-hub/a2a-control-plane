"""Token grammar shared by zone ids, agent ids, and subject segments (spec 02, section 6.1)."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def is_valid_token(value: str) -> bool:
    return _TOKEN_RE.fullmatch(value) is not None


def validate_token(value: str, what: str = "token") -> str:
    if not is_valid_token(value):
        raise ValueError(f"invalid {what} {value!r}: must match [A-Za-z0-9_-]{{1,64}}")
    return value
