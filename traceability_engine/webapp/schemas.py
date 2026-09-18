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


class CustomerCreate(BaseModel):
    name: str = Field(..., max_length=200)


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    customer_id: int
    name: str


class CustomerMatchOut(BaseModel):
    """Fase 21 -- one ranked suggestion from `services/customer_matching.py`.
    Suggestion-only; the caller decides whether/what to do with it (module
    docstring #1) -- this schema carries no side effect. `matched_alias` is
    set when this hit came from a confirmed `CustomerAlias` rather than
    `Customer.name` itself or a fuzzy score (module docstring #5)."""

    customer_id: int
    name: str
    score: float
    exact: bool
    matched_alias: Optional[str] = None


class CustomerAliasCreate(BaseModel):
    alias: str = Field(..., max_length=200)


class CustomerAliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    alias_id: int
    customer_id: int
    alias: str


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
    # Packaging attributes -- only ever populated for batch_type=PACKAGED
    # (Packing, Fase 15 slice 3) and Vacuum's plastic-line notes stay on the
    # ProcessEvent, not the Batch (services/vacuum_packing.py #1/#3). Pure
    # read-side exposure of existing Batch columns, no new rule here.
    plastic_size: Optional[str] = None
    plastic_lot: Optional[str] = None
    plastic_qty: Optional[Decimal] = None
    carton_lot: Optional[str] = None
    gross_weight: Optional[Decimal] = None
    tare_weight: Optional[Decimal] = None
    net_weight: Optional[Decimal] = None


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


# ------------------------------- curing / airdrying (Hijau route, Fase 19 engine / Fase 20 UI)
# Same field shape for all five (services/curing.py CuringStageInput, one
# shared dataclass) -- five separate schema classes anyway, one per
# EventType/business concept, matching the Magnetization/MDPowder
# convention (routers/powder.py) rather than a single reused class.
class MainCuringCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal
    starting_quantity: Optional[Decimal] = None
    unit: str = "kg"
    duration: Optional[str] = None  # free text, unit varies -- curing.py #4 [UNCONFIRMED]
    condition_notes: Optional[str] = None  # curing.py #5 [UNCONFIRMED]


class FirstCuringCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal
    starting_quantity: Optional[Decimal] = None
    unit: str = "kg"
    duration: Optional[str] = None
    condition_notes: Optional[str] = None


class SecondCuringCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal
    starting_quantity: Optional[Decimal] = None
    unit: str = "kg"
    duration: Optional[str] = None
    condition_notes: Optional[str] = None


class ThirdCuringCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal
    starting_quantity: Optional[Decimal] = None
    unit: str = "kg"
    duration: Optional[str] = None
    condition_notes: Optional[str] = None


class AirdryingCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal
    starting_quantity: Optional[Decimal] = None
    unit: str = "kg"
    duration: Optional[str] = None
    condition_notes: Optional[str] = None


# ------------------------------------------------------------------ sortation
class SortationCreate(BaseModel):
    event_date: dt.date  # SORT "start date"
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    initial_qty: Optional[Decimal] = None  # default: batch on-hand -- services/sortation.py #7
    unit: str = "kg"
    end_date: Optional[dt.date] = None  # SORT "end date" -- notes only, sortation.py #8
    gourmet_qty: Optional[Decimal] = None
    eg_qty: Optional[Decimal] = None
    ep_qty: Optional[Decimal] = None
    nc_qty: Optional[Decimal] = None  # "Non Conform" -- sortation.py #2
    powder_qty: Optional[Decimal] = None
    process_code: str = "00"  # "00" Original / "01" Upgrade / "02" Downgrade -- sortation.py #5


class SortationResult(BaseModel):
    event: ProcessEventOut
    batches: list[BatchOut]  # one per grade quantity > 0 supplied -- ONE->MANY, sortation.py #1


# ---------------------------------------------------------------------- mixing
class MixingSourceCreate(BaseModel):
    batch_id: int
    quantity: Decimal  # MIX "QTY ASAL (KG)" for this source -- required per source, mixing.py #10


class MixingCreate(BaseModel):
    event_date: dt.date  # MIX "TANGGAL"
    event_time: Optional[dt.time] = None
    pic_user_id: int
    sources: list[MixingSourceCreate] = Field(..., min_length=2)  # mixing.py #9
    final_qty: Decimal  # MIX "QTY AKHIR (KG)" -- required, mixing.py #3
    product_description: str  # MIX "DESKRIPSI PRODUK" -- notes only, mixing.py #8
    unit: str = "kg"
    grade_code: Optional[str] = None  # explicit override only, never inferred -- mixing.py #8
    jenis_code: Optional[str] = None  # never inherited automatically -- mixing.py #4
    supplier_id: Optional[int] = None  # default: unattributable -- mixing.py #5
    supplier_code: str = "000"  # see mixing.py #5
    batch_type: Literal["RAW_KERING", "RAW_HIJAU", "PROCESSED", "POWDER", "PACKAGED"] = "PROCESSED"


class MixingResult(BaseModel):
    event: ProcessEventOut
    batch: BatchOut  # single combined output batch -- MANY->ONE


# --------------------------------------------------------------------- powder
class GrindingCreate(BaseModel):
    event_date: dt.date  # grind "TANGGAL"
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int  # grind "BATCH NUMBER ASAL" (source NC batch) -- named batch_id here to match
    # the generic Proses form's single-batch selector (see app.js STAGE_DEFS);
    # the router maps it onto services.powder.GrindingInput.nc_batch_id.
    final_qty: Decimal  # grind "QTY AKHIR POWDER (KG)" -- required, powder.py #3
    starting_qty: Optional[Decimal] = None  # default: batch on-hand -- powder.py #2
    unit: str = "kg"
    result_date: Optional[dt.date] = None  # grind "TANGGAL HASIL" -- notes only, powder.py #1
    process_code: Optional[str] = None  # [UNCONFIRMED] -- powder.py #7


class GrindingResult(BaseModel):
    event: ProcessEventOut
    batch: BatchOut  # newly minted Powder batch -- ONE->NEW-BATCH


class MagnetizationCreate(BaseModel):
    event_date: dt.date  # MG "TANGGAL"
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None  # default: batch on-hand -- powder.py #10
    unit: str = "kg"
    finding: Optional[str] = None  # MG "TEMUAN"
    notes: Optional[str] = None  # MG "KETERANGAN"


class MDPowderCreate(BaseModel):
    event_date: dt.date  # MDPW "TANGGAL"
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None  # default: batch on-hand -- powder.py #10
    unit: str = "kg"
    finding: Optional[str] = None  # MDPW "TEMUAN"
    notes: Optional[str] = None  # MDPW "KETERANGAN"


# --------------------------------------------------------------------- rework
class ReworkCreate(BaseModel):
    event_date: dt.date  # REW "TANGGAL"
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int  # REW "BATCH NUMBER"
    starting_qty: Optional[Decimal] = None  # default: batch on-hand -- rework.py #6
    unit: str = "kg"
    process_description: Optional[str] = None  # REW "KETERANGAN PROSES" -- notes only, rework.py #8
    gourmet_qty: Optional[Decimal] = None
    eg_qty: Optional[Decimal] = None
    ep_qty: Optional[Decimal] = None
    nc_qty: Optional[Decimal] = None  # "Non Conform" -- sortation.py #2


class ReworkResult(BaseModel):
    event: ProcessEventOut
    batches: list[BatchOut]  # one per grade quantity > 0 supplied -- ONE->MANY, rework.py


# --------------------------------------------------------------- vacuum/packing
class VacuumPlasticLineCreate(BaseModel):
    total_weight: Decimal  # required, drives event quantity -- vacuum_packing.py #2
    plastic_size: Optional[str] = None
    plastic_lot: Optional[str] = None
    plastic_qty: Optional[Decimal] = None
    weight_per_pack: Optional[Decimal] = None


class VacuumCreate(BaseModel):
    event_date: dt.date  # VC "TANGGAL"
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int  # self-loop, no new batch minted -- vacuum_packing.py #1
    plastic_lines: list[VacuumPlasticLineCreate] = Field(..., min_length=1)  # #3
    unit: str = "kg"
    product_description: Optional[str] = None  # VC "DESKRIPSI PRODUK" -- notes only, #4
    buyer: Optional[str] = None  # VC "BUYER" -- notes only, #4


class PackingSourceCreate(BaseModel):
    batch_id: int
    quantity: Decimal  # PACK "BERAT (KG)" for this source line -- required, #5


class PackingCreate(BaseModel):
    event_date: dt.date  # PACK "TANGGAL"
    event_time: Optional[dt.time] = None
    pic_user_id: int
    sources: list[PackingSourceCreate] = Field(..., min_length=1)  # #5
    gross_weight: Decimal  # PACK "BRUTO" -- required, net/tare derived server-side, #6/#7
    unit: str = "kg"
    plastic_size: Optional[str] = None  # PACK "UKURAN PLASTIK VACCUM" -- #8
    plastic_lot: Optional[str] = None  # PACK "LOT NO. PLASTIK VACUM"
    plastic_qty: Optional[Decimal] = None  # PACK "QTY PLASTIK VACUM"
    carton_lot: Optional[str] = None  # PACK "LOT NO. KARTON"
    carton_qty: Optional[Decimal] = None  # PACK "QTY KARTON (COLY)" -- notes only, #9
    envelope_qty: Optional[Decimal] = None  # PACK "AMPLOP" -- notes only, #9
    shipping_number: Optional[str] = None  # PACK "NOMOR PENGIRIMAN" -- notes only, #10
    destination: Optional[str] = None  # PACK "TUJUAN PENGIRIMAN" -- notes only, #10
    product_description: Optional[str] = None  # PACK "DESKRIPSI PRODUK" -- notes only, #11
    buyer: Optional[str] = None  # PACK "PEMBELI" -- notes only, #11
    grade_code: Optional[str] = None  # inherited if single-source, else explicit only -- #12
    jenis_code: Optional[str] = None
    supplier_id: Optional[int] = None
    supplier_code: Optional[str] = None
    receiving_date: Optional[dt.date] = None
    process_code: Optional[str] = None  # [UNCONFIRMED] -- #13
    batch_type: Literal["RAW_KERING", "RAW_HIJAU", "PROCESSED", "POWDER", "PACKAGED"] = "PACKAGED"


class PackingResult(BaseModel):
    event: ProcessEventOut
    batch: BatchOut  # single new PACKAGED batch -- ONE-or-MANY->ONE


# ------------------------------------------------------------------ delivery (12)
class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    shipment_id: int
    event_id: int
    shipping_number: Optional[str] = None
    destination: Optional[str] = None
    expedition: Optional[str] = None
    transport_condition: Optional[str] = None
    packaging_condition: Optional[str] = None
    coly: Optional[int] = None
    gross_weight: Optional[Decimal] = None
    tare_weight: Optional[Decimal] = None
    net_weight: Optional[Decimal] = None
    customer_id: Optional[int] = None
    recipient: Optional[str] = None


class DeliverySourceCreate(BaseModel):
    batch_id: int
    quantity: Decimal  # PD/SmpD "NETTO" contribution for this batch -- required per source, delivery.py #3/#4


class DeliveryCreate(BaseModel):
    event_date: dt.date  # PD/SmpD "TANGGAL"
    event_time: Optional[dt.time] = None
    pic_user_id: int
    sources: list[DeliverySourceCreate] = Field(..., min_length=1)  # delivery.py #4
    gross_weight: Decimal  # PD/SmpD "BRUTO" -- required, net/tare derived server-side, delivery.py #3
    unit: str = "kg"
    shipping_number: Optional[str] = None  # PD/SmpD "NOMOR PENGIRIMAN" -- reused from Packing, #1
    destination: Optional[str] = None  # PD "LOKASI" / SmpD "ALAMAT" -- #2
    recipient: Optional[str] = None  # PD "PERUSAHAAN" / SmpD "NAMA" -- #2
    customer_id: Optional[int] = None  # optional FK, no matching performed here -- #7
    expedition: Optional[str] = None  # "JENIS EKSPEDISI"
    transport_condition: Optional[str] = None  # "KONDISI ANGKUT"
    packaging_condition: Optional[str] = None  # "KONDISI KEMASAN"
    coly: Optional[int] = None  # "QTY BOX (COLY)"
    description: Optional[str] = None  # SmpD "DESKRIPSI VANILA" -- notes only, #6


class DeliveryResult(BaseModel):
    event: ProcessEventOut  # NO_OUTPUT_EVENT_TYPES -- no output batch, delivery.py module docstring
    shipment: ShipmentOut


# ----------------------------------------------------------------- stock (13)
class StockTransactionOut(BaseModel):
    transaction_id: int
    batch_id: int
    event_id: int
    direction: str
    quantity: Decimal
    balance_after: Decimal
    is_sample: bool
    created_at: dt.datetime


class BatchBalanceOut(BaseModel):
    batch_id: int
    status: str
    cached_quantity: Decimal
    ledger_quantity: Decimal
    matches: bool  # False here is a real book discrepancy, never expected in normal operation -- stock.py module docstring


class StockSummaryRowOut(BaseModel):
    supplier_id: Optional[int] = None
    supplier_code: Optional[str] = None
    supplier_name: Optional[str] = None
    jenis_code: Optional[str] = None
    grade_code: Optional[str] = None
    batch_count: int
    total_quantity: Decimal


class StockTotalOut(BaseModel):
    total_quantity: Decimal  # ACTIVE batches only -- stock.py module docstring


# ----------------------------------------------------------- traceability (14)
class TraceBatchSummaryOut(BaseModel):
    batch_id: int
    batch_number: Optional[str] = None
    status: str
    current_quantity: Decimal


class TraceSupplierOut(BaseModel):
    supplier_id: int
    supplier_code: str
    name: str


class TraceShipmentOut(BaseModel):
    shipment_id: int
    shipping_number: Optional[str] = None
    customer_name: Optional[str] = None
    recipient: Optional[str] = None
    destination: Optional[str] = None


class TraceEventSummaryOut(BaseModel):
    event_id: int
    event_type: str
    event_date: dt.date
    pic: Optional[str] = None
    notes: Optional[str] = None
    total_input_quantity: Optional[Decimal] = None
    total_output_quantity: Optional[Decimal] = None


class ChainOfCustodyOut(BaseModel):
    """Mirrors services/traceability.py's `chain_of_custody_report()` dict
    shape field-for-field -- no new report logic at this layer, only a
    typed response contract over what the engine already assembled."""

    batch: TraceBatchSummaryOut
    suppliers: list[TraceSupplierOut] = []
    upstream_events: list[TraceEventSummaryOut] = []
    downstream_events: list[TraceEventSummaryOut] = []
    shipments: list[TraceShipmentOut] = []
    incomplete_leaves: list[TraceBatchSummaryOut] = []


# ---------------------------------------------------------------- adjustment
class AdjustmentCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    batch_id: int
    new_quantity: Decimal  # corrected on-hand qty -- server computes the delta, never accepted here
    actor_user_id: int  # must be role PRODUCTION_MANAGER -- enforced server-side, adjustment.py
    notes: str = Field(..., min_length=1)  # mandatory reason, adjustment.py


class RejectCreate(BaseModel):
    actor_user_id: int
    reason: str = Field(..., min_length=1)


class SupersedeCreate(BaseModel):
    actor_user_id: int
    reason: str = Field(..., min_length=1)


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    audit_id: int
    entity_type: str
    entity_id: int
    action: str
    actor_user_id: int
    timestamp: dt.datetime
    before_value: Optional[str] = None
    after_value: Optional[str] = None


# ------------------------------------------------------------------- errors
class ErrorOut(BaseModel):
    error: str
    detail: str
