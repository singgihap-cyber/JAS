"""Fase 8 -- Mixing (MIX).

Wires the real MIX source fields (REQUIREMENTS.md field group) to the
generic engine's `record_process_event(event_type=MIXING, ...)` as a
MANY->ONE event (N source batches -> 1 new combined batch -- the shape
already established and tested in Fase 3, see
`tests/test_events.py::test_mixing_combines_multiple_source_batches` and
`tests/test_genealogy.py::test_backward_trace_mixing_keeps_all_parents`,
GENEALOGY.md §2/§3.1/§7.1). Per 03_CORE_ENGINE.md/PROJECT_STATUS.md, Fase 8
only needs this field-mapping layer -- genealogy/stock/reconciliation side
effects are already handled generically by `record_process_event()` since
Fase 3. There is no dedicated Mixing satellite table, same situation
already noted for STEAM/SD (Fase 6) and SORT (Fase 7).

The real MIX sheet (`DATA PROSES VANILA 2026 REVISI.xlsx`, sheet "MIX",
header row) has columns: `NO., TANGGAL, BATCH NUMBER ASAL, QTY ASAL (KG),
BATCH NUMBER GABUNGAN, QTY CP (KG), SUSUT (KG), QTY AKHIR (KG), DESKRIPSI
PRODUK, PIC` -- this is the concrete evidence behind REQUIREMENTS.md's
abstract "MIX: source batches/quantities, combined batch, CP qty,
shrinkage, final qty, product description, PIC" field group, and grounds
every decision below (CLAUDE.md rule 11: document ambiguity instead of
guessing).

Decisions made in this phase, and why:

1. **`QTY CP (KG)` ("CP qty") is derived as `SUM(source quantities)`, not
   an independently caller-supplied value.** Checked directly against every
   completed row group in the real MIX sheet: e.g. sources
   `010100-251215-03` (1.05) + `010130-250725-00` (0.8) +
   `030143-241206-00` (0.2) = 2.05 = the recorded `QTY CP (KG)` for that
   event; sources 13.35 (+ nothing else in that group's visible rows) with
   `QTY CP (KG)` 108.32 likewise matches the group total. This mirrors
   Fase 6/7's derived-shrinkage pattern (steam_dry.py #2, sortation.py #6)
   and keeps quantity reconciliation (GENEALOGY.md §5) exact by
   construction -- there is no independent "combined weight" business rule
   to invent here, it is arithmetically the sum of what went in.

2. **`SUSUT (KG)` (shrinkage) is derived as `cp_qty - final_qty`, not
   caller-supplied.** Verified directly, e.g. `QTY CP` 108.32 -
   `SUSUT` 0.5 = `QTY AKHIR` 107.82. Same reasoning/pattern as #1. A
   resulting negative shrinkage (final > cp) is not blocked, for the same
   reason Sundrying's/Sortation's aren't (steam_dry.py #2, sortation.py
   #6) -- PROCESS_RULES.md still lists "shrinkage tolerances" as
   `[UNCONFIRMED]`.

3. **`final_qty` (`QTY AKHIR`) is required, not defaulted.** Unlike a
   single source batch's on-hand quantity (`_resolve_quantity` elsewhere),
   there is no sensible default for "the weight of the newly combined
   batch after mixing" -- it is always an independently measured value in
   the source sheet, exactly like Sundrying's `final_quantity`
   (steam_dry.py #3 note on required fields).

4. **The combined batch's `jenis_code` is NOT mechanically inherited from
   the sources and is left `None` unless the caller states it
   explicitly.** Real evidence: the mixing event ending in output
   `030200-260103-03` (AA=`03`) draws on source `010248-251124-03`
   (AA=`01`) alongside many AA=`03` sources -- i.e. a single Mixing event
   can blend batches whose Jenis segments genuinely differ, so there is no
   single correct "inherit from source" rule to encode (contrast with
   Sortation, a ONE->MANY event with exactly one source, where inheriting
   is unambiguous -- sortation.py #4). Guessing which input's Jenis "wins"
   would violate CLAUDE.md rule 11; the caller/UI states it if needed.

5. **`supplier_id` defaults to `None` and `supplier_code` defaults to
   `"000"`.** BATCH_NUMBER_SPEC.md's "Resolved by PT JAS" section
   documents that supplier code `00` (now `000` under the 3-digit
   migration confirmed 2026-09-14, since Mixing events recorded from now
   on are new-format) is used specifically -- and only -- for
   Mixing-output batches, "once several suppliers' material is blended
   the batch can no longer be attributed to one supplier, so the supplier
   slot is zeroed out", evidenced by 19+ real Mixing-output batches all
   carrying `CC=00`. A caller may still override `supplier_id` for the
   (unobserved but not precluded) case where every source happens to
   share one supplier.

6. **`process_code` is always `"03"` (Mixing)** -- BATCH_NUMBER_SPEC.md's
   confirmed "Ongoing Grading" (PP) codes. Unlike Sortation (module #5),
   there is no caller-chosen direction here: the event type itself fixes
   the code, so it is not exposed as a parameter.

7. **`batch_type` defaults to `PROCESSED`.** Every completed real MIX row
   observed produces a dried whole-product output (`DESKRIPSI PRODUK` =
   "GOURMET" or "EG"), never Powder or a packaging lot. A caller can
   override it for an undocumented case (e.g. mixing Powder-grade
   material) -- not observed in the sample data, not precluded either.

8. **`product_description` (`DESKRIPSI PRODUK`) is recorded verbatim in
   `ProcessEvent.notes`, not mechanically mapped onto `Batch.grade_code`.**
   The observed values ("GOURMET", "EG") happen to coincide with Grade
   Master terms, but REQUIREMENTS.md names this a "product description"
   field, not a grade field, and nothing confirms every future value will
   be a canonical grade string. Rather than inventing a fuzzy
   description->grade_code mapping (CLAUDE.md rule 11), the raw text goes
   to `notes` (same treatment as other non-generalizing descriptive
   fields, e.g. STEAM's water/pan condition -- steam_dry.py #4) and a
   caller who already knows the structural grade can pass `grade_code`
   explicitly as its own optional field.

9. **At least two source batches are required, and no batch may appear
   twice as a source in the same call.** "Combining" (REQUIREMENTS.md:
   "source batch*es*/quantities", plural) implies more than one, and every
   completed event in the real MIX sheet has >=2 source rows (the
   smallest observed group has exactly 2). A repeated `batch_id` would
   also collide with `EventBatchLink`'s
   `uq_event_batch_role` unique constraint at the database layer
   (models.py) -- this module raises a clear `ValueError` before that
   happens rather than surfacing a raw integrity error.

10. **Each source's quantity is required per source, never defaulted to
    that batch's on-hand balance** (unlike Steaming/Sundrying/Sortation's
    single-batch `_resolve_quantity`). The real MIX sheet always records an
    explicit `QTY ASAL (KG)` per source row, and a source batch is not
    necessarily consumed in full by a given Mixing event (e.g. source
    `010100-251215-03` reappears with a different remaining balance in a
    later, separate Mixing event elsewhere in the sheet).

11. **`event_date` maps to MIX's `TANGGAL`, recorded once per event**
    (the source sheet only fills the date on the first source row of each
    group) -- same single-`event_date`-per-event treatment as every other
    phase.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..enums import BatchType, EventType
from ..models import ProcessEvent
from .events import InputSpec, NewBatchSpec, OutputSpec, record_process_event

ZERO = Decimal("0")

# "Ongoing Grading" / PP code for Mixing (BATCH_NUMBER_SPEC.md, confirmed) --
# fixed for this event type, see module docstring #6.
PROCESS_CODE_MIXING = "03"

# Supplier slot zeroed out for a combined (multi-supplier) batch -- new
# 3-digit convention since the migration decided 2026-09-14 (module
# docstring #5).
MIXED_SUPPLIER_CODE = "000"


@dataclass
class MixingSource:
    batch_id: int
    quantity: Decimal  # MIX "QTY ASAL (KG)" for this source row -- see module docstring #10


@dataclass
class MixingInput:
    event_date: dt.date  # MIX "TANGGAL" -- see module docstring #11
    pic_user_id: int
    sources: list[MixingSource]  # MIX "source batches/quantities" -- see #9
    final_qty: Decimal  # MIX "QTY AKHIR (KG)" -- required, see #3
    product_description: str  # MIX "DESKRIPSI PRODUK" -- notes only, see #8
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    grade_code: Optional[str] = None  # explicit override only, never inferred -- see #8
    jenis_code: Optional[str] = None  # never inherited automatically -- see #4
    supplier_id: Optional[int] = None  # default: unattributable -- see #5
    supplier_code: str = MIXED_SUPPLIER_CODE  # see #5
    batch_type: BatchType = BatchType.PROCESSED  # see #7


def _mix_notes(data: MixingInput, cp_qty: Decimal) -> str:
    payload = {
        "product_description": data.product_description,
        "cp_qty": str(cp_qty),  # "QTY CP (KG)" -- derived, see #1; recorded for audit parity with the source form
    }
    return json.dumps(payload, ensure_ascii=False)


def record_mixing(session: Session, data: MixingInput) -> ProcessEvent:
    """Record a Mixing event: MANY->ONE (`EventType.MIXING`), combining
    `data.sources` into one newly-minted output batch. `cp_qty` and
    `shrinkage_qty` are both derived, never caller-supplied -- module
    docstring #1/#2.
    """
    if len(data.sources) < 2:
        raise ValueError(
            "Mixing requires at least two source batches (module docstring #9)."
        )

    batch_ids = [s.batch_id for s in data.sources]
    if len(set(batch_ids)) != len(batch_ids):
        raise ValueError(
            "Mixing sources must not repeat the same batch_id more than once "
            "(module docstring #9)."
        )

    for s in data.sources:
        if s.quantity <= 0:
            raise ValueError(
                f"Mixing source quantity for batch_id={s.batch_id} must be positive."
            )

    if data.final_qty <= 0:
        raise ValueError("Mixing final quantity must be positive.")

    cp_qty = sum((s.quantity for s in data.sources), ZERO)  # module docstring #1
    shrinkage_qty = cp_qty - data.final_qty  # module docstring #2

    inputs = [
        InputSpec(batch_id=s.batch_id, quantity=s.quantity, unit=data.unit)
        for s in data.sources
    ]

    output = OutputSpec(
        quantity=data.final_qty,
        unit=data.unit,
        new_batch=NewBatchSpec(
            batch_type=data.batch_type,
            grade_code=data.grade_code,
            jenis_code=data.jenis_code,
            supplier_id=data.supplier_id,
            supplier_code=data.supplier_code,
            process_code=PROCESS_CODE_MIXING,
            unit=data.unit,
        ),
    )

    return record_process_event(
        session,
        event_type=EventType.MIXING,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=inputs,
        outputs=[output],
        shrinkage_qty=shrinkage_qty,
        notes=_mix_notes(data, cp_qty),
    )
