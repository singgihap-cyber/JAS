# traceability-engine

Core domain/traceability engine for the PT Java Agro Spices (PT JAS)
vanilla traceability system -- Fase 3 of the project (see the
`Traceablitiy Tools` Claude project docs: `PROJECT_STATUS.md`,
`GENEALOGY.md`, `DATABASE_DESIGN.md`, `BATCH_NUMBER_SPEC.md`).

> **Note on history:** this repository's `phase-3-core-engine` checkpoint
> was reconstructed on 2026-09-14 after the original cloud session/container
> that produced it was reclaimed before the code could be pushed anywhere
> durable. It was rebuilt from the Phase 2/3 documentation (which did
> survive, in the Claude project) rather than recovered byte-for-byte.
> Nothing about the design changed as a result -- see PROJECT_STATUS.md.

## Stack

Python + SQLAlchemy 2.0. SQLite in-memory for the test suite, PostgreSQL in
production -- no code difference, since only SQLAlchemy-portable column
types are used.

## Layout

```
traceability_engine/
    models.py       Batch, ProcessEvent, EventBatchLink, QualityTest,
                     StockTransaction, Shipment, Supplier, Customer, User,
                     AuditLog (DATABASE_DESIGN.md)
    enums.py         All domain enums
    exceptions.py    Domain error types
    batch_number.py  parse() (both 2- and 3-digit supplier widths);
                     generate() intentionally raises -- see
                     BATCH_NUMBER_SPEC.md, AA segment still [UNCONFIRMED]
    services/
        events.py      record_process_event() -- the one generic engine
                        function for every process type (Receiving, QC, MD,
                        Steaming, Sundrying, Sortation, Mixing, Vacuum,
                        Packing, Grinding, Magnetization, MD-Powder, Rework,
                        Delivery, Sample Delivery)
        adjustment.py  record_adjustment() (gated to PRODUCTION_MANAGER),
                       mark_batch_rejected(), mark_batch_superseded()
        genealogy.py   backward_trace(), forward_trace(), resolve_suppliers(),
                       find_terminal_leaves()
        traceability.py  full_trace(), chain_of_custody_report() -- report
                          layer over genealogy.py (Fase 14)
webapp/                Fase 15 -- UI integration layer (see below)
tests/                 pytest suite, covers TEST_CASES.md #1-14 plus the
                        webapp/API layer (test_webapp_api.py)
```

## Running tests

```
pip install -r requirements.txt
pytest
```

## Running the web UI (Fase 15)

A thin FastAPI layer (`traceability_engine/webapp/`) sits on top of the
engine -- it adds no business rules of its own (see
`traceability_engine/webapp/__init__.py`'s docstring): every write endpoint
just shapes an HTTP request into the dataclass the matching
`services/*.py` function already expects, calls it, and shapes the
result/exception back into JSON. The static frontend
(`webapp/static/index.html` + `app.js`) is equally thin -- it renders
whatever the API returns and never recomputes a business decision
client-side.

```
pip install -r requirements.txt
uvicorn traceability_engine.webapp.main:app --reload
```

Then open http://127.0.0.1:8000/ in a browser. Defaults to a local SQLite
file (`jas_traceability.db` in the working directory, gitignored) unless
`DATABASE_URL` is set (e.g. `postgresql+psycopg2://user:pass@host/db` for
production).

**Slice 1 of N** (this checkpoint): Penerimaan Barang (Receiving), QC Test,
Metal Detection, Steaming, Sundrying, plus Suppliers/Users master data and
batch listing/history. Sortation, Mixing, Powder, Rework, Vacuum/Packing,
Delivery, Stock, and Traceability follow in later sessions -- see
PROJECT_STATUS.md "Fase 15" for the plan and the router/page pattern to
extend.

## Known, documented blockers (see PROJECT_STATUS.md for the full list)

- `batch_number.generate()` is not implemented (AA/Jenis segment
  [UNCONFIRMED]). Callers must supply `batch_number` externally, or leave it
  `None`, until PT JAS confirms the segment.
- The full User role/permission matrix is [UNCONFIRMED] beyond
  `PRODUCTION_MANAGER` (stock opname / `ADJUSTMENT`).
