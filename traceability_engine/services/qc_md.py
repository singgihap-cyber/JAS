"""Fase 5 -- Quality Testing (QC) and Metal Detection (MD).

Wires the real QT/MD source fields (REQUIREMENTS.md field groups) to the
generic engine's `record_process_event(event_type=QC_TEST/METAL_DETECTION,
...)` as a self-loop ONE->ONE event (same batch as input and output -- the
shape already established and tested in Fase 3, see
`tests/test_events.py::test_one_to_one_inspection_is_self_loop_and_stock_neutral`
and GENEALOGY.md §3.1), and additionally writes the `QualityTest` row that
DATABASE_DESIGN.md §4 already reserves for both event types. Per
03_CORE_ENGINE.md/PROJECT_STATUS.md, Fase 5 only needs this field-mapping +
`QualityTest` layer -- genealogy/stock side effects are already handled
generically by `record_process_event()` since Fase 3.

Decisions made in this phase, and why (CLAUDE.md rule 11: document
ambiguity instead of guessing):

1. **No numeric acceptance threshold is evaluated anywhere in this module.**
   Confirmed 2026-09-14 (GENEALOGY.md §5.1): KA1/KA2/KA3/AW/finding are
   recorded as measured/entered values only. Accept/reject remains a
   separate, explicit, manual call to `mark_batch_rejected()`
   (`services/adjustment.py`) -- never triggered from here.

2. **The self-loop `quantity` (the amount that flows through the
   INPUT/OUTPUT `EventBatchLink` rows) is not a QT/MD source field as
   such** -- QT lists `sample_weight` (a measured attribute, not a stock
   movement) and MD lists `quantity` directly, but neither source group
   states whether inspection covers the whole batch or a sub-lot. Fase 3's
   established self-loop pattern inspects the *entire* batch (input qty ==
   output qty == the batch's on-hand balance), so both `record_qc_test()`
   and `record_metal_detection()` default `quantity` to
   `batch.current_quantity` at call time when the caller doesn't supply
   one, while still allowing an explicit smaller `quantity` for a
   documented partial-batch inspection if a caller has one. Either way the
   event is stock-neutral (input == output on the same batch), so this
   choice cannot silently gain or lose stock.

3. **`sample_weight` is recorded on `QualityTest` only -- it never affects
   the stock ledger.** No source document states that pulling a test
   sample physically reduces the batch's recorded quantity, so inventing a
   deduction here would be inventing a business rule (CLAUDE.md).

4. **`stage` (RM/IP/FP) is a required caller input for both QC and MD.**
   `QualityTest.stage` is a NOT NULL column fixed in Phase 2
   (`DATABASE_DESIGN.md` §4) covering both event types (`METAL_DETECTION`
   qualifies as an inspection stage same as `QC_TEST`, per
   `CLAUDE.md`'s workflow listing QC and MD as adjacent stage-gated steps,
   e.g. "QC KA/AW1 -> MD1" / "QC KA/AW2 -> MD2"). REQUIREMENTS.md's MD
   field group does not name a dedicated stage field, so -- exactly like
   Fase 4's `batch_type` decision -- the caller (the specific MD1/MD2 form
   in the UI) supplies it explicitly rather than this module guessing from
   context.

5. **QT's `sample_received_date` (distinct from `test_date`) has no
   dedicated column** -- `ProcessEvent` has a single `event_date`, used
   here for `test_date` (the date the recorded activity actually
   happened, consistent with every other phase). `sample_received_date`
   is recorded as structured data in `ProcessEvent.notes`, matching the
   established pattern for non-generalizing per-process fields
   (DATABASE_DESIGN.md §2 `notes`; same treatment as Fase 4's
   packaging/coly/gross/tare fields).

6. **MD's `product_status` and `description` fields also have no dedicated
   column** and are not acted upon (per decision #1) -- both are recorded
   as structured data in `ProcessEvent.notes`, while MD's `finding` field
   maps to `QualityTest.metal_detection_finding` (the column
   `DATABASE_DESIGN.md` §4 reserves specifically for MD findings).
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..enums import EventType, QCStage
from ..models import Batch, ProcessEvent, QualityTest
from .events import InputSpec, OutputSpec, record_process_event


def _resolve_quantity(session: Session, batch_id: int, quantity: Optional[Decimal]) -> Decimal:
    if quantity is not None:
        return quantity
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"Batch {batch_id} does not exist.")
    return batch.current_quantity


@dataclass
class QCTestInput:
    event_date: dt.date  # QT "test date" -- see module docstring #5
    pic_user_id: int
    batch_id: int
    stage: QCStage  # RM/IP/FP, REQUIREMENTS.md QT field group
    quantity: Optional[Decimal] = None  # see module docstring #2; default = batch on-hand qty
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    sample_received_date: Optional[dt.date] = None
    sample_weight: Optional[Decimal] = None
    ka_1: Optional[Decimal] = None
    ka_2: Optional[Decimal] = None
    ka_3: Optional[Decimal] = None
    aw: Optional[Decimal] = None
    finding: Optional[str] = None  # manual PIC disposition -- never evaluated automatically


def _qc_notes(data: QCTestInput) -> Optional[str]:
    if data.sample_received_date is None:
        return None
    return json.dumps({"sample_received_date": data.sample_received_date.isoformat()})


def record_qc_test(session: Session, data: QCTestInput) -> ProcessEvent:
    """Record a QC (Quality Testing) event: self-loop ONE->ONE
    (`EventType.QC_TEST`) on the batch under test, plus its `QualityTest`
    row. No pass/fail rule is evaluated -- see module docstring #1.
    """
    quantity = _resolve_quantity(session, data.batch_id, data.quantity)
    if quantity <= 0:
        raise ValueError("QC test quantity must be positive.")

    event = record_process_event(
        session,
        event_type=EventType.QC_TEST,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        notes=_qc_notes(data),
    )

    session.add(
        QualityTest(
            event_id=event.event_id,
            batch_id=data.batch_id,
            stage=data.stage,
            sample_weight=data.sample_weight,
            ka_1=data.ka_1,
            ka_2=data.ka_2,
            ka_3=data.ka_3,
            aw=data.aw,
            finding=data.finding,
        )
    )
    session.flush()
    return event


@dataclass
class MetalDetectionInput:
    event_date: dt.date  # MD "date"
    pic_user_id: int
    batch_id: int
    stage: QCStage  # required by schema -- see module docstring #4
    quantity: Optional[Decimal] = None  # MD "quantity" field; default = batch on-hand qty (#2)
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    product_status: Optional[str] = None  # recorded only, see module docstring #6
    finding: Optional[str] = None  # -> QualityTest.metal_detection_finding
    description: Optional[str] = None  # recorded only, see module docstring #6


def _md_notes(data: MetalDetectionInput) -> Optional[str]:
    payload = {
        "product_status": data.product_status,
        "description": data.description,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return json.dumps(payload, ensure_ascii=False) if payload else None


def record_metal_detection(session: Session, data: MetalDetectionInput) -> ProcessEvent:
    """Record a Metal Detection event: self-loop ONE->ONE
    (`EventType.METAL_DETECTION`) on the batch under test, plus its
    `QualityTest` row (`metal_detection_finding` populated, KA/AW/sample
    fields left null -- DATABASE_DESIGN.md §4). No disposition is applied
    to `Batch.status` automatically -- see module docstring #1.
    """
    quantity = _resolve_quantity(session, data.batch_id, data.quantity)
    if quantity <= 0:
        raise ValueError("Metal detection quantity must be positive.")

    event = record_process_event(
        session,
        event_type=EventType.METAL_DETECTION,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        notes=_md_notes(data),
    )

    session.add(
        QualityTest(
            event_id=event.event_id,
            batch_id=data.batch_id,
            stage=data.stage,
            metal_detection_finding=data.finding,
        )
    )
    session.flush()
    return event
