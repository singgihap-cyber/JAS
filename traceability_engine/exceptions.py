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
