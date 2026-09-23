"""Fase 47 -- login, logout, sesi, dan manajemen kredensial user.

Endpoint di sini SENGAJA TIDAK dipasangi dependency global
`get_current_user` (lihat main.py `include_router`) kecuali yang memang
butuh sesi (`/auth/logout`, `/auth/me`, `/auth/change-password`,
`/auth/users/{user_id}/credentials` -- yang terakhir ini juga mensyaratkan
role Production Manager lewat `require_manager`). `/auth/login` harus bisa
diakses tanpa sesi apa pun, itulah sebabnya router ini didaftarkan terpisah
tanpa dependency router-level di `main.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy.orm import Session

from ...models import User
from ...services import auth as auth_service
from ..database import get_db
from ..dependencies import SESSION_COOKIE_NAME, get_current_user
from ..schemas import ChangePasswordIn, LoginIn, MeOut, SetCredentialsIn, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=int(auth_service.SESSION_LIFETIME.total_seconds()),
    )


@router.post("/login", response_model=MeOut)
def login(payload: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, username=payload.username, password=payload.password)
    session_row = auth_service.create_session(db, user_id=user.user_id)
    _set_session_cookie(response, session_row.session_token)
    must_change = _must_change(db, user.user_id)
    return MeOut(user_id=user.user_id, name=user.name, role=user.role.value, must_change_password=must_change)


def _must_change(db: Session, user_id: int) -> bool:
    from sqlalchemy import select

    from ...models import UserCredential

    credential = db.execute(
        select(UserCredential).where(UserCredential.user_id == user_id)
    ).scalar_one_or_none()
    return bool(credential and credential.must_change_password)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
):
    auth_service.revoke_session(db, session_token)
    response.delete_cookie(SESSION_COOKIE_NAME)


@router.get("/me", response_model=MeOut)
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    must_change = _must_change(db, current_user.user_id)
    return MeOut(
        user_id=current_user.user_id,
        name=current_user.name,
        role=current_user.role.value,
        must_change_password=must_change,
    )


@router.post("/change-password", status_code=204)
def change_password(
    payload: ChangePasswordIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    auth_service.change_password(
        db,
        user_id=current_user.user_id,
        old_password=payload.old_password,
        new_password=payload.new_password,
    )


@router.post("/users/{user_id}/credentials", response_model=UserOut, status_code=201)
def set_user_credentials(
    user_id: int,
    payload: SetCredentialsIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Production Manager membuat/mengganti login untuk `User` yang sudah
    ada (dibuat lewat `POST /users`, master_data.py). Password awal dikirim
    lewat jalur lain oleh PM ke pemilik akun (mis. lisan/WA) -- di luar
    lingkup sistem ini, sama seperti keputusan user 2026-09-23."""
    from fastapi import HTTPException

    from ...enums import UserRole
    from ...exceptions import UnauthorizedDispositionError

    if current_user.role != UserRole.PRODUCTION_MANAGER:
        raise UnauthorizedDispositionError(
            "Hanya Production Manager yang boleh membuat/mengganti login user."
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    auth_service.set_credentials(
        db, user_id=user_id, username=payload.username, password=payload.password
    )
    return user
