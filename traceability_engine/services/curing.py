"""Fase 19 (engine) / Fase 22 (correction) -- Hijau (green-curing) route:
Lepas Tangkai (stem removal), Blanching, Main Curing, 1st/2nd/3rd Curing,
Airdrying.

WORKFLOW.md / CLAUDE.md's Hijau route is:
  PB -> Sortation -> Steaming/blanching -> Main Curing -> 1st Curing ->
  2nd Curing -> 3rd Curing -> Sundrying -> Airdrying -> Sortation -> Mixing
  (if any) -> QC KA/AW2 -> MD2 -> Vacuum -> Packing -> Shipping -> Stock
  Monitoring

Fase 22 correction (2026-09-19 Cowork session) -- PT JAS supplied a real
source document, `PROSES HIJAU 2026.xlsx` (sheets `LT`, `PL`, `P INTI`,
`P1`, `P2`, `P3`, `KM`, `KR`), the first field-level data these stages have
ever had. It overturns two of Fase 19's `[UNCONFIRMED]` guesses below (#1
and #3) and fills in the rest (#4/#5) with real fields instead of a
generic Sundrying-shaped guess. Sheet-to-stage mapping, confirmed by Tommy:

  LT (Lepas Tangkai)     -> stem removal, NEW stage, before Blanching
  PL (Pelayuan)          -> Blanching, NEW stage -- NOT Steaming, see #1
  P INTI (Pemeraman Inti)-> Main Curing
  P1 (Pemeraman 1)       -> 1st Curing
  P2 (Pemeraman 2)       -> 2nd Curing
  P3 (Pemeraman 3)       -> 3rd Curing
  KM (Pengeringan)       -> Sundrying (unchanged, reused from steam_dry.py)
  KR (Pengeringan Rak)   -> Airdrying

Decisions, and why (CLAUDE.md rule 11: document ambiguity instead of
guessing):

1. **Blanching is its own event type (`EventType.BLANCHING`), NOT a reuse
   of `EventType.STEAMING`.** Fase 19 originally reused `STEAMING` for this
   stage on the strength of `traceability-trial(1).html`'s `STAGE_DEFS`,
   which models Kering-Steaming and Hijau-Blanching as two `slots` of one
   stage definition. **Tommy corrected this directly (2026-09-19): "Steaming
   hanya digunakan di proses kering. Untuk Hijau setelah lepas tangkai
   langsung ke blanching"** -- i.e. Steaming is Kering-only; Hijau has no
   Steaming step at all, it goes straight from stem removal to Blanching.
   This is a real business-process correction, not a relabeling, so it gets
   a real new `EventType` rather than continuing to overload `STEAMING`.
   Fields (`PL` sheet): starting quantity, `SUHU` (temperature, confirmed
   ~65°C), `LAMA CELUP` (dip duration in **minutes**, confirmed ~2) --
   directly matches the hint values `traceability-trial(1).html` already
   carried for the Hijau blanching slot, just now with a dedicated event
   type instead of a shared one.

2. **Lepas Tangkai (stem removal) is its own new event type
   (`EventType.STEM_REMOVAL`), not a call into `services/sortation.py`.**
   Fase 19 originally assumed this reused generic Sortation
   (`19_HIJAU_CURING.md`: "Sortation, including the Hijau-specific 'stem
   removal' first pass, reuses services/sortation.py unchanged"). The `LT`
   sheet disproves this: the batch number is identical across `LT`, `PL`,
   `P INTI`/`P1`/`P2`/`P3` for every sampled row (e.g. `040018-260505-00`
   unchanged through all of them) -- one input, one output, no grade
   breakdown -- whereas `record_sortation()` always mints at least one new
   output batch per grade (`sortation.py` docstring #1) and requires a
   grade quantity. Lepas Tangkai is a self-loop ONE->ONE with the removed
   stem/waste (`LIMBAH`) as derived shrinkage -- same shape as Sundrying,
   not Sortation. Actual grading into Gourmet/EG/EP/Powder/NC happens later,
   at the `SORT` sheet, well after Airdrying -- that later step is
   unaffected and still uses `record_sortation()`.

3. **Main/1st/2nd/3rd Curing are stock-neutral self-loops (no shrinkage),
   like Steaming -- NOT shrinkage-tracking like Sundrying.** Fase 19
   originally modeled all five ex-`[UNCONFIRMED]` stages (Main/1st/2nd/3rd
   Curing + Airdrying) on Sundrying's starting/final-quantity-with-derived-
   shrinkage shape, in the explicit absence of any source document. The
   `P INTI`/`P1`/`P2`/`P3` sheets disprove the shrinkage assumption for
   these four specifically: every sampled row shows the *identical* weight
   at every one of the four stages (e.g. 75.44kg unchanged across all of
   `P INTI`/`P1`/`P2`/`P3` for one batch) -- there is no "ending weight"
   column in any of these four sheets at all, only a starting weight
   (informational/unchanged) and `LAMA PEMERAMAN` (curing duration, in
   **hours**, confirmed). Weight loss first appears at `KM`
   (Sundrying, already reused unchanged) and again at `KR` (Airdrying).
   So these four stages are corrected to the Steaming shape: a single
   `quantity` (defaults to on-hand), no `final_quantity`, no derived
   shrinkage. `condition_notes` is dropped entirely -- the real form has no
   such field either (only date/batch/starting-weight/duration/PIC).

4. **Airdrying keeps the shrinkage-tracking shape (Fase 19 guess #3 was
   directionally right for this one stage) and gains two real fields from
   the `KR` sheet: `duration_days` (`LAMA (HARI)`, confirmed unit -- days,
   like Sundrying's Hijau-route duration) and `final_ka` (`KA (%)`, the
   final moisture-content reading, not previously modeled at all).** `KR`
   is the only one of the five ex-`[UNCONFIRMED]` stages whose sheet has a
   `BERAT AKHIR` (ending weight) and `SUSUT` (shrinkage) column -- `SUSUT`
   is simply the derived `starting - final` restated, consistent with
   every other self-loop shrinkage field in this codebase (never a
   caller-supplied value, same as Sundrying/Sortation).

5. **No new `Batch.batch_type` value for any of these six stages.** All are
   self-loop events (no new batch minted), so `RAW_HIJAU` (already in
   `BatchType`) continues to describe the batch throughout Lepas Tangkai ->
   ... -> Airdrying; batch_type only changes at a genuine transformation
   (e.g. the later Sortation into grades), unaffected by this phase.

Superseded Fase 19 decisions (kept here only as a pointer, not restated):
old docstring decisions #1 (Steaming/blanching and Sundrying both reuse
existing event types) and #3-5 (all five stages shrinkage-shaped, generic
`condition_notes`) are replaced by #1-4 above. Decision #2 (these are new
event types because no source document existed) and #6 (no new batch_type)
still hold, now with real fields instead of placeholders.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import Batch, ProcessEvent
from .events import InputSpec, OutputSpec, record_process_event

ZERO = Decimal("0")


def _resolve_quantity(session: Session, batch_id: int, quantity: Optional[Decimal]) -> Decimal:
    if quantity is not None:
        return quantity
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"Batch {batch_id} does not exist.")
    return batch.current_quantity


# --------------------------------------------------------------- Lepas Tangkai
@dataclass
class StemRemovalInput:
    """Lepas Tangkai -- stem-removal pass, before Blanching. Self-loop
    ONE->ONE with derived shrinkage (`LIMBAH`, i.e. removed stem/waste
    weight) -- module docstring #2. Same shape as Sundrying/Airdrying."""

    event_date: dt.date
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal  # LT "BERAT AKHIR" -- required, defines limbah/shrinkage
    starting_quantity: Optional[Decimal] = None  # LT "BERAT" -- defaults to on-hand
    unit: str = "kg"
    event_time: Optional[dt.time] = None


def record_stem_removal(session: Session, data: StemRemovalInput) -> ProcessEvent:
    """Record a Lepas Tangkai (stem removal) event: self-loop ONE->ONE
    (`EventType.STEM_REMOVAL`) with `shrinkage_qty` derived as
    `starting_quantity - final_quantity` (the removed stem/waste, `LIMBAH`)
    -- module docstring #2."""
    starting_quantity = _resolve_quantity(session, data.batch_id, data.starting_quantity)
    if starting_quantity <= 0:
        raise ValueError("Stem removal starting quantity must be positive.")
    if data.final_quantity <= 0:
        raise ValueError("Stem removal final quantity must be positive.")

    shrinkage_qty = starting_quantity - data.final_quantity

    return record_process_event(
        session,
        event_type=EventType.STEM_REMOVAL,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=starting_quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=data.final_quantity, unit=data.unit)],
        shrinkage_qty=shrinkage_qty,
    )


# -------------------------------------------------------------------- Blanching
@dataclass
class BlanchingInput:
    """Pelayuan/Blanching -- Hijau-route only, distinct from Kering's
    Steaming (module docstring #1). Self-loop ONE->ONE, stock-neutral (no
    shrinkage), same shape as Steaming (`steam_dry.py`)."""

    event_date: dt.date
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None  # PL "BERAT AWAL" -- defaults to on-hand
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    temperature: Optional[Decimal] = None  # PL "SUHU (C)" -- confirmed ~65 C
    dip_duration_minutes: Optional[Decimal] = None  # PL "LAMA CELUP (MENIT)" -- confirmed ~2

    def to_notes_payload(self) -> dict:
        return {
            "temperature": str(self.temperature) if self.temperature is not None else None,
            "dip_duration_minutes": (
                str(self.dip_duration_minutes) if self.dip_duration_minutes is not None else None
            ),
        }


def _blanching_notes(data: BlanchingInput) -> Optional[str]:
    payload = {k: v for k, v in data.to_notes_payload().items() if v is not None}
    return json.dumps(payload, ensure_ascii=False) if payload else None


def record_blanching(session: Session, data: BlanchingInput) -> ProcessEvent:
    """Record a Blanching event: self-loop ONE->ONE (`EventType.BLANCHING`),
    stock-neutral, same treatment as Steaming -- module docstring #1."""
    quantity = _resolve_quantity(session, data.batch_id, data.quantity)
    if quantity <= 0:
        raise ValueError("Blanching quantity must be positive.")

    return record_process_event(
        session,
        event_type=EventType.BLANCHING,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        notes=_blanching_notes(data),
    )


# ----------------------------------------------- Main / 1st / 2nd / 3rd Curing
@dataclass
class CuringStageInput:
    """Shared shape for Main/1st/2nd/3rd Curing (Pemeraman Inti/1/2/3) --
    stock-neutral self-loop, same shape as Blanching/Steaming -- module
    docstring #3. Corrected in Fase 22: `PROSES HIJAU 2026.xlsx` shows no
    ending-weight column at all for these four stages, only a starting
    weight (unchanged across the four) and a duration in hours."""

    event_date: dt.date
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None  # "BERAT AWAL" -- defaults to on-hand, stock-neutral
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    duration_hours: Optional[Decimal] = None  # "LAMA PEMERAMAN (JAM)" -- confirmed unit


def _curing_stage_notes(data: CuringStageInput) -> Optional[str]:
    if data.duration_hours is None:
        return None
    return json.dumps({"duration_hours": str(data.duration_hours)}, ensure_ascii=False)


def _record_curing_stage(
    session: Session, event_type: EventType, data: CuringStageInput
) -> ProcessEvent:
    quantity = _resolve_quantity(session, data.batch_id, data.quantity)
    if quantity <= 0:
        raise ValueError(f"{event_type.value} quantity must be positive.")

    return record_process_event(
        session,
        event_type=event_type,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        notes=_curing_stage_notes(data),
    )


def record_main_curing(session: Session, data: CuringStageInput) -> ProcessEvent:
    """Main Curing (Pemeraman Inti): self-loop ONE->ONE
    (`EventType.MAIN_CURING`), stock-neutral -- module docstring #3."""
    return _record_curing_stage(session, EventType.MAIN_CURING, data)


def record_first_curing(session: Session, data: CuringStageInput) -> ProcessEvent:
    """1st Curing (Pemeraman 1): self-loop ONE->ONE
    (`EventType.FIRST_CURING`), stock-neutral -- module docstring #3."""
    return _record_curing_stage(session, EventType.FIRST_CURING, data)


def record_second_curing(session: Session, data: CuringStageInput) -> ProcessEvent:
    """2nd Curing (Pemeraman 2): self-loop ONE->ONE
    (`EventType.SECOND_CURING`), stock-neutral -- module docstring #3."""
    return _record_curing_stage(session, EventType.SECOND_CURING, data)


def record_third_curing(session: Session, data: CuringStageInput) -> ProcessEvent:
    """3rd Curing (Pemeraman 3): self-loop ONE->ONE
    (`EventType.THIRD_CURING`), stock-neutral -- module docstring #3."""
    return _record_curing_stage(session, EventType.THIRD_CURING, data)


# ------------------------------------------------------------------ Airdrying
@dataclass
class AirdryingInput:
    """Pengeringan Rak/Airdrying -- the one ex-`[UNCONFIRMED]` stage that
    does carry an ending weight in the real form. Self-loop ONE->ONE with
    derived shrinkage -- module docstring #4."""

    event_date: dt.date
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal  # KR "BERAT AKHIR" -- required, defines shrinkage/SUSUT
    starting_quantity: Optional[Decimal] = None  # KR "BERAT AWAL" -- defaults to on-hand
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    duration_days: Optional[Decimal] = None  # KR "LAMA (HARI)" -- confirmed unit
    final_ka: Optional[Decimal] = None  # KR "KA (%)" -- final moisture content, confirmed field


def _airdrying_notes(data: AirdryingInput) -> Optional[str]:
    payload = {
        "duration_days": str(data.duration_days) if data.duration_days is not None else None,
        "final_ka": str(data.final_ka) if data.final_ka is not None else None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return json.dumps(payload, ensure_ascii=False) if payload else None


def record_airdrying(session: Session, data: AirdryingInput) -> ProcessEvent:
    """Record an Airdrying event: self-loop ONE->ONE (`EventType.AIRDRYING`)
    with `shrinkage_qty` derived as `starting_quantity - final_quantity`
    (`SUSUT`) -- module docstring #4."""
    starting_quantity = _resolve_quantity(session, data.batch_id, data.starting_quantity)
    if starting_quantity <= 0:
        raise ValueError("Airdrying starting quantity must be positive.")
    if data.final_quantity <= 0:
        raise ValueError("Airdrying final quantity must be positive.")

    shrinkage_qty = starting_quantity - data.final_quantity

    return record_process_event(
        session,
        event_type=EventType.AIRDRYING,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=starting_quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=data.final_quantity, unit=data.unit)],
        shrinkage_qty=shrinkage_qty,
        notes=_airdrying_notes(data),
    )
