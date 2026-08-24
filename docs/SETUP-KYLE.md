# SETUP-KYLE — LinkedIn app creation, OAuth, and the first live probe

Every step here is manual and yours alone: the app creation, the credential
entry, and the partner application all happen in your browser. No agent does
any of this for you, and no personal values ever go in this repo.

## 1. Create the LinkedIn Developer app

1. Open https://www.linkedin.com/developers/apps in your browser (logged in
   to your personal LinkedIn account).
2. Click the blue **Create app** button (top right).
3. Fill the form:
   - **App name:** `linkedin-mcp` (any name works; this shows on the OAuth
     consent screen).
   - **LinkedIn Page:** LinkedIn requires associating an app with a company
     page. If you have none, click "Create a new LinkedIn Page" link under
     the field, make a minimal page (Company → small business, any name),
     then come back and select it.
   - **App logo:** upload any square image (required).
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
   mkdir -p ~/.config/linkedin-mcp
   cp "/Users/kyle/code/custom MCPs/linkedin-mcp/.env.example" ~/.config/linkedin-mcp/.env
   chmod 600 ~/.config/linkedin-mcp/.env
   open -e ~/.config/linkedin-mcp/.env
   ```

3. In the TextEdit window that opens, replace `your-client-id-here` and
   `your-client-secret-here` with the real values from the Auth tab. Save
   and close. Never paste these values into a chat with any agent.

## 4. Request API access (partner program)

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

1. In the portfolio repo (or any project with the server registered), start
   a Claude Code session and run the `auth_start` tool.
2. It prints an authorization URL and starts a one-shot listener on
   localhost port 8765. Open the URL in your browser, sign in, click
   **Allow**.
3. The browser lands on the localhost callback page ("authorization
   complete"); the tool exchanges the code and writes the token into
   `~/.config/linkedin-mcp/.env` (still chmod 600).
4. Run `auth_status` to confirm the token and its expiry (LinkedIn access
   tokens live 60 days; when one expires, run `auth_start` again — the
   consent screen is skipped while you're logged in).

## 6. THE FIRST POST-OAUTH STEP: run the live probe

From the repo:

```bash
cd "/Users/kyle/code/custom MCPs/linkedin-mcp"
LINKEDIN_MCP_LIVE_PROBE=1 .venv/bin/python -m linkedin_mcp --live-probe
```

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
