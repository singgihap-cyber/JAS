"""ORM -> Pydantic shaping helpers shared by every router.

Pure read-side formatting (no business decisions): pulls the rows the
engine already wrote (`ProcessEvent`, `EventBatchLink`, `QualityTest`,
`Batch`) into the response schemas in `schemas.py`.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Batch, EventBatchLink, ProcessEvent, QualityTest, Supplier
from .schemas import (
    BatchDetailOut,
    BatchOut,
    EventBatchLinkOut,
    ProcessEventOut,
    QualityTestOut,
)


def batch_to_out(batch: Batch) -> BatchOut:
    return BatchOut(
        batch_id=batch.batch_id,
        batch_number=batch.batch_number,
        jenis_code=batch.jenis_code,
        grade_code=batch.grade_code,
        supplier_code=batch.supplier_code,
        receiving_date=batch.receiving_date,
        process_code=batch.process_code,
        batch_type=batch.batch_type.value,
        supplier_id=batch.supplier_id,
        status=batch.status.value,
        current_quantity=batch.current_quantity,
        unit=batch.unit,
        created_at=batch.created_at,
        plastic_size=batch.plastic_size,
        plastic_lot=batch.plastic_lot,
        plastic_qty=batch.plastic_qty,
        carton_lot=batch.carton_lot,
        gross_weight=batch.gross_weight,
        tare_weight=batch.tare_weight,
        net_weight=batch.net_weight,
    )


def _quality_test_for_event(session: Session, event_id: int) -> QualityTestOut | None:
    qt = session.execute(
        select(QualityTest).where(QualityTest.event_id == event_id)
    ).scalar_one_or_none()
    if qt is None:
        return None
    return QualityTestOut(
        test_id=qt.test_id,
        stage=qt.stage.value,
        sample_weight=qt.sample_weight,
        ka_1=qt.ka_1,
        ka_2=qt.ka_2,
        ka_3=qt.ka_3,
        aw=qt.aw,
        finding=qt.finding,
        metal_detection_finding=qt.metal_detection_finding,
    )


def event_to_out(session: Session, event: ProcessEvent) -> ProcessEventOut:
    links = session.execute(
        select(EventBatchLink).where(EventBatchLink.event_id == event.event_id)
    ).scalars().all()
    return ProcessEventOut(
        event_id=event.event_id,
        event_type=event.event_type.value,
        event_date=event.event_date,
        event_time=event.event_time,
        pic_user_id=event.pic_user_id,
        shrinkage_qty=event.shrinkage_qty,
        loss_qty=event.loss_qty,
        notes=event.notes,
        status=event.status.value,
        created_at=event.created_at,
        links=[
            EventBatchLinkOut(
                batch_id=link.batch_id, role=link.role.value, quantity=link.quantity, unit=link.unit
            )
            for link in links
        ],
        quality_test=_quality_test_for_event(session, event.event_id),
    )


def batch_detail_to_out(session: Session, batch: Batch) -> BatchDetailOut:
    supplier_name = None
    if batch.supplier_id is not None:
        supplier = session.get(Supplier, batch.supplier_id)
        supplier_name = supplier.name if supplier else None

    event_ids = session.execute(
        select(EventBatchLink.event_id).where(EventBatchLink.batch_id == batch.batch_id).distinct()
    ).scalars().all()
    events = []
    if event_ids:
        events = session.execute(
            select(ProcessEvent)
            .where(ProcessEvent.event_id.in_(event_ids))
            .order_by(ProcessEvent.event_date, ProcessEvent.event_time, ProcessEvent.event_id)
        ).scalars().all()

    base = batch_to_out(batch)
    return BatchDetailOut(
        **base.model_dump(),
        supplier_name=supplier_name,
        events=[event_to_out(session, e) for e in events],
    )
