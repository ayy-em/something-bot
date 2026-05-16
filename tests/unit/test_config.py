"""Tests for :mod:`something_really_bot.config`."""

import json

import pytest
from pydantic import ValidationError

from something_really_bot.config import Settings, get_settings

JM_TG_ID = 135499785
IRINDICA_CHAT_ID = 159278882


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")


def test_settings_loads_required_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Settings`` reads the webhook secret and bot token from the environment."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "from-env")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-tok-from-env")

    settings = Settings(_env_file=None)

    assert settings.telegram_webhook_secret.get_secret_value() == "from-env"
    assert settings.telegram_bot_token.get_secret_value() == "bot-tok-from-env"


def test_settings_fails_fast_when_webhook_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_fails_fast_when_bot_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "x")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_qa_user_ids_extracts_jm_and_irindica(monkeypatch: pytest.MonkeyPatch) -> None:
    """JM and IRINDICA keys are parsed into the allowlist."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv(
        "TELEGRAM_QA_USERS",
        json.dumps({"JM_TG_ID": JM_TG_ID, "IRINDICA_CHAT_ID": IRINDICA_CHAT_ID}),
    )

    settings = Settings(_env_file=None)

    assert settings.telegram_qa_user_ids == frozenset({JM_TG_ID, IRINDICA_CHAT_ID})


def test_irindica_chat_id_parsed_from_same_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv(
        "TELEGRAM_QA_USERS",
        json.dumps({"JM_TG_ID": JM_TG_ID, "IRINDICA_CHAT_ID": IRINDICA_CHAT_ID}),
    )

    settings = Settings(_env_file=None)

    assert settings.irindica_chat_id == IRINDICA_CHAT_ID


def test_irindica_chat_id_none_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_QA_USERS", json.dumps({"JM_TG_ID": JM_TG_ID}))

    settings = Settings(_env_file=None)

    assert settings.irindica_chat_id is None
    # And the allowlist still works partially
    assert settings.telegram_qa_user_ids == frozenset({JM_TG_ID})


def test_irindica_chat_id_none_when_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_QA_USERS", "{not valid")

    settings = Settings(_env_file=None)

    assert settings.irindica_chat_id is None


def test_qa_user_ids_ignores_non_qa_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Other secret keys (group/channel targets) are not allowlisted."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv(
        "TELEGRAM_QA_USERS",
        json.dumps(
            {
                "JM_TG_ID": JM_TG_ID,
                "IRINDICA_CHAT_ID": IRINDICA_CHAT_ID,
                "PSDLK_CHAT_ID": -111,
                "FC_GROUP": -222,
                "TEST_CHANNEL": -333,
            }
        ),
    )

    settings = Settings(_env_file=None)

    assert settings.telegram_qa_user_ids == frozenset({JM_TG_ID, IRINDICA_CHAT_ID})


def test_qa_user_ids_partial_when_one_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_QA_USERS", json.dumps({"JM_TG_ID": JM_TG_ID}))

    settings = Settings(_env_file=None)

    assert settings.telegram_qa_user_ids == frozenset({JM_TG_ID})


def test_qa_user_ids_empty_when_neither_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_QA_USERS", json.dumps({"PSDLK_CHAT_ID": -111}))

    settings = Settings(_env_file=None)

    assert settings.telegram_qa_user_ids == frozenset()


def test_qa_user_ids_empty_on_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_QA_USERS", "{not valid json")

    settings = Settings(_env_file=None)

    assert settings.telegram_qa_user_ids == frozenset()


def test_qa_user_ids_empty_when_payload_is_not_a_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_QA_USERS", json.dumps([JM_TG_ID, IRINDICA_CHAT_ID]))

    settings = Settings(_env_file=None)

    assert settings.telegram_qa_user_ids == frozenset()


def test_qa_user_ids_default_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.delenv("TELEGRAM_QA_USERS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.telegram_qa_user_ids == frozenset()


def test_get_settings_is_cached() -> None:
    """``get_settings`` returns the same instance on repeated calls."""
    get_settings.cache_clear()

    first = get_settings()
    second = get_settings()

    assert first is second
