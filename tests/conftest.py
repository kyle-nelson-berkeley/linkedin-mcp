"""Shared test fixtures.

SAFETY: no test in this suite makes a network call. Every httpx client is
constructed with an httpx.MockTransport; the stdio boot test launches the
server with an empty config dir and never triggers an outbound request.
"""

from __future__ import annotations

import os
import socket

import pytest

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


class OutboundNetworkBlocked(AssertionError):
    """Raised if any test tries to open a socket off this machine."""


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch):
    """Hard guarantee: no test reaches LinkedIn (or anything else remote).

    Loopback stays open because the OAuth redirect catcher is a real localhost
    HTTP server. Everything else must go through httpx.MockTransport.
    """
    real_connect = socket.socket.connect

    def guarded_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in _LOCAL_HOSTS:
            raise OutboundNetworkBlocked(
                f"test attempted an outbound connection to {host!r}; "
                "use httpx.MockTransport instead"
            )
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)

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
