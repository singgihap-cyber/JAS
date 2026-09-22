"""SQLAlchemy 2.0 models for the PT JAS traceability engine.

Implements the entities fixed in DATABASE_DESIGN.md (Phase 2): Batch,
ProcessEvent, EventBatchLink, QualityTest, StockTransaction, Shipment,
Supplier, Customer, User, AuditLog.

Only SQLAlchemy-portable types are used (no Postgres-only constructs) so the
exact same model code runs against SQLite in-memory (tests) and PostgreSQL
(production) per the stack decision in PROJECT_STATUS.md (2026-09-14).
Quantities use Numeric (not Float) so reconciliation equality checks are
exact, never subject to floating-point drift.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .enums import (
    AuditAction,
    BatchStatus,
    BatchType,
    EventStatus,
    EventType,
    LinkRole,
    QCStage,
    TransactionDirection,
    UserRole,
)

QTY = Numeric(18, 3)  # kg, 3 decimal places -- matches source workbook precision


def _enum_column(enum_cls, length: int):
    """Store as VARCHAR (not a native DB enum type) so SQLite (tests) and
    PostgreSQL (production) behave identically, while still round-tripping
    to real Python enum members (unlike a plain String column)."""
    return SAEnum(enum_cls, native_enum=False, length=length, validate_strings=True)


class Base(DeclarativeBase):
    pass


class Supplier(Base):
    """Master data. `supplier_code` is stored width-agnostic (DATABASE_DESIGN.md
    §7/8): zero-padding to 2 or 3 digits happens only when assembling/parsing a
    batch_number string, never here."""

    __tablename__ = "suppliers"

    supplier_id: Mapped[int] = mapped_column(primary_key=True)
    supplier_code: Mapped[str] = mapped_column(String(8), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class CustomerAlias(Base):
    """Fase 21 (lanjutan) -- a confirmed synonym/variant name for a
    `Customer`, e.g. PT JAS confirming "MALIK S/RUSIA" (PD's recorded
    recipient name) and "MALIK SABYTAEV" (Packing's recorded buyer name,
    `services/delivery.py` #2) are the same real customer. This is a
    human-confirmed identity claim, not a guess -- distinct from the
    fuzzy-similarity suggestions in `services/customer_matching.py`, a
    match via an alias is treated with the same certainty as matching
    `Customer.name` itself (`customer_matching.py` module docstring #5).
    No DB-level uniqueness constraint on `alias` text here (SQLite/
    PostgreSQL portability -- see `_enum_column()` note above for the same
    reasoning applied elsewhere in this file); conflict checking is done at
    the service layer (`services/customer_matching.add_customer_alias()`)
    instead, consistent with "keep business logic separate from
    persistence" (CLAUDE.md)."""

    __tablename__ = "customer_aliases"

    alias_id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.customer_id"), index=True)
    alias: Mapped[str] = mapped_column(String(200))

    customer: Mapped["Customer"] = relationship()


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(_enum_column(UserRole, 30), default=UserRole.STAFF)


class Batch(Base):
    __tablename__ = "batches"

    batch_id: Mapped[int] = mapped_column(primary_key=True)

    # Business label -- stored both as the assembled string (nullable: the
    # batch-number generator is NOT implemented yet, see batch_number.py /
    # PROJECT_STATUS.md blocker) and as parsed components, per
    # DATABASE_DESIGN.md §1.
    batch_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    jenis_code: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    grade_code: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    supplier_code: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    receiving_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    process_code: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)

    batch_type: Mapped[BatchType] = mapped_column(_enum_column(BatchType, 20))
    supplier_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("suppliers.supplier_id"), nullable=True
    )
    status: Mapped[BatchStatus] = mapped_column(_enum_column(BatchStatus, 20), default=BatchStatus.ACTIVE)

    current_quantity: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    unit: Mapped[str] = mapped_column(String(10), default="kg")

    created_from_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("process_events.event_id"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.user_id"), nullable=True)

    # Packaging-only attributes (batch_type == PACKAGED)
    plastic_size: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    plastic_lot: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    plastic_qty: Mapped[Optional[Decimal]] = mapped_column(QTY, nullable=True)
    carton_lot: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    gross_weight: Mapped[Optional[Decimal]] = mapped_column(QTY, nullable=True)
    tare_weight: Mapped[Optional[Decimal]] = mapped_column(QTY, nullable=True)
    net_weight: Mapped[Optional[Decimal]] = mapped_column(QTY, nullable=True)

    supplier: Mapped[Optional["Supplier"]] = relationship()
    created_from_event: Mapped[Optional["ProcessEvent"]] = relationship(
        foreign_keys=[created_from_event_id]
    )


class ProcessEvent(Base):
    __tablename__ = "process_events"

    event_id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[EventType] = mapped_column(_enum_column(EventType, 20))
    event_date: Mapped[dt.date] = mapped_column(Date)
    event_time: Mapped[Optional[dt.time]] = mapped_column(Time, nullable=True)

    pic_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))

    shrinkage_qty: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    loss_qty: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[EventStatus] = mapped_column(_enum_column(EventStatus, 10), default=EventStatus.COMPLETED)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.user_id"), nullable=True)

    links: Mapped[list["EventBatchLink"]] = relationship(
        back_populates="event", foreign_keys="EventBatchLink.event_id"
    )
    pic: Mapped["User"] = relationship(foreign_keys=[pic_user_id])


class EventBatchLink(Base):
    """Genealogy edge table (GENEALOGY.md §2/§3). Indexed on (batch_id, role)
    and (event_id, role) per DATABASE_DESIGN.md §3 -- traversal and
    reconciliation both filter on these."""

    __tablename__ = "event_batch_links"
    __table_args__ = (
        UniqueConstraint("event_id", "batch_id", "role", name="uq_event_batch_role"),
    )

    link_id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("process_events.event_id"), index=True
    )
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.batch_id"), index=True)
    role: Mapped[LinkRole] = mapped_column(_enum_column(LinkRole, 10))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(String(10), default="kg")

    event: Mapped["ProcessEvent"] = relationship(back_populates="links", foreign_keys=[event_id])
    batch: Mapped["Batch"] = relationship(foreign_keys=[batch_id])


class QualityTest(Base):
    __tablename__ = "quality_tests"

    test_id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("process_events.event_id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.batch_id"), index=True)
    stage: Mapped[QCStage] = mapped_column(_enum_column(QCStage, 4))

    sample_weight: Mapped[Optional[Decimal]] = mapped_column(QTY, nullable=True)
    ka_1: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 3), nullable=True)
    ka_2: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 3), nullable=True)
    ka_3: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 3), nullable=True)
    aw: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 3), nullable=True)

    # No numeric acceptance threshold is evaluated here -- confirmed
    # 2026-09-14 (GENEALOGY.md §5.1). `finding` is a manual PIC judgment.
    finding: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metal_detection_finding: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class StockTransaction(Base):
    __tablename__ = "stock_transactions"

    transaction_id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.batch_id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("process_events.event_id"))
    direction: Mapped[TransactionDirection] = mapped_column(_enum_column(TransactionDirection, 3))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    balance_after: Mapped[Decimal] = mapped_column(QTY)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class Shipment(Base):
    __tablename__ = "shipments"

    shipment_id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("process_events.event_id"))
    shipping_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    destination: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    expedition: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    transport_condition: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    packaging_condition: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    coly: Mapped[Optional[int]] = mapped_column(nullable=True)
    gross_weight: Mapped[Optional[Decimal]] = mapped_column(QTY, nullable=True)
    tare_weight: Mapped[Optional[Decimal]] = mapped_column(QTY, nullable=True)
    net_weight: Mapped[Optional[Decimal]] = mapped_column(QTY, nullable=True)
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.customer_id"), nullable=True
    )
    recipient: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    audit_id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int] = mapped_column()
    action: Mapped[AuditAction] = mapped_column(_enum_column(AuditAction, 30))
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    before_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class SupplierReturnReceipt(Base):
    """Fase 38 -- konfirmasi bahwa supplier SUDAH MENERIMA barang retur.

    Status retur dua tahap: DIKIRIM = ada event SUPPLIER_RETURN (Fase 26/29);
    DITERIMA = ada baris di tabel ini untuk event itu. Tabel baru (bukan kolom
    tambahan) supaya `create_all` cukup untuk DB produksi yang sudah berjalan,
    tanpa ALTER TABLE. Satu konfirmasi per event (UNIQUE).
    """

    __tablename__ = "supplier_return_receipts"
    __table_args__ = (UniqueConstraint("return_event_id", name="uq_supplier_return_receipt_event"),)

    receipt_id: Mapped[int] = mapped_column(primary_key=True)
    return_event_id: Mapped[int] = mapped_column(ForeignKey("process_events.event_id"))
    received_date: Mapped[dt.date] = mapped_column(Date)
    confirmed_by: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    confirmed_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class SupplierReturnHistory(Base):
    """Fase 40 -- riwayat konfirmasi/pembatalan status retur supplier.

    Status SAAT INI tetap = ada/tidaknya baris `supplier_return_receipts`
    (Fase 38). Membatalkan konfirmasi menghapus baris receipt itu, tetapi
    seluruh kejadian (CONFIRMED / CANCELLED) tetap tersimpan di sini sebagai
    jejak. Tabel baru -> `create_all` cukup untuk DB produksi (tanpa ALTER).
    """

    __tablename__ = "supplier_return_history"

    history_id: Mapped[int] = mapped_column(primary_key=True)
    return_event_id: Mapped[int] = mapped_column(ForeignKey("process_events.event_id"), index=True)
    action: Mapped[str] = mapped_column(String(12))  # CONFIRMED | CANCELLED
    received_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # catatan konfirmasi / alasan batal


class AaFindingReview(Base):
    """Fase 42 -- status tinjau sebuah temuan audit perubahan AA (Fase 36).

    Temuan dikenali lewat kunci (event, batch sumber, batch hasil). Tanpa baris
    = BARU. Status: DITINJAU (dianggap wajar / sudah dilihat), DIABAIKAN, atau
    DIKOREKSI (diisi otomatis saat Production Manager mengganti nomor batch
    yang terlibat). Tabel baru -> `create_all` cukup untuk DB produksi.
    """

    __tablename__ = "aa_finding_reviews"
    __table_args__ = (
        UniqueConstraint("event_id", "source_batch_id", "result_batch_id", name="uq_aa_finding_review"),
    )

    review_id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("process_events.event_id"), index=True)
    source_batch_id: Mapped[int] = mapped_column(ForeignKey("batches.batch_id"))
    result_batch_id: Mapped[int] = mapped_column(ForeignKey("batches.batch_id"))
    status: Mapped[str] = mapped_column(String(12))  # DITINJAU | DIABAIKAN | DIKOREKSI
    reviewed_by: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    reviewed_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class BatchNumberCorrection(Base):
    """Fase 42 -- riwayat koreksi nomor batch oleh Production Manager (nomor
    lama -> baru, alasan wajib). Batch dan event tetap tertaut lewat ID, jadi
    silsilah tidak putus."""

    __tablename__ = "batch_number_corrections"

    correction_id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.batch_id"), index=True)
    old_batch_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    new_batch_number: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    corrected_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class EventDateCorrection(Base):
    """Fase 44 -- riwayat koreksi `event_date` pada event historis (Production
    Manager, alasan wajib). Hanya event TANPA event/batch turunan setelahnya
    pada batch yang sama boleh dikoreksi (lihat services/event_correction.py
    `_downstream_event_ids`) -- menghindari inkonsistensi rute turunan.
    Hanya `event_date`; field lain (kuantitas, AA, catatan) di luar lingkup
    fase ini."""

    __tablename__ = "event_date_corrections"

    correction_id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("process_events.event_id"), index=True)
    old_event_date: Mapped[dt.date] = mapped_column(Date)
    new_event_date: Mapped[dt.date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    corrected_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class EventCancellation(Base):
    """Fase 44 -- riwayat pembatalan (soft-cancel) event historis. Status
    event yang sebenarnya tetap `ProcessEvent.status = VOID`; tabel ini
    adalah jejak siapa/kapan/kenapa yang bisa di-query per event, terpisah
    dari AuditLog generik. Sama syarat kelayakan dengan koreksi tanggal
    (tanpa turunan); efek stok event dibalik lewat StockTransaction
    kompensasi (lihat services/event_correction.py `cancel_event`)."""

    __tablename__ = "event_cancellations"

    cancellation_id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("process_events.event_id"), index=True)
    event_type: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    cancelled_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class EventQuantityCorrection(Base):
    """Fase 45 -- riwayat koreksi kuantitas pada SATU `EventBatchLink`
    (INPUT atau OUTPUT) sebuah event historis (Production Manager, alasan
    wajib). Sama syarat kelayakan dengan koreksi tanggal/pembatalan Fase 44
    (hanya event TANPA event/batch turunan setelahnya pada batch yang
    disentuhnya -- `services/event_correction.py` `_guard_correctable`).
    Koreksi OTOMATIS menyesuaikan `StockTransaction` (baris kompensasi
    delta, bukan edit/hapus baris lama) dan `Batch.current_quantity` --
    lihat `correct_event_quantity()`. Berlaku generik untuk semua tipe
    event (keputusan user 2026-09-22); TIDAK menegakkan ulang validasi
    rekonsiliasi SUM(input)=SUM(output)+susut+loss lintas link lain event
    yang sama -- di luar lingkup fase ini (lihat docstring
    `correct_event_quantity`)."""

    __tablename__ = "event_quantity_corrections"

    correction_id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("process_events.event_id"), index=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("event_batch_links.link_id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.batch_id"))
    role: Mapped[str] = mapped_column(String(10))  # INPUT | OUTPUT
    old_quantity: Mapped[Decimal] = mapped_column(QTY)
    new_quantity: Mapped[Decimal] = mapped_column(QTY)
    reason: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    corrected_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class EventNotesCorrection(Base):
    """Fase 46 -- riwayat koreksi `ProcessEvent.notes` pada event historis
    (Production Manager, alasan wajib). Keputusan user 2026-09-23: catatan
    lama DITIMPA (isi lama tersimpan di sini sebagai jejak), dan tetap hanya
    untuk event TANPA turunan (syarat sama dengan Fase 44/45,
    `services/event_correction.py` `_guard_correctable`)."""

    __tablename__ = "event_notes_corrections"

    correction_id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("process_events.event_id"), index=True)
    old_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    corrected_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class JenisCorrection(Base):
    """Fase 46 -- satu koreksi Jenis (AA) pada batch akar + cascade ke batch
    turunannya (`services/jenis_correction.py`). Setiap batch yang nomornya
    ikut berubah juga punya baris `BatchNumberCorrection` sendiri (jalur Fase
    42), jadi riwayat nomor per batch tetap lengkap; tabel ini menyimpan
    ringkasan per koreksi (batch mana yang diubah, di mana cascade berhenti)."""

    __tablename__ = "jenis_corrections"

    correction_id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.batch_id"), index=True)
    old_jenis_code: Mapped[str] = mapped_column(String(2))
    new_jenis_code: Mapped[str] = mapped_column(String(2))
    changed_batch_ids: Mapped[str] = mapped_column(Text)  # "12,15,18"
    stopped_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    corrected_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
