//! Checks the crate against the language-neutral vectors in `conformance/vectors`, which are
//! generated from the Python reference implementation (`sdk/python/scripts/export_vectors.py`).

use std::collections::BTreeMap;
use std::path::PathBuf;

use a2a_control_plane::backoff::reconnect_delay;
use a2a_control_plane::capabilities::{
    capabilities_match, diff_capabilities, validate_capabilities,
};
use a2a_control_plane::heartbeat::AdaptiveHeartbeat;
use a2a_control_plane::identity::SpiffeId;
use a2a_control_plane::payloads::{
    challenge_payload, length_prefixed, task_digest, voucher_signing_payload,
};
use a2a_control_plane::proto::{SignedTaskVoucher, Task};
use a2a_control_plane::state::{WorkerEvent, WorkerState, next_state};
use a2a_control_plane::subjects::{Subject, may_publish, may_subscribe, subject_matches};
use a2a_control_plane::tokens::is_valid_token;
use a2a_control_plane::voucher::open_voucher;
use serde_json::Value;

fn load(name: &str) -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../conformance/vectors")
        .join(format!("{name}.json"));
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let body: Value = serde_json::from_str(&text).expect("valid JSON");
    assert_eq!(body["vector"], name);
    assert_eq!(body["version"], 1);
    body
}

fn cases<'a>(body: &'a Value, key: &str) -> &'a Vec<Value> {
    let list = body[key]
        .as_array()
        .unwrap_or_else(|| panic!("no {key} array"));
    assert!(!list.is_empty(), "{key} is empty");
    list
}

fn s(v: &Value) -> &str {
    v.as_str().expect("string")
}

fn b(v: &Value) -> bool {
    v.as_bool().expect("bool")
}

fn f(v: &Value) -> f64 {
    v.as_f64().expect("number")
}

fn bytes(v: &Value) -> Vec<u8> {
    hex::decode(s(v)).expect("hex")
}

fn string_map(v: &Value) -> BTreeMap<String, String> {
    v.as_object()
        .expect("object")
        .iter()
        .map(|(k, v)| (k.clone(), s(v).to_owned()))
        .collect()
}

fn opt_str(v: &Value) -> Option<&str> {
    v.as_str()
}

fn opt_f64(v: &Value) -> Option<f64> {
    v.as_f64()
}

#[test]
fn tokens() {
    for case in cases(&load("tokens"), "cases") {
        assert_eq!(
            is_valid_token(s(&case["value"])),
            b(&case["valid"]),
            "{case}"
        );
    }
}

#[test]
fn spiffe_ids() {
    for case in cases(&load("spiffe_ids"), "cases") {
        let parsed = SpiffeId::parse(s(&case["uri"]));
        assert_eq!(parsed.is_ok(), b(&case["valid"]), "{case}");
        if let Ok(id) = parsed {
            assert_eq!(id.trust_domain(), s(&case["trust_domain"]));
            assert_eq!(id.role().as_str(), s(&case["role"]));
            assert_eq!(id.zone_id(), opt_str(&case["zone_id"]));
            assert_eq!(id.agent_id(), opt_str(&case["agent_id"]));
            assert_eq!(id.client_id(), opt_str(&case["client_id"]));
            assert_eq!(id.to_string(), s(&case["canonical"]));
        }
    }
}

#[test]
fn subjects() {
    for case in cases(&load("subjects"), "cases") {
        let parsed = Subject::parse(s(&case["subject"]));
        assert_eq!(parsed.is_some(), b(&case["valid"]), "{case}");
        if let Some(subject) = parsed {
            assert_eq!(subject.zone_id, s(&case["zone_id"]));
            assert_eq!(subject.agent_id.as_deref(), opt_str(&case["agent_id"]));
            assert_eq!(subject.channel.as_str(), s(&case["channel"]));
            assert_eq!(subject.to_string(), s(&case["subject"]));
        }
    }
}

#[test]
fn subject_matching() {
    for case in cases(&load("subject_matching"), "cases") {
        let result = subject_matches(s(&case["pattern"]), s(&case["subject"]));
        if case.get("invalid_pattern").is_some() {
            assert_eq!(result, None, "{case}");
        } else {
            assert_eq!(result, Some(b(&case["matches"])), "{case}");
        }
    }
}

#[test]
fn acl() {
    for case in cases(&load("acl"), "cases") {
        let identity = SpiffeId::parse(s(&case["identity"])).expect("identity");
        let target = s(&case["target"]);
        assert_eq!(
            may_publish(&identity, target),
            b(&case["publish"]),
            "publish {case}"
        );
        assert_eq!(
            may_subscribe(&identity, target),
            b(&case["subscribe"]),
            "subscribe {case}"
        );
    }
}

#[test]
fn state_machine() {
    let body = load("state_machine");
    let state_names: Vec<&str> = WorkerState::ALL.iter().map(|x| x.as_str()).collect();
    let event_names: Vec<&str> = WorkerEvent::ALL.iter().map(|x| x.as_str()).collect();
    let expect_states: Vec<&str> = cases(&body, "states").iter().map(s).collect();
    let expect_events: Vec<&str> = cases(&body, "events").iter().map(s).collect();
    assert_eq!(state_names, expect_states);
    assert_eq!(event_names, expect_events);
    let receiving: Vec<&str> = WorkerState::ALL
        .iter()
        .filter(|x| x.can_receive_tasks())
        .map(|x| x.as_str())
        .collect();
    let expect_receiving: Vec<&str> = cases(&body, "can_receive_tasks").iter().map(s).collect();
    assert_eq!(receiving, expect_receiving);

    let all = cases(&body, "cases");
    assert_eq!(all.len(), WorkerState::ALL.len() * WorkerEvent::ALL.len());
    for case in all {
        let state = *WorkerState::ALL
            .iter()
            .find(|x| x.as_str() == s(&case["state"]))
            .unwrap();
        let event = *WorkerEvent::ALL
            .iter()
            .find(|x| x.as_str() == s(&case["event"]))
            .unwrap();
        let next = next_state(state, event).map(WorkerState::as_str);
        assert_eq!(next, opt_str(&case["next"]), "{case}");
    }
}

#[test]
fn heartbeat() {
    let body = load("heartbeat");
    let defaults = AdaptiveHeartbeat::default();
    let p = body["default_params"].as_array().unwrap();
    assert_eq!(defaults.base_interval(), f(&p[0]));
    assert_eq!(defaults.max_interval(), f(&p[1]));
    assert_eq!(
        u64::from(defaults.miss_multiplier()),
        p[2].as_u64().unwrap()
    );

    for scenario in cases(&body, "scenarios") {
        let p = scenario["params"].as_array().unwrap();
        let multiplier = u32::try_from(p[2].as_u64().unwrap()).unwrap();
        let mut hb = AdaptiveHeartbeat::new(f(&p[0]), f(&p[1]), multiplier).unwrap();
        let steps = cases(scenario, "steps");
        let expected = cases(scenario, "expected");
        assert_eq!(steps.len(), expected.len());
        for (step, want) in steps.iter().zip(expected) {
            let accepted = match s(&step[0]) {
                "state_change" => {
                    hb.record_state_change(f(&step[1]));
                    None
                }
                "heartbeat" => {
                    hb.record_heartbeat(f(&step[1]));
                    None
                }
                "configure" => {
                    let args = step[1].as_array().unwrap();
                    let multiplier = args[2].as_u64().map(|m| u32::try_from(m).unwrap());
                    Some(hb.configure(opt_f64(&args[0]), opt_f64(&args[1]), multiplier))
                }
                other => panic!("unknown op {other}"),
            };
            let context = format!("{} / {step}", s(&scenario["name"]));
            assert_eq!(accepted, want.get("accepted").map(b), "{context}");
            assert_eq!(hb.interval(), f(&want["interval"]), "{context}");
            assert_eq!(hb.base_interval(), f(&want["base_interval"]), "{context}");
            assert_eq!(hb.max_interval(), f(&want["max_interval"]), "{context}");
            assert_eq!(
                u64::from(hb.miss_multiplier()),
                want["miss_multiplier"].as_u64().unwrap(),
                "{context}"
            );
            if let Some(deadline) = want.get("deadline") {
                let deadline = f(deadline);
                assert_eq!(hb.next_due(), Some(f(&want["next_due"])), "{context}");
                assert_eq!(hb.deadline(), Some(deadline), "{context}");
                assert_eq!(
                    hb.is_degraded(deadline),
                    b(&want["degraded_at_deadline"]),
                    "{context}"
                );
                assert_eq!(
                    hb.is_degraded(deadline + 0.001),
                    b(&want["degraded_after_deadline"]),
                    "{context}"
                );
            } else {
                assert_eq!(hb.deadline(), None, "{context}");
            }
        }
    }

    for case in cases(&body, "invalid_params") {
        let p = case["params"].as_array().unwrap();
        let multiplier = u32::try_from(p[2].as_u64().unwrap()).unwrap();
        let result = AdaptiveHeartbeat::new(f(&p[0]), f(&p[1]), multiplier);
        assert_eq!(result.is_ok(), b(&case["valid"]), "{case}");
    }
}

#[test]
fn capabilities() {
    let body = load("capabilities");
    for case in cases(&body, "validate") {
        let map = string_map(&case["capabilities"]);
        let result = validate_capabilities(
            map.iter().map(|(k, v)| (k.as_str(), v.as_str())),
            b(&case["allow_removal"]),
        );
        assert_eq!(result.is_ok(), b(&case["valid"]), "{case}");
    }
    for case in cases(&body, "diff") {
        let result = diff_capabilities(&string_map(&case["old"]), &string_map(&case["new"]));
        if case.get("invalid").is_some() {
            assert!(result.is_err(), "{case}");
        } else {
            assert_eq!(result.unwrap(), string_map(&case["changed"]), "{case}");
        }
    }
    for case in cases(&body, "match") {
        let matched = capabilities_match(
            &string_map(&case["required"]),
            &string_map(&case["advertised"]),
        );
        assert_eq!(matched, b(&case["matches"]), "{case}");
    }
}

#[test]
fn payloads() {
    let body = load("payloads");
    for case in cases(&body, "length_prefixed") {
        let fields: Vec<Vec<u8>> = case["fields"]
            .as_array()
            .unwrap()
            .iter()
            .map(bytes)
            .collect();
        let refs: Vec<&[u8]> = fields.iter().map(Vec::as_slice).collect();
        assert_eq!(length_prefixed(&refs), bytes(&case["encoded"]), "{case}");
    }
    for case in cases(&body, "task_digest") {
        let task = Task {
            kind: s(&case["kind"]).to_owned(),
            content_type: s(&case["content_type"]).to_owned(),
            payload: bytes(&case["payload"]),
            deadline_ns: case["deadline_ns"].as_u64().unwrap(),
            required_capabilities: string_map(&case["required_capabilities"]),
            task_id: "ignored-by-digest".into(),
            attempt: 7,
            ..Task::default()
        };
        assert_eq!(
            task_digest(&task).to_vec(),
            bytes(&case["digest"]),
            "{case}"
        );
    }
    for case in cases(&body, "challenge_payload") {
        let worker = SpiffeId::parse(s(&case["worker"])).unwrap();
        let payload = challenge_payload(
            &bytes(&case["nonce"]),
            &worker,
            &bytes(&case["register_request"]),
            &bytes(&case["channel_binding"]),
        );
        assert_eq!(payload, bytes(&case["payload"]), "{case}");
    }
}

#[test]
fn vouchers() {
    let body = load("vouchers");
    let key: [u8; 32] = bytes(&body["public_key"]).try_into().unwrap();
    let voucher = bytes(&body["voucher"]);
    assert_eq!(
        voucher_signing_payload(&voucher),
        bytes(&body["signing_payload"])
    );

    let seed: [u8; 32] = bytes(&body["seed"]).try_into().unwrap();
    let signing = ed25519_dalek::SigningKey::from_bytes(&seed);
    assert_eq!(signing.verifying_key().to_bytes(), key);

    for case in cases(&body, "cases") {
        let signed = SignedTaskVoucher {
            voucher: bytes(&case["voucher"]),
            signature: bytes(&case["signature"]),
            ..SignedTaskVoucher::default()
        };
        let opened = open_voucher(&signed, &key);
        assert_eq!(opened.is_ok(), b(&case["valid"]), "{case}");
        if let Ok(v) = opened {
            assert_eq!(v.task_id, "t1");
            assert_eq!(v.epoch, 3);
        }
    }
}

#[test]
fn backoff() {
    for case in cases(&load("backoff"), "cases") {
        let result = reconnect_delay(
            case["attempt"].as_i64().unwrap(),
            f(&case["initial"]),
            f(&case["factor"]),
            f(&case["maximum"]),
            f(&case["rng"]),
        );
        if case.get("invalid").is_some() {
            assert!(result.is_err(), "{case}");
        } else {
            assert_eq!(result.unwrap(), f(&case["delay"]), "{case}");
        }
    }
}
