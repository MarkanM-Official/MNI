## Render Setup

### 1. Create Postgres

On Render `New Postgres` page:

- `Name`: `mni-db`
- `Database`: `mni_automation`
- `User`: `mni_user`
- `Region`: same region as your backend service
- `PostgreSQL Version`: default is fine
- `Instance Type`: `Free` for testing, paid for production

After creation, copy the `Internal Database URL`.

### 2. Add Environment Variables to the Backend Web Service

Open your Render backend service and add these environment variables.

Required:

```env
DATABASE_URL=<paste the Internal Database URL here>
MNI_SECRET_KEY=<strong random secret>
ADMIN_SECRET_KEY=<strong random secret>
ADMIN_PASSWORD=<your admin login password>
BACKEND_URL=https://<your-backend-service>.onrender.com
```

Python runtime:

- this repo pins Python with `.python-version`
- Render should build it with Python `3.11.11`
- local `openai-whisper` is intentionally not installed in Render builds
- speech-to-text on Render falls back to hosted OpenAI transcription when `OPENAI_API_KEY` is configured

Only add the integrations you actually use:

```env
TELEGRAM_BOT_TOKEN=
DISCORD_BOT_TOKEN=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
ELEVENLABS_API_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
INSTAGRAM_ACCESS_TOKEN=
EMAIL_SMTP_HOST=
EMAIL_SMTP_PORT=587
EMAIL_SMTP_USERNAME=
EMAIL_SMTP_PASSWORD=
EMAIL_FROM=
```

### 3. Add Environment Variables to the Bot Worker

If you deploy the Node bot worker too, add:

```env
TELEGRAM_BOT_TOKEN=
DISCORD_BOT_TOKEN=
BACKEND_URL=https://<your-backend-service>.onrender.com
```

### 4. Important Rule: Internal vs External Database URL

- Use the `Internal Database URL` inside your Render backend service as `DATABASE_URL`.
- Use the `External Database URL` only from your own laptop or tools like `psql`, pgAdmin, or DBeaver.

The application code automatically reads `DATABASE_URL` and stores all app data there.
Render health checks can use `/healthz`.

### 5. What Happens on First Deploy

- The backend starts
- SQLAlchemy connects to Postgres
- Tables are created automatically
- Default config is seeded

You do not upload a `.db` file when using Render Postgres.

### 6. When to Use a Persistent Disk Instead

Only use a Render disk if you want to keep SQLite.

Then:

- do not set `DATABASE_URL`
- attach a disk
- set `RENDER_DISK_PATH`

For your project, Postgres is the better option.

### 7. Future External Database Switch

If later you move from Render Postgres to your own PostgreSQL server:

- open Admin Panel -> `Platform Integration`
- fill `Database Deployment`
- save the new DB URL and optional `sslmode`
- restart the backend service

Priority order stays:

- env `DATABASE_URL`
- admin-saved deployment DB URL
- Render disk SQLite
- local SQLite
