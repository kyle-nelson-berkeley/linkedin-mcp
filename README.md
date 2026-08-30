# linkedin-mcp

MCP server for editing your own LinkedIn profile from Claude Code — with a
hard **propose-then-approve** split: the model can *draft* a change and show
you the diff, but only one tool can ever write, and it acts only on a
proposal you have seen.

## Read this before you install

**1. You bring your own LinkedIn app.** There is no shared or hosted service
here. You create your own app at
[linkedin.com/developers/apps](https://www.linkedin.com/developers/apps), and
your own client ID and secret go in a file on your own machine
(`~/.config/linkedin-mcp/.env`, permissions `600`). The server itself makes no
outbound call to anything but LinkedIn's own API — there is no telemetry and
no third party. Note that it is still an MCP server: profile data and diffs it
returns go to whichever agent you connect, so if that agent is cloud-backed,
your profile content reaches that provider like any other chat content. See
[PRIVACY.md](PRIVACY.md).

**2. Writing to your profile needs LinkedIn partner approval — which you must
apply for yourself.** LinkedIn's Profile Edit API is restricted to
LinkedIn-approved partner developers (`w_compliance` is "a private permission
and access is granted to select developers" — see
[docs/api-notes.md](docs/api-notes.md)). Approval is granted per developer app,
so *your* app needs *your* approval; nobody else's carries over. Until
it lands, `apply_proposal` returns an invalid-scope error. **That is expected,
not a defect** — and it may never land, since the permission goes to "select
developers".

Everything else works today without partner approval: OAuth sign-in, reading
your profile, drafting edits, and reviewing diffs. Only the final write is
gated.

## How it works

| Tool | What it does | Writes to LinkedIn? |
| --- | --- | --- |
| `auth_start` | Prints the LinkedIn OAuth URL, catches the one-shot localhost redirect, exchanges the code, stores tokens | no (OAuth only) |
| `auth_status` | Reports whether a token exists and when it expires | no |
| `get_profile` | Fetches your profile (`GET /v2/me`) | no |
| `propose_edit` | Builds the exact API request for a change (headline, summary, positions, skills, educations), saves it as a proposal, returns a unified diff + `proposal_id` | **never** |
| `list_proposals` | Lists saved proposals | no |
| `discard_proposal` | Deletes a saved proposal | no |
| `apply_proposal` | Sends ONE saved proposal to LinkedIn — **the only write tool**. Code-enforced confirm gate: it refuses unless called with `approval="approve <proposal_id>"`, a phrase supplied only after the human has reviewed the diff in chat. | yes |

Proposals persist under `~/.config/linkedin-mcp/proposals/` so an approval
can happen in a later session. Tokens and client credentials live in
`~/.config/linkedin-mcp/.env` with permissions `600` — entered by you, never
by an agent, never committed (see [.env.example](.env.example)).

## Install

Requires Python ≥ 3.11. Clone the repo, then register it as an MCP server in
your agent — add this to your client's MCP config (for Claude Code that is
`~/.claude.json`, or run `claude mcp add linkedin -- bash /path/to/linkedin-mcp/run.sh`):

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "bash",
      "args": ["/path/to/linkedin-mcp/run.sh"]
    }
  }
}
```

Replace `/path/to/linkedin-mcp` with the absolute path to your clone. `run.sh`
creates `.venv/` and installs pinned dependencies on first launch (stamp-gated;
all bootstrap output goes to stderr, keeping the MCP stdio channel clean).

Then follow [docs/SETUP.md](docs/SETUP.md) to create your LinkedIn app and sign
in — the server has no credentials until you do.

## Setup

Follow [docs/SETUP.md](docs/SETUP.md) — it walks through creating your own
LinkedIn Developer app, registering the redirect URL, filling
`~/.config/linkedin-mcp/.env` with your own client ID and secret, running
`auth_start`, and applying for the partner program.

## Development

```bash
bash run.sh --help                                # bootstraps the venv (runtime deps only)
.venv/bin/pip install -r requirements-dev.txt     # adds pytest + coverage tooling
.venv/bin/python -m pytest                        # offline — every test runs against a mock transport
```

The test suite includes a **granted-write fixture**: a mock LinkedIn where
every documented write endpoint happily returns 200. Tests assert that
`propose_edit` leaves **zero** non-GET requests in the recorded log even
when writes would succeed, and (positive control) that `apply_proposal` does
record the documented write call in the same fixture. `--live-probe` is a
diagnostic flag (single real request, discriminates "endpoint right but
scope not granted" from "endpoint wrong"); it never runs in tests or CI and
requires `LINKEDIN_MCP_LIVE_PROBE=1`.

The API surface is pinned to dated verbatim excerpts from the official docs
in [docs/api-notes.md](docs/api-notes.md).

## License

MIT — see [LICENSE](LICENSE).
