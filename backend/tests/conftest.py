import os

# Point the app at the throwaway test database before anything imports
# app.config, so tests can never drop tables in the real one.
if os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

import pytest


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch):
    monkeypatch.setattr("app.sources.base.RETRY_BACKOFF_SECONDS", 0)
