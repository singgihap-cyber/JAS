"""Fase 9 -- Pengolahan Bubuk (Powder): Grinding & Sieving (grind),
Magnetization (MG), and Metal Detection Powder (MDPW).

Wires the real grind/MG/MDPW source fields (REQUIREMENTS.md field groups) to
the generic engine's `record_process_event()`. Per 03_CORE_ENGINE.md/
PROJECT_STATUS.md, this phase only needs a field-mapping layer --
genealogy/stock/reconciliation side effects are already handled generically
by `record_process_event()` since Fase 3.

**Source data caveat (CLAUDE.md rule 11 -- document ambiguity instead of
guessing):** unlike Fase 8's MIX sheet, the `grind`, `MG`, and `MDPW` sheets
in `DATA PROSES VANILA 2026 REVISI.xlsx` were checked directly (openpyxl,
2026-09-16) and contain ONLY their header rows -- every data row (rows 5+ on
all three sheets) is completely empty. `TRACEABILITY result.xlsx` /
`TRACEABILITY result 11.xlsx` also contain no grind/MG/MDPW-equivalent rows
(their "Powder" columns are Sortation output breakdowns, already handled in
Fase 7 -- `sortation.py`). So, unlike Mixing's "CP qty = SUM(sources)" or
Sortation's "shrinkage = initial - breakdown" derivations, which were each
verified against real recorded values, every decision below is grounded in
REQUIREMENTS.md's field group + the blank form's own column headers +
established patterns from Fase 3/5/6/7/8, not in a real filled-in example.
Anything below that is not already independently confirmed elsewhere (Grade
Master `05`=Powder, `BatchType.POWDER`, `EventType.GRINDING` as
ONE->NEW-BATCH -- all confirmed prior to this phase) should be treated as
provisional pending PT JAS's first real Grinding/MG/MDPW record.

## Grinding & Sieving (`grind`)

Real form header (`grind` sheet, row 4): `NO., TANGGAL, BATCH NUMBER ASAL,
QTY AWAL NC (KG), TANGGAL HASIL, BATCH NUMBER BARU, QTY AKHIR POWDER (KG),
PIC` -- concrete evidence behind REQUIREMENTS.md's "grind: source NC batch,
starting qty, result date, new batch, final powder qty, PIC". ONE->NEW-BATCH
(`EventType.GRINDING`), already established and tested in Fase 3
(`tests/test_events.py::test_grinding_creates_new_powder_batch`,
`GENEALOGY.md` §3.1 "Grinding (NC -> powder) | Yes | ONE->NEW-BATCH").

1. **Two distinct dates are recorded (`TANGGAL` and `TANGGAL HASIL`)**,
   unlike every other phase so far except Fase 5's QT. `ProcessEvent` has a
   single `event_date`. Following Fase 5's identical two-date situation
   (QT's `sample_received_date` vs `test_date` -- qc_md.py docstring #5),
   `event_date` maps to `TANGGAL` (when the NC batch was fed into grinding
   -- the activity start, consistent with every other phase using
   `event_date` for the start/primary date), and `TANGGAL HASIL` (the
   result/output date) is recorded in `notes` as structured data.
2. **`QTY AWAL NC (KG)` ("starting qty") defaults to the source batch's
   on-hand quantity** when the caller doesn't supply one -- the majority
   pattern already established for a single-source event (qc_md.py #2,
   steam_dry.py #3, sortation.py #7); nothing in the blank form or
   REQUIREMENTS.md suggests partial-batch grinding is a real case the way
   Mixing's per-source `QTY ASAL (KG)` was proven to be (mixing.py #10), so
   there is no evidence to justify deviating from the majority default.
3. **`QTY AKHIR POWDER (KG)` ("final powder qty") is required, no
   default** -- same reasoning as every other phase's terminal weighed
   output (Sundrying's `final_quantity`, Mixing's `final_qty`): there is no
   sensible default for an independently measured post-grinding weight.
4. **There is no `SUSUT`/shrinkage column on the `grind` form at all** (only
   starting and final quantities) -- `shrinkage_qty` is therefore derived as
   `starting_qty - final_qty`, mirroring Sundrying/Sortation/Mixing's
   derived-shrinkage pattern (steam_dry.py #2, sortation.py #6, mixing.py
   #2) and keeping quantity reconciliation exact by construction. A
   resulting negative shrinkage is not blocked, for the same reason as
   those phases (`PROCESS_RULES.md` still lists shrinkage tolerances as
   `[UNCONFIRMED]`).
5. **The new output batch is always `batch_type=POWDER`, `grade_code="05"`
   (Grade Master Powder, `BATCH_NUMBER_SPEC.md` "Resolved by PT JAS" --
   already confirmed, not inferred here)** -- this is the entire point of
   the Grinding process (turning NC/reject material into sellable powder),
   already established as the expected shape in Fase 3's
   `test_grinding_creates_new_powder_batch`. Neither is exposed as a
   caller-overridable parameter, unlike Mixing/Sortation's batch_type
   defaults, because no source evidence suggests Grinding ever produces
   anything other than Powder.
6. **`jenis_code`, `supplier_id`, `supplier_code`, and `receiving_date` are
   inherited from the single source (NC) batch** -- Grinding has exactly
   one source batch (unlike Mixing's multi-source case where inheriting was
   shown to be genuinely ambiguous, mixing.py #4), so the same reasoning
   that justified Sortation's single-source inheritance applies equally
   here (sortation.py #4: "Sortation changes grade/process, not species,
   supplier, or original receipt date") -- Grinding changes form (whole NC
   beans -> powder), not species or origin.
7. **`process_code` has no confirmed value for Grinding.**
   `BATCH_NUMBER_SPEC.md`'s "Ongoing Grading" (PP) scheme only defines
   `00=Original, 01=Upgrade, 02=Downgrade, 03=Mixing, 04=Rework` -- none of
   which is "Grinding", and the `grind` sheet has no real `BATCH NUMBER
   BARU` example to reverse-engineer a PP value from. Left as an explicit
   optional caller parameter, default `None` -- **`[UNCONFIRMED]`**,
   flagged in PROJECT_STATUS.md for PT JAS to confirm (does a Powder batch
   minted by Grinding get `PP="00"` as a fresh/"Original" product line, or
   does PT JAS use a PP value not yet in the confirmed 5-code table?).
8. **The single input batch is expected to be an NC (reject-grade) batch**
   per REQUIREMENTS.md's "source NC batch" wording, but this module does
   not enforce `Batch.grade_code == "04"` (Sortation's confirmed NC code,
   sortation.py #2) at this layer -- nothing in the source documents states
   Grinding is hard-blocked from ever processing a non-NC batch (e.g. a
   directly-received off-spec lot that skipped Sortation). Enforcing it
   here would be inventing a validation rule CLAUDE.md rule 11 forbids;
   left to the caller/UI to route correctly.

## Magnetization (`MG`) and Metal Detection Powder (`MDPW`)

Real form headers (row 4 on both sheets): `NO., TANGGAL, BATCH NUMBER, QTY
(KG), TEMUAN, KETERANGAN, PIC` -- concrete evidence behind REQUIREMENTS.md's
"MG/MDPW: date, batch, qty, finding, notes, PIC" (a single shared field
group for both). Both are self-loop ONE->ONE inspection events on a Powder
batch (`EventType.MAGNETIZATION` / `EventType.MD_POWDER`), the same shape
already established for QC/MD in Fase 5
(`tests/test_events.py::test_one_to_one_inspection_is_self_loop_and_stock_neutral`,
`GENEALOGY.md` §3.1).

9. **Neither MG nor MDPW writes to the `QualityTest` satellite table.**
   `DATABASE_DESIGN.md` §4 fixes `QualityTest.stage` (RM/IP/FP) as a NOT
   NULL column, and Fase 5 read that column as covering both `QC_TEST` and
   `METAL_DETECTION` (qc_md.py docstring #4) because both those event
   types' REQUIREMENTS.md field groups sit inside the RM/IP/FP-staged
   inspection flow (`CLAUDE.md`'s "QC KA/AW1 -> MD1"/"QC KA/AW2 -> MD2"
   workflow). Neither the `MG` nor `MDPW` field group names a stage at all,
   and `DATABASE_DESIGN.md` §4's own description only names `QC_TEST`/
   `METAL_DETECTION` explicitly. Forcing an arbitrary `stage` value (e.g.
   always `"FP"`) to satisfy the NOT NULL constraint would be inventing
   data CLAUDE.md rule 11 forbids. Instead, both `finding` (`TEMUAN`) and
   `notes`/`KETERANGAN` are recorded in `ProcessEvent.notes` as structured
   data -- the same "no dedicated satellite table" treatment already used
   for STEAM/SD (Fase 6) and SORT/MIX (Fase 7/8). **`[UNCONFIRMED]`**:
   whether PT JAS wants MG/MDPW findings queryable the way QC/MD's are
   (e.g. by extending `QualityTest.stage` to be nullable, or a new
   satellite table) is left open.
10. **`quantity` defaults to the batch's on-hand quantity** when not
    supplied -- identical reasoning and pattern to QC/MD (qc_md.py #2):
    neither form states whether the inspection covers a sub-lot, and the
    self-loop is stock-neutral either way, so this default cannot silently
    gain or lose stock.
11. **The self-loop batch's `batch_type` is expected to be `POWDER`** (both
    processes are named for powder-stage inspection), but this module does
    not enforce it -- same reasoning as decision #8 above, left to the
    caller/UI.
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
from .events import InputSpec, NewBatchSpec, OutputSpec, record_process_event

ZERO = Decimal("0")

# Grade Master "Powder" code (BATCH_NUMBER_SPEC.md "Resolved by PT JAS",
# already confirmed prior to this phase -- see module docstring #5). Same
# value as sortation.py's GRADE_POWDER; kept as a local constant here to
# avoid a cross-module import for one shared literal.
GRADE_POWDER = "05"


def _resolve_quantity(session: Session, batch_id: int, quantity: Optional[Decimal]) -> Decimal:
    if quantity is not None:
        return quantity
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"Batch {batch_id} does not exist.")
    return batch.current_quantity


# --------------------------------------------------------------------------
# Grinding & Sieving (grind)
# --------------------------------------------------------------------------


@dataclass
class GrindingInput:
    event_date: dt.date  # grind "TANGGAL" -- see module docstring #1
    pic_user_id: int
    nc_batch_id: int  # grind "BATCH NUMBER ASAL"
    final_qty: Decimal  # grind "QTY AKHIR POWDER (KG)" -- required, see #3
    starting_qty: Optional[Decimal] = None  # grind "QTY AWAL NC (KG)" -- default on-hand, see #2
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    result_date: Optional[dt.date] = None  # grind "TANGGAL HASIL" -- notes only, see #1
    process_code: Optional[str] = None  # [UNCONFIRMED] -- see #7


def _grinding_notes(data: GrindingInput) -> Optional[str]:
    if data.result_date is None:
        return None
    return json.dumps({"result_date": data.result_date.isoformat()})


def record_grinding(session: Session, data: GrindingInput) -> ProcessEvent:
    """Record a Grinding & Sieving event: ONE->NEW-BATCH
    (`EventType.GRINDING`), turning one NC (reject-grade) batch into one new
    Powder batch. `shrinkage_qty` is derived, never caller-supplied -- see
    module docstring #4.
    """
    source = session.get(Batch, data.nc_batch_id)
    if source is None:
        raise ValueError(f"Batch {data.nc_batch_id} does not exist.")

    starting_qty = _resolve_quantity(session, data.nc_batch_id, data.starting_qty)
    if starting_qty <= 0:
        raise ValueError("Grinding starting quantity must be positive.")
    if data.final_qty <= 0:
        raise ValueError("Grinding final powder quantity must be positive.")

    shrinkage_qty = starting_qty - data.final_qty  # derived -- see module docstring #4

    output = OutputSpec(
        quantity=data.final_qty,
        unit=data.unit,
        new_batch=NewBatchSpec(
            batch_type=BatchType.POWDER,  # see #5
            grade_code=GRADE_POWDER,  # see #5
            jenis_code=source.jenis_code,  # inherited -- see #6
            supplier_id=source.supplier_id,
            supplier_code=source.supplier_code,
            receiving_date=source.receiving_date,
            process_code=data.process_code,  # [UNCONFIRMED] -- see #7
            unit=data.unit,
        ),
    )

    return record_process_event(
        session,
        event_type=EventType.GRINDING,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.nc_batch_id, quantity=starting_qty, unit=data.unit)],
        outputs=[output],
        shrinkage_qty=shrinkage_qty,
        notes=_grinding_notes(data),
    )


# --------------------------------------------------------------------------
# Magnetization (MG) and Metal Detection Powder (MDPW)
# --------------------------------------------------------------------------


def _finding_notes(finding: Optional[str], extra_notes: Optional[str]) -> Optional[str]:
    payload = {"finding": finding, "notes": extra_notes}
    payload = {k: v for k, v in payload.items() if v is not None}
    return json.dumps(payload, ensure_ascii=False) if payload else None


@dataclass
class MagnetizationInput:
    event_date: dt.date  # MG "TANGGAL"
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None  # MG "QTY (KG)" -- default on-hand, see #10
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    finding: Optional[str] = None  # MG "TEMUAN"
    notes: Optional[str] = None  # MG "KETERANGAN"


def record_magnetization(session: Session, data: MagnetizationInput) -> ProcessEvent:
    """Record a Magnetization event: self-loop ONE->ONE
    (`EventType.MAGNETIZATION`) on the batch under test. No `QualityTest`
    row is written -- see module docstring #9.
    """
    quantity = _resolve_quantity(session, data.batch_id, data.quantity)
    if quantity <= 0:
        raise ValueError("Magnetization quantity must be positive.")

    return record_process_event(
        session,
        event_type=EventType.MAGNETIZATION,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        notes=_finding_notes(data.finding, data.notes),
    )


@dataclass
class MDPowderInput:
    event_date: dt.date  # MDPW "TANGGAL"
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None  # MDPW "QTY (KG)" -- default on-hand, see #10
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    finding: Optional[str] = None  # MDPW "TEMUAN"
    notes: Optional[str] = None  # MDPW "KETERANGAN"


def record_md_powder(session: Session, data: MDPowderInput) -> ProcessEvent:
    """Record a Metal Detection Powder event: self-loop ONE->ONE
    (`EventType.MD_POWDER`) on the batch under test. No `QualityTest` row is
    written -- see module docstring #9.
    """
    quantity = _resolve_quantity(session, data.batch_id, data.quantity)
    if quantity <= 0:
        raise ValueError("Metal Detection Powder quantity must be positive.")

    return record_process_event(
        session,
        event_type=EventType.MD_POWDER,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        notes=_finding_notes(data.finding, data.notes),
    )
