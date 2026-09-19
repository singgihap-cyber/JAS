"""QC Test and Metal Detection endpoints -- thin wrappers over
`services.qc_md.record_qc_test` / `record_metal_detection`. No accept/reject
rule is applied here; see qc_md.py's module docstring #1.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...enums import QCStage
from ...services.qc_md import (
    MetalDetectionInput,
    QCTestInput,
    record_metal_detection,
    record_qc_test,
)
from ..database import get_db
from ..schemas import MetalDetectionCreate, ProcessEventOut, QCTestCreate
from ..serializers import event_to_out

router = APIRouter(tags=["quality"])


@router.post("/qc-tests", response_model=ProcessEventOut, status_code=201)
def create_qc_test(payload: QCTestCreate, db: Session = Depends(get_db)):
    data = QCTestInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        stage=QCStage(payload.stage),
        quantity=payload.quantity,
        unit=payload.unit,
        sample_received_date=payload.sample_received_date,
        sample_weight=payload.sample_weight,
        ka_1=payload.ka_1,
        ka_2=payload.ka_2,
        ka_3=payload.ka_3,
        aw=payload.aw,
        finding=payload.finding,
        method_temperature=payload.method_temperature,
        product_description=payload.product_description,
    )
    event = record_qc_test(db, data)
    db.flush()
    return event_to_out(db, event)


@router.post("/metal-detections", response_model=ProcessEventOut, status_code=201)
def create_metal_detection(payload: MetalDetectionCreate, db: Session = Depends(get_db)):
    data = MetalDetectionInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        batch_id=payload.batch_id,
        stage=QCStage(payload.stage),
        quantity=payload.quantity,
        unit=payload.unit,
        product_status=payload.product_status,
        finding=payload.finding,
        description=payload.description,
    )
    event = record_metal_detection(db, data)
    db.flush()
    return event_to_out(db, event)
