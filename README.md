# MNI Automation Manager

MNI Automation Manager is an open-source control panel for running an AI assistant across chat platforms, admin workflows, local knowledge, and external automations.

Created by Raj Singh, founder of MarkanM.

Short description for GitHub:

```text
MNI Automation Manager is an open-source AI automation dashboard for Telegram, Discord, WhatsApp, Instagram, Google Meet, email, local knowledge, and n8n workflows. Built by Raj Singh, founder of MarkanM.
```

## What Runs Where

- `backend/app.py` starts the Flask API, creates database tables, seeds default config, and registers routes.
- `admin/templates/login.html` and `admin/templates/dashboard.html` are the connected admin GUI.
- `backend/routes/admin.py` powers admin panels for RAG, API keys, users, platform bots, logs, outreach, data agents, and monitor views.
- `backend/routes/chat.py` processes chat messages through moderation, local data, AI routing, and logging.
- `backend/routes/platforms.py` receives Telegram, Discord, WhatsApp, and Instagram webhooks.
- `bot/index.js` starts the Node bot layer for supported platform workers.

## Quick Start

```bash
python scripts/setup_local.py --install-deps --install-node
python backend/app.py
```

Open `http://localhost:5000/admin`.

On startup MNI automatically prepares first-run runtime files when missing:

- `instance/`
- local SQLite database tables
- `.env` from `.env.example`
- `config/google_auth.json` from `config/google_auth.example.json`
- default config, local knowledge sources, and data agents

On a fresh database the login page uses the default first-time unlock:

```text
username: admin
password: admin@123@321
```

After that, choose password login, Google button login, or password plus Google button.

## Feature Map

- Admin UI: login, Google admin login, allowed emails, users, admins, settings, logs, and monitor chat.
- AI: text chat, prompt/RAG, personalities, API provider routing, image, voice, and video tools when configured.
- Channels: Telegram, Discord, WhatsApp, Instagram, platform bots, moderation, and outreach.
- Content/data: local knowledge files, blogs/posts, data agents, forms, email logs, appointments, and availability.
- Automations: n8n URL/API key/webhook setup, test triggers, and bot-trigger keywords.
- Deployment: local SQLite, Render/Postgres, Docker, docker-compose, and ignored secret files.

## Google Admin Login

For the login button, the admin enters these once in `Access & Login`:

- Google OAuth Client ID
- Allowed Google admin emails

Users do not paste tokens every time. They click the Google button, choose their account, and the backend verifies the Google JWT before creating the MNI session. The email inside the verified token must match the allowlist, so unknown Google accounts cannot enter.

Google Client Secret is not needed for the login button and must not be exposed in frontend code. Client Secret and Refresh Token are only for backend Google API features such as Meet automation.

## Manual Setup

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm install
cp .env.example .env
cp config/google_auth.example.json config/google_auth.json
MARKANM_DISABLE_BACKGROUND_WORKERS=true .venv/bin/python backend/app.py
```

Start platform workers only after tokens are configured:

```bash
node bot/index.js
```

## Google Meet Automation

You can configure Google Meet OAuth in either place:

- Admin GUI: `Platform Integration` -> `Google Meet Automation`
- Local ignored file: copy `config/google_auth.example.json` to `config/google_auth.json`

The local `config/google_auth.json` file is git-ignored. Use a refresh token with:

```text
https://www.googleapis.com/auth/meetings.space.created
```

After configuration, user messages such as "create a Google Meet" will call the Meet API and return a meeting link.

Google Meet checklist:

- Google Cloud project exists.
- OAuth consent screen is configured.
- OAuth Client ID and Client Secret are available.
- Refresh Token has the Meet create scope.
- Deployment origin/redirect settings match where MNI runs.
- MNI `Connect Apps` has Google Client ID, Client Secret, and Refresh Token saved.

## Email Automation

MNI sends email through SMTP. Configure this in `Connect Apps` -> `Email Automation`:

- SMTP host and port
- SMTP username
- SMTP password or provider app password
- From email

For Gmail, use an App Password. Normal account passwords usually fail.

## n8n Automations

MNI supports a bring-your-own automation engine setup.

1. Run self-hosted n8n or use n8n Cloud.
2. Open `Access & Login` first and log in as admin.
3. Open `Automations`.
4. Add n8n Base URL, API key, default webhook URL, and trigger keyword.
5. Use `Send Test Trigger` to verify the workflow.

For Telegram, the configured keyword such as `/workflow` can trigger the default n8n webhook when the bot worker is running.

## MNI API

MNI can generate API keys for other tools, including n8n.

Open `MNI API` in the admin drawer and create a client. Each client can be limited to specific powers:

- chat
- image generation
- voice generation
- video generation
- selected local sources and data agents

Use the generated key with:

```text
POST /api/chat/client
X-API-Key: your_generated_mni_key
Content-Type: application/json
```

Example body:

```json
{
  "message": "Write a reply to this lead",
  "user_id": "n8n",
  "username": "n8n",
  "chat_id": "workflow"
}
```

MNI also gives each client a webhook URL:

```text
POST /api/chat/webhook/<webhook_slug>
```

Use the webhook URL when another app can send JSON but cannot set custom headers.

## Database

Default local database:

```text
instance/keli_ai.db
```

Production database:

```text
DATABASE_URL=postgresql://...
```

Startup fallback order:

1. `DATABASE_URL` or compatible env database URL
2. Admin-saved deployment database URL
3. Render disk SQLite
4. Local `instance/keli_ai.db`

Generated databases, logs, caches, `.env`, and `config/google_auth.json` are ignored.

## Open-Source Safety

Before publishing, check:

- `.env` is not committed.
- `config/google_auth.json` is not committed.
- `instance/*.db`, logs, `__pycache__`, `.venv`, and `node_modules` are not committed.
- Real keys are configured through env vars, admin UI, or ignored local files.
