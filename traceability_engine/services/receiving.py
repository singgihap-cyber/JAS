"""Fase 4 -- Receiving / PB (Penerimaan Barang).

Wires the real PB source fields (REQUIREMENTS.md "PB" field group: date,
batch, supplier, product description, packaging condition, coly, gross,
tare, net, on-spec, off-spec, smell test, PIC) to the generic engine's
`record_process_event(event_type=RECEIVING, ...)` -- per
03_CORE_ENGINE.md/PROJECT_STATUS.md, Fase 4+ should call the existing
engine primitive with process-specific parameters, not re-implement
genealogy/reconciliation/stock.

Decisions made in this phase, and why (CLAUDE.md rule 11: document
ambiguity instead of guessing):

1. **`batch_number` is external/manual input, or left null.**
   `batch_number.generate()` intentionally raises (AA/Jenis segment still
   [UNCONFIRMED], BATCH_NUMBER_SPEC.md) -- this is the carried-over Fase 3
   blocker named in PROJECT_STATUS.md as the thing Fase 4 must decide.
   `record_receiving()` accepts a `batch_number` staff key in by hand; if
   it parses cleanly (`batch_number.parse()`, historical 2-digit or new
   3-digit supplier width) the components are stored on the `Batch` row
   too, purely for later query/reporting convenience -- parsing failure is
   not an error, since staff may not always type the canonical format and
   inventing a stricter validation rule than PT JAS has confirmed is out of
   scope (CLAUDE.md rule: do not invent business rules).

2. **`batch_type` (RAW_KERING vs RAW_HIJAU) is a required caller input, not
   inferred.** CLAUDE.md's two known workflows (Kering/Hijau) start from
   different PB forms; REQUIREMENTS.md's PB field group is generic across
   both and does not name a field that distinguishes them. Guessing from
   `product_description` text would be inventing a business rule
   (PROCESS_RULES.md already lists "allowed grade transitions" etc. as
   [UNCONFIRMED] in the same spirit) -- so the caller/UI (which form the
   user opened) supplies it explicitly.

3. **Net weight is the stock-IN quantity.** The `net` PB field becomes
   `OutputSpec.quantity` (-> `Batch.current_quantity`), consistent with
   "gross/tare/net" being a standard weighing record and net being the
   actual receivable material.

4. **On-spec/off-spec quantities and the smell test result are recorded,
   not acted on.** Whether an off-spec quantity should be split into a
   second (e.g. REJECTED) batch at receiving time is not confirmed by any
   source document (PROCESS_RULES.md lists "metal detection disposition"
   and similar dispositions as open, and QC/MD have no numeric acceptance
   threshold per GENEALOGY.md §5.1 -- the same "manual PIC judgment, not an
   automated rule" principle is applied here). This phase therefore records
   `on_spec_qty`/`off_spec_qty`/`smell_test` as structured data on
   `ProcessEvent.notes` (matching the established pattern for
   non-generalizing per-process fields, DATABASE_DESIGN.md §2 `notes`) and
   creates exactly one Batch for the full `net` quantity. A manual
   `mark_batch_rejected()` call (services/adjustment.py) remains available
   if a PIC decides the whole batch should be quarantined.

5. **Packaging condition / coly / gross / tare are also recorded on
   `ProcessEvent.notes`.** `Batch.gross_weight/tare_weight/net_weight` are
   reserved for `batch_type=PACKAGED` per DATABASE_DESIGN.md §1 -- reusing
   them for the *incoming raw material* weighing record would conflate two
   different physical measurements on one column set, so Receiving's own
   weighing detail stays in the event record instead, exactly like STEAM's
   pan/water condition or SORT's shrinkage breakdown already do.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from .. import batch_number as batch_number_mod
from ..enums import BatchType, EventType
from ..models import ProcessEvent
from .events import NewBatchSpec, OutputSpec, record_process_event


@dataclass
class ReceivingInput:
    event_date: dt.date
    pic_user_id: int
    supplier_id: int
    batch_type: BatchType  # RAW_KERING or RAW_HIJAU -- caller-supplied, see module docstring #2
    net_quantity: Decimal  # PB "net" field -- becomes stock IN
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    batch_number: Optional[str] = None  # manual/external input, see module docstring #1
    product_description: Optional[str] = None
    packaging_condition: Optional[str] = None
    coly: Optional[int] = None
    gross_weight: Optional[Decimal] = None
    tare_weight: Optional[Decimal] = None
    on_spec_qty: Optional[Decimal] = None
    off_spec_qty: Optional[Decimal] = None
    smell_test: Optional[str] = None


def _build_notes(data: ReceivingInput) -> str:
    payload = {
        "product_description": data.product_description,
        "packaging_condition": data.packaging_condition,
        "coly": data.coly,
        "gross_weight": str(data.gross_weight) if data.gross_weight is not None else None,
        "tare_weight": str(data.tare_weight) if data.tare_weight is not None else None,
        "net_weight": str(data.net_quantity),
        "on_spec_qty": str(data.on_spec_qty) if data.on_spec_qty is not None else None,
        "off_spec_qty": str(data.off_spec_qty) if data.off_spec_qty is not None else None,
        "smell_test": data.smell_test,
    }
    return json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False)


def record_receiving(session: Session, data: ReceivingInput) -> ProcessEvent:
    """Record a PB Receiving event: creates one new Batch (RECEIVING has no
    inputs, NO_INPUT_EVENT_TYPES) for the full net quantity, and a matching
    stock-IN StockTransaction, via the generic engine.
    """
    if data.net_quantity <= 0:
        raise ValueError("net_quantity (PB 'net' field) must be positive.")

    parsed = None
    if data.batch_number:
        try:
            parsed = batch_number_mod.parse(data.batch_number)
        except ValueError:
            parsed = None  # accept as free-form manual entry -- see module docstring #1

    new_batch = NewBatchSpec(
        batch_type=data.batch_type,
        batch_number=data.batch_number,
        supplier_id=data.supplier_id,
        unit=data.unit,
        jenis_code=parsed.jenis_code if parsed else None,
        grade_code=parsed.grade_code if parsed else None,
        supplier_code=parsed.supplier_code if parsed else None,
        receiving_date=parsed.receiving_date if parsed else data.event_date,
        process_code=parsed.process_code if parsed else None,
        created_by=data.pic_user_id,
    )

    return record_process_event(
        session,
        event_type=EventType.RECEIVING,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[],
        outputs=[OutputSpec(quantity=data.net_quantity, unit=data.unit, new_batch=new_batch)],
        notes=_build_notes(data),
    )
