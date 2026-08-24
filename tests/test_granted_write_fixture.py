"""Proof (c): the granted-write fixture really grants writes and never falls through.

These tests validate the instrument itself. If they fail, the restraint tests in
test_write_restraint.py prove nothing.
"""

from __future__ import annotations

import httpx
import pytest

from linkedin_mcp import api
from tests.fixtures.granted_write import PERSON_ID, GrantedWriteLinkedIn, UnroutedRequestError


def _client(mock: GrantedWriteLinkedIn) -> api.LinkedInClient:
    return api.LinkedInClient(access_token="test-token", transport=mock.transport)


def test_every_documented_write_endpoint_succeeds():
    mock = GrantedWriteLinkedIn()
    client = _client(mock)

    prepared = [
        api.build_headline_request(PERSON_ID, "New headline"),
        api.build_summary_request(PERSON_ID, "New summary"),
    ]
    for sub in api.SUB_RESOURCES:
        prepared.append(api.build_sub_resource_create(PERSON_ID, sub, {"a": 1}))
        prepared.append(api.build_sub_resource_update(PERSON_ID, sub, "E1", {"a": 2}))
        prepared.append(api.build_sub_resource_delete(PERSON_ID, sub, "E1"))

    results = [client.send(item) for item in prepared]

    assert all(result["ok"] for result in results), [
        (r["method"], r["url"], r["status_code"]) for r in results if not r["ok"]
    ]
    assert len(mock.non_get_requests) == len(prepared)


def test_create_returns_201_with_new_entity_id_header():
    mock = GrantedWriteLinkedIn()
    result = _client(mock).send(
        api.build_sub_resource_create(PERSON_ID, "positions", {"title": "x"})
    )
    assert result["status_code"] == 201
    assert result["created_entity_id"] == "NEWENTITY1"


def test_read_endpoint_is_registered_alongside_the_writes():
    mock = GrantedWriteLinkedIn()
    profile = _client(mock).get_profile()
    assert profile["id"] == PERSON_ID
    assert mock.non_get_requests == []


def test_unrouted_request_raises_instead_of_falling_through():
    mock = GrantedWriteLinkedIn()
    client = httpx.Client(transport=mock.transport)
    with pytest.raises(UnroutedRequestError):
        client.post("https://api.linkedin.com/v2/people/id=ABC123/endorsements", json={})


def test_wrong_method_on_a_known_path_also_raises():
    mock = GrantedWriteLinkedIn()
    client = httpx.Client(transport=mock.transport)
    with pytest.raises(UnroutedRequestError):
        client.delete("https://api.linkedin.com/v2/me")


def test_requests_are_recorded_with_method_url_and_body():
    mock = GrantedWriteLinkedIn()
    _client(mock).send(api.build_headline_request(PERSON_ID, "Recorded headline"))
    record = mock.requests[-1]
    assert record["method"] == "POST"
    assert record["url"] == "https://api.linkedin.com/v2/people/(id:ABC123)"
    assert record["body"]["patch"]["$set"]["headline"]["localized"] == {
        "en_US": "Recorded headline"
    }
