"""Fase 47 -- login & sesi (services/auth.py, webapp/dependencies.py).

Menguji lapisan service secara langsung (pola sama dengan
test_disposition.py/test_event_correction.py dll.) -- tes HTTP end-to-end
(login lewat cookie, actor_mismatch lewat API) ada di test_webapp_api.py.
"""
from __future__ import annotations

import datetime as dt

import pytest

from traceability_engine.enums import UserRole
from traceability_engine.exceptions import ActorMismatchError, InvalidCredentialsError, NotLoggedInError
from traceability_engine.models import User, UserSession
from traceability_engine.services import auth as auth_service
from traceability_engine.webapp.dependencies import require_actor_matches


@pytest.fixture()
def staff(session):
    user = User(name="Wakhidah", role=UserRole.STAFF)
    session.add(user)
    session.flush()
    return user


def test_set_credentials_then_authenticate_succeeds(session, production_manager):
    auth_service.set_credentials(
        session, user_id=production_manager.user_id, username="robiah", password="Secret123!"
    )
    user = auth_service.authenticate(session, username="robiah", password="Secret123!")
    assert user.user_id == production_manager.user_id


def test_authenticate_wrong_password_is_generic_error(session, production_manager):
    auth_service.set_credentials(
        session, user_id=production_manager.user_id, username="robiah", password="Secret123!"
    )
    with pytest.raises(InvalidCredentialsError):
        auth_service.authenticate(session, username="robiah", password="salah")


def test_authenticate_unknown_username_is_same_generic_error(session):
    with pytest.raises(InvalidCredentialsError):
        auth_service.authenticate(session, username="tidak-ada", password="apapun")


def test_set_credentials_defaults_must_change_password_true(session, production_manager):
    cred = auth_service.set_credentials(
        session, user_id=production_manager.user_id, username="robiah", password="Secret123!"
    )
    assert cred.must_change_password is True


def test_reset_credentials_overwrites_existing(session, production_manager):
    auth_service.set_credentials(
        session, user_id=production_manager.user_id, username="robiah", password="Old12345"
    )
    auth_service.set_credentials(
        session, user_id=production_manager.user_id, username="robiah", password="New12345"
    )
    # password lama tidak lagi valid, password baru valid
    with pytest.raises(InvalidCredentialsError):
        auth_service.authenticate(session, username="robiah", password="Old12345")
    user = auth_service.authenticate(session, username="robiah", password="New12345")
    assert user.user_id == production_manager.user_id


def test_create_session_and_get_user_by_token_roundtrip(session, production_manager):
    session_row = auth_service.create_session(session, user_id=production_manager.user_id)
    user = auth_service.get_user_by_session_token(session, session_row.session_token)
    assert user is not None and user.user_id == production_manager.user_id


def test_get_user_by_session_token_none_for_missing_or_empty(session):
    assert auth_service.get_user_by_session_token(session, None) is None
    assert auth_service.get_user_by_session_token(session, "tidak-ada") is None


def test_get_user_by_session_token_none_when_expired(session, production_manager):
    session_row = auth_service.create_session(session, user_id=production_manager.user_id)
    session_row.expires_at = dt.datetime.utcnow() - dt.timedelta(seconds=1)
    session.flush()
    assert auth_service.get_user_by_session_token(session, session_row.session_token) is None


def test_revoke_session_invalidates_it(session, production_manager):
    session_row = auth_service.create_session(session, user_id=production_manager.user_id)
    token = session_row.session_token
    auth_service.revoke_session(session, token)
    assert auth_service.get_user_by_session_token(session, token) is None
    revoked = session.get(UserSession, token)
    assert revoked.revoked_at is not None


def test_revoke_session_is_idempotent_and_ignores_empty(session, production_manager):
    session_row = auth_service.create_session(session, user_id=production_manager.user_id)
    auth_service.revoke_session(session, session_row.session_token)
    auth_service.revoke_session(session, session_row.session_token)  # tidak boleh error
    auth_service.revoke_session(session, None)  # tidak boleh error


def test_require_logged_in_user_raises_when_no_valid_session(session):
    with pytest.raises(NotLoggedInError):
        auth_service.require_logged_in_user(session, None)
    with pytest.raises(NotLoggedInError):
        auth_service.require_logged_in_user(session, "token-ngawur")


def test_require_logged_in_user_returns_user_for_valid_session(session, production_manager):
    session_row = auth_service.create_session(session, user_id=production_manager.user_id)
    user = auth_service.require_logged_in_user(session, session_row.session_token)
    assert user.user_id == production_manager.user_id


def test_change_password_requires_correct_old_password(session, production_manager):
    auth_service.set_credentials(
        session, user_id=production_manager.user_id, username="robiah", password="Secret123!"
    )
    with pytest.raises(InvalidCredentialsError):
        auth_service.change_password(
            session, user_id=production_manager.user_id, old_password="salah", new_password="Baru123!"
        )


def test_change_password_success_clears_must_change_flag(session, production_manager):
    auth_service.set_credentials(
        session, user_id=production_manager.user_id, username="robiah", password="Secret123!"
    )
    auth_service.change_password(
        session, user_id=production_manager.user_id, old_password="Secret123!", new_password="Baru123!"
    )
    user = auth_service.authenticate(session, username="robiah", password="Baru123!")
    assert user.user_id == production_manager.user_id
    assert auth_service.has_login(session, user_id=production_manager.user_id) is True


def test_change_password_without_existing_login_raises(session, staff):
    with pytest.raises(InvalidCredentialsError):
        auth_service.change_password(
            session, user_id=staff.user_id, old_password="x", new_password="Baru123!"
        )


def test_has_login_false_before_credentials_set(session, staff):
    assert auth_service.has_login(session, user_id=staff.user_id) is False


# ------------------------------------------------------- require_actor_matches
def test_require_actor_matches_passes_when_same_user(production_manager):
    require_actor_matches(production_manager.user_id, production_manager)  # tidak boleh raise


def test_require_actor_matches_raises_when_different_user(production_manager, staff):
    with pytest.raises(ActorMismatchError):
        require_actor_matches(staff.user_id, production_manager)
