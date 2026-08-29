# Privacy Policy — profile-edit-mcp

**Effective date: 2026-08-29**

This policy covers **profile-edit-mcp**, a LinkedIn developer application
operated by **Desert Mango (Kyle Nelson)**, and the open-source software
behind it, `linkedin-mcp` (this repository).

## What the software is

`linkedin-mcp` is a small, self-hosted tool. It is a local MCP (Model Context
Protocol) server that a person runs on their own computer so that an AI
assistant can help them edit **their own LinkedIn profile** — and nothing
else — through LinkedIn's official API. It is not a website, not a hosted
service, and it has no server operated by us. Each copy runs entirely on the
machine of the person who installed it (the "operator").

## What data it accesses

- **Only the operator's own LinkedIn profile** (for example: name, headline,
  summary, positions, skills, and educations), read and written through
  LinkedIn's official API endpoints.
- The software collects **no data about anyone other than the operator**.
  It does not access other members' profiles, connections, messages, or
  any other LinkedIn data.

## Where credentials and data live

- Authentication uses **OAuth 2.0 with LinkedIn**. The operator signs in on
  LinkedIn's own website; the software never sees the operator's LinkedIn
  password.
- OAuth tokens and API client credentials are stored **only on the
  operator's computer**, in a local file at `~/.config/linkedin-mcp/.env`
  with owner-only file permissions (mode 0600). They are never uploaded,
  synced, or shared.
- Draft profile changes ("proposals") are stored as local files on the
  operator's computer until the operator approves or discards them.

## Where data goes

- Data is transmitted to **one place only: LinkedIn's own API endpoints**
  (`api.linkedin.com`), and only to read or update the operator's own
  profile.
- Every profile change is first drafted locally and shown to the operator
  as a diff. Nothing is sent to LinkedIn until the operator gives
  **explicit approval** for that specific change.

## What we do NOT do

- No analytics and no telemetry.
- No third-party services and no data sharing with anyone.
- No advertising and no selling of data.
- No server-side storage — we (the app operator, Desert Mango) run no
  servers and receive no data from any copy of this software.
- No cookies (there is no website).

## Data retention and deletion

All stored data lives on the operator's own computer. To delete everything:

1. Delete the local configuration directory `~/.config/linkedin-mcp/`
   (this removes tokens, credentials, and any saved draft proposals).
2. Revoke the app's access in LinkedIn: **Settings & Privacy → Data
   privacy → Other applications → Permitted services**, then remove the
   app.

Profile data itself always remains on LinkedIn and is governed by
[LinkedIn's Privacy Policy](https://www.linkedin.com/legal/privacy-policy).

## Self-hosted copies

This software is open source (MIT license). Anyone who runs their own copy
does so on their own machine, with their own LinkedIn developer
credentials, under their own responsibility. Desert Mango has no access to,
and no visibility into, any self-hosted copy.

## Changes to this policy

Any changes will be published in this file in the public repository, with
an updated effective date. The version history is visible in the
repository's commit log.

## Contact

Questions or concerns? Please open an issue on this repository's issue
tracker: <https://github.com/kyle-nelson-berkeley/linkedin-mcp/issues>
