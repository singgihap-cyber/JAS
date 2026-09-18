"""Suppliers, Users, Customers -- master data needed by every process form
(Supplier picker on Receiving, PIC picker everywhere, Customer picker on
Delivery). Plain CRUD-lite (list + create); no engine logic to defer to
here since these are just the `Supplier`/`User`/`Customer` tables Fase 2/3
already fixed.

Customer is added here in Fase 15 slice 5 (Delivery) -- `Customer` has
existed as an empty master-data table since Fase 3, but nothing wrote to
it until now. Per services/delivery.py #7, this router itself performs no
matching logic -- that free-text-to-Customer matching/fuzzy-lookup gap
(Fase 12 poin 7) is now resolved by `services/customer_matching.py` (Fase
21) and exposed below as `GET /customers/match`, a thin wrapper following
the same thin-router convention as every other stage. This CRUD-lite
endpoint still lets a user optionally create/pick a real Customer row up
front; `Shipment.recipient` (free text) is always recorded regardless, so
leaving `customer_id` unset remains a fully supported path, not a degraded
one -- matching only assists that choice, per customer_matching.py #1 it
never makes it automatically.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...enums import UserRole
from ...models import Customer, Supplier, User
from ...services.customer_matching import match_customer
from ..database import get_db
from ..schemas import (
    CustomerCreate,
    CustomerMatchOut,
    CustomerOut,
    SupplierCreate,
    SupplierOut,
    UserCreate,
    UserOut,
)

router = APIRouter(tags=["master-data"])


@router.get("/suppliers", response_model=list[SupplierOut])
def list_suppliers(db: Session = Depends(get_db)):
    return db.execute(select(Supplier).order_by(Supplier.supplier_code)).scalars().all()


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
def create_supplier(payload: SupplierCreate, db: Session = Depends(get_db)):
    supplier = Supplier(supplier_code=payload.supplier_code, name=payload.name, active=True)
    db.add(supplier)
    db.flush()
    return supplier


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.execute(select(User).order_by(User.name)).scalars().all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    user = User(name=payload.name, role=UserRole(payload.role))
    db.add(user)
    db.flush()
    return user


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(db: Session = Depends(get_db)):
    return db.execute(select(Customer).order_by(Customer.name)).scalars().all()


@router.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db)):
    customer = Customer(name=payload.name)
    db.add(customer)
    db.flush()
    return customer


@router.get("/customers/match", response_model=list[CustomerMatchOut])
def match_customers(
    q: str = Query(..., min_length=1, description="Free-text recipient name to match, e.g. the Delivery/Sample Delivery 'Perusahaan Penerima' field"),
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Fase 21 -- ranked `Customer` suggestions for a free-text name, thin
    wrapper over `services/customer_matching.match_customer()`. Suggestion
    only (customer_matching.py #1) -- the caller (UI) still decides whether
    to attribute `customer_id`, create a new `Customer`, or leave it unset.
    A static path, registered after `/customers` (list/create) but with no
    ordering hazard: there is no `/customers/{customer_id}` dynamic route in
    this router to shadow.
    """
    return [
        CustomerMatchOut(customer_id=m.customer_id, name=m.name, score=m.score, exact=m.exact)
        for m in match_customer(db, q, limit=limit)
    ]
