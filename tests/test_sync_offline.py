"""Offline robustness: no record is ever lost or duplicated, whatever the network does."""

from __future__ import annotations

import numpy as np
from conftest import FakeExtractor, jpeg_bytes, wait_processed
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from dayone.device.lifecycle import RecordState
from dayone.device.store import DeviceStore
from dayone.device.sync import Network, ServerClient, SyncEngine

S = RecordState
IMG = jpeg_bytes(np.full((60, 40, 3), 200, np.uint8))


def make_device(path, server, network):
    store = DeviceStore(path, "1234")
    events = []
    engine = SyncEngine(store, ServerClient(server, "token-sf-amina", network), notify=lambda k, d: events.append(k))
    return store, engine, events


def capture(store, n_pages=2):
    rid = store.create_record("sf-amina", {"fields": {}})
    pids = [store.add_page(rid, IMG, "pregnancy", {"ok": True}, {}) for _ in range(n_pages)]
    store.transition(rid, S.PENDING_AI, "test")
    for p in pids:
        store.enqueue("process_page", p, f"process:{p}")
    return rid, pids


def drain(engine, server, page_ids, rounds=30):
    for _ in range(rounds):
        engine.run_once()
        if page_ids:
            try:
                wait_processed(server, page_ids, timeout=2.0)
            except TimeoutError:
                pass
        if not engine.store.pending_jobs():
            return
    raise AssertionError(f"jobs still pending: {engine.store.pending_jobs()}")


def register(store, rid):
    for target in (S.NEEDS_REVIEW, S.VALIDATED, S.PATIENT_MATCHED):
        store.transition(rid, target, "test")
    store.update_record(rid, patient_id="p" * 32, bump_version=True)
    v = store.get_record(rid).version
    store.transition(rid, S.REGISTERED, "test", enqueue=("sync_record", rid, f"sync:{rid}:{v}"))


def test_offline_capture_waits_then_processes(tmp_path, server):
    net = Network(online=False)
    store, engine, events = make_device(tmp_path / "d.db", server, net)
    rid, pids = capture(store)
    engine.run_once()
    assert store.get_record(rid).state == S.PENDING_AI
    assert server.app.state.registry.execute("SELECT COUNT(*) FROM pages")[0][0] == 0
    net.online = True
    drain(engine, server, pids)
    assert store.get_record(rid).state == S.AI_PROCESSED
    assert "processed" in events
    assert store.get_record(rid).payload["fields"]["pregnancy.visit.t1v2.bp"]["status"] == "NEEDS_REVIEW"


def test_lost_response_does_not_duplicate(tmp_path, server):
    net = Network(online=True)
    store, engine, _ = make_device(tmp_path / "d.db", server, net)
    rid, pids = capture(store, 1)
    net.faults.extend(["drop_response"])  # server stores the page, the phone never hears back
    drain(engine, server, pids)
    assert not net.faults and any("response lost" in line for line in net.log)
    assert server.app.state.registry.execute("SELECT COUNT(*) FROM pages")[0][0] == 1
    assert store.get_record(rid).state == S.AI_PROCESSED
    register(store, rid)
    net.faults.extend(["drop_response"])
    drain(engine, server, [])
    assert not net.faults
    assert store.get_record(rid).state == S.SYNCED
    assert server.app.state.registry.execute("SELECT COUNT(*) FROM records")[0][0] == 1


def test_crash_between_upload_and_result_is_recovered(tmp_path, server):
    net = Network(online=True)
    store, engine, _ = make_device(tmp_path / "d.db", server, net)
    rid, pids = capture(store, 1)
    engine.client.upload_page(pids[0], rid, 0.0, "pregnancy", IMG)  # upload happened, then the app died
    store.close()
    store, engine, _ = make_device(tmp_path / "d.db", server, net)
    engine.recover()
    drain(engine, server, pids)
    assert store.get_record(rid).state == S.AI_PROCESSED
    assert server.app.state.registry.execute("SELECT COUNT(*) FROM pages")[0][0] == 1


def test_server_errors_on_sync_go_through_sync_failed(tmp_path, server):
    net = Network(online=True)
    store, engine, events = make_device(tmp_path / "d.db", server, net)
    rid, pids = capture(store, 1)
    drain(engine, server, pids)
    register(store, rid)
    net.faults.append("server_error")
    engine.run_once()
    assert store.get_record(rid).state == S.SYNC_FAILED
    store.db.execute("UPDATE outbox SET next_attempt_at=0")  # skip the back-off wait
    drain(engine, server, [])
    assert store.get_record(rid).state == S.SYNCED
    assert [h["to"] for h in store.history(rid)][-2:] == ["SYNC_FAILED", "SYNCED"]


def test_ai_unavailable_ends_in_manual_review(tmp_path):
    from fastapi.testclient import TestClient

    from dayone.server.app import create_app

    extractor = FakeExtractor(fail_first=10)
    server = TestClient(create_app(tmp_path / "srv", extractor_factory=lambda: extractor))
    net = Network(online=True)
    store, engine, events = make_device(tmp_path / "d.db", server, net)
    rid, pids = capture(store, 1)
    for _ in range(30):
        engine.run_once()
        store.db.execute("UPDATE outbox SET next_attempt_at=0")
        try:
            wait_processed(server, pids, timeout=1.0)
        except TimeoutError:
            pass
        if store.get_record(rid).state == S.MANUAL_REVIEW_REQUIRED:
            break
    assert store.get_record(rid).state == S.MANUAL_REVIEW_REQUIRED
    assert "processing_failed" in events
    # the model server is back: the midwife taps "retry" and the page is read
    assert engine.retry_ai(rid, "sf-amina") == 1
    for _ in range(10):
        engine.run_once()
        store.db.execute("UPDATE outbox SET next_attempt_at=0")
        try:
            wait_processed(server, pids, timeout=1.0)
        except TimeoutError:
            pass
        if store.get_record(rid).state == S.AI_PROCESSED:
            break
    assert store.get_record(rid).state == S.AI_PROCESSED


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(script=st.lists(st.sampled_from(["off", "on", "drop_request", "drop_response", "server_error", "crash",
                                        "step", "step", "step"]), min_size=5, max_size=30))
def test_random_network_chaos_never_loses_or_duplicates(tmp_path_factory, script):
    from fastapi.testclient import TestClient

    from dayone.server.app import create_app

    base = tmp_path_factory.mktemp("chaos")
    server = TestClient(create_app(base / "srv", extractor_factory=FakeExtractor))
    net = Network(online=False)
    store, engine, _ = make_device(base / "d.db", server, net)
    records = [capture(store, 2) for _ in range(2)]
    for action in script:
        if action == "off":
            net.online = False
        elif action == "on":
            net.online = True
        elif action in ("drop_request", "drop_response", "server_error"):
            net.faults.append(action)
        elif action == "crash":
            store.close()
            store, engine, _ = make_device(base / "d.db", server, net)
            engine.recover()
        else:
            engine.run_once()
        for rid, _ in records:
            if store.get_record(rid).state == S.AI_PROCESSED:
                register(store, rid)
    # connectivity finally comes back for good
    net.online, net.faults = True, type(net.faults)()
    store.db.execute("UPDATE outbox SET next_attempt_at=0")
    all_pages = [p for _, pids in records for p in pids]
    for _ in range(40):
        engine.run_once()
        store.db.execute("UPDATE outbox SET next_attempt_at=0")
        for rid, _ in records:
            if store.get_record(rid).state == S.AI_PROCESSED:
                register(store, rid)
        if all(store.get_record(r).state == S.SYNCED for r, _ in records):
            break
        try:
            wait_processed(server, all_pages, timeout=0.5)
        except TimeoutError:
            pass
    reg = server.app.state.registry
    states = [store.get_record(r).state for r, _ in records]
    # Every record is either synchronised or parked in an explicit failure state (repeated server
    # errors during AI processing hand it over to manual entry) — never silently dropped.
    assert all(s in (S.SYNCED, S.MANUAL_REVIEW_REQUIRED) for s in states), states
    page_ids = [r[0] for r in reg.execute("SELECT id FROM pages")]
    assert len(page_ids) == len(set(page_ids)) and set(page_ids) <= set(all_pages)  # no duplicate upload
    synced = [r for r, _ in records if store.get_record(r).state == S.SYNCED]
    assert sorted(r[0] for r in reg.execute("SELECT id FROM records")) == sorted(synced)  # each record once


def test_retry_after_upload_server_errors(tmp_path, server):
    net = Network(online=True)
    store, engine, events = make_device(tmp_path / "d.db", server, net)
    rid, pids = capture(store, 1)
    for _ in range(6):  # every upload attempt hits a server error
        net.faults.append("server_error")
        engine.run_once()
        store.db.execute("UPDATE outbox SET next_attempt_at=0")
    assert store.get_record(rid).state == S.MANUAL_REVIEW_REQUIRED
    net.faults.clear()
    assert engine.retry_ai(rid, "sf-amina") == 1
    drain(engine, server, pids)
    assert store.get_record(rid).state == S.AI_PROCESSED
    assert server.app.state.registry.execute("SELECT COUNT(*) FROM pages")[0][0] == 1


def test_sibling_page_is_accepted_as_the_expected_page(registrar):
    from conftest import page_png
    from dayone.schema import PageType

    reg = registrar.register(page_png(7), expected=PageType.PP_EARLY_MOTHER)  # late page, same layout and zones
    assert reg.ok and reg.page_type == PageType.PP_EARLY_MOTHER
    reg = registrar.register(page_png(3), expected=PageType.PP_EARLY_MOTHER)  # a different layout is refused
    assert not reg.ok and reg.reason.startswith("unexpected_page")
