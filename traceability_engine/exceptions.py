class TraceabilityError(Exception):
    """Base class for all domain errors raised by the engine."""


class QuantityReconciliationError(TraceabilityError):
    """SUM(inputs) != SUM(outputs) + shrinkage_qty + loss_qty (GENEALOGY.md §5).

    Confirmed 2026-09-14: there is no tolerance. This is a hard block, never
    a warning.
    """


class InvalidEventStructureError(TraceabilityError):
    """The input/output shape passed doesn't match the confirmed rules for
    this event_type (e.g. RECEIVING with inputs, DELIVERY with outputs,
    ADJUSTMENT routed through record_process_event instead of
    record_adjustment)."""


class InsufficientStockError(TraceabilityError):
    """An INPUT link would drive a batch's current_quantity below zero.

    This is an engineering safeguard implied by the ledger design (Stock is
    transaction/ledger based, PROJECT_STATUS/CLAUDE.md), not an invented
    business threshold -- it does not evaluate any QC/quantity acceptance
    rule, it only prevents the ledger from going negative.
    """


class UnauthorizedAdjustmentError(TraceabilityError):
    """Only a User with role PRODUCTION_MANAGER may create an ADJUSTMENT
    event (GENEALOGY.md §5.2, confirmed 2026-09-14)."""


class BatchNumberNotImplementedError(TraceabilityError):
    """Raised by batch_number.generate() -- see BATCH_NUMBER_SPEC.md: the
    AA (Jenis) segment is still [UNCONFIRMED], so the generator must not be
    implemented yet (PROJECT_STATUS.md blocker, carried over from Phase 3)."""


class StockReconciliationError(TraceabilityError):
    """A batch's `current_quantity` cache disagrees with the balance
    recomputed from its `StockTransaction` ledger (DATABASE_DESIGN.md §1:
    the ledger, not the cache, is the source of truth). Raised by
    services/stock.py's reconcile_batch() -- this should never happen in
    normal operation, since only record_process_event()/record_adjustment()
    are allowed to touch current_quantity, and both keep it in lockstep
    with the StockTransaction rows they write; a mismatch means something
    bypassed the ledger (13_STOCK.md: "do not manually overwrite current
    stock")."""


class EventDateOrderError(TraceabilityError):
    """Tanggal event lebih awal daripada event yang sudah tercatat pada batch
    yang sama (Fase 25, services/date_order.py). Blokir DEFAULT sejak Fase 25b
    (keputusan user 2026-09-19); entri riwayat memakai
    `strict_date_order=False`."""


class UnauthorizedDispositionError(TraceabilityError):
    """Hanya User dengan role PRODUCTION_MANAGER yang boleh memutuskan
    disposisi batch REJECTED (Fase 26, dikonfirmasi user 2026-09-19)."""
