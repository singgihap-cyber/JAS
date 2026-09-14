import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType, QCStage
from traceability_engine.models import Batch, QualityTest
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.qc_md import (
    MetalDetectionInput,
    QCTestInput,
    record_metal_detection,
    record_qc_test,
)

TODAY = dt.date(2026, 9, 14)


def _receive(session, staff_user, supplier, qty=Decimal("100.000")):
    event = record_receiving(
        session,
        ReceivingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            supplier_id=supplier.supplier_id,
            batch_type=BatchType.RAW_KERING,
            net_quantity=qty,
        ),
    )
    return session.query(Batch).filter_by(created_from_event_id=event.event_id).one().batch_id


def test_qc_test_is_self_loop_and_writes_quality_test_row(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)

    event = record_qc_test(
        session,
        QCTestInput(
            event_date=dt.date(2026, 9, 15),
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            stage=QCStage.RM,
            sample_received_date=dt.date(2026, 9, 14),
            sample_weight=Decimal("0.500"),
            ka_1=Decimal("32.100"),
            ka_2=Decimal("32.400"),
            ka_3=Decimal("32.250"),
            aw=Decimal("0.650"),
            finding="Diterima",
        ),
    )

    assert event.event_type == EventType.QC_TEST
    assert {l.batch_id for l in event.links} == {batch_id}
    assert len(event.links) == 2  # one INPUT + one OUTPUT row, same batch (self-loop)

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("100.000")  # stock-neutral
    assert batch.status == BatchStatus.ACTIVE  # never auto-changed

    qt = session.query(QualityTest).filter_by(event_id=event.event_id).one()
    assert qt.batch_id == batch_id
    assert qt.stage == QCStage.RM
    assert qt.sample_weight == Decimal("0.500")
    assert qt.ka_1 == Decimal("32.100")
    assert qt.ka_2 == Decimal("32.400")
    assert qt.ka_3 == Decimal("32.250")
    assert qt.aw == Decimal("0.650")
    assert qt.finding == "Diterima"
    assert qt.metal_detection_finding is None

    notes = json.loads(event.notes)
    assert notes["sample_received_date"] == "2026-09-14"


def test_qc_test_defaults_quantity_to_batch_on_hand(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("42.000"))

    event = record_qc_test(
        session,
        QCTestInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            stage=QCStage.FP,
        ),
    )
    quantities = {l.quantity for l in event.links}
    assert quantities == {Decimal("42.000")}


def test_qc_test_does_not_evaluate_a_threshold(session, staff_user, supplier):
    """No numeric acceptance rule -- GENEALOGY.md §5.1. Extreme KA/AW values
    must not raise or change batch status; only a manual finding is stored."""
    batch_id = _receive(session, staff_user, supplier)

    record_qc_test(
        session,
        QCTestInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            stage=QCStage.IP,
            aw=Decimal("99.000"),  # implausibly high -- must not trigger any rule
            finding="Tinggi, perlu keputusan manual",
        ),
    )
    batch = session.get(Batch, batch_id)
    assert batch.status == BatchStatus.ACTIVE


def test_qc_test_rejects_non_positive_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_qc_test(
            session,
            QCTestInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                stage=QCStage.RM,
                quantity=Decimal("0"),
            ),
        )


def test_metal_detection_is_self_loop_and_writes_finding(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)

    event = record_metal_detection(
        session,
        MetalDetectionInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            stage=QCStage.RM,
            product_status="OK",
            finding="Tidak ditemukan logam",
            description="MD1 setelah QC awal",
        ),
    )

    assert event.event_type == EventType.METAL_DETECTION
    assert len(event.links) == 2
    assert {l.batch_id for l in event.links} == {batch_id}

    batch = session.get(Batch, batch_id)
    assert batch.current_quantity == Decimal("100.000")  # stock-neutral
    assert batch.status == BatchStatus.ACTIVE

    qt = session.query(QualityTest).filter_by(event_id=event.event_id).one()
    assert qt.metal_detection_finding == "Tidak ditemukan logam"
    assert qt.ka_1 is None
    assert qt.sample_weight is None

    notes = json.loads(event.notes)
    assert notes["product_status"] == "OK"
    assert notes["description"] == "MD1 setelah QC awal"


def test_metal_detection_accepts_explicit_partial_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier, qty=Decimal("60.000"))

    event = record_metal_detection(
        session,
        MetalDetectionInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            stage=QCStage.FP,
            quantity=Decimal("10.000"),
        ),
    )
    quantities = {l.quantity for l in event.links}
    assert quantities == {Decimal("10.000")}
    assert session.get(Batch, batch_id).current_quantity == Decimal("60.000")


def test_metal_detection_does_not_auto_reject(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    record_metal_detection(
        session,
        MetalDetectionInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=batch_id,
            stage=QCStage.RM,
            product_status="TERDETEKSI LOGAM",
            finding="Ditemukan serpihan logam",
        ),
    )
    assert session.get(Batch, batch_id).status == BatchStatus.ACTIVE


def test_metal_detection_rejects_non_positive_quantity(session, staff_user, supplier):
    batch_id = _receive(session, staff_user, supplier)
    with pytest.raises(ValueError):
        record_metal_detection(
            session,
            MetalDetectionInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=batch_id,
                stage=QCStage.RM,
                quantity=Decimal("0"),
            ),
        )
