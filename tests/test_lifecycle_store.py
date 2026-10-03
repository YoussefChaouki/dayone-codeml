import pytest

from dayone.device.crypto import WrongPin
from dayone.device.lifecycle import InvalidTransition, RecordState
from dayone.device.store import DeviceStore

S = RecordState


def test_nominal_path_and_history(tmp_path):
    store = DeviceStore(tmp_path / "d.db", "1234")
    rid = store.create_record("sf-a", {"fields": {}})
    for target in (S.PENDING_AI, S.AI_PROCESSED, S.NEEDS_REVIEW, S.VALIDATED, S.PATIENT_MATCHED, S.REGISTERED,
                   S.SYNCED):
        store.transition(rid, target, "test")
    hist = [h["to"] for h in store.history(rid)]
    assert hist == ["CAPTURED", "PENDING_AI", "AI_PROCESSED", "NEEDS_REVIEW", "VALIDATED", "PATIENT_MATCHED",
                    "REGISTERED", "SYNCED"]


@pytest.mark.parametrize("path", [[S.VALIDATED], [S.PENDING_AI, S.SYNCED], [S.PENDING_AI, S.REGISTERED]])
def test_invalid_transitions_are_rejected(tmp_path, path):
    store = DeviceStore(tmp_path / "d.db", "1234")
    rid = store.create_record("sf-a")
    with pytest.raises(InvalidTransition):
        for target in path:
            store.transition(rid, target, "test")


def test_payloads_and_images_are_encrypted_on_disk(tmp_path):
    store = DeviceStore(tmp_path / "d.db", "1234")
    secret_value = "VALEUR-TRES-SECRETE-42"
    rid = store.create_record("sf-a", {"fields": {"x": {"value": secret_value}}})
    store.add_page(rid, b"JPEG" + secret_value.encode(), "pregnancy", {}, {})
    store.put("agent", {"note": secret_value})
    store.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir())
    assert secret_value.encode() not in raw
    again = DeviceStore(tmp_path / "d.db", "1234")
    assert again.get_record(rid).payload["fields"]["x"]["value"] == secret_value


def test_wrong_pin(tmp_path):
    DeviceStore(tmp_path / "d.db", "1234").close()
    with pytest.raises(WrongPin):
        DeviceStore(tmp_path / "d.db", "0000")


def test_transition_and_job_are_atomic_and_idempotent(tmp_path):
    store = DeviceStore(tmp_path / "d.db", "1234")
    rid = store.create_record("sf-a")
    store.transition(rid, S.PENDING_AI, "t", enqueue=("sync_record", rid, "k1"))
    store.enqueue("sync_record", rid, "k1")  # same idempotency key: ignored
    assert len(store.pending_jobs()) == 1
