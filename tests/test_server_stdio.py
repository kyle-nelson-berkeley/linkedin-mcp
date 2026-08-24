"""Proof (e): the server really boots over stdio and lists all seven tools.

The subprocess is launched with an empty temp config dir and no token, so it
cannot and does not make any outbound request; the only I/O is the MCP
handshake over its own stdin/stdout pipes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from linkedin_mcp import server

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

EXPECTED_TOOLS = {
    "auth_start",
    "auth_status",
    "get_profile",
    "propose_edit",
    "list_proposals",
    "discard_proposal",
    "apply_proposal",
}

PROTOCOL_VERSION = "2024-11-05"


def _frame(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode()


def _python() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def test_stdio_server_lists_all_seven_tools(isolated_config):
    env = {
        **os.environ,
        "LINKEDIN_MCP_CONFIG_DIR": str(isolated_config),
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        [_python(), "-m", "linkedin_mcp"],
        cwd=str(REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        process.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "0"},
                    },
                }
            )
        )
        process.stdin.flush()
        initialize_response = json.loads(process.stdout.readline())

        process.stdin.write(
            _frame({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        process.stdin.write(
            _frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        )
        process.stdin.flush()
        tools_response = json.loads(process.stdout.readline())
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
        process.stdout.close()
        process.stderr.close()

    assert initialize_response["result"]["serverInfo"]["name"] == server.SERVER_NAME

    tools = tools_response["result"]["tools"]
    assert {tool["name"] for tool in tools} == EXPECTED_TOOLS


def test_tool_descriptions_encode_the_workflow_contract():
    registry = {tool.name: tool.description for tool in server.mcp._tool_manager.list_tools()}
    assert set(registry) == EXPECTED_TOOLS

    propose = registry["propose_edit"].lower()
    assert "never writes" in propose
    assert "diff" in propose

    apply_description = registry["apply_proposal"].lower()
    assert "only tool that writes" in apply_description
    assert "approved" in apply_description
    assert "expected" in apply_description and "scope" in apply_description
