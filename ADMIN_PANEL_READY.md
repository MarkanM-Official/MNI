# MNI Automation Manager - Admin Panel Ready

MNI is now set up as a normal-user friendly automation manager, not a developer-only control panel. The backend feature set is still available, but the GUI is grouped around everyday tasks: connect apps, choose login security, control AI replies, manage users, review conversations, and monitor the system.

## What changed

- App name updated to **MNI Automation Manager** across the main GUI.
- Login now supports first-run setup with the default bootstrap credentials:
  - Username: `admin`
  - Password: `admin@123@321`
- After first setup, the admin can choose password login, Google button login, or password plus Google button for extra security.
- A new **Access & Login** page lets the admin update username, password, Google Client ID, allowed Google emails, and login method from the backend UI.
- The dashboard navigation was simplified into friendly groups: Start, Setup, AI Assistant, Channels, People, Content, Work, and Advanced.
- Fake/dead standalone pages were removed. Working backend-connected pages were kept.
- Local setup helper added: `python scripts/setup_local.py`
- Google auth local config example added: `config/google_auth.example.json`

## Main URLs

- Admin login: `http://127.0.0.1:5000/admin`
- Dashboard: `http://127.0.0.1:5000/admin/dashboard`
- Health check: `http://127.0.0.1:5000/healthz`

## First-run flow

On a fresh database, open `/admin`. MNI shows a first-time setup screen. Enter the bootstrap username/password, choose the future login method, then set the permanent admin login.

On this current local database, setup is already complete, so the normal login screen appears.

## Local setup

```bash
cd MNI
python scripts/setup_local.py --install-deps --install-node
python backend/app.py
```

The setup script creates local config files from examples, prepares the instance folder, and initializes database tables. Secret files like `.env`, local databases, and `config/google_auth.json` are ignored by git.

## Important backend areas

- `backend/routes/auth.py` - login, first-run setup, auth settings
- `backend/routes/admin.py` - admin APIs for config, platforms, users, messages, MNI API keys, data sources
- `backend/services/runtime_config.py` - runtime config, including optional Google auth JSON
- `backend/services/secret_store.py` - encrypted secret config handling
- `admin/templates/login.html` - first-run setup and login UI
- `admin/templates/dashboard.html` - full MNI admin GUI

## Verification

The backend was compiled, an isolated first-run auth flow was tested, and the running server responds on `/healthz`. No real API keys or private tokens should be committed; keep secrets in local `.env`, the database, or ignored config files.
