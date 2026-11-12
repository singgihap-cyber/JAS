"""One-off historical data import: `DATA PROSES VANILA 2026 REVISI.xlsx`
(the Kering-route source workbook) -> a fresh traceability database, via
the real service layer (never writing rows by hand).

Not part of the application (no code elsewhere imports this module) --
a data-migration tool, run once per revision of the source workbook
against a throwaway/local database. Kept in `scripts/` rather than
`traceability_engine/` for that reason.

Scope (user-confirmed 2026-11-12): all 16 process sheets in one pass.
Four of them (`grind`, `MG`, `MDPW`, `REW` -- Grinding, Magnetization, MD
Powder, Rework) have headers only, zero filled data rows in this revision
-- verified directly with openpyxl, nothing to import for those. `TRACE` is
a hand-built chain-of-custody example (one shipment's full backward trace,
stacked PACKING->VACUUM->MIXING->SORTASI->SUN DRYING->STEAM->METAL
DETECTOR->QUALITY TEST->BARANG DATANG), not a process log -- it is a
validation target (compare against `traceability_engine.services
.traceability.backward_trace()` on the imported data), not an import
source.

Rows across all sheets are replayed through the service layer in a single
GLOBAL chronological order (event_date, then a fixed per-sheet process
rank, then sheet row number) rather than one sheet at a time, because a
real batch's rows are interleaved across sheets by actual calendar date --
e.g. a Mixing event can occur before a later Sortation on a *different*
batch that happens to sort earlier. Processing sheet-by-sheet would apply
self-loop/shrinkage events (Sundrying, Vacuum, QC, MD) out of order against
a batch's on-hand quantity and raise spurious `InsufficientStockError`s.

Per-row failures (unparseable stage, missing linked batch, reconciliation
error, or any other `ValueError`) are caught, logged to the report, and
skipped -- the import never aborts on one bad row (user-confirmed policy:
"dokumentasikan & lanjut", not "stop and ask"). Every decision below that
isn't already pinned down by the existing `services/*.py` docstrings is a
NEW decision made by this script and is called out explicitly.

New decisions (documented, not guessed):

1. **Self-loop quantity (QT/MD/STEAM/SD/VC) always uses the already-
   established engine default (batch's current on-hand), never the
   sheet's own qty/sample-weight column**, even though the sheet usually
   agrees with on-hand. This matches the existing architectural choice
   (qc_md.py #2, steam_dry.py #3) and sidesteps any sheet transcription
   error in that column without silently trusting unverified input.

2. **VC (Vacuum) and PACK/PD/SmpD continuation rows** (blank leading
   NO./TANGGAL/shipping-number, inheriting the previous row's event): VC's
   multi-plastic-line continuation is common and is grouped properly (see
   `_group_by_leading_date`). PACK/PD/SmpD's rarer multi-batch-per-shipment
   continuation (documented as real in `delivery.py` module docstring #4,
   e.g. `LN/G/260517-013`) is NOT grouped by this first pass -- each row is
   imported as its own standalone single-source event; a row with no date
   of its own (a true continuation row) is skipped and logged under
   "unsupported continuation row" rather than guessed into the wrong
   group. This under-imports a small, enumerable set of multi-batch
   shipments; the report lists exactly which rows were skipped so they can
   be re-added by hand or in a follow-up pass.

3. **MIX's output batch number** (`BATCH NUMBER GABUNGAN`) has no field on
   `MixingInput` to set it directly (that dataclass only supports
   `auto_batch_number` generation, not a manual historical number). This
   script calls `record_mixing()` normally, then overwrites the returned
   output `Batch.batch_number` with the sheet's value directly (same
   session, before commit) and re-parses it to correct
   jenis/grade/supplier/receiving_date/process_code to match what the real
   label encodes -- the same post-hoc correction `jenis_correction.py`/
   `BatchNumberCorrection` already do for exactly this situation, just
   applied at import time instead of after the fact.

4. **Supplier/User master data is created on first sight**, keyed by the
   sheet's own name text (`NAMA SUPPLIER`, `PIC`). A supplier's
   `supplier_code` is taken from the *first* batch-number of theirs that
   parses cleanly; if none ever parses, a placeholder code is assigned
   (`"X1"`, `"X2"`, ...) -- there is no master-data sheet in this workbook
   to resolve codes from otherwise.

5. **QT's "STATUS (RM/IP/FP)" column is used directly as `QCTestInput.stage`**
   (and MD's "STATUS PRODUK" as `MetalDetectionInput.stage`) -- both are
   exactly the caller-supplied value `qc_md.py` #4 says the engine expects,
   just supplied by this script instead of a UI form. A QT row whose status
   text isn't one of RM/IP/FP (observed: literal `"PD"` on at least one
   row) is skipped and logged rather than guessed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Optional

import openpyxl
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from traceability_engine.enums import BatchType, QCStage, UserRole
from traceability_engine.models import Base, Batch, Supplier, User
from traceability_engine import batch_number as batch_number_mod
from traceability_engine.services.qc_md import (
    MetalDetectionInput, QCTestInput, record_metal_detection, record_qc_test,
)
from traceability_engine.services.receiving import ReceivingInput, record_receiving
from traceability_engine.services.steam_dry import (
    SteamingInput, SundryingInput, record_steaming, record_sundrying,
)
from traceability_engine.services.sortation import SortationInput, record_sortation
from traceability_engine.services.mixing import MixingInput, MixingSource, record_mixing
from traceability_engine.services.vacuum_packing import (
    PackingInput, PackingSource, VacuumInput, VacuumPlasticLine,
    record_packing, record_vacuum,
)
from traceability_engine.services.delivery import (
    DeliveryInput, DeliverySource, record_delivery, record_sample_delivery,
)

RANK_PB, RANK_QT, RANK_MD, RANK_STEAM, RANK_SD = 0, 1, 1, 2, 3
# Tried, then reverted: ranking FP-stage QT/MD rows late (after Sortation/
# Packing/Delivery) on the theory that a "finished product" sample is
# drawn post-processing. Investigated one real failing case directly
# (batch 010110-261001-00): Sortation on 2026-10-02 mints brand-new output
# batches and fully depletes/CONSUMEs the original batch_id (no output
# batch_number was typed on that SORT row, so the mint can't even be
# labelled back to the same number) -- yet the sheet keeps testing FP
# under the SAME original batch number on 2026-10-05 and later. That is a
# real mismatch between this engine's Sortation model (always mints a new
# batch, sortation.py #1) and this company's actual labelling (the
# original code keeps being used after a single-output sort) -- no rank
# ordering fixes it, since the source batch is genuinely at zero either
# way. Left as [UNCONFIRMED] in the report rather than papered over with a
# rank hack; QT/MD both stay at one early rank, which empirically imports
# more rows correctly than the late-FP-rank attempt did.
RANK_QT_FP, RANK_MD_FP = RANK_QT, RANK_MD
# SORT before MIX (rank, not alphabetical sheet order): Mixing sources are
# frequently a same-day Sortation *output* batch (e.g. AA=03/BB=02 lots) --
# "MIX" < "SORT" alphabetically would otherwise run Mixing first and miss
# them, even though both nominally share one process step in the sheet.
RANK_SORT, RANK_MIX, RANK_VC, RANK_PACK, RANK_PD = 4, 5, 6, 7, 8


# --------------------------------------------------------------------------
# Master-data caches
# --------------------------------------------------------------------------


class Registry:
    def __init__(self, session: Session):
        self.session = session
        self._users: dict[str, int] = {}
        self._suppliers: dict[str, int] = {}
        self._batches: dict[str, int] = {}
        self._placeholder_n = 0

    def user_id(self, name: Optional[str]) -> int:
        key = (name or "UNKNOWN").strip().upper() or "UNKNOWN"
        if key not in self._users:
            u = User(name=(name or "UNKNOWN").strip() or "UNKNOWN", role=UserRole.STAFF)
            self.session.add(u)
            self.session.flush()
            self._users[key] = u.user_id
        return self._users[key]

    def supplier_id(self, name: Optional[str], hint_batch_number: Optional[str] = None) -> int:
        key = (name or "UNKNOWN").strip().upper() or "UNKNOWN"
        if key in self._suppliers:
            return self._suppliers[key]
        code = None
        if hint_batch_number:
            try:
                code = batch_number_mod.parse(hint_batch_number).supplier_code
            except ValueError:
                code = None
        if code is None or self.session.execute(
            select(Supplier).where(Supplier.supplier_code == code)
        ).scalars().first() is not None:
            self._placeholder_n += 1
            code = f"X{self._placeholder_n}"
        s = Supplier(supplier_code=code, name=(name or "UNKNOWN").strip() or "UNKNOWN")
        self.session.add(s)
        self.session.flush()
        self._suppliers[key] = s.supplier_id
        return self._suppliers[key]

    def remember_batch(self, number: Optional[str], batch_id: int) -> None:
        if number:
            self._batches[number.strip()] = batch_id

    def find_batch(self, number: Optional[str]) -> Optional[int]:
        if not number:
            return None
        number = number.strip()
        if number in self._batches:
            return self._batches[number]
        existing = self.session.execute(
            select(Batch).where(Batch.batch_number == number)
        ).scalars().first()
        if existing is not None:
            self._batches[number] = existing.batch_id
            return existing.batch_id
        return None


# --------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------


def _s(v) -> Optional[str]:
    if v is None:
        return None
    v = str(v).strip()
    return v or None


_QTY_PLACES = Decimal("0.001")


def _d(v) -> Optional[Decimal]:
    """Parse a sheet cell as Decimal, rounded to the engine's own 3-decimal
    quantity precision (`QTY = Numeric(18, 3)`, models.py). openpyxl returns
    Excel's underlying IEEE-754 floats verbatim (e.g. 23.060000000000004 for
    a cell that reads "23.06"); without rounding here, that float-repr noise
    reaches `record_process_event()`'s zero-tolerance reconciliation check
    (GENEALOGY.md §5, confirmed no-tolerance 2026-09-14) as a spurious
    mismatch and fails rows whose source figures actually agree. Rounding to
    the same precision the column already stores is not inventing a
    shrinkage/reconciliation tolerance -- it is the one normalization step
    needed before two numbers the sheet shows as equal are compared.
    """
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).strip()).quantize(_QTY_PLACES)
    except (InvalidOperation, ValueError):
        return None


def _date(v) -> Optional[dt.date]:
    """A real Excel date cell comes back from openpyxl as `datetime`; a
    handful of PB rows have the date typed as plain text instead
    (`"18/8/2026"`, D/M/YYYY -- unambiguous, since day 18 can't be a
    month) -- parsed here rather than dropped, same spirit as Receiving's
    own lenient handling of a batch_number that doesn't parse cleanly
    (receiving.py #1)."""
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str) and v.strip():
        parts = v.strip().split("/")
        if len(parts) == 3:
            try:
                d, m, y = (int(p) for p in parts)
                return dt.date(y, m, d)
            except ValueError:
                return None
    return None


def _time(v) -> Optional[dt.time]:
    if isinstance(v, dt.time):
        return v
    if isinstance(v, dt.datetime):
        return v.time()
    if isinstance(v, str) and v.strip():
        txt = v.strip().replace(".", ":")
        parts = txt.split(":")
        try:
            if len(parts) == 2:
                return dt.time(int(parts[0]) % 24, int(parts[1]) % 60)
        except ValueError:
            return None
    return None


@dataclass
class PendingEvent:
    event_date: dt.date
    rank: int
    sheet: str
    row_no: int
    fn: Callable[[Session, Registry], None]
    label: str


@dataclass
class Report:
    imported: dict = field(default_factory=dict)  # sheet -> count
    skipped: list = field(default_factory=list)  # (sheet, row_no, label, reason)

    def ok(self, sheet: str) -> None:
        self.imported[sheet] = self.imported.get(sheet, 0) + 1

    def skip(self, sheet: str, row_no: int, label: str, reason: str) -> None:
        self.skipped.append((sheet, row_no, label, reason))


def rows_from(ws, start_row: int):
    for i, row in enumerate(ws.iter_rows(min_row=start_row, values_only=True), start=start_row):
        yield i, row


# --------------------------------------------------------------------------
# PB -- Receiving
# --------------------------------------------------------------------------


def collect_pb(ws, reg_placeholder, report: Report) -> list[PendingEvent]:
    out = []
    for i, row in rows_from(ws, 10):
        if row[0] is None or _date(row[1]) is None:
            continue
        no, tgl, batch, supplier_name, desc, kemasan, coly, bruto, tara, netto, onspec, offspec, smell_a, smell_b, pic = row[:15]
        event_date = _date(tgl)
        net = _d(netto)
        if net is None or net <= 0:
            report.skip("PB", i, str(batch), "netto kosong/<=0")
            continue
        smell = _s(smell_a) or _s(smell_b)

        def fn(session, registry, batch=_s(batch), supplier_name=supplier_name,
               desc=_s(desc), kemasan=_s(kemasan), coly=coly, bruto=_d(bruto),
               tara=_d(tara), net=net, onspec=_d(onspec), offspec=_d(offspec),
               smell=smell, pic=_s(pic), event_date=event_date, row_no=i):
            try:
                supplier_id = registry.supplier_id(supplier_name, hint_batch_number=batch)
                ev = record_receiving(session, ReceivingInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    supplier_id=supplier_id, batch_type=BatchType.RAW_KERING,
                    net_quantity=net, batch_number=batch, product_description=desc,
                    packaging_condition=kemasan, coly=int(coly) if coly else None,
                    gross_weight=bruto, tare_weight=tara, on_spec_qty=onspec,
                    off_spec_qty=offspec, smell_test=smell,
                ))
                out_batch = next(l.batch for l in ev.links)
                registry.remember_batch(batch, out_batch.batch_id)
                report.ok("PB")
            except Exception as e:
                report.skip("PB", row_no, batch, f"{type(e).__name__}: {e}")

        out.append(PendingEvent(event_date, RANK_PB, "PB", i, fn, str(batch)))
    return out


# --------------------------------------------------------------------------
# QT -- QC Test
# --------------------------------------------------------------------------

_STAGE_MAP = {s.value: s for s in QCStage}


def collect_qt(ws, report: Report) -> list[PendingEvent]:
    out = []
    for i, row in rows_from(ws, 8):
        if row[0] is None:
            continue
        no, recv, uji, batch, status, sample_w, ka1, ka2, ka3, aw, pic = row[:11]
        event_date = _date(uji) or _date(recv)
        if event_date is None or _s(batch) is None:
            continue
        stage = _STAGE_MAP.get(_s(status))

        def fn(session, registry, batch=_s(batch), stage=stage, recv=_date(recv),
               sample_w=_d(sample_w), ka1=_d(ka1), ka2=_d(ka2), ka3=_d(ka3), aw=_d(aw),
               pic=_s(pic), event_date=event_date, row_no=i, status=_s(status)):
            if stage is None:
                report.skip("QT", row_no, batch, f"STATUS tidak dikenal: {status!r}")
                return
            batch_id = registry.find_batch(batch)
            if batch_id is None:
                report.skip("QT", row_no, batch, "batch tidak ditemukan")
                return
            try:
                record_qc_test(session, QCTestInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    batch_id=batch_id, stage=stage, sample_received_date=recv,
                    sample_weight=sample_w, ka_1=ka1, ka_2=ka2, ka_3=ka3, aw=aw,
                ))
                report.ok("QT")
            except Exception as e:
                report.skip("QT", row_no, batch, f"{type(e).__name__}: {e}")

        rank = RANK_QT_FP if stage is QCStage.FP else RANK_QT
        out.append(PendingEvent(event_date, rank, "QT", i, fn, str(batch)))
    return out


# --------------------------------------------------------------------------
# MD -- Metal Detection
# --------------------------------------------------------------------------


def collect_md(ws, report: Report) -> list[PendingEvent]:
    out = []
    for i, row in rows_from(ws, 6):
        if row[0] is None:
            continue
        no, tgl, batch, qty, status, finding, ket, pic = row[:8]
        event_date = _date(tgl)
        if event_date is None or _s(batch) is None:
            continue
        stage = _STAGE_MAP.get(_s(status))

        def fn(session, registry, batch=_s(batch), stage=stage, finding=finding,
               ket=_s(ket), pic=_s(pic), event_date=event_date, row_no=i, status=_s(status)):
            if stage is None:
                report.skip("MD", row_no, batch, f"STATUS PRODUK tidak dikenal: {status!r}")
                return
            batch_id = registry.find_batch(batch)
            if batch_id is None:
                report.skip("MD", row_no, batch, "batch tidak ditemukan")
                return
            try:
                record_metal_detection(session, MetalDetectionInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    batch_id=batch_id, stage=stage,
                    finding=_s(finding) if finding is not None else None,
                    description=ket,
                ))
                report.ok("MD")
            except Exception as e:
                report.skip("MD", row_no, batch, f"{type(e).__name__}: {e}")

        rank = RANK_MD_FP if stage is QCStage.FP else RANK_MD
        out.append(PendingEvent(event_date, rank, "MD", i, fn, str(batch)))
    return out


# --------------------------------------------------------------------------
# STEAM -- Steaming
# --------------------------------------------------------------------------


def collect_steam(ws, report: Report) -> list[PendingEvent]:
    out = []
    for i, row in rows_from(ws, 9):
        if row[0] is None:
            continue
        (no, tgl, batch, qty, pan, w1, w2, p1, p2, temp_range,
         v1, v2, v3, start, end, pic) = row[:16]
        event_date = _date(tgl)
        if event_date is None or _s(batch) is None:
            continue
        water_bits = [lbl for lbl, v in (("Bersih, Jernih", w1), ("Tidak berbau", w2)) if v]
        pan_bits = [lbl for lbl, v in (("Bersih", p1), ("Tidak berkarat", p2)) if v]

        def fn(session, registry, batch=_s(batch), pan=pan,
               water=", ".join(water_bits) or None, pancond=", ".join(pan_bits) or None,
               v1=_d(v1), v2=_d(v2), v3=_d(v3), start=_time(start), end=_time(end),
               pic=_s(pic), event_date=event_date, row_no=i):
            batch_id = registry.find_batch(batch)
            if batch_id is None:
                report.skip("STEAM", row_no, batch, "batch tidak ditemukan")
                return
            try:
                record_steaming(session, SteamingInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    batch_id=batch_id, event_time=start, end_time=end,
                    pan_count=int(pan) if pan else None, water_condition=water,
                    pan_condition=pancond, verification_reading_1=v1,
                    verification_reading_2=v2, verification_reading_3=v3,
                ))
                report.ok("STEAM")
            except Exception as e:
                report.skip("STEAM", row_no, batch, f"{type(e).__name__}: {e}")

        out.append(PendingEvent(event_date, RANK_STEAM, "STEAM", i, fn, str(batch)))
    return out


# --------------------------------------------------------------------------
# SD -- Sundrying
# --------------------------------------------------------------------------


def collect_sd(ws, report: Report) -> list[PendingEvent]:
    out = []
    for i, row in rows_from(ws, 10):
        if row[0] is None:
            continue
        no, tgl, batch, qty_awal, ka_awal, durasi, qty_akhir, susut, pic = row[:9]
        event_date = _date(tgl)
        final_qty = _d(qty_akhir)
        if event_date is None or _s(batch) is None or final_qty is None:
            continue

        def fn(session, registry, batch=_s(batch), starting=_d(qty_awal),
               final=final_qty, ka=_d(ka_awal), durasi=_s(durasi), pic=_s(pic),
               event_date=event_date, row_no=i):
            batch_id = registry.find_batch(batch)
            if batch_id is None:
                report.skip("SD", row_no, batch, "batch tidak ditemukan")
                return
            try:
                record_sundrying(session, SundryingInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    batch_id=batch_id, final_quantity=final, starting_quantity=starting,
                    starting_ka=ka, drying_duration=durasi,
                ))
                report.ok("SD")
            except Exception as e:
                report.skip("SD", row_no, batch, f"{type(e).__name__}: {e}")

        out.append(PendingEvent(event_date, RANK_SD, "SD", i, fn, str(batch)))
    return out


# --------------------------------------------------------------------------
# SORT -- Sortation
# --------------------------------------------------------------------------


def collect_sort(ws, report: Report) -> list[PendingEvent]:
    out = []
    for i, row in rows_from(ws, 9):
        if row[0] is None:
            continue
        no, mulai, selesai, batch, awal, gourmet, eg, ep, nc, powder, akhir, susut, pic = row[:13]
        event_date = _date(mulai)
        end_date = _date(selesai)
        if event_date is None or _s(batch) is None:
            continue
        if end_date is not None and end_date < event_date:
            end_date = None  # data error; drop rather than let the service reject the whole row

        def fn(session, registry, batch=_s(batch), initial=_d(awal), gourmet=_d(gourmet),
               eg=_d(eg), ep=_d(ep), nc=_d(nc), powder=_d(powder), pic=_s(pic),
               event_date=event_date, end_date=end_date, row_no=i):
            batch_id = registry.find_batch(batch)
            if batch_id is None:
                report.skip("SORT", row_no, batch, "batch tidak ditemukan")
                return
            try:
                record_sortation(session, SortationInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    batch_id=batch_id, initial_qty=initial, end_date=end_date,
                    gourmet_qty=gourmet, eg_qty=eg, ep_qty=ep, nc_qty=nc, powder_qty=powder,
                ))
                report.ok("SORT")
            except Exception as e:
                report.skip("SORT", row_no, batch, f"{type(e).__name__}: {e}")

        out.append(PendingEvent(event_date, RANK_SORT, "SORT", i, fn, str(batch)))
    return out


# --------------------------------------------------------------------------
# MIX -- Mixing (groups of source rows, output cols on the last row)
# --------------------------------------------------------------------------


def collect_mix(ws, report: Report) -> list[PendingEvent]:
    out = []
    group: list[tuple] = []
    group_date = None
    group_start_row = None

    def flush(end_row, gabungan, cp, susut, akhir, desk, pic):
        nonlocal group, group_date, group_start_row
        if not group or group_date is None or akhir is None:
            if group:
                report.skip("MIX", end_row, str(gabungan), "grup tanpa TANGGAL/sumber yang valid")
            group, group_date, group_start_row = [], None, None
            return
        sources = list(group)
        gd, gsr = group_date, group_start_row

        def fn(session, registry, sources=sources, final=akhir, desk=_s(desk),
               pic=_s(pic), gabungan=_s(gabungan), event_date=gd, row_no=gsr):
            specs = []
            for bn, qty in sources:
                bid = registry.find_batch(bn)
                if bid is None:
                    report.skip("MIX", row_no, bn, "batch sumber tidak ditemukan")
                    return
                specs.append(MixingSource(batch_id=bid, quantity=qty))
            try:
                ev = record_mixing(session, MixingInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    sources=specs, final_qty=final, product_description=desk or "",
                ))
                out_link = next(l for l in ev.links if l.batch_id not in {s.batch_id for s in specs})
                if gabungan:
                    out_batch = out_link.batch
                    out_batch.batch_number = gabungan
                    try:
                        parsed = batch_number_mod.parse(gabungan)
                        out_batch.jenis_code = parsed.jenis_code
                        out_batch.grade_code = parsed.grade_code
                        out_batch.supplier_code = parsed.supplier_code
                        out_batch.receiving_date = parsed.receiving_date
                        out_batch.process_code = parsed.process_code
                    except ValueError:
                        pass
                registry.remember_batch(gabungan, out_link.batch_id)
                report.ok("MIX")
            except Exception as e:
                report.skip("MIX", row_no, gabungan or "?", f"{type(e).__name__}: {e}")

        out.append(PendingEvent(gd, RANK_MIX, "MIX", gsr, fn, str(gabungan)))
        group, group_date, group_start_row = [], None, None

    for i, row in rows_from(ws, 8):
        no, tgl, src, qty, gabungan, cp, susut, akhir, desk, pic = row[:10]
        if _date(tgl) is not None:
            group_date = _date(tgl)
        if group_start_row is None:
            group_start_row = i
        if _s(src) is not None and _d(qty) is not None:
            group.append((_s(src), _d(qty)))
        if akhir is not None:
            flush(i, gabungan, cp, susut, _d(akhir), desk, pic)
    return out


# --------------------------------------------------------------------------
# VC -- Vacuum (groups of plastic-line rows)
# --------------------------------------------------------------------------


def collect_vc(ws, report: Report) -> list[PendingEvent]:
    out = []
    current = None  # dict for the in-progress group

    def push_line(d, row):
        _no, _tgl, _batch, _desk, size, lot, qty, perpack, total, _buyer, _pic = row[:11]
        if total is None:
            return
        d["lines"].append(VacuumPlasticLine(
            total_weight=_d(total), plastic_size=_s(size), plastic_lot=_s(lot),
            plastic_qty=_d(qty), weight_per_pack=_d(perpack),
        ))

    def flush():
        nonlocal current
        if current is None:
            return
        d = current
        current = None
        if not d["lines"] or d["event_date"] is None or d["batch"] is None:
            return

        def fn(session, registry, batch=d["batch"], lines=d["lines"], desk=d["desc"],
               buyer=d["buyer"], pic=d["pic"], event_date=d["event_date"], row_no=d["row_no"]):
            batch_id = registry.find_batch(batch)
            if batch_id is None:
                report.skip("VC", row_no, batch, "batch tidak ditemukan")
                return
            try:
                record_vacuum(session, VacuumInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    batch_id=batch_id, plastic_lines=lines, product_description=desk, buyer=buyer,
                ))
                report.ok("VC")
            except Exception as e:
                report.skip("VC", row_no, batch, f"{type(e).__name__}: {e}")

        out.append(PendingEvent(d["event_date"], RANK_VC, "VC", d["row_no"], fn, str(d["batch"])))

    for i, row in rows_from(ws, 7):
        no, tgl, batch, desk, size, lot, qty, perpack, total, buyer, pic = row[:11]
        if _date(tgl) is not None:
            flush()
            current = {
                "event_date": _date(tgl), "batch": _s(batch), "desc": _s(desk),
                "buyer": _s(buyer), "pic": _s(pic), "row_no": i, "lines": [],
            }
        if current is not None:
            push_line(current, row)
    flush()
    return out


# --------------------------------------------------------------------------
# PACK -- Packing (standalone rows only; see module docstring #2)
# --------------------------------------------------------------------------


def collect_pack(ws, report: Report) -> list[PendingEvent]:
    out = []
    for i, row in rows_from(ws, 7):
        (no, tgl, nomor, tujuan, batch, desk, pembeli, berat, ukuran, lot_plastik,
         qty_plastik, lot_karton, qty_karton, amplop, bruto, tara, netto, pic) = row[:18]
        event_date = _date(tgl)
        if event_date is None:
            if _s(batch) is not None:
                report.skip("PACK", i, batch, "unsupported continuation row (no own date)")
            continue
        if _s(batch) is None or _d(bruto) is None or _d(netto) is None:
            report.skip("PACK", i, batch, "field wajib kosong (batch/bruto/netto)")
            continue

        def fn(session, registry, batch=_s(batch), netto=_d(netto), bruto=_d(bruto),
               desk=_s(desk), pembeli=_s(pembeli), ukuran=_s(ukuran), lot_plastik=_s(lot_plastik),
               qty_plastik=_d(qty_plastik), lot_karton=_s(lot_karton), qty_karton=_d(qty_karton),
               amplop=_d(amplop), nomor=_s(nomor), tujuan=_s(tujuan), pic=_s(pic),
               event_date=event_date, row_no=i):
            batch_id = registry.find_batch(batch)
            if batch_id is None:
                report.skip("PACK", row_no, batch, "batch tidak ditemukan")
                return
            try:
                ev = record_packing(session, PackingInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    sources=[PackingSource(batch_id=batch_id, quantity=netto)],
                    gross_weight=bruto, plastic_size=ukuran, plastic_lot=lot_plastik,
                    plastic_qty=qty_plastik, carton_lot=lot_karton, carton_qty=qty_karton,
                    envelope_qty=amplop, shipping_number=nomor, destination=tujuan,
                    product_description=desk, buyer=pembeli,
                ))
                out_link = next(l for l in ev.links if l.batch_id != batch_id)
                registry.remember_batch(None, out_link.batch_id)  # unlabeled, not referenceable by number
                report.ok("PACK")
            except Exception as e:
                report.skip("PACK", row_no, batch, f"{type(e).__name__}: {e}")

        out.append(PendingEvent(event_date, RANK_PACK, "PACK", i, fn, str(batch)))
    return out


# --------------------------------------------------------------------------
# PD / SmpD -- Delivery / Sample Delivery (standalone rows only)
# --------------------------------------------------------------------------


def collect_pd(ws, report: Report) -> list[PendingEvent]:
    out = []
    for i, row in rows_from(ws, 9):
        (no, tgl, nomor, batch, perusahaan, lokasi, ekspedisi, kondisi_angkut,
         kondisi_kemasan, coly, bruto, tara, netto, pic) = row[:14]
        event_date = _date(tgl)
        if event_date is None:
            if _s(batch) is not None:
                report.skip("PD", i, batch, "unsupported continuation row (no own date)")
            continue
        if _s(batch) is None or _d(bruto) is None or _d(netto) is None:
            report.skip("PD", i, batch, "field wajib kosong (batch/bruto/netto)")
            continue

        def fn(session, registry, batch=_s(batch), netto=_d(netto), bruto=_d(bruto),
               perusahaan=_s(perusahaan), lokasi=_s(lokasi), ekspedisi=_s(ekspedisi),
               kondisi_angkut=_s(kondisi_angkut), kondisi_kemasan=_s(kondisi_kemasan),
               coly=coly, nomor=_s(nomor), pic=_s(pic), event_date=event_date, row_no=i):
            batch_id = registry.find_batch(batch)
            if batch_id is None:
                report.skip("PD", row_no, batch, "batch tidak ditemukan")
                return
            try:
                record_delivery(session, DeliveryInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    sources=[DeliverySource(batch_id=batch_id, quantity=netto)],
                    gross_weight=bruto, shipping_number=nomor, destination=lokasi,
                    recipient=perusahaan, expedition=ekspedisi,
                    transport_condition=kondisi_angkut, packaging_condition=kondisi_kemasan,
                    coly=int(coly) if coly else None,
                ))
                report.ok("PD")
            except Exception as e:
                report.skip("PD", row_no, batch, f"{type(e).__name__}: {e}")

        out.append(PendingEvent(event_date, RANK_PD, "PD", i, fn, str(batch)))
    return out


def collect_smpd(ws, report: Report) -> list[PendingEvent]:
    out = []
    for i, row in rows_from(ws, 7):
        (no, tgl, nomor, batch, deskripsi, tujuan, alamat, ekspedisi, kondisi_angkut,
         kondisi_kemasan, coly, bruto, tara, netto, pic) = row[:15]
        event_date = _date(tgl)
        if event_date is None:
            if _s(batch) is not None:
                report.skip("SmpD", i, batch, "unsupported continuation row (no own date)")
            continue
        if _s(batch) is None or _d(bruto) is None or _d(netto) is None:
            report.skip("SmpD", i, batch, "field wajib kosong (batch/bruto/netto)")
            continue

        def fn(session, registry, batch=_s(batch), netto=_d(netto), bruto=_d(bruto),
               tujuan=_s(tujuan), alamat=_s(alamat), ekspedisi=_s(ekspedisi),
               kondisi_angkut=_s(kondisi_angkut), kondisi_kemasan=_s(kondisi_kemasan),
               coly=coly, nomor=_s(nomor), deskripsi=_s(deskripsi), pic=_s(pic),
               event_date=event_date, row_no=i):
            batch_id = registry.find_batch(batch)
            if batch_id is None:
                report.skip("SmpD", row_no, batch, "batch tidak ditemukan")
                return
            try:
                record_sample_delivery(session, DeliveryInput(
                    event_date=event_date, pic_user_id=registry.user_id(pic),
                    sources=[DeliverySource(batch_id=batch_id, quantity=netto)],
                    gross_weight=bruto, shipping_number=nomor, destination=alamat,
                    recipient=tujuan, expedition=ekspedisi, transport_condition=kondisi_angkut,
                    packaging_condition=kondisi_kemasan, coly=int(coly) if coly else None,
                    description=deskripsi,
                ))
                report.ok("SmpD")
            except Exception as e:
                report.skip("SmpD", row_no, batch, f"{type(e).__name__}: {e}")

        out.append(PendingEvent(event_date, RANK_PD, "SmpD", i, fn, str(batch)))
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--db", type=Path, default=Path("jas_traceability.db"))
    ap.add_argument("--report", type=Path, default=Path("import_report.txt"))
    args = ap.parse_args()

    if args.db.exists():
        args.db.unlink()

    wb = openpyxl.load_workbook(args.xlsx, data_only=True, read_only=True)
    engine = create_engine(f"sqlite:///{args.db}")
    Base.metadata.create_all(engine)

    report = Report()
    pending: list[PendingEvent] = []
    with Session(engine) as session:
        registry = Registry(session)

        pending += collect_pb(wb["PB"], registry, report)
        pending += collect_qt(wb["QT"], report)
        pending += collect_md(wb["MD"], report)
        pending += collect_steam(wb["STEAM"], report)
        pending += collect_sd(wb["SD"], report)
        pending += collect_sort(wb["SORT"], report)
        pending += collect_mix(wb["MIX"], report)
        pending += collect_vc(wb["VC"], report)
        pending += collect_pack(wb["PACK"], report)
        pending += collect_pd(wb["PD"], report)
        pending += collect_smpd(wb["SmpD"], report)

        pending.sort(key=lambda p: (p.event_date, p.rank, p.sheet, p.row_no))

        for p in pending:
            try:
                with session.begin_nested():
                    p.fn(session, registry)
            except Exception as e:
                report.skip(p.sheet, p.row_no, p.label, f"{type(e).__name__}: {e}")

        session.commit()

    lines = ["# Import report: DATA PROSES VANILA 2026 REVISI.xlsx", ""]
    lines.append("## Imported (events recorded via the service layer)")
    for sheet in ("PB", "QT", "MD", "STEAM", "SD", "SORT", "MIX", "VC", "PACK", "PD", "SmpD"):
        lines.append(f"  {sheet}: {report.imported.get(sheet, 0)}")
    lines.append("")
    lines.append(f"## Skipped ({len(report.skipped)} rows)")
    for sheet, row_no, label, reason in report.skipped:
        lines.append(f"  [{sheet}] row {row_no} batch={label!r}: {reason}")
    lines.append("")
    lines.append("## Sheets not imported (headers only, zero data rows -- verified)")
    lines.append("  grind, MG, MDPW, REW")
    lines.append("")
    lines.append("## Sheets not imported by design")
    lines.append("  TRACE (a hand-built chain-of-custody example, not a process log -- see module docstring)")
    args.report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
