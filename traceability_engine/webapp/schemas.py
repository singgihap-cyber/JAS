"""Pydantic request/response shapes for the web API.

These are pure serialization/validation shapes (field types, required vs.
optional, enum choices) -- they mirror the dataclasses already defined in
`services/*.py` (ReceivingInput, QCTestInput, ...) field-for-field and add
no rules of their own (no thresholds, no derived accept/reject decisions,
no batch-number generation). Where a `services` dataclass documents a
default (e.g. QC/MD/Steam/Sundrying `quantity` defaulting to the batch's
on-hand balance), the schema leaves the field `Optional` and lets the
service apply that default -- it is not duplicated here.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

QCStageLiteral = Literal["RM", "IP", "FP"]
BatchTypeLiteral = Literal["RAW_KERING", "RAW_HIJAU"]


# ---------------------------------------------------------------- master data
class SupplierCreate(BaseModel):
    supplier_code: str = Field(..., max_length=8)
    name: str = Field(..., max_length=200)


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    supplier_id: int
    supplier_code: str
    name: str
    active: bool


class UserCreate(BaseModel):
    name: str = Field(..., max_length=200)
    role: Literal["PRODUCTION_MANAGER", "STAFF"] = "STAFF"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: int
    name: str
    role: str


# --------------------------------------------------------------------- batch
class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    batch_id: int
    batch_number: Optional[str] = None
    jenis_code: Optional[str] = None
    grade_code: Optional[str] = None
    supplier_code: Optional[str] = None
    receiving_date: Optional[dt.date] = None
    process_code: Optional[str] = None
    batch_type: str
    supplier_id: Optional[int] = None
    status: str
    current_quantity: Decimal
    unit: str
    created_at: dt.datetime


class EventBatchLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    batch_id: int
    role: str
    quantity: Decimal
    unit: str


class QualityTestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    test_id: int
    stage: str
    sample_weight: Optional[Decimal] = None
    ka_1: Optional[Decimal] = None
    ka_2: Optional[Decimal] = None
    ka_3: Optional[Decimal] = None
    aw: Optional[Decimal] = None
    finding: Optional[str] = None
    metal_detection_finding: Optional[str] = None


class ProcessEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    event_id: int
    event_type: str
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    shrinkage_qty: Decimal
    loss_qty: Decimal
    notes: Optional[str] = None
    status: str
    created_at: dt.datetime
    links: list[EventBatchLinkOut] = []
    quality_test: Optional[QualityTestOut] = None


class BatchDetailOut(BatchOut):
    supplier_name: Optional[str] = None
    events: list[ProcessEventOut] = []


# ----------------------------------------------------------------- receiving
class ReceivingCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    supplier_id: int
    batch_type: BatchTypeLiteral
    net_quantity: Decimal
    unit: str = "kg"
    batch_number: Optional[str] = None
    product_description: Optional[str] = None
    packaging_condition: Optional[str] = None
    coly: Optional[int] = None
    gross_weight: Optional[Decimal] = None
    tare_weight: Optional[Decimal] = None
    on_spec_qty: Optional[Decimal] = None
    off_spec_qty: Optional[Decimal] = None
    smell_test: Optional[str] = None


class ReceivingResult(BaseModel):
    event: ProcessEventOut
    batch: BatchOut


# --------------------------------------------------------------------- qc/md
class QCTestCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    stage: QCStageLiteral
    quantity: Optional[Decimal] = None
    unit: str = "kg"
    sample_received_date: Optional[dt.date] = None
    sample_weight: Optional[Decimal] = None
    ka_1: Optional[Decimal] = None
    ka_2: Optional[Decimal] = None
    ka_3: Optional[Decimal] = None
    aw: Optional[Decimal] = None
    finding: Optional[str] = None


class MetalDetectionCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    stage: QCStageLiteral
    quantity: Optional[Decimal] = None
    unit: str = "kg"
    product_status: Optional[str] = None
    finding: Optional[str] = None
    description: Optional[str] = None


# --------------------------------------------------------------- steam / dry
class SteamingCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None
    unit: str = "kg"
    end_time: Optional[dt.time] = None
    pan_count: Optional[int] = None
    water_condition: Optional[str] = None
    pan_condition: Optional[str] = None
    steam_temperature: Optional[Decimal] = None
    verification_reading_1: Optional[Decimal] = None
    verification_reading_2: Optional[Decimal] = None
    verification_reading_3: Optional[Decimal] = None


class SundryingCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal
    starting_quantity: Optional[Decimal] = None
    unit: str = "kg"
    starting_ka: Optional[Decimal] = None
    drying_duration: Optional[str] = None


# ------------------------------------------------------------------- errors
class ErrorOut(BaseModel):
    error: str
    detail: str
