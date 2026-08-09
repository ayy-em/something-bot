"""Tests for the hand-invoked job route (`GET /jobs/<name>?token=…`)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from something_really_bot.main import app, job_registry

TOKEN = "test-manual-job-token"  # noqa: S105 — synthetic value, not a real secret

client = TestClient(app)


class _TestJob:
    name = "test-job"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, _ctx: Any) -> None:
        self.calls += 1


@pytest.fixture(autouse=True)
def _register_test_job(monkeypatch: pytest.MonkeyPatch) -> _TestJob:
    job = _TestJob()
    monkeypatch.setattr(job_registry, "_handlers", {job.name: job})
    return job


@pytest.fixture
def _configured_token(monkeypatch: pytest.MonkeyPatch):
    from something_really_bot import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("MANUAL_JOB_TOKEN", TOKEN)
    yield
    config.get_settings.cache_clear()


def test_manual_get_with_valid_token_dispatches(
    _configured_token, _register_test_job: _TestJob
) -> None:
    response = client.get(f"/jobs/test-job?token={TOKEN}")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "job": "test-job"}
    assert _register_test_job.calls == 1


def test_manual_get_without_token_returns_401(
    _configured_token, _register_test_job: _TestJob
) -> None:
    response = client.get("/jobs/test-job")

    assert response.status_code == 401
    assert _register_test_job.calls == 0


def test_manual_get_with_wrong_token_returns_401(
    _configured_token, _register_test_job: _TestJob
) -> None:
    response = client.get("/jobs/test-job?token=not-the-token")

    assert response.status_code == 401
    assert _register_test_job.calls == 0


def test_manual_get_unconfigured_token_returns_401(
    monkeypatch: pytest.MonkeyPatch, _register_test_job: _TestJob
) -> None:
    """With MANUAL_JOB_TOKEN unset the GET route is hard-disabled."""
    from something_really_bot import config

    config.get_settings.cache_clear()
    monkeypatch.delenv("MANUAL_JOB_TOKEN", raising=False)

    response = client.get(f"/jobs/test-job?token={TOKEN}")

    assert response.status_code == 401
    assert _register_test_job.calls == 0
    config.get_settings.cache_clear()


def test_manual_get_with_unknown_job_returns_404(_configured_token) -> None:
    response = client.get(f"/jobs/never-registered?token={TOKEN}")

    assert response.status_code == 404


def test_manual_get_records_job_history(
    _configured_token, _register_test_job: _TestJob, stub_job_history
) -> None:
    """A hand-run job lands the same history row a scheduled one would (#53)."""
    response = client.get(f"/jobs/test-job?token={TOKEN}")

    assert response.status_code == 200
    assert len(stub_job_history.rows) == 1
    assert stub_job_history.rows[0].job_name == "test-job"
    assert stub_job_history.rows[0].status == "succeeded"


def test_valid_manual_token_does_not_open_the_oidc_post_route(_configured_token) -> None:
    """The token gates GET only — POST still demands a scheduler OIDC token."""
    response = client.post(f"/jobs/test-job?token={TOKEN}")

    assert response.status_code == 401
