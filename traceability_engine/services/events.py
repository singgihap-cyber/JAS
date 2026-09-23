"""Generic process-event recording engine.

One function, `record_process_event()`, handles every process type in
REQUIREMENTS.md (Receiving, QC, MD, Steaming, Sundrying, Sortation, Mixing,
Vacuum, Packing, Grinding, Magnetization, MD-Powder, Rework, Delivery,
Sample Delivery) because GENEALOGY.md §2 collapses ONE-ONE / ONE-MANY /
MANY-ONE / ONE-NEW-BATCH into one event/link structure with different
input/output counts -- there is no per-process schema or code path.

ADJUSTMENT is deliberately NOT handled here -- see services/adjustment.py
and GENEALOGY.md §5.2 ("not part of the genealogy graph proper").

Stock-ledger side effect (documented implementation decision, carried over
from Phase 3, see PROJECT_STATUS.md "Temuan" / GENEALOGY.md §5.2): every
INPUT link produces a stock DEBIT (here: StockTransaction direction=OUT)
and every OUTPUT link produces a stock CREDIT (direction=IN), applied
uniformly including to self-loop inspection events (QC/MD) -- so a pure
inspection with no quantity change nets to zero stock effect, and a
self-loop event with shrinkage (e.g. Sundrying) reduces stock by exactly
the shrinkage/loss amount. If PT JAS says this reading of GENEALOGY.md §5.2
is wrong, this is the one place to revisit.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..enums import (
    BatchStatus,
    BatchType,
    EventStatus,
    EventType,
    LinkRole,
    NO_INPUT_EVENT_TYPES,
    NO_OUTPUT_EVENT_TYPES,
    NOT_A_GENEALOGY_EVENT,
    TransactionDirection,
)
from ..exceptions import InsufficientStockError, InvalidEventStructureError, QuantityReconciliationError
from ..models import Batch, EventBatchLink, ProcessEvent, StockTransaction

ZERO = Decimal("0")


@dataclass
class InputSpec:
    batch_id: int
    quantity: Decimal
    unit: Optional[str] = None


@dataclass
class NewBatchSpec:
    batch_type: BatchType
    batch_number: Optional[str] = None
    supplier_id: Optional[int] = None
    unit: str = "kg"
    jenis_code: Optional[str] = None
    grade_code: Optional[str] = None
    supplier_code: Optional[str] = None
    receiving_date: Optional[dt.date] = None
    process_code: Optional[str] = None
    created_by: Optional[int] = None
    # Packaging-only (batch_type == PACKAGED)
    plastic_size: Optional[str] = None
    plastic_lot: Optional[str] = None
    plastic_qty: Optional[Decimal] = None
    carton_lot: Optional[str] = None
    gross_weight: Optional[Decimal] = None
    tare_weight: Optional[Decimal] = None
    net_weight: Optional[Decimal] = None


@dataclass
class OutputSpec:
    quantity: Decimal
    unit: Optional[str] = None
    batch_id: Optional[int] = None  # reuse an existing batch (self-loop, GENEALOGY.md §3.1)
    new_batch: Optional[NewBatchSpec] = None

    def __post_init__(self) -> None:
        if (self.batch_id is None) == (self.new_batch is None):
            raise InvalidEventStructureError(
                "OutputSpec requires exactly one of batch_id (reuse existing "
                "batch, self-loop) or new_batch (mint a new batch)."
            )


def _validate_structure(
    event_type: EventType, inputs: list[InputSpec], outputs: list[OutputSpec]
) -> None:
    if event_type in NOT_A_GENEALOGY_EVENT:
        raise InvalidEventStructureError(
            f"{event_type.value} must be recorded via record_adjustment(), "
            "not record_process_event() -- see GENEALOGY.md §5.2."
        )

    if event_type in NO_INPUT_EVENT_TYPES:
        if inputs:
            raise InvalidEventStructureError(f"{event_type.value} must have no inputs.")
        # Fase 31: RECEIVING boleh menambah stok ke batch yang ada (batch_id)
        # -- penerimaan kedua bernomor sama di hari yang sama = satu batch.
        if len(outputs) != 1:
            raise InvalidEventStructureError(
                f"{event_type.value} must produce exactly one new batch."
            )
    elif event_type in NO_OUTPUT_EVENT_TYPES:
        if outputs:
            raise InvalidEventStructureError(f"{event_type.value} must have no outputs.")
        if not inputs:
            raise InvalidEventStructureError(f"{event_type.value} requires at least one input.")
    else:
        if not inputs or not outputs:
            raise InvalidEventStructureError(
                f"{event_type.value} requires at least one input and one output."
            )


def _check_reconciliation(
    event_type: EventType,
    inputs: list[InputSpec],
    outputs: list[OutputSpec],
    shrinkage_qty: Decimal,
    loss_qty: Decimal,
) -> None:
    if event_type in NO_INPUT_EVENT_TYPES or event_type in NO_OUTPUT_EVENT_TYPES:
        return  # exempt by construction -- GENEALOGY.md §5

    sum_in = sum((i.quantity for i in inputs), ZERO)
    sum_out = sum((o.quantity for o in outputs), ZERO)
    expected_in = sum_out + shrinkage_qty + loss_qty
    if sum_in != expected_in:
        raise QuantityReconciliationError(
            f"{event_type.value}: SUM(inputs)={sum_in} != SUM(outputs)={sum_out} "
            f"+ shrinkage={shrinkage_qty} + loss={loss_qty} (expected {expected_in}). "
            "No tolerance is configured -- confirmed 2026-09-14 (GENEALOGY.md §5)."
        )


def record_process_event(
    session: Session,
    *,
    event_type: EventType,
    event_date: dt.date,
    pic_user_id: int,
    inputs: list[InputSpec],
    outputs: list[OutputSpec],
    event_time: Optional[dt.time] = None,
    shrinkage_qty: Decimal = ZERO,
    loss_qty: Decimal = ZERO,
    notes: Optional[str] = None,
    end_date: Optional[dt.date] = None,  # Fase 48 -- lihat models.py ProcessEvent.end_date
    created_by: Optional[int] = None,
    strict_date_order: bool = True,
) -> ProcessEvent:
    """Record any non-ADJUSTMENT process event and its stock-ledger side
    effects. Raises InvalidEventStructureError / QuantityReconciliationError
    / InsufficientStockError (and, unless strict_date_order=False,
    EventDateOrderError) on violation; nothing is persisted (session is
    not committed here -- caller controls the transaction boundary) beyond
    what SQLAlchemy has already flushed for FK resolution.
    """
    _validate_structure(event_type, inputs, outputs)
    _check_reconciliation(event_type, inputs, outputs, shrinkage_qty, loss_qty)

    if strict_date_order:  # Fase 25b: default True -- lihat services/date_order.py
        from .date_order import enforce_event_date_order

        touched_ids = {i.batch_id for i in inputs} | {
            o.batch_id for o in outputs if o.batch_id is not None
        }
        enforce_event_date_order(session, event_date, touched_ids)

    event = ProcessEvent(
        event_type=event_type,
        event_date=event_date,
        event_time=event_time,
        pic_user_id=pic_user_id,
        shrinkage_qty=shrinkage_qty,
        loss_qty=loss_qty,
        notes=notes,
        end_date=end_date,
        status=EventStatus.COMPLETED,
        created_by=created_by if created_by is not None else pic_user_id,
    )
    session.add(event)
    session.flush()  # assign event.event_id

    touched: dict[int, Batch] = {}

    for inp in inputs:
        batch = session.get(Batch, inp.batch_id)
        if batch is None:
            raise InvalidEventStructureError(f"Input batch_id={inp.batch_id} does not exist.")
        if batch.current_quantity < inp.quantity:
            raise InsufficientStockError(
                f"Batch {batch.batch_id} has {batch.current_quantity} on hand, "
                f"cannot consume {inp.quantity}."
            )
        session.add(
            EventBatchLink(
                event_id=event.event_id,
                batch_id=batch.batch_id,
                role=LinkRole.INPUT,
                quantity=inp.quantity,
                unit=inp.unit or batch.unit,
            )
        )
        new_balance = batch.current_quantity - inp.quantity
        batch.current_quantity = new_balance
        session.add(
            StockTransaction(
                batch_id=batch.batch_id,
                event_id=event.event_id,
                direction=TransactionDirection.OUT,
                quantity=inp.quantity,
                balance_after=new_balance,
                is_sample=(event_type == EventType.SAMPLE_DELIVERY),
            )
        )
        touched[batch.batch_id] = batch

    for out in outputs:
        if out.new_batch is not None:
            nb = out.new_batch
            batch = Batch(
                batch_number=nb.batch_number,
                jenis_code=nb.jenis_code,
                grade_code=nb.grade_code,
                supplier_code=nb.supplier_code,
                receiving_date=nb.receiving_date,
                process_code=nb.process_code,
                batch_type=nb.batch_type,
                supplier_id=nb.supplier_id,
                status=BatchStatus.ACTIVE,
                current_quantity=ZERO,
                unit=nb.unit,
                created_from_event_id=event.event_id,
                created_by=nb.created_by if nb.created_by is not None else pic_user_id,
                plastic_size=nb.plastic_size,
                plastic_lot=nb.plastic_lot,
                plastic_qty=nb.plastic_qty,
                carton_lot=nb.carton_lot,
                gross_weight=nb.gross_weight,
                tare_weight=nb.tare_weight,
                net_weight=nb.net_weight,
            )
            session.add(batch)
            session.flush()  # assign batch.batch_id
        else:
            batch = session.get(Batch, out.batch_id)
            if batch is None:
                raise InvalidEventStructureError(
                    f"Output batch_id={out.batch_id} does not exist."
                )

        session.add(
            EventBatchLink(
                event_id=event.event_id,
                batch_id=batch.batch_id,
                role=LinkRole.OUTPUT,
                quantity=out.quantity,
                unit=out.unit or batch.unit,
            )
        )
        new_balance = batch.current_quantity + out.quantity
        batch.current_quantity = new_balance
        session.add(
            StockTransaction(
                batch_id=batch.batch_id,
                event_id=event.event_id,
                direction=TransactionDirection.IN,
                quantity=out.quantity,
                balance_after=new_balance,
            )
        )
        touched[batch.batch_id] = batch

    # A batch fully consumed by this event (and not itself replenished as an
    # output of the same event) is marked CONSUMED. REJECTED/SUPERSEDED are
    # deliberately never set here -- those are manual PIC/Sortation
    # decisions, see mark_batch_rejected() (GENEALOGY.md §3.2).
    for batch in touched.values():
        if batch.status == BatchStatus.ACTIVE and batch.current_quantity == ZERO:
            batch.status = BatchStatus.CONSUMED

    session.flush()
    return event
