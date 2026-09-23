"""Fase 47 -- login & sesi.

Password di-hash dengan PBKDF2-HMAC-SHA256 (stdlib `hashlib`, tanpa
dependency baru seperti passlib/bcrypt -- konsisten dengan gaya proyek ini
yang meminimalkan dependency, lihat `requirements.txt`) dengan salt acak 16
byte per user dan 200_000 iterasi.

Token sesi: `secrets.token_urlsafe(32)`, disimpan sebagai primary key
`UserSession.session_token` dan dikembalikan ke klien lewat cookie HttpOnly
(`webapp/routers/auth.py`). Sesi tidak "sliding" -- `expires_at` ditetapkan
saat dibuat dan tidak diperpanjang otomatis saat dipakai
([UNCONFIRMED] durasi default; lihat `SESSION_LIFETIME`).

Tidak ada tabel/alter pada `users` -- `UserCredential`/`UserSession` adalah
tabel baru terpisah (models.py), pola sama dengan setiap tabel baru sejak
Fase 38 (`create_all` cukup untuk DB produksi).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..exceptions import InvalidCredentialsError, NotLoggedInError
from ..models import User, UserCredential, UserSession

_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16
SESSION_LIFETIME = dt.timedelta(hours=8)


def _hash_password(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return digest.hex()


def _new_salt() -> str:
    return secrets.token_hex(_SALT_BYTES)


def set_credentials(
    db: Session,
    *,
    user_id: int,
    username: str,
    password: str,
    must_change_password: bool = True,
) -> UserCredential:
    """Buat atau ganti kredensial login untuk sebuah `User` yang sudah ada
    (Production Manager lewat halaman admin, atau reset password). Tidak
    memeriksa role pemanggil di sini -- itu tanggung jawab router/dependency
    (`webapp/dependencies.require_manager`), sama seperti pola
    `services/*.py` lain yang menerima `actor_user_id` sudah tervalidasi."""
    salt = _new_salt()
    password_hash = _hash_password(password, salt)

    existing = db.execute(
        select(UserCredential).where(UserCredential.user_id == user_id)
    ).scalar_one_or_none()
    if existing is not None:
        existing.username = username
        existing.password_hash = password_hash
        existing.password_salt = salt
        existing.must_change_password = must_change_password
        db.flush()
        return existing

    credential = UserCredential(
        user_id=user_id,
        username=username,
        password_hash=password_hash,
        password_salt=salt,
        must_change_password=must_change_password,
    )
    db.add(credential)
    db.flush()
    return credential


def authenticate(db: Session, *, username: str, password: str) -> User:
    """Cek username+password. Pesan galat generik (tidak membedakan
    "username tidak ada" vs "password salah") supaya tidak membocorkan
    username terdaftar."""
    credential = db.execute(
        select(UserCredential).where(UserCredential.username == username)
    ).scalar_one_or_none()
    if credential is None:
        raise InvalidCredentialsError("Username atau password salah.")

    candidate_hash = _hash_password(password, credential.password_salt)
    if not secrets.compare_digest(candidate_hash, credential.password_hash):
        raise InvalidCredentialsError("Username atau password salah.")

    user = db.get(User, credential.user_id)
    if user is None:  # pragma: no cover -- integritas data, seharusnya tidak terjadi
        raise InvalidCredentialsError("Username atau password salah.")
    return user


def create_session(db: Session, *, user_id: int) -> UserSession:
    token = secrets.token_urlsafe(32)
    now = dt.datetime.utcnow()
    session_row = UserSession(
        session_token=token,
        user_id=user_id,
        created_at=now,
        expires_at=now + SESSION_LIFETIME,
    )
    db.add(session_row)
    db.flush()
    return session_row


def get_user_by_session_token(db: Session, token: str | None) -> User | None:
    """None kalau token kosong/tidak ada/kedaluwarsa/dicabut -- pemanggil
    (dependency FastAPI) yang memutuskan apakah itu berarti 401."""
    if not token:
        return None
    session_row = db.get(UserSession, token)
    if session_row is None or session_row.revoked_at is not None:
        return None
    if session_row.expires_at < dt.datetime.utcnow():
        return None
    return db.get(User, session_row.user_id)


def require_logged_in_user(db: Session, token: str | None) -> User:
    user = get_user_by_session_token(db, token)
    if user is None:
        raise NotLoggedInError("Sesi tidak valid atau sudah berakhir. Silakan login kembali.")
    return user


def revoke_session(db: Session, token: str | None) -> None:
    if not token:
        return
    session_row = db.get(UserSession, token)
    if session_row is not None and session_row.revoked_at is None:
        session_row.revoked_at = dt.datetime.utcnow()
        db.flush()


def change_password(db: Session, *, user_id: int, old_password: str, new_password: str) -> None:
    """Ganti password sendiri (butuh password lama) -- dipakai alur
    'must_change_password' setelah login pertama."""
    credential = db.execute(
        select(UserCredential).where(UserCredential.user_id == user_id)
    ).scalar_one_or_none()
    if credential is None:
        raise InvalidCredentialsError("Akun ini belum punya login.")
    candidate_hash = _hash_password(old_password, credential.password_salt)
    if not secrets.compare_digest(candidate_hash, credential.password_hash):
        raise InvalidCredentialsError("Password lama salah.")

    salt = _new_salt()
    credential.password_hash = _hash_password(new_password, salt)
    credential.password_salt = salt
    credential.must_change_password = False
    db.flush()


def has_login(db: Session, *, user_id: int) -> bool:
    return (
        db.execute(select(UserCredential).where(UserCredential.user_id == user_id)).scalar_one_or_none()
        is not None
    )
