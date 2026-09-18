"""Stock Ledger (Fase 13) query/report endpoints -- thin wrapper over
`services.stock`. Every function in `services/stock.py` is read-only (module
docstring: "no function here ever assigns to Batch.current_quantity"), so
every endpoint here is a GET. No new aggregation or reconciliation rule is
added at this layer -- it only shapes what the service already computed for
the "Stock Monitoring" page (PROJECT_STATUS.md, Fase 15 slice 4: a report
page, not a schema-driven Input Proses entry, since Stock Ledger records no
new event of its own).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...models import Batch
from ...services.stock import (
    get_batch_balance,
    reconcile_all_batches,
    stock_summary,
    total_on_hand_stock,
    transaction_history,
)
from ..database import get_db
from ..schemas import BatchBalanceOut, StockSummaryRowOut, StockTotalOut, StockTransactionOut
from ..serializers import batch_balance_to_out, stock_summary_row_to_out, stock_transaction_to_out

router = APIRouter(tags=["stock"])


def _require_batch(db: Session, batch_id: int) -> None:
    if db.get(Batch, batch_id) is None:
        raise HTTPException(404, f"Batch {batch_id} not found")


@router.get("/stock/summary", response_model=list[StockSummaryRowOut])
def get_stock_summary(db: Session = Depends(get_db)):
    """On-hand stock grouped by supplier/jenis/grade -- ACTIVE batches only
    (stock.py module docstring, mirrors traceability-trial(1).html's "Stock
    Summary" page)."""
    rows = stock_summary(db)
    return [stock_summary_row_to_out(db, row) for row in rows]


@router.get("/stock/total", response_model=StockTotalOut)
def get_stock_total(db: Session = Depends(get_db)):
    """Company-wide on-hand total (ACTIVE batches only) -- the "Total Stock"
    dashboard figure."""
    return StockTotalOut(total_quantity=total_on_hand_stock(db))


@router.get("/stock/reconcile", response_model=list[BatchBalanceOut])
def get_stock_reconciliation(db: Session = Depends(get_db)):
    """Full-book reconciliation sweep: every batch where the cached
    `current_quantity` disagrees with its recomputed `StockTransaction`
    ledger balance. An empty list means the whole book is reconciled --
    the expected state in normal operation, since only
    record_process_event()/record_adjustment() are allowed to touch the
    cache and both keep it in lockstep (services/stock.py module
    docstring)."""
    mismatches = reconcile_all_batches(db)
    return [batch_balance_to_out(m) for m in mismatches]


@router.get("/batches/{batch_id}/stock", response_model=BatchBalanceOut)
def get_batch_stock(batch_id: int, db: Session = Depends(get_db)):
    """One batch's cached balance alongside its independently recomputed
    ledger balance -- never raises on a mismatch (that's `matches: false`
    in the response), only reports it."""
    _require_batch(db, batch_id)
    return batch_balance_to_out(get_batch_balance(db, batch_id))


@router.get("/batches/{batch_id}/transactions", response_model=list[StockTransactionOut])
def get_batch_transactions(batch_id: int, db: Session = Depends(get_db)):
    """Full StockTransaction ledger history for one batch, oldest first
    (transaction_id order, not created_at -- see stock.py's
    transaction_history() docstring)."""
    _require_batch(db, batch_id)
    return [stock_transaction_to_out(t) for t in transaction_history(db, batch_id)]
