"""
MNI Automation Manager - Platform Webhook Routes
Receives messages from Telegram, Discord, WhatsApp, Instagram
"""
import os
import re
import threading
import requests
import logging
import base64
import json
from flask import Blueprint, request, jsonify, current_app
from backend.database import db
from backend.models.config_model import BotConfig
from backend.models.platform_bot import PlatformBot
from backend.middleware.pipeline import run_pipeline
from backend.services.ai_service import get_ai_response, detect_intent
from backend.services.assistant_service import handle_utility_request, generate_qr_code, translate_text, perform_web_search
from backend.services.image_service import generate_image
from backend.services.runtime_config import get_config_value
from backend.services.voice_service import generate_voice, detect_voice_params, transcribe_audio, extract_spoken_text
from backend.services.video_service import generate_video
from backend.services.secret_store import decrypt_config_value, decrypt_secret_text, encrypt_config_value
from backend.services.admin_ops_service import execute_admin_command, get_actor_role, actor_has_permission

platforms_bp = Blueprint('platforms', __name__)


def get_config(key, default=''):
    return get_config_value(key, default)


def set_config(key, value):
    row = BotConfig.query.filter_by(key=key).first()
    stored = encrypt_config_value(key, value)
    if row:
        row.value = str(stored)
    else:
        db.session.add(BotConfig(key=key, value=str(stored)))
    db.session.commit()


def _split_config_values(raw):
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or '').strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except Exception:
        pass
    return [item.strip() for item in text.replace('\n', ',').split(',') if item.strip()]


def _load_json_list(key):
    raw = get_config(key, '[]')
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_json_list(key, values):
    set_config(key, json.dumps(values))


def _scope_key(platform, chat_id):
    return f"{str(platform or '').strip().lower()}:{str(chat_id or '').strip()}"


def _is_admin_sender(user_id, username, platform):
    actor = get_actor_role(user_id=user_id, username=username, platform=platform)
    return bool(actor.get('is_admin'))


def _normalize_admin_command_text(text):
    command = str(text or '').strip().lower()
    if not command:
        return ''
    command = re.sub(r'^<@!?[0-9]+>\s*', '', command)
    command = re.sub(r'^[@/][\w.-]+\s*', '', command)
    command = re.sub(r'^[,.:;!?\-]+\s*', '', command)
    command = re.sub(r'\s+', ' ', command).strip()
    return command


def _handle_admin_scope_command(user_id, username, platform, chat_id, text, scope_type='scope'):
    command = _normalize_admin_command_text(text)
    if command not in {'shutdown', 'arise'}:
        return None
    if str(scope_type or '').strip().lower() in {'dm', 'direct_message', 'private'}:
        if _is_admin_sender(user_id, username, platform):
            return "Ye scope command DM me nahi chalega. Jis group ya channel ko control karna hai, usi jagah `shutdown` ya `arise` bhejo."
        return "Ye admin scope command hai."
    if not _is_admin_sender(user_id, username, platform):
        return "Ye command sirf admin use kar sakta hai."
    if not chat_id:
        return "Scope missing hai, is platform/chat ko control nahi kar paaya."

    key = _scope_key(platform, chat_id)
    silent_scopes = _load_json_list('silent_scopes')

    if command == 'shutdown':
        if key not in silent_scopes:
            silent_scopes.append(key)
            _save_json_list('silent_scopes', silent_scopes)
        return f"{platform} scope shutdown ho gaya. Ab admin ke `arise` tak yahan AI reply nahi karega."

    updated = [item for item in silent_scopes if item != key]
    _save_json_list('silent_scopes', updated)
    return f"{platform} scope arise ho gaya. AI yahan phir se active hai."


def _decode_data_uri(data_uri):
    match = re.match(r'^data:([^;]+);base64,(.+)$', str(data_uri or ''), flags=re.I | re.S)
    if not match:
        return None, None
    mime = match.group(1).strip()
    payload = base64.b64decode(match.group(2))
    return mime, payload


def _is_image_payload(value):
    text = str(value or '')
    return text.startswith('data:image/') or re.match(r'^https?://\S+\.(png|jpe?g|gif|webp)(\?.*)?$', text, flags=re.I)


def _is_audio_payload(value):
    return str(value or '').startswith('data:audio/')


def _is_video_payload(value):
    text = str(value or '')
    return bool(re.match(r'^https?://\S+\.(mp4|mov|webm|mkv)(\?.*)?$', text, flags=re.I))


def _audio_extension(mime):
    mime = str(mime or '').lower()
    if 'wav' in mime:
        return 'wav'
    if 'ogg' in mime:
        return 'ogg'
    return 'mp3'


def send_telegram_message(chat_id, text, token):
    if not token or not chat_id or not text:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(url, json={'chat_id': chat_id, 'text': _sanitize_plain_text(text)}, timeout=10)
    response.raise_for_status()


def send_telegram_photo(chat_id, image_payload, token):
    if not token or not chat_id or not image_payload:
        return
    if str(image_payload).startswith('data:image/'):
        mime, binary = _decode_data_uri(image_payload)
        files = {'photo': ('image.' + (mime.split('/')[-1] if mime else 'png'), binary, mime or 'image/png')}
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={'chat_id': str(chat_id)},
            files=files,
            timeout=30,
        )
    else:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            json={'chat_id': str(chat_id), 'photo': str(image_payload)},
            timeout=20,
        )
    response.raise_for_status()


def send_telegram_audio(chat_id, audio_payload, token, caption=''):
    if not token or not chat_id or not audio_payload:
        return
    mime, binary = _decode_data_uri(audio_payload)
    if binary:
        ext = _audio_extension(mime)
        endpoint = 'sendVoice' if ext in {'ogg'} else 'sendAudio'
        field_name = 'voice' if endpoint == 'sendVoice' else 'audio'
        files = {
            field_name: (f'voice.{ext}', binary, mime or 'audio/mpeg')
        }
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{endpoint}",
            data={'chat_id': str(chat_id), 'caption': _sanitize_plain_text(caption or '')[:256]},
            files=files,
            timeout=60,
        )
    else:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendAudio",
            json={'chat_id': str(chat_id), 'audio': str(audio_payload), 'caption': _sanitize_plain_text(caption or '')[:256]},
            timeout=30,
        )
    response.raise_for_status()


def send_telegram_video(chat_id, video_payload, token, caption=''):
    if not token or not chat_id or not video_payload:
        return
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendVideo",
        json={'chat_id': str(chat_id), 'video': str(video_payload), 'caption': _sanitize_plain_text(caption or '')[:256]},
        timeout=60,
    )
    response.raise_for_status()


def send_discord_message(channel_id, text, token):
    if not token or not channel_id or not text:
        return
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    response = requests.post(url, json={'content': _sanitize_plain_text(text)}, headers=headers, timeout=10)
    response.raise_for_status()


def send_discord_photo(channel_id, image_payload, token):
    if not token or not channel_id or not image_payload:
        return
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}
    if str(image_payload).startswith('data:image/'):
        mime, binary = _decode_data_uri(image_payload)
        ext = (mime.split('/')[-1] if mime else 'png')
        files = {'files[0]': (f'image.{ext}', binary, mime or 'image/png')}
        response = requests.post(url, headers=headers, files=files, timeout=30)
    else:
        response = requests.post(url, json={'content': str(image_payload)}, headers={**headers, "Content-Type": "application/json"}, timeout=10)
    response.raise_for_status()


def send_discord_audio(channel_id, audio_payload, token, caption=''):
    if not token or not channel_id or not audio_payload:
        return
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}
    if str(audio_payload).startswith('data:audio/'):
        mime, binary = _decode_data_uri(audio_payload)
        ext = _audio_extension(mime)
        files = {'files[0]': (f'voice.{ext}', binary, mime or 'audio/mpeg')}
        data = {'content': _sanitize_plain_text(caption or '')[:1800]}
        response = requests.post(url, headers=headers, data=data, files=files, timeout=60)
    else:
        response = requests.post(
            url,
            json={'content': _sanitize_plain_text(caption or str(audio_payload))[:1800]},
            headers={**headers, "Content-Type": "application/json"},
            timeout=20,
        )
    response.raise_for_status()


def send_discord_video(channel_id, video_payload, token, caption=''):
    if not token or not channel_id or not video_payload:
        return
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    content = _sanitize_plain_text(caption or '')
    if content:
        content = f"{content}\n{video_payload}"
    else:
        content = str(video_payload)
    response = requests.post(url, json={'content': content[:1800]}, headers=headers, timeout=30)
    response.raise_for_status()


def _telegram_aliases(token_override=''):
    try:
        configured = get_config('telegram_bot_aliases', '').strip()
    except Exception:
        configured = ''
    raw = configured or os.getenv('TELEGRAM_BOT_ALIASES', 'mni,@mni,/mni,hey mni,mnibot,@mnibot,/mnibot')
    aliases = [item.strip().lower() for item in raw.replace('\n', ',').split(',') if item.strip()]
    token = (token_override or '').strip() or os.getenv('TELEGRAM_BOT_TOKEN', '')
    try:
        token = token or get_config('telegram_token', '')
    except Exception:
        pass
    if token:
        try:
            payload = requests.get(f'https://api.telegram.org/bot{token}/getMe', timeout=10).json()
            username = str(payload.get('result', {}).get('username', '')).strip().lower()
            if username:
                aliases.extend([username, f'@{username}', f'/{username}'])
        except Exception:
            pass
    return sorted(set(aliases))


def _telegram_should_respond(chat_type, text, token_override=''):
    if chat_type == 'private':
        return True
    lowered = str(text or '').strip().lower()
    if not lowered:
        return False
    return any(alias in lowered for alias in _telegram_aliases(token_override))


def _telegram_is_reply_to_bot(msg, token_override=''):
    reply_from = (msg or {}).get('reply_to_message', {}).get('from', {}) or {}
    if not reply_from or not reply_from.get('is_bot'):
        return False
    reply_username = str(reply_from.get('username', '')).strip().lower()
    if not reply_username:
        return False
    aliases = {alias.lstrip('@/').strip().lower() for alias in _telegram_aliases(token_override)}
    return reply_username in aliases


def _clean_telegram_text(text, token_override=''):
    cleaned = str(text or '')
    for alias in _telegram_aliases(token_override):
        cleaned = cleaned.replace(alias, ' ')
        cleaned = cleaned.replace(alias.capitalize(), ' ')
    cleaned = cleaned.replace('@', ' ').replace('/', ' ')
    cleaned = ' '.join(cleaned.split())
    return cleaned or str(text or '')


def _sanitize_plain_text(text):
    cleaned = str(text or '')
    cleaned = re.sub(r'[*_`#]+', '', cleaned)
    cleaned = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _download_telegram_file(token, file_id):
    file_meta = requests.get(
        f'https://api.telegram.org/bot{token}/getFile',
        params={'file_id': file_id},
        timeout=20,
    )
    file_meta.raise_for_status()
    file_path = file_meta.json().get('result', {}).get('file_path', '')
    if not file_path:
        return None
    file_resp = requests.get(f'https://api.telegram.org/file/bot{token}/{file_path}', timeout=60)
    file_resp.raise_for_status()
    return file_resp.content


def process_and_respond(user_id, username, platform, message, reply_fn, chat_id=None, scope_type=''):
    """Common processing for all platforms."""
    admin_command_response = execute_admin_command(platform, user_id, username, message)
    if admin_command_response is not None:
        reply_fn(admin_command_response)
        return

    admin_scope_response = _handle_admin_scope_command(user_id, username, platform, chat_id, message, scope_type=scope_type)
    if admin_scope_response:
        reply_fn(admin_scope_response)
        return

    # Pass chat_id to the pipeline so it can be used for logging and context
    result = run_pipeline(user_id, username, platform, message, chat_id=chat_id, scope_type=scope_type)
    if not result['allowed']:
        if str(result.get('reason', '')).strip():
            reply_fn(result['reason'])
        return

    config = result['config']
    pipeline_request_type = result.get('request_type', 'text')

    utility_result = handle_utility_request(message)
    if utility_result:
        reply_fn(utility_result.get('response', ''))
        return

    # ── MNI Smart Router ─────────────────────────────────────────────────
    try:
        intent_data = detect_intent(message, config)
    except Exception:
        intent_data = {'intent': 'chat', 'params': message}
    request_intent = intent_data.get('intent', 'chat')
    params = intent_data.get('params', message)

    # If the pipeline already classified this as non-text, trust that over a chat fallback.
    if request_intent == 'chat':
        type_to_intent = {
            'image': 'generate_image',
            'voice': 'generate_voice',
            'video': 'generate_video',
        }
        request_intent = type_to_intent.get(pipeline_request_type, request_intent)

    if request_intent == 'create_qr':
        url = generate_qr_code(params)
        reply_fn(f"Here is your QR Code:\n{url}")

    elif request_intent == 'translate':
        parts = params.split('|')
        reply_fn(translate_text(parts[0], parts[1].strip() if len(parts) > 1 else 'en'))

    elif request_intent == 'generate_image':
        url, err = generate_image(params or message)
        reply_fn(url if url else (err or "Image gen failed 😅"))

    elif request_intent == 'generate_voice':
        gender, tone = detect_voice_params(message)
        spoken_text = extract_spoken_text(params or message) or "Hello from MNI."
        audio_bytes, err = generate_voice(spoken_text, gender, tone)
        if audio_bytes:
            mime = 'audio/wav' if audio_bytes[:4] == b'RIFF' else 'audio/mpeg'
            encoded = base64.b64encode(audio_bytes).decode('ascii')
            reply_fn(f"data:{mime};base64,{encoded}")
        else:
            reply_fn(err or "Voice generation failed 😅")

    elif request_intent == 'generate_video':
        url, err = generate_video(params or message)
        reply_fn(url if url else (err or "Video generation failed 😅"))

    elif request_intent == 'web_search':
        search_results = perform_web_search(params)
        augmented_prompt = f"User asked: {params}\n\nHere is real-time web information I just found:\n{search_results}\n\nPlease provide a helpful conversational answer based on this."
        response_text, _ = get_ai_response(augmented_prompt, config)
        reply_fn(response_text)

    else:
        response, _ = get_ai_response(message, config)
        reply_fn(response)


def handle_telegram_message(msg, token_override='', bot_id=None, bot_name=''):
    if not msg:
        return {'ok': True}

    chat_id  = msg.get('chat', {}).get('id')
    chat_type = str(msg.get('chat', {}).get('type', 'private')).strip().lower()
    text     = msg.get('text', '')
    voice    = msg.get('voice') or {}
    from_u   = msg.get('from', {})
    user_id  = str(from_u.get('id', 'unknown'))
    username = from_u.get('username') or from_u.get('first_name', 'User')
    token    = (token_override or '').strip() or get_config('telegram_token') or os.getenv('TELEGRAM_BOT_TOKEN', '')

    if not text and voice and token:
        try:
            file_id = voice.get('file_id')
            audio_bytes = _download_telegram_file(token, file_id) if file_id else None
            if audio_bytes:
                transcript = transcribe_audio(audio_bytes)
                if transcript:
                    text = transcript.strip()
        except Exception:
            text = ''

    if not text or not chat_id:
        return {'ok': True}

    if not (_telegram_should_respond(chat_type, text, token) or _telegram_is_reply_to_bot(msg, token)):
        return {'ok': True, 'ignored': True}

    text = _clean_telegram_text(text, token)

    def reply(response_text):
        if _is_image_payload(response_text):
            send_telegram_photo(chat_id, response_text, token)
            return
        if _is_audio_payload(response_text):
            send_telegram_audio(chat_id, response_text, token, caption=text[:120])
            return
        if _is_video_payload(response_text):
            send_telegram_video(chat_id, response_text, token, caption=text[:120])
            return
        send_telegram_message(chat_id, response_text, token)

    scope_type = 'dm' if chat_type == 'private' else 'channel'
    process_and_respond(user_id, username, 'telegram', text, reply, chat_id=str(chat_id), scope_type=scope_type)
    return {'ok': True}


def queue_telegram_message_processing(msg, token_override='', bot_id=None, bot_name=''):
    app = current_app._get_current_object()

    def runner():
        try:
            with app.app_context():
                handle_telegram_message(msg, token_override=token_override, bot_id=bot_id, bot_name=bot_name)
        except Exception as exc:
            logging.error(f"[Telegram Webhook Worker:{bot_name or bot_id or 'default'}] {exc}")
    threading.Thread(target=runner, daemon=True).start()


def handle_discord_message_event(event, token_override='', bot_user_id=''):
    if not event:
        return {'ok': True}

    channel_id = str(event.get('channel_id', ''))
    content = str(event.get('content') or '').strip()
    author = event.get('author', {}) or {}
    user_id = str(author.get('id', 'unknown'))
    username = author.get('username', 'User')
    bot_flag = bool(author.get('bot', False))
    guild_id = str(event.get('guild_id', '') or '')
    token = (token_override or '').strip() or get_config('discord_token') or os.getenv('DISCORD_BOT_TOKEN', '')

    if bot_flag or not content or not channel_id:
        return {'ok': True, 'ignored': True}

    # In guilds, only respond when explicitly mentioned.
    if guild_id:
        mentions = event.get('mentions') or []
        mentioned_ids = {str(item.get('id', '')) for item in mentions if isinstance(item, dict)}
        if bot_user_id and str(bot_user_id) not in mentioned_ids:
            return {'ok': True, 'ignored': True}

    def reply(response_text):
        if _is_image_payload(response_text):
            send_discord_photo(channel_id, response_text, token)
            return
        if _is_audio_payload(response_text):
            send_discord_audio(channel_id, response_text, token, caption=content[:180])
            return
        if _is_video_payload(response_text):
            send_discord_video(channel_id, response_text, token, caption=content[:180])
            return
        send_discord_message(channel_id, response_text, token)

    scope_type = 'dm' if not guild_id else 'channel'
    process_and_respond(user_id, username, 'discord', content, reply, chat_id=channel_id, scope_type=scope_type)
    return {'ok': True}


# ── TELEGRAM ──────────────────────────────────────────────────────────

@platforms_bp.route('/webhook/telegram', methods=['POST'])
def telegram_webhook():
    data = request.get_json() or {}
    msg  = data.get('message') or data.get('edited_message', {})
    queue_telegram_message_processing(msg)
    return jsonify({'ok': True, 'queued': True})


@platforms_bp.route('/webhook/telegram/<int:bot_id>', methods=['POST'])
def telegram_webhook_for_bot(bot_id):
    bot = PlatformBot.query.get_or_404(bot_id)
    bot_token = decrypt_secret_text(bot.bot_token)
    if bot.platform != 'telegram' or not bot_token:
        return jsonify({'ok': False, 'error': 'Telegram bot token not configured'}), 400
    data = request.get_json() or {}
    msg = data.get('message') or data.get('edited_message', {})
    queue_telegram_message_processing(msg, token_override=bot_token, bot_id=bot.id, bot_name=bot.name)
    return jsonify({'ok': True, 'queued': True})


# ── DISCORD ───────────────────────────────────────────────────────────

@platforms_bp.route('/webhook/discord', methods=['POST'])
def discord_webhook():
    data = request.get_json() or {}

    # Discord sends a ping verification first
    if data.get('type') == 1:
        return jsonify({'type': 1})

    if data.get('type') == 2:  # slash command (future)
        return jsonify({'type': 4, 'data': {'content': 'Command received!'}})

    token = get_config('discord_token') or os.getenv('DISCORD_BOT_TOKEN', '')
    return jsonify(handle_discord_message_event(data, token_override=token))


# ── WHATSAPP ──────────────────────────────────────────────────────────

@platforms_bp.route('/webhook/whatsapp', methods=['GET'])
def whatsapp_verify():
    """Meta webhook verification handshake."""
    mode      = request.args.get('hub.mode')
    token     = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    verify_token = get_config('meta_verify_token', 'mni-verify-token')

    if mode == 'subscribe' and token == verify_token:
        return challenge, 200
    return 'Forbidden', 403


@platforms_bp.route('/webhook/whatsapp', methods=['POST'])
def whatsapp_webhook():
    data = request.get_json() or {}
    try:
        entry   = data['entry'][0]
        changes = entry['changes'][0]
        value   = changes['value']
        msgs    = value.get('messages', [])
        if not msgs:
            return jsonify({'ok': True})

        msg      = msgs[0]
        user_id  = msg.get('from', 'unknown')
        text     = msg.get('text', {}).get('body', '')
        contacts = value.get('contacts', [{}])
        username = contacts[0].get('profile', {}).get('name', 'User')
        wa_token = get_config('whatsapp_access_token') or os.getenv('WHATSAPP_ACCESS_TOKEN', '')
        phone_id = get_config('whatsapp_phone_number_id') or os.getenv('WHATSAPP_PHONE_NUMBER_ID', '')

        if not text:
            return jsonify({'ok': True})

        def reply(response_text):
            url     = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
            headers = {"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"}
            body    = {"messaging_product": "whatsapp", "to": user_id,
                       "type": "text", "text": {"body": response_text}}
            requests.post(url, json=body, headers=headers, timeout=10)

        process_and_respond(user_id, username, 'whatsapp', text, reply, chat_id=user_id, scope_type='dm') # For WA, user_id is the chat_id
    except (KeyError, IndexError):
        pass
    return jsonify({'ok': True})


# ── INSTAGRAM ─────────────────────────────────────────────────────────

@platforms_bp.route('/webhook/instagram', methods=['GET'])
def instagram_verify():
    mode      = request.args.get('hub.mode')
    token     = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    verify_token = get_config('meta_verify_token', 'mni-verify-token')

    if mode == 'subscribe' and token == verify_token:
        return challenge, 200
    return 'Forbidden', 403


@platforms_bp.route('/webhook/instagram', methods=['POST'])
def instagram_webhook():
    data = request.get_json() or {}
    try:
        entry    = data['entry'][0]
        msg_event= entry.get('messaging', [{}])[0]
        sender   = msg_event.get('sender', {})
        user_id  = str(sender.get('id', 'unknown'))
        text     = msg_event.get('message', {}).get('text', '')
        ig_token = get_config('instagram_access_token') or os.getenv('INSTAGRAM_ACCESS_TOKEN', '')

        if not text:
            return jsonify({'ok': True})

        def reply(response_text):
            url     = "https://graph.facebook.com/v18.0/me/messages"
            headers = {"Authorization": f"Bearer {ig_token}", "Content-Type": "application/json"}
            body    = {"recipient": {"id": user_id}, "message": {"text": response_text}}
            requests.post(url, json=body, headers=headers, timeout=10)

        process_and_respond(user_id, user_id, 'instagram', text, reply, chat_id=user_id, scope_type='dm') # For IG, user_id is the chat_id
    except (KeyError, IndexError):
        pass
    return jsonify({'ok': True})


# ── Health check ──────────────────────────────────────────────────────

@platforms_bp.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'bot': 'MNI'})
