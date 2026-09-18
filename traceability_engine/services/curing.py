"""Fase 19 -- Hijau (green-curing) route: Main Curing, 1st/2nd/3rd Curing,
Airdrying.

WORKFLOW.md / CLAUDE.md's Hijau route is:
  PB -> Sortation -> Steaming/blanching -> Main Curing -> 1st Curing ->
  2nd Curing -> 3rd Curing -> Sundrying -> Airdrying -> Sortation -> Mixing
  (if any) -> QC KA/AW2 -> MD2 -> Vacuum -> Packing -> Shipping -> Stock
  Monitoring

This module only covers the five stages with no Kering equivalent and no
existing service: Main Curing, 1st Curing, 2nd Curing, 3rd Curing,
Airdrying. It deliberately does NOT re-implement Steaming/blanching or
Sundrying -- see decision #1 below -- and does not touch Sortation, which
is already generic across both routes (services/sortation.py).

Decisions made in this phase, and why (CLAUDE.md rule 11: document
ambiguity instead of guessing):

1. **Steaming/blanching and Sundrying in the Hijau route reuse
   `EventType.STEAMING`/`EventType.SUNDRYING` (Fase 6, `steam_dry.py`)
   unchanged -- they are NOT new event types, and this module does not
   define them.** `traceability-trial(1).html`'s `STAGE_DEFS` (the
   project's UI-mockup source document, CLAUDE.md source-of-truth list)
   defines exactly one `steaming` stage and one `sundrying` stage, each
   with two `slots` -- one per route -- and a `byAlur` block that only
   changes hint text/units (Kering steams ~96-97°C/15min; Hijau blanches
   ~60-65°C/2min; Kering's Sundrying duration is minutes×frequency, Hijau's
   is whole days -- this second point was already independently confirmed
   in Fase 6, `steam_dry.py` module docstring #6, before this phase existed
   and without reference to the Hijau route at all). One stage definition
   serving both routes is direct evidence these are the same *kind* of
   event recorded on the same genealogy self-loop shape, not two different
   business processes -- so `record_steaming()`/`record_sundrying()` are
   reused as-is; only the caller (future UI) passes different notes/duration
   values for a Hijau-route batch. Introducing separate `BLANCHING`/
   `HIJAU_SUNDRYING` event types would duplicate an already-generic shape
   for no confirmed reason.

2. **Main Curing / 1st / 2nd / 3rd Curing / Airdrying are new event types,
   each its own self-loop ONE->ONE stage (same shape as Sundrying), because
   no source document defines fields for them at all.** CLAUDE.md already
   flags this explicitly: "The current operational workbook does not
   contain dedicated sheets for all historical green-curing stages. Treat
   those as [UNCONFIRMED] until verified." `traceability-trial(1).html`
   lists all five by name in `ALUR_HIJAU` but never gives any of them a
   `STAGE_DEFS` entry (unlike `steaming`/`sundrying`, which both have full
   field lists) -- confirming the gap is real, not an oversight in this
   session's research. Per explicit user instruction (2026-09-18 Cowork
   session, "Rancang dari pola tahap sejenis"): fields below are modeled on
   the closest already-confirmed analogous stage (Sundrying) rather than
   invented from nothing, and are marked `[UNCONFIRMED]` so they are easy
   to find and revise once PT JAS provides real field data / source sheets
   for these stages specifically.

3. **Each of the five stages carries the same shape as Sundrying:
   `final_quantity` required, `starting_quantity` optional (defaults to
   the batch's on-hand quantity, `steam_dry.py`'s `_resolve_quantity`
   pattern), `shrinkage_qty` derived as `starting_quantity -
   final_quantity`, no tolerance/threshold enforced.** Vanilla curing is a
   multi-day fermentation/moisture-loss process at every one of these
   sub-stages (general domain knowledge, not sourced from a PT JAS
   document -- flagged `[UNCONFIRMED]` for that reason), so weight loss at
   each stage is plausible the same way Sundrying's is documented
   (SD "starting qty and final qty, shrinkage" -- REQUIREMENTS.md). A
   stage that in practice has zero shrinkage still works correctly here:
   passing `final_quantity == starting_quantity` derives `shrinkage_qty =
   0`, exactly like Steaming's stock-neutral self-loop.

4. **`duration` is free text, not a numeric column, matching SD's
   `drying_duration` (`steam_dry.py` module docstring #6) for the same
   reason: no source document fixes a unit for these stages, and inventing
   one (days? hours?) would be guessing.** `[UNCONFIRMED]`.

5. **`condition_notes` is one generic free-text field per stage (stored in
   `ProcessEvent.notes` as JSON, matching the established non-generalizing-
   field pattern -- `steam_dry.py` module docstring #4), not a set of typed
   columns.** Real vanilla curing commonly tracks temperature/humidity/wrap
   condition at sweating stages, but inventing specific field names here
   without a source document would be guessing exactly the kind of detail
   CLAUDE.md rule 11 says to avoid -- a single free-text field lets the
   caller/UI record whatever PT JAS actually tracks once confirmed, without
   a schema change. `[UNCONFIRMED]` -- likely candidate for typed columns
   once real field data exists.

6. **No new `Batch.batch_type` value.** These are self-loop events (no new
   batch minted), so `RAW_HIJAU` (already in `BatchType`) continues to
   describe the batch throughout Sortation -> ... -> Airdrying; batch_type
   only changes at a genuine transformation (e.g. Sortation output grading),
   unaffected by this phase.
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


@dataclass
class CuringStageInput:
    """Shared shape for all five Hijau curing/airdrying stages -- see
    module docstring #3/#4/#5."""

    event_date: dt.date
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal  # required -- defines shrinkage, see #3
    starting_quantity: Optional[Decimal] = None  # defaults to on-hand, see #3
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    duration: Optional[str] = None  # free text, unit varies -- see #4 [UNCONFIRMED]
    condition_notes: Optional[str] = None  # see #5 [UNCONFIRMED]


def _stage_notes(data: CuringStageInput) -> Optional[str]:
    payload = {
        "duration": data.duration,
        "condition_notes": data.condition_notes,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return json.dumps(payload, ensure_ascii=False) if payload else None


def _record_curing_stage(
    session: Session, event_type: EventType, data: CuringStageInput
) -> ProcessEvent:
    starting_quantity = _resolve_quantity(session, data.batch_id, data.starting_quantity)
    if starting_quantity <= 0:
        raise ValueError(f"{event_type.value} starting quantity must be positive.")
    if data.final_quantity <= 0:
        raise ValueError(f"{event_type.value} final quantity must be positive.")

    shrinkage_qty = starting_quantity - data.final_quantity

    return record_process_event(
        session,
        event_type=event_type,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=starting_quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=data.final_quantity, unit=data.unit)],
        shrinkage_qty=shrinkage_qty,
        notes=_stage_notes(data),
    )


def record_main_curing(session: Session, data: CuringStageInput) -> ProcessEvent:
    """Main Curing: self-loop ONE->ONE (`EventType.MAIN_CURING`) with
    derived shrinkage -- see module docstring #2/#3."""
    return _record_curing_stage(session, EventType.MAIN_CURING, data)


def record_first_curing(session: Session, data: CuringStageInput) -> ProcessEvent:
    """1st Curing: self-loop ONE->ONE (`EventType.FIRST_CURING`) with
    derived shrinkage -- see module docstring #2/#3."""
    return _record_curing_stage(session, EventType.FIRST_CURING, data)


def record_second_curing(session: Session, data: CuringStageInput) -> ProcessEvent:
    """2nd Curing: self-loop ONE->ONE (`EventType.SECOND_CURING`) with
    derived shrinkage -- see module docstring #2/#3."""
    return _record_curing_stage(session, EventType.SECOND_CURING, data)


def record_third_curing(session: Session, data: CuringStageInput) -> ProcessEvent:
    """3rd Curing: self-loop ONE->ONE (`EventType.THIRD_CURING`) with
    derived shrinkage -- see module docstring #2/#3."""
    return _record_curing_stage(session, EventType.THIRD_CURING, data)


def record_airdrying(session: Session, data: CuringStageInput) -> ProcessEvent:
    """Airdrying: self-loop ONE->ONE (`EventType.AIRDRYING`) with derived
    shrinkage -- see module docstring #2/#3."""
    return _record_curing_stage(session, EventType.AIRDRYING, data)
