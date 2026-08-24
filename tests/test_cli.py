"""CLI entry point — including that --live-probe refuses without the opt-in flag.

No test here ever lets the probe reach the network: the refusal path returns
before a client is built, and the "flag set" case is stubbed.
"""

from __future__ import annotations

import pytest

from linkedin_mcp import __main__ as cli
from linkedin_mcp import probe


def test_live_probe_refuses_without_the_env_flag(isolated_config, capsys):
    exit_code = cli.main(["--live-probe"])
    assert exit_code == cli.EXIT_REFUSED
    assert probe.LIVE_PROBE_ENV in capsys.readouterr().err


def test_live_probe_reports_probe_errors_as_failure(isolated_config, monkeypatch, capsys):
    monkeypatch.setenv(probe.LIVE_PROBE_ENV, "1")
    exit_code = cli.main(["--live-probe"])
    assert exit_code == cli.EXIT_FAILURE
    assert "could not run" in capsys.readouterr().err


def test_live_probe_prints_a_report_and_exits_zero_on_expected_outcome(
    isolated_config, monkeypatch, capsys
):
    monkeypatch.setattr(
        probe,
        "run_live_probe",
        lambda: {
            **probe.discriminate(403, {"message": "Not enough permissions"}),
            "request": {"method": "POST", "url": "https://api.linkedin.com/v2/people/(id:X)"},
            "response_body": {"message": "Not enough permissions"},
        },
    )
    exit_code = cli.main(["--live-probe"])
    out = capsys.readouterr().out
    assert exit_code == cli.EXIT_OK
    assert probe.EXPECTED_PRE_APPROVAL in out
    assert "partner" in out.lower()


def test_live_probe_exits_nonzero_on_spec_error(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr(
        probe,
        "run_live_probe",
        lambda: {
            **probe.discriminate(404, {"message": "Not Found"}),
            "request": {"method": "POST", "url": "https://api.linkedin.com/v2/people/(id:X)"},
            "response_body": {"message": "Not Found"},
        },
    )
    assert cli.main(["--live-probe"]) == cli.EXIT_FAILURE
    assert probe.SPEC_ERROR in capsys.readouterr().out


def test_default_invocation_starts_the_stdio_server(isolated_config, monkeypatch):
    from linkedin_mcp import server

    started: list[bool] = []
    monkeypatch.setattr(server, "run", lambda: started.append(True))
    assert cli.main([]) == cli.EXIT_OK
    assert started == [True]


def test_unknown_flag_is_rejected(isolated_config):
    with pytest.raises(SystemExit):
        cli.main(["--send-everything"])
