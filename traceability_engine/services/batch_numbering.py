"""Fase 32 -- generator nomor batch untuk batch TURUNAN (PP 01/02/03/04).

Keputusan user (2026-09-21), dicatat di claude/32_GENERATOR_TURUNAN.md:

1. **Upgrade (01), Downgrade (02), Rework (04)**: nomor = [AA][BB][CCC]-
   [tanggal batch SUMBER]-[PP]. BB = grade hasil; CCC = supplier batch sumber
   (zero-pad 3 digit); tanggal = `receiving_date` batch sumber (tanggal
   kedatangan asli dipertahankan). AA = AA batch sumber; AA lama 03/04 dibawa
   apa adanya (legacy dinormalisasi hanya bila staf memilih 01/02 lewat
   `jenis_code`).
2. **Mixing (03)**: staf memilih AA + BB; tanggal = tanggal Mixing; supplier
   `000`. Sistem hanya memvalidasi bahwa AA semua batch sumber SAMA.
   [UNCONFIRMED] AA lama `03` dianggap Planifolia (= `02`) saat membandingkan;
   AA `04`/tak dikenal tidak ikut dibandingkan (jenis sebenarnya tidak
   diketahui, `LEGACY_JENIS_CODES`).
3. **Nomor kembar** (tanpa segmen urut): output digabung ke batch yang sudah
   ada bila batch itu ACTIVE, bertipe sama, dan belum diproses lanjut
   (tidak pernah jadi INPUT event selain SUPPLIER_RETURN, dan dibuat oleh
   jenis event yang sama); selain itu `BatchNumberConflictError`. Nomor
   turunan yang sama dengan nomor batch sumber sendiri juga konflik.

Semua fungsi di sini opt-in dari sisi pemanggil (`auto_batch_number`); nomor
yang diketik staf (Sortation Fase 23) tetap menang.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import batch_number as bn
from ..enums import BatchStatus, BatchType, EventType, LinkRole
from ..exceptions import BatchNumberConflictError
from ..models import Batch, EventBatchLink, ProcessEvent
from .events import NewBatchSpec, OutputSpec

DERIVED_SOURCE_PROCESS_CODES = ("01", "02", "04")


def derive_number(
    source: Batch, *, grade_code: str, process_code: str, jenis_code: Optional[str] = None
) -> str:
    """Nomor untuk hasil Upgrade/Downgrade/Rework dari `source` (aturan #1)."""
    if process_code not in DERIVED_SOURCE_PROCESS_CODES:
        raise ValueError(
            f"Nomor otomatis turunan hanya untuk PP {', '.join(DERIVED_SOURCE_PROCESS_CODES)} "
            f"(bukan {process_code!r})."
        )
    jenis = jenis_code or source.jenis_code
    if not jenis:
        raise ValueError(
            f"Batch sumber #{source.batch_id} tidak punya Jenis (AA) yang terbaca; "
            f"pilih Jenis 01/02 atau isi nomor batch manual."
        )
    if not source.supplier_code:
        raise ValueError(
            f"Batch sumber #{source.batch_id} tidak punya kode supplier; isi nomor batch manual."
        )
    if source.receiving_date is None:
        raise ValueError(
            f"Batch sumber #{source.batch_id} tidak punya tanggal (segmen tanggal nomor); "
            f"isi nomor batch manual."
        )
    return bn.generate(
        jenis_code=jenis,
        grade_code=grade_code,
        supplier_code=source.supplier_code,
        receiving_date=source.receiving_date,
        process_code=process_code,
        allow_legacy_jenis=jenis_code is None,
    )


def _comparable_jenis(code: Optional[str]) -> Optional[str]:
    """AA yang bisa dibandingkan antar-batch sumber Mixing. `03` (legacy,
    dibaca Planifolia) disamakan dengan `02`; `04`/None = tidak diketahui."""
    if code in ("01", "02"):
        return code
    if code == "03":
        return "02"
    return None


def mixing_number(
    session: Session,
    *,
    source_batch_ids: Iterable[int],
    jenis_code: str,
    grade_code: str,
    event_date: dt.date,
    supplier_code: str = "000",
) -> str:
    """Nomor hasil Mixing (aturan #2). Menolak bila AA batch sumber berbeda."""
    known: dict[str, list[int]] = {}
    for sid in source_batch_ids:
        b = session.get(Batch, sid)
        if b is None:
            raise ValueError(f"Batch {sid} does not exist.")
        c = _comparable_jenis(b.jenis_code)
        if c is not None:
            known.setdefault(c, []).append(sid)
    if len(known) > 1:
        detail = "; ".join(
            f"{bn.JENIS_CODES[k]}: batch #{', #'.join(map(str, v))}" for k, v in sorted(known.items())
        )
        raise ValueError(
            f"Batch sumber Mixing punya Jenis (AA) berbeda ({detail}); nomor otomatis "
            f"hanya untuk sumber berjenis sama."
        )
    return bn.generate(
        jenis_code=jenis_code,
        grade_code=grade_code,
        supplier_code=supplier_code,
        receiving_date=event_date,
        process_code="03",
    )


def find_mergeable_batch(
    session: Session,
    number: str,
    *,
    batch_type: BatchType,
    event_type: EventType,
    exclude_ids: Iterable[int] = (),
) -> Optional[Batch]:
    """Batch yang sudah memakai `number` dan boleh ditambah stok (aturan #3),
    atau None bila nomor belum dipakai. Selain itu BatchNumberConflictError."""
    existing = session.execute(
        select(Batch).where(Batch.batch_number == number)
    ).scalars().first()
    if existing is None:
        return None
    if existing.batch_id in set(exclude_ids):
        raise BatchNumberConflictError(
            f"Nomor batch {number} sama dengan nomor batch sumber #{existing.batch_id}; "
            f"isi nomor batch manual."
        )
    links = session.execute(
        select(ProcessEvent.event_type, EventBatchLink.role)
        .join(EventBatchLink, EventBatchLink.event_id == ProcessEvent.event_id)
        .where(EventBatchLink.batch_id == existing.batch_id)
    ).all()
    used_as_input = any(
        role == LinkRole.INPUT and et != EventType.SUPPLIER_RETURN for et, role in links
    )
    made_by_other = any(role == LinkRole.OUTPUT and et != event_type for et, role in links)
    if (
        existing.status != BatchStatus.ACTIVE
        or existing.batch_type != batch_type
        or used_as_input
        or made_by_other
    ):
        raise BatchNumberConflictError(
            f"Nomor batch {number} sudah dipakai batch #{existing.batch_id} yang "
            f"sudah diproses/tidak aktif/berbeda tipe, sehingga hasil baru tidak bisa "
            f"digabung. Isi nomor batch manual."
        )
    return existing


def output_for_number(
    session: Session,
    *,
    number: str,
    quantity,
    unit: str,
    spec: NewBatchSpec,
    event_type: EventType,
    exclude_ids: Iterable[int] = (),
) -> OutputSpec:
    """OutputSpec untuk `number`: digabung ke batch yang ada bila boleh,
    selain itu batch baru dengan komponen nomor diurai (aturan #3)."""
    merge_into = find_mergeable_batch(
        session, number, batch_type=spec.batch_type, event_type=event_type,
        exclude_ids=exclude_ids,
    )
    if merge_into is not None:
        return OutputSpec(quantity=quantity, unit=unit, batch_id=merge_into.batch_id)
    c = bn.parse(number)
    spec.batch_number = number
    spec.jenis_code = c.jenis_code
    spec.grade_code = c.grade_code
    spec.supplier_code = c.supplier_code
    spec.receiving_date = c.receiving_date
    spec.process_code = c.process_code
    return OutputSpec(quantity=quantity, unit=unit, new_batch=spec)
