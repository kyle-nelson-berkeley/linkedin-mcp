# LinkedIn API notes — recorded spec baseline

**Fetched: 2026-08-24** from Microsoft Learn (the official host of LinkedIn's API docs).
Every excerpt below is verbatim from the fetched pages. This file is the diffable
baseline the server implementation and its tests are built against. If a live probe
ever disagrees with this file, the FILE is re-verified against the docs first — the
code is never bent to match a guess.

Sources (canonical URLs + doc commit ids as served on the fetch date):

1. Profile Edit API — https://learn.microsoft.com/en-us/linkedin/shared/integrations/people/profile-edit-api
   (git_commit_id 57591bfcfd413f08f52e5aada0d48925d0bf43a6, updated_at 2026-04-30)
2. Positions sub-resource — https://learn.microsoft.com/en-us/linkedin/shared/integrations/people/profile-edit-api/positions
   (git_commit_id 0405a677c318861f186fb39bc5d60669d99a8e4d, updated_at 2023-09-06)
3. Skills sub-resource — https://learn.microsoft.com/en-us/linkedin/shared/integrations/people/profile-edit-api/skills
   (git_commit_id 1e850449cb0a7b26614b9db32108b3e5573cfb32, updated_at 2022-04-06)
4. Educations sub-resource — https://learn.microsoft.com/en-us/linkedin/shared/integrations/people/profile-edit-api/educations
   (git_commit_id 0405a677c318861f186fb39bc5d60669d99a8e4d, updated_at 2023-09-06)
5. 3-legged OAuth — https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow
   (git_commit_id b5e53c17b6e3b6c284acb2f39f22eb0bd386203a, updated_at 2026-05-15)

## Partner gating (verbatim, source 1)

> "The use of this API is restricted to those developers approved by LinkedIn and
> subject to applicable data restrictions in their agreements."

Permission table (verbatim):

> | Permissions | Description |
> | --- | --- |
> | w_compliance | Required to manage and delete data for compliance. This is a private permission and access is granted to select developers. |

Consequence for this server: until LinkedIn grants partner access, every write
call is EXPECTED to fail with an authorization error (invalid scope / access
denied). That failure proves the endpoint shape, not a bug.

## Required headers (verbatim, source 1)

> "In order to make the sample calls below succeed, you must include
> `X-RestLi-Protocol-Version:2.0.0` in your request header."

Auth header (source 5, Step 4): `Authorization: Bearer {INSERT_TOKEN}` against
`https://api.linkedin.com/v2/me` (the GET-profile smoke endpoint).

## Profile edit — basic fields (headline, summary) (source 1)

Endpoint (verbatim):

> `POST https://api.linkedin.com/v2/people/(id:{person ID})`

- A successful profile edit returns `200 OK`.
- `headline` is a `MultiLocaleString`; set-shape (per the `address` sample, verbatim pattern):

```json
{ "patch": { "$set": { "headline": {
  "localized": { "en_US": "..." },
  "preferredLocale": { "country": "US", "language": "en" } } } } }
```

- `summary` is a `MultiLocaleRichText` (verbatim sample):

```json
{
    "patch": {
        "$set": {
            "summary": {
                "localized": {
                    "en_US": {
                        "rawText": "Awesome summary of me."
                    }
                },
                "preferredLocale": {
                    "country": "US",
                    "language": "en"
                }
            }
        }
    }
}
```

- Whole-field semantics (verbatim): "Keep in mind when you update a field, the
  entire value will update." Deleting uses `{"patch": {"$delete": ["field1"]}}`;
  deleting a mandatory field returns `422`.

## Complex sub-resources (sources 2–4)

All three support `CREATE | PARTIAL_UPDATE | DELETE`. CREATE returns
`201 Created` with the new entity id in the `x-linkedin-id` response header.

Positions (verbatim endpoints):

> `POST https://api.linkedin.com/v2/people/id={person ID}/positions` (CREATE)
> `POST https://api.linkedin.com/v2/people/id={person ID}/positions/{position ID}` (PARTIAL_UPDATE, `patch.$set` body)
> `DELETE https://api.linkedin.com/v2/people/id={person ID}/positions/{position ID}`

Skills (verbatim endpoints):

> `POST https://api.linkedin.com/v2/people/id={person ID}/skills`
> `POST https://api.linkedin.com/v2/people/id={person ID}/skills/{skill ID}`
> `DELETE https://api.linkedin.com/v2/people/id={person ID}/skills/{skill ID}`

Skills CREATE minimal body (verbatim):

```json
{ "name": { "localized": { "en_US": "Project Management" },
  "preferredLocale": { "country": "US", "language": "en" } } }
```

Positions CREATE sample body (verbatim, source 2 — note every human-text
field is a MultiLocale envelope):

```json
{
  "company": "urn:li:organization:0000",
  "companyName": {
    "localized": { "en_US": "LinkedIn" },
    "preferredLocale": { "country": "US", "language": "en" }
  },
  "description": {
    "localized": { "en_US": { "rawText": "Awesome developer manager!" } },
    "preferredLocale": { "country": "US", "language": "en" }
  },
  "startMonthYear": { "month": 1, "year": 2014 },
  "title": {
    "localized": { "en_US": "Engineering Manager" },
    "preferredLocale": { "country": "US", "language": "en" }
  }
}
```

(The full sample on the page also carries `location`, `geoPositionLocation.displayLocationName`,
and `locationName`; the latter two are MultiLocaleString envelopes in the same
shape as `title`. The PARTIAL_UPDATE sample uses `patch.$set` with these same
envelope shapes plus a plain `endMonthYear` object.)

Educations (verbatim endpoints):

> `POST https://api.linkedin.com/v2/people/id={person ID}/educations`
> `POST https://api.linkedin.com/v2/people/id={person ID}/educations/{education ID}`
> `DELETE https://api.linkedin.com/v2/people/id={person ID}/educations/{education ID}`

Educations CREATE sample body (verbatim excerpt, source 4 — same envelope
pattern):

```json
{
  "degreeName": {
    "localized": { "en_US": "Bachelor of Science (B.S.)" },
    "preferredLocale": { "country": "US", "language": "en" }
  },
  "notes": {
    "localized": { "en_US": { "rawText": "Graduated with Honors." } },
    "preferredLocale": { "country": "US", "language": "en" }
  },
  "organization": "urn:li:organization:12345",
  "schoolName": {
    "localized": { "en_US": "Santa Clara University" },
    "preferredLocale": { "country": "US", "language": "en" }
  },
  "startMonthYear": { "year": 2011 }
}
```

(The full sample also carries `activities` and `grade.grade` as
MultiLocaleString envelopes and `fieldsOfStudy[].fieldOfStudyName` in the
same shape; the PARTIAL_UPDATE sample uses `patch.$set` with `degreeName`,
`schoolName` in these envelopes.)

NOTE the URL-shape asymmetry, preserved exactly as documented: the basic-field
edit uses `people/(id:{person ID})` (parenthesized Rest.li key) while the
sub-resources use `people/id={person ID}/...`.

## OAuth 2.0 3-legged flow (source 5)

Authorization (verbatim): `GET https://www.linkedin.com/oauth/v2/authorization`
with `response_type=code`, `client_id`, `redirect_uri` (must exactly match a
registered URL; parameters ignored; no `#`), `state` (CSRF), `scope`
(URL-encoded, space-delimited).

Token exchange (verbatim): `POST https://www.linkedin.com/oauth/v2/accessToken`
with `Content-Type: application/x-www-form-urlencoded` and
`grant_type=authorization_code`, `code`, `client_id`, `client_secret`,
`redirect_uri`.

Token response fields (verbatim table entries): `access_token` (~500 chars, plan
for ≥1000), `expires_in` ("all access tokens are issued with a 60-day
lifespan"), `refresh_token`, `refresh_token_expires_in`, `scope`. Sample:

```json
{
"access_token":"AQUvlL_DYEzvT2wz1QJiEPeLioeA",
"expires_in":5184000,
"scope":"r_basicprofile"
}
```

- Authorization codes live 30 minutes and are single-use.
- `state` mismatch ⇒ treat as CSRF, respond 401, abort.
- "Programmatic refresh tokens are available for a limited set of partners." —
  default refresh = re-run the flow (screen bypassed while the LinkedIn session
  and token are valid).
- Scope-change note (verbatim): "If you request a different scope than the
  previously granted scope, all the previous access tokens are invalidated."
- Auth-page errors surface as `error=user_cancelled_login|user_cancelled_authorize`
  + `error_description` + `state` on the redirect.
- Documented failure statuses used by the probe discriminator: 401 invalid scope
  ("Ensure that the permissions sent in scope parameter is assigned to the
  developer application"), 400 invalid_request / invalid_redirect_uri.

## GET profile (source 5, Step 4 — verbatim sample)

```bash
curl -X GET 'https://api.linkedin.com/v2/me' \
-H 'Authorization: Bearer {INSERT_TOKEN}'
```
