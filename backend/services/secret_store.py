import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


SECRET_PREFIX = 'enc::'

SECRET_CONFIG_KEYS = {
    'telegram_token',
    'discord_token',
    'whatsapp_access_token',
    'whatsapp_phone_number_id',
    'instagram_access_token',
    'meta_verify_token',
    'google_client_id',
    'google_client_secret',
    'google_refresh_token',
    'email_smtp_password',
    'admin_google_auth_token',
    'admin_github_auth_token',
    'mikasha_system_api_key',
    'keli_core_voice_call_secret',
    'n8n_api_key',
}

SECRET_ENV_KEYS = {
    'ADMIN_SECRET_KEY',
    'ANTHROPIC_API_KEY',
    'BOT_SYNC_SECRET',
    'DATABASE_URL',
    'DISCORD_TOKEN',
    'ELEVENLABS_API_KEY',
    'FLASK_SECRET_KEY',
    'GEMINI_API_KEY',
    'GOOGLE_AI_API_KEY',
    'GOOGLE_API_KEY',
    'INSTAGRAM_ACCESS_TOKEN',
    'JWT_SECRET_KEY',
    'META_VERIFY_TOKEN',
    'MIKASHA_SYSTEM_API_KEY',
    'OPENAI_API_KEY',
    'SARVAM_API_KEY',
    'TELEGRAM_TOKEN',
    'WHATSAPP_ACCESS_TOKEN',
    'WHATSAPP_PHONE_NUMBER_ID',
}

SECRET_ENV_SUFFIXES = (
    '_TOKEN',
    '_SECRET',
    '_PASSWORD',
    '_API_KEY',
)


def _raw_master_secret():
    return (
        os.getenv('MNI_SECRET_KEY')
        or os.getenv('MARKANM_SECRET_KEY')
        or os.getenv('SECRET_KEY')
        or os.getenv('JWT_SECRET_KEY')
        or os.getenv('FLASK_SECRET_KEY')
        or 'mni-local-dev-secret'
    )


def _fernet():
    digest = hashlib.sha256(_raw_master_secret().encode('utf-8')).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def is_encrypted(value):
    return str(value or '').startswith(SECRET_PREFIX)


def is_secret_config_key(key):
    return str(key or '').strip() in SECRET_CONFIG_KEYS


def is_secret_env_key(key):
    env_key = str(key or '').strip().upper()
    if not env_key:
        return False
    if env_key in SECRET_ENV_KEYS:
        return True
    return any(env_key.endswith(suffix) for suffix in SECRET_ENV_SUFFIXES)


def encrypt_secret_text(value):
    text = str(value or '')
    if not text:
        return text
    if is_encrypted(text):
        return text
    token = _fernet().encrypt(text.encode('utf-8')).decode('utf-8')
    return f'{SECRET_PREFIX}{token}'


def decrypt_secret_text(value):
    text = str(value or '')
    if not text:
        return text
    if not is_encrypted(text):
        return text
    token = text[len(SECRET_PREFIX):]
    try:
        return _fernet().decrypt(token.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        return ''


def encrypt_config_value(key, value):
    return encrypt_secret_text(value) if is_secret_config_key(key) else str(value)


def decrypt_config_value(key, value):
    return decrypt_secret_text(value) if is_secret_config_key(key) else value


def decrypt_environment_secrets():
    for key, value in list(os.environ.items()):
        if key == 'MARKANM_SECRET_KEY':
            continue
        if not is_secret_env_key(key):
            continue
        if not is_encrypted(value):
            continue
        plain = decrypt_secret_text(value)
        if plain:
            os.environ[key] = plain


def mask_secret(value):
    plain = decrypt_secret_text(value)
    if not plain:
        return ''
    if len(plain) <= 12:
        return '***'
    return plain[:8] + '...' + plain[-4:]
