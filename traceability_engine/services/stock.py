"""Fase 13 -- Stock Ledger (query/report layer over StockTransaction).

`13_STOCK.md`: "Implement transaction-based stock ledger and batch
balances. Do not manually overwrite current stock." The transaction-based
ledger *mechanism* itself is not new work in this phase -- `StockTransaction`
has been an automatic side effect of `record_process_event()`/
`record_adjustment()` since Fase 3 (every INPUT link -> DEBIT/OUT, every
OUTPUT link -> CREDIT/IN, `balance_after` maintained per row -- see
`services/events.py` module docstring, `GENEALOGY.md` §5.2). `PROJECT_STATUS.md`'s
own framing of this phase's likely scope agrees: not the recording
mechanism, but the QUERY/REPORT layer on top of it -- current stock balance
per batch/grade/supplier, transaction history, and reconciliation of the
ledger against the `Batch.current_quantity` cache. This module is exactly
that layer.

Per `13_STOCK.md`'s explicit instruction, this module is read-only: no
function here ever assigns to `Batch.current_quantity`. Three places in the
codebase are allowed to do that: `record_process_event()`
(services/events.py), `record_adjustment()` (services/adjustment.py), and,
since Fase 44, `cancel_event()` (services/event_correction.py) -- all three
already gated/validated (quantity reconciliation, insufficient-stock guard,
PRODUCTION_MANAGER-only + mandatory reason + AuditLog) and all three keep
`StockTransaction` in lockstep with the cache (Fase 44's reversal adds
compensating rows rather than editing/deleting existing ones). This module
adds no new way to change a balance, only ways to read and verify it.

## Why a reconciliation function, concretely

`DATABASE_DESIGN.md` §1 is explicit that `Batch.current_quantity` is "a
denormalized running balance for fast lookup ... a cache, not the source of
truth" -- the authoritative value is "the sum of `StockTransaction` rows for
this batch". Nothing before this phase actually verified the cache stays
correct; every prior phase simply relied on `record_process_event()`/
`record_adjustment()` maintaining it correctly on every write.
`reconcile_batch()`/`reconcile_all_batches()` close that gap: they
independently recompute each batch's balance from `StockTransaction` and
compare it to the cache, surfacing any drift instead of silently trusting
it. This is also the concrete, testable meaning of `13_STOCK.md`'s "do not
manually overwrite current stock" -- if something ever did bypass the two
gated write paths above, a reconciliation sweep is how it gets caught.

## Stock summary grouping

`traceability-trial(1).html`'s own "Stock Summary" page groups on-hand
stock by Supplier + Jenis + Grade, showing batch count and total on-spec
kg per group. `stock_summary()` mirrors that exact grouping
(`supplier_id`, `jenis_code`, `grade_code`) rather than inventing a
different one.

**Decision:** only `BatchStatus.ACTIVE` batches are counted as on-hand
stock. `GENEALOGY.md` §3.2 defines `ACTIVE` as "currently has on-hand
stock", in contrast to `CONSUMED`/`SUPERSEDED`/`SHIPPED` (all necessarily
zero or terminal) and `REJECTED` (quarantined -- explicitly not
sellable/available stock, even though a `REJECTED` batch's
`current_quantity` is not automatically zeroed by this engine, since
disposition of `REJECTED` batches is still `[UNCONFIRMED]`, `GENEALOGY.md`
§3.2/§8). This follows directly from `GENEALOGY.md`'s own status
definitions rather than inventing a new stock-eligibility rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import BatchStatus, TransactionDirection
from ..exceptions import StockReconciliationError
from ..models import Batch, StockTransaction

ZERO = Decimal("0")


def transaction_history(session: Session, batch_id: int) -> list[StockTransaction]:
    """Full ledger history for one batch, oldest first.

    Ordered by `transaction_id` (insertion order), not `created_at` --
    `StockTransaction` rows are append-only and several rows from the same
    event can share a timestamp (or, under SQLite in tests, even collide
    exactly), so only the surrogate key reliably reflects chronological
    order.
    """
    if session.get(Batch, batch_id) is None:
        raise ValueError(f"Batch {batch_id} does not exist.")
    return list(
        session.execute(
            select(StockTransaction)
            .where(StockTransaction.batch_id == batch_id)
            .order_by(StockTransaction.transaction_id)
        )
        .scalars()
        .all()
    )


def compute_ledger_balance(session: Session, batch_id: int) -> Decimal:
    """Recompute a batch's balance purely from its `StockTransaction` rows
    (IN - OUT), independent of the `Batch.current_quantity` cache -- the
    authoritative value per `DATABASE_DESIGN.md` §1.
    """
    balance = ZERO
    for txn in transaction_history(session, batch_id):
        if txn.direction == TransactionDirection.IN:
            balance += txn.quantity
        else:
            balance -= txn.quantity
    return balance


@dataclass
class BatchBalance:
    batch_id: int
    status: BatchStatus
    cached_quantity: Decimal  # Batch.current_quantity
    ledger_quantity: Decimal  # recomputed from StockTransaction
    matches: bool


def get_batch_balance(session: Session, batch_id: int) -> BatchBalance:
    """Report a batch's cached balance alongside its independently
    recomputed ledger balance, without raising on a mismatch (see
    `reconcile_batch()` for the fail-fast version).
    """
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"Batch {batch_id} does not exist.")
    ledger_quantity = compute_ledger_balance(session, batch_id)
    return BatchBalance(
        batch_id=batch_id,
        status=batch.status,
        cached_quantity=batch.current_quantity,
        ledger_quantity=ledger_quantity,
        matches=(batch.current_quantity == ledger_quantity),
    )


def reconcile_batch(session: Session, batch_id: int) -> BatchBalance:
    """Like `get_batch_balance()`, but raises `StockReconciliationError` if
    the cache and the ledger disagree, instead of only reporting it.
    """
    result = get_batch_balance(session, batch_id)
    if not result.matches:
        raise StockReconciliationError(
            f"Batch {batch_id}: cached current_quantity={result.cached_quantity} "
            f"!= ledger balance={result.ledger_quantity}."
        )
    return result


def reconcile_all_batches(session: Session) -> list[BatchBalance]:
    """Sweep every batch in the database and return only the ones where the
    cache and the ledger disagree (an empty list means the whole book is
    reconciled). Deliberately never raises -- a full-book audit reports
    every discrepancy at once rather than stopping at the first one;
    `reconcile_batch()` is the per-batch, fail-fast version.
    """
    batch_ids = session.execute(select(Batch.batch_id)).scalars().all()
    mismatches = []
    for batch_id in batch_ids:
        result = get_batch_balance(session, batch_id)
        if not result.matches:
            mismatches.append(result)
    return mismatches


@dataclass
class StockSummaryRow:
    supplier_id: Optional[int]
    jenis_code: Optional[str]
    grade_code: Optional[str]
    batch_count: int
    total_quantity: Decimal


def stock_summary(session: Session) -> list[StockSummaryRow]:
    """On-hand stock grouped by supplier/jenis/grade -- mirrors
    `traceability-trial(1).html`'s "Stock Summary" page. Only `ACTIVE`
    batches count as on-hand -- see module docstring.
    """
    batches = (
        session.execute(select(Batch).where(Batch.status == BatchStatus.ACTIVE))
        .scalars()
        .all()
    )

    groups: dict[tuple, StockSummaryRow] = {}
    for batch in batches:
        key = (batch.supplier_id, batch.jenis_code, batch.grade_code)
        row = groups.get(key)
        if row is None:
            row = StockSummaryRow(
                supplier_id=batch.supplier_id,
                jenis_code=batch.jenis_code,
                grade_code=batch.grade_code,
                batch_count=0,
                total_quantity=ZERO,
            )
            groups[key] = row
        row.batch_count += 1
        row.total_quantity += batch.current_quantity

    return sorted(
        groups.values(),
        key=lambda r: (r.supplier_id or 0, r.jenis_code or "", r.grade_code or ""),
    )


def total_on_hand_stock(session: Session) -> Decimal:
    """Company-wide on-hand total (`ACTIVE` batches only) -- the "Total
    Stock" dashboard figure in `traceability-trial(1).html`.
    """
    quantities = (
        session.execute(select(Batch.current_quantity).where(Batch.status == BatchStatus.ACTIVE))
        .scalars()
        .all()
    )
    return sum(quantities, ZERO)
