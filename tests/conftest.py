"""Shared fixtures: an isolated SQLite file per test session, and a client."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

import pytest

# Settings are read at import time, so the env must be set before any app import.
_TMPDIR = tempfile.mkdtemp(prefix="markaz-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMPDIR}/test.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("BCRYPT_ROUNDS", "4")  # fast hashing in tests

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_client(client: TestClient) -> TestClient:
    """A client already signed in as a fresh, uniquely-named user."""
    import uuid  # noqa: PLC0415

    email = f"user-{uuid.uuid4().hex[:10]}@markaz.dev"
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "test-password-123", "display_name": "Test User"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
