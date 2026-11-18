"""Operator CLI for accounts: ``python -m qr_llm_evals.accounts.cli <command>``.

Run it on the box as the service user, with the service's environment, so it
opens the same ``<QR_EVALS_DATA_DIR>/accounts.sqlite``::

    list                         every account (no secrets are stored to show)
    disable <username>           block sign-in and launches; signs the user out
    enable <username>
    make-admin <username>
    revoke-admin <username>
    reset-password <username>    prompts for the new password; signs the user out

There is no ``delete``: runs keep their ``owner_id``, so accounts are disabled,
never removed.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from qr_llm_evals.accounts.service import (
    ACCOUNTS_DB_NAME,
    AccountError,
    Accounts,
    User,
)
from qr_llm_evals.config import load_settings

PasswordPrompt = Callable[[str], str]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qr_llm_evals.accounts.cli", description=__doc__)
    parser.add_argument("--db", help="accounts.sqlite path (default: under QR_EVALS_DATA_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for name in ("disable", "enable", "make-admin", "revoke-admin", "reset-password"):
        sub.add_parser(name).add_argument("username")
    return parser


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
    db = Path(args.db) if args.db else Path(load_settings().data_dir) / ACCOUNTS_DB_NAME
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
