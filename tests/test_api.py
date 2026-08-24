"""Request-builder + client tests.

Every URL and body asserted here is copied from docs/api-notes.md. No shape is
invented; if a shape is not in that file, it is not built.
"""

from __future__ import annotations

import httpx
import pytest

from linkedin_mcp import api

PERSON = "ABC123"


def _transport(handler):
    return httpx.MockTransport(handler)


def test_basic_field_url_uses_parenthesised_restli_key():
    assert api.basic_field_url(PERSON) == "https://api.linkedin.com/v2/people/(id:ABC123)"


def test_sub_resource_urls_use_the_id_equals_form():
    assert (
        api.sub_resource_url(PERSON, "positions")
        == "https://api.linkedin.com/v2/people/id=ABC123/positions"
    )
    assert (
        api.sub_resource_url(PERSON, "skills", "SKILL9")
        == "https://api.linkedin.com/v2/people/id=ABC123/skills/SKILL9"
    )


def test_unknown_sub_resource_is_rejected():
    with pytest.raises(api.ApiError):
        api.sub_resource_url(PERSON, "endorsements")


def test_headline_request_matches_documented_multilocalestring_patch():
    prepared = api.build_headline_request(PERSON, "Mechanical engineer")
    assert prepared.method == "POST"
    assert prepared.url == "https://api.linkedin.com/v2/people/(id:ABC123)"
    assert prepared.json_body == {
        "patch": {
            "$set": {
                "headline": {
                    "localized": {"en_US": "Mechanical engineer"},
                    "preferredLocale": {"country": "US", "language": "en"},
                }
            }
        }
    }
    assert prepared.headers["X-RestLi-Protocol-Version"] == "2.0.0"
    assert "Authorization" not in prepared.headers


def test_summary_request_matches_documented_multilocalerichtext_patch():
    prepared = api.build_summary_request(PERSON, "Awesome summary of me.")
    assert prepared.url == "https://api.linkedin.com/v2/people/(id:ABC123)"
    assert prepared.json_body == {
        "patch": {
            "$set": {
                "summary": {
                    "localized": {"en_US": {"rawText": "Awesome summary of me."}},
                    "preferredLocale": {"country": "US", "language": "en"},
                }
            }
        }
    }


def test_locale_override_flows_into_body_and_preferred_locale():
    prepared = api.build_headline_request(PERSON, "Ingeniero", locale="es_ES")
    body = prepared.json_body["patch"]["$set"]["headline"]
    assert body["localized"] == {"es_ES": "Ingeniero"}
    assert body["preferredLocale"] == {"country": "ES", "language": "es"}


def _localized_string(value: str) -> dict:
    return {
        "localized": {"en_US": value},
        "preferredLocale": {"country": "US", "language": "en"},
    }


def _localized_rich_text(value: str) -> dict:
    return {
        "localized": {"en_US": {"rawText": value}},
        "preferredLocale": {"country": "US", "language": "en"},
    }


def test_sub_resource_create_passes_an_already_shaped_multilocale_value_through():
    body = {"name": _localized_string("Project Management")}
    prepared = api.build_sub_resource_create(PERSON, "skills", body)
    assert prepared.method == "POST"
    assert prepared.url == "https://api.linkedin.com/v2/people/id=ABC123/skills"
    assert prepared.json_body == body


def test_skill_create_normalizes_a_plain_string_name_to_the_documented_shape():
    """api-notes.md, Skills CREATE minimal body — name is a MultiLocaleString."""
    prepared = api.build_sub_resource_create(
        PERSON, "skills", {"name": "Project Management"}
    )
    assert prepared.json_body == {"name": _localized_string("Project Management")}


def test_skill_update_normalizes_a_plain_string_name_inside_patch_set():
    prepared = api.build_sub_resource_update(
        PERSON, "skills", "SK1", {"name": "Robotics"}
    )
    assert prepared.json_body == {
        "patch": {"$set": {"name": _localized_string("Robotics")}}
    }


def test_position_text_fields_use_documented_multilocale_shapes():
    prepared = api.build_sub_resource_create(
        PERSON,
        "positions",
        {
            "title": "Engineer",
            "companyName": "WHOI",
            "description": "Built an ocean sensor probe.",
        },
    )
    assert prepared.json_body == {
        "title": _localized_string("Engineer"),
        "companyName": _localized_string("WHOI"),
        "description": _localized_rich_text("Built an ocean sensor probe."),
    }


def test_education_text_fields_use_documented_multilocale_shapes():
    prepared = api.build_sub_resource_update(
        PERSON,
        "educations",
        "EDU3",
        {"schoolName": "UC Berkeley", "degreeName": "BS"},
    )
    assert prepared.json_body == {
        "patch": {
            "$set": {
                "schoolName": _localized_string("UC Berkeley"),
                "degreeName": _localized_string("BS"),
            }
        }
    }


def test_non_localized_fields_are_forwarded_untouched():
    prepared = api.build_sub_resource_create(
        PERSON,
        "positions",
        {
            "title": "Engineer",
            "company": "urn:li:organization:1234",
            "startMonthYear": {"month": 6, "year": 2025},
        },
    )
    assert prepared.json_body["company"] == "urn:li:organization:1234"
    assert prepared.json_body["startMonthYear"] == {"month": 6, "year": 2025}


def test_sub_resource_locale_override_flows_into_the_normalized_field():
    prepared = api.build_sub_resource_create(
        PERSON, "skills", {"name": "Robótica"}, locale="es_ES"
    )
    assert prepared.json_body == {
        "name": {
            "localized": {"es_ES": "Robótica"},
            "preferredLocale": {"country": "ES", "language": "es"},
        }
    }


def test_sub_resource_update_wraps_body_in_patch_set():
    prepared = api.build_sub_resource_update(
        PERSON, "positions", "POS7", {"title": "Engineer"}
    )
    assert prepared.method == "POST"
    assert prepared.url == "https://api.linkedin.com/v2/people/id=ABC123/positions/POS7"
    assert prepared.json_body == {
        "patch": {"$set": {"title": _localized_string("Engineer")}}
    }


def test_sub_resource_delete_has_no_body():
    prepared = api.build_sub_resource_delete(PERSON, "educations", "EDU3")
    assert prepared.method == "DELETE"
    assert (
        prepared.url == "https://api.linkedin.com/v2/people/id=ABC123/educations/EDU3"
    )
    assert prepared.json_body is None


def test_prepared_request_dict_roundtrip_excludes_authorization():
    prepared = api.build_headline_request(PERSON, "x")
    restored = api.PreparedRequest.from_dict(prepared.to_dict())
    assert restored == prepared
    assert "Authorization" not in restored.headers


def test_get_profile_sends_documented_read_call():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": PERSON, "localizedHeadline": "Hi"})

    client = api.LinkedInClient(access_token="tok", transport=_transport(handler))
    profile = client.get_profile()

    assert profile["id"] == PERSON
    assert seen[0].method == "GET"
    assert str(seen[0].url) == "https://api.linkedin.com/v2/me"
    assert seen[0].headers["Authorization"] == "Bearer tok"
    assert seen[0].headers["X-RestLi-Protocol-Version"] == "2.0.0"


def test_client_requires_a_token():
    with pytest.raises(api.ApiError):
        api.LinkedInClient(access_token=None, transport=_transport(lambda r: None))


def test_send_records_status_body_and_created_entity_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"ok": True}, headers={"x-linkedin-id": "NEW1"})

    client = api.LinkedInClient(access_token="tok", transport=_transport(handler))
    result = client.send(api.build_sub_resource_create(PERSON, "skills", {"a": 1}))

    assert result["status_code"] == 201
    assert result["body"] == {"ok": True}
    assert result["created_entity_id"] == "NEW1"
    assert result["ok"] is True


def test_send_keeps_non_json_body_as_text_and_marks_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="ACCESS_DENIED")

    client = api.LinkedInClient(access_token="tok", transport=_transport(handler))
    result = client.send(api.build_headline_request(PERSON, "x"))

    assert result["status_code"] == 403
    assert result["body"] == "ACCESS_DENIED"
    assert result["ok"] is False


def test_send_never_echoes_the_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = api.LinkedInClient(
        access_token="AQVsecret-value", transport=_transport(handler)
    )
    result = client.send(api.build_headline_request(PERSON, "x"))
    assert "AQVsecret-value" not in repr(result)
