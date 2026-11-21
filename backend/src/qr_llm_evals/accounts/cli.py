"""Operator CLI for accounts: ``python -m qr_llm_evals.accounts.cli <command>``.

Run it on the box as the service user, with the service's environment, so it
opens the same ``<QR_EVALS_DATA_DIR>/accounts.sqlite``::

    list                         every account (no secrets are stored to show)
    disable <username>           block sign-in and launches; signs the user out
    enable <username>
    make-admin <username>
    revoke-admin <username>
    reset-password <username>    prompts for the new password; signs the user out
    set-quota <username> <n>     lifetime token budget: a number, 0 = unlimited,
                                 'default' = follow QR_EVALS_DEFAULT_TOKEN_QUOTA
    balance <username>           what the account owes at the configured prices
    settle <username> [--note]   record that the account paid what it owes now

There is no ``delete``: runs keep their ``owner_id``, so accounts are disabled,
never removed.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from qr_llm_evals.accounts.service import (
    ACCOUNTS_DB_NAME,
    AccountError,
    Accounts,
    User,
)
from qr_llm_evals.accounts.usage import MICRO, Prices, UsageLedger
from qr_llm_evals.config import Settings, load_settings

PasswordPrompt = Callable[[str], str]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qr_llm_evals.accounts.cli", description=__doc__)
    parser.add_argument("--db", help="accounts.sqlite path (default: under QR_EVALS_DATA_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for name in ("disable", "enable", "make-admin", "revoke-admin", "reset-password"):
        sub.add_parser(name).add_argument("username")
    quota = sub.add_parser("set-quota")
    quota.add_argument("username")
    quota.add_argument("tokens", help="a non-negative integer, or 'default'")
    sub.add_parser("balance").add_argument("username")
    settle = sub.add_parser("settle")
    settle.add_argument("username")
    settle.add_argument("--note", help="free text kept with the settlement (e.g. an invoice id)")
    return parser


def _ledger(accounts: Accounts, settings: Settings) -> UsageLedger:
    return UsageLedger(
        accounts,
        default_quota=settings.default_token_quota,
        prices=Prices(
            qrng_input=settings.price_qrng_input,
            qrng_output=settings.price_qrng_output,
            prng_input=settings.price_prng_input,
            prng_output=settings.price_prng_output,
        ),
    )


def _usd(microusd: int) -> str:
    return f"${microusd / MICRO:,.2f}"


def _row(user: User) -> str:
    flags = ",".join(
        flag for flag, on in (("admin", user.is_admin), ("disabled", user.disabled)) if on
    )
    return (
        f"{user.username:<32}  {user.user_id}  key={user.quota_id:<24}  "
        f"created={user.created_at.date().isoformat()}  {flags}"
    ).rstrip()


def _require(accounts: Accounts, username: str) -> User:
    user = accounts.find_user(username)
    if user is None:
        raise AccountError(f"no account named {username!r}")
    return user


def run(argv: Sequence[str] | None = None, *, prompt: PasswordPrompt = getpass.getpass) -> int:
    args = _parser().parse_args(argv)
    settings = load_settings()
    db = Path(args.db) if args.db else Path(settings.data_dir) / ACCOUNTS_DB_NAME
    accounts = Accounts(db)
    try:
        if args.command == "list":
            users = accounts.list_users()
            for user in users:
                print(_row(user))
            print(f"{len(users)} account(s)")
            return 0
        user = _require(accounts, args.username)
        if args.command == "disable":
            accounts.set_disabled(user.user_id, True)
        elif args.command == "enable":
            accounts.set_disabled(user.user_id, False)
        elif args.command == "make-admin":
            accounts.set_admin(user.user_id, True)
        elif args.command == "revoke-admin":
            accounts.set_admin(user.user_id, False)
        elif args.command == "set-quota":
            if args.tokens == "default":
                accounts.set_quota(user.user_id, None)
            elif args.tokens.isdigit():
                accounts.set_quota(user.user_id, int(args.tokens))
            else:
                raise AccountError("tokens must be a non-negative integer or 'default'")
        elif args.command == "balance":
            print(f"{user.username}: {_usd(_ledger(accounts, settings).outstanding(user.user_id))}")
            return 0
        elif args.command == "settle":
            settlement = _ledger(accounts, settings).settle(
                user.user_id, settled_by="cli", at=datetime.now(UTC), note=args.note
            )
            print(f"settle: {user.username} paid {_usd(settlement.amount_microusd)}")
            return 0
        else:
            first = prompt("New password: ")
            if first != prompt("Repeat: "):
                raise AccountError("passwords do not match")
            accounts.set_password(user.user_id, first)
        print(f"{args.command}: {user.username}")
        return 0
    except AccountError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    finally:
        accounts.close()


if __name__ == "__main__":
    raise SystemExit(run())
