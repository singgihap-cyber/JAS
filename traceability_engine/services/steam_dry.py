"""Fase 6 -- Steaming (STEAM) and Sundrying (SD).

Wires the real STEAM/SD source fields (REQUIREMENTS.md field groups) to the
generic engine's `record_process_event(event_type=STEAMING/SUNDRYING, ...)`
as a self-loop ONE->ONE event (same batch as input and output -- the shape
already established and tested in Fase 3, see
`tests/test_events.py::test_one_to_one_inspection_is_self_loop_and_stock_neutral`
and `test_one_to_one_with_shrinkage_reduces_stock`, and GENEALOGY.md §3.1).
Per 03_CORE_ENGINE.md/PROJECT_STATUS.md, Fase 6 only needs this
field-mapping layer -- genealogy/stock/reconciliation side effects are
already handled generically by `record_process_event()` since Fase 3.
Unlike QC/MD (Fase 5), neither STEAM nor SD writes a satellite table -- no
document reserves one for them (DATABASE_DESIGN.md §4 only covers
`QualityTest`), so this module only builds the event + notes.

Decisions made in this phase, and why (CLAUDE.md rule 11: document
ambiguity instead of guessing):

1. **Steaming is stock-neutral; only Sundrying carries `shrinkage_qty`.**
   REQUIREMENTS.md's STEAM field group lists a single `qty` (no
   starting/final split), while SD explicitly lists both `starting qty` and
   `final qty` plus `shrinkage`. This matches PROJECT_STATUS.md's Fase 6
   task framing ("event ONE->ONE self-loop dengan shrinkage_qty terisi
   untuk Sundrying") and CLAUDE.md's known workflow (steaming is a heat
   treatment step; drying is where moisture loss/shrinkage is tracked).

2. **Sundrying's `shrinkage` field is derived (`starting_quantity -
   final_quantity`), not a separate caller-supplied value.** The source
   mockup (`traceability-trial(1).html`, `sundrying` stage) defines
   "Penyusutan (kg) - Auto" as `compute: v => berat_awal - berat_hasil`,
   i.e. shrinkage is always computed from the two weights, never entered
   independently. Deriving it here also keeps the self-loop's quantity
   reconciliation (GENEALOGY.md §5) exact by construction, without
   inventing a shrinkage-tolerance rule -- PROCESS_RULES.md explicitly
   lists "shrinkage tolerances" as `[UNCONFIRMED]`. A resulting negative
   `shrinkage_qty` (final > starting) is not blocked here for the same
   reason `record_qc_test`/`record_metal_detection` evaluate no threshold
   (qc_md.py docstring #1) -- flagging an implausible weight *gain* during
   drying as an error would be inventing a business rule this phase has no
   source document for.

3. **`quantity`/`starting_quantity` default to the batch's on-hand
   quantity when the caller doesn't supply one, exactly like Fase 5's
   `_resolve_quantity` (qc_md.py docstring #2)** -- REQUIREMENTS.md's STEAM
   `qty` and SD `starting qty` don't state whether the whole batch is
   always steamed/dried at once, so a caller can still pass an explicit
   smaller value for a documented partial-batch run.

4. **Non-generalizing fields go to `ProcessEvent.notes` as structured
   JSON**, matching the established pattern (DATABASE_DESIGN.md §2; same
   treatment as Fase 4 PB weighing detail and Fase 5 QT/MD fields):
   STEAM's pan count, water condition, pan condition, steam temperature,
   the three verification readings, and end time; SD's starting KA and
   drying duration.

5. **STEAM's "start/end time" maps `event_time` to start time; end time has
   no dedicated column** (`ProcessEvent` has one `event_time`, same
   limitation noted for QT's `sample_received_date` in Fase 5) -- so end
   time is recorded in `notes` instead.

6. **SD's `drying_duration` is stored as free text, not a numeric
   column.** Per the source mockup, its unit genuinely differs by flow
   (Kering: minutes-per-session x frequency; Hijau: whole days) --
   REQUIREMENTS.md's generic SD field group doesn't fix one unit, so
   inventing a single numeric column (and which unit it's in) would be
   guessing. The caller/UI passes whatever string representation the
   specific form uses (e.g. "60 menit x 3" or "14 hari").

7. **SD only maps `event_date` to the source "start date"** -- REQUIREMENTS.md's
   SD field group does not name a separate end/completion date, so no
   second date field is invented here.
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
class SteamingInput:
    event_date: dt.date  # STEAM "date"
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None  # STEAM "qty" -- see module docstring #3
    unit: str = "kg"
    event_time: Optional[dt.time] = None  # STEAM "start time"
    end_time: Optional[dt.time] = None  # STEAM "end time" -- notes only, see #5
    pan_count: Optional[int] = None  # STEAM "pan"
    water_condition: Optional[str] = None
    pan_condition: Optional[str] = None
    steam_temperature: Optional[Decimal] = None
    verification_reading_1: Optional[Decimal] = None  # STEAM "three verification readings"
    verification_reading_2: Optional[Decimal] = None
    verification_reading_3: Optional[Decimal] = None


def _steam_notes(data: SteamingInput) -> Optional[str]:
    verification_readings = [
        str(v)
        for v in (
            data.verification_reading_1,
            data.verification_reading_2,
            data.verification_reading_3,
        )
        if v is not None
    ]
    payload = {
        "pan_count": data.pan_count,
        "water_condition": data.water_condition,
        "pan_condition": data.pan_condition,
        "steam_temperature": str(data.steam_temperature) if data.steam_temperature is not None else None,
        "verification_readings": verification_readings or None,
        "end_time": data.end_time.isoformat() if data.end_time is not None else None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return json.dumps(payload, ensure_ascii=False) if payload else None


def record_steaming(session: Session, data: SteamingInput) -> ProcessEvent:
    """Record a Steaming event: self-loop ONE->ONE (`EventType.STEAMING`),
    stock-neutral (no shrinkage -- see module docstring #1).
    """
    quantity = _resolve_quantity(session, data.batch_id, data.quantity)
    if quantity <= 0:
        raise ValueError("Steaming quantity must be positive.")

    return record_process_event(
        session,
        event_type=EventType.STEAMING,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=quantity, unit=data.unit)],
        notes=_steam_notes(data),
    )


@dataclass
class SundryingInput:
    event_date: dt.date  # SD "start date" -- see module docstring #7
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal  # SD "final qty" -- required, defines shrinkage (#2)
    starting_quantity: Optional[Decimal] = None  # SD "starting qty" -- see module docstring #3
    unit: str = "kg"
    event_time: Optional[dt.time] = None
    starting_ka: Optional[Decimal] = None  # SD "starting KA" -- recorded only, notes
    drying_duration: Optional[str] = None  # SD "drying duration" -- free text, see #6


def _sd_notes(data: SundryingInput) -> Optional[str]:
    payload = {
        "starting_ka": str(data.starting_ka) if data.starting_ka is not None else None,
        "drying_duration": data.drying_duration,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return json.dumps(payload, ensure_ascii=False) if payload else None


def record_sundrying(session: Session, data: SundryingInput) -> ProcessEvent:
    """Record a Sundrying event: self-loop ONE->ONE (`EventType.SUNDRYING`)
    with `shrinkage_qty` derived as `starting_quantity - final_quantity`
    (module docstring #2) -- the pattern tested in Fase 3
    `test_one_to_one_with_shrinkage_reduces_stock`.
    """
    starting_quantity = _resolve_quantity(session, data.batch_id, data.starting_quantity)
    if starting_quantity <= 0:
        raise ValueError("Sundrying starting quantity must be positive.")
    if data.final_quantity <= 0:
        raise ValueError("Sundrying final quantity must be positive.")

    shrinkage_qty = starting_quantity - data.final_quantity

    return record_process_event(
        session,
        event_type=EventType.SUNDRYING,
        event_date=data.event_date,
        event_time=data.event_time,
        pic_user_id=data.pic_user_id,
        inputs=[InputSpec(batch_id=data.batch_id, quantity=starting_quantity, unit=data.unit)],
        outputs=[OutputSpec(batch_id=data.batch_id, quantity=data.final_quantity, unit=data.unit)],
        shrinkage_qty=shrinkage_qty,
        notes=_sd_notes(data),
    )
