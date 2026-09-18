"""Product Delivery (PD) + Sample Delivery (SmpD) endpoints -- thin wrappers
over `services.delivery.record_delivery` / `record_sample_delivery`. Both
are STOCK-OUT events (`NO_OUTPUT_EVENT_TYPES` since Fase 3): 1..N source
batches consumed, no output batch minted -- so unlike every prior router in
this webapp, the result surfaces a `Shipment` satellite instead of a
`BatchOut`. `net_weight`/`tare_weight` are both derived server-side, never
accepted from the client (services/delivery.py #3). No Customer
matching/lookup happens here either (services/delivery.py #7) -- `customer_id`
is passed through verbatim if the caller supplied one from the (separate,
optional) `/customers` master-data picker.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...enums import EventType
from ...models import ProcessEvent, Shipment
from ...services.delivery import DeliveryInput, DeliverySource, record_delivery, record_sample_delivery
from ..database import get_db
from ..schemas import DeliveryCreate, DeliveryResult
from ..serializers import event_to_out, shipment_to_out

router = APIRouter(tags=["delivery"])


def _to_input(payload: DeliveryCreate) -> DeliveryInput:
    return DeliveryInput(
        event_date=payload.event_date,
        event_time=payload.event_time,
        pic_user_id=payload.pic_user_id,
        sources=[DeliverySource(batch_id=s.batch_id, quantity=s.quantity) for s in payload.sources],
        gross_weight=payload.gross_weight,
        unit=payload.unit,
        shipping_number=payload.shipping_number,
        destination=payload.destination,
        recipient=payload.recipient,
        customer_id=payload.customer_id,
        expedition=payload.expedition,
        transport_condition=payload.transport_condition,
        packaging_condition=payload.packaging_condition,
        coly=payload.coly,
        description=payload.description,
    )


def _shipment_for(db: Session, event_id: int) -> Shipment:
    # One Shipment row per DELIVERY/SAMPLE_DELIVERY event -- record_delivery()/
    # record_sample_delivery() always create exactly one (services/delivery.py).
    return db.execute(select(Shipment).where(Shipment.event_id == event_id)).scalar_one()


@router.post("/delivery", response_model=DeliveryResult, status_code=201)
def create_delivery(payload: DeliveryCreate, db: Session = Depends(get_db)):
    event = record_delivery(db, _to_input(payload))
    db.flush()
    shipment = _shipment_for(db, event.event_id)
    return DeliveryResult(event=event_to_out(db, event), shipment=shipment_to_out(shipment))


@router.post("/sample-delivery", response_model=DeliveryResult, status_code=201)
def create_sample_delivery(payload: DeliveryCreate, db: Session = Depends(get_db)):
    event = record_sample_delivery(db, _to_input(payload))
    db.flush()
    shipment = _shipment_for(db, event.event_id)
    return DeliveryResult(event=event_to_out(db, event), shipment=shipment_to_out(shipment))


_DELIVERY_TYPES = (EventType.DELIVERY, EventType.SAMPLE_DELIVERY)


@router.get("/deliveries", response_model=list[DeliveryResult])
def list_deliveries(
    event_type: Optional[str] = Query(
        None, description="DELIVERY or SAMPLE_DELIVERY -- omit for both"
    ),
    db: Session = Depends(get_db),
):
    """Purpose-built history endpoint, unlike every other stage's generic
    `GET /process-events` (routers/batches.py) -- Delivery/Sample Delivery
    are the one stage whose defining fields (shipping number, destination,
    net/tare weight, customer/recipient) live entirely on the `Shipment`
    satellite, not on `ProcessEventOut` or any output `Batch` (there is no
    output batch, module docstring). A generic event listing alone cannot
    surface those fields, so this endpoint joins ProcessEvent+Shipment,
    same reasoning that gave Stock/Traceability their own report endpoints
    in slice 4.
    """
    types = _DELIVERY_TYPES
    if event_type:
        try:
            requested = EventType(event_type)
        except ValueError:
            raise HTTPException(422, f"Unknown event_type {event_type!r}")
        if requested not in _DELIVERY_TYPES:
            raise HTTPException(422, f"{event_type!r} is not a delivery event type")
        types = (requested,)

    stmt = (
        select(ProcessEvent)
        .where(ProcessEvent.event_type.in_(types))
        .order_by(ProcessEvent.event_date.desc(), ProcessEvent.event_id.desc())
    )
    events = db.execute(stmt).scalars().all()
    results = []
    for event in events:
        shipment = db.execute(
            select(Shipment).where(Shipment.event_id == event.event_id)
        ).scalar_one_or_none()
        if shipment is None:
            continue
        results.append(DeliveryResult(event=event_to_out(db, event), shipment=shipment_to_out(shipment)))
    return results
