"""Suppliers and Users -- master data needed by every process form (Supplier
picker on Receiving, PIC picker everywhere). Plain CRUD-lite (list +
create); no engine logic to defer to here since these are just the
`Supplier`/`User` tables Fase 2/3 already fixed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...enums import UserRole
from ...models import Supplier, User
from ..database import get_db
from ..schemas import SupplierCreate, SupplierOut, UserCreate, UserOut

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
