"""Development X.509-SVID issuance and verification (spec 01, section 3).

This is NOT a SPIRE replacement. It has no attestation, rotation, federation or revocation
list: a single self-signed Ed25519 root signs short-lived leaf certificates so that mTLS can be
configured in tests and local runs. Everything here is pure: time is passed in as ``now``
(seconds since the epoch) and certificates and keys are returned as bytes.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from a2a_control_plane.identity import SpiffeId

MAX_SVID_LIFETIME_SECONDS = 3600
_ROOT_NOT_BEFORE = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
_ROOT_NOT_AFTER = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)
# cryptography parses extensions lazily, so a malformed peer certificate can raise any of these
# on first access; DuplicateExtension and UnsupportedGeneralNameType are not ValueErrors.
_PARSE_ERRORS: tuple[type[Exception], ...] = (
    ValueError,
    TypeError,
    x509.DuplicateExtension,
    x509.UnsupportedGeneralNameType,
)


class CertificateError(ValueError):
    """A certificate does not satisfy the X.509-SVID rules or does not chain to the bundle."""


@dataclass(frozen=True, slots=True)
class X509Svid:
    """An issued X.509-SVID: PEM chain (leaf first), PKCS#8 PEM private key, and trust bundle."""

    identity: SpiffeId
    cert_chain_pem: bytes
    private_key_pem: bytes
    trust_bundle_pem: bytes


def _at(now: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(now, dt.UTC)


def new_root(key: Ed25519PrivateKey, trust_domain: str) -> x509.Certificate:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"a2a dev CA {trust_domain}")])
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_ROOT_NOT_BEFORE)
        .not_valid_after(_ROOT_NOT_AFTER)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, None)
    )


def issue_leaf(
    root_key: Ed25519PrivateKey,
    root: x509.Certificate,
    identity: SpiffeId,
    leaf_key: Ed25519PrivateKey,
    now: float,
    lifetime: float = MAX_SVID_LIFETIME_SECONDS,
) -> x509.Certificate:
    if not 0 < lifetime <= MAX_SVID_LIFETIME_SECONDS:
        raise ValueError(f"SVID lifetime must be in (0, {MAX_SVID_LIFETIME_SECONDS}] seconds")
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(identity))]))
        .issuer_name(root.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_at(now))
        .not_valid_after(_at(now + lifetime))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(str(identity))]),
            critical=False,
        )
        .sign(root_key, None)
    )


def pem_chain(certs: Sequence[x509.Certificate]) -> bytes:
    return b"".join(c.public_bytes(serialization.Encoding.PEM) for c in certs)


def private_key_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def spiffe_id_from_certificate(cert: x509.Certificate) -> SpiffeId:
    """The SPIFFE ID in the single URI SAN of ``cert``.

    Rejects a missing SAN extension, zero or several URI SANs, and an ID that is not one of the
    role-scoped forms of spec 01. The subject is never consulted. Does not check trust or time.
    """
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound as exc:
        raise CertificateError("certificate has no subject alternative name") from exc
    except _PARSE_ERRORS as exc:
        raise CertificateError(f"malformed certificate extensions: {exc}") from exc
    uris = san.get_values_for_type(x509.UniformResourceIdentifier)
    if len(uris) != 1:
        raise CertificateError(f"expected exactly one URI SAN, found {len(uris)}")
    try:
        return SpiffeId.parse(uris[0])
    except ValueError as exc:
        raise CertificateError(f"URI SAN is not a valid role-scoped SPIFFE ID: {exc}") from exc


def _check_leaf_profile(cert: x509.Certificate) -> None:
    try:
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound as exc:
        raise CertificateError("leaf lacks basic constraints or key usage") from exc
    if basic.ca:
        raise CertificateError("leaf certificate must not be a CA")
    if not usage.digital_signature:
        raise CertificateError("leaf key usage must include digitalSignature")
    if usage.key_cert_sign or usage.crl_sign:
        raise CertificateError("leaf key usage must not include keyCertSign or cRLSign")


def verify_svid_chain(
    chain: Sequence[x509.Certificate],
    trust_bundle: Sequence[x509.Certificate],
    trust_domain: str,
    now: float,
) -> SpiffeId:
    """Verify a peer's X.509-SVID against the trust bundle and return its SPIFFE ID.

    ``chain`` is the leaf alone: the dev PKI has no intermediates, so the leaf must be issued
    directly by a certificate in ``trust_bundle``. Checks signature, validity at ``now``, the
    leaf profile (not a CA, digitalSignature), a lifetime of at most one hour, exactly one URI
    SAN, and that the ID belongs to ``trust_domain``. Every rejection, including a certificate
    that ``cryptography`` cannot parse lazily (for example a duplicate extension), raises
    CertificateError.
    """
    try:
        return _verify_svid_chain(chain, trust_bundle, trust_domain, now)
    except CertificateError:
        raise
    except _PARSE_ERRORS as exc:
        raise CertificateError(f"malformed certificate: {exc}") from exc


def _verify_svid_chain(
    chain: Sequence[x509.Certificate],
    trust_bundle: Sequence[x509.Certificate],
    trust_domain: str,
    now: float,
) -> SpiffeId:
    if len(chain) != 1:
        raise CertificateError("chain must contain exactly the leaf certificate")
    leaf = chain[0]
    when = _at(now)
    if not leaf.not_valid_before_utc <= when <= leaf.not_valid_after_utc:
        raise CertificateError("certificate is expired or not yet valid")
    if leaf.not_valid_after_utc - leaf.not_valid_before_utc > dt.timedelta(
        seconds=MAX_SVID_LIFETIME_SECONDS
    ):
        raise CertificateError("certificate lifetime exceeds one hour")
    _check_leaf_profile(leaf)
    identity = spiffe_id_from_certificate(leaf)
    if identity.trust_domain != trust_domain:
        raise CertificateError(f"wrong trust domain {identity.trust_domain!r}")
    for root in trust_bundle:
        try:
            ca = root.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        except x509.ExtensionNotFound:
            continue
        if not ca or not root.not_valid_before_utc <= when <= root.not_valid_after_utc:
            continue
        try:
            leaf.verify_directly_issued_by(root)
        except (ValueError, TypeError, InvalidSignature):
            continue
        return identity
    raise CertificateError("certificate was not issued by a CA in the trust bundle")


def verify_svid_pem(
    chain_pem: bytes, trust_bundle_pem: bytes, trust_domain: str, now: float
) -> SpiffeId:
    """``verify_svid_chain`` over PEM bytes, e.g. a peer certificate from a TLS handshake."""
    try:
        chain = x509.load_pem_x509_certificates(chain_pem)
        bundle = x509.load_pem_x509_certificates(trust_bundle_pem)
    except ValueError as exc:
        raise CertificateError(f"unparseable PEM: {exc}") from exc
    return verify_svid_chain(chain, bundle, trust_domain, now)
