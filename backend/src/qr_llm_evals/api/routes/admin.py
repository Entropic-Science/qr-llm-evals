"""Admin routes: every account's usage and quota, and the switches on an account.

``GET /admin/users``
    All accounts with their token totals and quota.
``POST /admin/users/{user_id}``
    Change the token quota, disable or enable the account, grant or revoke
    admin. Only the fields present in the body change. An admin cannot disable
    or demote their own account, so the last admin cannot lock everyone out
    from the API (the operator CLI can still do either).

Admin is a flag on an account, set with ``python -m qr_llm_evals.accounts.cli
make-admin <username>`` on the box.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from qr_llm_evals.accounts import Quota, UnknownUser, UsageTotals, User
from qr_llm_evals.api.deps import AdminUser, AppState, State
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.routes.auth import user_view
from qr_llm_evals.api.schema import AdminUsersResponse, AdminUserUpdate, AdminUserView
from qr_llm_evals.api.usage_views import quota_view, totals_view

log = logging.getLogger("qr_llm_evals.api.admin")

router = APIRouter(tags=["admin"])


def _admin_view(state: AppState, user: User, totals: UsageTotals) -> AdminUserView:
    quota = Quota(limit=state.ledger.limit_for(user), used=totals.charged_tokens)
    return AdminUserView(
        user=user_view(user),
        disabled=user.disabled,
        token_quota=user.token_quota,
        quota=quota_view(quota),
        totals=totals_view(totals),
    )


@router.get("/admin/users", response_model=AdminUsersResponse)
async def list_users(admin: AdminUser, state: State) -> AdminUsersResponse:
    totals = state.ledger.totals_by_user()
    return AdminUsersResponse(
        default_token_quota=state.ledger.default_quota,
        users=[
            _admin_view(state, user, totals.get(user.user_id, UsageTotals()))
            for user in state.accounts.list_users()
        ],
    )


@router.post("/admin/users/{user_id}", response_model=AdminUserView)
async def update_user(
    user_id: str, body: AdminUserUpdate, admin: AdminUser, state: State
) -> AdminUserView:
    try:
        user = state.accounts.get_user(user_id)
    except UnknownUser as err:
        raise ApiError(404, f"user {user_id!r} not found", param="user_id") from err
    changed = body.model_fields_set
    if user.user_id == admin.user_id and (body.disabled is True or body.is_admin is False):
        raise ApiError(409, "an admin cannot disable or demote their own account")
    if "token_quota" in changed:
        user = state.accounts.set_quota(user_id, body.token_quota)
    if body.disabled is not None:
        user = state.accounts.set_disabled(user_id, body.disabled)
    if body.is_admin is not None:
        user = state.accounts.set_admin(user_id, body.is_admin)
    log.info(
        "account updated by admin",
        extra={
            "event": "admin.user_update",
            "admin_id": admin.user_id,
            "user_id": user_id,
            "fields": sorted(changed),
        },
    )
    return _admin_view(state, user, state.ledger.totals(user_id))


__all__ = ["router"]
