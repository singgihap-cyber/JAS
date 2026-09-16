import datetime as dt
import json
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, EventType
from traceability_engine.models import Batch, QualityTest
from traceability_engine.services.powder import (
    GRADE_POWDER,
    GrindingInput,
    MagnetizationInput,
    MDPowderInput,
    record_grinding,
    record_magnetization,
    record_md_powder,
)
from traceability_engine.services.receiving import ReceivingInput, record_receiving

TODAY = dt.date(2026, 9, 16)


def _receive(session, staff_user, supplier, qty=Decimal("10.000"), batch_type=BatchType.RAW_KERING):
    event = record_receiving(
        session,
        ReceivingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            supplier_id=supplier.supplier_id,
            batch_type=batch_type,
            net_quantity=qty,
        ),
    )
    return session.query(Batch).filter_by(created_from_event_id=event.event_id).one().batch_id


# --- TEST_CASES.md #5 / GENEALOGY.md §3.1: Grinding is ONE->NEW-BATCH -------


def test_grinding_creates_new_powder_batch_and_derives_shrinkage(session, staff_user, supplier):
    nc_batch = _receive(session, staff_user, supplier, qty=Decimal("5.000"))

    event = record_grinding(
        session,
        GrindingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            nc_batch_id=nc_batch,
            final_qty=Decimal("4.500"),
            result_date=dt.date(2026, 9, 17),
        ),
    )

    assert event.event_type == EventType.GRINDING
    inputs = [l for l in event.links if l.role.value == "INPUT"]
    outputs = [l for l in event.links if l.role.value == "OUTPUT"]
    assert {l.batch_id for l in inputs} == {nc_batch}
    assert len(outputs) == 1

    # shrinkage = starting (5.000, on-hand default) - final (4.500) = 0.500
    assert event.shrinkage_qty == Decimal("0.500")

    assert session.get(Batch, nc_batch).status == BatchStatus.CONSUMED

    powder_batch = session.get(Batch, outputs[0].batch_id)
    assert powder_batch.batch_type == BatchType.POWDER
    assert powder_batch.grade_code == GRADE_POWDER
    assert powder_batch.current_quantity == Decimal("4.500")
    assert powder_batch.status == BatchStatus.ACTIVE

    notes = json.loads(event.notes)
    assert notes["result_date"] == "2026-09-17"


def test_grinding_inherits_source_batch_identity(session, staff_user, supplier):
    """Module docstring #6 -- single-source inheritance, same reasoning as
    Sortation's (sortation.py #4)."""
    nc_batch = _receive(session, staff_user, supplier, qty=Decimal("8.000"))
    source = session.get(Batch, nc_batch)
    source.jenis_code = "03"
    source.supplier_code = supplier.supplier_code
    session.flush()

    event = record_grinding(
        session,
        GrindingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            nc_batch_id=nc_batch,
            final_qty=Decimal("7.000"),
        ),
    )
    powder_batch_id = [l.batch_id for l in event.links if l.role.value == "OUTPUT"][0]
    powder = session.get(Batch, powder_batch_id)
    assert powder.jenis_code == "03"
    assert powder.supplier_id == supplier.supplier_id
    assert powder.supplier_code == supplier.supplier_code


def test_grinding_starting_qty_defaults_to_batch_on_hand(session, staff_user, supplier):
    nc_batch = _receive(session, staff_user, supplier, qty=Decimal("12.000"))

    event = record_grinding(
        session,
        GrindingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            nc_batch_id=nc_batch,
            final_qty=Decimal("10.000"),
        ),
    )
    quantities = {l.quantity for l in event.links if l.role.value == "INPUT"}
    assert quantities == {Decimal("12.000")}
    assert event.shrinkage_qty == Decimal("2.000")


def test_grinding_accepts_explicit_starting_qty_and_process_code(session, staff_user, supplier):
    nc_batch = _receive(session, staff_user, supplier, qty=Decimal("20.000"))

    event = record_grinding(
        session,
        GrindingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            nc_batch_id=nc_batch,
            starting_qty=Decimal("5.000"),
            final_qty=Decimal("4.000"),
            process_code="00",
        ),
    )
    quantities = {l.quantity for l in event.links if l.role.value == "INPUT"}
    assert quantities == {Decimal("5.000")}
    # source batch still has remaining balance -- not fully consumed
    assert session.get(Batch, nc_batch).current_quantity == Decimal("15.000")
    assert session.get(Batch, nc_batch).status == BatchStatus.ACTIVE

    powder_batch_id = [l.batch_id for l in event.links if l.role.value == "OUTPUT"][0]
    assert session.get(Batch, powder_batch_id).process_code == "00"


def test_grinding_negative_shrinkage_not_blocked(session, staff_user, supplier):
    nc_batch = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    event = record_grinding(
        session,
        GrindingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            nc_batch_id=nc_batch,
            final_qty=Decimal("5.500"),  # > starting qty -- implausible but not blocked
        ),
    )
    assert event.shrinkage_qty == Decimal("-0.500")


def test_grinding_rejects_non_positive_final_qty(session, staff_user, supplier):
    nc_batch = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    with pytest.raises(ValueError):
        record_grinding(
            session,
            GrindingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                nc_batch_id=nc_batch,
                final_qty=Decimal("0"),
            ),
        )


def test_grinding_rejects_non_positive_starting_qty(session, staff_user, supplier):
    nc_batch = _receive(session, staff_user, supplier, qty=Decimal("5.000"))
    with pytest.raises(ValueError):
        record_grinding(
            session,
            GrindingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                nc_batch_id=nc_batch,
                starting_qty=Decimal("0"),
                final_qty=Decimal("1.000"),
            ),
        )


def test_grinding_rejects_unknown_source_batch(session, staff_user):
    with pytest.raises(ValueError):
        record_grinding(
            session,
            GrindingInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                nc_batch_id=999999,
                final_qty=Decimal("1.000"),
            ),
        )


# --- Magnetization (MG) -- self-loop ONE->ONE, no QualityTest row ----------


def test_magnetization_is_self_loop_and_stock_neutral(session, staff_user, supplier):
    powder_batch = _receive(session, staff_user, supplier, qty=Decimal("4.500"), batch_type=BatchType.POWDER)

    event = record_magnetization(
        session,
        MagnetizationInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=powder_batch,
            finding="Tidak ditemukan serpihan logam",
            notes="Pemagnetan rutin",
        ),
    )

    assert event.event_type == EventType.MAGNETIZATION
    assert len(event.links) == 2
    assert {l.batch_id for l in event.links} == {powder_batch}

    batch = session.get(Batch, powder_batch)
    assert batch.current_quantity == Decimal("4.500")  # stock-neutral
    assert batch.status == BatchStatus.ACTIVE

    # module docstring #9 -- no QualityTest row written for MAGNETIZATION
    assert session.query(QualityTest).filter_by(event_id=event.event_id).count() == 0

    notes = json.loads(event.notes)
    assert notes["finding"] == "Tidak ditemukan serpihan logam"
    assert notes["notes"] == "Pemagnetan rutin"


def test_magnetization_quantity_defaults_to_batch_on_hand(session, staff_user, supplier):
    powder_batch = _receive(session, staff_user, supplier, qty=Decimal("7.000"), batch_type=BatchType.POWDER)

    event = record_magnetization(
        session,
        MagnetizationInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=powder_batch,
        ),
    )
    quantities = {l.quantity for l in event.links}
    assert quantities == {Decimal("7.000")}
    assert event.notes is None  # no finding/notes supplied


def test_magnetization_rejects_non_positive_quantity(session, staff_user, supplier):
    powder_batch = _receive(session, staff_user, supplier, qty=Decimal("7.000"), batch_type=BatchType.POWDER)
    with pytest.raises(ValueError):
        record_magnetization(
            session,
            MagnetizationInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=powder_batch,
                quantity=Decimal("0"),
            ),
        )


# --- Metal Detection Powder (MDPW) -- self-loop ONE->ONE, no QualityTest ---


def test_md_powder_is_self_loop_and_writes_finding_to_notes(session, staff_user, supplier):
    powder_batch = _receive(session, staff_user, supplier, qty=Decimal("4.500"), batch_type=BatchType.POWDER)

    event = record_md_powder(
        session,
        MDPowderInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=powder_batch,
            finding="Terdeteksi logam ringan",
            notes="Perlu pemagnetan ulang",
        ),
    )

    assert event.event_type == EventType.MD_POWDER
    assert len(event.links) == 2
    assert {l.batch_id for l in event.links} == {powder_batch}

    batch = session.get(Batch, powder_batch)
    assert batch.current_quantity == Decimal("4.500")  # stock-neutral
    assert batch.status == BatchStatus.ACTIVE  # never auto-rejected, same as MD (qc_md.py #1)

    assert session.query(QualityTest).filter_by(event_id=event.event_id).count() == 0

    notes = json.loads(event.notes)
    assert notes["finding"] == "Terdeteksi logam ringan"
    assert notes["notes"] == "Perlu pemagnetan ulang"


def test_md_powder_accepts_explicit_partial_quantity(session, staff_user, supplier):
    powder_batch = _receive(session, staff_user, supplier, qty=Decimal("10.000"), batch_type=BatchType.POWDER)

    event = record_md_powder(
        session,
        MDPowderInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=powder_batch,
            quantity=Decimal("2.000"),
        ),
    )
    quantities = {l.quantity for l in event.links}
    assert quantities == {Decimal("2.000")}
    assert session.get(Batch, powder_batch).current_quantity == Decimal("10.000")


def test_md_powder_rejects_non_positive_quantity(session, staff_user, supplier):
    powder_batch = _receive(session, staff_user, supplier, qty=Decimal("10.000"), batch_type=BatchType.POWDER)
    with pytest.raises(ValueError):
        record_md_powder(
            session,
            MDPowderInput(
                event_date=TODAY,
                pic_user_id=staff_user.user_id,
                batch_id=powder_batch,
                quantity=Decimal("0"),
            ),
        )
