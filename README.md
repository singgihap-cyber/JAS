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
tests/                 pytest suite, covers TEST_CASES.md #1-14
```

## Running tests

```
pip install -r requirements.txt
pytest
```

## Known, documented blockers (see PROJECT_STATUS.md for the full list)

- `batch_number.generate()` is not implemented (AA/Jenis segment
  [UNCONFIRMED]). Callers must supply `batch_number` externally, or leave it
  `None`, until PT JAS confirms the segment.
- The full User role/permission matrix is [UNCONFIRMED] beyond
  `PRODUCTION_MANAGER` (stock opname / `ADJUSTMENT`).
