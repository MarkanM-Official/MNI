"""
MNI Automation Manager - Admin Routes (Full Control Panel API)
All routes require JWT authentication.
"""
import os
import re
import json
import base64
import requests
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from backend.database import db
from backend.models.user import User
from backend.models.message import Message
from backend.models.config_model import BotConfig, ApiKey
from backend.models.local_data import LocalDataSource
from backend.models.blog_post import BlogPost
from backend.models.moderation_event import ModerationEvent
from backend.services.local_data_service import ensure_local_data_sources, ensure_default_data_agents, collect_local_data_debug
from backend.models.bot_personality import BotPersonality
from backend.models.outreach import OutreachMessage
from backend.models.platform_bot import PlatformBot
from backend.models.data_agent import DataAgent
from backend.models.form_submission import FormSubmission
from backend.models.appointment import AppointmentRequest, AvailabilityRule
from backend.models.email_log import EmailLog
from backend.models.developer_api_client import DeveloperApiClient
import threading
from backend.services.crawler_service import run_agent_crawler
from backend.services.outreach_service import deliver_outreach_message
from backend.services.keli_core_service import get_keli_core_meta, process_keli_request, transcribe_keli_audio, answer_with_keli, get_keli_core_config as get_keli_core_runtime_config
from backend.services.ai_service import get_ai_response
from backend.services.voice_service import clone_voice, generate_voice
from backend.services.email_service import send_email_via_smtp
from backend.services.n8n_service import list_workflows, trigger_webhook
from backend.services.secret_store import (
    decrypt_config_value,
    decrypt_secret_text,
    encrypt_config_value,
    encrypt_secret_text,
)
from backend.services.runtime_config import get_all_config_values
from backend.services.deployment_settings import build_database_diagnostics, save_deployment_settings
from backend.services.admin_ops_service import (
    get_super_admin_identities,
    get_platform_admin_roles,
    grant_platform_admin,
    revoke_platform_admin,
    list_known_contacts,
    list_known_scopes,
    list_scope_members,
    send_platform_message,
)
import secrets

admin_bp = Blueprint('admin', __name__)


def _slugify(text):
    text = re.sub(r'[^a-zA-Z0-9\s-]', '', (text or '').strip().lower())
    text = re.sub(r'[\s_-]+', '-', text).strip('-')
    return text or 'untitled-post'


def _build_seo_payload(title, summary, content):
    title = (title or '').strip()
    summary = (summary or '').strip()
    content = (content or '').strip()
    seo_title = title[:60] if title else 'MNI Blog'
    seo_description = (summary or content[:155]).strip()[:155]
    return seo_title, seo_description


def _normalize_provider(provider, category=''):
    provider = (provider or '').strip().lower()
    category = (category or '').strip().lower()

    if 'api.sarvam.ai' in provider:
        return 'sarvam'
    if 'api.openai.com' in provider:
        return 'openai'
    if 'anthropic.com' in provider:
        return 'anthropic'
    if 'generativelanguage.googleapis.com' in provider or 'googleapis.com' in provider:
        return 'gemini'
    if 'elevenlabs.io' in provider:
        return 'elevenlabs'
    if 'stability.ai' in provider:
        return 'stability'
    if 'replicate.com' in provider:
        return 'replicate'
    if 'openai' in provider:
        return 'openai'
    if 'anthropic' in provider or 'claude' in provider:
        return 'anthropic'
    if 'sarvam' in provider:
        return 'sarvam'
    if 'gemini' in provider or 'google' in provider or 'generativelanguage' in provider:
        return 'gemini'
    if 'elevenlabs' in provider:
        return 'elevenlabs'
    if 'replicate' in provider:
        return 'replicate'
    if 'stability' in provider:
        return 'stability'

    defaults = {
        'text': 'openai',
        'image': 'openai',
        'voice': 'elevenlabs',
        'video': 'replicate',
    }
    return provider or defaults.get(category, '')


# ── Config helpers ────────────────────────────────────────────────────

def set_config(key, value):
    row = BotConfig.query.filter_by(key=key).first()
    stored_value = encrypt_config_value(key, value)
    if row:
        row.value = stored_value
    else:
        db.session.add(BotConfig(key=key, value=stored_value))
    db.session.commit()


def get_all_config():
    return get_all_config_values()


def _get_platform_tokens_payload():
    config = get_all_config()
    database = build_database_diagnostics()
    return {
        'telegram_token':       config.get('telegram_token', ''),
        'discord_token':        config.get('discord_token', ''),
        'whatsapp_token':       config.get('whatsapp_access_token', ''),
        'whatsapp_phone_id':    config.get('whatsapp_phone_number_id', ''),
        'instagram_token':      config.get('instagram_access_token', ''),
        'meta_verify_token':    config.get('meta_verify_token', 'mni-verify-token'),
        'backend_url':          config.get('backend_url', ''),
        'trusted_member_ids':   config.get('trusted_member_ids', ''),
        'moderation_enabled':   config.get('moderation_enabled', 'true'),
        'google_client_id':     config.get('google_client_id', ''),
        'google_client_secret': config.get('google_client_secret', ''),
        'google_refresh_token': config.get('google_refresh_token', ''),
        'email_smtp_host':      config.get('email_smtp_host', ''),
        'email_smtp_port':      config.get('email_smtp_port', '587'),
        'email_smtp_username':  config.get('email_smtp_username', ''),
        'email_smtp_password':  config.get('email_smtp_password', ''),
        'email_from':           config.get('email_from', ''),
        'n8n_url':              config.get('n8n_url', ''),
        'n8n_api_key':          config.get('n8n_api_key', ''),
        'n8n_webhook_url':      config.get('n8n_webhook_url', ''),
        'n8n_trigger_keyword':  config.get('n8n_trigger_keyword', '/workflow'),
        'database_url':         database.get('saved_database_url', ''),
        'database_url_masked':  database.get('saved_database_url_masked', ''),
        'database_label':       database.get('database_label', ''),
        'database_ssl_mode':    database.get('database_ssl_mode', ''),
        'database_mode':        database.get('mode', ''),
        'database_source':      database.get('source', ''),
        'database_env_override': database.get('env_override', False),
    }


def _resolved_backend_url():
    configured = str(get_all_config().get('backend_url', '')).strip()
    if configured and not re.match(r'^https?://(localhost|127\.0\.0\.1)(:\d+)?/?$', configured, flags=re.IGNORECASE):
        return configured.rstrip('/')
    try:
        return request.host_url.rstrip('/')
    except Exception:
        return configured.rstrip('/')


def _is_public_http_url(url):
    value = str(url or '').strip()
    return bool(value) and not re.match(r'^https?://(localhost|127\.0\.0\.1)(:\d+)?/?$', value, flags=re.IGNORECASE)


def sync_telegram_webhooks():
    base_url = _resolved_backend_url()
    if not _is_public_http_url(base_url):
        return

    bots = PlatformBot.query.filter_by(platform='telegram', is_active=True).order_by(PlatformBot.id.asc()).all()
    seen_tokens = set()
    for bot in bots:
        token = str(bot.bot_token or '').strip()
        token = decrypt_secret_text(token)
        if not token or token in seen_tokens:
            continue
        seen_tokens.add(token)
        webhook_url = f"{base_url}/api/platforms/webhook/telegram/{bot.id}"
        try:
            requests.post(
                f'https://api.telegram.org/bot{token}/setWebhook',
                json={'url': webhook_url},
                timeout=20,
            )
        except Exception:
            continue


def _bot_sync_secret():
    return os.getenv('BOT_SYNC_SECRET') or os.getenv('ADMIN_SECRET_KEY', '')


def _is_bot_sync_authorized():
    expected = _bot_sync_secret()
    provided = request.headers.get('X-Bot-Admin-Token', '')
    return bool(expected) and provided == expected


def _split_config_values(raw):
    return [item.strip() for item in str(raw or '').replace('\n', ',').split(',') if item.strip()]


def _build_admin_identity_candidates(data):
    username = str(data.get('username', '')).strip()
    user_id = str(data.get('user_id', '')).strip()
    platform = str(data.get('platform', '')).strip().lower()
    identity = str(data.get('identity', '')).strip()

    candidates = []
    if identity:
        candidates.append(identity)
    if username:
        candidates.append(username)
        if platform:
            candidates.append(f'{platform}:{username}')
    if platform and user_id:
        candidates.append(f'{platform}:{user_id}')
    elif user_id:
        candidates.append(user_id)

    unique = []
    seen = set()
    for item in candidates:
        normalized = item.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(normalized)
    return unique


def _get_admin_control_payload():
    config = get_all_config()
    return {
        'admin_identities': config.get('admin_identities', ''),
        'super_admin_identities': '\n'.join(get_super_admin_identities()),
        'platform_admin_roles': get_platform_admin_roles(),
        'admin_rules': config.get('admin_rules', '[]'),
        'silent_mode': config.get('silent_mode', 'false'),
        'silent_scopes': config.get('silent_scopes', '[]'),
        'banned_scopes': config.get('banned_scopes', '[]'),
        'ai_master_enabled': config.get('ai_master_enabled', 'true'),
    }


def _parse_json_object(raw, default=None):
    if default is None:
        default = {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or '{}')
        return parsed if isinstance(parsed, dict) else default
    except Exception:
        return default


def _serialize_platform_bot(bot):
    payload = bot.to_dict()
    extra = _parse_json_object(bot.extra_config_json)
    endpoint = extra.get('webhook_url') or extra.get('target_url', '')
    payload.update({
        'description': extra.get('description', ''),
        'target_url': endpoint,
        'webhook_url': endpoint,
        'notes': extra.get('notes', ''),
        'has_token': bool(bot.bot_token),
    })
    return payload


def _normalize_dev_slug(value, fallback='client'):
    slug = re.sub(r'[^a-zA-Z0-9\s_-]', '', str(value or '').strip().lower())
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug or fallback


def _unique_dev_slug(base_slug, current_id=None, field_name='slug'):
    base_slug = _normalize_dev_slug(base_slug)
    candidate = base_slug
    index = 2
    while True:
        query = DeveloperApiClient.query.filter(getattr(DeveloperApiClient, field_name) == candidate)
        if current_id:
            query = query.filter(DeveloperApiClient.id != current_id)
        if not query.first():
            return candidate
        candidate = f'{base_slug}-{index}'
        index += 1


def _parse_string_list(value):
    if isinstance(value, list):
        items = value
    else:
        items = str(value or '').split(',')
    cleaned = []
    seen = set()
    for item in items:
        normalized = str(item or '').strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned.append(normalized)
    return cleaned


def _serialize_developer_client(client):
    payload = client.to_dict()
    base = _resolved_backend_url()
    payload['webhook_url'] = f"{base}/api/chat/webhook/{client.webhook_slug}" if base else ''
    payload['api_endpoint'] = f"{base}/api/chat/client" if base else ''
    return payload


def _platform_token_config_key(platform):
    mapping = {
        'telegram': 'telegram_token',
        'discord': 'discord_token',
        'whatsapp': 'whatsapp_access_token',
        'instagram': 'instagram_access_token',
    }
    return mapping.get((platform or '').strip().lower(), '')


def _sync_platform_runtime_token(platform):
    platform = (platform or '').strip().lower()
    config_key = _platform_token_config_key(platform)
    if not config_key:
        return

    active_bot = PlatformBot.query.filter_by(platform=platform, is_active=True) \
        .order_by(PlatformBot.updated_at.desc(), PlatformBot.created_at.desc()) \
        .first()
    if active_bot and active_bot.bot_token:
        set_config(config_key, decrypt_secret_text(active_bot.bot_token))


def _upsert_primary_platform_bot(platform, token):
    platform = (platform or '').strip().lower()
    token = str(token or '').strip()
    if not platform or not token:
        return

    default_names = {
        'telegram': 'Telegram Primary',
        'discord': 'Discord Primary',
        'whatsapp': 'WhatsApp Primary',
        'instagram': 'Instagram Primary',
    }
    name = default_names.get(platform, f'{platform.title()} Primary')
    bot = PlatformBot.query.filter_by(platform=platform, name=name).first()
    if not bot:
        bot = PlatformBot(
            name=name,
            platform=platform,
            bot_token=encrypt_secret_text(token),
            extra_config_json='{}',
            is_active=True,
            status='active',
        )
        db.session.add(bot)
    else:
        bot.bot_token = encrypt_secret_text(token)
        bot.is_active = True
        bot.status = 'active'


def _extract_grounded_monitor_answer(message, source_rows):
    lowered = str(message or '').lower()
    if 'gdr' not in lowered:
        return None

    if 'readxhub' in lowered or 'snapcourse' in lowered:
        try:
            response = requests.get(
                'https://blogs.snapcourse.in/fetch_new_blogs.php?q=gdr&limit=5&offset=0',
                headers={'User-Agent': 'MNI/1.0'},
                timeout=15,
            )
            response.raise_for_status()
            rows = response.json() if response.text.strip() else []
            if isinstance(rows, list) and rows:
                first = rows[0]
                title = str(first.get('title', '')).strip()
                description = str(first.get('description', '')).strip()
                slug = str(first.get('slug', '')).strip()
                if title:
                    return (
                        f"ReadxHub/SnapCourse me GDR ka reference `{title}` se milta hai. "
                        f"Practical meaning: GDR ek chat-based knowledge retrieval feature hai jo `/gdr` command se structured information laata hai. "
                        f"Source: blogs.snapcourse.in article `{title}`"
                        f"{f' | slug: {slug}' if slug else ''}. {description}"
                    )
        except Exception:
            pass

    previews = '\n'.join(str(row.get('preview') or '') for row in source_rows if row.get('enabled'))
    title_match = re.search(r'GDR:\s*([^|.\n]+)', previews, flags=re.IGNORECASE)
    guide_match = re.search(r'/gdr command', previews, flags=re.IGNORECASE)
    if not title_match:
        return None

    expansion = title_match.group(1).strip()
    answer = f"ReadxHub/SnapCourse me GDR ka reference `{expansion}` ke liye use ho raha hai."
    if guide_match:
        answer += " Ye `/gdr` command ke through chat ke andar knowledge retrieval ke liye use hota hai."
    answer += " Source: Snapcourse blog results fetched from blogs.snapcourse.in."
    return answer


def _send_telegram_direct(chat_id, text):
    token = get_all_config().get('telegram_token', '')
    if not token or not chat_id or not text:
        return False, 'Telegram token, recipient, or text missing'
    res = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': str(chat_id), 'text': text},
        timeout=20,
    )
    if not res.ok:
        return False, res.text[:300]
    return True, 'sent'


def _send_telegram_audio_direct(chat_id, audio_bytes, filename='voice.mp3', caption=''):
    token = get_all_config().get('telegram_token', '')
    if not token or not chat_id or not audio_bytes:
        return False, 'Telegram token, recipient, or audio missing'
    files = {
        'audio': (filename, audio_bytes, 'audio/mpeg'),
    }
    data = {
        'chat_id': str(chat_id),
        'caption': caption or '',
    }
    res = requests.post(
        f'https://api.telegram.org/bot{token}/sendAudio',
        data=data,
        files=files,
        timeout=60,
    )
    if not res.ok:
        return False, res.text[:300]
    return True, 'sent'


def _send_discord_direct(channel_id, text):
    token = get_all_config().get('discord_token', '')
    if not token or not channel_id or not text:
        return False, 'Discord token, recipient, or text missing'
    res = requests.post(
        f'https://discord.com/api/v10/channels/{channel_id}/messages',
        json={'content': text},
        headers={'Authorization': f'Bot {token}', 'Content-Type': 'application/json'},
        timeout=20,
    )
    if not res.ok:
        return False, res.text[:300]
    return True, 'sent'


def _send_discord_audio_direct(channel_id, audio_bytes, filename='voice.mp3', caption=''):
    token = get_all_config().get('discord_token', '')
    if not token or not channel_id or not audio_bytes:
        return False, 'Discord token, recipient, or audio missing'
    res = requests.post(
        f'https://discord.com/api/v10/channels/{channel_id}/messages',
        data={'content': caption or ''},
        files={'files[0]': (filename, audio_bytes, 'audio/mpeg')},
        headers={'Authorization': f'Bot {token}'},
        timeout=60,
    )
    if not res.ok:
        return False, res.text[:300]
    return True, 'sent'


def _send_whatsapp_direct(recipient, text):
    config = get_all_config()
    token = config.get('whatsapp_access_token', '')
    phone_id = config.get('whatsapp_phone_number_id', '')
    if not token or not phone_id or not recipient or not text:
        return False, 'WhatsApp token, phone number id, recipient, or text missing'
    res = requests.post(
        f'https://graph.facebook.com/v18.0/{phone_id}/messages',
        json={
            'messaging_product': 'whatsapp',
            'to': str(recipient),
            'type': 'text',
            'text': {'body': text},
        },
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        timeout=20,
    )
    if not res.ok:
        return False, res.text[:300]
    return True, 'sent'


def _send_instagram_direct(recipient, text):
    token = get_all_config().get('instagram_access_token', '')
    if not token or not recipient or not text:
        return False, 'Instagram token, recipient, or text missing'
    res = requests.post(
        'https://graph.facebook.com/v18.0/me/messages',
        json={'recipient': {'id': str(recipient)}, 'message': {'text': text}},
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        timeout=20,
    )
    if not res.ok:
        return False, res.text[:300]
    return True, 'sent'


def _send_platform_message(platform, recipient, text):
    platform = (platform or '').strip().lower()
    if platform == 'telegram':
        return _send_telegram_direct(recipient, text)
    if platform == 'discord':
        return _send_discord_direct(recipient, text)
    if platform == 'whatsapp':
        return _send_whatsapp_direct(recipient, text)
    if platform == 'instagram':
        return _send_instagram_direct(recipient, text)
    return False, f'Unsupported platform: {platform}'


def _send_platform_voice_message(platform, recipient, text, gender='female', tone='soft'):
    audio_bytes, err = generate_voice(text, gender, tone)
    if not audio_bytes:
        return False, err or 'Voice generation failed'
    platform = (platform or '').strip().lower()
    if platform == 'telegram':
        return _send_telegram_audio_direct(recipient, audio_bytes, caption=text[:120])
    if platform == 'discord':
        return _send_discord_audio_direct(recipient, audio_bytes, caption=text[:180])
    return False, f'Voice outreach not supported for platform: {platform}'


def _telegram_api(method, payload=None):
    token = get_all_config().get('telegram_token', '')
    if not token:
        return False, 'Telegram token not configured'
    response = requests.post(
        f'https://api.telegram.org/bot{token}/{method}',
        json=payload or {},
        timeout=20,
    )
    try:
        data = response.json()
    except Exception:
        data = {'ok': False, 'description': response.text[:300]}
    if not response.ok or not data.get('ok'):
        return False, data.get('description') or response.text[:300]
    return True, data.get('result')


# ── Config endpoints ──────────────────────────────────────────────────

@admin_bp.route('/config', methods=['GET'])
@jwt_required()
def get_config():
    return jsonify(get_all_config())


@admin_bp.route('/config', methods=['POST'])
@jwt_required()
def update_config():
    data = request.get_json() or {}
    for key, value in data.items():
        set_config(key, str(value))
    return jsonify({'success': True, 'message': 'Config updated ✅'})


@admin_bp.route('/keli-core/config', methods=['GET'])
@jwt_required()
def get_keli_core_config():
    return jsonify(get_keli_core_meta())


@admin_bp.route('/keli-core/config', methods=['POST'])
@jwt_required()
def save_keli_core_config():
    data = request.get_json() or {}
    allowed = {
        'keli_core_enabled',
        'keli_core_name',
        'keli_core_text_provider',
        'keli_core_system_prompt',
        'keli_core_prefer_local_tools',
        'keli_core_tts_enabled',
        'keli_core_stt_enabled',
        'keli_core_voice_gender',
        'keli_core_voice_tone',
        'keli_core_voice_call_webhook',
        'keli_core_voice_call_secret',
    }
    for key, value in data.items():
        if key not in allowed:
            continue
        if isinstance(value, bool):
            value = 'true' if value else 'false'
        set_config(key, str(value))
    return jsonify({'success': True, 'message': 'MNI Core config saved ✅'})


@admin_bp.route('/keli-core/test', methods=['POST'])
@jwt_required()
def run_keli_core_test():
    data = request.get_json() or {}
    result = process_keli_request(
        data.get('message', ''),
        speak_response=bool(data.get('speak_response', False)),
        voice_gender=data.get('voice_gender'),
        voice_tone=data.get('voice_tone'),
        conversation_history=data.get('conversation_history') or [],
    )

    message_text = str(data.get('message', '')).strip()
    response_preview = str(result.get('response') or result.get('error') or '')[:500]
    db.session.add(Message(
        user_id='admin-panel',
        username='Admin',
        platform='admin',
        chat_id='keli-core',
        message_type=result.get('type', 'text'),
        content=message_text or '[empty]',
        response=response_preview,
        api_used=result.get('api_used', 'keli_core'),
        status='ok' if result.get('success') else 'error',
    ))
    db.session.commit()

    status = 200 if result.get('success') else 400
    return jsonify(result), status


@admin_bp.route('/keli-core/transcribe', methods=['POST'])
@jwt_required()
def transcribe_keli_core_audio():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Audio file is required'}), 400
    audio = request.files['file']
    result = transcribe_keli_audio(audio.read())
    return jsonify(result), (200 if result.get('success') else 400)


@admin_bp.route('/keli-core/clone-voice', methods=['POST'])
@jwt_required()
def clone_keli_core_voice():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Reference voice file is required'}), 400
    text = str(request.form.get('text', '')).strip()
    if not text:
        return jsonify({'success': False, 'error': 'Text is required'}), 400

    audio_bytes, err = clone_voice(text, request.files['file'].read())
    if not audio_bytes:
        return jsonify({'success': False, 'error': err or 'Voice cloning failed'}), 400

    encoded_audio = base64.b64encode(audio_bytes).decode('ascii')
    return jsonify({
        'success': True,
        'audio_base64': encoded_audio,
        'audio_mime': 'audio/wav',
        'message': 'Voice clone generated',
    })


# ── RAG / System Prompt ───────────────────────────────────────────────

@admin_bp.route('/rag', methods=['GET'])
@jwt_required()
def get_rag():
    row = BotConfig.query.filter_by(key='rag_prompt').first()
    return jsonify({'rag_prompt': row.value if row else ''})


@admin_bp.route('/rag', methods=['POST'])
@jwt_required()
def update_rag():
    data = request.get_json() or {}
    prompt = data.get('rag_prompt', '')
    set_config('rag_prompt', prompt)
    return jsonify({'success': True, 'message': 'RAG brain updated instantly 🧠'})


@admin_bp.route('/rag/upload', methods=['POST'])
@jwt_required()
def upload_rag():
    uploaded = request.files.get('file')
    if not uploaded:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400

    filename = (uploaded.filename or '').lower()
    if not filename.endswith(('.txt', '.md')):
        return jsonify({'success': False, 'error': 'Only .txt or .md files are allowed'}), 400

    try:
        content = uploaded.read().decode('utf-8')
    except UnicodeDecodeError:
        return jsonify({'success': False, 'error': 'File must be UTF-8 text'}), 400

    if not content.strip():
        return jsonify({'success': False, 'error': 'Uploaded file is empty'}), 400

    set_config('rag_prompt', content)
    return jsonify({
        'success': True,
        'message': 'RAG file uploaded and applied instantly 🧠',
        'rag_prompt': content,
    })


@admin_bp.route('/local-data', methods=['GET'])
@jwt_required()
def get_local_data_sources():
    ensure_local_data_sources()
    return jsonify([source.to_dict() for source in LocalDataSource.query.order_by(LocalDataSource.name.asc()).all()])


@admin_bp.route('/local-data/<int:source_id>/toggle', methods=['POST'])
@jwt_required()
def toggle_local_data_source(source_id):
    source = LocalDataSource.query.get_or_404(source_id)
    source.is_enabled = not source.is_enabled
    db.session.commit()
    return jsonify({'success': True, 'is_enabled': source.is_enabled})


@admin_bp.route('/local-data/debug', methods=['GET'])
@jwt_required()
def get_local_data_debug():
    message = str(request.args.get('message', '')).strip()
    sources = collect_local_data_debug(message)
    return jsonify({
        'message': message,
        'sources': sources,
        'summary': {
            'total': len(sources),
            'enabled': len([row for row in sources if row.get('enabled')]),
            'with_data': len([row for row in sources if row.get('preview')]),
        },
    })


@admin_bp.route('/blogs', methods=['GET'])
@jwt_required()
def get_blogs():
    posts = BlogPost.query.order_by(BlogPost.updated_at.desc()).all()
    return jsonify([post.to_dict() for post in posts])


@admin_bp.route('/blogs', methods=['POST'])
@jwt_required()
def create_blog():
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    if not title or not content:
        return jsonify({'success': False, 'error': 'Title and content are required'}), 400

    summary = (data.get('summary') or content[:220]).strip()
    slug = _slugify(data.get('slug') or title)
    seo_title, seo_description = _build_seo_payload(title, summary, content)

    if BlogPost.query.filter_by(slug=slug).first():
        slug = f"{slug}-{BlogPost.query.count() + 1}"

    post = BlogPost(
        title=title,
        slug=slug,
        summary=summary,
        content=content,
        seo_title=(data.get('seo_title') or seo_title).strip(),
        seo_description=(data.get('seo_description') or seo_description).strip(),
        tags=(data.get('tags') or '').strip(),
        status=(data.get('status') or 'draft').strip() or 'draft',
        is_enabled=bool(data.get('is_enabled', True)),
    )
    db.session.add(post)
    db.session.commit()
    return jsonify({'success': True, 'post': post.to_dict()})


@admin_bp.route('/blogs/<int:post_id>', methods=['PUT'])
@jwt_required()
def update_blog(post_id):
    post = BlogPost.query.get_or_404(post_id)
    data = request.get_json() or {}
    title = (data.get('title') or post.title).strip()
    content = (data.get('content') or post.content).strip()
    summary = (data.get('summary') or post.summary or content[:220]).strip()
    seo_title, seo_description = _build_seo_payload(title, summary, content)

    new_slug = _slugify(data.get('slug') or title)
    existing = BlogPost.query.filter(BlogPost.slug == new_slug, BlogPost.id != post_id).first()
    if existing:
        new_slug = f"{new_slug}-{post_id}"

    post.title = title
    post.slug = new_slug
    post.summary = summary
    post.content = content
    post.seo_title = (data.get('seo_title') or seo_title).strip()
    post.seo_description = (data.get('seo_description') or seo_description).strip()
    post.tags = (data.get('tags') or post.tags or '').strip()
    post.status = (data.get('status') or post.status).strip() or 'draft'
    post.is_enabled = bool(data.get('is_enabled', post.is_enabled))
    db.session.commit()
    return jsonify({'success': True, 'post': post.to_dict()})


@admin_bp.route('/blogs/<int:post_id>', methods=['DELETE'])
@jwt_required()
def delete_blog(post_id):
    post = BlogPost.query.get_or_404(post_id)
    db.session.delete(post)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/moderation-events', methods=['GET'])
@jwt_required()
def get_moderation_events():
    platform = str(request.args.get('platform', '')).strip().lower()
    query = ModerationEvent.query
    if platform:
        query = query.filter_by(platform=platform)
    events = query.order_by(ModerationEvent.created_at.desc()).limit(200).all()
    return jsonify([event.to_dict() for event in events])


@admin_bp.route('/moderation-events/<int:event_id>', methods=['DELETE'])
@jwt_required()
def delete_moderation_event(event_id):
    event = ModerationEvent.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/moderation-events-bot', methods=['POST'])
def create_moderation_event_bot():
    if not _is_bot_sync_authorized():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    data = request.get_json() or {}
    event = ModerationEvent(
        user_id=str(data.get('user_id', 'unknown')),
        username=str(data.get('username', 'unknown')),
        platform=str(data.get('platform', 'telegram')),
        chat_id=str(data.get('chat_id', '')),
        action=str(data.get('action', 'warn')),
        reason=str(data.get('reason', '')),
    )
    db.session.add(event)
    db.session.commit()
    return jsonify({'success': True, 'event': event.to_dict()})


# ── Bot Personalities (Multiple Bots) ─────────────────────────────────

@admin_bp.route('/personalities', methods=['GET'])
@jwt_required()
def get_personalities():
    personalities = BotPersonality.query.order_by(BotPersonality.name.asc()).all()
    return jsonify([p.to_dict() for p in personalities])

@admin_bp.route('/personalities', methods=['POST'])
@jwt_required()
def create_personality():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    personality_text = (data.get('personality') or data.get('description') or '').strip()
    if not name or not personality_text:
        return jsonify({'success': False, 'error': 'Name and personality text are required'}), 400

    new_personality = BotPersonality(
        name=name,
        personality=personality_text,
        tone=(data.get('tone') or data.get('role') or '').strip(),
        rag_prompt=data.get('rag_prompt', '')
    )
    db.session.add(new_personality)
    db.session.commit()
    return jsonify({'success': True, 'personality': new_personality.to_dict()}), 201

@admin_bp.route('/personalities/<int:p_id>', methods=['PUT'])
@jwt_required()
def update_personality(p_id):
    p = BotPersonality.query.get_or_404(p_id)
    data = request.get_json() or {}
    p.name = (data.get('name') or p.name).strip()
    p.personality = (data.get('personality') or p.personality).strip()
    p.tone = data.get('tone', p.tone)
    p.rag_prompt = data.get('rag_prompt', p.rag_prompt)
    db.session.commit()
    return jsonify({'success': True, 'personality': p.to_dict()})

@admin_bp.route('/personalities/<int:p_id>', methods=['DELETE'])
@jwt_required()
def delete_personality(p_id):
    p = BotPersonality.query.get_or_404(p_id)
    if p.is_active:
        return jsonify({'success': False, 'error': 'Cannot delete an active personality'}), 400
    db.session.delete(p)
    db.session.commit()
    return jsonify({'success': True})

@admin_bp.route('/personalities/<int:p_id>/activate', methods=['POST'])
@jwt_required()
def activate_personality(p_id):
    p_to_activate = BotPersonality.query.get_or_404(p_id)

    BotPersonality.query.update({'is_active': False})
    p_to_activate.is_active = True

    set_config('personality', p_to_activate.personality)
    set_config('tone', p_to_activate.tone)
    set_config('rag_prompt', p_to_activate.rag_prompt)

    db.session.commit()
    return jsonify({'success': True, 'message': f"Personality '{p_to_activate.name}' is now active."})


# ── Platform toggles ──────────────────────────────────────────────────

@admin_bp.route('/platforms', methods=['GET'])
@jwt_required()
def get_platforms():
    config = get_all_config()
    return jsonify({
        'telegram':  config.get('telegram_enabled',  'true'),
        'discord':   config.get('discord_enabled',   'true'),
        'whatsapp':  config.get('whatsapp_enabled',  'true'),
        'instagram': config.get('instagram_enabled', 'true'),
    })


@admin_bp.route('/platforms', methods=['POST'])
@jwt_required()
def update_platforms():
    data = request.get_json() or {}
    for platform in ['telegram', 'discord', 'whatsapp', 'instagram']:
        if platform in data:
            set_config(f'{platform}_enabled', 'true' if data[platform] else 'false')
    return jsonify({'success': True})


# ── Platform tokens (integration page) ────────────────────────────────

@admin_bp.route('/platform-tokens', methods=['GET'])
@jwt_required()
def get_platform_tokens():
    return jsonify(_get_platform_tokens_payload())


@admin_bp.route('/platform-tokens', methods=['POST'])
@jwt_required()
def save_platform_tokens():
    data = request.get_json() or {}
    fields = [
        'telegram_token', 'discord_token',
        'whatsapp_access_token', 'whatsapp_phone_number_id',
        'instagram_access_token', 'meta_verify_token', 'backend_url',
        'google_client_id', 'google_client_secret', 'google_refresh_token',
        'email_smtp_host', 'email_smtp_port', 'email_smtp_username', 'email_smtp_password', 'email_from',
        'n8n_url', 'n8n_api_key', 'n8n_webhook_url', 'n8n_trigger_keyword',
    ]
    for field in fields:
        if field in data:
            set_config(field, str(data[field]))
    if any(field in data for field in ('database_url', 'database_label', 'database_ssl_mode')):
        save_deployment_settings({
            'database_url': data.get('database_url', ''),
            'database_label': data.get('database_label', ''),
            'database_ssl_mode': data.get('database_ssl_mode', ''),
        })
    primary_tokens = {
        'telegram': str(data.get('telegram_token', '')).strip(),
        'discord': str(data.get('discord_token', '')).strip(),
        'whatsapp': str(data.get('whatsapp_access_token', '')).strip(),
        'instagram': str(data.get('instagram_access_token', '')).strip(),
    }
    for platform, token in primary_tokens.items():
        _upsert_primary_platform_bot(platform, token)
    db.session.commit()
    sync_telegram_webhooks()
    return jsonify({'success': True, 'message': 'Platform tokens saved ✅'})


# ── n8n automations ──────────────────────────────────────────────────

@admin_bp.route('/n8n/config', methods=['GET'])
@jwt_required()
def get_n8n_config():
    config = get_all_config()
    return jsonify({
        'n8n_url': config.get('n8n_url', ''),
        'n8n_api_key_set': bool(config.get('n8n_api_key', '')),
        'n8n_webhook_url': config.get('n8n_webhook_url', ''),
        'n8n_trigger_keyword': config.get('n8n_trigger_keyword', '/workflow'),
    })


@admin_bp.route('/n8n/config', methods=['POST'])
@jwt_required()
def save_n8n_config():
    data = request.get_json() or {}
    for field in ['n8n_url', 'n8n_webhook_url', 'n8n_trigger_keyword']:
        if field in data:
            set_config(field, str(data.get(field, '')).strip())
    api_key = str(data.get('n8n_api_key', '')).strip()
    if api_key:
        set_config('n8n_api_key', api_key)
    return jsonify({'success': True, 'message': 'n8n automation settings saved'})


@admin_bp.route('/n8n/workflows', methods=['GET'])
@jwt_required()
def get_n8n_workflows():
    config = get_all_config()
    try:
        return jsonify(list_workflows(config.get('n8n_url', ''), config.get('n8n_api_key', '')))
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 502


@admin_bp.route('/n8n/trigger', methods=['POST'])
@jwt_required()
def trigger_n8n_workflow():
    config = get_all_config()
    data = request.get_json() or {}
    webhook_url = str(data.get('webhook_url') or config.get('n8n_webhook_url', '')).strip()
    payload = data.get('payload') or {
        'source': 'mni_admin',
        'message': data.get('message', 'Test from MNI Automation Manager'),
        'keyword': config.get('n8n_trigger_keyword', '/workflow'),
    }
    try:
        return jsonify(trigger_webhook(webhook_url, payload))
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 502


# ── Bot token sync endpoint ───────────────────────────────────────────

@admin_bp.route('/platform-tokens-bot', methods=['GET'])
def get_platform_tokens_bot():
    """Protected endpoint for local bot processes to fetch tokens."""
    if not _is_bot_sync_authorized():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    return jsonify(_get_platform_tokens_payload())


@admin_bp.route('/platform-tokens-public', methods=['GET'])
def get_platform_tokens_public():
    """Legacy route kept for compatibility but no longer exposes secrets."""
    return jsonify({
        'backend_url': _resolved_backend_url(),
        'message': 'Use /api/admin/platform-tokens-bot with X-Bot-Admin-Token.',
    }), 410


# ── Feature toggles ───────────────────────────────────────────────────

@admin_bp.route('/features', methods=['GET'])
@jwt_required()
def get_features():
    config = get_all_config()
    return jsonify({
        'text':  config.get('text_enabled',  'true'),
        'image': config.get('image_enabled', 'true'),
        'video': config.get('video_enabled', 'true'),
        'voice': config.get('voice_enabled', 'true'),
    })


@admin_bp.route('/features', methods=['POST'])
@jwt_required()
def update_features():
    data = request.get_json() or {}
    for feat in ['text', 'image', 'video', 'voice']:
        if feat in data:
            set_config(f'{feat}_enabled', 'true' if data[feat] else 'false')
    return jsonify({'success': True})


# ── User management ───────────────────────────────────────────────────

@admin_bp.route('/users', methods=['GET'])
@jwt_required()
def get_users():
    try:
        page    = request.args.get('page', 1, type=int)
        per_page= 20
        q       = request.args.get('q', '')
        query   = User.query
        if q:
            query = query.filter(
                (User.username.ilike(f'%{q}%')) | (User.user_id.ilike(f'%{q}%'))
            )
        paginated = query.order_by(User.last_seen.desc()).paginate(page=page, per_page=per_page, error_out=False)
        return jsonify({
            'users': [u.to_dict() for u in paginated.items],
            'total': paginated.total,
            'pages': paginated.pages,
            'current_page': page,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@admin_bp.route('/users/<int:user_db_id>/block', methods=['POST'])
@jwt_required()
def block_user(user_db_id):
    data   = request.get_json() or {}
    user   = User.query.get_or_404(user_db_id)
    user.is_blocked   = True
    user.block_reason = data.get('reason', 'Blocked by admin')
    db.session.commit()
    return jsonify({'success': True, 'message': f'User {user.username} blocked 🚫'})


@admin_bp.route('/users/<int:user_db_id>/unblock', methods=['POST'])
@jwt_required()
def unblock_user(user_db_id):
    user = User.query.get_or_404(user_db_id)
    user.is_blocked   = False
    user.block_reason = ''
    db.session.commit()
    return jsonify({'success': True, 'message': f'User {user.username} unblocked ✅'})


# ── User Outreach ─────────────────────────────────────────────────────

@admin_bp.route('/outreach', methods=['GET'])
@jwt_required()
def get_outreach_messages():
    messages = OutreachMessage.query.order_by(OutreachMessage.created_at.desc()).all()
    return jsonify([m.to_dict() for m in messages])

@admin_bp.route('/outreach', methods=['POST'])
@jwt_required()
def create_outreach_message():
    data = request.get_json() or {}
    content = (data.get('message_content') or '').strip()
    platform = (data.get('target_platform') or 'all').strip()
    if not content:
        return jsonify({'success': False, 'error': 'Message content is required'}), 400

    msg = OutreachMessage(
        target_platform=platform,
        message_content=content,
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({'success': True, 'message': msg.to_dict()}), 201

@admin_bp.route('/outreach/<int:msg_id>/send', methods=['POST'])
@jwt_required()
def send_outreach_message(msg_id):
    msg = OutreachMessage.query.get_or_404(msg_id)
    if msg.status == 'sent':
        return jsonify({'success': False, 'error': 'This message has already been sent'}), 400

    # Run delivery in a background thread to avoid blocking the API response
    from flask import current_app
    app = current_app._get_current_object()

    def background_send(app_context, m_id):
        with app_context:
            result = deliver_outreach_message(m_id)
            print(f"[Outreach Job] {result.get('message')}")

    thread = threading.Thread(target=background_send, args=(app.app_context(), msg_id))
    thread.start()

    return jsonify({'success': True, 'message': 'Outreach delivery started in the background. 🚀'})


@admin_bp.route('/contacts', methods=['GET'])
@jwt_required()
def get_contacts():
    platform = request.args.get('platform', '').strip().lower()
    query = request.args.get('q', '').strip()
    return jsonify(list_known_contacts(platform=platform, query=query))


@admin_bp.route('/contacts/groups', methods=['GET'])
@jwt_required()
def get_contact_groups():
    platform = request.args.get('platform', '').strip().lower()
    query = request.args.get('q', '').strip()
    return jsonify(list_known_scopes(platform=platform, query=query))


@admin_bp.route('/contacts/groups/<platform>/<chat_id>/members', methods=['GET'])
@jwt_required()
def get_contact_group_members(platform, chat_id):
    query = request.args.get('q', '').strip()
    return jsonify(list_scope_members(platform, chat_id, query=query))


@admin_bp.route('/contacts/<platform>/<user_id>/messages', methods=['GET'])
@jwt_required()
def get_contact_messages(platform, user_id):
    rows = Message.query.filter_by(platform=platform, user_id=user_id).order_by(Message.timestamp.desc()).limit(100).all()
    return jsonify([row.to_dict() for row in rows])


@admin_bp.route('/outreach/direct', methods=['POST'])
@jwt_required()
def send_direct_outreach():
    data = request.get_json() or {}
    recipients = data.get('recipients') or []
    default_message = str(data.get('message', '')).strip()
    as_admin = bool(data.get('as_admin', False))
    message_type = str(data.get('message_type', 'text')).strip().lower()
    voice_gender = str(data.get('voice_gender', 'female')).strip().lower() or 'female'
    voice_tone = str(data.get('voice_tone', 'soft')).strip().lower() or 'soft'

    if not recipients:
        return jsonify({'success': False, 'error': 'At least one recipient is required'}), 400

    results = []
    for recipient in recipients:
        item_platform = str(recipient.get('platform') or '').strip().lower()
        user_id = str(recipient.get('user_id') or recipient.get('id') or '').strip()
        username = str(recipient.get('username') or user_id or 'unknown').strip()
        item_message = str(recipient.get('message') or default_message).strip()
        if not item_platform or not user_id or not item_message:
            results.append({
                'platform': item_platform,
                'user_id': user_id,
                'username': username,
                'success': False,
                'detail': 'Missing platform, user_id, or message',
            })
            continue
        recipient_kind = str(recipient.get('recipient_kind') or 'user').strip().lower() or 'user'
        if message_type == 'voice':
            ok, detail = _send_platform_voice_message(item_platform, user_id, item_message, voice_gender, voice_tone)
        else:
            ok, detail = send_platform_message(item_platform, user_id, item_message, recipient_kind=recipient_kind)
        results.append({
            'platform': item_platform,
            'user_id': user_id,
            'username': username,
            'success': ok,
            'detail': detail,
        })
        db.session.add(Message(
            user_id=user_id,
            username=username,
            platform=item_platform,
            message_type='voice' if message_type == 'voice' else 'text',
            content=f"[{'ADMIN DIRECT' if as_admin else 'AI OUTREACH'}] {item_message}",
            response=detail if ok else f"Failed: {detail}",
            api_used='admin_panel',
            status='ok' if ok else 'error',
        ))
    db.session.commit()
    return jsonify({'success': True, 'results': results})


# ── Message logs ──────────────────────────────────────────────────────

@admin_bp.route('/messages', methods=['GET'])
@jwt_required()
def get_messages():
    page     = request.args.get('page', 1, type=int)
    platform = request.args.get('platform', '')
    query    = Message.query
    if platform:
        query = query.filter_by(platform=platform)
    paginated = query.order_by(Message.timestamp.desc()).paginate(page=page, per_page=30, error_out=False)
    return jsonify({
        'messages': [m.to_dict() for m in paginated.items],
        'total':    paginated.total,
        'pages':    paginated.pages,
    })


@admin_bp.route('/control', methods=['GET'])
@jwt_required()
def get_admin_control():
    return jsonify(_get_admin_control_payload())


@admin_bp.route('/control', methods=['POST'])
@jwt_required()
def save_admin_control():
    data = request.get_json() or {}
    for key in ['admin_identities', 'super_admin_identities', 'silent_mode', 'ai_master_enabled']:
        if key in data:
            value = data[key]
            if isinstance(value, bool):
                value = 'true' if value else 'false'
            set_config(key, value)
    for key in ['admin_rules', 'silent_scopes', 'banned_scopes']:
        if key in data:
            value = data[key]
            if not isinstance(value, str):
                value = json.dumps(value)
            set_config(key, value)
    return jsonify({'success': True, 'message': 'Control settings saved'})


@admin_bp.route('/control/admin-identities/add', methods=['POST'])
@jwt_required()
def add_admin_identity():
    data = request.get_json() or {}
    values = _build_admin_identity_candidates(data)
    if not values:
        return jsonify({'success': False, 'error': 'identity, username, or user_id is required'}), 400

    current = _split_config_values(get_all_config().get('admin_identities', ''))
    current_lookup = {item.lower() for item in current}
    for item in values:
        if item and item.lower() not in current_lookup:
            current.append(item)
            current_lookup.add(item.lower())
    set_config('admin_identities', '\n'.join(current))
    return jsonify({'success': True, 'admin_identities': current})


@admin_bp.route('/control/admin-identities/remove', methods=['POST'])
@jwt_required()
def remove_admin_identity():
    data = request.get_json() or {}
    target = str(data.get('identity', '')).strip()
    if not target:
        return jsonify({'success': False, 'error': 'identity is required'}), 400

    current = _split_config_values(get_all_config().get('admin_identities', ''))
    updated = [item for item in current if item != target]
    set_config('admin_identities', '\n'.join(updated))
    return jsonify({'success': True, 'admin_identities': updated})


@admin_bp.route('/control/roles', methods=['GET'])
@jwt_required()
def get_admin_roles():
    return jsonify({
        'success': True,
        'super_admin_identities': get_super_admin_identities(),
        'platform_admin_roles': get_platform_admin_roles(),
    })


@admin_bp.route('/control/roles/grant', methods=['POST'])
@jwt_required()
def grant_admin_role():
    data = request.get_json() or {}
    identity = str(data.get('identity', '')).strip()
    role = str(data.get('role', 'platform_admin')).strip() or 'platform_admin'
    platforms = data.get('platforms') or ['all']
    permissions = data.get('permissions') or ['all']
    if not identity:
        return jsonify({'success': False, 'error': 'identity is required'}), 400
    roles = grant_platform_admin(identity, role=role, platforms=platforms, permissions=permissions)
    return jsonify({'success': True, 'platform_admin_roles': roles})


@admin_bp.route('/control/roles/revoke', methods=['POST'])
@jwt_required()
def revoke_admin_role():
    data = request.get_json() or {}
    identity = str(data.get('identity', '')).strip()
    if not identity:
        return jsonify({'success': False, 'error': 'identity is required'}), 400
    roles = revoke_platform_admin(identity)
    return jsonify({'success': True, 'platform_admin_roles': roles})


@admin_bp.route('/scopes', methods=['GET'])
@jwt_required()
def get_scopes():
    platform = request.args.get('platform', '').strip().lower()
    query = db.session.query(
        Message.platform,
        Message.chat_id,
        db.func.max(Message.username).label('last_username'),
        db.func.count(Message.id).label('message_count'),
        db.func.max(Message.timestamp).label('last_seen'),
    ).filter(Message.chat_id != '')
    if platform:
        query = query.filter(Message.platform == platform)
    rows = query.group_by(Message.platform, Message.chat_id).order_by(db.desc('last_seen')).all()
    return jsonify([
        {
            'platform': row[0],
            'chat_id': row[1],
            'chat_title': (
                f"Telegram Chat {row[1]}" if row[0] == 'telegram' else
                f"Discord Channel {row[1]}" if row[0] == 'discord' else
                (row[2] or f"Instagram DM {row[1]}") if row[0] == 'instagram' else
                (row[2] or f"{row[0].title()} Chat {row[1]}")
            ),
            'message_count': row[3],
            'last_seen': row[4].isoformat() if row[4] else None,
        }
        for row in rows
    ])


@admin_bp.route('/scopes/leave', methods=['POST'])
@jwt_required()
def leave_scope():
    data = request.get_json() or {}
    platform = str(data.get('platform', '')).strip().lower()
    chat_id = str(data.get('chat_id', '')).strip()
    if platform != 'telegram' or not chat_id:
        return jsonify({'success': False, 'error': 'Leave action abhi sirf Telegram groups ke liye supported hai. Discord/Instagram ke liye Block ya Remove use karo.'}), 400
    ok, result = _telegram_api('leaveChat', {'chat_id': chat_id})
    if not ok:
        return jsonify({'success': False, 'error': result}), 400
    return jsonify({'success': True, 'message': 'Bot left the group'})


@admin_bp.route('/scopes/remove', methods=['POST'])
@jwt_required()
def remove_scope():
    data = request.get_json() or {}
    platform = str(data.get('platform', '')).strip().lower()
    chat_id = str(data.get('chat_id', '')).strip()
    if not platform or not chat_id:
        return jsonify({'success': False, 'error': 'platform and chat_id are required'}), 400
    if platform == 'telegram':
        _telegram_api('leaveChat', {'chat_id': chat_id})
    Message.query.filter_by(platform=platform, chat_id=chat_id).delete()
    db.session.commit()
    return jsonify({'success': True, 'message': 'Scope removed from panel'})


@admin_bp.route('/scopes/invite', methods=['POST'])
@jwt_required()
def create_scope_invite():
    data = request.get_json() or {}
    platform = str(data.get('platform', '')).strip().lower()
    chat_id = str(data.get('chat_id', '')).strip()
    if platform != 'telegram' or not chat_id:
        return jsonify({'success': False, 'error': 'Only Telegram invite is supported right now'}), 400
    ok, result = _telegram_api('createChatInviteLink', {'chat_id': chat_id})
    if not ok:
        return jsonify({'success': False, 'error': result}), 400
    return jsonify({'success': True, 'invite_link': result.get('invite_link', '')})


@admin_bp.route('/scopes/admins', methods=['GET'])
@jwt_required()
def get_scope_admins():
    platform = str(request.args.get('platform', '')).strip().lower()
    chat_id = str(request.args.get('chat_id', '')).strip()
    if not chat_id:
        return jsonify({'success': False, 'error': 'chat_id is required'}), 400
    if platform == 'telegram':
        ok, result = _telegram_api('getChatAdministrators', {'chat_id': chat_id})
        if not ok:
            return jsonify({'success': False, 'error': result}), 400
        admins = []
        for item in result or []:
            user = item.get('user', {}) if isinstance(item, dict) else {}
            admins.append({
                'user_id': str(user.get('id', '')),
                'username': user.get('username', ''),
                'name': ' '.join(part for part in [user.get('first_name', ''), user.get('last_name', '')] if part).strip(),
                'status': item.get('status', ''),
                'is_bot': bool(user.get('is_bot', False)),
            })
        return jsonify({'success': True, 'admins': admins})

    if platform == 'discord':
        token = get_all_config().get('discord_token', '')
        if not token:
            return jsonify({'success': False, 'error': 'Discord token not configured'}), 400
        response = requests.get(
            f'https://discord.com/api/v10/channels/{chat_id}',
            headers={'Authorization': f'Bot {token}'},
            timeout=20,
        )
        if not response.ok:
            return jsonify({'success': False, 'error': response.text[:300]}), 400
        channel = response.json()
        admins = [{
            'user_id': str(channel.get('guild_id', '') or chat_id),
            'username': '',
            'name': channel.get('name') or 'Discord Channel',
            'status': f"type={channel.get('type', 'unknown')}",
            'is_bot': False,
        }]
        return jsonify({'success': True, 'admins': admins})

    if platform == 'instagram':
        latest = Message.query.filter_by(platform='instagram', chat_id=chat_id).order_by(Message.timestamp.desc()).first()
        admins = [{
            'user_id': chat_id,
            'username': latest.username if latest else '',
            'name': latest.username if latest and latest.username else f'Instagram DM {chat_id}',
            'status': 'direct_conversation',
            'is_bot': False,
        }]
        return jsonify({'success': True, 'admins': admins})

    return jsonify({'success': False, 'error': f'Admin info not supported for {platform}'}), 400


# ── API Key management ────────────────────────────────────────────────

@admin_bp.route('/apikeys', methods=['GET'])
@jwt_required()
def get_apikeys():
    keys = ApiKey.query.order_by(ApiKey.category, ApiKey.priority).all()
    return jsonify([k.to_dict() for k in keys])


@admin_bp.route('/apikeys', methods=['POST'])
@jwt_required()
def add_apikey():
    try:
        data = request.get_json() or {}
        category = data.get('category', 'text')
        provider = data.get('provider', '')
        normalized_provider = 'custom' if provider.lower() == 'custom' else _normalize_provider(provider, category)
        key  = ApiKey(
            name       = data.get('name', 'Unnamed'),
            category   = category,
            provider   = normalized_provider,
            api_key    = encrypt_secret_text(data.get('api_key', '').strip()),
            base_url   = data.get('base_url', '').strip(),
            is_primary = data.get('is_primary', False),
            priority   = data.get('priority', 1),
            is_active  = True,
            fail_count = 0,
        )
        db.session.add(key)
        db.session.commit()
        return jsonify({'success': True, 'message': 'API key added ✅'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400


@admin_bp.route('/apikeys/<int:key_id>', methods=['DELETE'])
@jwt_required()
def delete_apikey(key_id):
    key = ApiKey.query.get_or_404(key_id)
    db.session.delete(key)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/apikeys/<int:key_id>/toggle', methods=['POST'])
@jwt_required()
def toggle_apikey(key_id):
    key = ApiKey.query.get_or_404(key_id)
    key.is_active = not key.is_active
    if key.is_active:
        key.fail_count = 0
    db.session.commit()
    return jsonify({'success': True, 'is_active': key.is_active})


# ── Stats ─────────────────────────────────────────────────────────────

@admin_bp.route('/stats', methods=['GET'])
@jwt_required()
def get_stats():
    try:
        return jsonify({
            'total_users':    User.query.count(),
            'blocked_users':  User.query.filter_by(is_blocked=True).count(),
            'total_messages': Message.query.count(),
            'total_apikeys':  ApiKey.query.filter_by(is_active=True).count(),
            'by_platform': {
                'telegram':  Message.query.filter_by(platform='telegram').count(),
                'discord':   Message.query.filter_by(platform='discord').count(),
                'whatsapp':  Message.query.filter_by(platform='whatsapp').count(),
                'instagram': Message.query.filter_by(platform='instagram').count(),
            },
            'by_type': {
                'text':  Message.query.filter_by(message_type='text').count(),
                'image': Message.query.filter_by(message_type='image').count(),
                'voice': Message.query.filter_by(message_type='voice').count(),
                'video': Message.query.filter_by(message_type='video').count(),
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ── Platform Bots (Multiple Bots) ─────────────────────────────────────

@admin_bp.route('/platform-bots', methods=['GET'])
@jwt_required()
def get_platform_bots():
    if PlatformBot.query.count() == 0:
        config = get_all_config()
        defaults = [
            ('telegram', 'Telegram Primary', config.get('telegram_token', '')),
            ('discord', 'Discord Primary', config.get('discord_token', '')),
            ('whatsapp', 'WhatsApp Primary', config.get('whatsapp_access_token', '')),
            ('instagram', 'Instagram Primary', config.get('instagram_access_token', '')),
        ]
        changed = False
        for platform, name, token in defaults:
            if not token:
                continue
            db.session.add(PlatformBot(
                name=name,
                platform=platform,
                bot_token=encrypt_secret_text(token),
                extra_config_json='{}',
                is_active=True,
                status='active',
            ))
            changed = True
        if changed:
            db.session.commit()
    bots = PlatformBot.query.order_by(PlatformBot.created_at.desc()).all()
    return jsonify([_serialize_platform_bot(b) for b in bots])

@admin_bp.route('/platform-bots', methods=['POST'])
@jwt_required()
def create_platform_bot():
    data = request.get_json() or {}
    platform = str(data.get('platform', 'telegram')).strip().lower() or 'telegram'
    endpoint = str(data.get('webhook_url', data.get('target_url', ''))).strip()
    extra = {
        'description': str(data.get('description', '')).strip(),
        'target_url': endpoint,
        'webhook_url': endpoint,
        'notes': str(data.get('notes', '')).strip(),
    }
    bot = PlatformBot(
        name=data.get('name', 'New Bot'),
        platform=platform,
        bot_token=encrypt_secret_text(data.get('bot_token', '')),
        extra_config_json=json.dumps(extra),
        is_active=data.get('is_active', True)
    )
    db.session.add(bot)
    db.session.commit()
    if bot.is_active:
        bot.status = 'active'
        db.session.commit()
        _sync_platform_runtime_token(bot.platform)
    if bot.platform == 'telegram':
        sync_telegram_webhooks()
    return jsonify({'success': True, 'bot': _serialize_platform_bot(bot)})


@admin_bp.route('/platform-bots/<int:bot_id>', methods=['PUT'])
@jwt_required()
def update_platform_bot(bot_id):
    bot = PlatformBot.query.get_or_404(bot_id)
    data = request.get_json() or {}
    bot.name = str(data.get('name', bot.name)).strip() or bot.name
    bot.platform = str(data.get('platform', bot.platform)).strip() or bot.platform
    incoming_token = str(data.get('bot_token', '')).strip()
    if incoming_token:
        bot.bot_token = encrypt_secret_text(incoming_token)
    if 'is_active' in data:
        bot.is_active = bool(data.get('is_active'))
    extra = _parse_json_object(bot.extra_config_json)
    endpoint = str(data.get('webhook_url', data.get('target_url', extra.get('webhook_url', extra.get('target_url', ''))))).strip()
    extra.update({
        'description': str(data.get('description', extra.get('description', ''))).strip(),
        'target_url': endpoint,
        'webhook_url': endpoint,
        'notes': str(data.get('notes', extra.get('notes', ''))).strip(),
    })
    bot.extra_config_json = json.dumps(extra)
    if bot.is_active:
        bot.status = 'active'
    else:
        bot.status = 'inactive'
    db.session.commit()
    _sync_platform_runtime_token(bot.platform)
    if bot.platform == 'telegram':
        sync_telegram_webhooks()
    return jsonify({'success': True, 'bot': _serialize_platform_bot(bot)})


@admin_bp.route('/platform-bots/<int:bot_id>/toggle', methods=['POST'])
@jwt_required()
def toggle_platform_bot(bot_id):
    bot = PlatformBot.query.get_or_404(bot_id)
    bot.is_active = not bot.is_active
    bot.status = 'active' if bot.is_active else 'inactive'
    db.session.commit()
    _sync_platform_runtime_token(bot.platform)
    if bot.platform == 'telegram':
        sync_telegram_webhooks()
    return jsonify({'success': True, 'bot': _serialize_platform_bot(bot)})


@admin_bp.route('/platform-bots/<int:bot_id>', methods=['DELETE'])
@jwt_required()
def delete_platform_bot(bot_id):
    bot = PlatformBot.query.get_or_404(bot_id)
    platform = bot.platform
    db.session.delete(bot)
    db.session.commit()
    _sync_platform_runtime_token(platform)
    if platform == 'telegram':
        sync_telegram_webhooks()
    return jsonify({'success': True})


# ── Data Agents (Automation) ──────────────────────────────────────────

@admin_bp.route('/data-agents', methods=['GET'])
@jwt_required()
def get_data_agents():
    ensure_default_data_agents()
    agents = DataAgent.query.order_by(DataAgent.created_at.desc()).all()
    return jsonify([a.to_dict() for a in agents])

@admin_bp.route('/data-agents', methods=['POST'])
@jwt_required()
def create_data_agent():
    data = request.get_json() or {}
    agent = DataAgent(
        name=data.get('name', 'New Agent'),
        role=data.get('role', ''),
        source_url=data.get('source_url', ''),
        mode=data.get('mode', 'wait')
    )
    db.session.add(agent)
    db.session.commit()
    return jsonify({'success': True, 'agent': agent.to_dict()})

@admin_bp.route('/data-agents/<int:agent_id>', methods=['PUT'])
@jwt_required()
def update_data_agent(agent_id):
    agent = DataAgent.query.get_or_404(agent_id)
    data = request.get_json() or {}
    agent.name = data.get('name', agent.name)
    agent.role = data.get('role', agent.role)
    agent.source_url = data.get('source_url', agent.source_url)
    agent.mode = data.get('mode', agent.mode)
    db.session.commit()
    return jsonify({'success': True, 'agent': agent.to_dict()})

@admin_bp.route('/data-agents/<int:agent_id>', methods=['DELETE'])
@jwt_required()
def delete_data_agent(agent_id):
    agent = DataAgent.query.get_or_404(agent_id)
    db.session.delete(agent)
    db.session.commit()
    return jsonify({'success': True})

# ── Form Submissions & Emails (Outreach/Lead Gen) ─────────────────────

@admin_bp.route('/form-submissions', methods=['GET'])
@jwt_required()
def get_form_submissions():
    forms = FormSubmission.query.order_by(FormSubmission.created_at.desc()).all()
    return jsonify([f.to_dict() for f in forms])

@admin_bp.route('/email-logs', methods=['GET'])
@jwt_required()
def get_email_logs():
    logs = EmailLog.query.order_by(EmailLog.created_at.desc()).all()
    return jsonify([l.to_dict() for l in logs])


@admin_bp.route('/email/send-test', methods=['POST'])
@jwt_required()
def send_test_email():
    data = request.get_json() or {}
    recipients = data.get('recipients') or []
    if isinstance(recipients, str):
        recipients = [item.strip() for item in recipients.split(',') if item.strip()]
    subject = str(data.get('subject', '')).strip()
    body = str(data.get('body', '')).strip()
    if not recipients or not subject or not body:
        return jsonify({'success': False, 'error': 'Recipients, subject, and body are required'}), 400

    config = get_all_config()
    config.setdefault('email_sender_email', config.get('email_from', ''))
    try:
        result = send_email_via_smtp(config, subject, body, recipients)
        log = EmailLog(
            subject=subject,
            recipients=', '.join(recipients),
            body=body,
            status='sent',
            detail=json.dumps(result),
        )
        db.session.add(log)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Test email sent ✅', 'log': log.to_dict()})
    except Exception as exc:
        log = EmailLog(
            subject=subject,
            recipients=', '.join(recipients),
            body=body,
            status='failed',
            detail=str(exc),
        )
        db.session.add(log)
        db.session.commit()
        return jsonify({'success': False, 'error': str(exc), 'log': log.to_dict()}), 400


# ── Appointments (Booking Call Slots) ─────────────────────────────────

@admin_bp.route('/appointments', methods=['GET'])
@jwt_required()
def get_appointments():
    apps = AppointmentRequest.query.order_by(AppointmentRequest.scheduled_for.desc()).all()
    return jsonify([a.to_dict() for a in apps])


@admin_bp.route('/appointments/<int:appointment_id>/status', methods=['POST'])
@jwt_required()
def update_appointment_status(appointment_id):
    appointment = AppointmentRequest.query.get_or_404(appointment_id)
    data = request.get_json() or {}
    status = str(data.get('status', '')).strip().lower()
    allowed_statuses = {'pending', 'confirmed', 'approved', 'rejected', 'cancelled'}
    if status not in allowed_statuses:
        return jsonify({'success': False, 'error': 'Invalid appointment status'}), 400
    appointment.status = status
    db.session.commit()
    return jsonify({'success': True, 'appointment': appointment.to_dict()})


@admin_bp.route('/monitor/overview', methods=['GET'])
@jwt_required()
def get_monitor_overview():
    recent_messages = Message.query.order_by(Message.timestamp.desc()).limit(20).all()
    local_sources = LocalDataSource.query.order_by(LocalDataSource.name.asc()).all()
    agents = DataAgent.query.order_by(DataAgent.updated_at.desc()).all()
    api_keys = ApiKey.query.order_by(ApiKey.category.asc(), ApiKey.priority.asc()).all()
    monitor_messages = Message.query.filter_by(platform='admin_monitor').order_by(Message.timestamp.desc()).limit(20).all()
    developer_clients = DeveloperApiClient.query.order_by(DeveloperApiClient.updated_at.desc()).all()
    config = get_all_config()
    database = build_database_diagnostics()
    return jsonify({
        'api_keys': [key.to_dict() for key in api_keys],
        'local_sources': [source.to_dict() for source in local_sources],
        'data_agents': [agent.to_dict() for agent in agents],
        'developer_clients': [_serialize_developer_client(client) for client in developer_clients],
        'recent_messages': [message.to_dict() for message in recent_messages],
        'monitor_messages': [message.to_dict() for message in reversed(monitor_messages)],
        'credentials': {
            'backend_url': bool(config.get('backend_url', '')),
            'google_client_id': bool(config.get('google_client_id', '')),
            'google_client_secret': bool(config.get('google_client_secret', '')),
            'google_refresh_token': bool(config.get('google_refresh_token', '')),
            'email_smtp_host': bool(config.get('email_smtp_host', '')),
            'email_smtp_username': bool(config.get('email_smtp_username', '')),
            'email_smtp_password': bool(config.get('email_smtp_password', '')),
            'email_from': bool(config.get('email_from', '')),
            'voice_call_webhook': bool(config.get('keli_core_voice_call_webhook', '')),
            'voice_call_secret': bool(config.get('keli_core_voice_call_secret', '')),
        },
        'database': {
            'mode': database.get('mode', ''),
            'source': database.get('source', ''),
            'active_uri_masked': database.get('active_uri_masked', ''),
            'saved_database_url_masked': database.get('saved_database_url_masked', ''),
            'database_label': database.get('database_label', ''),
            'database_ssl_mode': database.get('database_ssl_mode', ''),
            'env_override': database.get('env_override', False),
            'restart_required_for_saved_changes': database.get('restart_required_for_saved_changes', True),
        },
    })


@admin_bp.route('/developer-clients', methods=['GET'])
@jwt_required()
def get_developer_clients():
    clients = DeveloperApiClient.query.order_by(DeveloperApiClient.updated_at.desc()).all()
    return jsonify([_serialize_developer_client(client) for client in clients])


@admin_bp.route('/developer-clients', methods=['POST'])
@jwt_required()
def create_developer_client():
    data = request.get_json() or {}
    name = str(data.get('name', '')).strip() or 'MNI Client'
    slug = _unique_dev_slug(data.get('slug') or name, field_name='slug')
    webhook_slug = _unique_dev_slug(data.get('webhook_slug') or slug, field_name='webhook_slug')
    allowed_features = _parse_string_list(data.get('allowed_features') or ['chat'])
    allowed_sources = _parse_string_list(data.get('allowed_sources') or [])
    plain_key = f"mni_live_{secrets.token_urlsafe(24)}"
    client = DeveloperApiClient(
        name=name,
        slug=slug,
        api_key=encrypt_secret_text(plain_key),
        webhook_slug=webhook_slug,
        allowed_features_json=json.dumps(allowed_features),
        allowed_sources_json=json.dumps(allowed_sources),
        is_active=bool(data.get('is_active', True)),
    )
    db.session.add(client)
    db.session.commit()
    payload = _serialize_developer_client(client)
    payload['plain_api_key'] = plain_key
    return jsonify({'success': True, 'client': payload})


@admin_bp.route('/developer-clients/<int:client_id>', methods=['PUT'])
@jwt_required()
def update_developer_client(client_id):
    client = DeveloperApiClient.query.get_or_404(client_id)
    data = request.get_json() or {}
    client.name = str(data.get('name', client.name)).strip() or client.name
    if 'slug' in data:
        client.slug = _unique_dev_slug(data.get('slug') or client.name, current_id=client.id, field_name='slug')
    if 'webhook_slug' in data:
        client.webhook_slug = _unique_dev_slug(data.get('webhook_slug') or client.slug, current_id=client.id, field_name='webhook_slug')
    if 'allowed_features' in data:
        client.allowed_features_json = json.dumps(_parse_string_list(data.get('allowed_features')))
    if 'allowed_sources' in data:
        client.allowed_sources_json = json.dumps(_parse_string_list(data.get('allowed_sources')))
    if 'is_active' in data:
        client.is_active = bool(data.get('is_active'))
    db.session.commit()
    return jsonify({'success': True, 'client': _serialize_developer_client(client)})


@admin_bp.route('/developer-clients/<int:client_id>/toggle', methods=['POST'])
@jwt_required()
def toggle_developer_client(client_id):
    client = DeveloperApiClient.query.get_or_404(client_id)
    client.is_active = not client.is_active
    db.session.commit()
    return jsonify({'success': True, 'client': _serialize_developer_client(client)})


@admin_bp.route('/developer-clients/<int:client_id>/regenerate', methods=['POST'])
@jwt_required()
def regenerate_developer_client_key(client_id):
    client = DeveloperApiClient.query.get_or_404(client_id)
    plain_key = f"mni_live_{secrets.token_urlsafe(24)}"
    client.api_key = encrypt_secret_text(plain_key)
    db.session.commit()
    payload = _serialize_developer_client(client)
    payload['plain_api_key'] = plain_key
    return jsonify({'success': True, 'client': payload})


@admin_bp.route('/developer-clients/<int:client_id>', methods=['DELETE'])
@jwt_required()
def delete_developer_client(client_id):
    client = DeveloperApiClient.query.get_or_404(client_id)
    db.session.delete(client)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/monitor/chat', methods=['POST'])
@jwt_required()
def monitor_chat():
    data = request.get_json() or {}
    message = str(data.get('message', '')).strip()
    if not message:
        return jsonify({'success': False, 'error': 'message is required'}), 400

    conversation_history = data.get('history') or []
    if not isinstance(conversation_history, list):
        conversation_history = []

    source_rows = collect_local_data_debug(message)
    context_chunks = []
    for row in source_rows:
        preview = str(row.get('preview') or '').strip()
        if not preview or not row.get('enabled'):
            continue
        source_ref = row.get('fetched_from') or row.get('endpoint') or row.get('name')
        context_chunks.append(f"[{row.get('name')}] source={source_ref}\n{preview}")

    direct_answer = _extract_grounded_monitor_answer(message, source_rows)
    if direct_answer:
        response_text = direct_answer
        api_used = 'grounded_monitor'
    else:
        response_text, api_used = get_ai_response(
            message,
            get_all_config_values(),
            conversation_history=conversation_history,
        )

    log = Message(
        user_id='admin',
        username='Admin Monitor',
        platform='admin_monitor',
        message_type='text',
        content=message,
        response=(response_text or '')[:500],
        api_used=api_used,
        status='ok',
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({
        'success': True,
        'intent': 'monitor_chat',
        'type': 'text',
        'response': response_text,
        'api_used': api_used,
        'sources': source_rows,
    })


@admin_bp.route('/data-agents/bulk-assign', methods=['POST'])
@jwt_required()
def bulk_assign_data_agents():
    data = request.get_json() or {}
    agent_ids = data.get('agent_ids') or []
    if not isinstance(agent_ids, list):
        return jsonify({'success': False, 'error': 'agent_ids must be a list'}), 400

    source_url = str(data.get('source_url', '')).strip()
    mode = str(data.get('mode', '')).strip().lower()
    if not source_url:
        return jsonify({'success': False, 'error': 'source_url is required'}), 400

    updated = []
    for raw_id in agent_ids:
        try:
            agent_id = int(raw_id)
        except Exception:
            continue
        agent = DataAgent.query.get(agent_id)
        if not agent:
            continue
        agent.source_url = source_url
        if mode in {'wait', 'active', 'inactive'}:
            agent.mode = mode
        updated.append(agent.id)

    db.session.commit()
    return jsonify({'success': True, 'updated_agent_ids': updated, 'source_url': source_url, 'mode': mode})

@admin_bp.route('/availability-rules', methods=['GET'])
@jwt_required()
def get_availability_rules():
    rules = AvailabilityRule.query.order_by(AvailabilityRule.weekday.asc()).all()
    return jsonify([r.to_dict() for r in rules])


# ── MNI System API Key ───────────────────────────────────────────────────

@admin_bp.route('/system-api-key', methods=['GET'])
@jwt_required()
def get_system_api_key():
    config = get_all_config()
    return jsonify({'api_key': config.get('mikasha_system_api_key', '')})

@admin_bp.route('/system-api-key', methods=['POST'])
@jwt_required()
def generate_system_api_key():
    new_key = f"mikasha-{secrets.token_hex(16)}"
    set_config('mikasha_system_api_key', new_key)
    return jsonify({'success': True, 'api_key': new_key, 'message': 'New system API key generated. You can now use this to call MNI externally.'})
