import json
import os
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from backend.services.secret_store import decrypt_secret_text, encrypt_secret_text


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INSTANCE_DIR = os.path.join(PROJECT_ROOT, 'instance')
DEFAULT_DB_PATH = os.path.join(INSTANCE_DIR, 'keli_ai.db')
SETTINGS_PATH = os.path.join(INSTANCE_DIR, 'deployment_settings.json')

DATABASE_URL_ENV_KEYS = (
    'DATABASE_URL',
    'RENDER_INTERNAL_DATABASE_URL',
    'INTERNAL_DATABASE_URL',
)


def _ensure_instance_dir():
    os.makedirs(INSTANCE_DIR, exist_ok=True)


def _normalize_database_uri(uri, ssl_mode=''):
    raw = str(uri or '').strip().replace('postgres://', 'postgresql://')
    if not raw:
        return ''
    if raw in {'sqlite:///keli_ai.db', 'sqlite:///' + os.path.basename(DEFAULT_DB_PATH)}:
        _ensure_instance_dir()
        return f'sqlite:///{DEFAULT_DB_PATH}'
    if not raw.startswith('postgresql://') or not ssl_mode:
        return raw

    parsed = urlsplit(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault('sslmode', ssl_mode)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def load_deployment_settings():
    if not os.path.exists(SETTINGS_PATH):
        return {}
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    database_url = decrypt_secret_text(payload.get('database_url', ''))
    return {
        'database_url': database_url,
        'database_label': str(payload.get('database_label', '')).strip(),
        'database_ssl_mode': str(payload.get('database_ssl_mode', '')).strip(),
        'updated_at': payload.get('updated_at'),
    }


def save_deployment_settings(data):
    current = load_deployment_settings()
    merged = {
        'database_url': str(data.get('database_url', current.get('database_url', '')) or '').strip(),
        'database_label': str(data.get('database_label', current.get('database_label', '')) or '').strip(),
        'database_ssl_mode': str(data.get('database_ssl_mode', current.get('database_ssl_mode', '')) or '').strip(),
    }

    _ensure_instance_dir()
    payload = {
        'database_url': encrypt_secret_text(merged['database_url']) if merged['database_url'] else '',
        'database_label': merged['database_label'],
        'database_ssl_mode': merged['database_ssl_mode'],
        'updated_at': datetime.utcnow().isoformat(),
    }
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    return load_deployment_settings()


def resolve_database_uri():
    settings = load_deployment_settings()
    for key in DATABASE_URL_ENV_KEYS:
        value = str(os.getenv(key, '')).strip()
        if value:
            return _normalize_database_uri(value, settings.get('database_ssl_mode', ''))

    configured = _normalize_database_uri(
        settings.get('database_url', ''),
        settings.get('database_ssl_mode', ''),
    )
    if configured:
        return configured

    render_disk_path = str(os.getenv('RENDER_DISK_PATH', '')).strip()
    if render_disk_path:
        disk_db_path = os.path.join(render_disk_path, 'keli_ai.db')
        os.makedirs(os.path.dirname(disk_db_path), exist_ok=True)
        return f'sqlite:///{disk_db_path}'

    _ensure_instance_dir()
    return f'sqlite:///{DEFAULT_DB_PATH}'


def _mask_database_uri(uri):
    value = str(uri or '').strip()
    if not value:
        return ''
    if value.startswith('sqlite:///'):
        return value
    try:
        parsed = urlsplit(value)
    except Exception:
        return 'configured'
    host = parsed.hostname or 'configured'
    database_name = parsed.path.rsplit('/', 1)[-1] if parsed.path else ''
    username = parsed.username or ''
    auth = f'{username}:***@' if username else ''
    path = f'/{database_name}' if database_name else parsed.path
    return urlunsplit((parsed.scheme, f'{auth}{host}', path, parsed.query, parsed.fragment))


def build_database_diagnostics(active_uri=''):
    settings = load_deployment_settings()
    resolved = str(active_uri or resolve_database_uri()).strip()
    env_source = next((key for key in DATABASE_URL_ENV_KEYS if str(os.getenv(key, '')).strip()), '')
    database_url = settings.get('database_url', '')

    if resolved.startswith('postgresql://'):
        mode = 'postgresql'
    elif resolved.startswith('sqlite:///'):
        mode = 'sqlite'
    else:
        mode = 'unknown'

    if env_source:
        source = f'env:{env_source}'
    elif database_url:
        source = 'admin_saved'
    elif str(os.getenv('RENDER_DISK_PATH', '')).strip():
        source = 'render_disk'
    else:
        source = 'local_default'

    return {
        'mode': mode,
        'source': source,
        'active_uri': resolved,
        'active_uri_masked': _mask_database_uri(resolved),
        'saved_database_url': database_url,
        'saved_database_url_masked': _mask_database_uri(database_url),
        'database_label': settings.get('database_label', ''),
        'database_ssl_mode': settings.get('database_ssl_mode', ''),
        'env_override': bool(env_source),
        'restart_required_for_saved_changes': True,
        'settings_file_path': SETTINGS_PATH,
    }
