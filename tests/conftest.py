"""Shared test fixtures.

SAFETY: no test in this suite makes a network call. Every httpx client is
constructed with an httpx.MockTransport; the stdio boot test launches the
server with an empty config dir and never triggers an outbound request.
"""

from __future__ import annotations

import os

import pytest

CONFIG_DIR_ENV = "LINKEDIN_MCP_CONFIG_DIR"

SECRET_ENV_KEYS = (
    "LINKEDIN_CLIENT_ID",
    "LINKEDIN_CLIENT_SECRET",
    "LINKEDIN_ACCESS_TOKEN",
    "LINKEDIN_ACCESS_TOKEN_EXPIRES_AT",
    "LINKEDIN_REFRESH_TOKEN",
    "LINKEDIN_REDIRECT_URI",
    "LINKEDIN_MCP_LIVE_PROBE",
)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point every test at a throwaway config dir and scrub real env values."""
    config_dir = tmp_path / "config"
    monkeypatch.setenv(CONFIG_DIR_ENV, str(config_dir))
    for key in SECRET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert os.environ[CONFIG_DIR_ENV] == str(config_dir)
    return config_dir
