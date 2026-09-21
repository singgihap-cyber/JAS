"""Fase 10 -- Rework (REW / "Olah Ulang").

Wires the real REW source fields (REQUIREMENTS.md field group) to the
generic engine's `record_process_event(event_type=REWORK, ...)`. Per
03_CORE_ENGINE.md/PROJECT_STATUS.md, this phase only needs a field-mapping
layer -- genealogy/stock/reconciliation side effects are already handled
generically by `record_process_event()` since Fase 3. Rework is confirmed
capable of ONE->MANY, structurally identical to Sortation
(`GENEALOGY.md` §3.1: "Rework -- confirmed: can produce multiple
quality-grade outputs, structurally identical to Sortation ... Yes, one per
output produced (1..N) | ONE->MANY"), already tested in Fase 3
(`tests/test_events.py::test_rework_creates_new_batch` and
`test_rework_can_split_into_multiple_grade_outputs`). Proses code `"04"` for
Rework is fully confirmed (`BATCH_NUMBER_SPEC.md` "Resolved by PT JAS" --
`00=Original, 01=Upgrade, 02=Downgrade, 03=Mixing, 04=Rework`), unlike Fase
9's Grinding whose PP code was left `[UNCONFIRMED]`.

**Source data check (CLAUDE.md rule 11 -- document ambiguity instead of
guessing):** the `REW` sheet in `DATA PROSES VANILA 2026 REVISI.xlsx` was
read directly (openpyxl, 2026-09-16), same discipline as every prior phase.
Its real header (rows 5-6) is: `NO., TANGGAL, BATCH NUMBER, QTY AWAL (KG),
KETERANGAN PROSES, BATCH NUMBER SETELAH REWORK, QTY AKHIR (KG) [broken down
into GOURMET, EG, EP, NC, SUSUT], PIC` -- concrete evidence behind
REQUIREMENTS.md's "REW: date, batch, starting qty, process description, new
batch, Gourmet/EG/EP/NC outputs, shrinkage, PIC". As PROJECT_STATUS.md
anticipated, all 15 data rows on this sheet (rows 7-21) are completely
empty -- same situation as Fase 9's grind/MG/MDPW sheets. Decisions below
are therefore grounded in the real (but unfilled) form header +
REQUIREMENTS.md + established cross-phase patterns, not verified against a
real filled-in example -- flagged as provisional pending PT JAS's first real
Rework record, **except** where a detail is already independently confirmed
elsewhere (Proses code `"04"`, Grade Master codes, quantity-reconciliation
zero-tolerance) -- those are not provisional.

Decisions made in this phase, and why:

1. **Only four grade-breakdown slots: GOURMET, EG, EP, NC -- no Powder
   slot.** This is a real, confirmed difference from Sortation's five-slot
   `QTY AKHIR` breakdown (`sortation.py`'s `_GRADE_SLOTS`): the REW sheet's
   own merged header row 6 (`GOURMET, EG, EP, NC, SUSUT`) has no "Powder"
   column at all, unlike SORT's row 7 (`GOURMET, EG, EP, NC, POWDER`) --
   checked directly against both sheets' real headers, not inferred. This is
   consistent with Rework's purpose (re-working already-processed product
   back into sellable grades), whereas Powder is produced by the dedicated
   Grinding process (Fase 9) from NC/reject material specifically.
2. **Grade code mapping reuses the same confirmed Grade Master values as
   Sortation**: `Gourmet=01, EG=02, EP=03, NC=04` ("Non Conform" --
   `sortation.py` #2, `BATCH_NUMBER_SPEC.md` "Resolved by PT JAS"). No new
   mapping decision needed here.
3. **New output `batch_type` defaults to `PROCESSED` for all four grades**
   (there is no Powder slot to special-case, unlike Sortation's #3). A
   caller can override any grade's `batch_type` via `ReworkInput` for
   symmetry with `sortation.py`, if a documented counter-example turns up.
4. **New output batches inherit `jenis_code`, `supplier_id`,
   `supplier_code`, and `receiving_date` from the single input batch** --
   identical reasoning to Sortation's single-source inheritance
   (`sortation.py` #4) and Grinding's single-source inheritance
   (`powder.py` #6): Rework changes grade/condition, not species, supplier,
   or original receipt date, and there is exactly one source batch so
   inheritance is unambiguous.
5. **`process_code` on every output batch is hardcoded to `"04"` (Rework),
   not exposed as a caller parameter.** This differs from `sortation.py`
   (which exposes `process_code` because a single `SORTATION` event can
   represent Original/Upgrade/Downgrade depending on caller intent) --
   `EventType.REWORK` has no such ambiguity: any output of a Rework event
   is, by definition, the confirmed PP code `"04"`
   (`BATCH_NUMBER_SPEC.md`). Hardcoding avoids inventing a parameter with no
   evidenced use case, matching `powder.py`'s treatment of Grinding's fixed
   `grade_code="05"` (#5 there).
6. **`starting_qty` ("QTY AWAL (KG)") defaults to the source batch's on-hand
   quantity** when the caller doesn't supply one -- the majority
   single-source pattern already established (qc_md.py #2, steam_dry.py #3,
   sortation.py #7, powder.py #2). Nothing in the blank REW form or
   REQUIREMENTS.md suggests partial-batch Rework is a distinct real case.
7. **`SUSUT` (shrinkage) is derived, never caller-supplied**, exactly like
   `sortation.py` #6: `shrinkage_qty = starting_qty - SUM(grade
   quantities)`. The REW form *does* have an explicit `SUSUT` column (like
   SORT's own explicit `QTY SUSUT` column, both checked directly), but
   Sortation's precedent is to derive rather than trust an independently
   entered total, because accepting it risked silently disagreeing with the
   breakdown and breaking quantity reconciliation, which has zero
   configured tolerance (`GENEALOGY.md` §5). The same reasoning applies
   here without modification. A resulting negative shrinkage is not
   blocked, for the same reason as Sortation/Sundrying/Grinding
   (`PROCESS_RULES.md` still lists shrinkage tolerances as `[UNCONFIRMED]`).
8. **`KETERANGAN PROSES` ("process description") is recorded in
   `ProcessEvent.notes` as structured data** (`{"process_description":
   ...}`), the same treatment as Mixing's `DESKRIPSI PRODUK`
   (`mixing.py` #8) -- a free-text descriptive field with no dedicated
   schema column.
9. **(Fase 32) Nomor batch hasil bisa dibuat otomatis** dengan
   `ReworkInput.auto_batch_number=True` (`services/batch_numbering.py`,
   `claude/32_GENERATOR_TURUNAN.md`). Tanpa flag itu perilaku lama berlaku:
   **`BATCH NUMBER SETELAH REWORK` (the new batch number(s)) is not settable
   here.** Batch-number *generation* remains blocked on the AA/Jenis segment
   (`BATCH_NUMBER_SPEC.md`), same situation as every prior phase --
   `NewBatchSpec.batch_number` is left `None` and assigned later once the
   generator is implemented.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..enums import BatchType, EventType
from ..models import Batch, ProcessEvent
from .batch_numbering import derive_number, output_for_number
from .events import InputSpec, NewBatchSpec, OutputSpec, record_process_event
from .sortation import GRADE_EG, GRADE_EP, GRADE_GOURMET, GRADE_NC

ZERO = Decimal("0")

# Rework's Proses (PP) code is fully confirmed, unlike Grinding's -- see
# module docstring #5. Reused as a module-level constant for clarity at
# call sites / tests, mirroring sortation.py's GRADE_* constants.
PROCESS_CODE_REWORK = "04"

# (attribute name on ReworkInput, grade_code, default batch_type) -- module
# docstring #1/#2/#3. No Powder slot -- confirmed absent from the real REW
# form header, unlike sortation.py's five-slot _GRADE_SLOTS.
_GRADE_SLOTS = (
    ("gourmet_qty", GRADE_GOURMET, BatchType.PROCESSED),
    ("eg_qty", GRADE_EG, BatchType.PROCESSED),
    ("ep_qty", GRADE_EP, BatchType.PROCESSED),
    ("nc_qty", GRADE_NC, BatchType.PROCESSED),
)

_OVERRIDE_ATTR = {
    "gourmet_qty": "gourmet_batch_type",
    "eg_qty": "eg_batch_type",
    "ep_qty": "ep_batch_type",
    "nc_qty": "nc_batch_type",
}


def _resolve_quantity(session: Session, batch_id: int, quantity: Optional[Decimal]) -> Decimal:
    if quantity is not None:
        return quantity
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"Batch {batch_id} does not exist.")
    return batch.current_quantity


@dataclass
class ReworkInput:
    event_date: dt.date  # REW "TANGGAL"
    pic_user_id: int
    batch_id: int  # REW "BATCH NUMBER"
    starting_qty: Optional[Decimal] = None  # REW "QTY AWAL (KG)" -- default on-hand, see #6
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    process_description: Optional[str] = None  # REW "KETERANGAN PROSES" -- notes only, see #8
    gourmet_qty: Optional[Decimal] = None
    eg_qty: Optional[Decimal] = None
    ep_qty: Optional[Decimal] = None
    nc_qty: Optional[Decimal] = None  # "Non Conform" -- sortation.py #2
    gourmet_batch_type: Optional[BatchType] = None  # override for #3, if ever needed
    eg_batch_type: Optional[BatchType] = None
    ep_batch_type: Optional[BatchType] = None
    nc_batch_type: Optional[BatchType] = None
    # Fase 32 -- nomor otomatis PP=04 (services/batch_numbering.py).
    auto_batch_number: bool = False
    jenis_code: Optional[str] = None  # opsional: ganti AA batch sumber (01/02)


def _rework_notes(data: ReworkInput) -> Optional[str]:
    if data.process_description is None:
        return None
    return json.dumps({"process_description": data.process_description}, ensure_ascii=False)


def record_rework(session: Session, data: ReworkInput) -> ProcessEvent:
    """Record a Rework event: ONE->MANY (`EventType.REWORK`), one new output
    batch per grade with a supplied quantity > 0 (module docstring #1/#3),
    always tagged `process_code="04"` (see #5). Passing exactly one grade
    field naturally produces the ONE->NEW-BATCH single-output shape already
    tested in Fase 3 (`test_rework_creates_new_batch`).
    """
    source = session.get(Batch, data.batch_id)
    if source is None:
        raise ValueError(f"Batch {data.batch_id} does not exist.")

    starting_qty = _resolve_quantity(session, data.batch_id, data.starting_qty)
    if starting_qty <= 0:
        raise ValueError("Rework starting quantity must be positive.")

    outputs: list[OutputSpec] = []
    for attr, grade_code, default_batch_type in _GRADE_SLOTS:
        qty = getattr(data, attr)
        if qty is None:
            continue
        if qty < 0:
            raise ValueError(f"Rework grade quantity ({attr}) cannot be negative.")
        if qty == 0:
            continue
        batch_type = getattr(data, _OVERRIDE_ATTR[attr]) or default_batch_type
        spec = NewBatchSpec(
            batch_type=batch_type,
            grade_code=grade_code,
            jenis_code=source.jenis_code,  # inherited -- see #4
            supplier_id=source.supplier_id,
            supplier_code=source.supplier_code,
            receiving_date=source.receiving_date,
            process_code=PROCESS_CODE_REWORK,  # hardcoded -- see #5
            unit=data.unit,
        )
        if data.auto_batch_number:
            number = derive_number(
                source, grade_code=grade_code, process_code=PROCESS_CODE_REWORK,
                jenis_code=data.jenis_code,
            )
            outputs.append(output_for_number(
                session, number=number, quantity=qty, unit=data.unit, spec=spec,
                event_type=EventType.REWORK, exclude_ids=[source.batch_id],
            ))
        else:
            outputs.append(OutputSpec(quantity=qty, unit=data.unit, new_batch=spec))

    if not outputs:
        raise ValueError(
            "Rework requires at least one grade quantity (gourmet/eg/ep/nc) "
            "greater than zero."
        )

    total_output = sum((o.quantity for o in outputs), ZERO)
    shrinkage_qty = starting_qty - total_output  # derived -- see module docstring #7

    return record_process_event(
        session,
        event_type=EventType.REWORK,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=starting_qty, unit=data.unit)],
        outputs=outputs,
        shrinkage_qty=shrinkage_qty,
        notes=_rework_notes(data),
    )
