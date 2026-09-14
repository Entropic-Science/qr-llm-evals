"""Repo-root pytest configuration for qr-llm-evals.

Live-marker gating (pattern from qr-llm-research): registers the ``--run-live``
CLI flag and auto-skips any test marked ``@pytest.mark.live``. Without the flag
the suite is green on a developer laptop with no on-box services.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.live (require live on-box services).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live to execute")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
