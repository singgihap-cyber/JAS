"""Fase 11 -- Vacuum (VC) + Packing (PACK).

Wires the real VC and PACK source fields (REQUIREMENTS.md field groups) to
the generic engine's `record_process_event()`. Per 03_CORE_ENGINE.md/
PROJECT_STATUS.md, this phase only needs a field-mapping layer --
genealogy/stock/reconciliation side effects are already handled generically
by `record_process_event()` since Fase 3.

**Source data check (CLAUDE.md rule 11 -- document ambiguity instead of
guessing):** unlike Fase 9/10's grind/MG/MDPW/REW sheets (headers only, no
data), the `VC` and `PACK` sheets in `DATA PROSES VANILA 2026 REVISI.xlsx`
were read directly (openpyxl, 2026-09-16) and **do contain real filled-in
rows** (VC: rows 7-116+; PACK: rows 7-72+) -- the first real data this
project has seen since Fase 8's MIX sheet. Every decision below that
concerns Vacuum/Packing's own shape is grounded in that real data, not
guessed from REQUIREMENTS.md's abstract field-group description alone.

## Vacuum (`VC`)

Real header (row 6): `NO., TANGGAL, BATCH NUMBER, DESKRIPSI PRODUK, UKURAN
PLASTIK, BATCH NO. PLASTIK, QTY PLASTIK (pcs), BERAT PER PACK (Kg), BERAT
TOTAL (Kg), BUYER, PIC` -- concrete evidence behind REQUIREMENTS.md's "VC:
date, batch, description, plastic size/lot/qty, pack weight, total weight,
buyer, PIC".

1. **Vacuum is a self-loop ONE->ONE event -- it does NOT mint a new batch.**
   This resolves the ambiguity PROJECT_STATUS.md flagged going into this
   phase ("Vacuum kemungkinan besar ONE->ONE self-loop ... belum
   dikonfirmasi eksplisit"). Two pieces of direct evidence from the real
   sheet, not assumption: (a) the `VC` header has **no "new/output batch
   number" column at all** -- contrast with MIX's `BATCH NUMBER GABUNGAN`
   and REW's `BATCH NUMBER SETELAH REWORK`, both of which exist as
   dedicated (if often-blank) columns precisely because those processes
   mint a new batch; VC has only the single input `BATCH NUMBER` column.
   (b) Cross-checking real rows: batch `010103-250121-00` appears in `VC`
   row 7 and the *same, unchanged* batch number reappears later as the
   `PACK` sheet's `VANILLA BATCH NO.` for a *different* packing event (row
   12) -- i.e. the batch identity that survives past Vacuum into Packing is
   the original vanilla-product batch, not a distinct "vacuumed" batch.
   `EventType.VACUUM` therefore uses `OutputSpec(batch_id=..., ...)` (reuse)
   never `OutputSpec(new_batch=...)`, the same shape as QC/MD/Steaming's
   inspection self-loops (Fase 3/5/6).

2. **The event's quantity is derived as `SUM(plastic line "BERAT TOTAL
   (Kg)")`, not caller-supplied or defaulted to the batch's on-hand
   balance.** The `VC` form has no "starting qty" column at all (unlike
   every self-loop phase so far -- QC/MD/Steaming all have an explicit or
   defaultable quantity field), so there is nothing to default
   `_resolve_quantity`-style. What the form *does* record, per event, is
   one or more plastic-packaging lines (see #3) each with its own measured
   `BERAT TOTAL (Kg)` -- e.g. real row group NO.=1 (`010103-250121-00`,
   2026-01-12) has two plastic lines, `20` kg (25x37.5 plastic) + `0.03` kg
   (15x25 plastic) = `20.03` kg vacuum-packed in that session. Summing the
   line totals is therefore the only value the source data actually
   supports as "how much of this batch got vacuum-packed today" --
   inventing a separate independent quantity field would contradict the
   real form. This also mirrors Mixing's `cp_qty = SUM(sources)` /
   Sortation's `shrinkage = initial - breakdown` pattern of deriving a
   total from a recorded breakdown rather than trusting/inventing a
   parallel field.

3. **Multiple plastic lines per Vacuum event are real, not hypothetical.**
   Confirmed directly: NO.=1 (`010103-250121-00`) has 2 plastic lines
   (20kg @ 25x37.5, 0.03kg @ 15x25); NO.=4 (`010100-260120-03`) has 2 lines
   (2kg @ 25x30, 0.03kg @ 15x25); NO.=6/7 show a single large-format line
   each. `VacuumInput.plastic_lines` is therefore a list
   (`VacuumPlasticLine`), each carrying its own `plastic_size` ("UKURAN
   PLASTIK"), `plastic_lot` ("BATCH NO. PLASTIK"), `plastic_qty` ("QTY
   PLASTIK (pcs)"), `weight_per_pack` ("BERAT PER PACK (Kg)"), and
   `total_weight` ("BERAT TOTAL (Kg)", required, drives #2). Because a
   self-loop batch has no per-batch-type packaging columns to hold a *list*
   of plastic lines (the `Batch.plastic_*` fields are flat, single-valued,
   and reserved for `batch_type=PACKAGED` per `DATABASE_DESIGN.md` §1
   anyway -- see #1, this event never touches them), the full line list is
   recorded in `ProcessEvent.notes` as structured JSON -- the same
   "no dedicated column for this shape" treatment as Mixing's
   `product_description` (mixing.py #8) and Rework's `process_description`
   (rework.py #8).

4. **`DESKRIPSI PRODUK` and `BUYER` are recorded in `notes`, not mapped onto
   any `Batch`/dedicated column.** `DESKRIPSI PRODUK` is the same kind of
   free-text grade-like descriptor already treated as notes-only in Mixing
   (mixing.py #8); `BUYER` has no `Customer` FK at this layer -- per
   `GENEALOGY.md` §6 the `Customer` link belongs to `Shipment`
   (Delivery/`PD`/`SmpD`, Fase 12), not Vacuum/Packing, so a buyer name
   appearing here is informal/preliminary and captured as text only.

## Packing (`PACK`)

Real header (rows 5-6): `NO., TANGGAL, NOMOR PENGIRIMAN, TUJUAN PENGIRIMAN,
VANILLA BATCH NO., DESKRIPSI PRODUK, PEMBELI, BERAT (KG), UKURAN PLASTIK
VACCUM, LOT NO. PLASTIK VACUM, QTY PLASTIK VACUM, LOT NO. KARTON, QTY
KARTON (COLY), AMPLOP, BRUTO, TARA, NETTO, PIC` -- concrete evidence behind
REQUIREMENTS.md's "PACK: date, shipping number, destination, vanilla batch,
description, buyer, weight, plastic size/lot/qty, carton lot". Packing's
ONE-or-MANY->ONE, `batch_type=PACKAGED` shape is **not new to this phase**
-- it was already established and tested at the generic-engine layer in
Fase 3 (`tests/test_events.py::test_packing_single_batch_creates_packaged_lot`,
`test_packing_can_combine_multiple_batches`, `GENEALOGY.md` §3.1/§6:
"Vacuum, Packing -- confirmed: a single Packing event may combine more than
one vanilla batch ... output is a PackagingLot-typed batch"). This module
only adds the PACK field-mapping layer on top of that already-confirmed
shape.

5. **Each source batch's contributed weight ("BERAT (KG)") is required per
   source, never defaulted to on-hand** -- directly analogous to Mixing's
   per-source `QTY ASAL (KG)` (mixing.py #10): the same vanilla batch
   number (e.g. `030300-251121-03`) recurs across multiple separate `PACK`
   rows with different `BERAT (KG)` values each time (1000, then later 1000
   again in a different shipment), confirming partial-batch Packing across
   several distinct events is real, the same pattern as Mixing/Vacuum.
   `PackingInput.sources` is a list of `PackingSource(batch_id, quantity)`,
   requiring >=1 (not >=2 like Mixing -- the majority real case is a single
   source batch per carton; Fase 3's confirmed multi-batch case is still
   fully supported by the same list, just with len>1).

6. **`NETTO` ("net_weight") is derived as `SUM(sources quantities)`, never
   independently caller-supplied.** Verified directly against every real
   row: `BERAT (KG)` always equals `NET WEIGHT (KG)` exactly (e.g. row 7:
   `H=1000, Q=1000`; row 8: `H=1.4, Q=1.4`), and the `PACK` sheet has no
   separate loss/shrinkage column at all -- there is no evidence Packing
   ever loses product weight, unlike Sortation/Mixing/Rework's explicit
   `SUSUT` columns. `shrinkage_qty` is therefore fixed at `0` for
   `EventType.PACKING` (not exposed as a parameter), and the derived
   `net_weight` is what both the output batch's `net_weight` attribute and
   `OutputSpec.quantity` (stock ledger effect) use -- keeping quantity
   reconciliation exact by construction (`GENEALOGY.md` §5), same
   philosophy as Mixing's derived `cp_qty`.

7. **`TARA` ("tare_weight") is derived as `gross_weight - net_weight`, never
   caller-supplied**, per `Proses.docx`'s own description of the Packing
   step ("Tidak ada penimbangan khusus untuk kemasan ... dihitung bruto
   dikurangi netto vanili" -- "no dedicated weighing for the packaging...
   it's calculated as gross minus net vanilla weight") and verified
   arithmetically against every real row (e.g. row 7: `O(BRUTO)=1057.2,
   Q(NETTO)=1000 => P(TARA)=57.2`, matches exactly; row 9:
   `O=2.275, Q=2 => 0.275`, matches). This is both textually documented by
   PT JAS and numerically exact in the real data -- the strongest evidence
   basis of any derived field in this project so far. `gross_weight`
   ("BRUTO") is the one independently *measured* value with no sensible
   default, required from the caller (same reasoning as every other
   phase's terminal weighed figure -- Sundrying/Mixing's `final_qty`).
   A resulting negative `tare_weight` (gross < net) is not blocked, for the
   same reason other derived-quantity edge cases aren't
   (`PROCESS_RULES.md` still lists tolerances/edge-case handling as
   `[UNCONFIRMED]`) -- it would indicate a data-entry error to catch
   downstream, not a case for this layer to guess a validation rule for.

8. **`UKURAN PLASTIK VACCUM` / `LOT NO. PLASTIK VACUM` / `QTY PLASTIK
   VACUM` map directly onto the output batch's `plastic_size` /
   `plastic_lot` / `plastic_qty`, and `LOT NO. KARTON` onto `carton_lot`.**
   Unlike Vacuum's own multiple plastic lines (module docstring #3), the
   `PACK` sheet records exactly one plastic-size/lot/qty triple per event
   (the carton-level packaging summary at the point the carton is closed),
   which fits `Batch`'s flat, single-valued packaging columns exactly as
   `DATABASE_DESIGN.md` §1 defines them for `batch_type=PACKAGED` -- no
   list/JSON workaround needed here, unlike Vacuum.

9. **`QTY KARTON (COLY)` and `AMPLOP` (envelope count) have no dedicated
   `Batch` column** -- `DATABASE_DESIGN.md`'s packaging-attribute list is
   `plastic_size/plastic_lot/plastic_qty/carton_lot/gross_weight/
   tare_weight/net_weight`, with a carton *lot* but no carton *count*
   column, and no envelope column at all. Both are recorded in `notes` as
   structured data, the same "no dedicated column for this shape"
   treatment as Vacuum's plastic-line list (#3) and every prior phase's
   non-generalizing descriptive fields.

10. **`NOMOR PENGIRIMAN` (shipping number) and `TUJUAN PENGIRIMAN`
    (destination) are recorded in `notes`, not modeled as a `Shipment`.**
    This is a genuine, evidenced overlap worth flagging explicitly
    (CLAUDE.md rule 11): `GENEALOGY.md` §6 assigns shipping number and
    destination to the `Shipment` satellite entity attached to a
    `DELIVERY`/`SAMPLE_DELIVERY` event (Fase 12, from the `PD`/`SmpD`
    sheets), yet the real `PACK` sheet already carries both fields at the
    Packing step. Rather than building a `Shipment`-shaped side-table
    ahead of Fase 12 (which would duplicate/pre-empt that phase's own
    design work) or discarding the data, this module preserves it
    verbatim in `ProcessEvent.notes` -- **`[UNCONFIRMED]`**: whether
    PT JAS assigns the shipping number at Packing time and Delivery simply
    reuses it, or these are two independently-recorded numbers that happen
    to coincide, is left open for Fase 12 to resolve against the `PD`
    sheet.

11. **`DESKRIPSI PRODUK` and `PEMBELI` (buyer) go to `notes`** -- identical
    reasoning to Vacuum's #4 (Mixing-style free-text description, no
    `Customer` FK at this layer).

12. **`grade_code`/`jenis_code`/`supplier_id`/`supplier_code`/
    `receiving_date` are inherited from the single source batch only when
    there is exactly one source; left `None` (unless the caller states them
    explicitly) when there are multiple.** Mirrors Mixing's documented
    reasoning exactly (mixing.py #4): with one source, inheritance is
    unambiguous (same reasoning as Sortation's/Rework's/Grinding's
    single-source inheritance); with several, which source's grade/jenis/
    supplier "wins" for the combined carton is not evidenced anywhere in
    the real data and guessing would violate CLAUDE.md rule 11.

13. **`process_code` has no confirmed value for Packing**, exactly the same
    open point as Grinding's (`powder.py` #7): `BATCH_NUMBER_SPEC.md`'s
    "Ongoing Grading" (PP) table only defines
    `00=Original, 01=Upgrade, 02=Downgrade, 03=Mixing, 04=Rework` -- none
    named "Packing" -- and neither `VC` nor `PACK` has any "new batch
    number" column to reverse-engineer a PP value from (module docstring
    #1). Left as an explicit optional caller parameter, default `None` --
    **`[UNCONFIRMED]`**, added to PROJECT_STATUS.md's open questions
    alongside Grinding's identical gap.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..enums import BatchType, EventType
from ..models import Batch, ProcessEvent
from .events import InputSpec, NewBatchSpec, OutputSpec, record_process_event

ZERO = Decimal("0")


# --------------------------------------------------------------------------
# Vacuum (VC)
# --------------------------------------------------------------------------


@dataclass
class VacuumPlasticLine:
    total_weight: Decimal  # VC "BERAT TOTAL (Kg)" -- required, drives event quantity, see module docstring #2
    plastic_size: Optional[str] = None  # VC "UKURAN PLASTIK"
    plastic_lot: Optional[str] = None  # VC "BATCH NO. PLASTIK"
    plastic_qty: Optional[Decimal] = None  # VC "QTY PLASTIK (pcs)"
    weight_per_pack: Optional[Decimal] = None  # VC "BERAT PER PACK (Kg)"


@dataclass
class VacuumInput:
    event_date: dt.date  # VC "TANGGAL"
    pic_user_id: int
    batch_id: int  # VC "BATCH NUMBER" -- self-loop, see module docstring #1
    plastic_lines: list[VacuumPlasticLine] = field(default_factory=list)  # see #3
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    product_description: Optional[str] = None  # VC "DESKRIPSI PRODUK" -- notes only, see #4
    buyer: Optional[str] = None  # VC "BUYER" -- notes only, see #4


def _vacuum_notes(data: VacuumInput) -> str:
    payload = {
        "plastic_lines": [
            {
                "plastic_size": line.plastic_size,
                "plastic_lot": line.plastic_lot,
                "plastic_qty": str(line.plastic_qty) if line.plastic_qty is not None else None,
                "weight_per_pack": str(line.weight_per_pack) if line.weight_per_pack is not None else None,
                "total_weight": str(line.total_weight),
            }
            for line in data.plastic_lines
        ]
    }
    if data.product_description is not None:
        payload["product_description"] = data.product_description
    if data.buyer is not None:
        payload["buyer"] = data.buyer
    return json.dumps(payload, ensure_ascii=False)


def record_vacuum(session: Session, data: VacuumInput) -> ProcessEvent:
    """Record a Vacuum event: self-loop ONE->ONE (`EventType.VACUUM`), no
    new batch minted -- module docstring #1. Event quantity is
    `SUM(plastic_lines.total_weight)` -- see #2.
    """
    if not data.plastic_lines:
        raise ValueError("Vacuum requires at least one plastic line (module docstring #3).")

    total_weight = ZERO
    for line in data.plastic_lines:
        if line.total_weight is None or line.total_weight <= 0:
            raise ValueError("Each Vacuum plastic line's total_weight must be positive.")
        total_weight += line.total_weight

    return record_process_event(
        session,
        event_type=EventType.VACUUM,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=total_weight, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=total_weight, unit=data.unit)],
        notes=_vacuum_notes(data),
    )


# --------------------------------------------------------------------------
# Packing (PACK)
# --------------------------------------------------------------------------


@dataclass
class PackingSource:
    batch_id: int
    quantity: Decimal  # PACK "BERAT (KG)" for this source line -- required, see module docstring #5


@dataclass
class PackingInput:
    event_date: dt.date  # PACK "TANGGAL"
    pic_user_id: int
    sources: list[PackingSource]  # PACK "VANILLA BATCH NO." (+ "BERAT (KG)") -- see #5
    gross_weight: Decimal  # PACK "BRUTO" -- required, see #7
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    plastic_size: Optional[str] = None  # PACK "UKURAN PLASTIK VACCUM" -- see #8
    plastic_lot: Optional[str] = None  # PACK "LOT NO. PLASTIK VACUM"
    plastic_qty: Optional[Decimal] = None  # PACK "QTY PLASTIK VACUM"
    carton_lot: Optional[str] = None  # PACK "LOT NO. KARTON"
    carton_qty: Optional[Decimal] = None  # PACK "QTY KARTON (COLY)" -- notes only, see #9
    envelope_qty: Optional[Decimal] = None  # PACK "AMPLOP" -- notes only, see #9
    shipping_number: Optional[str] = None  # PACK "NOMOR PENGIRIMAN" -- notes only, see #10
    destination: Optional[str] = None  # PACK "TUJUAN PENGIRIMAN" -- notes only, see #10
    product_description: Optional[str] = None  # PACK "DESKRIPSI PRODUK" -- notes only, see #11
    buyer: Optional[str] = None  # PACK "PEMBELI" -- notes only, see #11
    grade_code: Optional[str] = None  # inherited if single-source, else explicit only -- see #12
    jenis_code: Optional[str] = None
    supplier_id: Optional[int] = None
    supplier_code: Optional[str] = None
    receiving_date: Optional[dt.date] = None
    process_code: Optional[str] = None  # [UNCONFIRMED] -- see #13
    batch_type: BatchType = BatchType.PACKAGED


def _packing_notes(data: PackingInput, carton_qty: Optional[Decimal], envelope_qty: Optional[Decimal]) -> Optional[str]:
    payload = {}
    if data.shipping_number is not None:
        payload["shipping_number"] = data.shipping_number
    if data.destination is not None:
        payload["destination"] = data.destination
    if data.product_description is not None:
        payload["product_description"] = data.product_description
    if data.buyer is not None:
        payload["buyer"] = data.buyer
    if carton_qty is not None:
        payload["carton_qty"] = str(carton_qty)
    if envelope_qty is not None:
        payload["envelope_qty"] = str(envelope_qty)
    return json.dumps(payload, ensure_ascii=False) if payload else None


def record_packing(session: Session, data: PackingInput) -> ProcessEvent:
    """Record a Packing event: ONE-or-MANY->ONE (`EventType.PACKING`),
    minting one new `batch_type=PACKAGED` output batch (shape already
    established/tested in Fase 3 -- see module docstring, "Packing"
    section). `net_weight` and `tare_weight` are both derived, never
    caller-supplied -- see #6/#7.
    """
    if not data.sources:
        raise ValueError("Packing requires at least one source batch (module docstring #5).")

    batch_ids = [s.batch_id for s in data.sources]
    if len(set(batch_ids)) != len(batch_ids):
        raise ValueError("Packing sources must not repeat the same batch_id more than once.")

    for s in data.sources:
        if s.quantity <= 0:
            raise ValueError(f"Packing source quantity for batch_id={s.batch_id} must be positive.")

    if data.gross_weight <= 0:
        raise ValueError("Packing gross weight (BRUTO) must be positive.")

    net_weight = sum((s.quantity for s in data.sources), ZERO)  # derived -- see #6
    tare_weight = data.gross_weight - net_weight  # derived -- see #7

    inherited: dict = {}
    if len(data.sources) == 1:
        source = session.get(Batch, data.sources[0].batch_id)
        if source is None:
            raise ValueError(f"Batch {data.sources[0].batch_id} does not exist.")
        inherited = {
            "grade_code": source.grade_code,
            "jenis_code": source.jenis_code,
            "supplier_id": source.supplier_id,
            "supplier_code": source.supplier_code,
            "receiving_date": source.receiving_date,
        }  # single-source inheritance -- see #12

    def pick(explicit, key):
        return explicit if explicit is not None else inherited.get(key)

    inputs = [
        InputSpec(batch_id=s.batch_id, quantity=s.quantity, unit=data.unit) for s in data.sources
    ]

    output = OutputSpec(
        quantity=net_weight,
        unit=data.unit,
        new_batch=NewBatchSpec(
            batch_type=data.batch_type,
            grade_code=pick(data.grade_code, "grade_code"),
            jenis_code=pick(data.jenis_code, "jenis_code"),
            supplier_id=pick(data.supplier_id, "supplier_id"),
            supplier_code=pick(data.supplier_code, "supplier_code"),
            receiving_date=pick(data.receiving_date, "receiving_date"),
            process_code=data.process_code,  # [UNCONFIRMED] -- see #13
            unit=data.unit,
            plastic_size=data.plastic_size,  # see #8
            plastic_lot=data.plastic_lot,
            plastic_qty=data.plastic_qty,
            carton_lot=data.carton_lot,
            gross_weight=data.gross_weight,
            tare_weight=tare_weight,
            net_weight=net_weight,
        ),
    )

    return record_process_event(
        session,
        event_type=EventType.PACKING,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=inputs,
        outputs=[output],
        shrinkage_qty=ZERO,  # fixed at 0 -- see #6
        notes=_packing_notes(data, data.carton_qty, data.envelope_qty),
    )
