"""Fase 15 (slice 1) -- end-to-end tests for the web API layer.

These exercise the HTTP layer (`traceability_engine.webapp`), not the
services directly (those already have their own unit tests in
test_receiving.py/test_qc_md.py/test_steam_dry.py) -- the point here is to
confirm the API wiring (request parsing, dependency-injected session,
commit/rollback, error -> HTTP status mapping, response shaping) works
end-to-end through a real HTTP client, covering the same
Receiving -> QC -> MD -> Steaming -> Sundrying chain a PT JAS staff member
would actually drive from the browser.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from traceability_engine.models import Base
from traceability_engine.webapp.database import get_db
from traceability_engine.webapp.main import app


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def supplier_id(client):
    r = client.post("/api/suppliers", json={"supplier_code": "024", "name": "WARDOYO"})
    assert r.status_code == 201
    return r.json()["supplier_id"]


@pytest.fixture()
def pic_id(client):
    r = client.post("/api/users", json={"name": "Wakhidah", "role": "STAFF"})
    assert r.status_code == 201
    return r.json()["user_id"]


def test_master_data_roundtrip(client, supplier_id, pic_id):
    suppliers = client.get("/api/suppliers").json()
    users = client.get("/api/users").json()
    assert any(s["supplier_id"] == supplier_id for s in suppliers)
    assert any(u["user_id"] == pic_id for u in users)


def test_receiving_creates_batch_for_full_net_quantity(client, supplier_id, pic_id):
    """services/receiving.py docstring #4: the WHOLE net quantity becomes
    one Batch -- on_spec/off_spec are recorded, not split into batches. The
    API must not silently "fix" this by using on_spec as the batch qty."""
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "55.500",
            "batch_number": "030224-260221-00",
            "on_spec_qty": "50.000",
            "off_spec_qty": "5.500",
            "smell_test": "Normal",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["batch"]["current_quantity"] == "55.500"
    assert body["batch"]["jenis_code"] == "03"  # parsed from batch_number
    assert body["batch"]["grade_code"] == "02"
    assert body["event"]["event_type"] == "RECEIVING"


def test_receiving_without_batch_number_still_works(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_HIJAU",
            "net_quantity": "20.000",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["batch"]["batch_number"] is None


def test_receiving_non_positive_net_quantity_is_422(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "0",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


def test_full_chain_receiving_qc_md_steam_dry(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "100.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    qc = client.post(
        "/api/qc-tests",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "stage": "RM",
            "ka_1": "32.10",
            "ka_2": "31.90",
            "aw": "0.850",
            "finding": "PASS",
        },
    )
    assert qc.status_code == 201, qc.text
    assert qc.json()["quality_test"]["ka_1"] == "32.100"

    md = client.post(
        "/api/metal-detections",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "stage": "RM",
            "finding": "Tidak Ada",
        },
    )
    assert md.status_code == 201, md.text

    steam = client.post(
        "/api/steaming",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "steam_temperature": "96.5",
            "pan_count": 4,
        },
    )
    assert steam.status_code == 201, steam.text

    dry = client.post(
        "/api/sundrying",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "final_quantity": "80.000",
            "drying_duration": "60 menit x 3",
        },
    )
    assert dry.status_code == 201, dry.text
    assert dry.json()["shrinkage_qty"] == "20.000"

    detail = client.get(f"/api/batches/{batch_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["current_quantity"] == "80.000"
    event_types = [e["event_type"] for e in body["events"]]
    assert event_types == ["RECEIVING", "QC_TEST", "METAL_DETECTION", "STEAMING", "SUNDRYING"]

    history = client.get("/api/process-events", params={"batch_id": batch_id, "event_type": "STEAMING"})
    assert history.status_code == 200
    assert len(history.json()) == 1


def test_qc_test_on_missing_batch_is_422_not_500(client, pic_id):
    r = client.post(
        "/api/qc-tests",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": 999999,
            "stage": "RM",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


def test_get_unknown_batch_is_404(client):
    r = client.get("/api/batches/999999")
    assert r.status_code == 404


def test_batch_number_parse_endpoint(client):
    ok = client.get("/api/batch-number/parse", params={"value": "030224-260221-00"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["jenis_code"] == "03"
    assert body["process_code_label"] == "Original"

    bad = client.get("/api/batch-number/parse", params={"value": "not-a-batch-number"})
    assert bad.status_code == 200
    assert bad.json()["ok"] is False


def test_list_batches_filters_by_status_and_type(client, supplier_id, pic_id):
    client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_HIJAU",
            "net_quantity": "10.000",
        },
    )
    active_kering = client.get("/api/batches", params={"status": "ACTIVE", "batch_type": "RAW_KERING"}).json()
    active_hijau = client.get("/api/batches", params={"status": "ACTIVE", "batch_type": "RAW_HIJAU"}).json()
    assert all(b["batch_type"] == "RAW_KERING" for b in active_kering)
    assert all(b["batch_type"] == "RAW_HIJAU" for b in active_hijau)
    assert len(active_hijau) == 1
