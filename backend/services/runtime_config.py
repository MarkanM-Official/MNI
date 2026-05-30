import json
import os

from backend.models.config_model import BotConfig
from backend.services.secret_store import decrypt_config_value


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOOGLE_AUTH_CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config', 'google_auth.json')


ENV_CONFIG_MAP = {
    'admin_identities': ('ADMIN_IDENTITIES',),
    'backend_url': ('BACKEND_URL',),
    'discord_token': ('DISCORD_BOT_TOKEN', 'DISCORD_TOKEN'),
    'email_from': ('EMAIL_FROM', 'EMAIL_SENDER_EMAIL'),
    'email_smtp_host': ('EMAIL_SMTP_HOST',),
    'email_smtp_password': ('EMAIL_SMTP_PASSWORD',),
    'email_smtp_port': ('EMAIL_SMTP_PORT',),
    'email_smtp_username': ('EMAIL_SMTP_USERNAME',),
    'google_client_id': ('GOOGLE_CLIENT_ID',),
    'google_client_secret': ('GOOGLE_CLIENT_SECRET',),
    'google_refresh_token': ('GOOGLE_REFRESH_TOKEN',),
    'instagram_access_token': ('INSTAGRAM_ACCESS_TOKEN',),
    'instagram_enabled': ('INSTAGRAM_ENABLED',),
    'keli_core_enabled': ('KELI_CORE_ENABLED',),
    'keli_core_name': ('KELI_CORE_NAME',),
    'keli_core_prefer_local_tools': ('KELI_CORE_PREFER_LOCAL_TOOLS',),
    'keli_core_stt_enabled': ('KELI_CORE_STT_ENABLED',),
    'keli_core_system_prompt': ('KELI_CORE_SYSTEM_PROMPT',),
    'keli_core_text_provider': ('KELI_CORE_TEXT_PROVIDER',),
    'keli_core_tts_enabled': ('KELI_CORE_TTS_ENABLED',),
    'keli_core_voice_call_secret': ('KELI_CORE_VOICE_CALL_SECRET',),
    'keli_core_voice_call_webhook': ('KELI_CORE_VOICE_CALL_WEBHOOK',),
    'keli_core_voice_gender': ('KELI_CORE_VOICE_GENDER',),
    'keli_core_voice_tone': ('KELI_CORE_VOICE_TONE',),
    'meta_verify_token': ('META_VERIFY_TOKEN',),
    'moderation_enabled': ('MODERATION_ENABLED',),
    'telegram_token': ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_TOKEN'),
    'telegram_enabled': ('TELEGRAM_ENABLED',),
    'trusted_member_ids': ('TRUSTED_MEMBER_IDS',),
    'whatsapp_access_token': ('WHATSAPP_ACCESS_TOKEN',),
    'whatsapp_enabled': ('WHATSAPP_ENABLED',),
    'whatsapp_phone_number_id': ('WHATSAPP_PHONE_NUMBER_ID',),
}

GOOGLE_AUTH_FILE_MAP = {
    'google_client_id': ('client_id', 'google_client_id'),
    'google_client_secret': ('client_secret', 'google_client_secret'),
    'google_refresh_token': ('refresh_token', 'google_refresh_token'),
}

_GOOGLE_AUTH_FILE_CACHE = None


def _env_value(key):
    for env_key in ENV_CONFIG_MAP.get(str(key or '').strip(), ()):
        value = os.getenv(env_key)
        if value is not None and str(value).strip() != '':
            return value
    return ''


def _load_google_auth_file():
    global _GOOGLE_AUTH_FILE_CACHE
    if _GOOGLE_AUTH_FILE_CACHE is not None:
        return _GOOGLE_AUTH_FILE_CACHE
    if not os.path.exists(GOOGLE_AUTH_CONFIG_PATH):
        _GOOGLE_AUTH_FILE_CACHE = {}
        return _GOOGLE_AUTH_FILE_CACHE
    try:
        with open(GOOGLE_AUTH_CONFIG_PATH, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except Exception:
        payload = {}
    _GOOGLE_AUTH_FILE_CACHE = payload if isinstance(payload, dict) else {}
    return _GOOGLE_AUTH_FILE_CACHE


def _google_auth_file_value(key):
    payload = _load_google_auth_file()
    for file_key in GOOGLE_AUTH_FILE_MAP.get(str(key or '').strip(), ()):
        value = payload.get(file_key)
        if value is not None and str(value).strip() != '':
            return str(value).strip()
    return ''


def get_config_value(key, default=''):
    row = BotConfig.query.filter_by(key=key).first()
    if row:
        value = decrypt_config_value(key, row.value)
        if value is not None and str(value).strip() != '':
            return value
    env_value = _env_value(key)
    if env_value != '':
        return env_value
    file_value = _google_auth_file_value(key)
    if file_value != '':
        return file_value
    return default


def get_all_config_values():
    rows = {row.key: decrypt_config_value(row.key, row.value) for row in BotConfig.query.all()}
    merged = dict(rows)
    for key in ENV_CONFIG_MAP:
        if str(merged.get(key, '')).strip():
            continue
        env_value = _env_value(key)
        if env_value != '':
            merged[key] = env_value
    for key in GOOGLE_AUTH_FILE_MAP:
        if str(merged.get(key, '')).strip():
            continue
        file_value = _google_auth_file_value(key)
        if file_value != '':
            merged[key] = file_value
    return merged
