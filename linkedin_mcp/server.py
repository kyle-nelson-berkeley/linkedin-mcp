"""FastMCP stdio server exposing the seven linkedin tools.

The tool DESCRIPTIONS carry the workflow contract, because the descriptions are
what a model actually reads: propose_edit never writes; apply_proposal is the
only tool that writes and requires the human to have approved the diff in chat
first; an invalid-scope error from apply_proposal is EXPECTED until LinkedIn
partner approval lands.
"""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from . import api, config, oauth, proposals

SERVER_NAME = "linkedin"

INSTRUCTIONS = """\
Edit Kyle's own LinkedIn profile through a propose-then-approve workflow.

Never call apply_proposal on your own initiative. The sequence is always:
  1. propose_edit  -> returns a diff and a proposal_id, writes NOTHING
  2. show the human the diff and WAIT for their explicit approval in chat
  3. apply_proposal(proposal_id) -> the only tool that writes to LinkedIn

Until LinkedIn grants partner access to the Profile Edit API, apply_proposal is
expected to return an invalid-scope / permission error. That is not a bug and
must not be worked around.
"""

mcp = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)


def _client() -> api.LinkedInClient:
    return api.LinkedInClient(config.access_token())


def _error(exc: Exception) -> dict[str, Any]:
    """Uniform, secret-free error envelope.

    A failed HTTP call carries its status code, so a caller can tell an expired
    token (401) from a drifted endpoint (404) without parsing prose.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return {
            "ok": False,
            "error": type(exc).__name__,
            "status_code": exc.response.status_code,
            "message": api.http_error_summary(exc),
        }
    return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


@mcp.tool(
    description=(
        "Run the LinkedIn OAuth sign-in. Opens the consent page in a browser, catches "
        "the one-shot localhost redirect, exchanges the code, and stores the token in "
        "the owner-only config file (0600). Does NOT modify the profile. Returns only "
        "non-secret token status — never the token itself. Set url_only=true to just "
        "print the authorization URL without starting the listener."
    )
)
def auth_start(url_only: bool = False, timeout_seconds: float = 300.0) -> dict[str, Any]:
    try:
        if url_only:
            url, state = oauth.start_authorization()
            return {
                "ok": True,
                "authorization_url": url,
                "state": state,
                "note": (
                    "Open this URL, approve, then run auth_start again (without "
                    "url_only) to catch the redirect. The second call reuses this "
                    "same state, so the tab you already opened completes the flow."
                ),
            }
        result = oauth.run_authorization_flow(timeout=timeout_seconds)
        return {"ok": True, **result}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    description=(
        "Report whether a LinkedIn access token is stored and when it expires. Reads "
        "local config only: no network call, no profile change, and the token value is "
        "never returned."
    )
)
def auth_status() -> dict[str, Any]:
    try:
        return {"ok": True, **config.token_status()}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    description=(
        "Fetch the owner's LinkedIn profile with GET /v2/me. Read-only — it cannot "
        "change anything. Use it to get the person id and current values before "
        "calling propose_edit."
    )
)
def get_profile() -> dict[str, Any]:
    try:
        with _client() as client:
            return {"ok": True, "profile": client.get_profile()}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    description=(
        "Draft a profile change WITHOUT sending it. This tool NEVER writes to "
        "LinkedIn: it builds the exact API request, saves it as a proposal, and "
        "returns a unified diff of the current value versus the proposed one for human "
        "review. Show the diff to the human and wait for their approval; only then may "
        "apply_proposal be called with the returned proposal_id.\n"
        "section: headline | summary | position | skill | education.\n"
        "changes: for headline/summary use {'text': '...'}; for position/skill/"
        "education use {'action': 'create'|'update'|'delete', 'entity_id': '<id>' (for "
        "update/delete), 'fields': {...}}.\n"
        "Localized text fields (skill name, position title/companyName/description, "
        "education schoolName/degreeName/...) may be given as plain strings — they are "
        "wrapped in LinkedIn's documented MultiLocale shape for the chosen locale."
    )
)
def propose_edit(
    section: str,
    changes: dict[str, Any],
    person_id: str | None = None,
    locale: str = proposals.DEFAULT_LOCALE,
) -> dict[str, Any]:
    try:
        client = None
        if config.access_token():
            # A read client whenever a token exists — even with an explicit
            # person_id — so the approval diff shows the CURRENT value being
            # replaced, not an empty placeholder.
            client = _client()
        try:
            record = proposals.propose_edit(
                section, changes, person_id=person_id, locale=locale, client=client
            )
        finally:
            if client is not None:
                client.close()
        return {
            "ok": True,
            "proposal_id": record["proposal_id"],
            "section": record["section"],
            "label": record["label"],
            "diff": record["diff"],
            "request": record["request"],
            "next_step": (
                "Show this diff to the human. Call apply_proposal only after they "
                "approve it in chat."
            ),
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    description=(
        "List saved edit proposals with their diffs. Local read only — no network call "
        "and no profile change. Set include_applied=true to also show proposals that "
        "have already been sent."
    )
)
def list_proposals(include_applied: bool = False) -> dict[str, Any]:
    try:
        records = proposals.list_proposals(include_applied=include_applied)
        return {
            "ok": True,
            "count": len(records),
            "proposals": [
                {
                    "proposal_id": item["proposal_id"],
                    "section": item["section"],
                    "label": item.get("label", ""),
                    "status": item.get("status", proposals.STATUS_PENDING),
                    "created_at": item.get("created_at"),
                    "diff": item.get("diff", ""),
                }
                for item in records
            ],
        }
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    description=(
        "Delete a saved proposal that the human rejected or that is no longer wanted. "
        "Local file removal only — it never touches LinkedIn."
    )
)
def discard_proposal(proposal_id: str) -> dict[str, Any]:
    try:
        return {"ok": True, **proposals.discard_proposal(proposal_id)}
    except Exception as exc:
        return _error(exc)


@mcp.tool(
    description=(
        "THE ONLY TOOL THAT WRITES TO LINKEDIN. Sends the single prepared request "
        "stored in one proposal. Requires the human to have approved the returned diff "
        "in chat first — never call it on your own initiative, and never immediately "
        "after propose_edit without that approval. The approval is code-enforced: "
        "the 'approval' argument must be exactly 'approve <proposal_id>', supplied "
        "only after the human has seen the diff and said yes in chat. Until "
        "LinkedIn grants partner access to the Profile Edit API, an invalid-scope / "
        "permission error here is EXPECTED and is not a defect: report it plainly "
        "and stop, do not attempt any workaround."
    )
)
def apply_proposal(proposal_id: str, approval: str | None = None) -> dict[str, Any]:
    try:
        # The confirm-gate comes FIRST: nothing is loaded, claimed, or sent
        # until the verbatim approval phrase is present. The phrase names the
        # proposal id, so approval of one diff can never authorize another.
        expected = f"approve {proposal_id}"
        if approval != expected:
            raise proposals.ProposalError(
                "refused: apply_proposal requires the verbatim approval phrase "
                f"{expected!r} in the 'approval' argument, given only after the "
                "human has reviewed the diff in chat"
            )
        # Fail fast on a bad id before any credential or client is touched.
        proposals.load_proposal(proposal_id)
        with _client() as client:
            outcome = proposals.apply_proposal(proposal_id, client=client)
        return {"ok": outcome["response"]["ok"], **outcome}
    except Exception as exc:
        return _error(exc)


def run() -> None:
    """Serve MCP over stdio."""
    mcp.run(transport="stdio")
