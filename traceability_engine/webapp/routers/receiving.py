"""Receiving (PB) endpoint -- thin wrapper over
`services.receiving.record_receiving`. See services/receiving.py's module
docstring for the confirmed decisions this defers to (manual/nullable
batch_number, batch_type as required caller input, one Batch created for
the full net quantity, on/off-spec recorded but not split into batches).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...enums import BatchType
from ...services.receiving import ReceivingInput, record_receiving
from ..database import get_db
from ..schemas import ReceivingCreate, ReceivingResult
from ..serializers import batch_to_out, event_to_out

router = APIRouter(tags=["receiving"])


@router.post("/receiving", response_model=ReceivingResult, status_code=201)
def create_receiving(payload: ReceivingCreate, db: Session = Depends(get_db)):
    data = ReceivingInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        supplier_id=payload.supplier_id,
        batch_type=BatchType(payload.batch_type),
        net_quantity=payload.net_quantity,
        unit=payload.unit,
        batch_number=payload.batch_number,
        product_description=payload.product_description,
        packaging_condition=payload.packaging_condition,
        coly=payload.coly,
        gross_weight=payload.gross_weight,
        tare_weight=payload.tare_weight,
        on_spec_qty=payload.on_spec_qty,
        off_spec_qty=payload.off_spec_qty,
        smell_test=payload.smell_test,
        transport_no=payload.transport_no,
        transport_condition=payload.transport_condition,
        jenis_code=payload.jenis_code,
        grade_code=payload.grade_code,
    )
    event = record_receiving(db, data)
    db.flush()
    batch = event.links[0].batch  # RECEIVING: exactly one OUTPUT link, see events.py
    return ReceivingResult(event=event_to_out(db, event), batch=batch_to_out(batch))
