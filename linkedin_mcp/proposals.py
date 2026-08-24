"""The propose-then-approve split.

``propose_edit`` builds the FULL request that *would* change the profile, writes
it to disk, and returns a human-readable diff. It performs NO HTTP write, ever —
the only network call it can make is an optional read of the current profile,
and only when the caller hands it a client and no profile snapshot.

``apply_proposal`` is the sole write path: it loads one saved proposal and sends
exactly that one prepared request.

Exactly-once, and how a proposal is recovered
---------------------------------------------
A proposal file lives in exactly one of three directories, and the file's
location IS its state:

* ``proposals/``            — pending: not sent, safe to apply or discard.
* ``proposals/in-progress/`` — claimed: a caller has taken ownership of this
  proposal and its one write is in flight, or was in flight when something went
  wrong.
* ``proposals/applied/``    — applied: sent, and LinkedIn answered with success.

``apply_proposal`` CLAIMS before it sends: it ``os.rename``s the pending file
into ``in-progress/``, which is atomic on POSIX, so of two concurrent callers
exactly one gets the file and the other gets a clean "already being applied"
error without touching the network. The claim is what makes the write
exactly-once; the file being gone from ``proposals/`` is the lock.

After a definitive HTTP answer the claim is resolved: success (2xx) moves the
record to ``applied/``; a 4xx moves it back to ``pending`` (LinkedIn
definitively REJECTED the request, so the write did not land and retrying is
safe). A 5xx (or any other odd status) is NOT proof the write failed — the
server may have committed it before erroring — so the record stays claimed in
``in-progress/`` for manual recovery, exactly like the indeterminate cases
below.

Anything else — a transport error mid-send, or the process dying — leaves the
record in ``in-progress/``. That state is INDETERMINATE: the request may or may
not have reached LinkedIn. It is deliberately never retried automatically and
``apply_proposal`` refuses it, because a silent re-send could duplicate an edit
that already landed. Recovery is a human step: read the record with
``load_proposal`` (a claim that got as far as an answer carries ``last_response``;
one that did not carries ``last_error`` or neither), check the live profile, then
``discard_proposal`` it and propose a fresh edit if the change is still wanted.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from . import api, config, probe

STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_APPLIED = "applied"

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
SUB_ACTIONS = (ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE)

BASIC_SECTIONS = {"headline": "headline", "summary": "summary"}
SUB_SECTIONS = {"position": "positions", "skill": "skills", "education": "educations"}
SECTIONS = tuple(BASIC_SECTIONS) + tuple(SUB_SECTIONS)

DEFAULT_LOCALE = api.DEFAULT_LOCALE
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

class ProposalError(RuntimeError):
    """Raised for bad proposal input or a missing/duplicate proposal."""


# ---------------------------------------------------------------- building


def _text_change(changes: Mapping[str, Any], section: str) -> str:
    for key in ("text", section, "value"):
        value = changes.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raise ProposalError(
        f"a {section} proposal needs the new text, e.g. changes={{'text': '...'}}"
    )


def _resolve_person_id(person_id: str | None, profile: Mapping[str, Any] | None) -> str:
    resolved = person_id or (profile or {}).get("id")
    if not resolved:
        raise ProposalError(
            "person_id is unknown — call get_profile first, or pass person_id explicitly"
        )
    return str(resolved)


def _current_headline(profile: Mapping[str, Any] | None, locale: str) -> str | None:
    """The current headline, or None when the profile does not carry one."""
    if not profile:
        return None
    localized = profile.get("localizedHeadline")
    if isinstance(localized, str):
        return localized
    nested = (profile.get("headline") or {}).get("localized") or {}
    value = nested.get(locale)
    return value if isinstance(value, str) else None


def _current_summary(profile: Mapping[str, Any] | None, locale: str) -> str | None:
    """The current summary, or None when the profile does not carry one."""
    if not profile:
        return None
    localized = profile.get("localizedSummary")
    if isinstance(localized, str):
        return localized
    nested = (profile.get("summary") or {}).get("localized") or {}
    entry = nested.get(locale)
    if isinstance(entry, Mapping):
        raw = entry.get("rawText")
        return raw if isinstance(raw, str) else None
    return entry if isinstance(entry, str) else None


def build_diff(current: str, proposed: str, label: str) -> str:
    """Unified diff of two human-readable values (never of secrets)."""
    diff = difflib.unified_diff(
        current.splitlines() or [""],
        proposed.splitlines() or [""],
        fromfile=f"current/{label}",
        tofile=f"proposed/{label}",
        lineterm="",
    )
    text = "\n".join(diff)
    return text or f"--- current/{label}\n+++ proposed/{label}\n(no textual change)"


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, sort_keys=True)


def _basic_proposal(
    section: str,
    changes: Mapping[str, Any],
    person_id: str,
    profile: Mapping[str, Any] | None,
    locale: str,
) -> tuple[api.PreparedRequest, str, str, str]:
    text = _text_change(changes, section)
    if section == "headline":
        prepared = api.build_headline_request(person_id, text, locale)
        current = _current_headline(profile, locale)
    else:
        prepared = api.build_summary_request(person_id, text, locale)
        current = _current_summary(profile, locale)
    return prepared, current, text, f"set {section}"


def _sub_proposal(
    section: str,
    changes: Mapping[str, Any],
    person_id: str,
    profile: Mapping[str, Any] | None,
    locale: str,
) -> tuple[api.PreparedRequest, str, str, str]:
    sub = SUB_SECTIONS[section]
    action = str(changes.get("action", ACTION_CREATE)).lower()
    if action not in SUB_ACTIONS:
        raise ProposalError(
            f"unsupported action {action!r} for {section}; use one of {', '.join(SUB_ACTIONS)}"
        )
    entity_id = changes.get("entity_id")
    fields = changes.get("fields")

    if action == ACTION_DELETE:
        if not entity_id:
            raise ProposalError(f"deleting a {section} requires changes['entity_id']")
        prepared = api.build_sub_resource_delete(person_id, sub, str(entity_id))
        return prepared, f"{section} {entity_id}", "", f"delete {section} {entity_id}"

    if not isinstance(fields, Mapping) or not fields:
        raise ProposalError(
            f"a {section} {action} needs changes['fields'] with the entry's values"
        )

    if action == ACTION_CREATE:
        prepared = api.build_sub_resource_create(person_id, sub, fields, locale)
        return prepared, "", _pretty(dict(fields)), f"create {section}"

    if not entity_id:
        raise ProposalError(f"updating a {section} requires changes['entity_id']")
    prepared = api.build_sub_resource_update(
        person_id, sub, str(entity_id), fields, locale
    )
    return prepared, "", _pretty(dict(fields)), f"update {section} {entity_id}"


def propose_edit(
    section: str,
    changes: Mapping[str, Any],
    *,
    person_id: str | None = None,
    profile: Mapping[str, Any] | None = None,
    locale: str = DEFAULT_LOCALE,
    client: "api.LinkedInClient | None" = None,
) -> dict[str, Any]:
    """Build + persist a proposal. NEVER sends a write request.

    ``client`` is used only to READ the current profile when no snapshot was
    supplied, so the diff can show a real before-value.
    """
    section = str(section).lower().strip()
    if section not in SECTIONS:
        raise ProposalError(
            f"unsupported section {section!r}; supported: {', '.join(SECTIONS)}"
        )
    if not isinstance(changes, Mapping):
        raise ProposalError("changes must be a mapping")

    # Validated before anything is built: the locale shapes every documented
    # MultiLocale body, so a bad locale must fail as a ProposalError, not leak
    # an ApiError out of a request builder.
    try:
        api.split_locale(locale)
    except api.ApiError as exc:
        raise ProposalError(str(exc)) from exc

    # A read (GET /v2/me) is the ONLY request this function may make, and only
    # when the caller gave it neither a profile snapshot nor a person id.
    read_error: str | None = None
    if profile is None and client is not None and (person_id is None or section in BASIC_SECTIONS):
        profile, read_error = _safe_read_profile(client)

    resolved_person_id = _resolve_person_id(person_id, profile)

    if section in BASIC_SECTIONS:
        prepared, current, proposed, label = _basic_proposal(
            section, changes, resolved_person_id, profile, locale
        )
    else:
        prepared, current, proposed, label = _sub_proposal(
            section, changes, resolved_person_id, profile, locale
        )

    # Basic-field updates replace the WHOLE value. If the profile read failed,
    # an empty "current" would be a materially misleading approval diff — say
    # plainly that the before-value is unknown instead.
    if (
        profile is not None
        and person_id is not None
        and str(profile.get("id", "")) != str(person_id)
    ):
        raise ProposalError(
            f"person_id {person_id!r} does not match the authenticated member "
            f"{profile.get('id')!r} — this server edits the owner's own profile only"
        )

    current_unavailable = section in BASIC_SECTIONS and current is None
    if current_unavailable:
        reason = (
            f"profile read failed: {read_error}"
            if read_error is not None
            else "not present in the profile response"
        )
        current = f"«current {section} unavailable — {reason}»"
    current = current if current is not None else ""

    record = {
        "proposal_id": uuid.uuid4().hex,
        "created_at": int(time.time()),
        "section": section,
        "label": label,
        "person_id": resolved_person_id,
        "locale": locale,
        "status": STATUS_PENDING,
        "current_value": current,
        "proposed_value": proposed,
        "current_value_unavailable": current_unavailable,
        "diff": build_diff(current, proposed, label),
        "request": prepared.to_dict(),
    }
    _save(record, config.ensure_dir(config.proposals_dir()))
    return record


def _safe_read_profile(
    client: "api.LinkedInClient",
) -> tuple[dict[str, Any] | None, str | None]:
    """Read-only best effort: a failed read must never block a proposal.

    Returns (profile, error_summary). The error summary is token-free and is
    surfaced in the approval diff so a failed read is never mistaken for an
    empty current value.
    """
    import httpx

    try:
        profile = client.get_profile()
    except httpx.HTTPStatusError as exc:
        return None, api.http_error_summary(exc)
    except Exception as exc:
        return None, (str(exc) or type(exc).__name__)
    if isinstance(profile, dict):
        return profile, None
    return None, "profile response was not an object"


# ----------------------------------------------------------------- storage


def _validate_id(proposal_id: str) -> str:
    if not _ID_PATTERN.match(str(proposal_id)):
        raise ProposalError(f"invalid proposal id {proposal_id!r}")
    return str(proposal_id)


_STATE_DIRS = {
    STATUS_PENDING: config.proposals_dir,
    STATUS_CLAIMED: config.claimed_dir,
    STATUS_APPLIED: config.applied_dir,
}


def _dir_for(state: str) -> Path:
    return _STATE_DIRS[state]()


def _path_for(proposal_id: str, state: str = STATUS_PENDING) -> Path:
    return _dir_for(state) / f"{_validate_id(proposal_id)}.json"


def _save(record: Mapping[str, Any], directory: Path) -> Path:
    config.ensure_dir(directory)
    path = directory / f"{record['proposal_id']}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, config.FILE_MODE)
    with os.fdopen(fd, "w") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
    os.chmod(path, config.FILE_MODE)
    return path


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ProposalError(f"no proposal {path.stem}") from exc
    except ValueError as exc:
        raise ProposalError(f"proposal {path.stem} is corrupt: {exc}") from exc


def load_proposal(proposal_id: str) -> dict[str, Any]:
    for state in _STATE_DIRS:
        path = _path_for(proposal_id, state)
        if path.exists():
            return _read(path)
    raise ProposalError(f"no proposal with id {proposal_id}")


def list_proposals(include_applied: bool = False) -> list[dict[str, Any]]:
    """Pending proposals; with ``include_applied``, the finished and claimed ones too.

    Claimed records are never listed as pending — they are not applicable — but
    they are not invisible either, so an interrupted apply can be found.
    """
    records: list[dict[str, Any]] = []
    directories = [config.proposals_dir()]
    if include_applied:
        directories += [config.claimed_dir(), config.applied_dir()]
    for directory in directories:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            records.append(_read(path))
    return sorted(records, key=lambda item: item.get("created_at", 0))


def discard_proposal(proposal_id: str) -> dict[str, Any]:
    """Throw a proposal away. Also the recovery step for a stuck claim.

    Discarding a claimed proposal only forgets the local record — it cannot undo
    a write that may already have landed, so check the live profile first.
    """
    path = next(
        (
            candidate
            for candidate in (
                _path_for(proposal_id),
                _path_for(proposal_id, STATUS_CLAIMED),
            )
            if candidate.exists()
        ),
        None,
    )
    if path is None:
        raise ProposalError(f"no pending proposal with id {proposal_id}")
    record = _read(path)
    path.unlink()
    return {
        "discarded": True,
        "proposal_id": record["proposal_id"],
        "label": record.get("label", ""),
    }


# ------------------------------------------------------------------- apply


def looks_like_pre_approval(status_code: int, body: Any) -> bool:
    """True when a failure is the EXPECTED 'partner approval not granted yet'.

    Both halves are required: a 401/403 (LinkedIn refused on authorization
    grounds) AND an explicit scope/permission marker (it refused over a scope,
    not over a bad token). A bare 401/403 is an expired token; a 400 mentioning
    "permission" is drifted spec.

    This delegates to ``probe.discriminate`` so the apply path and the live probe
    can never drift apart on what counts as the partner gate.
    """
    return probe.discriminate(status_code, body)["outcome"] == probe.EXPECTED_PRE_APPROVAL


def _claim_conflict(proposal_id: str, claimed: Path) -> ProposalError:
    """Explain a claim we did not win — and why it is not retried for you."""
    try:
        record = _read(claimed)
    except ProposalError:
        record = {}
    if record.get("last_response") is not None:
        detail = (
            "it was already sent to LinkedIn and the answer recorded, but the apply "
            "did not finish"
        )
    elif record.get("last_error"):
        detail = (
            "an earlier apply failed mid-send, so whether it reached LinkedIn is "
            f"unknown ({record['last_error']})"
        )
    else:
        detail = (
            "it is already being applied, or an earlier apply was interrupted mid-send"
        )
    return ProposalError(
        f"proposal {proposal_id} is claimed: {detail}. It will NOT be re-sent. "
        "Check the live profile, then discard_proposal it and propose a fresh edit "
        "if the change is still wanted."
    )


def _claim(proposal_id: str) -> Path:
    """Take exclusive ownership of a pending proposal BEFORE anything is sent.

    ``os.rename`` is atomic on POSIX, so when two callers race for the same
    proposal exactly one moves the file; the loser sees FileNotFoundError and
    never reaches the network.
    """
    pending = _path_for(proposal_id)
    claimed = _path_for(proposal_id, STATUS_CLAIMED)
    config.ensure_dir(config.claimed_dir())

    if claimed.exists():
        raise _claim_conflict(proposal_id, claimed)
    try:
        os.rename(pending, claimed)
    except FileNotFoundError as exc:
        if _path_for(proposal_id, STATUS_APPLIED).exists():
            raise ProposalError(
                f"proposal {proposal_id} was already applied — propose a new edit instead"
            ) from exc
        if claimed.exists():
            raise _claim_conflict(proposal_id, claimed) from exc
        raise ProposalError(f"no pending proposal with id {proposal_id}") from exc
    return claimed


def _release_claim(claimed: Path, proposal_id: str) -> None:
    """Undo a claim taken for a proposal we then refused to send."""
    os.rename(claimed, _path_for(proposal_id))


def _resolve_claim(record: dict[str, Any], claimed: Path, state: str) -> None:
    """Write the record into its new state, THEN drop the claim.

    In that order: a crash between the two leaves both files, and a leftover
    claim only ever costs a manual discard. The reverse order could lose the
    record entirely.
    """
    record["status"] = state
    _save(record, config.ensure_dir(_dir_for(state)))
    claimed.unlink()


def apply_proposal(
    proposal_id: str,
    *,
    client: "api.LinkedInClient | None" = None,
    transport: Any = None,
) -> dict[str, Any]:
    """Send the ONE prepared request stored in a proposal. The only write path.

    The proposal is claimed (atomically renamed out of ``proposals/``) before the
    request goes out, so it can be sent at most once. See the module docstring
    for what a claim left behind by a crash means and how to clear it.
    """
    # Build the client BEFORE claiming: if construction fails (e.g. no access
    # token stored), nothing was sent, and the proposal must stay pending and
    # retryable rather than stuck claimed.
    # Loaded FIRST so an unknown id is refused before any credential or
    # client is touched.
    preview = load_proposal(proposal_id)

    owns_client = client is None
    if owns_client:
        client = api.LinkedInClient(config.access_token(), transport=transport)

    # Own-profile-only, enforced at the write boundary: the proposal's target
    # must be the AUTHENTICATED member. Verified with a read before anything
    # is claimed or sent; if the member cannot be verified, the write is
    # refused rather than trusted.
    try:
        try:
            me = client.get_profile()
        except Exception as exc:
            raise ProposalError(
                "cannot verify the authenticated member before writing "
                f"({type(exc).__name__}) — refusing to apply; re-run auth_start "
                "and try again"
            ) from exc
        authenticated = str((me or {}).get("id", "")) if isinstance(me, dict) else ""
        target = str(preview.get("person_id", ""))
        if not authenticated or target != authenticated:
            raise ProposalError(
                f"proposal {proposal_id} targets person id {target!r} but the "
                f"authenticated member is {authenticated!r} — this server edits "
                "the owner's own profile only; discard the proposal"
            )
    except ProposalError:
        if owns_client:
            client.close()
        raise

    claimed = _claim(proposal_id)
    record = _read(claimed)
    if record.get("status") != STATUS_PENDING:
        _release_claim(claimed, proposal_id)
        raise ProposalError(f"proposal {proposal_id} is not pending")

    prepared = api.PreparedRequest.from_dict(record["request"])
    record["status"] = STATUS_CLAIMED
    record["claimed_at"] = int(time.time())
    _save(record, config.claimed_dir())

    try:
        response = client.send(prepared)
    except BaseException as exc:
        # Indeterminate: the request may already be on the wire. Leave the claim
        # standing so nothing re-sends it behind the owner's back.
        record["last_error"] = str(exc) or type(exc).__name__
        _save(record, config.claimed_dir())
        raise
    finally:
        if owns_client:
            client.close()

    record["last_response"] = response
    record["applied_at"] = int(time.time())
    # Resolution semantics:
    #   2xx           -> APPLIED (final).
    #   4xx           -> PENDING: LinkedIn definitively REJECTED the request,
    #                    so the write did not land and retrying is safe.
    #   anything else -> stays CLAIMED: a 5xx (or other odd status) is NOT
    #                    proof the write failed — LinkedIn may have committed
    #                    it before erroring. Releasing it would let a retry
    #                    duplicate the write; manual recovery only.
    status_code = response.get("status_code", 0)
    if response["ok"]:
        _resolve_claim(record, claimed, STATUS_APPLIED)
    elif 400 <= status_code < 500:
        _resolve_claim(record, claimed, STATUS_PENDING)
    else:
        record["status"] = STATUS_CLAIMED
        record["last_error"] = (
            f"ambiguous server failure (HTTP {status_code}); the write may have "
            "landed — kept claimed for manual recovery, will not auto-retry"
        )
        _save(record, config.claimed_dir())

    return {
        "proposal_id": record["proposal_id"],
        "status": record["status"],
        "label": record.get("label", ""),
        "response": response,
        "expected_pre_approval": (
            not response["ok"]
            and looks_like_pre_approval(response["status_code"], response["body"])
        ),
    }
