//! Checking the CCR's signature on a task voucher (spec 04, section 4.3).
//!
//! The rules an Aggregator applies after the signature verifies (client, instance, epoch, digest,
//! validity window, single use) are in spec 04, section 4.4.

use ed25519_dalek::{Signature, VerifyingKey};
use prost::Message;

use crate::payloads::voucher_signing_payload;
use crate::proto::{SignedTaskVoucher, TaskVoucher};

/// The voucher was not signed by the expected key, or its bytes do not decode.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct InvalidVoucher;

/// Verify `signed` with the CCR's Ed25519 public key and return the voucher it carries.
pub fn open_voucher(
    signed: &SignedTaskVoucher,
    key: &[u8; 32],
) -> Result<TaskVoucher, InvalidVoucher> {
    let key = VerifyingKey::from_bytes(key).map_err(|_| InvalidVoucher)?;
    let signature =
        <[u8; 64]>::try_from(signed.signature.as_slice()).map_err(|_| InvalidVoucher)?;
    key.verify_strict(
        &voucher_signing_payload(&signed.voucher),
        &Signature::from_bytes(&signature),
    )
    .map_err(|_| InvalidVoucher)?;
    TaskVoucher::decode(signed.voucher.as_slice()).map_err(|_| InvalidVoucher)
}
