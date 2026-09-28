"""Test isolation from the developer's .env and MSRKIT_* variables."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never load ./.env, and drop MSRKIT_* overrides set in the shell."""
    monkeypatch.setenv("MSRKIT_NO_DOTENV", "1")
    for key in list(os.environ):
        if key.startswith("MSRKIT_") and key not in {"MSRKIT_NO_DOTENV", "MSRKIT_RECORD"}:
            monkeypatch.delenv(key)
