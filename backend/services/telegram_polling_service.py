"""
Telegram long-polling worker for local setups where public webhooks are unavailable.
"""
import logging
import threading
import time
import os

import requests
from urllib.parse import urlparse

from backend.models.config_model import BotConfig
from backend.models.platform_bot import PlatformBot
from backend.routes.platforms import handle_telegram_message
from backend.services.runtime_config import get_config_value
from backend.services.secret_store import decrypt_config_value, decrypt_secret_text, encrypt_config_value


_last_update_ids = {}


def _is_public_http_url(url):
    parsed = urlparse(str(url or '').strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return False
    host = (parsed.hostname or '').lower()
    return host not in {'localhost', '127.0.0.1', '0.0.0.0'} and not host.startswith('192.168.')


def _ensure_polling_allowed(token):
    """Local installs need long polling; remove stale Telegram webhooks if present."""
    webhook_info = requests.get(
        f'https://api.telegram.org/bot{token}/getWebhookInfo',
        timeout=15,
    ).json()
    webhook_url = webhook_info.get('result', {}).get('url') if webhook_info.get('ok') else ''
    if not webhook_url:
        return True

    backend_url = _get_config('backend_url', '')
    if _is_public_http_url(backend_url):
        return False

    requests.post(
        f'https://api.telegram.org/bot{token}/deleteWebhook',
        json={'drop_pending_updates': False},
        timeout=15,
    ).raise_for_status()
    logging.warning('[Telegram Polling] Removed stale webhook so local polling can receive messages.')
    return True


def _get_config(key, default=''):
    return get_config_value(key, default)


def _set_config(key, value):
    row = BotConfig.query.filter_by(key=key).first()
    stored = encrypt_config_value(key, value)
    if row:
        row.value = str(stored)
    else:
        row = BotConfig(key=key, value=str(stored))
        from backend.database import db
        db.session.add(row)
    from backend.database import db
    db.session.commit()


def _offset_key(bot_id):
    return f'last_telegram_update_id_bot_{bot_id}'


def _active_telegram_bots():
    bots = PlatformBot.query.filter_by(platform='telegram', is_active=True).order_by(PlatformBot.updated_at.desc(), PlatformBot.created_at.desc()).all()
    payload = []
    seen_tokens = set()
    for bot in bots:
        token = decrypt_secret_text(bot.bot_token).strip()
        if not token or token in seen_tokens:
            continue
        seen_tokens.add(token)
        payload.append({
            'id': bot.id,
            'name': bot.name,
            'token': token,
            'source': 'platform_bot',
        })

    fallback_token = (_get_config('telegram_token', '') or os.getenv('TELEGRAM_BOT_TOKEN', '')).strip()
    if fallback_token and fallback_token not in seen_tokens:
        payload.append({
            'id': 0,
            'name': 'Telegram Primary',
            'token': fallback_token,
            'source': 'config',
        })
    return payload


def _get_stored_offset(bot_id):
    key = _offset_key(bot_id)
    stored = int(_get_config(key, '0') or '0')
    cached = int(_last_update_ids.get(bot_id, 0) or 0)
    return max(stored, cached)


def _set_stored_offset(bot_id, value):
    value = int(value or 0)
    _last_update_ids[bot_id] = value
    _set_config(_offset_key(bot_id), value)


def _process_update_async(app, msg, token, bot_id, bot_name):
    def runner():
        try:
            with app.app_context():
                handle_telegram_message(
                    msg,
                    token_override=token,
                    bot_id=bot_id,
                    bot_name=bot_name,
                )
        except Exception as exc:
            logging.error(f"[Telegram Message Worker:{bot_name or bot_id}] {exc}")

    threading.Thread(target=runner, daemon=True).start()


def _poll_loop(app):
    while True:
        try:
            with app.app_context():
                telegram_enabled = _get_config('telegram_enabled', 'true').lower() == 'true'
                bots = _active_telegram_bots()

            if not telegram_enabled or not bots:
                time.sleep(5)
                continue

            with app.app_context():
                for bot in bots:
                    token = bot['token']
                    bot_id = bot['id']
                    offset = _get_stored_offset(bot_id)

                    if not _ensure_polling_allowed(token):
                        continue

                    response = requests.get(
                        f'https://api.telegram.org/bot{token}/getUpdates',
                        params={'timeout': 20, 'offset': offset + 1, 'allowed_updates': ['message', 'edited_message']},
                        timeout=25,
                    )
                    response.raise_for_status()
                    updates = response.json().get('result', [])
                    if not updates:
                        continue

                    for update in updates:
                        offset = max(offset, int(update.get('update_id', 0)))
                        _set_stored_offset(bot_id, offset)
                        msg = update.get('message') or update.get('edited_message')
                        if not msg:
                            continue
                        _process_update_async(app, msg, token, bot_id, bot.get('name', 'Telegram'))
        except Exception as exc:
            logging.error(f"[Telegram Polling] {exc}")
            time.sleep(5)


def _bootstrap_pending_updates(app):
    try:
        with app.app_context():
            telegram_enabled = _get_config('telegram_enabled', 'true').lower() == 'true'
            bots = _active_telegram_bots()
        if not telegram_enabled or not bots:
            return
        for bot in bots:
            with app.app_context():
                polling_allowed = _ensure_polling_allowed(bot["token"])
            if not polling_allowed:
                continue
            response = requests.get(
                f'https://api.telegram.org/bot{bot["token"]}/getUpdates',
                params={'limit': 20, 'allowed_updates': ['message', 'edited_message']},
                timeout=20,
            )
            response.raise_for_status()
            updates = response.json().get('result', [])
            max_seen = max([int(update.get('update_id', 0)) for update in updates], default=0)
            if max_seen:
                with app.app_context():
                    _set_stored_offset(bot['id'], max_seen)
    except Exception as exc:
        logging.error(f"[Telegram Bootstrap] {exc}")


def start_telegram_polling(app):
    _bootstrap_pending_updates(app)
    thread = threading.Thread(target=_poll_loop, args=(app,), daemon=True)
    thread.start()
    return thread
