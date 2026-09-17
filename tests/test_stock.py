"""Fase 13 -- Stock Ledger query/report layer (services/stock.py).

These tests exercise the ledger as a *read* layer on top of events that
other phases already record (Receiving, Sortation, Delivery, Adjustment) --
per 13_STOCK.md, this phase adds no new way to change a balance, so every
test here either reads what a prior-phase service wrote, or deliberately
tampers with `Batch.current_quantity` by hand (bypassing every gated write
path) to prove the reconciliation sweep actually catches that.
"""
import datetime as dt
from decimal import Decimal

import pytest

from traceability_engine.enums import BatchStatus, BatchType, TransactionDirection
from traceability_engine.exceptions import StockReconciliationError
from traceability_engine.models import Batch
from traceability_engine.services.adjustment import record_adjustment
from traceability_engine.services.delivery import DeliveryInput, DeliverySource, record_delivery
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.sortation import SortationInput, record_sortation
from traceability_engine.services.stock import (
    BatchBalance,
    compute_ledger_balance,
    get_batch_balance,
    reconcile_all_batches,
    reconcile_batch,
    stock_summary,
    total_on_hand_stock,
    transaction_history,
)

TODAY = dt.date(2026, 9, 17)
Q = Decimal


def _receive(session, staff_user, supplier, qty, batch_number=None):
    event = record_receiving(
        session,
        ReceivingInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            supplier_id=supplier.supplier_id,
            batch_type=BatchType.RAW_KERING,
            net_quantity=qty,
            batch_number=batch_number,
        ),
    )
    return session.query(Batch).filter_by(created_from_event_id=event.event_id).one()


# --- transaction_history ---------------------------------------------------


def test_transaction_history_orders_chronologically_and_matches_balance_after(
    session, staff_user, production_manager, supplier
):
    batch = _receive(session, staff_user, supplier, Q("100.000"))

    record_adjustment(
        session,
        batch_id=batch.batch_id,
        new_quantity=Q("90.000"),
        actor_user_id=production_manager.user_id,
        notes="Koreksi stok opname",
        event_date=TODAY,
    )

    txns = transaction_history(session, batch.batch_id)
    assert len(txns) == 2
    assert [t.direction for t in txns] == [TransactionDirection.IN, TransactionDirection.OUT]
    assert [t.balance_after for t in txns] == [Q("100.000"), Q("90.000")]
    # Ordering must be chronological (insertion/transaction_id order), not
    # e.g. reversed or grouped by direction.
    assert txns[0].transaction_id < txns[1].transaction_id


def test_transaction_history_raises_for_unknown_batch(session):
    with pytest.raises(ValueError):
        transaction_history(session, batch_id=999999)


def test_transaction_history_and_ledger_balance_after_receiving(session, staff_user, supplier):
    batch = _receive(session, staff_user, supplier, Q("55.500"))

    txns = transaction_history(session, batch.batch_id)
    assert len(txns) == 1
    assert txns[0].direction == TransactionDirection.IN
    assert txns[0].quantity == Q("55.500")
    assert txns[0].balance_after == Q("55.500")

    assert compute_ledger_balance(session, batch.batch_id) == Q("55.500")


# --- get_batch_balance / reconcile_batch -----------------------------------


def test_get_batch_balance_matches_by_default(session, staff_user, supplier):
    batch = _receive(session, staff_user, supplier, Q("40.000"))

    result = get_batch_balance(session, batch.batch_id)
    assert isinstance(result, BatchBalance)
    assert result.cached_quantity == Q("40.000")
    assert result.ledger_quantity == Q("40.000")
    assert result.matches is True
    assert result.status == BatchStatus.ACTIVE

    # reconcile_batch() must not raise when the cache and ledger agree.
    reconcile_batch(session, batch.batch_id)


def test_get_batch_balance_raises_for_unknown_batch(session):
    with pytest.raises(ValueError):
        get_batch_balance(session, batch_id=999999)


def test_reconcile_batch_detects_manual_cache_tampering(session, staff_user, supplier):
    """13_STOCK.md: 'do not manually overwrite current stock'. This test
    proves that if something ever did bypass record_process_event()/
    record_adjustment() and wrote Batch.current_quantity directly, the
    ledger-vs-cache reconciliation catches it rather than silently trusting
    the cache (DATABASE_DESIGN.md §1: the ledger is the source of truth).
    """
    batch = _receive(session, staff_user, supplier, Q("30.000"))

    # Simulate an unauthorized direct overwrite of the cache -- exactly the
    # thing 13_STOCK.md says must not happen, and exactly the thing this
    # module has no API for doing legitimately.
    batch.current_quantity = Q("999.000")
    session.flush()

    with pytest.raises(StockReconciliationError):
        reconcile_batch(session, batch.batch_id)

    result = get_batch_balance(session, batch.batch_id)
    assert result.matches is False
    assert result.cached_quantity == Q("999.000")
    assert result.ledger_quantity == Q("30.000")


def test_reconcile_all_batches_returns_only_mismatches(session, staff_user, supplier):
    good_batch = _receive(session, staff_user, supplier, Q("10.000"))
    bad_batch = _receive(session, staff_user, supplier, Q("20.000"))

    bad_batch.current_quantity = Q("500.000")  # tamper with one batch only
    session.flush()

    mismatches = reconcile_all_batches(session)
    assert [m.batch_id for m in mismatches] == [bad_batch.batch_id]
    assert good_batch.batch_id not in [m.batch_id for m in mismatches]


# --- stock_summary / total_on_hand_stock -----------------------------------


def test_stock_summary_groups_by_supplier_jenis_grade_and_excludes_non_active(
    session, staff_user, production_manager, supplier
):
    # Two ACTIVE batches sharing supplier/jenis/grade (via parsed batch_number)
    # should collapse into one summary row with combined quantity.
    b1 = _receive(
        session, staff_user, supplier, Q("55.500"), batch_number="030224-260221-00"
    )
    b2 = _receive(
        session, staff_user, supplier, Q("10.000"), batch_number="030224-260222-00"
    )
    assert (b1.jenis_code, b1.grade_code, b1.supplier_id) == (
        b2.jenis_code,
        b2.grade_code,
        b2.supplier_id,
    )

    # A batch fully delivered (SHIPPED) must not count as on-hand stock.
    shipped_source = _receive(session, staff_user, supplier, Q("5.000"))
    record_delivery(
        session,
        DeliveryInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[DeliverySource(batch_id=shipped_source.batch_id, quantity=Q("5.000"))],
            gross_weight=Q("5.500"),
        ),
    )
    assert shipped_source.status == BatchStatus.SHIPPED

    # A REJECTED batch (quarantined, still holding quantity) must also not
    # count as on-hand stock -- GENEALOGY.md §3.2's own definition of ACTIVE.
    from traceability_engine.services.adjustment import mark_batch_rejected

    rejected_source = _receive(session, staff_user, supplier, Q("7.000"))
    mark_batch_rejected(
        session, batch_id=rejected_source.batch_id, actor_user_id=staff_user.user_id, reason="test"
    )

    summary = stock_summary(session)
    matching_rows = [
        r
        for r in summary
        if r.supplier_id == b1.supplier_id
        and r.jenis_code == b1.jenis_code
        and r.grade_code == b1.grade_code
    ]
    assert len(matching_rows) == 1
    row = matching_rows[0]
    assert row.batch_count == 2
    assert row.total_quantity == Q("65.500")

    total = total_on_hand_stock(session)
    # Total on-hand must include only ACTIVE batches: the two above (65.5)
    # plus nothing from the SHIPPED or REJECTED batches.
    assert total == Q("65.500")


def test_total_on_hand_stock_is_zero_when_no_active_batches(session):
    assert total_on_hand_stock(session) == Decimal("0")


# --- integration across Receiving / Sortation / Delivery / Adjustment -----


def test_stock_reflects_receiving_sortation_delivery_and_adjustment(
    session, staff_user, production_manager, supplier
):
    received = _receive(session, staff_user, supplier, Q("100.000"))
    reconcile_batch(session, received.batch_id)

    sort_event = record_sortation(
        session,
        SortationInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            batch_id=received.batch_id,
            initial_qty=Q("100.000"),
            gourmet_qty=Q("60.000"),
            ep_qty=Q("35.000"),
            # 5.000 shrinkage, derived
        ),
    )
    assert received.status == BatchStatus.CONSUMED
    reconcile_batch(session, received.batch_id)  # 0 on-hand now, ledger agrees

    from traceability_engine.models import EventBatchLink
    from traceability_engine.enums import LinkRole

    outputs = (
        session.query(EventBatchLink)
        .filter_by(event_id=sort_event.event_id, role=LinkRole.OUTPUT)
        .all()
    )
    gourmet_batch = next(o.batch for o in outputs if o.quantity == Q("60.000"))
    ep_batch = next(o.batch for o in outputs if o.quantity == Q("35.000"))

    for b in (gourmet_batch, ep_batch):
        reconcile_batch(session, b.batch_id)

    # Deliver the Gourmet batch out entirely.
    record_delivery(
        session,
        DeliveryInput(
            event_date=TODAY,
            pic_user_id=staff_user.user_id,
            sources=[DeliverySource(batch_id=gourmet_batch.batch_id, quantity=Q("60.000"))],
            gross_weight=Q("61.000"),
        ),
    )
    assert gourmet_batch.status == BatchStatus.SHIPPED
    reconcile_batch(session, gourmet_batch.batch_id)

    # Stock opname correction on the EP batch: physical count says 33.000, not 35.000.
    record_adjustment(
        session,
        batch_id=ep_batch.batch_id,
        new_quantity=Q("33.000"),
        actor_user_id=production_manager.user_id,
        notes="Physical stock count found shrinkage not previously recorded",
        event_date=TODAY,
    )
    reconcile_batch(session, ep_batch.batch_id)
    assert compute_ledger_balance(session, ep_batch.batch_id) == Q("33.000")

    txns = transaction_history(session, ep_batch.batch_id)
    assert [t.direction for t in txns] == [TransactionDirection.IN, TransactionDirection.OUT]
    assert txns[-1].balance_after == Q("33.000")

    # Whole-book sweep: everything should reconcile cleanly.
    assert reconcile_all_batches(session) == []

    # Only the EP batch (33.000, ACTIVE) remains on hand.
    total = total_on_hand_stock(session)
    assert total == Q("33.000")
