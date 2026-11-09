//! Byte-exact payloads that are signed or hashed (spec 04, sections 2.3, 4.2 and 4.3).
//!
//! Protobuf serialization is not canonical, so signed and hashed data is built from fields in a
//! fixed order, each prefixed with its 4-byte big-endian length. These functions must produce the
//! same bytes in every implementation.

use sha2::{Digest, Sha256};

use crate::identity::SpiffeId;
use crate::proto::Task;

const REGISTER_LABEL: &[u8] = b"a2a-cp/v1/register";
const TASK_LABEL: &[u8] = b"a2a-cp/v1/task";
const VOUCHER_LABEL: &[u8] = b"a2a-cp/v1/voucher";

/// Each field as a 4-byte big-endian length followed by its bytes.
pub fn length_prefixed(fields: &[&[u8]]) -> Vec<u8> {
    let mut out = Vec::with_capacity(fields.iter().map(|f| 4 + f.len()).sum());
    for field in fields {
        let len = u32::try_from(field.len()).expect("field shorter than 4 GiB");
        out.extend_from_slice(&len.to_be_bytes());
        out.extend_from_slice(field);
    }
    out
}

fn sha256(data: &[u8]) -> [u8; 32] {
    Sha256::digest(data).into()
}

/// SHA-256 binding a voucher to one task (spec 04, section 4.2).
///
/// Covers kind, content type, payload, deadline and required capabilities sorted by key. Leaves out
/// `task_id` and `attempt`, which the control plane assigns, and `trace_context`.
pub fn task_digest(task: &Task) -> [u8; 32] {
    let deadline = task.deadline_ns.to_be_bytes();
    let count = u32::try_from(task.required_capabilities.len())
        .expect("capability count fits in u32")
        .to_be_bytes();
    let mut fields: Vec<&[u8]> = vec![
        TASK_LABEL,
        task.kind.as_bytes(),
        task.content_type.as_bytes(),
        &task.payload,
        &deadline,
        &count,
    ];
    // BTreeMap iterates in key order, which for UTF-8 strings is code point order.
    for (key, value) in &task.required_capabilities {
        fields.push(key.as_bytes());
        fields.push(value.as_bytes());
    }
    sha256(&length_prefixed(&fields))
}

/// What a Worker signs to answer a registration challenge (spec 04, section 2.3).
pub fn challenge_payload(
    nonce: &[u8],
    worker: &SpiffeId,
    register_request: &[u8],
    channel_binding: &[u8],
) -> Vec<u8> {
    let id = worker.to_string();
    let request_digest = sha256(register_request);
    length_prefixed(&[
        REGISTER_LABEL,
        nonce,
        id.as_bytes(),
        &request_digest,
        channel_binding,
    ])
}

/// What the CCR signs for a voucher: a label and the exact serialized `TaskVoucher`.
pub fn voucher_signing_payload(voucher: &[u8]) -> Vec<u8> {
    length_prefixed(&[VOUCHER_LABEL, voucher])
}
