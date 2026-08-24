"""Config/credential store tests — proof (d) for the .env half of storage."""

from __future__ import annotations

import os
import stat

import pytest

from linkedin_mcp import config


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_config_dir_follows_env_override(isolated_config):
    assert config.config_dir() == isolated_config


def test_reads_values_from_env_file(isolated_config):
    isolated_config.mkdir(parents=True)
    (isolated_config / ".env").write_text(
        "# a comment\n"
        "LINKEDIN_CLIENT_ID=abc123\n"
        '\n'
        'LINKEDIN_CLIENT_SECRET="quoted-secret"\n'
        "export LINKEDIN_ACCESS_TOKEN=tok\n"
    )
    assert config.get("LINKEDIN_CLIENT_ID") == "abc123"
    assert config.get("LINKEDIN_CLIENT_SECRET") == "quoted-secret"
    assert config.get("LINKEDIN_ACCESS_TOKEN") == "tok"
    assert config.get("LINKEDIN_MISSING") is None
    assert config.get("LINKEDIN_MISSING", "fallback") == "fallback"


def test_process_env_takes_precedence(isolated_config, monkeypatch):
    isolated_config.mkdir(parents=True)
    (isolated_config / ".env").write_text("LINKEDIN_CLIENT_ID=from-file\n")
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "from-process")
    assert config.get("LINKEDIN_CLIENT_ID") == "from-process"


def test_missing_env_file_is_not_an_error(isolated_config):
    assert config.get("LINKEDIN_CLIENT_ID") is None


def test_require_raises_with_scrubbed_message(isolated_config):
    with pytest.raises(config.ConfigError) as excinfo:
        config.require("LINKEDIN_CLIENT_SECRET")
    assert "LINKEDIN_CLIENT_SECRET" in str(excinfo.value)


def test_write_values_creates_0600_file_in_0700_dir(isolated_config):
    config.write_values({"LINKEDIN_ACCESS_TOKEN": "tok-1"})
    env_path = isolated_config / ".env"
    assert env_path.exists()
    assert _mode(env_path) == 0o600
    assert _mode(isolated_config) == 0o700
    assert config.get("LINKEDIN_ACCESS_TOKEN") == "tok-1"


def test_write_values_updates_in_place_and_preserves_others(isolated_config):
    config.write_values({"LINKEDIN_CLIENT_ID": "cid", "LINKEDIN_ACCESS_TOKEN": "old"})
    config.write_values({"LINKEDIN_ACCESS_TOKEN": "new"})
    text = (isolated_config / ".env").read_text()
    assert text.count("LINKEDIN_ACCESS_TOKEN=") == 1
    assert config.get("LINKEDIN_ACCESS_TOKEN") == "new"
    assert config.get("LINKEDIN_CLIENT_ID") == "cid"
    assert _mode(isolated_config / ".env") == 0o600


def test_scrub_hides_secret_values():
    payload = {
        "LINKEDIN_CLIENT_ID": "public-ish",
        "LINKEDIN_CLIENT_SECRET": "sup3rsecret",
        "access_token": "AQVtok",
        "refresh_token": "rtok",
        "note": "fine",
    }
    scrubbed = config.scrub(payload)
    assert scrubbed["LINKEDIN_CLIENT_SECRET"] == config.REDACTED
    assert scrubbed["access_token"] == config.REDACTED
    assert scrubbed["refresh_token"] == config.REDACTED
    assert scrubbed["note"] == "fine"
    assert scrubbed["LINKEDIN_CLIENT_ID"] == "public-ish"
    assert "sup3rsecret" not in repr(scrubbed)


def test_scrub_recurses_into_nested_structures():
    scrubbed = config.scrub({"outer": {"CLIENT_SECRET": "s"}, "list": [{"token": "t"}]})
    assert scrubbed["outer"]["CLIENT_SECRET"] == config.REDACTED
    assert scrubbed["list"][0]["token"] == config.REDACTED


def test_token_helpers_roundtrip(isolated_config):
    secret = "AQVfake-access-value"
    config.save_token(access_token=secret, expires_in=5184000, refresh_token="rfsh-value")
    status = config.token_status()
    assert status["has_token"] is True
    assert status["expires_at"] > 0
    assert status["expired"] is False
    assert secret not in repr(status)
    assert "rfsh-value" not in repr(status)
    assert config.get("LINKEDIN_REFRESH_TOKEN") == "rfsh-value"
    assert config.access_token() == secret


def test_token_status_reports_missing_token(isolated_config):
    status = config.token_status()
    assert status["has_token"] is False
    assert status["expired"] is True


def test_token_status_reports_expiry_in_the_past(isolated_config):
    config.save_token(access_token="tok", expires_in=-10)
    assert config.token_status()["expired"] is True
