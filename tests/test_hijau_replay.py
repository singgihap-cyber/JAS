"""Fase 23 -- replay of the three real Hijau batches in
`PROSES HIJAU 2026.xlsx` (sheets PPH -> LT -> PL -> P INTI/P1/P2/P3 -> KR ->
SORT -> KW -> MD) through the actual services, asserting the sheet's own
weights. Validates that the pre-existing generic stages (Receiving,
Sortation, QC, MD) really fit the Hijau route, and pins the two gaps found
(transport fields; per-grade output batch numbers) as regression tests.

Deliberate choices, all from reading the sheet:
- Stem removal / blanching / curing / airdrying use the on-hand quantity
  instead of retyping the sheet's rounded figure (PL/KR show 14.83 for
  the third batch while LT's 14.825 is the exact value).
- The drying step uses Airdrying (sheet KR): KM (Sundrying) lists the same
  start/end weights for the same batches, so both cannot be applied in
  sequence -- open question for PT JAS, see PROJECT_STATUS.md.
- Batch number typos in the source (`040024-250618-00` year `25`,
  `...-260525-000` in P3) are NOT normalised here; the first is stored as
  keyed (year 2025 parses fine), matching the lenient rule in receiving.py.
"""
import datetime as dt
import json
from decimal import Decimal as D

import pytest

from traceability_engine.enums import BatchType, QCStage
from traceability_engine.models import Batch, ProcessEvent
from traceability_engine.services.curing import (
    AirdryingInput, BlanchingInput, CuringStageInput, StemRemovalInput,
    record_airdrying, record_blanching, record_first_curing, record_main_curing,
    record_second_curing, record_stem_removal, record_third_curing,
)
from traceability_engine.services.qc_md import (
    MetalDetectionInput, QCTestInput, record_metal_detection, record_qc_test,
)
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.sortation import SortationInput, record_sortation

# (green batch no, receiving date, PPH netto, LT limbah, KR final weight,
#  SORT kwargs, SORT date, expected LT final)
CASES = [
    dict(no="040018-260505-00", recv=dt.date(2026, 5, 5), net=D("77.74"), limbah=D("2.3"),
         final=D("13.555"), sort_date=dt.date(2026, 6, 18), lt_final=D("75.44"),
         outs={"eg": ("030218-260618-00", D("11.28")), "ep": ("030318-260618-00", D("2.275"))},
         supplier_code="18"),
    dict(no="040018-260525-00", recv=dt.date(2026, 5, 25), net=D("28.24"), limbah=D("0.85"),
         final=D("5.77"), sort_date=dt.date(2026, 7, 7), lt_final=D("27.39"),
         outs={"gourmet": ("030118-260707-00", D("1.92")), "eg": ("030218-260707-00", D("3.85"))},
         supplier_code="18"),
    dict(no="040024-250618-00", recv=dt.date(2026, 6, 18), net=D("15.1"), limbah=D("0.275"),
         final=D("3.715"), sort_date=dt.date(2026, 7, 29), lt_final=D("14.825"),
         outs={"eg": ("030224-260729-00", D("3.715"))}, supplier_code="24"),
]


@pytest.mark.parametrize("c", CASES, ids=[c["no"] for c in CASES])
def test_replay_real_hijau_batch(session, staff_user, supplier, c):
    uid = staff_user.user_id
    ev = record_receiving(session, ReceivingInput(
        event_date=c["recv"], pic_user_id=uid, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_HIJAU, net_quantity=c["net"], batch_number=c["no"],
        packaging_condition="BAIK", transport_no="AA 8520 EE", transport_condition="BAIK",
    ))
    green = session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()
    assert green.batch_number == c["no"] and green.jenis_code == "04" and green.grade_code == "00"
    assert json.loads(ev.notes)["transport_no"] == "AA 8520 EE"

    record_stem_removal(session, StemRemovalInput(
        event_date=c["recv"], pic_user_id=uid, batch_id=green.batch_id,
        final_quantity=c["net"] - c["limbah"]))
    assert green.current_quantity == c["lt_final"]  # sheet LT "BERAT AKHIR"

    record_blanching(session, BlanchingInput(
        event_date=c["recv"], pic_user_id=uid, batch_id=green.batch_id,
        temperature=D("65"), dip_duration_minutes=D("2")))
    for fn in (record_main_curing, record_first_curing, record_second_curing, record_third_curing):
        fn(session, CuringStageInput(event_date=c["recv"], pic_user_id=uid,
                                     batch_id=green.batch_id, duration_hours=D("20")))
    assert green.current_quantity == c["lt_final"]  # curing is stock-neutral

    record_airdrying(session, AirdryingInput(
        event_date=c["sort_date"], pic_user_id=uid, batch_id=green.batch_id,
        final_quantity=c["final"], duration_days=D("19"), final_ka=D("22.05")))
    assert green.current_quantity == c["final"]  # sheet KR "BERAT AKHIR"

    kwargs = {}
    for grade, (bn, qty) in c["outs"].items():
        kwargs[f"{grade}_qty"] = qty
        kwargs[f"{grade}_batch_number"] = bn
    sort_ev = record_sortation(session, SortationInput(
        event_date=c["sort_date"], pic_user_id=uid, batch_id=green.batch_id, **kwargs))
    assert sort_ev.shrinkage_qty == D("0")  # SORT "TOTAL" == input in every row

    outs = {b.batch_number: b for b in session.query(Batch).filter(
        Batch.batch_number.in_([bn for bn, _ in c["outs"].values()]))}
    assert len(outs) == len(c["outs"])
    for grade, (bn, qty) in c["outs"].items():
        b = outs[bn]
        assert b.current_quantity == qty
        # AA flips 04 -> 03 and date = sort date, exactly as typed on the sheet.
        assert b.jenis_code == "03"
        assert b.receiving_date == c["sort_date"]
        assert b.supplier_code == c["supplier_code"]
        assert b.process_code == "00"
        assert b.batch_type == BatchType.PROCESSED
    assert green.current_quantity == D("0")

    # KW (QC) + MD on each sorted batch.
    for bn, qty in c["outs"].values():
        b = outs[bn]
        qc = record_qc_test(session, QCTestInput(
            event_date=c["sort_date"], pic_user_id=uid, batch_id=b.batch_id, stage=QCStage.RM,
            sample_received_date=c["sort_date"], ka_1=D("22.25"), ka_2=D("21.85"), aw=D("0.785"),
            method_temperature=D("153"), product_description="EG"))
        assert json.loads(qc.notes)["method_temperature"] == "153"
        record_metal_detection(session, MetalDetectionInput(
            event_date=c["sort_date"], pic_user_id=uid, batch_id=b.batch_id,
            stage=QCStage.RM, finding="0"))
        assert b.current_quantity == qty  # QC/MD stay stock-neutral


def test_sortation_without_batch_numbers_still_inherits(session, staff_user, supplier):
    """Pre-Fase 23 behavior is unchanged when no batch number is typed."""
    ev = record_receiving(session, ReceivingInput(
        event_date=dt.date(2026, 5, 5), pic_user_id=staff_user.user_id,
        supplier_id=supplier.supplier_id, batch_type=BatchType.RAW_HIJAU,
        net_quantity=D("10"), batch_number="040018-260505-00"))
    src = session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()
    record_sortation(session, SortationInput(
        event_date=dt.date(2026, 6, 18), pic_user_id=staff_user.user_id,
        batch_id=src.batch_id, eg_qty=D("10")))
    out = session.query(Batch).filter(Batch.batch_id != src.batch_id).one()
    assert out.batch_number is None and out.jenis_code == "04"
    assert out.receiving_date == dt.date(2026, 5, 5)


def test_sortation_unparseable_batch_number_kept_as_free_text(session, staff_user, supplier):
    ev = record_receiving(session, ReceivingInput(
        event_date=dt.date(2026, 5, 5), pic_user_id=staff_user.user_id,
        supplier_id=supplier.supplier_id, batch_type=BatchType.RAW_HIJAU,
        net_quantity=D("10"), batch_number="040018-260505-00"))
    src = session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()
    record_sortation(session, SortationInput(
        event_date=dt.date(2026, 6, 18), pic_user_id=staff_user.user_id,
        batch_id=src.batch_id, eg_qty=D("10"), eg_batch_number="EG-MANUAL"))
    out = session.query(Batch).filter(Batch.batch_id != src.batch_id).one()
    assert out.batch_number == "EG-MANUAL" and out.grade_code == "02" and out.jenis_code == "04"
