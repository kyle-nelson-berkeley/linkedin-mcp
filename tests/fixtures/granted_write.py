"""The GRANTED-WRITE fixture: a mock LinkedIn where every write SUCCEEDS.

This exists so restraint tests prove restraint rather than deprivation. If a
write endpoint were reachable and the code chose to call it, this fixture would
happily return 200/201 — so "zero non-GET requests recorded" means the code
decided not to write, not that it tried and failed.

Two hard properties:

1. Every documented write endpoint from docs/api-notes.md is registered with a
   success response (headline/summary people-PATCH, and create/update/delete for
   positions, skills, educations), alongside the GET /v2/me read.
2. Any request that matches NO registered route raises ``UnroutedRequestError``.
   There is no silent fall-through, so an undocumented endpoint cannot slip past
   a test by quietly returning something plausible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx

PERSON_ID = "ABC123"

SAMPLE_PROFILE: dict[str, Any] = {
    "id": PERSON_ID,
    "localizedFirstName": "Test",
    "localizedLastName": "Owner",
    "localizedHeadline": "Current headline from the mock profile",
    "summary": {
        "localized": {"en_US": {"rawText": "Current summary from the mock profile."}},
        "preferredLocale": {"country": "US", "language": "en"},
    },
}

_SUBS = "positions|skills|educations"


class UnroutedRequestError(AssertionError):
    """Raised when a request matches no registered route (no silent fall-through)."""


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern[str]
    responder: Callable[[httpx.Request], httpx.Response]


class GrantedWriteLinkedIn:
    """Mock LinkedIn transport that records every request and grants every write."""

    def __init__(self, person_id: str = PERSON_ID, profile: dict[str, Any] | None = None):
        self.person_id = person_id
        self.profile = dict(profile) if profile is not None else dict(SAMPLE_PROFILE)
        self.requests: list[dict[str, Any]] = []
        self.routes: list[Route] = []
        self._register_documented_routes()

    # ---- route table -----------------------------------------------------
    def register(self, method: str, pattern: str, responder) -> None:
        self.routes.append(Route(method.upper(), re.compile(pattern), responder))

    def _register_documented_routes(self) -> None:
        pid = re.escape(self.person_id)

        # READ — api-notes.md source 5, step 4.
        self.register("GET", r"^/v2/me$", lambda req: httpx.Response(200, json=self.profile))

        # WRITE — basic fields (headline, summary): parenthesised Rest.li key.
        self.register(
            "POST",
            rf"^/v2/people/\(id:{pid}\)$",
            lambda req: httpx.Response(200, json={"granted": True}),
        )

        # WRITE — sub-resource PARTIAL_UPDATE (entity route registered first).
        self.register(
            "POST",
            rf"^/v2/people/id={pid}/({_SUBS})/[^/]+$",
            lambda req: httpx.Response(200, json={"granted": True}),
        )
        # WRITE — sub-resource CREATE: 201 + new id in the x-linkedin-id header.
        self.register(
            "POST",
            rf"^/v2/people/id={pid}/({_SUBS})$",
            lambda req: httpx.Response(
                201, json={"granted": True}, headers={"x-linkedin-id": "NEWENTITY1"}
            ),
        )
        # WRITE — sub-resource DELETE.
        self.register(
            "DELETE",
            rf"^/v2/people/id={pid}/({_SUBS})/[^/]+$",
            lambda req: httpx.Response(204),
        )

    # ---- transport -------------------------------------------------------
    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "body": _decode(request),
                "headers": dict(request.headers),
            }
        )
        for route in self.routes:
            if route.method == request.method and route.pattern.match(request.url.path):
                return route.responder(request)
        raise UnroutedRequestError(
            f"no registered route for {request.method} {request.url} — the granted-write "
            "fixture never falls through silently"
        )

    # ---- assertions helpers ---------------------------------------------
    @property
    def non_get_requests(self) -> list[dict[str, Any]]:
        return [item for item in self.requests if item["method"] != "GET"]

    def calls(self) -> list[tuple[str, str]]:
        return [(item["method"], item["url"]) for item in self.requests]


def _decode(request: httpx.Request) -> Any:
    raw = request.read()
    if not raw:
        return None
    import json

    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")
