"""Fase 24 -- RENDEMEN Sortasi derived from genealogy (services/rendemen.py).

Real anchor: sheet SORT of `PROSES HIJAU 2026.xlsx` (see also
tests/test_hijau_replay.py). The sheet's own helper column shows the exact
ratios 5.7351..., 4.8942..., 4.0646...; its RENDEMEN column shows them
rounded to 2 dp (5.74 / 4.89 / 4.07 -- the third is the sheet rounding
4.0646 up, not a different formula).
"""
import datetime as dt
from decimal import Decimal as D

import pytest

from traceability_engine.enums import BatchType, EventType
from traceability_engine.models import Batch
from traceability_engine.services.curing import (
    AirdryingInput, BlanchingInput, StemRemovalInput,
    record_airdrying, record_blanching, record_stem_removal,
)
from traceability_engine.services.events import (
    InputSpec, NewBatchSpec, OutputSpec, record_process_event,
)
from traceability_engine.services.qc_md import QCTestInput, record_qc_test
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.rendemen import (
    list_sortation_rendemen, sortation_rendemen,
)
from traceability_engine.services.sortation import SortationInput, record_sortation
from traceability_engine.enums import QCStage

D0 = dt.date(2026, 6, 18)

# (batch no, net received, limbah, airdried final, {grade: qty}, exact ratio)
REAL = [
    ("040018-260505-00", D("77.74"), D("2.3"), D("13.555"),
     {"eg": D("11.28"), "ep": D("2.275")}, D("5.7352")),
    ("040018-260525-00", D("28.24"), D("0.85"), D("5.77"),
     {"gourmet": D("1.92"), "eg": D("3.85")}, D("4.8943")),
    ("040024-250618-00", D("15.1"), D("0.275"), D("3.715"),
     {"eg": D("3.715")}, D("4.0646")),
]


def _green(session, uid, supplier, net, no=None):
    ev = record_receiving(session, ReceivingInput(
        event_date=D0, pic_user_id=uid, supplier_id=supplier.supplier_id,
        batch_type=BatchType.RAW_HIJAU, net_quantity=net, batch_number=no))
    return session.query(Batch).filter_by(created_from_event_id=ev.event_id).one()


@pytest.mark.parametrize("no,net,limbah,final,outs,ratio", REAL, ids=[r[0] for r in REAL])
def test_real_sort_sheet_rows(session, staff_user, supplier, no, net, limbah, final, outs, ratio):
    uid = staff_user.user_id
    g = _green(session, uid, supplier, net, no)
    record_stem_removal(session, StemRemovalInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, final_quantity=net - limbah))
    record_blanching(session, BlanchingInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, temperature=D("65"),
        dip_duration_minutes=D("2")))
    record_airdrying(session, AirdryingInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, final_quantity=final))
    ev = record_sortation(session, SortationInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id,
        **{f"{k}_qty": v for k, v in outs.items()}))

    r = sortation_rendemen(session, ev.event_id)
    assert r.raw_weight == net                   # PPH netto, not the post-LT weight
    assert r.output_quantity == final            # sheet TOTAL (KG)
    assert r.rendemen == ratio
    assert r.complete and len(r.outputs) == len(outs)
    assert r.yield_percent == (final / net * 100).quantize(D("0.0001"))


def test_qc_md_in_place_events_do_not_move_raw_weight(session, staff_user, supplier):
    uid = staff_user.user_id
    g = _green(session, uid, supplier, D("100"))
    record_qc_test(session, QCTestInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, stage=QCStage.RM, ka_1=D("20")))
    ev = record_sortation(session, SortationInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, eg_qty=D("100")))
    assert sortation_rendemen(session, ev.event_id).rendemen == D("1.0000")


def test_partial_sortations_split_raw_weight_by_mass_fraction(session, staff_user, supplier):
    """100 kg received, dried to 40 kg, sorted in two sessions (10 then 30):
    each session carries its share of the 100 kg, so both are 2.5 -- not
    100/10 and 100/30, which would double-count the raw weight."""
    uid = staff_user.user_id
    g = _green(session, uid, supplier, D("100"))
    record_airdrying(session, AirdryingInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, final_quantity=D("40")))
    e1 = record_sortation(session, SortationInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, initial_qty=D("10"), eg_qty=D("10")))
    e2 = record_sortation(session, SortationInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, initial_qty=D("30"), eg_qty=D("30")))
    r1, r2 = sortation_rendemen(session, e1.event_id), sortation_rendemen(session, e2.event_id)
    assert (r1.raw_weight, r1.rendemen) == (D("25.000"), D("2.5000"))
    assert (r2.raw_weight, r2.rendemen) == (D("75.000"), D("2.5000"))
    assert r1.raw_weight + r2.raw_weight == D("100.000")


def test_multi_grade_output_shares_one_rendemen(session, staff_user, supplier):
    """Rendemen is per sortation row (over TOTAL), identical for all grades."""
    uid = staff_user.user_id
    g = _green(session, uid, supplier, D("60"))
    record_airdrying(session, AirdryingInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, final_quantity=D("12")))
    ev = record_sortation(session, SortationInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, eg_qty=D("8"), ep_qty=D("4")))
    r = sortation_rendemen(session, ev.event_id)
    assert r.rendemen == D("5.0000") and r.output_quantity == D("12")


def test_mixing_then_sortation_sums_all_sources(session, staff_user, supplier):
    """Two raw batches (100 and 50) dried to 20 and 10, mixed into 30, sorted:
    the mixed batch carries 150 kg of raw weight -> ratio 5."""
    uid = staff_user.user_id
    a, b = _green(session, uid, supplier, D("100")), _green(session, uid, supplier, D("50"))
    for batch, final in ((a, D("20")), (b, D("10"))):
        record_airdrying(session, AirdryingInput(
            event_date=D0, pic_user_id=uid, batch_id=batch.batch_id, final_quantity=final))
    mix = record_process_event(
        session, event_type=EventType.MIXING, event_date=D0, pic_user_id=uid,
        inputs=[InputSpec(a.batch_id, D("20")), InputSpec(b.batch_id, D("10"))],
        outputs=[OutputSpec(D("30"), new_batch=NewBatchSpec(batch_type=BatchType.PROCESSED))])
    mixed = [l.batch_id for l in mix.links if l.role.value == "OUTPUT"][0]
    ev = record_sortation(session, SortationInput(
        event_date=D0, pic_user_id=uid, batch_id=mixed, eg_qty=D("30")))
    r = sortation_rendemen(session, ev.event_id)
    assert r.raw_weight == D("150.000") and r.rendemen == D("5.0000")


def test_unknown_lineage_is_reported_not_guessed(session, staff_user):
    orphan = Batch(batch_type=BatchType.PROCESSED, current_quantity=D("10"))  # no creating event
    session.add(orphan)
    session.flush()
    ev = record_sortation(session, SortationInput(
        event_date=D0, pic_user_id=staff_user.user_id, batch_id=orphan.batch_id, eg_qty=D("10")))
    r = sortation_rendemen(session, ev.event_id)
    assert r.raw_weight is None and r.rendemen is None and r.yield_percent is None
    assert r.complete is False and r.output_quantity == D("10")


def test_shrinkage_in_sortation_uses_output_total(session, staff_user, supplier):
    uid = staff_user.user_id
    g = _green(session, uid, supplier, D("50"))
    ev = record_sortation(session, SortationInput(
        event_date=D0, pic_user_id=uid, batch_id=g.batch_id, eg_qty=D("9")))
    r = sortation_rendemen(session, ev.event_id)
    # input defaults to on-hand (50); output 9 -> shrinkage 41, base = output total
    assert r.input_quantity == D("50") and r.shrinkage_qty == D("41")
    assert r.rendemen == (D("50") / D("9")).quantize(D("0.0001"))


def test_non_sortation_event_rejected(session, staff_user, supplier):
    g = _green(session, staff_user.user_id, supplier, D("10"))
    with pytest.raises(ValueError):
        sortation_rendemen(session, g.created_from_event_id)
    with pytest.raises(ValueError):
        sortation_rendemen(session, 99999)


def test_list_filters_by_batch_and_date(session, staff_user, supplier):
    uid = staff_user.user_id
    g1, g2 = _green(session, uid, supplier, D("10")), _green(session, uid, supplier, D("20"))
    e1 = record_sortation(session, SortationInput(
        event_date=dt.date(2026, 6, 20), pic_user_id=uid, batch_id=g1.batch_id, eg_qty=D("10")))
    e2 = record_sortation(session, SortationInput(
        event_date=dt.date(2026, 7, 1), pic_user_id=uid, batch_id=g2.batch_id, eg_qty=D("20")))
    assert [r.event_id for r in list_sortation_rendemen(session)] == [e1.event_id, e2.event_id]
    assert [r.event_id for r in list_sortation_rendemen(session, batch_id=g2.batch_id)] == [e2.event_id]
    out1 = sortation_rendemen(session, e1.event_id).outputs[0].batch_id
    assert [r.event_id for r in list_sortation_rendemen(session, batch_id=out1)] == [e1.event_id]
    assert [r.event_id for r in list_sortation_rendemen(session, date_from=dt.date(2026, 6, 25))] == [e2.event_id]
    assert [r.event_id for r in list_sortation_rendemen(session, date_to=dt.date(2026, 6, 25))] == [e1.event_id]
