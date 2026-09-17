"""Fase 15 -- UI integration layer.

This package is a thin presentation/API layer over the domain engine
(`traceability_engine.services.*`). Per CLAUDE.md ("Keep business logic
separate from UI") and 15_UI.md ("Do not duplicate business rules in
UI"), nothing in here re-implements validation, reconciliation, stock, or
genealogy logic -- every write endpoint does exactly three things: (1)
parse/validate the HTTP request shape into the dataclass the relevant
`services.*` function already expects, (2) call that function, (3) shape
its return value (or the exception it raises) into an HTTP response.

Scope of this slice (see PROJECT_STATUS.md "Fase 15" for the full note):
Receiving, QC Test, Metal Detection, Steaming, Sundrying, plus generic
master-data (Suppliers/Users) and batch listing/history endpoints. The
router layout (one router per domain module, sharing the same
`get_db`/error-handling plumbing) is meant to be extended by future
sessions one `services/*.py` module at a time (Sortation, Mixing, Powder,
Rework, Vacuum/Packing, Delivery, Stock, Traceability) without changing
this scaffolding.
"""
