"""User accounts, linked-key digests and login sessions.

Layer 0: imports nothing internal except :mod:`qr_llm_evals.config`. The API
layer verifies a qr-llm-api key and hands this package only its digest, name
and fingerprint; plaintext keys never reach it.
"""

from __future__ import annotations

from qr_llm_evals.accounts.service import (
    ACCOUNTS_DB_NAME,
    SESSION_PREFIX,
    AccountDisabled,
    AccountError,
    Accounts,
    BadCredentials,
    InvalidPassword,
    InvalidUsername,
    IssuedSession,
    KeyAlreadyLinked,
    UnknownUser,
    User,
    UsernameTaken,
)
from qr_llm_evals.accounts.usage import (
    Prices,
    Quota,
    Settlement,
    UsageBucket,
    UsageEvent,
    UsageLedger,
    UsageSummary,
    UsageTotals,
)

__all__ = [
    "ACCOUNTS_DB_NAME",
    "SESSION_PREFIX",
    "AccountDisabled",
    "AccountError",
    "Accounts",
    "BadCredentials",
    "InvalidPassword",
    "InvalidUsername",
    "IssuedSession",
    "KeyAlreadyLinked",
    "Prices",
    "Quota",
    "Settlement",
    "UnknownUser",
    "UsageBucket",
    "UsageEvent",
    "UsageLedger",
    "UsageSummary",
    "UsageTotals",
    "User",
    "UsernameTaken",
]
