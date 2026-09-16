"""Fase 12 -- Delivery (PD) + Sample Delivery (SmpD).

Wires the real PD and SmpD source fields (REQUIREMENTS.md field groups) to
the generic engine's `record_process_event()`. Per 03_CORE_ENGINE.md/
PROJECT_STATUS.md, this phase only needs a field-mapping layer plus the
`Shipment` satellite -- `EventType.DELIVERY`/`SAMPLE_DELIVERY` are already
`NO_OUTPUT_EVENT_TYPES` (STOCK-OUT, no output batch) since Fase 3, already
validated (`InvalidEventStructureError` on outputs/no-inputs) and already
tested at the generic-engine layer
(`tests/test_events.py::test_delivery_creates_stock_out`,
`test_delivery_rejects_outputs`,
`test_sample_delivery_marks_stock_out_as_sample`).

**Source data check (CLAUDE.md rule 11 -- document ambiguity instead of
guessing):** unlike Fase 9/10's grind/MG/MDPW/REW sheets, the `PD` and
`SmpD` sheets in `DATA PROSES VANILA 2026 REVISI.xlsx` were read directly
(openpyxl, 2026-09-16) and **do contain real filled-in rows** (PD: rows
9-45+; SmpD: rows 9-42+) -- real data, same situation as Fase 11's VC/PACK.
Every decision below is grounded in that real data.

## PD (Product Delivery)

Real header (rows 6-8, two-row merged header): `NO., TANGGAL, NOMOR
PENGIRIMAN, BATCH NO., TUJUAN (PERUSAHAAN, LOKASI), JENIS EKSPEDISI,
KONDISI ANGKUT, KONDISI KEMASAN, QTY BOX (COLY), BERAT (BRUTO, TARA,
NETTO), PIC` -- concrete evidence behind REQUIREMENTS.md's "PD: date,
shipping number, batch, destination, expedition, transport/packaging
condition, coly, gross/tare/net, PIC".

## SmpD (Sample Delivery)

Real header: `NO., TANGGAL, NOMOR PENGIRIMAN, BATCH NO., DESKRIPSI VANILA,
TUJUAN (NAMA, ALAMAT), JENIS EKSPEDISI, KONDISI ANGKUT, KONDISI KEMASAN,
QTY BOX (COLY), BERAT (BRUTO, TARA, NETTO), PIC` -- concrete evidence
behind REQUIREMENTS.md's "SmpD: date, shipping number, batch, description,
recipient, expedition, transport/packaging condition, coly, gross/tare/net,
PIC".

## Decisions

1. **`NOMOR PENGIRIMAN` (shipping number) is CONFIRMED reused verbatim from
   Packing, resolving Fase 11's open question #11** ("apakah nomor
   pengiriman yang sama di PACK dipakai ulang di PD, atau dua pencatatan
   independen"). Direct cross-sheet evidence: `LN/EP/260408-007` appears
   both in `PACK` (row for batch `030300-251121-03`, `TUJUAN
   PENGIRIMAN`="USA", `PEMBELI`="MCC") and in `PD` (row for the same batch
   `030300-251121-03`, `TUJUAN`="MCC"/"USA"); `DN/G/260120-001` appears in
   both `PACK` (batch `010100-260120-03`, "TANGERANG", "LIBERTA GELATO")
   and `PD` (same batch, "LIBERTA GELATO"/"Tangerang"); `SmpD`'s `EP MCC
   610893` appears in both `SmpD` (batch `030300-251121-03`, "SGS
   VIETNAM"/"VIETNAM") and `PACK` (same batch, "VIETNAM", "SGS VIETNAM").
   The number is therefore assigned once (apparently at Packing time, per
   `Proses.docx`'s own Packing note "Batch number masih sama" /
   `PROJECT_STATUS.md` Fase 11 keputusan #11) and simply re-recorded here,
   verbatim, on the `Shipment` satellite -- this layer never generates or
   derives it, only stores what the caller passes in from the source row.

2. **`TUJUAN` splits into two real sub-columns for both sheets** -- PD:
   `PERUSAHAAN` + `LOKASI`; SmpD: `NAMA` + `ALAMAT`. `Shipment` (per
   `DATABASE_DESIGN.md` §6) already has exactly two fitting fields,
   `recipient` and `destination` -- no schema change needed. Mapping,
   confirmed by cross-referencing `PACK`'s own two shipment-adjacent
   fields for the same shipping numbers: `PACK`'s `PEMBELI` (buyer) values
   match PD's `PERUSAHAAN` values for the same shipping number (e.g.
   `DN/G/260120-001`: `PACK` `PEMBELI`="LIBERTA GELATO" = `PD`
   `PERUSAHAAN`="LIBERTA GELATO"; `LN/G/260128-003`: `PACK`
   `PEMBELI`="MALIK SABYTAEV" ~ `PD` `PERUSAHAAN`="MALIK S/RUSIA") --
   confirming `PERUSAHAAN`/`NAMA` is the buyer/recipient identity, mapped
   to `Shipment.recipient`. `PACK`'s single `TUJUAN PENGIRIMAN` field
   matches PD's `LOKASI` values for the same shipping number (e.g.
   `DN/G/260120-001`: `PACK` `TUJUAN PENGIRIMAN`="TANGERANG" = `PD`
   `LOKASI`="Tangerang"; `LN/G/260128-003`: both "RUSIA"/"Rusia") --
   confirming `LOKASI`/`ALAMAT` is the destination place, mapped to
   `Shipment.destination`.

3. **`NETTO` (net_weight) is derived as `SUM(sources quantities)`, never
   caller-supplied**, and **`TARA` (tare_weight) is derived as
   `gross_weight - net_weight`, never caller-supplied** -- the exact same
   two rules already confirmed for Packing (`vacuum_packing.py` #6/#7),
   re-verified directly against real PD/SmpD rows: PD row 1
   (`010100-260120-03`): `BRUTO`=2.275, `TARA`=0.275, `NETTO`=2 (2.275 -
   0.275 = 2, exact); SmpD row 1 (`030300-251121-03`): `BRUTO`=1.505,
   `TARA`=0.105, `NETTO`=1.4 (exact). `gross_weight` ("BRUTO") is the one
   independently measured value with no sensible default, required from
   the caller -- same reasoning as every other phase's terminal weighed
   figure (Packing's `gross_weight`, Sundrying's `final_qty`).

4. **`sources` is a list (>=1), not hardcoded to exactly one batch** --
   mirrors `PackingInput.sources` exactly (module docstring pattern from
   Fase 11). The overwhelming majority of real PD/SmpD rows carry exactly
   one batch per shipping number, but at least one clear real multi-batch
   case exists: PD's `LN/G/260517-013` (2026-05-17, to "MALIK
   S/RUSIA"/"Rusia") spans two sheet rows -- a header row naming the
   shipment, and a continuation row (blank `NO.`/`TANGGAL`/`NOMOR
   PENGIRIMAN`, i.e. inheriting the same shipment) referencing a second,
   distinct batch (`010100-260502-03` on the header row, `010100-260513-03`
   on the continuation row) -- the same "blank leading fields = same event,
   another source line" shape already used for Mixing's/Packing's
   continuation rows. A resulting negative `tare_weight` is not blocked,
   for the same reason as Packing's (`PROCESS_RULES.md` still lists
   tolerances/edge cases as `[UNCONFIRMED]`).

5. **`[UNCONFIRMED]` -- same-day multi-document grouping is NOT collapsed
   into one event.** A separate real grouping, PD's 2026-06-06 rows (sheet
   `NO.`=13), shows FOUR DIFFERENT distinct shipping-number strings
   (`DN/EG/260606-014`, `DN/G/260606-014` twice, `DN/GP/260606-014`,
   `DN/P/260606-014`) sharing one sheet-row sequence index and date --
   unlike the `LN/G/260517-013` case above, each of these rows carries its
   OWN distinct shipping number rather than inheriting a blank one. Per
   REQUIREMENTS.md's PD field group ("shipping number, batch" -- singular),
   and because this module treats `shipping_number` (not the sheet's `NO.`
   column) as the row-grouping key, each such row is modeled as its own
   separate `record_delivery()` call / `Shipment`, not merged into one
   multi-batch event. Whether PT JAS actually intends these four same-day,
   same-truck-run deliveries as logically separate shipment documents (as
   modeled here) or as one physical delivery recorded with redundant
   per-product-type document numbers is left open for a future
   confirmation -- documented per CLAUDE.md rule 11 rather than guessed.

6. **`DESKRIPSI VANILA` (SmpD only, e.g. "EP", "EG", "GOURMET", "POWDER")
   is recorded in `ProcessEvent.notes`**, not mapped onto any `Batch`/
   `Shipment` column -- identical free-text-descriptor treatment to
   Mixing's/Vacuum's/Packing's product descriptions (`mixing.py` #8,
   `vacuum_packing.py` #4/#11). `DeliveryInput.description` is accepted for
   both Delivery and Sample Delivery (nothing in the model restricts it to
   one event type), even though only the SmpD field group names it.

7. **`customer_id` is an optional caller-supplied FK, left `None` by
   default -- no Customer-matching/lookup is performed by this module.**
   `GENEALOGY.md` §6 assigns the `Customer` FK to `Shipment` specifically
   at this layer (unlike Vacuum/Packing's buyer-as-notes-only treatment,
   `vacuum_packing.py` #4/#11, since "the Customer link belongs to
   Shipment/Delivery, Fase 12" -- PROJECT_STATUS.md Fase 11 keputusan #4).
   However, `Customer` is still an empty, unpopulated master-data table in
   this codebase (`REQUIREMENTS.md` lists "Supplier/Customer master data"
   as in-scope but not yet built out) -- resolving a free-text company name
   like "LIBERTA GELATO" or "MALIK S/RUSIA" to a specific `Customer` row
   (exact/fuzzy matching, or first creating that row) is a data-import/UI
   concern this engine layer cannot invent a rule for. `customer_id`
   therefore stays an optional explicit parameter (int, resolved by
   whatever caller eventually implements that matching), defaulting
   `None`; `Shipment.recipient` (the free-text `PERUSAHAAN`/`NAMA` value)
   is always recorded regardless of whether `customer_id` is set -- this
   already matches `DATABASE_DESIGN.md` §6's own documented behavior
   ("recipient used when customer_id is null"), which in practice today is
   *every* real row, since nothing populates `Customer` yet.
   **`[UNCONFIRMED]`**: the Customer matching/creation workflow itself,
   added to PROJECT_STATUS.md's open questions.

8. **A fully-consumed INPUT batch is promoted from the generic engine's
   `CONSUMED` to the more specific `SHIPPED` status.** `GENEALOGY.md` §3.2
   explicitly names `SHIPPED` as the terminal status for a batch "fully
   consumed by a Delivery/Sample Delivery event" (forward trace ends
   there), distinct from `CONSUMED` (used generically for any batch fully
   consumed as input to a *later processing* event -- Sortation, Mixing,
   Grinding, Rework, Packing). `record_process_event()` itself cannot know
   which specific terminal label a zero-balance battach deserves -- this is
   exactly why `tests/test_events.py::test_delivery_creates_stock_out`'s
   own comment defers "terminal 'shipped' handling ... to caller/UI (Fase
   12)". This module is that caller: after `record_process_event()`
   returns, any INPUT batch left at `CONSUMED` (i.e. fully depleted by this
   event) is promoted to `SHIPPED`. A batch only *partially* delivered
   (remaining `current_quantity` > 0) correctly stays untouched at
   `ACTIVE` -- still on hand, not shipped out.

9. **Reconciliation/shrinkage do not apply** -- `EventType.DELIVERY` and
   `EventType.SAMPLE_DELIVERY` are both in `NO_OUTPUT_EVENT_TYPES`
   (`enums.py`, since Fase 3), which the generic engine's own
   `_check_reconciliation()` exempts by construction (`GENEALOGY.md` §5 --
   "not applicable to ... Delivery", `DATABASE_DESIGN.md` §2). This module
   therefore never passes `shrinkage_qty`/`loss_qty`.

10. **`is_sample` stock-ledger tagging needs no new code** -- already
    generic since Fase 3 (`services/events.py`:
    `is_sample=(event_type == EventType.SAMPLE_DELIVERY)` on every
    `StockTransaction`). `record_delivery()`/`record_sample_delivery()`
    only need to pass the right `event_type` through.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..enums import BatchStatus, EventType
from ..models import Batch, ProcessEvent, Shipment
from .events import InputSpec, record_process_event

ZERO = Decimal("0")


@dataclass
class DeliverySource:
    batch_id: int
    quantity: Decimal  # PD/SmpD "NETTO" contribution for this batch -- required, see module docstring #3/#4


@dataclass
class DeliveryInput:
    event_date: dt.date  # PD/SmpD "TANGGAL"
    pic_user_id: int
    sources: list[DeliverySource]  # PD/SmpD "BATCH NO." (+ per-source NETTO) -- see #4
    gross_weight: Decimal  # PD/SmpD "BRUTO" -- required, see #3
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    shipping_number: Optional[str] = None  # PD/SmpD "NOMOR PENGIRIMAN" -- reused from Packing, see #1
    destination: Optional[str] = None  # PD "LOKASI" / SmpD "ALAMAT" -- see #2
    recipient: Optional[str] = None  # PD "PERUSAHAAN" / SmpD "NAMA" -- see #2
    customer_id: Optional[int] = None  # optional FK, see #7
    expedition: Optional[str] = None  # "JENIS EKSPEDISI"
    transport_condition: Optional[str] = None  # "KONDISI ANGKUT"
    packaging_condition: Optional[str] = None  # "KONDISI KEMASAN"
    coly: Optional[int] = None  # "QTY BOX (COLY)"
    description: Optional[str] = None  # SmpD "DESKRIPSI VANILA" -- notes only, see #6


def _shipment_notes(data: DeliveryInput) -> Optional[str]:
    if data.description is None:
        return None
    return json.dumps({"description": data.description}, ensure_ascii=False)


def _record_shipment(session: Session, data: DeliveryInput, event_type: EventType) -> ProcessEvent:
    if not data.sources:
        raise ValueError(f"{event_type.value} requires at least one source batch (module docstring #4).")

    batch_ids = [s.batch_id for s in data.sources]
    if len(set(batch_ids)) != len(batch_ids):
        raise ValueError(f"{event_type.value} sources must not repeat the same batch_id more than once.")

    for s in data.sources:
        if s.quantity <= 0:
            raise ValueError(f"{event_type.value} source quantity for batch_id={s.batch_id} must be positive.")

    if data.gross_weight <= 0:
        raise ValueError(f"{event_type.value} gross weight (BRUTO) must be positive.")

    net_weight = sum((s.quantity for s in data.sources), ZERO)  # derived -- see #3
    tare_weight = data.gross_weight - net_weight  # derived -- see #3

    inputs = [
        InputSpec(batch_id=s.batch_id, quantity=s.quantity, unit=data.unit) for s in data.sources
    ]

    event = record_process_event(
        session,
        event_type=event_type,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=inputs,
        outputs=[],
        notes=_shipment_notes(data),
    )

    session.add(
        Shipment(
            event_id=event.event_id,
            shipping_number=data.shipping_number,
            destination=data.destination,
            expedition=data.expedition,
            transport_condition=data.transport_condition,
            packaging_condition=data.packaging_condition,
            coly=data.coly,
            gross_weight=data.gross_weight,
            tare_weight=tare_weight,
            net_weight=net_weight,
            customer_id=data.customer_id,
            recipient=data.recipient,
        )
    )

    # module docstring #8: promote fully-consumed inputs from the generic
    # engine's CONSUMED to the more specific terminal SHIPPED status.
    for s in data.sources:
        batch = session.get(Batch, s.batch_id)
        if batch is not None and batch.status == BatchStatus.CONSUMED:
            batch.status = BatchStatus.SHIPPED

    session.flush()
    return event


def record_delivery(session: Session, data: DeliveryInput) -> ProcessEvent:
    """Record a Product Delivery event (`EventType.DELIVERY`): 1..N source
    batches consumed as stock OUT, no output batch (`NO_OUTPUT_EVENT_TYPES`
    since Fase 3). `net_weight`/`tare_weight` are both derived -- see
    module docstring #3. Fully-depleted source batches end up `SHIPPED`,
    not `CONSUMED` -- see #8.
    """
    return _record_shipment(session, data, EventType.DELIVERY)


def record_sample_delivery(session: Session, data: DeliveryInput) -> ProcessEvent:
    """Record a Sample Delivery event (`EventType.SAMPLE_DELIVERY`): same
    shape as `record_delivery()`, but every resulting `StockTransaction` is
    tagged `is_sample=True` generically by `record_process_event()` (Fase
    3) -- see module docstring #10.
    """
    return _record_shipment(session, data, EventType.SAMPLE_DELIVERY)
