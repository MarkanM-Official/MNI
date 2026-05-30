"""
MNI Automation Manager - Chat Route
Processes messages through the full pipeline.
"""
import base64
import os
from datetime import datetime
from flask import Blueprint, request, jsonify
from backend.database import db
from backend.models.message import Message
from backend.models.developer_api_client import DeveloperApiClient
from backend.middleware.pipeline import run_pipeline, upsert_user, classify_request
from backend.services.ai_service import get_ai_response, detect_intent
from backend.services.assistant_service import handle_utility_request, offline_fallback, generate_qr_code, translate_text, perform_web_search
from backend.services.image_service import generate_image
from backend.services.voice_service import generate_voice, detect_voice_params, extract_spoken_text
from backend.services.video_service import generate_video
from backend.services.keli_core_service import answer_with_keli, get_keli_core_config as get_keli_core_runtime_config
from backend.services.local_data_service import collect_local_data_debug
from backend.services.secret_store import decrypt_secret_text
from backend.services.admin_ops_service import execute_admin_command
from backend.routes.platforms import _handle_admin_scope_command

chat_bp = Blueprint('chat', __name__)


def _detect_audio_mime(audio_bytes):
    if audio_bytes and audio_bytes[:4] == b'RIFF':
        return 'audio/wav'
    return 'audio/mpeg'


def _bot_log_secret():
    return os.getenv('BOT_SYNC_SECRET') or os.getenv('ADMIN_SECRET_KEY', '')


def _is_platform_log_authorized():
    expected = _bot_log_secret()
    provided = request.headers.get('X-Bot-Admin-Token', '')
    return bool(expected) and provided == expected


def _source_context_for_client(message, allowed_sources):
    allowed = {str(item or '').strip().lower() for item in (allowed_sources or []) if str(item or '').strip()}
    if not allowed:
        return ''
    chunks = []
    for row in collect_local_data_debug(message):
        row_key = str(row.get('key', '')).strip().lower()
        row_name = str(row.get('name', '')).strip().lower()
        if row_key not in allowed and row_name not in allowed:
            continue
        preview = str(row.get('preview', '')).strip()
        if not preview or not row.get('enabled'):
            continue
        source_ref = row.get('fetched_from') or row.get('endpoint') or row.get('name')
        chunks.append(f"[{row.get('name')}] source={source_ref}\n{preview}")
    return '\n\n'.join(chunks)


def _process_message_core(user_id, username, platform, chat_id, message, config, allowed_features=None, allowed_sources=None, pipeline_request_type='text'):
    allowed_features = {str(item or '').strip().lower() for item in (allowed_features or []) if str(item or '').strip()}
    response_text = ''
    api_used = ''
    resp_type = 'text'

    utility_result = handle_utility_request(message)
    if utility_result:
        response_text = utility_result['response']
        api_used = utility_result.get('api_used', 'utility')
        resp_type = utility_result.get('type', 'text')
        request_intent = 'utility'
        params = message
    else:
        try:
            intent_data = detect_intent(message, config)
        except Exception:
            intent_data = {'intent': 'chat', 'params': message}
        request_intent = intent_data.get('intent', 'chat')
        params = intent_data.get('params', message)
        resp_type = request_intent if request_intent in ['image', 'voice', 'video'] else 'text'
        if request_intent == 'chat':
            type_to_intent = {
                'image': 'generate_image',
                'voice': 'generate_voice',
                'video': 'generate_video',
            }
            request_intent = type_to_intent.get(pipeline_request_type, request_intent)

    feature_map = {
        'chat': 'chat',
        'web_search': 'chat',
        'translate': 'chat',
        'utility': 'chat',
        'create_qr': 'chat',
        'generate_image': 'image',
        'generate_voice': 'voice',
        'generate_video': 'video',
    }
    required_feature = feature_map.get(request_intent, 'chat')
    if allowed_features and required_feature not in allowed_features:
        response_text = f"This API key does not have {required_feature} access enabled."
        resp_type = 'text'
        api_used = 'feature_blocked'
    elif utility_result:
        pass
    elif request_intent == 'create_qr':
        response_text = generate_qr_code(params)
        api_used = 'local_qr_generator'
        resp_type = 'image'
    elif request_intent == 'generate_image':
        url, err = generate_image(params)
        if url:
            response_text = url
            api_used = 'image_api'
            resp_type = 'image'
        else:
            response_text = err or "Couldn't generate image right now 😅"
            resp_type = 'text'
    elif request_intent == 'generate_voice':
        gender, tone = detect_voice_params(message)
        spoken_text = extract_spoken_text(params or message) or "Hello from MNI."
        audio_bytes, err = generate_voice(spoken_text, gender, tone)
        if audio_bytes:
            encoded_audio = base64.b64encode(audio_bytes).decode('ascii')
            response_text = f"data:{_detect_audio_mime(audio_bytes)};base64,{encoded_audio}"
            api_used = 'voice_api'
            resp_type = 'voice'
        else:
            response_text = err or "Voice generation failed 😅"
            resp_type = 'text'
    elif request_intent == 'generate_video':
        url, err = generate_video(params)
        if url:
            response_text = url
            api_used = 'video_api'
            resp_type = 'video'
        else:
            response_text = err or "Couldn't generate video right now 🎬"
            resp_type = 'text'
    elif request_intent == 'translate':
        parts = params.split('|')
        text_to_translate = parts[0]
        target_lang = parts[1].strip() if len(parts) > 1 else 'en'
        response_text = translate_text(text_to_translate, target_lang)
        api_used = 'local_translator'
        resp_type = 'text'
    elif request_intent == 'web_search':
        search_results = perform_web_search(params)
        augmented_prompt = f"User asked: {params}\n\nHere is real-time web information I just found:\n{search_results}\n\nPlease provide a helpful conversational answer based on this."
        response_text, api_used = get_ai_response(augmented_prompt, config)
        resp_type = 'text'
    else:
        extra_context = _source_context_for_client(message, allowed_sources)
        if extra_context:
            grounded_message = (
                f"Question: {message}\n\n"
                "Use only the approved source context below when it is relevant. "
                "If the answer is not in those sources, answer normally without inventing citations."
            )
            response_text, api_used = answer_with_keli(
                grounded_message,
                get_keli_core_runtime_config(),
                conversation_history=[],
                extra_context=extra_context,
            )
        else:
            response_text, api_used = get_ai_response(message, config)
        if api_used in {'none', 'error', 'auth_error', 'billing_error', 'rate_limited', 'forbidden'}:
            response_text = offline_fallback(message)
            api_used = 'offline_assistant'

    log = Message(
        user_id=user_id,
        username=username,
        platform=platform,
        chat_id=chat_id,
        message_type=resp_type,
        content=message,
        response=response_text[:500],
        api_used=api_used,
        status='ok',
    )
    db.session.add(log)
    db.session.commit()

    return {
        'response': response_text,
        'type': resp_type,
        'api_used': api_used,
        'status': 'ok',
    }


def _lookup_developer_client(raw_key='', webhook_slug=''):
    plain = str(raw_key or '').strip()
    slug = str(webhook_slug or '').strip()
    if slug:
        client = DeveloperApiClient.query.filter_by(webhook_slug=slug, is_active=True).first()
        if client:
            return client
    if not plain:
        return None
    for client in DeveloperApiClient.query.filter_by(is_active=True).all():
        if decrypt_secret_text(client.api_key) == plain:
            return client
    return None


@chat_bp.route('/message', methods=['POST'])
def process_message():
    data     = request.get_json() or {}
    user_id  = str(data.get('user_id', 'unknown'))
    username = str(data.get('username', 'User'))
    platform = str(data.get('platform', 'telegram'))
    chat_id  = str(data.get('chat_id', ''))
    scope_type = str(data.get('scope_type', ''))
    message  = str(data.get('message', ''))

    if not message.strip():
        return jsonify({'response': 'Say something! 😏', 'type': 'text'})

    admin_command_response = execute_admin_command(platform, user_id, username, message)
    if admin_command_response is not None:
        return jsonify({'response': admin_command_response, 'type': 'text', 'status': 'ok', 'api_used': 'admin_command'})

    admin_scope_response = _handle_admin_scope_command(
        user_id,
        username,
        platform,
        chat_id,
        message,
        scope_type=scope_type,
    )
    if admin_scope_response:
        return jsonify({'response': admin_scope_response, 'type': 'text', 'status': 'ok', 'api_used': 'admin_scope'})

    result = run_pipeline(user_id, username, platform, message, chat_id=chat_id, scope_type=scope_type)
    if not result['allowed']:
        return jsonify({'response': result.get('reason', ''), 'type': 'text', 'status': result['status'], 'api_used': 'pipeline_block'})
    return jsonify(_process_message_core(
        user_id, username, platform, chat_id, message, result['config'],
        pipeline_request_type=result.get('request_type', 'text')
    ))


@chat_bp.route('/platform-log', methods=['POST'])
def log_platform_message():
    if not _is_platform_log_authorized():
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    data = request.get_json() or {}
    user_id = str(data.get('user_id', 'unknown')).strip()
    username = str(data.get('username', 'User')).strip() or 'User'
    platform = str(data.get('platform', '')).strip().lower()
    chat_id = str(data.get('chat_id', '')).strip()
    message = str(data.get('message', '')).strip()
    status = str(data.get('status', 'seen')).strip() or 'seen'
    api_used = str(data.get('api_used', 'platform_sync')).strip() or 'platform_sync'

    if not platform or not message:
        return jsonify({'success': False, 'error': 'platform and message are required'}), 400

    upsert_user(user_id, username, platform)
    db.session.add(Message(
        user_id=user_id,
        username=username,
        platform=platform,
        chat_id=chat_id,
        message_type=classify_request(message),
        content=message,
        response='',
        api_used=api_used,
        status=status,
    ))
    db.session.commit()
    return jsonify({'success': True})


@chat_bp.route('/client', methods=['POST'])
def process_client_message():
    data = request.get_json() or {}
    raw_key = (
        request.headers.get('X-API-Key', '')
        or request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        or data.get('api_key', '')
    )
    client = _lookup_developer_client(raw_key=raw_key)
    if not client:
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401

    message = str(data.get('message', '')).strip()
    if not message:
        return jsonify({'success': False, 'error': 'message is required'}), 400

    user_id = str(data.get('user_id', f'client-{client.slug}')).strip()
    username = str(data.get('username', client.name)).strip() or client.name
    platform = str(data.get('platform', f'developer_api:{client.slug}')).strip()
    chat_id = str(data.get('chat_id', client.slug)).strip()

    result = run_pipeline(user_id, username, platform, message, chat_id=chat_id)
    if not result['allowed']:
        return jsonify({'response': result['reason'], 'type': 'text', 'status': result['status']})

    client.last_used_at = datetime.utcnow()
    db.session.commit()
    response = _process_message_core(
        user_id,
        username,
        platform,
        chat_id,
        message,
        result['config'],
        allowed_features=client.allowed_features(),
        allowed_sources=client.allowed_sources(),
        pipeline_request_type=result.get('request_type', 'text'),
    )
    response['client'] = {
        'name': client.name,
        'slug': client.slug,
    }
    return jsonify(response)


@chat_bp.route('/webhook/<string:webhook_slug>', methods=['POST'])
def process_client_webhook(webhook_slug):
    data = request.get_json(silent=True) or {}
    client = _lookup_developer_client(webhook_slug=webhook_slug)
    if not client:
        return jsonify({'success': False, 'error': 'Webhook not found'}), 404
    message = str(data.get('message', '')).strip()
    if not message:
        return jsonify({'success': False, 'error': 'message is required'}), 400

    user_id = str(data.get('user_id', f'webhook-{client.slug}')).strip()
    username = str(data.get('username', client.name)).strip() or client.name
    platform = str(data.get('platform', f'webhook:{client.slug}')).strip()
    chat_id = str(data.get('chat_id', client.slug)).strip()

    result = run_pipeline(user_id, username, platform, message, chat_id=chat_id)
    if not result['allowed']:
        return jsonify({'response': result['reason'], 'type': 'text', 'status': result['status']})

    client.last_used_at = datetime.utcnow()
    db.session.commit()
    response = _process_message_core(
        user_id,
        username,
        platform,
        chat_id,
        message,
        result['config'],
        allowed_features=client.allowed_features(),
        allowed_sources=client.allowed_sources(),
        pipeline_request_type=result.get('request_type', 'text'),
    )
    response['client'] = {
        'name': client.name,
        'slug': client.slug,
    }
    return jsonify(response)
