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


def test_sortation_splits_into_multiple_output_batches(client, supplier_id, pic_id):
    """services/sortation.py #1: ONE->MANY -- one new output batch per grade
    quantity > 0 supplied, never a self-loop reuse of the input. Grade code
    mapping (Gourmet=01/EG=02/EP=03/NC=04/Powder=05, sortation.py #2/#3) and
    the derived shrinkage (initial - SUM(grades), sortation.py #6) must both
    come from the engine, not be recomputed by the API layer."""
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

    r = client.post(
        "/api/sortation",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "gourmet_qty": "40.000",
            "eg_qty": "30.000",
            "nc_qty": "20.000",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event"]["event_type"] == "SORTATION"
    assert body["event"]["shrinkage_qty"] == "10.000"  # 100 - (40+30+20)
    batches = {b["grade_code"]: b for b in body["batches"]}
    assert set(batches) == {"01", "02", "04"}
    assert batches["01"]["current_quantity"] == "40.000"
    assert batches["01"]["batch_type"] == "PROCESSED"
    assert batches["04"]["batch_type"] == "PROCESSED"  # NC -- still whole product, not Powder

    source = client.get(f"/api/batches/{batch_id}").json()
    assert source["current_quantity"] == "0.000"
    assert source["status"] == "CONSUMED"


def test_sortation_single_grade_is_reclassify_shape(client, supplier_id, pic_id):
    """A caller filling in exactly one grade field produces the single-output
    Downgrade/Upgrade shape -- same function, different call shape
    (sortation.py #1), no separate endpoint."""
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "10.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(
        "/api/sortation",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "eg_qty": "10.000",
            "process_code": "02",
        },
    )
    assert r.status_code == 201, r.text
    assert len(r.json()["batches"]) == 1
    assert r.json()["batches"][0]["process_code"] == "02"


def test_sortation_with_no_grade_quantity_is_422(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "10.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]
    r = client.post(
        "/api/sortation",
        json={"event_date": "2026-09-17", "pic_user_id": pic_id, "batch_id": batch_id},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


def test_mixing_combines_two_sources_into_one_batch(client, supplier_id, pic_id):
    """services/mixing.py #1/#2: cp_qty and shrinkage_qty are both derived,
    never accepted from the client -- MANY->ONE."""
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
    src_batch = r.json()["batch"]["batch_id"]
    sort = client.post(
        "/api/sortation",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": src_batch,
            "gourmet_qty": "40.000",
            "eg_qty": "30.000",
        },
    )
    batches = {b["grade_code"]: b["batch_id"] for b in sort.json()["batches"]}

    r = client.post(
        "/api/mixing",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "sources": [
                {"batch_id": batches["01"], "quantity": "40.000"},
                {"batch_id": batches["02"], "quantity": "30.000"},
            ],
            "final_qty": "68.000",
            "product_description": "GOURMET",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event"]["event_type"] == "MIXING"
    assert body["event"]["shrinkage_qty"] == "2.000"  # cp_qty(70) - final_qty(68)
    assert body["batch"]["current_quantity"] == "68.000"
    assert body["batch"]["supplier_code"] == "000"  # unattributable default, mixing.py #5
    assert body["batch"]["batch_type"] == "PROCESSED"

    for gc in ("01", "02"):
        consumed = client.get(f"/api/batches/{batches[gc]}").json()
        assert consumed["current_quantity"] == "0.000"
        assert consumed["status"] == "CONSUMED"


def test_mixing_requires_at_least_two_sources(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "10.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]
    r = client.post(
        "/api/mixing",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "sources": [{"batch_id": batch_id, "quantity": "10.000"}],
            "final_qty": "9.000",
            "product_description": "GOURMET",
        },
    )
    assert r.status_code == 422  # pydantic min_length=2 on `sources`


def test_mixing_rejects_duplicate_source_batch_id(client, supplier_id, pic_id):
    """services/mixing.py #9 -- enforced by the service, surfaced as a clean
    422 rather than a raw DB integrity error."""
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "10.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]
    r = client.post(
        "/api/mixing",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "sources": [
                {"batch_id": batch_id, "quantity": "5.000"},
                {"batch_id": batch_id, "quantity": "5.000"},
            ],
            "final_qty": "9.000",
            "product_description": "GOURMET",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


def test_grinding_creates_new_powder_batch(client, supplier_id, pic_id):
    """services/powder.py #5: Grinding always mints a new POWDER batch,
    grade_code '05' -- ONE->NEW-BATCH, shrinkage derived (starting - final,
    powder.py #4)."""
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
    src_batch = r.json()["batch"]["batch_id"]
    sort = client.post(
        "/api/sortation",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": src_batch,
            "nc_qty": "20.000",
        },
    )
    nc_batch = sort.json()["batches"][0]["batch_id"]
    assert sort.json()["batches"][0]["grade_code"] == "04"

    r = client.post(
        "/api/grinding",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": nc_batch,
            "final_qty": "15.000",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event"]["event_type"] == "GRINDING"
    assert body["event"]["shrinkage_qty"] == "5.000"  # 20 (on-hand default) - 15
    assert body["batch"]["batch_type"] == "POWDER"
    assert body["batch"]["grade_code"] == "05"
    assert body["batch"]["current_quantity"] == "15.000"


def test_magnetization_and_md_powder_are_stock_neutral_self_loops(client, supplier_id, pic_id):
    """services/powder.py #9: self-loop ONE->ONE, no QualityTest row -- the
    finding/notes are recorded in ProcessEvent.notes instead."""
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "50.000",
        },
    )
    src_batch = r.json()["batch"]["batch_id"]
    sort = client.post(
        "/api/sortation",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": src_batch,
            "nc_qty": "50.000",
        },
    )
    nc_batch = sort.json()["batches"][0]["batch_id"]
    grind = client.post(
        "/api/grinding",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": nc_batch,
            "final_qty": "10.000",
        },
    )
    powder_batch = grind.json()["batch"]["batch_id"]

    mg = client.post(
        "/api/magnetization",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": powder_batch,
            "finding": "Tidak Ada",
        },
    )
    assert mg.status_code == 201, mg.text
    assert mg.json()["quality_test"] is None
    assert mg.json()["shrinkage_qty"] == "0"

    mdpw = client.post(
        "/api/md-powder",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": powder_batch,
            "finding": "Tidak Ada",
        },
    )
    assert mdpw.status_code == 201, mdpw.text

    final = client.get(f"/api/batches/{powder_batch}").json()
    assert final["current_quantity"] == "10.000"  # unchanged by either inspection
    event_types = [e["event_type"] for e in final["events"]]
    assert event_types == ["GRINDING", "MAGNETIZATION", "MD_POWDER"]


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
