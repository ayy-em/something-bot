"""Tests for the scheduler OIDC token verification dependency."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from something_really_bot.main import app, job_registry

EXPECTED_SA_EMAIL = "something-bot-scheduler-sa@something-bot-338300.iam.gserviceaccount.com"

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
def _configured_scheduler_email(monkeypatch: pytest.MonkeyPatch):
    """Make the verifier read a known scheduler SA email from Settings."""
    from something_really_bot import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("SCHEDULER_SERVICE_ACCOUNT_EMAIL", EXPECTED_SA_EMAIL)
    yield
    config.get_settings.cache_clear()


def _stub_verify(claims: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace google-auth's id_token verification with a fixed claims dict."""
    from something_really_bot.services import scheduler_auth

    monkeypatch.setattr(scheduler_auth, "_verify_token", lambda _token: claims)


def test_jobs_call_without_oidc_returns_401(_configured_scheduler_email) -> None:
    response = client.post("/jobs/test-job")
    assert response.status_code == 401


def test_jobs_call_with_empty_bearer_returns_401(_configured_scheduler_email) -> None:
    response = client.post("/jobs/test-job", headers={"Authorization": "Bearer "})
    assert response.status_code == 401


def test_jobs_call_with_invalid_token_returns_401(
    _configured_scheduler_email, monkeypatch: pytest.MonkeyPatch
) -> None:
    from something_really_bot.services import scheduler_auth

    def _raise(_token: str) -> Any:
        raise ValueError("bad signature")

    monkeypatch.setattr(scheduler_auth, "_verify_token", _raise)

    response = client.post("/jobs/test-job", headers={"Authorization": "Bearer faketoken"})
    assert response.status_code == 401


def test_jobs_call_with_wrong_sa_email_returns_403(
    _configured_scheduler_email, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_verify({"email": "intruder@evil.example"}, monkeypatch)

    response = client.post("/jobs/test-job", headers={"Authorization": "Bearer faketoken"})
    assert response.status_code == 403


def test_jobs_call_with_valid_oidc_dispatches_to_registered_handler(
    _configured_scheduler_email,
    monkeypatch: pytest.MonkeyPatch,
    _register_test_job: _TestJob,
) -> None:
    _stub_verify({"email": EXPECTED_SA_EMAIL}, monkeypatch)

    response = client.post("/jobs/test-job", headers={"Authorization": "Bearer faketoken"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "job": "test-job"}
    assert _register_test_job.calls == 1


def test_jobs_call_with_unknown_name_returns_404(
    _configured_scheduler_email, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_verify({"email": EXPECTED_SA_EMAIL}, monkeypatch)

    response = client.post(
        "/jobs/never-registered",
        headers={"Authorization": "Bearer faketoken"},
    )

    assert response.status_code == 404


def test_jobs_call_records_job_history_on_success(
    _configured_scheduler_email,
    monkeypatch: pytest.MonkeyPatch,
    _register_test_job: _TestJob,
    stub_job_history,
) -> None:
    """A successful scheduled-job run lands one ``succeeded`` row (#53)."""
    _stub_verify({"email": EXPECTED_SA_EMAIL}, monkeypatch)

    response = client.post("/jobs/test-job", headers={"Authorization": "Bearer faketoken"})

    assert response.status_code == 200
    assert len(stub_job_history.rows) == 1
    row = stub_job_history.rows[0]
    assert row.job_name == "test-job"
    assert row.status == "succeeded"
    assert row.chat_id is None
    assert row.user_id is None
    assert row.error_class is None


def test_jobs_call_records_job_history_on_failure(
    _configured_scheduler_email,
    monkeypatch: pytest.MonkeyPatch,
    stub_job_history,
) -> None:
    """A scheduled job that raises lands one ``failed`` row and the 5xx still surfaces."""
    _stub_verify({"email": EXPECTED_SA_EMAIL}, monkeypatch)

    class _Boom:
        name = "boom-job"

        async def run(self, _ctx: Any) -> None:
            raise RuntimeError("scheduled crash")

    monkeypatch.setattr(job_registry, "_handlers", {"boom-job": _Boom()})

    quiet_client = TestClient(app, raise_server_exceptions=False)
    response = quiet_client.post("/jobs/boom-job", headers={"Authorization": "Bearer faketoken"})

    assert response.status_code == 500
    assert len(stub_job_history.rows) == 1
    row = stub_job_history.rows[0]
    assert row.job_name == "boom-job"
    assert row.status == "failed"
    assert row.error_class == "RuntimeError"
    assert row.error_message == "scheduled crash"


def test_jobs_call_accepts_additional_sa_email(
    _configured_scheduler_email,
    monkeypatch: pytest.MonkeyPatch,
    _register_test_job: _TestJob,
) -> None:
    """An email listed in SCHEDULER_ADDITIONAL_EMAILS is accepted."""
    from something_really_bot import config

    extra_email = "deployer@something-bot-338300.iam.gserviceaccount.com"
    config.get_settings.cache_clear()
    monkeypatch.setenv("SCHEDULER_ADDITIONAL_EMAILS", extra_email)
    _stub_verify({"email": extra_email}, monkeypatch)

    response = client.post("/jobs/test-job", headers={"Authorization": "Bearer faketoken"})

    assert response.status_code == 200
    assert _register_test_job.calls == 1
    config.get_settings.cache_clear()


def test_jobs_call_without_scheduler_sa_configured_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SCHEDULER_SERVICE_ACCOUNT_EMAIL is unset, /jobs/* is hard-disabled."""
    from something_really_bot import config

    config.get_settings.cache_clear()
    monkeypatch.delenv("SCHEDULER_SERVICE_ACCOUNT_EMAIL", raising=False)

    response = client.post("/jobs/test-job", headers={"Authorization": "Bearer faketoken"})

    assert response.status_code == 401
    config.get_settings.cache_clear()
