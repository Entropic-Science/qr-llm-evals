"""Account routes: sign up, log in, log out, and the linked API key.

``POST /auth/signup``
    Username + password + a working api.entropic.science key. The key is
    verified through the same :class:`~qr_llm_evals.keys.verify.KeyVerifier`
    that gates launches; the account is created only if it passes. The key is
    then dropped: the account keeps its SHA-256, name and fingerprint.
``POST /auth/login`` / ``POST /auth/logout``
    Exchange credentials for a session token; revoke it.
``GET /me``
    The signed-in account.
``POST /me/key``
    Link a different key (verified the same way).
``GET /me/usage``
    Token totals and the quota for the signed-in account.

Signup and login are rate-limited per client address, and login per username
as well. Login failures do not say whether the username exists.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request

from qr_llm_evals.accounts import (
    AccountDisabled,
    BadCredentials,
    InvalidPassword,
    InvalidUsername,
    IssuedSession,
    KeyAlreadyLinked,
    User,
    UsernameTaken,
)
from qr_llm_evals.api.deps import AppState, CurrentUser, State
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.ratelimit import enforce, enforce_subject
from qr_llm_evals.api.schema import (
    AuthResponse,
    LinkKeyRequest,
    LoginRequest,
    LogoutResponse,
    SignupRequest,
    StorageResponse,
    StorageRunView,
    UsageResponse,
    UserView,
)
from qr_llm_evals.api.usage_views import usage_response
from qr_llm_evals.keys.verify import (
    KeyIdentity,
    KeyRefused,
    KeyVerifyUnavailable,
    parse_bearer,
    sha256_hex,
)

log = logging.getLogger("qr_llm_evals.api.auth")

router = APIRouter(tags=["auth"])


def user_view(user: User) -> UserView:
    return UserView(
        user_id=user.user_id,
        username=user.username,
        key_name=user.key_name,
        key_fingerprint=user.key_fingerprint,
        key_linked_at=user.key_linked_at,
        is_admin=user.is_admin,
        created_at=user.created_at,
    )


def _auth_response(user: User, session: IssuedSession) -> AuthResponse:
    return AuthResponse(
        user=user_view(user), session_token=session.token, expires_at=session.expires_at
    )


async def _verify_key(state: AppState, api_key: str) -> KeyIdentity:
    """Verify a presented key or raise the matching 4xx/503. The key is not kept."""
    try:
        return await state.verifier.averify(api_key.strip())
    except KeyRefused as err:
        raise ApiError(
            403, f"this API key was not accepted ({err.reason})", param="api_key"
        ) from err
    except KeyVerifyUnavailable as err:
        raise ApiError(
            503,
            f"API keys cannot be verified right now: {err}",
            headers={"Retry-After": "10"},
        ) from err


@router.post("/auth/signup", response_model=AuthResponse, status_code=201)
async def signup(body: SignupRequest, request: Request, state: State) -> AuthResponse:
    enforce(state.limiter, request, scope="signup")
    if not state.settings.signup_enabled:
        raise ApiError(403, "registration is closed")
    identity = await _verify_key(state, body.api_key)
    try:
        # scrypt takes tens of milliseconds: keep it off the event loop.
        user = await asyncio.to_thread(
            state.accounts.create_user,
            body.username,
            body.password,
            key_sha256=sha256_hex(body.api_key.strip()),
            key_name=identity.key_name,
            key_fingerprint=identity.key_fingerprint,
        )
    except InvalidUsername as err:
        raise ApiError(400, str(err), param="username") from err
    except InvalidPassword as err:
        raise ApiError(400, str(err), param="password") from err
    except UsernameTaken as err:
        raise ApiError(409, str(err), param="username") from err
    except KeyAlreadyLinked as err:
        raise ApiError(409, str(err), param="api_key") from err
    session = state.accounts.create_session(user.user_id)
    log.info(
        "account created",
        extra={"event": "auth.signup", "user_id": user.user_id, "key_name": user.quota_id},
    )
    return _auth_response(user, session)


@router.post("/auth/login", response_model=AuthResponse)
async def login(body: LoginRequest, request: Request, state: State) -> AuthResponse:
    enforce(state.limiter, request, scope="login")
    # Also per username, so guesses against one account are slowed whatever
    # addresses they come from.
    enforce_subject(state.limiter, "login-user", body.username.strip().lower())
    try:
        user = await asyncio.to_thread(state.accounts.authenticate, body.username, body.password)
    except BadCredentials as err:
        raise ApiError(401, str(err)) from err
    except AccountDisabled as err:
        raise ApiError(403, str(err)) from err
    session = state.accounts.create_session(user.user_id)
    log.info("signed in", extra={"event": "auth.login", "user_id": user.user_id})
    return _auth_response(user, session)


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout(request: Request, user: CurrentUser, state: State) -> LogoutResponse:
    token = parse_bearer(request.headers.get("authorization"))
    if token is not None:
        state.accounts.revoke_session(token)
    log.info("signed out", extra={"event": "auth.logout", "user_id": user.user_id})
    return LogoutResponse()


@router.get("/me", response_model=UserView)
async def me(user: CurrentUser) -> UserView:
    return user_view(user)


@router.get("/me/usage", response_model=UsageResponse)
async def my_usage(user: CurrentUser, state: State) -> UsageResponse:
    return usage_response(state.ledger, user)


@router.get("/me/storage", response_model=StorageResponse)
async def my_storage(user: CurrentUser, state: State) -> StorageResponse:
    """Disk used by the account's runs; download a run's study bundle to keep a copy."""
    sized = state.storage.runs_of(state.store, user.user_id)
    return StorageResponse(
        total_bytes=sum(item.size_bytes for item in sized),
        runs=[
            StorageRunView(
                run_id=item.run.run_id,
                study_id=item.run.study_id,
                state=item.run.state,
                created_at=item.run.created_at,
                size_bytes=item.size_bytes,
            )
            for item in sized
        ],
    )


@router.post("/me/key", response_model=UserView)
async def link_key(body: LinkKeyRequest, user: CurrentUser, state: State) -> UserView:
    identity = await _verify_key(state, body.api_key)
    try:
        updated = state.accounts.link_key(
            user.user_id,
            key_sha256=sha256_hex(body.api_key.strip()),
            key_name=identity.key_name,
            key_fingerprint=identity.key_fingerprint,
        )
    except KeyAlreadyLinked as err:
        raise ApiError(409, str(err), param="api_key") from err
    log.info(
        "key linked",
        extra={"event": "auth.key", "user_id": user.user_id, "key_name": updated.quota_id},
    )
    return user_view(updated)


__all__ = ["router", "user_view"]
