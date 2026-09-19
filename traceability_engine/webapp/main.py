"""FastAPI application entrypoint for the JAS Traceability UI (Fase 15).

Run with: `uvicorn traceability_engine.webapp.main:app --reload`
(or `python -m traceability_engine.webapp.main` for a quick local run).

Every domain exception the engine defines (`exceptions.py`) is caught here
and turned into a structured JSON error instead of a bare 500 -- this is
formatting, not a new rule: the *decision* that an event is invalid was
already made by `services/events.py` before it ever raised.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..exceptions import (
    InsufficientStockError,
    InvalidEventStructureError,
    QuantityReconciliationError,
    TraceabilityError,
    UnauthorizedAdjustmentError,
    UnauthorizedDispositionError,
)
from .database import init_db
from .routers import (
    adjustment,
    batches,
    curing,
    delivery,
    master_data,
    mixing,
    powder,
    qc_md,
    receiving,
    rendemen,
    date_order,
    disposition,
    rework,
    sortation,
    steam_dry,
    stock,
    traceability,
    vacuum_packing,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="PT JAS Traceability",
    description="Fase 15 UI integration layer over traceability_engine.services.*",
    lifespan=_lifespan,
)


def _error_response(status_code: int, error: str, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "detail": str(exc)})


@app.exception_handler(QuantityReconciliationError)
async def _reconciliation_handler(request: Request, exc: QuantityReconciliationError):
    return _error_response(422, "quantity_reconciliation_error", exc)


@app.exception_handler(InvalidEventStructureError)
async def _structure_handler(request: Request, exc: InvalidEventStructureError):
    return _error_response(422, "invalid_event_structure", exc)


@app.exception_handler(InsufficientStockError)
async def _stock_handler(request: Request, exc: InsufficientStockError):
    return _error_response(409, "insufficient_stock", exc)


@app.exception_handler(UnauthorizedAdjustmentError)
async def _unauthorized_handler(request: Request, exc: UnauthorizedAdjustmentError):
    return _error_response(403, "unauthorized_adjustment", exc)


@app.exception_handler(UnauthorizedDispositionError)
async def _unauthorized_disposition_handler(request: Request, exc: UnauthorizedDispositionError):
    return _error_response(403, "unauthorized_disposition", exc)


@app.exception_handler(TraceabilityError)
async def _domain_handler(request: Request, exc: TraceabilityError):
    return _error_response(422, "domain_error", exc)


@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError):
    # services/*.py raise plain ValueError for simple field-level checks
    # (e.g. "net_quantity must be positive") that predate/sit alongside the
    # richer TraceabilityError hierarchy -- same treatment, still a 422.
    return _error_response(422, "invalid_input", exc)


api_prefix = "/api"
app.include_router(master_data.router, prefix=api_prefix)
app.include_router(receiving.router, prefix=api_prefix)
app.include_router(qc_md.router, prefix=api_prefix)
app.include_router(steam_dry.router, prefix=api_prefix)
app.include_router(curing.router, prefix=api_prefix)
app.include_router(sortation.router, prefix=api_prefix)
app.include_router(mixing.router, prefix=api_prefix)
app.include_router(powder.router, prefix=api_prefix)
app.include_router(rework.router, prefix=api_prefix)
app.include_router(vacuum_packing.router, prefix=api_prefix)
app.include_router(delivery.router, prefix=api_prefix)
app.include_router(adjustment.router, prefix=api_prefix)
app.include_router(stock.router, prefix=api_prefix)
app.include_router(traceability.router, prefix=api_prefix)
app.include_router(rendemen.router, prefix=api_prefix)
app.include_router(date_order.router, prefix=api_prefix)
app.include_router(disposition.router, prefix=api_prefix)
app.include_router(batches.router, prefix=api_prefix)

_static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("traceability_engine.webapp.main:app", host="0.0.0.0", port=8000, reload=True)
