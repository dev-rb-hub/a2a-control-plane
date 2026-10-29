"""Task vouchers: the CCR's signed authorization to submit one task to one Aggregator instance.

Pure logic with no I/O (spec 04, section 4). The CCR signs with ``sign_voucher``; an Aggregator
checks the signature with ``open_voucher`` and then applies the rules of spec 04, section 4.4.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from google.protobuf.message import DecodeError

from a2a_control_plane._proto.submission_pb2 import SignedTaskVoucher, TaskVoucher
from a2a_control_plane.framing import length_prefixed

__all__ = [
    "SignedTaskVoucher",
    "TaskVoucher",
    "open_voucher",
    "sign_voucher",
    "voucher_signing_payload",
]

_VOUCHER_LABEL = b"a2a-cp/v1/voucher"


def voucher_signing_payload(voucher: bytes) -> bytes:
    """What the CCR signs: a label, so the signature cannot be replayed as another kind of
    statement, and the exact serialized voucher."""
    return length_prefixed(_VOUCHER_LABEL, voucher)


def sign_voucher(
    voucher: TaskVoucher,
    sign: Callable[[bytes], bytes],
    signer_chain: Sequence[bytes] = (),
) -> SignedTaskVoucher:
    serialized = voucher.SerializeToString()
    return SignedTaskVoucher(
        voucher=serialized,
        signature=sign(voucher_signing_payload(serialized)),
        signer_chain=signer_chain,
    )


def open_voucher(signed: SignedTaskVoucher, key: Ed25519PublicKey) -> TaskVoucher:
    """Verify the signature and return the voucher. Raises PermissionError if it does not verify."""
    try:
        key.verify(signed.signature, voucher_signing_payload(signed.voucher))
        return TaskVoucher.FromString(signed.voucher)
    except (InvalidSignature, DecodeError) as error:
        raise PermissionError("voucher signature is not valid") from error
