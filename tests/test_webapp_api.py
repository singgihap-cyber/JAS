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


@pytest.fixture()
def pm_id(client):
    r = client.post("/api/users", json={"name": "Robiah", "role": "PRODUCTION_MANAGER"})
    assert r.status_code == 201
    return r.json()["user_id"]


def test_master_data_roundtrip(client, supplier_id, pic_id):
    suppliers = client.get("/api/suppliers").json()
    users = client.get("/api/users").json()
    assert any(s["supplier_id"] == supplier_id for s in suppliers)
    assert any(u["user_id"] == pic_id for u in users)


def test_receiving_creates_batch_and_auto_returns_off_spec(client, supplier_id, pic_id):
    """services/receiving.py docstring #4 (Fase 29): the WHOLE net quantity
    is received into one Batch, then off_spec_qty is automatically returned
    to the supplier (SUPPLIER_RETURN) -- stock left = net - off_spec."""
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
    assert body["batch"]["current_quantity"] == "50.000"  # 55.500 - off-spec 5.500 dikembalikan
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


# ------------------------------------------------------------------- rework
# Fase 15 slice 3


def test_rework_splits_into_multiple_output_batches(client, supplier_id, pic_id):
    """services/rework.py: ONE->MANY like Sortation (Gourmet/EG/EP/NC, no
    Powder slot), but every output is unconditionally tagged
    process_code='04' (#5) -- not a caller parameter, unlike Sortation's
    exposed process_code."""
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
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(
        "/api/rework",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "gourmet_qty": "20.000",
            "eg_qty": "15.000",
            "nc_qty": "10.000",
            "process_description": "Olah ulang sisa sortasi",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event"]["event_type"] == "REWORK"
    assert body["event"]["shrinkage_qty"] == "5.000"  # 50 - (20+15+10)
    batches = {b["grade_code"]: b for b in body["batches"]}
    assert set(batches) == {"01", "02", "04"}
    for b in batches.values():
        assert b["process_code"] == "04"  # hardcoded, rework.py #5
        assert b["batch_type"] == "PROCESSED"

    source = client.get(f"/api/batches/{batch_id}").json()
    assert source["current_quantity"] == "0.000"
    assert source["status"] == "CONSUMED"


def test_rework_with_no_grade_quantity_is_422(client, supplier_id, pic_id):
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
        "/api/rework",
        json={"event_date": "2026-09-17", "pic_user_id": pic_id, "batch_id": batch_id},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


# ------------------------------------------------------------- vacuum/packing
# Fase 15 slice 3


def test_vacuum_is_self_loop_and_sums_plastic_lines(client, supplier_id, pic_id):
    """services/vacuum_packing.py #1/#2: no new batch minted, event quantity
    is SUM(plastic_lines.total_weight), stock-neutral (INPUT==OUTPUT==same
    batch, same quantity)."""
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "20.030",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(
        "/api/vacuum",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "plastic_lines": [
                {"total_weight": "20.000", "plastic_size": "25x37.5"},
                {"total_weight": "0.030", "plastic_size": "15x25"},
            ],
            "product_description": "GOURMET",
            "buyer": "MCC",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event_type"] == "VACUUM"
    assert body["shrinkage_qty"] == "0"
    assert {link["role"] for link in body["links"]} == {"INPUT", "OUTPUT"}
    for link in body["links"]:
        assert link["batch_id"] == batch_id
        assert link["quantity"] == "20.030"

    unchanged = client.get(f"/api/batches/{batch_id}").json()
    assert unchanged["current_quantity"] == "20.030"  # stock-neutral self-loop
    assert unchanged["status"] == "ACTIVE"


def test_vacuum_requires_at_least_one_plastic_line_is_422(client, supplier_id, pic_id):
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
        "/api/vacuum",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "plastic_lines": [],
        },
    )
    assert r.status_code == 422  # pydantic min_length=1 on plastic_lines


def test_packing_single_source_derives_net_and_tare_weight(client, supplier_id, pic_id):
    """services/vacuum_packing.py #6/#7: net_weight=SUM(sources),
    tare_weight=gross_weight-net_weight, both derived server-side. Single
    source -> grade/jenis/supplier/receiving_date inherited (#12)."""
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "1000.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(
        "/api/packing",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "sources": [{"batch_id": batch_id, "quantity": "1000.000"}],
            "gross_weight": "1057.200",
            "plastic_size": "25x37.5",
            "carton_lot": "K-001",
            "carton_qty": "10",
            "shipping_number": "SHP-001",
            "destination": "Vietnam",
            "product_description": "GOURMET",
            "buyer": "MCC",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event"]["event_type"] == "PACKING"
    assert body["event"]["shrinkage_qty"] == "0"  # Packing never loses weight, #6
    packed = body["batch"]
    assert packed["batch_type"] == "PACKAGED"
    assert packed["current_quantity"] == "1000.000"
    assert packed["net_weight"] == "1000.000"
    assert packed["gross_weight"] == "1057.200"
    assert packed["tare_weight"] == "57.200"
    assert packed["carton_lot"] == "K-001"
    assert packed["supplier_id"] == supplier_id  # inherited from single source, #12
    assert packed["receiving_date"] == "2026-09-17"  # inherited (== source's event_date, receiving.py)

    source = client.get(f"/api/batches/{batch_id}").json()
    assert source["current_quantity"] == "0.000"
    assert source["status"] == "CONSUMED"


def test_packing_multiple_sources_combine_without_auto_inheritance(client, supplier_id, pic_id):
    """services/vacuum_packing.py #12: with >1 source, grade/jenis/supplier
    are left None unless the caller states them explicitly -- no guessed
    "winner" between sources."""
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
        "/api/packing",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "sources": [
                {"batch_id": batches["01"], "quantity": "40.000"},
                {"batch_id": batches["02"], "quantity": "30.000"},
            ],
            "gross_weight": "72.000",
        },
    )
    assert r.status_code == 201, r.text
    packed = r.json()["batch"]
    assert packed["net_weight"] == "70.000"
    assert packed["tare_weight"] == "2.000"
    assert packed["grade_code"] is None
    assert packed["supplier_id"] is None


def test_packing_requires_at_least_one_source_is_422(client, supplier_id, pic_id):
    r = client.post(
        "/api/packing",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "sources": [],
            "gross_weight": "10.000",
        },
    )
    assert r.status_code == 422  # pydantic min_length=1 on sources


def test_packing_rejects_duplicate_source_batch_id(client, supplier_id, pic_id):
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
        "/api/packing",
        json={
            "event_date": "2026-09-17",
            "pic_user_id": pic_id,
            "sources": [
                {"batch_id": batch_id, "quantity": "5.000"},
                {"batch_id": batch_id, "quantity": "5.000"},
            ],
            "gross_weight": "10.000",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


# ------------------------------------------------------------- stock (Fase 15 slice 4)
# Reconciliation-mismatch detection and the full grouping/exclusion rules
# already have dedicated service-level coverage in test_stock.py -- these
# only confirm the HTTP wiring (routing, session injection, response
# shaping, 404s) the same way the rest of this file does for every other
# router.
def test_stock_summary_and_total_after_receiving(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "250.000",
        },
    )
    assert r.status_code == 201, r.text

    summary = client.get("/api/stock/summary").json()
    assert len(summary) == 1
    row = summary[0]
    assert row["supplier_id"] == supplier_id
    assert row["supplier_code"] == "024"
    assert row["supplier_name"] == "WARDOYO"
    assert row["batch_count"] == 1
    assert row["total_quantity"] == "250.000"

    total = client.get("/api/stock/total").json()
    assert total["total_quantity"] == "250.000"


def test_stock_summary_excludes_consumed_batches(client, supplier_id, pic_id):
    """Only BatchStatus.ACTIVE counts as on-hand stock (stock.py module
    docstring) -- once a batch is fully consumed (Packing here), it must
    drop out of /stock/summary and /stock/total."""
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "100.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]
    r = client.post(
        "/api/packing",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "sources": [{"batch_id": batch_id, "quantity": "100.000"}],
            "gross_weight": "105.000",
        },
    )
    assert r.status_code == 201, r.text
    packed_id = r.json()["batch"]["batch_id"]

    summary = client.get("/api/stock/summary").json()
    assert len(summary) == 1  # only the new PACKAGED batch, not the CONSUMED source
    assert summary[0]["batch_count"] == 1
    total = client.get("/api/stock/total").json()
    assert total["total_quantity"] == "100.000"  # packed batch's net weight only

    consumed = client.get(f"/api/batches/{batch_id}/stock").json()
    assert consumed["status"] == "CONSUMED"
    assert consumed["cached_quantity"] == "0.000"

    active = client.get(f"/api/batches/{packed_id}/stock").json()
    assert active["status"] == "ACTIVE"
    assert active["matches"] is True


def test_stock_reconcile_is_empty_in_normal_operation(client, supplier_id, pic_id):
    client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "10.000",
        },
    )
    assert client.get("/api/stock/reconcile").json() == []


def test_batch_transactions_ledger_history_is_chronological(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "50.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]
    client.post(
        "/api/qc-tests",
        json={"event_date": "2026-09-18", "pic_user_id": pic_id, "batch_id": batch_id, "stage": "RM"},
    )

    txns = client.get(f"/api/batches/{batch_id}/transactions").json()
    assert len(txns) >= 1
    assert txns[0]["direction"] == "IN"
    assert txns[0]["quantity"] == "50.000"
    assert txns[0]["balance_after"] == "50.000"


def test_batch_stock_and_transactions_404_for_unknown_batch(client):
    assert client.get("/api/batches/99999/stock").status_code == 404
    assert client.get("/api/batches/99999/transactions").status_code == 404


# ------------------------------------------------------- traceability (Fase 15 slice 4)
# The traversal itself (backward/forward, supplier/shipment resolution,
# incomplete-leaf classification) is already covered end-to-end in
# test_traceability.py -- these confirm GET /batches/{id}/trace shapes that
# same engine output correctly over HTTP.
def test_batch_trace_resolves_supplier_and_upstream_event(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "100.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    trace = client.get(f"/api/batches/{batch_id}/trace")
    assert trace.status_code == 200, trace.text
    body = trace.json()
    assert body["batch"]["batch_id"] == batch_id
    assert len(body["suppliers"]) == 1
    assert body["suppliers"][0]["supplier_code"] == "024"
    assert len(body["upstream_events"]) == 1
    assert body["upstream_events"][0]["event_type"] == "RECEIVING"
    assert body["downstream_events"] == []
    assert body["shipments"] == []
    # ACTIVE, never consumed further -- a genuine still-in-process leaf
    # (GENEALOGY.md §3.2, services/traceability.py incomplete_leaves()).
    assert len(body["incomplete_leaves"]) == 1
    assert body["incomplete_leaves"][0]["batch_id"] == batch_id


def test_batch_trace_covers_split_genealogy_downstream(client, supplier_id, pic_id):
    """Receiving -> Sortation (split into 2 grades): both output batches
    must show up as forward-trace leaves (incomplete_leaves, since neither
    is SHIPPED/REJECTED yet), and the Sortation event must appear in
    downstream_events for the original batch's trace."""
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "100.000",
        },
    )
    src_batch = r.json()["batch"]["batch_id"]
    r = client.post(
        "/api/sortation",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "batch_id": src_batch,
            "gourmet_qty": "60.000",
            "eg_qty": "40.000",
        },
    )
    output_ids = {b["batch_id"] for b in r.json()["batches"]}

    trace = client.get(f"/api/batches/{src_batch}/trace").json()
    downstream_types = {ev["event_type"] for ev in trace["downstream_events"]}
    assert downstream_types == {"SORTATION"}
    leaf_ids = {b["batch_id"] for b in trace["incomplete_leaves"]}
    assert leaf_ids == output_ids


def test_batch_trace_404_for_unknown_batch(client):
    r = client.get("/api/batches/99999/trace")
    assert r.status_code == 404


# --------------------------------------------------------- delivery (Fase 15 slice 5)
# The event/shipment mechanics themselves (net/tare derivation, SHIPPED
# promotion, is_sample tagging, multi-source, customer_id passthrough) are
# already covered end-to-end at the service layer in test_delivery.py --
# these confirm GET/POST /delivery, /sample-delivery, /customers wiring
# through HTTP (request parsing, response shaping incl. the Shipment
# satellite, 404s) works, same split of responsibility as every other
# stage's webapp-vs-service test pair in this suite.
def test_customers_roundtrip(client):
    r = client.post("/api/customers", json={"name": "LIBERTA GELATO"})
    assert r.status_code == 201, r.text
    customer_id = r.json()["customer_id"]
    customers = client.get("/api/customers").json()
    assert any(c["customer_id"] == customer_id and c["name"] == "LIBERTA GELATO" for c in customers)


# --------------------------------------------------------- customer matching (Fase 21)
# The matching algorithm itself (normalization, exact-vs-fuzzy scoring,
# threshold, limit) is already covered at the service layer in
# test_customer_matching.py -- these confirm the thin GET /customers/match
# wiring (query params, response shaping) works end-to-end through HTTP,
# same split of responsibility as every other stage's webapp-vs-service
# test pair in this suite.
def test_customers_match_exact(client):
    customer_id = client.post("/api/customers", json={"name": "LIBERTA GELATO"}).json()["customer_id"]
    r = client.get("/api/customers/match", params={"q": "liberta gelato"})
    assert r.status_code == 200, r.text
    results = r.json()
    assert len(results) == 1
    assert results[0] == {
        "customer_id": customer_id,
        "name": "LIBERTA GELATO",
        "score": 1.0,
        "exact": True,
        "matched_alias": None,
    }


def test_customers_match_no_suggestion_is_an_empty_list_not_an_error(client):
    client.post("/api/customers", json={"name": "LIBERTA GELATO"})
    r = client.get("/api/customers/match", params={"q": "SOMETHING ENTIRELY UNRELATED"})
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_customers_match_requires_nonempty_q(client):
    r = client.get("/api/customers/match", params={"q": ""})
    assert r.status_code == 422


def test_customers_match_respects_limit_param(client):
    for i in range(5):
        client.post("/api/customers", json={"name": f"MCC {i}"})
    r = client.get("/api/customers/match", params={"q": "MCC", "limit": 2})
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2


# --------------------------------------------------------- customer aliases (Fase 21 lanjutan)
# The alias conflict-checking/idempotency itself is already covered at the
# service layer in test_customer_matching.py -- these confirm the HTTP
# wiring (path param, 422 on ValueError via the app-level handler, response
# shaping) for the real confirmed pair (PT JAS, 2026-09-18): "MALIK
# SABYTAEV" and "MALIK S/RUSIA" are the same customer.
def test_customer_alias_roundtrip_and_exact_match(client):
    customer_id = client.post("/api/customers", json={"name": "MALIK SABYTAEV"}).json()["customer_id"]

    r = client.post(f"/api/customers/{customer_id}/aliases", json={"alias": "MALIK S/RUSIA"})
    assert r.status_code == 201, r.text
    alias_body = r.json()
    assert alias_body["customer_id"] == customer_id
    assert alias_body["alias"] == "MALIK S/RUSIA"

    per_customer = client.get(f"/api/customers/{customer_id}/aliases").json()
    assert any(a["alias"] == "MALIK S/RUSIA" for a in per_customer)

    flat = client.get("/api/customer-aliases").json()
    assert any(a["alias"] == "MALIK S/RUSIA" and a["customer_id"] == customer_id for a in flat)

    matches = client.get("/api/customers/match", params={"q": "malik s/rusia"}).json()
    assert len(matches) == 1
    assert matches[0]["customer_id"] == customer_id
    assert matches[0]["exact"] is True
    assert matches[0]["matched_alias"] == "MALIK S/RUSIA"


def test_customer_alias_conflict_returns_422(client):
    malik_id = client.post("/api/customers", json={"name": "MALIK SABYTAEV"}).json()["customer_id"]
    client.post("/api/customers", json={"name": "MALIK S/RUSIA"})  # already exists as its own customer

    r = client.post(f"/api/customers/{malik_id}/aliases", json={"alias": "MALIK S/RUSIA"})
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "invalid_input"


def test_customer_alias_unknown_customer_returns_422(client):
    r = client.post("/api/customers/99999/aliases", json={"alias": "ANYTHING"})
    assert r.status_code == 422, r.text


def test_delivery_full_depletion_returns_shipment_and_ships_batch(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "2.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    customer_id = client.post("/api/customers", json={"name": "LIBERTA GELATO"}).json()["customer_id"]

    r = client.post(
        "/api/delivery",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "sources": [{"batch_id": batch_id, "quantity": "2.000"}],
            "gross_weight": "2.275",
            "shipping_number": "DN/G/260120-001",
            "destination": "Tangerang",
            "recipient": "LIBERTA GELATO",
            "customer_id": customer_id,
            "coly": 1,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event"]["event_type"] == "DELIVERY"
    assert body["event"]["links"] == [
        {"batch_id": batch_id, "role": "INPUT", "quantity": "2.000", "unit": "kg"}
    ]
    shipment = body["shipment"]
    assert shipment["net_weight"] == "2.000"  # derived: SUM(sources) -- delivery.py #3
    assert shipment["tare_weight"] == "0.275"  # derived: gross - net -- delivery.py #3
    assert shipment["customer_id"] == customer_id
    assert shipment["recipient"] == "LIBERTA GELATO"

    shipped = client.get(f"/api/batches/{batch_id}").json()
    assert shipped["status"] == "SHIPPED"  # not CONSUMED -- delivery.py #8


def test_sample_delivery_is_tagged_is_sample_over_http(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "1.400",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(
        "/api/sample-delivery",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "sources": [{"batch_id": batch_id, "quantity": "1.400"}],
            "gross_weight": "1.505",
            "description": "EP",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["event"]["event_type"] == "SAMPLE_DELIVERY"

    txns = client.get(f"/api/batches/{batch_id}/transactions").json()
    assert any(t["is_sample"] is True for t in txns)


def test_list_deliveries_joins_shipment_for_both_event_types(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "5.000",
        },
    )
    b1 = r.json()["batch"]["batch_id"]
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "1.000",
        },
    )
    b2 = r.json()["batch"]["batch_id"]

    client.post(
        "/api/delivery",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "sources": [{"batch_id": b1, "quantity": "5.000"}],
            "gross_weight": "5.200",
            "shipping_number": "DN/1",
        },
    )
    client.post(
        "/api/sample-delivery",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "sources": [{"batch_id": b2, "quantity": "1.000"}],
            "gross_weight": "1.100",
            "shipping_number": "SMP/1",
        },
    )

    both = client.get("/api/deliveries").json()
    assert len(both) == 2
    assert {d["event"]["event_type"] for d in both} == {"DELIVERY", "SAMPLE_DELIVERY"}
    assert {d["shipment"]["shipping_number"] for d in both} == {"DN/1", "SMP/1"}

    only_samples = client.get("/api/deliveries?event_type=SAMPLE_DELIVERY").json()
    assert len(only_samples) == 1
    assert only_samples[0]["shipment"]["shipping_number"] == "SMP/1"


def test_delivery_insufficient_stock_is_409_over_http(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "1.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]
    r = client.post(
        "/api/delivery",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "sources": [{"batch_id": batch_id, "quantity": "5.000"}],
            "gross_weight": "5.000",
        },
    )
    assert r.status_code == 409


# ------------------------------------------------------- adjustment (Fase 15 slice 5)
# Same split as delivery above -- role-gating, mandatory-reason, audit-log
# writing, and REJECTED/SUPERSEDED status transitions are already covered
# at the service layer in test_adjustment.py. These confirm the HTTP layer
# (403/422/404 mapping, response shaping, the new generic /audit-logs
# report endpoint) works end-to-end.
def test_non_production_manager_adjustment_is_403_over_http(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "100.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]
    r = client.post(
        "/api/adjustment",
        json={
            "event_date": "2026-09-18",
            "batch_id": batch_id,
            "new_quantity": "90.000",
            "actor_user_id": pic_id,  # STAFF, not PRODUCTION_MANAGER
            "notes": "stok opname bulanan",
        },
    )
    assert r.status_code == 403


def test_production_manager_adjustment_updates_batch_and_writes_audit_log(
    client, supplier_id, pic_id, pm_id
):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "100.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(
        "/api/adjustment",
        json={
            "event_date": "2026-09-18",
            "batch_id": batch_id,
            "new_quantity": "92.500",
            "actor_user_id": pm_id,
            "notes": "Selisih stok opname fisik gudang",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["event_type"] == "ADJUSTMENT"

    updated = client.get(f"/api/batches/{batch_id}").json()
    assert updated["current_quantity"] == "92.500"

    logs = client.get(f"/api/audit-logs?entity_type=Batch&entity_id={batch_id}").json()
    assert any(l["action"] == "ADJUSTMENT_APPROVED" for l in logs)


def test_adjustment_without_reason_is_422_over_http(client, supplier_id, pic_id, pm_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "100.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]
    r = client.post(
        "/api/adjustment",
        json={
            "event_date": "2026-09-18",
            "batch_id": batch_id,
            "new_quantity": "90.000",
            "actor_user_id": pm_id,
            "notes": "   ",
        },
    )
    assert r.status_code == 422


def test_reject_batch_over_http_updates_status_and_audit_log(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "10.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(f"/api/batches/{batch_id}/reject", json={"actor_user_id": pic_id, "reason": "AW terlalu tinggi"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REJECTED"

    logs = client.get(f"/api/audit-logs?entity_type=Batch&entity_id={batch_id}").json()
    assert any(l["action"] == "REJECTED" and "AW terlalu tinggi" in (l["after_value"] or "") for l in logs)


def test_supersede_batch_over_http_updates_status(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_KERING",
            "net_quantity": "10.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(
        f"/api/batches/{batch_id}/supersede", json={"actor_user_id": pic_id, "reason": "Downgrade via Sortasi"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SUPERSEDED"


def test_reject_and_supersede_404_for_unknown_batch(client, pic_id):
    r1 = client.post("/api/batches/99999/reject", json={"actor_user_id": pic_id, "reason": "x"})
    r2 = client.post("/api/batches/99999/supersede", json={"actor_user_id": pic_id, "reason": "x"})
    assert r1.status_code == 404
    assert r2.status_code == 404


# ---------------------------------------------- Fase 20 UI / Fase 22 correction: Hijau route
# API-level tests for the six endpoints over services/curing.py --
# routers/curing.py, schemas StemRemovalCreate/BlanchingCreate/
# MainCuringCreate/FirstCuringCreate/SecondCuringCreate/ThirdCuringCreate/
# AirdryingCreate. Fase 22 (2026-09-19) corrected these shapes against a
# real source document (PROSES HIJAU 2026.xlsx) -- see
# services/curing.py module docstring. Mirrors
# test_full_chain_receiving_qc_md_steam_dry's shape: exercise the real HTTP
# layer, not the service functions directly (unit coverage lives in
# tests/test_curing.py).

# Main/1st/2nd/3rd Curing are stock-neutral (no final_quantity/shrinkage).
HIJAU_CURING_NEUTRAL_ENDPOINTS = [
    ("/api/main-curing", "MAIN_CURING"),
    ("/api/first-curing", "FIRST_CURING"),
    ("/api/second-curing", "SECOND_CURING"),
    ("/api/third-curing", "THIRD_CURING"),
]


@pytest.mark.parametrize("endpoint,event_type", HIJAU_CURING_NEUTRAL_ENDPOINTS)
def test_hijau_curing_stage_is_stock_neutral_over_http(client, supplier_id, pic_id, endpoint, event_type):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_HIJAU",
            "net_quantity": "100.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(
        endpoint,
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "duration_hours": "20",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event_type"] == event_type
    assert body["shrinkage_qty"] == "0"

    detail = client.get(f"/api/batches/{batch_id}")
    assert detail.json()["current_quantity"] == "100.000"

    history = client.get("/api/process-events", params={"batch_id": batch_id, "event_type": event_type})
    assert history.status_code == 200
    assert len(history.json()) == 1


@pytest.mark.parametrize("endpoint,event_type", HIJAU_CURING_NEUTRAL_ENDPOINTS)
def test_hijau_curing_stage_on_missing_batch_is_422_not_500(client, pic_id, endpoint, event_type):
    r = client.post(
        endpoint,
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "batch_id": 999999,
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_input"


def test_stem_removal_derives_shrinkage_over_http(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_HIJAU",
            "net_quantity": "77.740",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(
        "/api/stem-removal",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "final_quantity": "75.440",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["event_type"] == "STEM_REMOVAL"
    assert r.json()["shrinkage_qty"] == "2.300"


def test_blanching_is_stock_neutral_and_not_steaming_over_http(client, supplier_id, pic_id):
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_HIJAU",
            "net_quantity": "75.440",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    r = client.post(
        "/api/blanching",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "temperature": "65.0",
            "dip_duration_minutes": "2",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["event_type"] == "BLANCHING"
    assert r.json()["shrinkage_qty"] == "0"


def test_hijau_full_chain_over_http(client, supplier_id, pic_id):
    """WORKFLOW.md Hijau route, corrected in Fase 22: Lepas Tangkai ->
    Blanching -> Main -> 1st -> 2nd -> 3rd Curing -> Sundrying -> Airdrying,
    through the HTTP layer. Blanching is its own event type, distinct from
    Steaming (Kering-only, curing.py module docstring #1)."""
    r = client.post(
        "/api/receiving",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "supplier_id": supplier_id,
            "batch_type": "RAW_HIJAU",
            "net_quantity": "100.000",
        },
    )
    batch_id = r.json()["batch"]["batch_id"]

    stem = client.post(
        "/api/stem-removal",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "final_quantity": "97.000",
        },
    )
    assert stem.status_code == 201, stem.text

    blanch = client.post(
        "/api/blanching",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "temperature": "65.0",
            "dip_duration_minutes": "2",
        },
    )
    assert blanch.status_code == 201, blanch.text

    for endpoint in ("/api/main-curing", "/api/first-curing", "/api/second-curing", "/api/third-curing"):
        r = client.post(
            endpoint,
            json={"event_date": "2026-09-18", "pic_user_id": pic_id, "batch_id": batch_id},
        )
        assert r.status_code == 201, r.text

    dry = client.post(
        "/api/sundrying",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "final_quantity": "70.000",
            "drying_duration": "14 hari",
        },
    )
    assert dry.status_code == 201, dry.text

    air = client.post(
        "/api/airdrying",
        json={
            "event_date": "2026-09-18",
            "pic_user_id": pic_id,
            "batch_id": batch_id,
            "final_quantity": "65.000",
            "duration_days": "19",
            "final_ka": "22.05",
        },
    )
    assert air.status_code == 201, air.text
    assert air.json()["shrinkage_qty"] == "5.000"

    detail = client.get(f"/api/batches/{batch_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["current_quantity"] == "65.000"
    event_types = [e["event_type"] for e in body["events"]]
    assert event_types == [
        "RECEIVING",
        "STEM_REMOVAL",
        "BLANCHING",
        "MAIN_CURING",
        "FIRST_CURING",
        "SECOND_CURING",
        "THIRD_CURING",
        "SUNDRYING",
        "AIRDRYING",
    ]


def test_fase23_transport_fields_and_sortation_batch_numbers_via_api(client, supplier_id, pic_id):
    """Fase 23: PPH transport fields reach ProcessEvent.notes, and SORT's
    per-grade output batch numbers reach the new Batch rows (parsed
    components override the inherited ones, sortation.py #9)."""
    r = client.post("/api/receiving", json={
        "event_date": "2026-05-05", "pic_user_id": pic_id, "supplier_id": supplier_id,
        "batch_type": "RAW_HIJAU", "net_quantity": "77.740", "batch_number": "040018-260505-00",
        "transport_no": "AA 8520 EE", "transport_condition": "BAIK",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert "AA 8520 EE" in body["event"]["notes"]
    batch_id = body["batch"]["batch_id"]

    r = client.post("/api/sortation", json={
        "event_date": "2026-06-18", "pic_user_id": pic_id, "batch_id": batch_id,
        "eg_qty": "11.280", "eg_batch_number": "030218-260618-00",
    })
    assert r.status_code == 201, r.text
    out = r.json()["batches"][0]
    assert out["batch_number"] == "030218-260618-00"
    assert out["jenis_code"] == "03" and out["receiving_date"] == "2026-06-18"


def test_rendemen_sortation_endpoints(client, supplier_id, pic_id):
    """Fase 24: RENDEMEN derived from genealogy, list + single + 404."""
    r = client.post("/api/receiving", json={
        "event_date": "2026-09-18", "pic_user_id": pic_id, "supplier_id": supplier_id,
        "batch_type": "RAW_HIJAU", "net_quantity": "77.740"})
    src = r.json()["batch"]["batch_id"]
    s = client.post("/api/sortation", json={
        "event_date": "2026-09-18", "pic_user_id": pic_id, "batch_id": src,
        "initial_qty": "77.740", "eg_qty": "11.280", "ep_qty": "2.275"})
    assert s.status_code == 201, s.text
    event_id = s.json()["event"]["event_id"]

    rows = client.get("/api/rendemen/sortation").json()
    assert len(rows) == 1 and rows[0]["event_id"] == event_id
    assert rows[0]["raw_weight"] == "77.740" and rows[0]["complete"] is True
    assert rows[0]["output_quantity"] == "13.555"
    assert rows[0]["rendemen"] == "5.7352" and len(rows[0]["outputs"]) == 2

    one = client.get(f"/api/rendemen/sortation/{event_id}")
    assert one.status_code == 200 and one.json()["rendemen"] == "5.7352"
    assert len(client.get("/api/rendemen/sortation", params={"batch_id": src}).json()) == 1
    assert client.get("/api/rendemen/sortation", params={"batch_id": 999}).json() == []
    assert client.get("/api/rendemen/sortation/9999").status_code == 404


def test_date_order_audit_endpoint(client, supplier_id, pic_id):
    """Fase 25/25b: urutan tanggal terbalik diblokir default; audit kosong bila konsisten."""
    r = client.post("/api/receiving", json={
        "event_date": "2026-06-18", "pic_user_id": pic_id, "supplier_id": supplier_id,
        "batch_type": "RAW_HIJAU", "net_quantity": "10.000"})
    src = r.json()["batch"]["batch_id"]
    assert client.get("/api/audit/date-order").json() == []
    s = client.post("/api/sortation", json={
        "event_date": "2026-06-15", "pic_user_id": pic_id, "batch_id": src,
        "initial_qty": "10.000", "eg_qty": "2.000"})
    # Fase 25b: default = blokir (422, detail "lebih awal N hari"), tidak ada yang tersimpan.
    assert s.status_code == 422, s.text
    assert "lebih awal 3 hari" in s.json()["detail"]
    assert client.get("/api/audit/date-order").json() == []
    assert client.get("/api/audit/date-order", params={"batch_id": 999}).json() == []
    # Sortasi dicatat dengan tanggal MULAI yang benar -> lolos, audit bersih.
    ok = client.post("/api/sortation", json={
        "event_date": "2026-06-18", "pic_user_id": pic_id, "batch_id": src,
        "initial_qty": "10.000", "eg_qty": "2.000"})
    assert ok.status_code == 201, ok.text
    assert client.get("/api/audit/date-order").json() == []


def test_supplier_return_and_disposition_audit_endpoints(client, supplier_id, pic_id, pm_id):
    """Fase 26: batch REJECTED dikembalikan ke supplier (PM saja); audit = laporan."""
    r = client.post("/api/receiving", json={
        "event_date": "2026-06-18", "pic_user_id": pic_id, "supplier_id": supplier_id,
        "batch_type": "RAW_KERING", "net_quantity": "10.000"})
    b = r.json()["batch"]["batch_id"]
    body = {"event_date": "2026-06-20", "reason": "Ditolak MD1", "actor_user_id": pm_id}
    assert client.get("/api/audit/disposition").json() == []
    # Belum REJECTED -> 422
    assert client.post(f"/api/batches/{b}/return-to-supplier", json=body).status_code == 422
    assert client.post(f"/api/batches/{b}/reject", json={"actor_user_id": pic_id, "reason": "logam"}).status_code == 200
    rows = client.get("/api/audit/disposition").json()
    assert len(rows) == 1 and rows[0]["disposition"] == "PENDING_RETURN" and rows[0]["supplier_name"] == "WARDOYO"
    # STAFF -> 403
    denied = client.post(f"/api/batches/{b}/return-to-supplier", json={**body, "actor_user_id": pic_id})
    assert denied.status_code == 403 and denied.json()["error"] == "unauthorized_disposition"
    assert client.post("/api/batches/999/return-to-supplier", json=body).status_code == 404
    ok = client.post(f"/api/batches/{b}/return-to-supplier", json={**body, "quantity": "4.000"})
    assert ok.status_code == 201 and ok.json()["event_type"] == "SUPPLIER_RETURN"
    assert client.get("/api/audit/disposition").json()[0]["disposition"] == "PARTIALLY_RETURNED"
    assert client.post(f"/api/batches/{b}/return-to-supplier", json=body).status_code == 201
    row = client.get("/api/audit/disposition", params={"batch_id": b}).json()[0]
    assert row["disposition"] == "RETURNED" and row["returned_quantity"] == "10.000"
    assert client.get("/api/audit/disposition", params={"batch_id": 999}).json() == []
