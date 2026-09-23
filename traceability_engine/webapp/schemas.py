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

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ..batch_number import JENIS_CODES, LEGACY_JENIS_CODES

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

    # Fase 34: label Jenis (AA) untuk UI -- murni turunan dari `jenis_code`,
    # tanpa kolom/aturan baru (batch_number.JENIS_CODES / LEGACY_JENIS_CODES).
    @computed_field  # type: ignore[misc]
    @property
    def jenis_label(self) -> Optional[str]:
        code = self.jenis_code
        return (JENIS_CODES.get(code) or LEGACY_JENIS_CODES.get(code)) if code else None

    @computed_field  # type: ignore[misc]
    @property
    def jenis_is_legacy(self) -> bool:
        return self.jenis_code in LEGACY_JENIS_CODES


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
    end_date: Optional[dt.date] = None  # Fase 48 -- diisi Sortation (services/sortation.py #8); event lain NULL
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
    transport_no: Optional[str] = None  # PPH "NO. ANGKUT" -- Fase 23
    transport_condition: Optional[str] = None  # PPH "KONDISI ANGKUT" -- Fase 23
    jenis_code: Optional[str] = None  # Fase 31: 01 Tahitensis / 02 Planifolia -> nomor batch otomatis
    grade_code: Optional[str] = None  # Fase 31: BB 00-06 (Hijau default 00)


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
    method_temperature: Optional[Decimal] = None  # KW "METODE SUHU" -- Fase 23
    product_description: Optional[str] = None  # KW "DESK.I VANILLA" -- Fase 23


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


# ------------------- Hijau route stages (Fase 19 engine / Fase 20 UI / Fase 22 correction)
# Fase 22 (2026-09-19): PROSES HIJAU 2026.xlsx supplied real fields for
# these stages, correcting several Fase 19/20 guesses -- see
# services/curing.py module docstring for the full explanation.
class StemRemovalCreate(BaseModel):
    """Lepas Tangkai -- self-loop with derived shrinkage (LIMBAH)."""

    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal
    starting_quantity: Optional[Decimal] = None
    unit: str = "kg"


class BlanchingCreate(BaseModel):
    """Pelayuan/Blanching -- Hijau-route only, distinct from Steaming
    (curing.py module docstring #1). Stock-neutral self-loop."""

    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None
    unit: str = "kg"
    temperature: Optional[Decimal] = None
    dip_duration_minutes: Optional[Decimal] = None


# Same field shape for all four (services/curing.py CuringStageInput, one
# shared dataclass) -- four separate schema classes anyway, one per
# EventType/business concept, matching the Magnetization/MDPowder
# convention (routers/powder.py) rather than a single reused class.
# Stock-neutral (no final_quantity/shrinkage) -- curing.py module docstring
# #3, corrected in Fase 22.
class MainCuringCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None
    unit: str = "kg"
    duration_hours: Optional[Decimal] = None  # "LAMA PEMERAMAN (JAM)" -- confirmed unit


class FirstCuringCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None
    unit: str = "kg"
    duration_hours: Optional[Decimal] = None


class SecondCuringCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None
    unit: str = "kg"
    duration_hours: Optional[Decimal] = None


class ThirdCuringCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    quantity: Optional[Decimal] = None
    unit: str = "kg"
    duration_hours: Optional[Decimal] = None


class AirdryingCreate(BaseModel):
    """The one Hijau stage that keeps the shrinkage shape -- plus
    duration_days/final_ka, both confirmed fields from the KR sheet
    (curing.py module docstring #4)."""

    event_date: dt.date
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    final_quantity: Decimal
    starting_quantity: Optional[Decimal] = None
    unit: str = "kg"
    duration_days: Optional[Decimal] = None
    final_ka: Optional[Decimal] = None


# ------------------------------------------------------------------ sortation
class SortationCreate(BaseModel):
    event_date: dt.date  # SORT "start date"
    event_time: Optional[dt.time] = None
    pic_user_id: int
    batch_id: int
    initial_qty: Optional[Decimal] = None  # default: batch on-hand -- services/sortation.py #7
    unit: str = "kg"
    end_date: Optional[dt.date] = None  # SORT "end date" -- kolom resmi ProcessEvent.end_date, sortation.py #8
    gourmet_qty: Optional[Decimal] = None
    eg_qty: Optional[Decimal] = None
    ep_qty: Optional[Decimal] = None
    nc_qty: Optional[Decimal] = None  # "Non Conform" -- sortation.py #2
    powder_qty: Optional[Decimal] = None
    process_code: str = "00"  # "00" Original / "01" Upgrade / "02" Downgrade -- sortation.py #5
    # Fase 23 -- staff-assigned output batch number per grade, sortation.py #9
    gourmet_batch_number: Optional[str] = None
    eg_batch_number: Optional[str] = None
    ep_batch_number: Optional[str] = None
    nc_batch_number: Optional[str] = None
    powder_batch_number: Optional[str] = None
    # Fase 32 -- nomor otomatis PP 01/02 (services/batch_numbering.py)
    auto_batch_number: bool = False
    jenis_code: Optional[str] = None


class SortationResult(BaseModel):
    event: ProcessEventOut
    batches: list[BatchOut]  # one per grade quantity > 0 supplied -- ONE->MANY, sortation.py #1
    warnings: list[str] = []  # Fase 33 -- peringatan pewarisan AA (tidak memblokir)


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
    auto_batch_number: bool = False  # Fase 32 -- wajib jenis_code + grade_code


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
    auto_batch_number: bool = False  # Fase 32 -- PP=04 otomatis
    jenis_code: Optional[str] = None


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
    status: str = "ACTIVE"
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
    # Fase 50: event VOID disaring kecuali include_void=true.
    include_void: bool = False
    voided_origin: bool = False
    void_events_excluded: int = 0


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


# ------------------------------------------------------------ rendemen (Fase 24)
class SortationOutputOut(BaseModel):
    batch_id: int
    batch_number: Optional[str] = None
    grade_code: Optional[str] = None
    quantity: Decimal


class SortationRendemenOut(BaseModel):
    """Mirrors services/rendemen.py `SortationRendemen` field-for-field.
    `rendemen` is the SORT sheet's ratio (kg raw per kg sorted output), not a percent; `raw_weight`/`rendemen` are null when lineage is incomplete."""

    event_id: int
    event_date: dt.date
    input_batch_id: int
    input_batch_number: Optional[str] = None
    input_quantity: Decimal
    output_quantity: Decimal
    shrinkage_qty: Decimal
    raw_weight: Optional[Decimal] = None
    rendemen: Optional[Decimal] = None
    yield_percent: Optional[Decimal] = None
    complete: bool
    outputs: list[SortationOutputOut] = []


class MixingSourceRowOut(BaseModel):
    batch_id: int
    batch_number: Optional[str] = None
    quantity: Decimal


class MixingRendemenOut(BaseModel):
    """Mirrors services/rendemen.py `MixingRendemen` field-for-field (Fase 35).
    `rendemen` = output / total input sumber (fraksi, <= 1 bila ada susut);
    arahnya kebalikan dari rendemen Sortasi."""

    event_id: int
    event_date: dt.date
    input_quantity: Decimal
    output_quantity: Decimal
    shrinkage_qty: Decimal
    rendemen: Optional[Decimal] = None
    yield_percent: Optional[Decimal] = None
    output_batch_id: Optional[int] = None
    output_batch_number: Optional[str] = None
    source_count: int
    sources: list[MixingSourceRowOut] = []


# ------------------------------------------------- urutan tanggal (Fase 25)
class DateOrderViolationOut(BaseModel):
    """Mirrors services/date_order.py `DateOrderViolation` field-for-field."""

    batch_id: int
    batch_number: Optional[str] = None
    event_id: Optional[int] = None
    event_type: Optional[str] = None
    event_date: dt.date
    prior_event_id: int
    prior_event_type: str
    prior_event_date: dt.date
    days_early: int
    exempt: bool = False
    message: str


# ------------------------------------------- perubahan AA rute (Fase 36)
class AaChangeFindingOut(BaseModel):
    """Mirrors services/aa_chain.py `AaChangeFinding` field-for-field."""

    event_id: int
    event_type: str
    event_date: dt.date
    source_batch_id: int
    source_batch_number: Optional[str] = None
    source_jenis_code: str
    source_jenis_label: str
    result_batch_id: int
    result_batch_number: Optional[str] = None
    result_jenis_code: str
    result_jenis_label: str
    hijau_route: bool
    message: str
    # Fase 42: status tinjau
    review_status: str = "BARU"
    review_note: Optional[str] = None
    reviewed_by: Optional[int] = None
    reviewed_at: Optional[dt.datetime] = None


class AaFindingReviewIn(BaseModel):
    event_id: int
    source_batch_id: int
    result_batch_id: int
    status: str  # DITINJAU | DIABAIKAN
    actor_user_id: int  # harus PRODUCTION_MANAGER -- ditegakkan server
    note: str = Field(..., min_length=1)


class AaFindingReviewOut(BaseModel):
    review_id: int
    event_id: int
    source_batch_id: int
    result_batch_id: int
    status: str
    reviewed_by: int
    reviewed_at: Optional[dt.datetime] = None
    note: Optional[str] = None

    model_config = {"from_attributes": True}


class BatchNumberCorrectionIn(BaseModel):
    new_batch_number: str = Field(..., min_length=1)
    actor_user_id: int  # harus PRODUCTION_MANAGER -- ditegakkan server
    reason: str = Field(..., min_length=1)


class BatchNumberCorrectionOut(BaseModel):
    correction_id: int
    batch_id: int
    old_batch_number: Optional[str] = None
    new_batch_number: str
    reason: str
    actor_user_id: int
    corrected_at: Optional[dt.datetime] = None
    notice: Optional[str] = None  # hanya pada respons koreksi: ganti label/nomor lama yang sudah tercetak

    model_config = {"from_attributes": True}


# ------------------------------------------- disposisi REJECTED (Fase 26)
class SupplierReturnCreate(BaseModel):
    event_date: dt.date
    event_time: Optional[dt.time] = None
    actor_user_id: int  # harus PRODUCTION_MANAGER -- ditegakkan server, services/disposition.py
    reason: str = Field(..., min_length=1)
    quantity: Optional[Decimal] = None  # kosong = seluruh stok batch


class DispositionRowOut(BaseModel):
    """Mirrors services/disposition.py `DispositionRow` field-for-field."""

    batch_id: int
    batch_number: Optional[str] = None
    supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None
    rejected_at: Optional[dt.datetime] = None
    reject_reason: Optional[str] = None
    quantity_on_hand: Decimal
    returned_quantity: Decimal
    disposition: str
    return_event_ids: list[int]
    used_after_rejection_event_ids: list[int]
    message: str


# ------------------------------------------- status retur supplier (Fase 38)
class SupplierReturnConfirm(BaseModel):
    received_date: dt.date  # tanggal supplier menerima barang retur
    actor_user_id: int  # Production Manager atau PIC Receiving batch itu (Fase 40; selain itu 403)
    note: Optional[str] = None


class SupplierReturnBulkConfirm(BaseModel):
    """Fase 41 -- konfirmasi massal (hanya Production Manager, satu tanggal terima)."""

    event_ids: list[int]
    received_date: dt.date
    actor_user_id: int
    note: Optional[str] = None


class SupplierReturnCancel(BaseModel):
    """Fase 40 -- batalkan konfirmasi 'diterima' (hanya Production Manager)."""

    actor_user_id: int
    reason: str


class SupplierReturnHistoryOut(BaseModel):
    history_id: int
    return_event_id: int
    action: str  # CONFIRMED | CANCELLED
    received_date: Optional[dt.date] = None
    actor_user_id: int
    occurred_at: Optional[dt.datetime] = None
    note: Optional[str] = None

    model_config = {"from_attributes": True}


class SupplierReturnReceiptOut(BaseModel):
    receipt_id: int
    return_event_id: int
    received_date: dt.date
    confirmed_by: int
    confirmed_at: Optional[dt.datetime] = None
    note: Optional[str] = None

    model_config = {"from_attributes": True}


class SupplierReturnRowOut(BaseModel):
    """Mirrors services/supplier_return.py `SupplierReturnRow` field-for-field."""

    event_id: int
    event_date: dt.date
    batch_id: Optional[int] = None
    batch_number: Optional[str] = None
    supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None
    quantity: Decimal
    unit: str
    source: str
    reason: Optional[str] = None
    status: str
    received_date: Optional[dt.date] = None
    confirmed_by: Optional[int] = None
    note: Optional[str] = None
    days_outstanding: Optional[int] = None
    overdue: bool = False  # Fase 39


class SupplierReturnRemindersOut(BaseModel):
    """Fase 39 -- mirrors services/supplier_return.py `SupplierReturnReminders`."""

    threshold_days: int
    count: int
    oldest_days: Optional[int] = None
    total_quantity: Decimal
    message: str
    items: list[SupplierReturnRowOut]


# ------------------------------------------- koreksi & pembatalan event historis (Fase 44)
class CorrectableEventOut(BaseModel):
    """Mirrors services/event_correction.py `CorrectableEventRow` field-for-field."""

    event_id: int
    event_type: str
    event_date: dt.date
    status: str
    batch_ids: list[int]
    batch_numbers: list[Optional[str]]
    notes: Optional[str] = None  # Fase 46


class EventDateCorrectionIn(BaseModel):
    new_event_date: dt.date
    actor_user_id: int  # harus PRODUCTION_MANAGER -- ditegakkan server
    reason: str = Field(..., min_length=1)


class EventDateCorrectionOut(BaseModel):
    correction_id: int
    event_id: int
    old_event_date: dt.date
    new_event_date: dt.date
    reason: str
    actor_user_id: int
    corrected_at: Optional[dt.datetime] = None

    model_config = {"from_attributes": True}


class EventCancelIn(BaseModel):
    actor_user_id: int  # harus PRODUCTION_MANAGER -- ditegakkan server
    reason: str = Field(..., min_length=1)


class EventCancellationOut(BaseModel):
    cancellation_id: int
    event_id: int
    event_type: str
    reason: str
    actor_user_id: int
    cancelled_at: Optional[dt.datetime] = None

    model_config = {"from_attributes": True}


class EventHistoryOut(BaseModel):
    """Mirrors services/event_correction.py `EventHistoryEntry` field-for-field."""

    kind: str  # DATE_CORRECTION | CANCELLATION | QUANTITY_CORRECTION | SHRINKAGE_CORRECTION | NOTES_CORRECTION
    occurred_at: dt.datetime
    actor_user_id: int
    reason: str
    detail: str


# ------------------------------------------- koreksi kuantitas + rantai blocking (Fase 45)
class EventLinkOut(BaseModel):
    """Mirrors services/event_correction.py `EventLinkRow` field-for-field."""

    link_id: int
    batch_id: int
    batch_number: Optional[str]
    role: str
    quantity: Decimal
    unit: str


class EventQuantityCorrectionIn(BaseModel):
    link_id: int
    new_quantity: Decimal
    actor_user_id: int  # harus PRODUCTION_MANAGER -- ditegakkan server
    reason: str = Field(..., min_length=1)


class EventQuantityCorrectionOut(BaseModel):
    correction_id: int
    event_id: int
    link_id: int
    batch_id: int
    role: str
    old_quantity: Decimal
    new_quantity: Decimal
    reason: str
    actor_user_id: int
    corrected_at: Optional[dt.datetime] = None
    # Fase 49 -- susut yang ikut menyesuaikan otomatis (None bila tipe event
    # tidak direkonsiliasi, mis. RECEIVING/DELIVERY).
    old_shrinkage_qty: Optional[Decimal] = None
    new_shrinkage_qty: Optional[Decimal] = None
    warnings: list[str] = []

    model_config = {"from_attributes": True}


class BlockingChainOut(BaseModel):
    """Mirrors services/event_correction.py `BlockingChainResult` field-for-field."""

    event_id: int
    blocked: bool
    chain: list[CorrectableEventOut]


# ------------------------------------------- koreksi susut/loss + audit keseimbangan (Fase 49)
class ShrinkageTransferIn(BaseModel):
    new_shrinkage_qty: Decimal
    new_loss_qty: Decimal
    actor_user_id: int  # harus PRODUCTION_MANAGER -- ditegakkan server
    reason: str = Field(..., min_length=1)


class ShrinkageCorrectionOut(BaseModel):
    correction_id: int
    event_id: int
    source: str  # AUTO_QUANTITY | TRANSFER
    quantity_correction_id: Optional[int] = None
    old_shrinkage_qty: Decimal
    new_shrinkage_qty: Decimal
    old_loss_qty: Decimal
    new_loss_qty: Decimal
    reason: str
    actor_user_id: int
    corrected_at: Optional[dt.datetime] = None

    model_config = {"from_attributes": True}


class ShrinkageRebalanceIn(BaseModel):
    """Fase 50 -- susut = SUM(input) - SUM(output) - loss."""
    actor_user_id: int  # harus PRODUCTION_MANAGER -- ditegakkan server
    reason: str = Field(..., min_length=1)
    batch_id: Optional[int] = None  # hanya dipakai endpoint massal


class ShrinkageRebalanceOut(BaseModel):
    correction: ShrinkageCorrectionOut
    warnings: list[str] = []


class UnbalancedEventOut(BaseModel):
    """Mirrors services/event_correction.py `UnbalancedEventRow` field-for-field."""

    event_id: int
    event_type: str
    event_date: dt.date
    batch_ids: list[int]
    batch_numbers: list[Optional[str]]
    sum_input: Decimal
    sum_output: Decimal
    shrinkage_qty: Decimal
    loss_qty: Decimal
    difference: Decimal
    has_downstream: bool


# ------------------------------------------- koreksi catatan + jenis/cascade (Fase 46)
class EventNotesCorrectionIn(BaseModel):
    new_notes: Optional[str] = None  # kosong = hapus catatan
    actor_user_id: int  # harus PRODUCTION_MANAGER -- ditegakkan server
    reason: str = Field(..., min_length=1)


class EventNotesCorrectionOut(BaseModel):
    correction_id: int
    event_id: int
    old_notes: Optional[str] = None
    new_notes: Optional[str] = None
    reason: str
    actor_user_id: int
    corrected_at: Optional[dt.datetime] = None

    model_config = {"from_attributes": True}


class JenisPlanRowOut(BaseModel):
    """Mirrors services/jenis_correction.py `JenisPlanRow`."""

    batch_id: int
    batch_number: Optional[str] = None
    new_batch_number: Optional[str] = None
    via_event_id: Optional[int] = None
    via_event_type: Optional[str] = None
    note: str = ""

    model_config = {"from_attributes": True}


class JenisCorrectionPlanOut(BaseModel):
    batch_id: int
    old_jenis_code: str
    new_jenis_code: str
    ok: bool
    changes: list[JenisPlanRowOut]
    stops: list[JenisPlanRowOut]
    blockers: list[JenisPlanRowOut]

    model_config = {"from_attributes": True}


class JenisCorrectionIn(BaseModel):
    new_jenis_code: str
    actor_user_id: int  # harus PRODUCTION_MANAGER -- ditegakkan server
    reason: str = Field(..., min_length=1)


class JenisCorrectionOut(BaseModel):
    correction_id: int
    batch_id: int
    old_jenis_code: str
    new_jenis_code: str
    changed_batch_ids: str
    stopped_summary: Optional[str] = None
    reason: str
    actor_user_id: int
    corrected_at: Optional[dt.datetime] = None
    notice: Optional[str] = None
    plan: Optional[JenisCorrectionPlanOut] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------- auth (Fase 47)
class LoginIn(BaseModel):
    username: str = Field(..., max_length=100)
    password: str = Field(..., max_length=200)


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: int
    name: str
    role: str
    must_change_password: bool = False


class SetCredentialsIn(BaseModel):
    username: str = Field(..., max_length=100)
    password: str = Field(..., min_length=6, max_length=200)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(..., max_length=200)
    new_password: str = Field(..., min_length=6, max_length=200)
