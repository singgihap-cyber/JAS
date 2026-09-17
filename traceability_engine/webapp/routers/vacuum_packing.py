"""Vacuum (VC) + Packing (PACK) endpoints -- thin wrappers over
`services.vacuum_packing.record_vacuum` / `record_packing`. Vacuum is a
self-loop ONE->ONE inspection-like event: no new batch minted, event
quantity is `SUM(plastic_lines.total_weight)` derived server-side
(services/vacuum_packing.py #1/#2/#3). Packing is ONE-or-MANY->ONE: mints
one new `batch_type=PACKAGED` batch, with `net_weight`/`tare_weight` both
derived server-side, never accepted from the client (module docstring
"Packing" section, #6/#7). Neither derived value is recomputed here -- both
come back from the engine. No accept/reject rule is applied at this layer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...enums import BatchType, LinkRole
from ...services.vacuum_packing import (
    PackingInput,
    PackingSource,
    VacuumInput,
    VacuumPlasticLine,
    record_packing,
    record_vacuum,
)
from ..database import get_db
from ..schemas import PackingCreate, PackingResult, ProcessEventOut, VacuumCreate
from ..serializers import batch_to_out, event_to_out

router = APIRouter(tags=["vacuum-packing"])


@router.post("/vacuum", response_model=ProcessEventOut, status_code=201)
def create_vacuum(payload: VacuumCreate, db: Session = Depends(get_db)):
    data = VacuumInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        plastic_lines=[
            VacuumPlasticLine(
                total_weight=line.total_weight,
                plastic_size=line.plastic_size,
                plastic_lot=line.plastic_lot,
                plastic_qty=line.plastic_qty,
                weight_per_pack=line.weight_per_pack,
            )
            for line in payload.plastic_lines
        ],
        unit=payload.unit,
        product_description=payload.product_description,
        buyer=payload.buyer,
    )
    event = record_vacuum(db, data)
    db.flush()
    # VACUUM is a self-loop -- INPUT and OUTPUT both point at the same
    # batch (vacuum_packing.py #1), so there is no separate "new batch" to
    # surface, only the event itself.
    return event_to_out(db, event)


@router.post("/packing", response_model=PackingResult, status_code=201)
def create_packing(payload: PackingCreate, db: Session = Depends(get_db)):
    data = PackingInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        sources=[PackingSource(batch_id=s.batch_id, quantity=s.quantity) for s in payload.sources],
        gross_weight=payload.gross_weight,
        unit=payload.unit,
        plastic_size=payload.plastic_size,
        plastic_lot=payload.plastic_lot,
        plastic_qty=payload.plastic_qty,
        carton_lot=payload.carton_lot,
        carton_qty=payload.carton_qty,
        envelope_qty=payload.envelope_qty,
        shipping_number=payload.shipping_number,
        destination=payload.destination,
        product_description=payload.product_description,
        buyer=payload.buyer,
        grade_code=payload.grade_code,
        jenis_code=payload.jenis_code,
        supplier_id=payload.supplier_id,
        supplier_code=payload.supplier_code,
        receiving_date=payload.receiving_date,
        process_code=payload.process_code,
        batch_type=BatchType(payload.batch_type),
    )
    event = record_packing(db, data)
    db.flush()
    # PACKING: N INPUT links (the sources) + exactly one OUTPUT link (the
    # new PACKAGED batch) -- filter by role rather than indexing, same
    # reasoning as routers/mixing.py / "Keputusan Fase 15 (slice 2)" #2.
    output_link = next(link for link in event.links if link.role == LinkRole.OUTPUT)
    return PackingResult(event=event_to_out(db, event), batch=batch_to_out(output_link.batch))
