"""Suppliers, Users, Customers -- master data needed by every process form
(Supplier picker on Receiving, PIC picker everywhere, Customer picker on
Delivery). Plain CRUD-lite (list + create); no engine logic to defer to
here since these are just the `Supplier`/`User`/`Customer` tables Fase 2/3
already fixed.

Customer is added here in Fase 15 slice 5 (Delivery) -- `Customer` has
existed as an empty master-data table since Fase 3, but nothing wrote to
it until now. Per services/delivery.py #7, this engine layer performs NO
free-text-to-Customer matching/fuzzy-lookup -- that remains [UNCONFIRMED]
(Fase 12 poin 7). This CRUD-lite endpoint only lets a user optionally
create/pick a real Customer row up front; `Shipment.recipient` (free text)
is always recorded regardless, so leaving `customer_id` unset is a fully
supported path, not a degraded one.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...enums import UserRole
from ...models import Customer, Supplier, User
from ..database import get_db
from ..schemas import CustomerCreate, CustomerOut, SupplierCreate, SupplierOut, UserCreate, UserOut

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
