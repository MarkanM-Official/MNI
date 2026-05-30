# Contributing

Thanks for helping improve MNI Automation Manager.

## Local Setup

```bash
python scripts/setup_local.py --install-deps --install-node
python backend/app.py
```

Open `http://127.0.0.1:5000/admin`.

## Branch Names

- `feature/short-description`
- `fix/short-description`
- `docs/short-description`

## Pull Requests

- Keep changes focused.
- Do not commit `.env`, local databases, generated logs, `node_modules`, or real API keys.
- Contributions are submitted under the Apache License 2.0 unless explicitly stated otherwise.
- Include screenshots for UI changes.
- Add or update tests when touching auth, admin APIs, data agents, or platform webhooks.
- Run basic checks before opening a PR:

```bash
python -m compileall backend database scripts
pytest
```

## Security

Please do not open public issues for real credentials or exploitable vulnerabilities. Remove secrets from logs and screenshots before sharing.
