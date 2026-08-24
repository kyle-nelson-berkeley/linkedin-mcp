"""HTTP layer for the documented LinkedIn endpoints.

Every URL and body shape below is transcribed from docs/api-notes.md. Note the
documented URL asymmetry, preserved exactly:

* basic fields (headline, summary): ``/v2/people/(id:{person ID})``
* sub-resources (positions, skills, educations): ``/v2/people/id={person ID}/...``

Nothing here decides *whether* to write — that is the proposal layer's job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

BASE_URL = "https://api.linkedin.com"
PROFILE_PATH = "/v2/me"
RESTLI_HEADER = "X-RestLi-Protocol-Version"
RESTLI_VERSION = "2.0.0"
CREATED_ID_HEADER = "x-linkedin-id"

DEFAULT_LOCALE = "en_US"
SUB_RESOURCES = ("positions", "skills", "educations")
BASIC_FIELDS = ("headline", "summary")

# The two documented localized shapes (docs/api-notes.md, source 1).
MULTI_LOCALE_STRING = "MultiLocaleString"
MULTI_LOCALE_RICH_TEXT = "MultiLocaleRichText"

# Which sub-resource fields are localized, and with which shape.
#
# api-notes.md records the shapes verbatim: headline is a MultiLocaleString,
# summary a MultiLocaleRichText, and the Skills CREATE minimal body shows
# ``name`` as a MultiLocaleString. The position/education entries below are the
# SAME two documented shapes applied to those sub-resources' localized text
# fields. Anything NOT listed here (urns, dates, ids, booleans) is forwarded
# untouched, and a value that already carries a ``localized`` key is passed
# through as the caller shaped it.
LOCALIZED_SUB_FIELDS: dict[str, dict[str, str]] = {
    "skills": {"name": MULTI_LOCALE_STRING},
    "positions": {
        "title": MULTI_LOCALE_STRING,
        "companyName": MULTI_LOCALE_STRING,
        "description": MULTI_LOCALE_RICH_TEXT,
    },
    "educations": {
        "schoolName": MULTI_LOCALE_STRING,
        "degreeName": MULTI_LOCALE_STRING,
        "fieldOfStudy": MULTI_LOCALE_STRING,
        "activities": MULTI_LOCALE_STRING,
        "grade": MULTI_LOCALE_STRING,
        "notes": MULTI_LOCALE_RICH_TEXT,
    },
}

DEFAULT_TIMEOUT = 30.0

# How much of a failed response body an error summary may quote. Bodies are
# LinkedIn's own error text (never a secret), but they are still untrusted
# input, so the quote is bounded.
MAX_ERROR_DETAIL = 300


class ApiError(RuntimeError):
    """Raised for programming errors in request construction or missing auth."""


def _base_headers() -> dict[str, str]:
    """Headers shared by every call — Authorization is added at send time."""
    return {
        RESTLI_HEADER: RESTLI_VERSION,
        "Content-Type": "application/json",
    }


@dataclass(frozen=True)
class PreparedRequest:
    """A fully-built request that has NOT been sent.

    ``headers`` deliberately excludes Authorization so a proposal can be
    persisted to disk and shown to a human without leaking the token.
    """

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=_base_headers)
    json_body: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "headers": dict(self.headers),
            "json_body": self.json_body,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PreparedRequest":
        try:
            return cls(
                method=data["method"],
                url=data["url"],
                headers=dict(data.get("headers") or _base_headers()),
                json_body=data.get("json_body"),
            )
        except KeyError as exc:
            raise ApiError(f"prepared request is missing field {exc}") from exc


def split_locale(locale: str) -> dict[str, str]:
    """``en_US`` -> ``{"country": "US", "language": "en"}`` (documented shape)."""
    parts = locale.split("_")
    if len(parts) != 2 or not all(parts):
        raise ApiError(f"locale must look like 'en_US', got {locale!r}")
    language, country = parts
    return {"country": country, "language": language}


def basic_field_url(person_id: str) -> str:
    _require_person_id(person_id)
    return f"{BASE_URL}/v2/people/(id:{person_id})"


def sub_resource_url(person_id: str, sub: str, entity_id: str | None = None) -> str:
    _require_person_id(person_id)
    if sub not in SUB_RESOURCES:
        raise ApiError(
            f"unsupported sub-resource {sub!r}; documented: {', '.join(SUB_RESOURCES)}"
        )
    url = f"{BASE_URL}/v2/people/id={person_id}/{sub}"
    return f"{url}/{entity_id}" if entity_id else url


def _require_person_id(person_id: str) -> None:
    if not person_id:
        raise ApiError("person_id is required to build a profile URL")


def _basic_patch(field_name: str, value: Any) -> dict[str, Any]:
    return {"patch": {"$set": {field_name: value}}}


def multi_locale_string(value: str, locale: str = DEFAULT_LOCALE) -> dict[str, Any]:
    """``"x"`` -> the documented MultiLocaleString envelope."""
    return {"localized": {locale: value}, "preferredLocale": split_locale(locale)}


def multi_locale_rich_text(value: str, locale: str = DEFAULT_LOCALE) -> dict[str, Any]:
    """``"x"`` -> the documented MultiLocaleRichText envelope (``rawText``)."""
    return {
        "localized": {locale: {"rawText": value}},
        "preferredLocale": split_locale(locale),
    }


def normalize_sub_resource_fields(
    sub: str, fields: Mapping[str, Any], locale: str = DEFAULT_LOCALE
) -> dict[str, Any]:
    """Wrap plain-string values of documented localized fields.

    A caller may hand in ``{"name": "Project Management"}``; LinkedIn documents
    ``name`` as a MultiLocaleString, so the plain string is wrapped. Values that
    are already shaped (any non-string, including a caller-built ``localized``
    mapping) are forwarded exactly as given.
    """
    localized = LOCALIZED_SUB_FIELDS.get(sub, {})
    normalized: dict[str, Any] = {}
    for key, value in fields.items():
        shape = localized.get(key)
        if shape is None or not isinstance(value, str):
            normalized[key] = value
        elif shape == MULTI_LOCALE_RICH_TEXT:
            normalized[key] = multi_locale_rich_text(value, locale)
        else:
            normalized[key] = multi_locale_string(value, locale)
    return normalized


def build_headline_request(
    person_id: str, headline: str, locale: str = DEFAULT_LOCALE
) -> PreparedRequest:
    """headline is a MultiLocaleString (api-notes.md, source 1)."""
    body = _basic_patch("headline", multi_locale_string(headline, locale))
    return PreparedRequest("POST", basic_field_url(person_id), _base_headers(), body)


def build_summary_request(
    person_id: str, summary: str, locale: str = DEFAULT_LOCALE
) -> PreparedRequest:
    """summary is a MultiLocaleRichText (api-notes.md, source 1)."""
    body = _basic_patch("summary", multi_locale_rich_text(summary, locale))
    return PreparedRequest("POST", basic_field_url(person_id), _base_headers(), body)


def build_sub_resource_create(
    person_id: str, sub: str, body: Mapping[str, Any], locale: str = DEFAULT_LOCALE
) -> PreparedRequest:
    url = sub_resource_url(person_id, sub)
    return PreparedRequest(
        "POST", url, _base_headers(), normalize_sub_resource_fields(sub, body, locale)
    )


def build_sub_resource_update(
    person_id: str,
    sub: str,
    entity_id: str,
    changes: Mapping[str, Any],
    locale: str = DEFAULT_LOCALE,
) -> PreparedRequest:
    if not entity_id:
        raise ApiError(f"updating a {sub} entry requires its entity_id")
    url = sub_resource_url(person_id, sub, entity_id)
    return PreparedRequest(
        "POST",
        url,
        _base_headers(),
        {"patch": {"$set": normalize_sub_resource_fields(sub, changes, locale)}},
    )


def build_sub_resource_delete(
    person_id: str, sub: str, entity_id: str
) -> PreparedRequest:
    if not entity_id:
        raise ApiError(f"deleting a {sub} entry requires its entity_id")
    return PreparedRequest(
        "DELETE", sub_resource_url(person_id, sub, entity_id), _base_headers(), None
    )


class LinkedInClient:
    """Thin httpx wrapper. Transport is injectable so tests never hit the network."""

    def __init__(
        self,
        access_token: str | None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not access_token:
            raise ApiError(
                "no access token stored — run the auth_start tool first "
                "(tokens live in the owner-only config .env)"
            )
        self._token = access_token
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def __enter__(self) -> "LinkedInClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _auth_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        return {**dict(headers), "Authorization": f"Bearer {self._token}"}

    def get_profile(self) -> Any:
        """Read GET /v2/me. Raises ``httpx.HTTPStatusError`` on any non-2xx.

        A read that failed is NOT profile data: returning the decoded error body
        would let a caller report success while holding an error payload (and
        would make an expired token look like a profile without an id).
        """
        response = self._client.get(
            f"{BASE_URL}{PROFILE_PATH}", headers=self._auth_headers(_base_headers())
        )
        response.raise_for_status()
        return decode_body(response)

    def send(self, prepared: PreparedRequest) -> dict[str, Any]:
        """Send exactly ONE prepared request and return a recordable result."""
        response = self._client.request(
            prepared.method,
            prepared.url,
            headers=self._auth_headers(prepared.headers),
            json=prepared.json_body,
        )
        return {
            "method": prepared.method,
            "url": prepared.url,
            "status_code": response.status_code,
            "ok": response.is_success,
            "body": decode_body(response),
            "created_entity_id": response.headers.get(CREATED_ID_HEADER),
        }


def decode_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def http_error_summary(exc: httpx.HTTPStatusError) -> str:
    """One safe line describing a failed response.

    Quotes the status, the method + PATH (never the query string), and a bounded
    slice of LinkedIn's error body. Request headers — where the bearer token
    lives — are never touched.
    """
    detail = decode_body(exc.response)
    if not isinstance(detail, str):
        try:
            detail = json.dumps(detail, sort_keys=True)
        except (TypeError, ValueError):
            detail = str(detail)
    detail = detail.strip()
    if len(detail) > MAX_ERROR_DETAIL:
        detail = detail[:MAX_ERROR_DETAIL] + "..."
    summary = (
        f"LinkedIn returned HTTP {exc.response.status_code} for "
        f"{exc.request.method} {exc.request.url.path}"
    )
    return f"{summary}: {detail}" if detail else summary
