"""Batch-number parsing (NOT generation; Fase 30: AA/Jenis now confirmed,
generator still not opened -- parser/validator scope only) -- see BATCH_NUMBER_SPEC.md.

Format:
    Historical (2-digit supplier): [AA][BB][CC]-[YYMMDD]-[PP]   e.g. 030224-260221-00
    New, from 2026-09-14 (3-digit): [AA][BB][CCC]-[YYMMDD]-[PP]  e.g. 0302024-260221-00

`parse()` accepts both widths, since historical rows and newly-generated
rows now coexist (BATCH_NUMBER_SPEC.md "Resolved by PT JAS", go-forward
migration, no retroactive rewrite of history).

`generate()` intentionally raises BatchNumberNotImplementedError.
BATCH_NUMBER_SPEC.md explicitly prohibits implementing/changing the
generator until the AA (Jenis) segment is confirmed with PT JAS staff
(Robiah/Wakhidah/Fahrul) -- see "Open questions" in that document and the
carried-over blocker in PROJECT_STATUS.md. This is not an oversight.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from .exceptions import BatchNumberNotImplementedError

# Grade Master (Proses.docx) vs. the compressed BB in-string encoding
# (Grade Master code / 10). Kept here only as a documented reference for
# callers that need to cross-check -- not used to validate/reject anything,
# since Grade 04/06 real-world meaning is still partly [UNCONFIRMED]
# (BATCH_NUMBER_SPEC.md).
GRADE_MASTER_TO_BB = {10: "01", 20: "02", 30: "03", 40: "04", 50: "05", 60: "06", 0: "00"}
BB_TO_GRADE_MASTER = {v: k for k, v in GRADE_MASTER_TO_BB.items()}

# Jenis (AA) -- CONFIRMED by PT JAS (Tommy, 2026-09-21, Fase 30): hanya ada
# DUA jenis, 01 = Tahitensis, 02 = Planifolia. "Hijau" adalah GRADE (BB=00),
# bukan jenis; batch hijau bisa Tahitensis atau Planifolia.
JENIS_CODES = {"01": "Tahitensis", "02": "Planifolia"}

# Kode AA lama yang muncul di data riwayat (123 sampel) tetapi BUKAN kode
# jenis resmi. Parser tetap menerimanya apa adanya (tidak menulis ulang
# riwayat); generator batch baru tidak boleh memakainya.
#   03 -> dipakai 61 batch, dibaca sebagai Planifolia (label staf lama)
#   04 -> dipakai 12 batch intake Hijau (BB=00); jenis sebenarnya tidak diketahui
LEGACY_JENIS_CODES = {
    "03": "Planifolia (kode lama)",
    "04": "Hijau (kode lama; jenis tidak diketahui)",
}

# "Ongoing Grading" / process-history code -- BATCH_NUMBER_SPEC.md, all 5
# confirmed real and in use by PT JAS (2026-09-14).
PROCESS_CODES = {
    "00": "Original",
    "01": "Upgrade",
    "02": "Downgrade",
    "03": "Mixing",
    "04": "Rework",
}


@dataclass(frozen=True)
class BatchNumberComponents:
    raw: str
    jenis_code: str  # AA -- 01 Tahitensis / 02 Planifolia (Fase 30); 03/04 = legacy
    grade_code: str  # BB, batch-number-internal (Grade Master / 10) encoding
    supplier_code: str  # CC or CCC, un-padded numeric string preserved as given
    supplier_code_width: int  # 2 (historical) or 3 (new, from 2026-09-14)
    receiving_date: dt.date
    process_code: str  # PP

    @property
    def jenis_label(self) -> Optional[str]:
        """Nama jenis untuk AA; None bila kode tak dikenal sama sekali."""
        return JENIS_CODES.get(self.jenis_code) or LEGACY_JENIS_CODES.get(self.jenis_code)

    @property
    def jenis_is_legacy(self) -> bool:
        """True bila AA adalah kode lama (03/04) yang hanya valid untuk riwayat."""
        return self.jenis_code in LEGACY_JENIS_CODES


def parse(batch_number: str) -> BatchNumberComponents:
    """Parse a historical (2-digit supplier) or new (3-digit supplier)
    batch-number string into its components.

    Raises ValueError if the string doesn't match either known shape.
    """
    if not batch_number or batch_number.count("-") != 2:
        raise ValueError(f"Not a recognized batch-number format: {batch_number!r}")

    prefix, date_part, process_code = batch_number.split("-")

    if len(prefix) == 6:
        width = 2
        jenis_code, grade_code, supplier_code = prefix[0:2], prefix[2:4], prefix[4:6]
    elif len(prefix) == 7:
        width = 3
        jenis_code, grade_code, supplier_code = prefix[0:2], prefix[2:4], prefix[4:7]
    else:
        raise ValueError(
            f"Unrecognized prefix width {len(prefix)} (expected 6 or 7) in {batch_number!r}"
        )

    if len(date_part) != 6 or not date_part.isdigit():
        raise ValueError(f"Unrecognized date segment {date_part!r} in {batch_number!r}")
    if len(process_code) != 2 or not process_code.isdigit():
        raise ValueError(f"Unrecognized process code {process_code!r} in {batch_number!r}")

    # YYMMDD -- confirmed by BATCH_NUMBER_SPEC.md against 123 samples, zero
    # exceptions. NOT DDMMYY (that was the traceability-trial(1).html bug).
    yy, mm, dd = int(date_part[0:2]), int(date_part[2:4]), int(date_part[4:6])
    receiving_date = dt.date(2000 + yy, mm, dd)

    return BatchNumberComponents(
        raw=batch_number,
        jenis_code=jenis_code,
        grade_code=grade_code,
        supplier_code=supplier_code,
        supplier_code_width=width,
        receiving_date=receiving_date,
        process_code=process_code,
    )


def generate(*args, **kwargs) -> str:
    """DO NOT IMPLEMENT until the AA (Jenis) segment is confirmed.

    See BATCH_NUMBER_SPEC.md: "Do not implement/change the generator until
    AA is resolved." Callers needing a batch number for a newly-created
    batch (e.g. Fase 4 Receiving) must accept `batch_number` as external
    manual input, or leave it null, until this blocker clears -- see
    PROJECT_STATUS.md.
    """
    raise BatchNumberNotImplementedError(
        "batch_number.generate() is intentionally not implemented: the AA "
        "(Jenis) segment meaning is still [UNCONFIRMED] per "
        "BATCH_NUMBER_SPEC.md, and that document explicitly prohibits "
        "implementing or changing the generator before it is resolved with "
        "PT JAS staff (Robiah/Wakhidah/Fahrul). Pass batch_number explicitly "
        "(manual/external input) or None instead of calling this function."
    )
