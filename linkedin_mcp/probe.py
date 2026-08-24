"""One-shot live diagnostic: is the write endpoint right, or is the scope missing?

Run manually, never in tests or CI, and only with ``LINKEDIN_MCP_LIVE_PROBE=1``:

    LINKEDIN_MCP_LIVE_PROBE=1 .venv/bin/python -m linkedin_mcp --live-probe

It sends exactly ONE request to the documented headline-write endpoint, writing
back the headline the profile already has (so a success is a no-op), and reads
the answer through ``discriminate`` — a pure function, unit-tested with mocks.

Outcomes (docs/api-notes.md, "Partner gating"):

* EXPECTED_PRE_APPROVAL — endpoint shape verified, setup correct, LinkedIn is
  refusing because partner approval has not landed. Wait; do not work around it.
* SPEC_ERROR           — the endpoint spec has drifted. Report it as a failure
  and re-verify docs/api-notes.md against the live docs. Do not work around it.
* WRITE_OK             — partner access is live and the write path works.
* UNKNOWN              — anything else; reported as a failure, never as a pass.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from . import api, config

LIVE_PROBE_ENV = "LINKEDIN_MCP_LIVE_PROBE"

EXPECTED_PRE_APPROVAL = "EXPECTED_PRE_APPROVAL"
SPEC_ERROR = "SPEC_ERROR"
WRITE_OK = "WRITE_OK"
UNKNOWN = "UNKNOWN"

_PRE_APPROVAL_STATUSES = (401, 403)
_PRE_APPROVAL_MARKERS = (
    "invalid scope",
    "invalid_scope",
    "access_denied",
    "permission-denied",
    "permission denied",
    "not enough permissions",
    "unpermitted",
)
_SPEC_ERROR_STATUSES = (400, 404, 405, 422)

_EXPLANATIONS = {
    EXPECTED_PRE_APPROVAL: (
        "The endpoint shape is right and the setup is correct — LinkedIn is refusing "
        "because partner approval for the Profile Edit API has not landed yet. This is "
        "the expected outcome before approval. Wait for the partner program; do not "
        "work around it."
    ),
    SPEC_ERROR: (
        "LinkedIn did not recognise the request. The spec this server was built from "
        "has drifted. Report this as a FAILURE: re-verify docs/api-notes.md against the "
        "current Microsoft Learn docs before changing any code."
    ),
    WRITE_OK: (
        "Partner access is live and the write path works. From here the workflow is "
        "propose_edit -> your approval -> apply_proposal."
    ),
    UNKNOWN: (
        "Unrecognised response. Treat as a FAILURE and inspect the raw output below "
        "rather than assuming the endpoint is fine."
    ),
}


class ProbeRefused(RuntimeError):
    """Raised when the probe is invoked without the explicit opt-in env flag."""


class ProbeError(RuntimeError):
    """Raised when the probe cannot run (no credentials, no profile, etc.)."""


def _as_text(body: Any) -> str:
    if isinstance(body, str):
        return body.lower()
    try:
        return json.dumps(body).lower()
    except (TypeError, ValueError):
        return str(body).lower()


def discriminate(status_code: int, body: Any) -> dict[str, Any]:
    """Pure classifier — no I/O, no state. The whole point of the probe."""
    text = _as_text(body)

    if 200 <= status_code < 300:
        outcome = WRITE_OK
    elif status_code in _PRE_APPROVAL_STATUSES or any(
        marker in text for marker in _PRE_APPROVAL_MARKERS
    ):
        outcome = EXPECTED_PRE_APPROVAL
    elif status_code in _SPEC_ERROR_STATUSES:
        outcome = SPEC_ERROR
    else:
        outcome = UNKNOWN

    return {
        "outcome": outcome,
        "status_code": status_code,
        "explanation": _EXPLANATIONS[outcome],
        "is_failure": outcome in (SPEC_ERROR, UNKNOWN),
    }


def _require_opt_in() -> None:
    if config.get(LIVE_PROBE_ENV) != "1":
        raise ProbeRefused(
            f"refusing to run the live probe: set {LIVE_PROBE_ENV}=1 to opt in. "
            "It sends one REAL request to LinkedIn and must never run in tests or CI."
        )


def run_live_probe(transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Send ONE real write request and classify the answer. Manual use only."""
    _require_opt_in()

    token = config.access_token()
    if not token:
        raise ProbeError(
            "no access token stored — run the auth_start tool before probing "
            f"(expected in {config.env_path()})"
        )

    with api.LinkedInClient(token, transport=transport) as client:
        try:
            profile = client.get_profile()
        except httpx.HTTPError as exc:
            raise ProbeError(f"could not read the profile before probing: {exc}") from exc

        if not isinstance(profile, dict) or not profile.get("id"):
            raise ProbeError(
                "the profile read did not return an id, so the write URL cannot be "
                f"built; raw response: {profile!r}"
            )

        person_id = str(profile["id"])
        headline = profile.get("localizedHeadline") or "headline"
        prepared = api.build_headline_request(person_id, headline)
        result = client.send(prepared)

    verdict = discriminate(result["status_code"], result["body"])
    return {
        **verdict,
        "request": {"method": prepared.method, "url": prepared.url},
        "response_body": result["body"],
    }


def format_report(result: dict[str, Any]) -> str:
    lines = [
        "linkedin-mcp live probe",
        f"  request : {result['request']['method']} {result['request']['url']}",
        f"  status  : HTTP {result['status_code']}",
        f"  outcome : {result['outcome']}",
        f"  meaning : {result['explanation']}",
        f"  raw body: {result['response_body']!r}",
    ]
    return "\n".join(lines)
