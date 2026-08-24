"""The propose-then-approve split.

``propose_edit`` builds the FULL request that *would* change the profile, writes
it to disk, and returns a human-readable diff. It performs NO HTTP write, ever —
the only network call it can make is an optional read of the current profile,
and only when the caller hands it a client and no profile snapshot.

``apply_proposal`` is the sole write path: it loads one saved proposal and sends
exactly that one prepared request.
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

from . import api, config

STATUS_PENDING = "pending"
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

# Signals that a write failed because partner approval has not landed yet —
# expected, not a defect (docs/api-notes.md, "Partner gating").
_PRE_APPROVAL_MARKERS = ("invalid scope", "access_denied", "permission", "not enough permissions")
_PRE_APPROVAL_STATUSES = (401, 403)


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


def _current_headline(profile: Mapping[str, Any] | None, locale: str) -> str:
    if not profile:
        return ""
    localized = profile.get("localizedHeadline")
    if isinstance(localized, str):
        return localized
    nested = (profile.get("headline") or {}).get("localized") or {}
    value = nested.get(locale)
    return value if isinstance(value, str) else ""


def _current_summary(profile: Mapping[str, Any] | None, locale: str) -> str:
    if not profile:
        return ""
    localized = profile.get("localizedSummary")
    if isinstance(localized, str):
        return localized
    nested = (profile.get("summary") or {}).get("localized") or {}
    entry = nested.get(locale)
    if isinstance(entry, Mapping):
        raw = entry.get("rawText")
        return raw if isinstance(raw, str) else ""
    return entry if isinstance(entry, str) else ""


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
    if profile is None and client is not None and (person_id is None or section in BASIC_SECTIONS):
        profile = _safe_read_profile(client)

    resolved_person_id = _resolve_person_id(person_id, profile)

    if section in BASIC_SECTIONS:
        prepared, current, proposed, label = _basic_proposal(
            section, changes, resolved_person_id, profile, locale
        )
    else:
        prepared, current, proposed, label = _sub_proposal(
            section, changes, resolved_person_id, profile, locale
        )

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
        "diff": build_diff(current, proposed, label),
        "request": prepared.to_dict(),
    }
    _save(record, config.ensure_dir(config.proposals_dir()))
    return record


def _safe_read_profile(client: "api.LinkedInClient") -> dict[str, Any] | None:
    """Read-only best effort: a failed read must never block a proposal."""
    try:
        profile = client.get_profile()
    except Exception:
        return None
    return profile if isinstance(profile, dict) else None


# ----------------------------------------------------------------- storage


def _validate_id(proposal_id: str) -> str:
    if not _ID_PATTERN.match(str(proposal_id)):
        raise ProposalError(f"invalid proposal id {proposal_id!r}")
    return str(proposal_id)


def _path_for(proposal_id: str, applied: bool = False) -> Path:
    directory = config.applied_dir() if applied else config.proposals_dir()
    return directory / f"{_validate_id(proposal_id)}.json"


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
    pending = _path_for(proposal_id)
    if pending.exists():
        return _read(pending)
    applied = _path_for(proposal_id, applied=True)
    if applied.exists():
        return _read(applied)
    raise ProposalError(f"no proposal with id {proposal_id}")


def list_proposals(include_applied: bool = False) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    directories = [config.proposals_dir()]
    if include_applied:
        directories.append(config.applied_dir())
    for directory in directories:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            records.append(_read(path))
    return sorted(records, key=lambda item: item.get("created_at", 0))


def discard_proposal(proposal_id: str) -> dict[str, Any]:
    path = _path_for(proposal_id)
    if not path.exists():
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
    """True when a failure is the EXPECTED 'partner approval not granted yet'."""
    text = body.lower() if isinstance(body, str) else json.dumps(body).lower()
    if status_code in _PRE_APPROVAL_STATUSES:
        return True
    return any(marker in text for marker in _PRE_APPROVAL_MARKERS)


def apply_proposal(
    proposal_id: str,
    *,
    client: "api.LinkedInClient | None" = None,
    transport: Any = None,
) -> dict[str, Any]:
    """Send the ONE prepared request stored in a proposal. The only write path."""
    path = _path_for(proposal_id)
    if not path.exists():
        if _path_for(proposal_id, applied=True).exists():
            raise ProposalError(
                f"proposal {proposal_id} was already applied — propose a new edit instead"
            )
        raise ProposalError(f"no pending proposal with id {proposal_id}")

    record = _read(path)
    if record.get("status") != STATUS_PENDING:
        raise ProposalError(f"proposal {proposal_id} is not pending")

    prepared = api.PreparedRequest.from_dict(record["request"])
    owns_client = client is None
    if owns_client:
        client = api.LinkedInClient(config.access_token(), transport=transport)
    try:
        response = client.send(prepared)
    finally:
        if owns_client:
            client.close()

    record["last_response"] = response
    record["applied_at"] = int(time.time())

    if response["ok"]:
        record["status"] = STATUS_APPLIED
        _save(record, config.ensure_dir(config.applied_dir()))
        path.unlink()
    else:
        _save(record, config.proposals_dir())

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
