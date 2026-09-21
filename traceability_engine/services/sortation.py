"""Fase 7 -- Sortation (SORT), including Downgrade/Upgrade re-sortation.

Wires the real SORT source fields (REQUIREMENTS.md field group) to the
generic engine's `record_process_event(event_type=SORTATION, ...)` as a
ONE->MANY event (one input batch, one new output batch per grade produced
-- GENEALOGY.md §3.1: "Sortation, including re-sortation that changes grade
(Downgrade/Upgrade) -- Yes, once per output grade produced -- ONE->MANY (or
1-output re-grade -- see note below)", tested in Fase 3
`tests/test_events.py::test_sortation_splits_into_multiple_outputs` and
`test_sortation_downgrade_is_single_output_reclassified_manually`, confirmed
2026-09-14 NOT a separate event type). Per 03_CORE_ENGINE.md/PROJECT_STATUS.md,
Fase 7 only needs this field-mapping layer -- genealogy/stock/reconciliation
side effects are already handled generically by `record_process_event()`
since Fase 3. There is no dedicated Sortation satellite table --
DATABASE_DESIGN.md §4 only reserves one for QualityTest (QC/MD), same
situation already noted for STEAM/SD in Fase 6 (steam_dry.py docstring).

Decisions made in this phase, and why (CLAUDE.md rule 11: document
ambiguity instead of guessing):

1. **Each grade with a supplied quantity > 0 becomes its own new output
   batch** (`OutputSpec(new_batch=...)`), never a self-loop reuse of the
   input `batch_id` -- unlike QC/MD/Steaming/Sundrying's self-loop
   (Fase 3/5/6), GENEALOGY.md §3.1 explicitly says Sortation mints a new
   batch "once per output grade produced". A caller that only fills in one
   of the five grade fields naturally produces the single-output
   Downgrade/Upgrade/re-grade shape (`test_sortation_downgrade_is_single_
   output_reclassified_manually`) -- there is no separate function for
   that case, only a different call shape of `record_sortation()`.

2. **Grade code mapping for new output batches**: `Gourmet=01, EG=02,
   EP=03, Powder=05` per the confirmed Grade Master table
   (`BATCH_NUMBER_SPEC.md` "Resolved by PT JAS"). **`NC` = "Non Conform"
   and is mapped to grade `04` ("Others") -- `[CONFIRMED BY USER --
   2026-09-14]`.** REQUIREMENTS.md's SORT field group lists "Gourmet, EG,
   EP, NC, Powder" as the five breakdown columns, occupying exactly the
   slot `Proses.docx`'s Grade Master table calls "Others" (`40`/`04`);
   the project owner (Tommy) confirmed directly that NC stands for Non
   Conform (reject-grade material). This is also consistent with
   REQUIREMENTS.md's separate `grind` field group ("source NC batch,
   starting qty, ... final powder qty") -- NC-graded Sortation output is
   exactly what Fase 9's Grinding later consumes.

3. **New output `batch_type` defaults to `PROCESSED` for Gourmet/EG/EP/NC
   and `POWDER` for the Powder grade** -- `[CONFIRMED BY USER --
   2026-09-14]`. Sortation's "Powder" grade (Grade Master `05`) is
   already-powder material sorted out directly (distinct from Fase 9
   Grinding, which turns NC beans into powder per
   REQUIREMENTS.md's `grind` field group), so it is typed as `POWDER`
   from the moment Sortation produces it; the other four grades are whole
   dried/green product, typed `PROCESSED` exactly like Fase 3's
   `test_sortation_splits_into_multiple_outputs`. A caller can override
   any grade's `batch_type` via `SortationInput` if a documented
   counter-example turns up.

4. **New output batches inherit `jenis_code`, `supplier_id`,
   `supplier_code`, and `receiving_date` from the input batch** --
   Sortation changes grade/process, not species, supplier, or original
   receipt date, so genealogy should carry those identifying fields
   forward rather than leaving them null (batch-number generation itself
   remains blocked on the AA/Jenis question, per `BATCH_NUMBER_SPEC.md`,
   independent of this decision).

5. **`process_code` defaults to `"00"` (Original) and must be passed
   explicitly as `"01"` (Upgrade) or `"02"` (Downgrade) for a re-grade
   call** -- `BATCH_NUMBER_SPEC.md`'s confirmed "Ongoing Grading" (PP)
   code table. This module never infers Upgrade vs Downgrade from
   quantities or grade codes; the caller (UI) states it explicitly, same
   spirit as Fase 5's QC `finding` staying a manual PIC judgment
   (qc_md.py docstring #1) -- CLAUDE.md rule 11 forbids guessing which
   direction a re-grade went.

9. **Fase 23: optional per-grade output batch numbers.** The real SORT
   sheet (`PROSES HIJAU 2026.xlsx`) records the batch number staff assign
   to each grade output. `SortationInput.<grade>_batch_number` stores it
   on the new `Batch` and, when it parses, its AA/BB/CC/date/PP components
   replace the inherited ones (#4) -- real example: green `040018-260505-00`
   sorted on 2026-06-18 yields EG `030218-260618-00` (AA `04`->`03`, date
   segment = sort date). Fase 32: PP 01/02 kini bisa dibuat otomatis
   (`auto_batch_number=True`, services/batch_numbering.py); PP 00 tetap
   ketikan staf, dan nomor ketikan selalu menang. The grade
   segment of a typed number is not cross-checked against its slot: the
   spec's own `030442-251113-02` shows staff labelling that differs from
   the BB code, and rule 11 forbids inventing a rejection rule.

6. **`shrinkage_qty` (and therefore SORT's own "final qty" field) is
   derived, not caller-supplied**: `shrinkage_qty = initial_qty -
   SUM(grade quantities)`, mirroring Fase 6 Sundrying's derived-shrinkage
   pattern (`steam_dry.py` docstring #2) and REQUIREMENTS.md's SORT field
   ordering (breakdown fields, then trailing "final qty, shrinkage" as
   summary fields). Accepting "final qty" as an independently-entered
   value risked silently disagreeing with the breakdown and breaking
   quantity reconciliation, which has no configured tolerance
   (`GENEALOGY.md` §5). The derived total is exposed as
   `record_sortation()`'s returned event's `shrinkage_qty` plus
   `sum(o.quantity for o in event outputs)` for the "final qty" figure --
   no separate field is invented for it. A resulting negative shrinkage
   (breakdown sum > initial_qty) is not blocked, for the same reason
   Sundrying's is not (steam_dry.py docstring #2) -- PROCESS_RULES.md
   still lists "shrinkage tolerances" as `[UNCONFIRMED]`.

7. **`initial_qty` defaults to the batch's on-hand quantity** when the
   caller doesn't supply one, exactly like every previous phase's
   `_resolve_quantity` pattern (qc_md.py #2, steam_dry.py #3).

8. **SORT's "end date" has no dedicated column** -- `ProcessEvent` has a
   single `event_date` (mapped here to SORT's "start date", consistent
   with every other phase using `event_date` for the activity date); "end
   date" is recorded in `notes` instead, the same treatment as Steaming's
   "end time" (Fase 6 #5).
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
from ..models import Batch, ProcessEvent
from .batch_numbering import derive_number, output_for_number
from .events import InputSpec, NewBatchSpec, OutputSpec, record_process_event

ZERO = Decimal("0")

# Grade Master codes (BATCH_NUMBER_SPEC.md "Resolved by PT JAS"). NC = "Non
# Conform" -> "04" -- confirmed by user 2026-09-14, see module docstring #2.
GRADE_GOURMET = "01"
GRADE_EG = "02"
GRADE_EP = "03"
GRADE_NC = "04"  # "Non Conform" -- confirmed by user 2026-09-14, see #2
GRADE_POWDER = "05"

# (attribute name on SortationInput, grade_code, default batch_type) --
# module docstring #2/#3.
_GRADE_SLOTS = (
    ("gourmet_qty", GRADE_GOURMET, BatchType.PROCESSED),
    ("eg_qty", GRADE_EG, BatchType.PROCESSED),
    ("ep_qty", GRADE_EP, BatchType.PROCESSED),
    ("nc_qty", GRADE_NC, BatchType.PROCESSED),
    ("powder_qty", GRADE_POWDER, BatchType.POWDER),
)


def _resolve_quantity(session: Session, batch_id: int, quantity: Optional[Decimal]) -> Decimal:
    if quantity is not None:
        return quantity
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"Batch {batch_id} does not exist.")
    return batch.current_quantity


@dataclass
class SortationInput:
    event_date: dt.date  # SORT "start date"
    pic_user_id: int
    batch_id: int
    initial_qty: Optional[Decimal] = None  # SORT "initial qty" -- default batch on-hand (#7)
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    end_date: Optional[dt.date] = None  # SORT "end date" -- notes only, see #8
    gourmet_qty: Optional[Decimal] = None
    eg_qty: Optional[Decimal] = None
    ep_qty: Optional[Decimal] = None
    nc_qty: Optional[Decimal] = None  # "Non Conform" -- see #2
    powder_qty: Optional[Decimal] = None
    process_code: str = "00"  # "00" Original / "01" Upgrade / "02" Downgrade -- see #5
    gourmet_batch_type: Optional[BatchType] = None  # override for #3, if ever needed
    eg_batch_type: Optional[BatchType] = None
    ep_batch_type: Optional[BatchType] = None
    nc_batch_type: Optional[BatchType] = None
    powder_batch_type: Optional[BatchType] = None
    # Fase 23 -- SORT sheet (PROSES HIJAU 2026.xlsx) lists the output batch
    # number per grade (BATCH-G / BATCH-EG / BATCH-EP) as staff-assigned
    # input, e.g. `040018-260505-00` -> EG `030218-260618-00`. See module
    # docstring #9.
    gourmet_batch_number: Optional[str] = None
    eg_batch_number: Optional[str] = None
    ep_batch_number: Optional[str] = None
    nc_batch_number: Optional[str] = None
    powder_batch_number: Optional[str] = None
    # Fase 32 -- nomor otomatis untuk hasil Upgrade/Downgrade (PP 01/02), lihat
    # services/batch_numbering.py. Nomor ketikan staf per grade tetap menang.
    # `jenis_code` (01/02) opsional: mengganti AA batch sumber (mis. AA lama).
    auto_batch_number: bool = False
    jenis_code: Optional[str] = None


def _sort_notes(data: SortationInput) -> Optional[str]:
    if data.end_date is None:
        return None
    return json.dumps({"end_date": data.end_date.isoformat()}, ensure_ascii=False)


_BATCH_NUMBER_ATTR = {
    "gourmet_qty": "gourmet_batch_number",
    "eg_qty": "eg_batch_number",
    "ep_qty": "ep_batch_number",
    "nc_qty": "nc_batch_number",
    "powder_qty": "powder_batch_number",
}


def _new_batch_spec(source: Batch, data: "SortationInput", attr: str, grade_code: str,
                    batch_type: BatchType) -> NewBatchSpec:
    """Build the NewBatchSpec for one grade output (module docstring #4/#9).

    Without a staff-supplied batch number the identifying fields are
    inherited from the source batch (unchanged pre-Fase 23 behavior). With
    one, a cleanly-parsing number is the source of truth for
    jenis/grade/supplier/date/process -- the real SORT sheet shows AA
    flipping `04` -> `03` and the date segment becoming the sort date, so
    inheriting from the source would contradict the number staff typed. An
    unparseable number is stored as free text with inherited components,
    same lenient rule as Receiving (receiving.py #1).
    """
    supplied = getattr(data, _BATCH_NUMBER_ATTR[attr])
    supplied = supplied.strip() if supplied else None
    parsed = None
    if supplied:
        try:
            parsed = batch_number_mod.parse(supplied)
        except ValueError:
            parsed = None
    return NewBatchSpec(
        batch_type=batch_type,
        batch_number=supplied or None,
        grade_code=parsed.grade_code if parsed else grade_code,
        jenis_code=parsed.jenis_code if parsed else source.jenis_code,
        supplier_id=source.supplier_id,
        supplier_code=parsed.supplier_code if parsed else source.supplier_code,
        receiving_date=parsed.receiving_date if parsed else source.receiving_date,
        process_code=parsed.process_code if parsed else data.process_code,
        unit=data.unit,
    )


_OVERRIDE_ATTR = {
    "gourmet_qty": "gourmet_batch_type",
    "eg_qty": "eg_batch_type",
    "ep_qty": "ep_batch_type",
    "nc_qty": "nc_batch_type",
    "powder_qty": "powder_batch_type",
}


def record_sortation(session: Session, data: SortationInput) -> ProcessEvent:
    """Record a Sortation event: ONE->MANY (`EventType.SORTATION`), one new
    output batch per grade with a supplied quantity > 0 (module docstring
    #1). Passing exactly one grade field produces the 1-output
    Downgrade/Upgrade/re-grade shape (`process_code="01"`/`"02"`) -- the
    same event type and function, per GENEALOGY.md §3.1.
    """
    source = session.get(Batch, data.batch_id)
    if source is None:
        raise ValueError(f"Batch {data.batch_id} does not exist.")

    initial_qty = _resolve_quantity(session, data.batch_id, data.initial_qty)
    if initial_qty <= 0:
        raise ValueError("Sortation initial quantity must be positive.")

    outputs: list[OutputSpec] = []
    for attr, grade_code, default_batch_type in _GRADE_SLOTS:
        qty = getattr(data, attr)
        if qty is None:
            continue
        if qty < 0:
            raise ValueError(f"Sortation grade quantity ({attr}) cannot be negative.")
        if qty == 0:
            continue
        batch_type = getattr(data, _OVERRIDE_ATTR[attr]) or default_batch_type
        spec = _new_batch_spec(source, data, attr, grade_code, batch_type)
        if data.auto_batch_number and not spec.batch_number:
            # Fase 32 -- PP 01/02 dibuat otomatis; nomor ketikan staf menang.
            number = derive_number(
                source, grade_code=grade_code, process_code=data.process_code,
                jenis_code=data.jenis_code,
            )
            outputs.append(output_for_number(
                session, number=number, quantity=qty, unit=data.unit, spec=spec,
                event_type=EventType.SORTATION, exclude_ids=[source.batch_id],
            ))
            continue
        outputs.append(OutputSpec(quantity=qty, unit=data.unit, new_batch=spec))

    if not outputs:
        raise ValueError(
            "Sortation requires at least one grade quantity "
            "(gourmet/eg/ep/nc/powder) greater than zero."
        )

    total_output = sum((o.quantity for o in outputs), ZERO)
    shrinkage_qty = initial_qty - total_output  # derived -- see module docstring #6

    return record_process_event(
        session,
        event_type=EventType.SORTATION,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=initial_qty, unit=data.unit)],
        outputs=outputs,
        shrinkage_qty=shrinkage_qty,
        notes=_sort_notes(data),
    )
