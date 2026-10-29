"""Wire round trips for the registration, submission, administration and service schemas."""

from __future__ import annotations

import pytest
from google.protobuf.message import Message

from a2a_control_plane._proto import (
    admin_pb2,
    registration_pb2,
    services_pb2,
    submission_pb2,
    zone_report_pb2,
)
from a2a_control_plane.tasks import make_task

MESSAGES: list[Message] = [
    registration_pb2.RegisterRequest(
        supported_versions=[1, 2], features=["recovery.replay"], capabilities={"model": "a"}
    ),
    registration_pb2.BeginRegistrationRequest(
        worker_spiffe_id="spiffe://example.org/zone/z1/agent/w1",
        register_request=b"\x0a\x01\x01",
        svid_chain=[b"leaf", b"intermediate"],
        channel_binding=b"\x07" * 32,
        instance_id="i-1",
    ),
    registration_pb2.RegistrationChallenge(
        nonce=b"\x01" * 16, timeout_ms=10_000, selected_version=1, selected_features=["a", "b"]
    ),
    registration_pb2.CompleteRegistrationRequest(
        worker_spiffe_id="spiffe://example.org/zone/z1/agent/w1",
        signature=b"\x02" * 64,
        instance_id="i-1",
    ),
    registration_pb2.RegistrationAdmission(
        session_id="s-1",
        selected_version=1,
        selected_features=["recovery.snapshot"],
        admitted_capabilities={"model": "a"},
    ),
    registration_pb2.AttachRequest(
        instance_id="i-1", supported_versions=[1], features=["x"], endpoint="agg.z1:50051"
    ),
    registration_pb2.AttachResponse(epoch=3, selected_version=1, selected_features=["y"]),
    registration_pb2.DetachRequest(instance_id="i-1"),
    submission_pb2.IssueTaskVoucherRequest(
        zone_id="z1", required_capabilities={"model": "a"}, task_digest=b"\x03" * 32
    ),
    submission_pb2.IssueTaskVoucherResponse(
        voucher=submission_pb2.SignedTaskVoucher(
            voucher=b"v", signature=b"s" * 64, signer_chain=[b"leaf"]
        ),
        task_id="t1",
        endpoint="agg.z1:50051",
    ),
    submission_pb2.TaskVoucher(
        voucher_id=b"\x04" * 16,
        task_id="t1",
        client_spiffe_id="spiffe://example.org/client/c1",
        zone_id="z1",
        instance_id="i-1",
        epoch=3,
        task_digest=b"\x03" * 32,
        issued_at_ns=1,
        expires_at_ns=2,
    ),
    submission_pb2.SubmitTaskRequest(
        voucher=submission_pb2.SignedTaskVoucher(voucher=b"v", signature=b"s"),
        task=make_task("t1", b"p", required_capabilities={"model": "a"}),
    ),
    submission_pb2.CancelTaskRequest(task_id="t1", reason="no longer needed"),
    submission_pb2.CancelTaskResponse(accepted=True),
    submission_pb2.ZoneSnapshotRequest(zone_id="z1"),
    admin_pb2.RevokeRequest(spiffe_id="spiffe://example.org/zone/z1/agent/w1", reason="r"),
    admin_pb2.RevokeResponse(found=True),
    admin_pb2.ListWorkersRequest(zone_id="z1"),
    admin_pb2.ListWorkersResponse(
        workers=[
            admin_pb2.WorkerInfo(
                spiffe_id="spiffe://example.org/zone/z1/agent/w1",
                zone_id="z1",
                agent_id="w1",
                state=zone_report_pb2.WORKER_STATE_IDLE,
                capabilities={"model": "a"},
                instance_id="i-1",
                session_id="s-1",
            )
        ]
    ),
]


@pytest.mark.parametrize("message", MESSAGES, ids=lambda m: type(m).__name__)
def test_message_survives_a_wire_round_trip(message: Message) -> None:
    decoded = type(message).FromString(message.SerializeToString())
    assert decoded == message


def test_unknown_fields_are_preserved_when_forwarding() -> None:
    # Spec 03, section 3: an intermediary re-serializes what it parsed without losing new fields.
    extra = b"\xa0\x06\x07"  # field 100, varint 7: a field this version does not define
    wire = registration_pb2.RegisterRequest(supported_versions=[1]).SerializeToString() + extra
    forwarded = registration_pb2.RegisterRequest.FromString(wire).SerializeToString()
    assert forwarded.endswith(extra)


def test_services_expose_the_methods_the_spec_names() -> None:
    methods = {
        service: {m.name for m in services_pb2.DESCRIPTOR.services_by_name[service].methods}
        for service in ("Registration", "Admin", "TaskSubmission", "TaskIntake", "ZoneControl")
    }
    assert methods == {
        "Registration": {"Attach", "Detach", "BeginRegistration", "CompleteRegistration"},
        "Admin": {"RevokeIdentity", "ListWorkers"},
        "TaskSubmission": {"IssueTaskVoucher"},
        "TaskIntake": {"SubmitTask", "CancelTask"},
        "ZoneControl": {"RequestZoneSnapshot", "RevokeWorker"},
    }


def test_no_service_on_the_ccr_accepts_a_task_payload() -> None:
    # The CCR is a referee: nothing it serves may take a Task, so no payload can reach it.
    for service in ("Registration", "Admin", "TaskSubmission"):
        for method in services_pb2.DESCRIPTOR.services_by_name[service].methods:
            assert "Task" != method.input_type.name
            assert "payload" not in {f.name for f in method.input_type.fields}
            assert all(
                f.message_type is None or f.message_type.name != "Task"
                for f in method.input_type.fields
            )
