"""Enumerations shared across the traceability engine.

Sourced from DATABASE_DESIGN.md / GENEALOGY.md (Phase 2). Kept as plain
Python str-Enums (rather than native DB enums) so SQLite (tests) and
PostgreSQL (production) behave identically — no dialect-specific enum DDL.
"""
from __future__ import annotations

import enum


class BatchType(str, enum.Enum):
    RAW_KERING = "RAW_KERING"
    RAW_HIJAU = "RAW_HIJAU"
    PROCESSED = "PROCESSED"
    POWDER = "POWDER"
    PACKAGED = "PACKAGED"


class BatchStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    SUPERSEDED = "SUPERSEDED"
    SHIPPED = "SHIPPED"
    REJECTED = "REJECTED"


class EventType(str, enum.Enum):
    RECEIVING = "RECEIVING"
    QC_TEST = "QC_TEST"
    METAL_DETECTION = "METAL_DETECTION"
    STEAMING = "STEAMING"
    SUNDRYING = "SUNDRYING"
    # Fase 19 -- Hijau (green-curing) route only, WORKFLOW.md/CLAUDE.md:
    # PB -> Sortation -> Steaming/blanching -> Main Curing -> 1st Curing ->
    # 2nd Curing -> 3rd Curing -> Sundrying -> Airdrying -> Sortation -> ...
    # Steaming/blanching and the later Sundrying reuse STEAMING/SUNDRYING
    # above rather than getting their own types -- see services/curing.py
    # module docstring #1 for why (traceability-trial(1).html STAGE_DEFS
    # confirms both are the same stage definition across both routes, only
    # the byAlur hint text differs). These five are the genuinely new
    # stages with no Kering equivalent.
    MAIN_CURING = "MAIN_CURING"
    FIRST_CURING = "FIRST_CURING"
    SECOND_CURING = "SECOND_CURING"
    THIRD_CURING = "THIRD_CURING"
    AIRDRYING = "AIRDRYING"
    SORTATION = "SORTATION"
    MIXING = "MIXING"
    VACUUM = "VACUUM"
    PACKING = "PACKING"
    GRINDING = "GRINDING"
    MAGNETIZATION = "MAGNETIZATION"
    MD_POWDER = "MD_POWDER"
    REWORK = "REWORK"
    DELIVERY = "DELIVERY"
    SAMPLE_DELIVERY = "SAMPLE_DELIVERY"
    ADJUSTMENT = "ADJUSTMENT"


# Event types with no INPUT batch (external -> batch).
NO_INPUT_EVENT_TYPES = frozenset({EventType.RECEIVING})

# Event types with no OUTPUT batch (batch -> external / stock-out).
NO_OUTPUT_EVENT_TYPES = frozenset({EventType.DELIVERY, EventType.SAMPLE_DELIVERY})

# ADJUSTMENT does not go through record_process_event() at all (GENEALOGY.md
# §5.2) -- it has its own gated path, record_adjustment().
NOT_A_GENEALOGY_EVENT = frozenset({EventType.ADJUSTMENT})


class EventStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    VOID = "VOID"


class LinkRole(str, enum.Enum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"


class TransactionDirection(str, enum.Enum):
    IN = "IN"
    OUT = "OUT"


class QCStage(str, enum.Enum):
    RM = "RM"  # raw material
    IP = "IP"  # in-process
    FP = "FP"  # finished product


class UserRole(str, enum.Enum):
    # Only PRODUCTION_MANAGER's authorization rule is confirmed
    # (GENEALOGY.md §5.2 / §9). Everything else is [UNCONFIRMED]
    # (PROCESS_RULES.md) -- STAFF is a permissive placeholder, not a
    # confirmed role/permission matrix.
    PRODUCTION_MANAGER = "PRODUCTION_MANAGER"
    STAFF = "STAFF"


class AuditAction(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    VOID = "VOID"
    ADJUSTMENT_APPROVED = "ADJUSTMENT_APPROVED"
    REJECTED = "REJECTED"
