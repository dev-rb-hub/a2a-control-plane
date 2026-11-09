import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from a2a_control_plane.dev import DevCA
from a2a_control_plane.devpki import (
    CertificateError,
    spiffe_id_from_certificate,
    verify_svid_chain,
    verify_svid_pem,
)
from a2a_control_plane.identity import SpiffeId

TD = "example.org"
NOW = 1_800_000_000.0
WORKER = SpiffeId.worker(TD, "z1", "w1")


def _bundle(ca: DevCA, td: str = TD) -> list[x509.Certificate]:
    return x509.load_pem_x509_certificates(ca.trust_bundle_pem(td))


def _leaf(pem: bytes) -> list[x509.Certificate]:
    return x509.load_pem_x509_certificates(pem)


def _forged(
    ca: DevCA,
    *,
    sans: list[x509.GeneralName] | None = None,
    is_ca: bool = False,
    digital_signature: bool = True,
    start: float = NOW,
    lifetime: float = 600,
) -> list[x509.Certificate]:
    """A leaf signed by ``ca``'s real key but with a deliberately wrong profile."""
    key = Ed25519PrivateKey.generate()
    root = _bundle(ca)[0]
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([]))
        .issuer_name(root.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.fromtimestamp(start, dt.UTC))
        .not_valid_after(dt.datetime.fromtimestamp(start + lifetime, dt.UTC))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature, False, False, False, False, is_ca, False, False, False
            ),
            critical=True,
        )
    )
    if sans is not None:
        builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    return [builder.sign(ca._root_key, None)]


def _uri(identity: SpiffeId) -> x509.GeneralName:
    return x509.UniformResourceIdentifier(str(identity))


def test_valid_svid_roundtrip() -> None:
    ca = DevCA()
    svid = ca.issue_x509(WORKER, NOW)
    assert verify_svid_pem(svid.cert_chain_pem, svid.trust_bundle_pem, TD, NOW + 1) == WORKER
    assert spiffe_id_from_certificate(_leaf(svid.cert_chain_pem)[0]) == WORKER
    assert b"PRIVATE KEY" in svid.private_key_pem


def test_leaf_profile() -> None:
    leaf = _leaf(DevCA().issue_x509(WORKER, NOW).cert_chain_pem)[0]
    assert leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    usage = leaf.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.digital_signature and not usage.key_cert_sign
    assert leaf.not_valid_after_utc - leaf.not_valid_before_utc <= dt.timedelta(hours=1)


def test_existing_challenge_signing_still_works() -> None:
    ca = DevCA()
    cred = ca.issue(WORKER)
    ca.public_key_for(WORKER).verify(cred.sign(b"m"), b"m")  # type: ignore[union-attr]
    svid = ca.issue_x509(WORKER, NOW)
    assert ca.public_key_for(WORKER) is not None
    assert svid.identity == WORKER


def test_wrong_trust_domain() -> None:
    ca = DevCA()
    svid = ca.issue_x509(SpiffeId.worker("other.org", "z1", "w1"), NOW)
    with pytest.raises(CertificateError, match="trust domain"):
        verify_svid_chain(_leaf(svid.cert_chain_pem), _bundle(ca, "other.org"), TD, NOW)


def test_missing_san() -> None:
    ca = DevCA()
    ca.trust_bundle_pem(TD)
    with pytest.raises(CertificateError, match="subject alternative name"):
        verify_svid_chain(_forged(ca), _bundle(ca), TD, NOW)


def test_zero_uri_sans() -> None:
    ca = DevCA()
    ca.trust_bundle_pem(TD)
    chain = _forged(ca, sans=[x509.DNSName("a.example.org")])
    with pytest.raises(CertificateError, match="exactly one URI"):
        verify_svid_chain(chain, _bundle(ca), TD, NOW)


def test_multiple_uri_sans() -> None:
    ca = DevCA()
    ca.trust_bundle_pem(TD)
    chain = _forged(ca, sans=[_uri(WORKER), _uri(SpiffeId.worker(TD, "z1", "w2"))])
    with pytest.raises(CertificateError, match="exactly one URI"):
        verify_svid_chain(chain, _bundle(ca), TD, NOW)


def test_non_spiffe_or_unknown_role_uri() -> None:
    ca = DevCA()
    ca.trust_bundle_pem(TD)
    for uri in ("https://example.org/x", f"spiffe://{TD}/something/else"):
        chain = _forged(ca, sans=[x509.UniformResourceIdentifier(uri)])
        with pytest.raises(CertificateError, match="SPIFFE ID"):
            verify_svid_chain(chain, _bundle(ca), TD, NOW)


def test_ca_flag_on_leaf_rejected() -> None:
    ca = DevCA()
    ca.trust_bundle_pem(TD)
    chain = _forged(ca, sans=[_uri(WORKER)], is_ca=True)
    with pytest.raises(CertificateError, match="must not be a CA"):
        verify_svid_chain(chain, _bundle(ca), TD, NOW)


def test_missing_digital_signature_rejected() -> None:
    ca = DevCA()
    ca.trust_bundle_pem(TD)
    chain = _forged(ca, sans=[_uri(WORKER)], digital_signature=False)
    with pytest.raises(CertificateError, match="digitalSignature"):
        verify_svid_chain(chain, _bundle(ca), TD, NOW)


def test_expired_and_not_yet_valid() -> None:
    ca = DevCA()
    svid = ca.issue_x509(WORKER, NOW, lifetime=60)
    chain, bundle = _leaf(svid.cert_chain_pem), _bundle(ca)
    assert verify_svid_chain(chain, bundle, TD, NOW + 60) == WORKER
    with pytest.raises(CertificateError, match="expired"):
        verify_svid_chain(chain, bundle, TD, NOW + 61)
    with pytest.raises(CertificateError, match="not yet valid"):
        verify_svid_chain(chain, bundle, TD, NOW - 1)


def test_lifetime_over_one_hour_rejected() -> None:
    ca = DevCA()
    ca.trust_bundle_pem(TD)
    chain = _forged(ca, sans=[_uri(WORKER)], lifetime=7200)
    with pytest.raises(CertificateError, match="one hour"):
        verify_svid_chain(chain, _bundle(ca), TD, NOW)
    with pytest.raises(ValueError):
        ca.issue_x509(WORKER, NOW, lifetime=7200)


def test_cert_from_another_ca() -> None:
    ca, other = DevCA(), DevCA()
    svid = other.issue_x509(WORKER, NOW)
    ca.trust_bundle_pem(TD)
    with pytest.raises(CertificateError, match="not issued by"):
        verify_svid_chain(_leaf(svid.cert_chain_pem), _bundle(ca), TD, NOW)


def test_extra_chain_certs_and_bad_pem_rejected() -> None:
    ca = DevCA()
    svid = ca.issue_x509(WORKER, NOW)
    with pytest.raises(CertificateError, match="exactly the leaf"):
        verify_svid_pem(svid.cert_chain_pem + svid.trust_bundle_pem, svid.trust_bundle_pem, TD, NOW)
    with pytest.raises(CertificateError, match="PEM"):
        verify_svid_pem(b"garbage", svid.trust_bundle_pem, TD, NOW)
