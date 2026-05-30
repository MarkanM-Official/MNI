"""
MNI Automation Manager - 6-Step Execution Pipeline
Every message goes through this before getting a response.
"""
from datetime import datetime
import json
import re
from sqlalchemy.exc import IntegrityError
from backend.database import db
from backend.models.user import User
from backend.models.message import Message
from backend.services.runtime_config import get_config_value


def get_config(key, default='true'):
    """Fetch a live config value from DB or environment."""
    return get_config_value(key, default)


def upsert_user(user_id, username, platform):
    """Create or update user record."""
    user = User.query.filter_by(user_id=user_id, platform=platform).first()
    if not user:
        user = User(user_id=user_id, username=username, platform=platform)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            user = User.query.filter_by(user_id=user_id, platform=platform).first()

    if user:
        user.username = username
        user.last_seen = datetime.utcnow()
        db.session.commit()
    return user


def classify_request(text):
    """Step 4: Detect request type from message text."""
    t = text.lower().strip()
    has_image = any(token in t for token in ['image', 'photo', 'pic', 'picture', 'logo', 'poster', 'banner', 'thumbnail', 'wallpaper', 'illustration', 'portrait'])
    if any(k in t for k in ['generate image', 'create image', 'make image', 'draw', 'image of', 'photo of', 'create logo', 'make logo', 'design poster']) or (
        has_image and re.search(r'\b(generate|genrate|genarate|create|make|draw|send|design|render)\b', t)
    ):
        return 'image'
    if any(k in t for k in ['generate video', 'create video', 'make video']):
        return 'video'
    if any(k in t for k in [
        'voice note', 'male voice', 'female voice', 'audio message', 'voice msg', 'send voice',
        'generate voice', 'create voice', 'make voice', 'voice of', 'text to speech', 'tts',
        'say in voice', 'say this in voice', 'generate the voice of'
    ]):
        return 'voice'
    return 'text'


def _load_scope_list(key):
    raw = get_config(key, '[]')
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _load_platform_list(key):
    raw = get_config(key, '[]')
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip().lower() for item in parsed if str(item).strip()]
    except Exception:
        pass
    return []


def _scope_matches(items, platform, chat_id):
    if not chat_id:
        return False
    platform = str(platform or '').strip().lower()
    chat_id = str(chat_id or '').strip()
    for item in items:
        if isinstance(item, dict):
            if str(item.get('platform', '')).strip().lower() == platform and str(item.get('chat_id', '')).strip() == chat_id:
                return True
        elif isinstance(item, str) and item.strip() == f'{platform}:{chat_id}':
            return True
    return False


def run_pipeline(user_id, username, platform, message_text, chat_id='', scope_type=''):
    """
    Full 6-step pipeline. Returns dict with:
      - allowed: bool
      - reason: str (if blocked/disabled)
      - request_type: str
      - user: User object
      - config: dict of live settings
    """

    # ── Step 1: Platform status ─────────────────────────────────────
    platform_key = f'{platform}_enabled'
    if get_config(platform_key, 'true').lower() != 'true':
        return {'allowed': False, 'reason': f'This platform is currently disabled.', 'status': 'disabled'}

    if str(platform or '').strip().lower() in _load_platform_list('silent_platforms'):
        return {'allowed': False, 'reason': '', 'status': 'silent'}

    if get_config('ai_master_enabled', 'true').lower() != 'true':
        return {'allowed': False, 'reason': 'AI is currently turned off by admin.', 'status': 'disabled'}

    if _scope_matches(_load_scope_list('banned_scopes'), platform, chat_id):
        return {'allowed': False, 'reason': 'This group is banned by admin.', 'status': 'blocked'}

    if get_config('dm_only_mode', 'false').lower() == 'true' and str(scope_type or '').strip().lower() not in {'dm', 'direct_message', 'private'}:
        return {'allowed': False, 'reason': '', 'status': 'silent'}

    if _scope_matches(_load_scope_list('silent_scopes'), platform, chat_id) or get_config('silent_mode', 'false').lower() == 'true':
        return {'allowed': False, 'reason': '', 'status': 'silent'}

    # ── Step 2: User block check ─────────────────────────────────────
    user = upsert_user(user_id, username, platform)
    if user.is_blocked:
        _log_message(user_id, username, platform, chat_id, 'text', message_text, 'You are blocked by admin.', '', 'blocked')
        return {'allowed': False, 'reason': 'You are blocked by admin.', 'status': 'blocked'}

    # ── Step 3: Load admin config ─────────────────────────────────────
    config = {
        'personality':   get_config('personality'),
        'rag_prompt':    get_config('rag_prompt'),
        'tone':          get_config('tone'),
        'api_usage':     get_config('api_usage', 'true').lower() == 'true',
        'text_enabled':  get_config('text_enabled',  'true').lower() == 'true',
        'image_enabled': get_config('image_enabled', 'true').lower() == 'true',
        'video_enabled': get_config('video_enabled', 'true').lower() == 'true',
        'voice_enabled': get_config('voice_enabled', 'true').lower() == 'true',
        'load_balancing':get_config('load_balancing', 'round_robin'),
    }

    # ── Step 4: Classify request ──────────────────────────────────────
    request_type = classify_request(message_text)

    # ── Step 5: Feature check ─────────────────────────────────────────
    feature_map = {
        'text':  config['text_enabled'],
        'image': config['image_enabled'],
        'video': config['video_enabled'],
        'voice': config['voice_enabled'],
    }
    if not feature_map.get(request_type, True):
        msg = f"Sorry, {request_type} generation is currently disabled 😅"
        _log_message(user_id, username, platform, chat_id, request_type, message_text, msg, '', 'disabled')
        return {'allowed': False, 'reason': msg, 'status': 'feature_disabled'}

    # ── Step 6: API check ─────────────────────────────────────────────
    if not config['api_usage']:
        msg = "I'm running in offline mode right now. Try again later!"
        return {'allowed': False, 'reason': msg, 'status': 'api_off'}

    return {
        'allowed':      True,
        'user':         user,
        'config':       config,
        'request_type': request_type,
        'status':       'ok',
    }


def _log_message(user_id, username, platform, chat_id, msg_type, content, response, api_used, status):
    log = Message(
        user_id=user_id, username=username, platform=platform,
        chat_id=str(chat_id or ''),
        message_type=msg_type, content=content,
        response=response, api_used=api_used, status=status
    )
    db.session.add(log)
    db.session.commit()
