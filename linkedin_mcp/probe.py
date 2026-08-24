"""One-shot live diagnostic: is the write endpoint right, or is the scope missing?

Run manually, never in tests or CI, and only with ``LINKEDIN_MCP_LIVE_PROBE=1``:

    LINKEDIN_MCP_LIVE_PROBE=1 .venv/bin/python -m linkedin_mcp --live-probe

It sends exactly ONE request to the documented headline-write endpoint, writing
back the headline the profile already has (so a success is a no-op), and reads
the answer through ``discriminate`` — a pure function, unit-tested with mocks.

Outcomes (docs/api-notes.md, "Partner gating"):

* EXPECTED_PRE_APPROVAL — endpoint shape verified, setup correct, LinkedIn is
  refusing because partner approval has not landed. This needs an explicit
  scope/permission marker in the body. Wait; do not work around it.
* AUTH_ERROR           — a bare 401/403 with NO scope/permission marker: the
  token is invalid or expired. A failure, distinct from SPEC_ERROR.
* SPEC_ERROR           — the endpoint spec has drifted. Report it as a failure
  and re-verify docs/api-notes.md against the live docs. Do not work around it.
* WRITE_OK             — partner access is live and the write path works.
* UNKNOWN              — anything else; reported as a failure, never as a pass.

The probe first READS the profile to learn the person id. If that read fails,
the probe stops there and reports the read's own status (``phase`` =
``preflight_profile_read``) without sending any write — an expired token surfaces
as AUTH_ERROR rather than as a confusing "the profile had no id" error.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from . import api, config

LIVE_PROBE_ENV = "LINKEDIN_MCP_LIVE_PROBE"

EXPECTED_PRE_APPROVAL = "EXPECTED_PRE_APPROVAL"
AUTH_ERROR = "AUTH_ERROR"
SPEC_ERROR = "SPEC_ERROR"
WRITE_OK = "WRITE_OK"
UNKNOWN = "UNKNOWN"

_AUTH_STATUSES = (401, 403)
_PRE_APPROVAL_MARKERS = (
    "invalid scope",
    "invalid_scope",
    "access_denied",
    "permission",
    "not permitted",
    "not_authorized",
    "not authorized",
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
    AUTH_ERROR: (
        "LinkedIn rejected the credentials and said nothing about scopes or "
        "permissions: the access token is invalid or expired — re-run auth_start to "
        "get a fresh one, then probe again. This is a FAILURE, and it is NOT the "
        "expected pre-approval outcome: nothing here proves the endpoint shape."
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
    elif any(marker in text for marker in _PRE_APPROVAL_MARKERS):
        # Only an explicit scope/permission marker proves "setup correct, waiting
        # on partner approval". A bare 401/403 proves nothing of the sort.
        outcome = EXPECTED_PRE_APPROVAL
    elif status_code in _AUTH_STATUSES:
        outcome = AUTH_ERROR
    elif status_code in _SPEC_ERROR_STATUSES:
        outcome = SPEC_ERROR
    else:
        outcome = UNKNOWN

    return {
        "outcome": outcome,
        "status_code": status_code,
        "explanation": _EXPLANATIONS[outcome],
        "is_failure": outcome in (AUTH_ERROR, SPEC_ERROR, UNKNOWN),
    }


def _preflight_verdict(exc: httpx.HTTPStatusError) -> dict[str, Any]:
    """Classify a failed GET /v2/me. Always a failure — no write was attempted.

    ``discriminate`` can only return a non-failure outcome here for a 401/403
    carrying a scope marker; on the READ path that means the read itself is not
    permitted, which is an auth/setup problem, not the expected partner gate on
    writes. So it is reported as AUTH_ERROR.
    """
    body = api.decode_body(exc.response)
    verdict = discriminate(exc.response.status_code, body)
    if not verdict["is_failure"]:
        verdict = {
            **verdict,
            "outcome": AUTH_ERROR,
            "explanation": _EXPLANATIONS[AUTH_ERROR],
            "is_failure": True,
        }
    return {
        **verdict,
        "phase": "preflight_profile_read",
        "request": {"method": "GET", "url": f"{api.BASE_URL}{api.PROFILE_PATH}"},
        "response_body": body,
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
        except httpx.HTTPStatusError as exc:
            # LinkedIn answered the preflight READ with an error. Report that
            # answer instead of stumbling on into a "no id in the profile"
            # complaint — and send NO write, because nothing was learned about
            # the write endpoint.
            return _preflight_verdict(exc)
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
