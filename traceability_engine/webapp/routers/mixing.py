"""Mixing (MIX) endpoint -- thin wrapper over `services.mixing.record_mixing`.
MANY->ONE: N source batches (>=2, no repeats, per-source quantity required)
combined into one newly-minted batch -- `cp_qty` and `shrinkage_qty` are
both derived server-side, never accepted from the client (services/mixing.py
docstring #1/#2). This is the one slice-2 stage that does NOT fit the
generic single-batch "Input Proses" selector used by QC/MD/Steam/Dry/
Sortation/Grinding/Magnetization/MD-Powder -- the frontend gives it its own
form with a dynamic source-batch list, see static/app.js.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...enums import BatchType, LinkRole
from ...services.mixing import MixingInput, MixingSource, record_mixing
from ..database import get_db
from ..schemas import MixingCreate, MixingResult
from ..serializers import batch_to_out, event_to_out

router = APIRouter(tags=["mixing"])


@router.post("/mixing", response_model=MixingResult, status_code=201)
def create_mixing(payload: MixingCreate, db: Session = Depends(get_db)):
    data = MixingInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        sources=[MixingSource(batch_id=s.batch_id, quantity=s.quantity) for s in payload.sources],
        final_qty=payload.final_qty,
        product_description=payload.product_description,
        unit=payload.unit,
        grade_code=payload.grade_code,
        jenis_code=payload.jenis_code,
        supplier_id=payload.supplier_id,
        supplier_code=payload.supplier_code,
        batch_type=BatchType(payload.batch_type),
        auto_batch_number=payload.auto_batch_number,
    )
    event = record_mixing(db, data)
    db.flush()
    # MIXING: N INPUT links (the sources) + exactly one OUTPUT link (the new
    # combined batch, services/mixing.py -- MANY->ONE) -- filter by role
    # rather than indexing, since relationship load order is not guaranteed.
    output_link = next(link for link in event.links if link.role == LinkRole.OUTPUT)
    return MixingResult(event=event_to_out(db, event), batch=batch_to_out(output_link.batch))
