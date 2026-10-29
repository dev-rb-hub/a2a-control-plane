"""Length-prefixed encoding for the byte strings that are signed or hashed (spec 04, 2.3 and 4).

Each field is a 4-byte big-endian length followed by the field bytes, so no field can be confused
with its neighbour. Protobuf serialization is not canonical, which is why signed and hashed data is
built from fields in a fixed order instead of from a serialized message.
"""

from __future__ import annotations

import struct

__all__ = ["length_prefixed"]


def length_prefixed(*fields: bytes) -> bytes:
    return b"".join(struct.pack(">I", len(field)) + field for field in fields)
