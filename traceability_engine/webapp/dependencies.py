"""Fase 47 -- dependency FastAPI untuk login/sesi.

`get_current_user` dipasang sebagai dependency di level `include_router(...)`
di `main.py` untuk SETIAP router kecuali `auth.router` sendiri -- jadi
seluruh API (selain `POST /auth/login`) mensyaratkan sesi valid tanpa perlu
mengubah signature tiap fungsi endpoint satu per satu. FastAPI meng-cache
resolusi dependency per request (kunci: callable + parameter), jadi
endpoint yang JUGA butuh objek `User` (mis. untuk `require_actor_matches`)
tinggal menambahkan `current_user: User = Depends(get_current_user)` di
signature-nya sendiri -- tidak dieksekusi dua kali dalam satu request.
"""
from __future__ import annotations

from fastapi import Cookie, Depends
from sqlalchemy.orm import Session

from ..exceptions import ActorMismatchError
from ..models import User
from ..services import auth as auth_service
from .database import get_db

SESSION_COOKIE_NAME = "session_token"


def get_current_user(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> User:
    return auth_service.require_logged_in_user(db, session_token)


def require_actor_matches(actor_user_id: int, current_user: User) -> None:
    """Tutup celah penyamaran (Fase 47): `actor_user_id` di body request
    harus sama dengan user yang sedang login. Dipanggil eksplisit di setiap
    endpoint yang menerima `actor_user_id` (12 titik di 5 router -- lihat
    PROJECT_STATUS.md Fase 47), bukan lewat dependency global, karena
    `actor_user_id` ada di dalam body Pydantic, bukan path/query/cookie."""
    if actor_user_id != current_user.user_id:
        raise ActorMismatchError(
            f"actor_user_id={actor_user_id} tidak sama dengan user yang sedang login "
            f"(user_id={current_user.user_id})."
        )
