# MNI Automation Manager

MNI Automation Manager is an open-source admin panel for running an AI automation system from one place.

It helps you connect chat platforms, manage AI provider keys, review conversations, control users, add local knowledge, send email, create Google Meet links, expose your own MNI API, and trigger n8n workflows without digging through backend files.

Created by **Raj Singh**, founder of **MarkanM**.

## GitHub Description

```text
Open-source AI automation manager for bots, APIs, n8n workflows, Google Meet, email, users, logs, and local knowledge.
```

## What MNI Can Do

- Manage Telegram, Discord, WhatsApp, and Instagram bot connections.
- Add AI provider keys for text, image, voice, and video features.
- Chat with MNI from the admin panel.
- Store local knowledge and use it inside AI replies.
- Create and manage data agents that watch/crawl useful sources.
- Review conversations, users, moderation events, and blocked users.
- Configure Google admin login with an allowed-email list.
- Configure Google Meet automation.
- Send email through SMTP and keep email logs.
- Connect n8n using your own n8n URL, API key, webhook URL, and trigger keyword.
- Generate MNI API keys and webhook URLs for websites, bots, tools, and n8n.
- Run locally with SQLite or deploy with PostgreSQL.

## Who This Is For

MNI is for people who want a self-hosted AI control room:

- creators running AI bots
- founders testing automation ideas
- small teams managing support or lead flows
- developers who want an open backend they can customize
- non-developers who need a simpler dashboard after setup

## Project Structure

```text
backend/                 Flask API, auth, admin routes, chat routes, services
admin/templates/          Login page and dashboard UI
bot/                      Node bot workers for Telegram and Discord
config/                   Default RAG prompt and Google auth example
database/                 Database initialization helpers
docs/                     Extra guides and workflow notes
scripts/                  Local setup and utility scripts
tests/                    Basic test coverage
docker-compose.yml        Local Docker setup with optional n8n profile
render.yaml               Render deployment blueprint
```

## Quick Start

From the project folder:

```bash
python scripts/setup_local.py --install-deps --install-node
python backend/app.py
```

Open:

```text
http://localhost:5000/admin
```

Fresh install login:

```text
username: admin
password: admin@123@321
```

After the first unlock, set your permanent login method from the admin panel:

- password only
- Google login
- password plus Google login

## What Happens on First Run

MNI prepares the local runtime automatically when files are missing:

- creates `instance/`
- creates local SQLite tables
- creates `.env` from `.env.example`
- creates `config/google_auth.json` from `config/google_auth.example.json`
- seeds default config
- seeds local knowledge sources
- seeds data agents

Runtime files are ignored by git so your local credentials and database do not get committed.

## Manual Setup

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm install
cp .env.example .env
cp config/google_auth.example.json config/google_auth.json
.venv/bin/python backend/app.py
```

Start platform workers only after tokens are configured:

```bash
node bot/index.js
```

For local Telegram use, the Flask backend can also run polling workers when background workers are enabled.

## Docker

```bash
docker compose up --build
```

Optional n8n service:

```bash
docker compose --profile automation up --build
```

Open MNI at:

```text
http://localhost:5000/admin
```

Open n8n at:

```text
http://localhost:5678
```

## Main Admin Sections

- **Home**: overview, stats, quick actions
- **MNI Chat**: talk to your configured AI backend
- **Access & Login**: password and Google admin login settings
- **Connect Apps**: platform tokens, backend URL, Google Meet, email, database settings
- **Automations**: n8n URL, API key, webhook URL, trigger keyword, test trigger
- **AI Providers**: text/image/voice/video API keys and fallback routing
- **AI Brain**: RAG prompt and knowledge behavior
- **Channel Bots**: manage platform-specific bot entries
- **Conversations**: review user messages and AI responses
- **MNI API**: generate API keys and webhook URLs for external tools

## MNI API for n8n and External Tools

MNI can generate API keys for other apps.

Open **MNI API** in the admin drawer and create a client. You can choose what that key is allowed to use:

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

MNI also gives each API client a webhook URL:

```text
POST /api/chat/webhook/<webhook_slug>
```

Use the webhook URL when another tool can send JSON but cannot set custom headers.

## n8n Integration

n8n is the automation layer. MNI handles AI, bot logic, users, and admin state. n8n handles actions across external apps.

Example workflows:

- Telegram lead -> MNI -> n8n -> Google Sheet + email notification
- User asks to book a meeting -> n8n checks calendar -> creates Meet link -> returns result
- Support message -> n8n creates a ticket in Notion, Airtable, HubSpot, or another CRM
- Admin chat -> Run n8n automation -> send payload to a workflow webhook

Setup:

1. Run self-hosted n8n or use n8n Cloud.
2. Open MNI admin.
3. Go to **Automations**.
4. Add n8n Base URL.
5. Add n8n API key.
6. Add default webhook URL.
7. Set a trigger keyword such as `/workflow`.
8. Use **Send Test Trigger**.

## Google Admin Login

For Google login, the admin enters these once in **Access & Login**:

- Google OAuth Client ID
- allowed Google admin emails

Users do not paste tokens each time. They click the Google button, choose their account, and the backend verifies the Google JWT. The email inside the verified token must match the allowlist.

Google Client Secret is not needed for the login button. Do not put a client secret in frontend code.

## Google Meet Automation

Google Meet automation needs backend OAuth credentials:

- Google OAuth Client ID
- Google OAuth Client Secret
- Google Refresh Token

The refresh token should include:

```text
https://www.googleapis.com/auth/meetings.space.created
```

Checklist:

- Google Cloud project exists.
- OAuth consent screen is configured.
- OAuth Client ID and Client Secret are created.
- Refresh Token has the Meet create scope.
- Deployment URL/origin matches Google Cloud settings.
- MNI **Connect Apps** has the Google credentials saved.

You can also use the ignored local file:

```text
config/google_auth.json
```

## Email Automation

MNI sends email through SMTP.

Configure in **Connect Apps**:

- SMTP host
- SMTP port
- SMTP username
- SMTP password or app password
- From email

For Gmail, use an App Password. Normal Gmail account passwords usually fail.

## Database

Default local database:

```text
instance/keli_ai.db
```

Production database:

```text
DATABASE_URL=postgresql://...
```

Startup priority:

1. `DATABASE_URL`
2. admin-saved deployment database URL
3. Render disk SQLite
4. local SQLite

## Deployment

MNI includes:

- `Dockerfile`
- `docker-compose.yml`
- `render.yaml`
- `RENDER_SETUP.md`

For Render, PostgreSQL is recommended. Set `DATABASE_URL` in the Render service and let MNI create tables on startup.

## Open-Source Safety

These files are ignored and should not be committed:

```text
.env
config/google_auth.json
instance/*.db
instance/deployment_settings.json
.venv/
node_modules/
__pycache__/
*.log
```

Before publishing changes:

```bash
git status --short --ignored
git grep -n "YOUR_REAL_SECRET_OR_TOKEN" || true
```

Use environment variables, ignored local files, or the admin secret store for real keys.

## License

This project is licensed under the Apache License 2.0.

Copyright (c) 2026 Raj Singh, founder of MarkanM, and MNI Automation Manager contributors.
