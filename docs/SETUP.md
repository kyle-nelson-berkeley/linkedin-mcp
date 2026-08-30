# Setup — LinkedIn app creation, OAuth, and the first live probe

Every step here is manual and yours alone: the app creation, the credential
entry, and the partner application all happen in your browser. No agent does
any of this for you, and no personal values ever go in this repo.

**Two things to know before you start:**

1. **You create your own LinkedIn Developer app** (§1) and use **your own**
   client ID and secret (§3). This project ships no shared credentials and
   runs no server — every copy is self-hosted on the machine of the person
   running it.
2. **Writing to your profile needs LinkedIn partner approval, which you must
   apply for yourself** (§4). Approval is granted per developer app, so it is
   *your* app that needs it — nobody else's approval carries over. Until it
   lands (and it may never: the permission goes to "select developers"),
   `apply_proposal` returns an invalid-scope error, which is expected. Steps
   1–3 and 5 all work without it: sign-in, reading your profile, drafting
   edits, and reviewing diffs.

Paths below are written relative to your clone, so run the commands from the
directory you cloned this repository into:

```bash
git clone https://github.com/kyle-nelson-berkeley/linkedin-mcp.git
cd linkedin-mcp
```

## 1. Create the LinkedIn Developer app

1. Open https://www.linkedin.com/developers/apps in your browser (logged in
   to your personal LinkedIn account).
2. Click the blue **Create app** button (top right).
3. Fill the form:
   - **App name:** any name works; this shows on the OAuth consent screen.
     (The software is `linkedin-mcp`; the reference LinkedIn app listing that
     [PRIVACY.md](../PRIVACY.md) covers is registered as `profile-edit-mcp`.
     Your app is separate from both and is yours alone.)
   - **LinkedIn Page:** LinkedIn requires associating an app with a company
     page. If you have none, click "Create a new LinkedIn Page" link under
     the field, make a minimal page (Company → small business, any name),
     then come back and select it.
   - **App logo:** a square image is required. Use your own, or one of the
     ready-made square PNGs in this repo's `assets/` directory (for example
     `assets/profile-edit-mcp-logo-512.png`).
   - Check the legal-agreement box.
4. Click **Create app**.

## 2. Register the redirect URL

1. In your new app, open the **Auth** tab.
2. Under **OAuth 2.0 settings → Authorized redirect URLs for your app**,
   click the pencil icon, then **Add redirect URL**.
3. Enter exactly:

   ```
   http://localhost:8765/callback
   ```

   (If the portal refuses a plain-http localhost URL, tell your agent —
   the server's redirect listener port/scheme is configurable via
   `LINKEDIN_REDIRECT_URI`, but the registered URL and the configured one
   must match character for character.)
4. Click **Update**.

## 3. Store the client credentials (you type these, nobody else)

1. Still on the **Auth** tab, find **Client ID** and **Primary Client
   Secret** (click the eye icon to reveal the secret).
2. In Terminal:

   ```bash
   # run this from your clone of this repository
   mkdir -p ~/.config/linkedin-mcp
   cp .env.example ~/.config/linkedin-mcp/.env
   chmod 600 ~/.config/linkedin-mcp/.env
   open -e ~/.config/linkedin-mcp/.env   # Linux: use `xdg-open` or any editor
   ```

3. In the editor that opens, replace `your-client-id-here` and
   `your-client-secret-here` with the real values from the Auth tab. Save
   and close. Never paste these values into a chat with any agent.

## 4. Request API access (partner program)

> **Status 2026-08-30: the program is CLOSED.** LinkedIn's Compliance FAQ
> (learn.microsoft.com/en-us/linkedin/compliance/compliance-api/compliance-faq,
> last updated 2026-05-15) says the Compliance API is a closed permission and
> is "currently not accepting applications for new Partners due to resource
> constraints" — LinkedIn will announce and restore the request form if that
> changes. It is also described as a private, *paid* partnership for
> FINRA/SEC-registered archiving use cases, which a single-user profile-edit
> tool would not satisfy even when applications reopen. Nothing below is
> currently actionable; the steps are kept for the day the program reopens.
> Everything else in this setup (OAuth, propose/diff tooling) works without it.

1. Open the app's **Products** tab.
2. Profile editing (`w_compliance`) is a **private permission** — it is not
   a self-serve product tile. Request it through LinkedIn's access form:
   the Products tab links to available programs, and restricted APIs go
   through https://developer.linkedin.com/ → "Request access" (the
   Compliance / Data portability program routes to a form).
3. Suggested application text (edit freely):

   > I am requesting Profile Edit API access for a personal, single-user
   > integration. The application is an open-source MCP (Model Context
   > Protocol) server — https://github.com/kyle-nelson-berkeley/linkedin-mcp —
   > that lets me edit MY OWN profile (headline, summary, positions,
   > skills, education) from my development environment. Every change is
   > drafted locally, shown to me as a diff, and sent to the API only
   > after my explicit approval; changes are posted unaltered, exactly as
   > I approve them, satisfying the member-request and unaltered-posting
   > requirements in the Profile Edit API documentation. The app serves
   > one member (me), stores tokens locally with owner-only permissions,
   > and requests the minimum scope needed.

4. Expect this to take days-to-weeks, and possibly a rejection (the
   permission is granted to "select developers"). Nothing else in this
   setup is blocked while you wait.

## 5. First OAuth (works before partner approval)

1. In any project where you registered the server (see the README's Install
   section), start an agent session and run the `auth_start` tool.
2. It prints an authorization URL and starts a one-shot listener on
   localhost port 8765. Open the URL in your browser, sign in, click
   **Allow**.
3. The browser lands on the localhost callback page ("authorization
   complete"); the tool exchanges the code and writes the token into
   `~/.config/linkedin-mcp/.env` (still chmod 600).
4. Run `auth_status` to confirm the token and its expiry (LinkedIn access
   tokens live 60 days; when one expires, run `auth_start` again — the
   consent screen is skipped while you're logged in).
5. Scope note: this first sign-in requests only the read scope
   (`r_basicprofile`) on purpose — the write scope `w_compliance` is a
   private permission, and asking for it before LinkedIn assigns it makes
   the whole authorization fail with "invalid scope". **After partner
   approval lands**, add this line to `~/.config/linkedin-mcp/.env`:

   ```
   LINKEDIN_SCOPE=r_basicprofile w_compliance
   ```

   then run `auth_start` once more to get a token that carries the write
   scope.

## 6. THE FIRST POST-OAUTH STEP: run the live probe

From your clone of this repository:

```bash
LINKEDIN_MCP_LIVE_PROBE=1 .venv/bin/python -m linkedin_mcp --live-probe
```

(`.venv/` is created the first time `run.sh` runs — see the README's Install
section. If it does not exist yet, run `bash run.sh --help` once.)

This sends exactly ONE real request to the documented profile-write
endpoint and interprets the result. Read the outcome in plain words:

- **HTTP 403, or an error mentioning invalid scope / ACCESS_DENIED /
  permission** → the endpoint shape is right and your setup is correct;
  LinkedIn is simply refusing because partner approval hasn't landed.
  **This is the expected outcome. Stop here and wait for the partner
  program.** Do not let any agent "work around" it.
- **HTTP 404, or a 400 complaining the request is malformed** → the
  endpoint spec this server was built from is wrong or has drifted. Do
  not work around it either — re-open the build: hand the full probe
  output to an agent with docs/api-notes.md and have the spec re-verified
  against the current Microsoft Learn docs.
- **HTTP 200** → partner access is live and the write path works; from
  here on, `propose_edit` → your approval → `apply_proposal` is the
  workflow.
