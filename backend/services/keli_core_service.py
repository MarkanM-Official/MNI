"""
MNI Core orchestration layer for admin testing and multimodal actions.
"""
import base64
import json
import os
import re

import requests

from backend.models.config_model import ApiKey, BotConfig
from backend.services.ai_service import get_ai_response
from backend.services.assistant_service import handle_utility_request, offline_fallback, perform_web_search
from backend.services.image_service import generate_image
from backend.services.local_data_service import build_local_context
from backend.services.runtime_config import get_all_config_values
from backend.services.router import _normalize_provider
from backend.services.secret_store import decrypt_config_value, decrypt_secret_text
from backend.services.video_service import generate_video
from backend.services.voice_service import SARVAM_VOICES, generate_voice, transcribe_audio


DEFAULT_CONFIG = {
    'keli_core_enabled': 'true',
    'keli_core_name': 'MNI Core',
    'keli_core_text_provider': 'sarvam',
    'keli_core_system_prompt': (
        "You are MNI Core, a compact but powerful AI assistant inside the MNI admin panel. "
        "Prefer open-source or built-in tools when they can solve the task directly. "
        "When the task needs fresh data, telephony, image generation, video generation, or hosted speech services, "
        "use the configured APIs. Speak naturally in English, Hindi, or Hinglish depending on the user."
    ),
    'keli_core_prefer_local_tools': 'true',
    'keli_core_tts_enabled': 'true',
    'keli_core_stt_enabled': 'true',
    'keli_core_voice_gender': 'female',
    'keli_core_voice_tone': 'soft',
    'keli_core_voice_call_webhook': '',
    'keli_core_voice_call_secret': '',
}

VOICE_GENDERS = ['female', 'male']
VOICE_TONES = ['soft', 'energetic', 'formal', 'robotic']


def _sanitize_text_response(text):
    text = str(text or '').strip()
    text = re.sub(r'<think>.*?</think>\s*', '', text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def get_keli_core_config():
    rows = {key: value for key, value in get_all_config_values().items() if key.startswith('keli_core_')}
    merged = {**DEFAULT_CONFIG, **rows}
    merged['keli_core_enabled'] = _as_bool(merged.get('keli_core_enabled'), True)
    merged['keli_core_prefer_local_tools'] = _as_bool(merged.get('keli_core_prefer_local_tools'), True)
    merged['keli_core_tts_enabled'] = _as_bool(merged.get('keli_core_tts_enabled'), True)
    merged['keli_core_stt_enabled'] = _as_bool(merged.get('keli_core_stt_enabled'), True)
    return merged


def get_keli_core_meta():
    config = get_keli_core_config()
    return {
        'config': config,
        'voices': {
            'genders': VOICE_GENDERS,
            'tones': VOICE_TONES,
            'sarvam_speakers': sorted(SARVAM_VOICES.values()),
        },
        'providers': {
            'preferred_text': config.get('keli_core_text_provider', 'sarvam'),
            'sarvam_configured': bool(_get_text_api_entry('sarvam') or os.getenv('SARVAM_API_KEY')),
            'voice_call_connector': bool(config.get('keli_core_voice_call_webhook')),
        },
        'capabilities': [
            'Sarvam-first response generation',
            'Speech-to-text via Whisper when installed',
            'Text-to-speech with selectable voices',
            'Utility actions via local/open-source tools',
            'API fallback for search, image, video, and call connectors',
        ],
    }


def _get_text_api_entry(provider=None):
    query = ApiKey.query.filter_by(category='text', is_active=True)
    entries = query.order_by(ApiKey.is_primary.desc(), ApiKey.priority.asc()).all()
    if not provider:
        return entries[0] if entries else None
    provider = _normalize_provider(provider)
    for entry in entries:
        if _normalize_provider(entry.provider) == provider:
            return entry
    return None


def _get_text_provider_and_key(preferred='sarvam'):
    preferred = _normalize_provider(preferred)
    if preferred:
        preferred_entry = _get_text_api_entry(preferred)
        if preferred_entry:
            return preferred, decrypt_secret_text(preferred_entry.api_key), preferred_entry.name
        if preferred == 'sarvam' and os.getenv('SARVAM_API_KEY'):
            return 'sarvam', os.getenv('SARVAM_API_KEY'), 'sarvam-env'

    fallback = _get_text_api_entry()
    if fallback:
        return _normalize_provider(fallback.provider), decrypt_secret_text(fallback.api_key), fallback.name

    if os.getenv('SARVAM_API_KEY'):
        return 'sarvam', os.getenv('SARVAM_API_KEY'), 'sarvam-env'
    return '', '', 'none'


def _format_messages(system_prompt, messages):
    return [{'role': 'system', 'content': system_prompt}, *messages]


def _call_sarvam(system_prompt, messages, api_key):
    response = requests.post(
        'https://api.sarvam.ai/v1/chat/completions',
        json={
            'model': os.getenv('SARVAM_TEXT_MODEL', 'sarvam-m'),
            'messages': _format_messages(system_prompt, messages),
            'temperature': 0.2,
            'max_tokens': 1024,
        },
        headers={
            'api-subscription-key': api_key,
            'content-type': 'application/json',
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    return _sanitize_text_response(payload['choices'][0]['message']['content'])


def _heuristic_classify(message):
    lowered = (message or '').lower().strip()
    if any(token in lowered for token in ['call ', 'phone ', 'dial ']):
        return {'intent': 'voice_call', 'params': message, 'should_speak': False}
    if any(token in lowered for token in ['voice note', 'speak this', 'read this', 'say this', 'audio message']):
        return {'intent': 'generate_voice', 'params': message, 'should_speak': True}
    if any(token in lowered for token in ['draw ', 'generate image', 'create image', 'make poster']):
        return {'intent': 'generate_image', 'params': message, 'should_speak': False}
    if any(token in lowered for token in ['generate video', 'make video', 'create video']):
        return {'intent': 'generate_video', 'params': message, 'should_speak': False}
    if any(token in lowered for token in ['search the web', 'latest', 'news', 'search online']):
        return {'intent': 'web_search', 'params': message, 'should_speak': False}
    if any(token in lowered for token in ['translate ', 'qr code', 'google meet', 'weather', 'calc ', 'calculate ']):
        return {'intent': 'utility', 'params': message, 'should_speak': False}
    return {'intent': 'chat', 'params': message, 'should_speak': False}


def classify_request(message, config):
    provider, api_key, _ = _get_text_provider_and_key(config.get('keli_core_text_provider', 'sarvam'))
    if provider != 'sarvam' or not api_key:
        return _heuristic_classify(message)

    system_prompt = """
You are the MNI orchestration router.
Return only compact JSON.
Schema:
{"intent":"chat|utility|web_search|generate_image|generate_video|generate_voice|voice_call","params":"string","should_speak":true|false}
Rules:
- utility = things MNI can do with built-in or open-source tools like weather, translation, QR, calculations, Meet links.
- voice_call = user wants an actual outbound call or telephony-style action.
- generate_voice = user wants TTS/audio output.
- chat = normal answer generation.
"""
    try:
        raw = _call_sarvam(system_prompt, [{'role': 'user', 'content': message}], api_key)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            return {
                'intent': parsed.get('intent', 'chat'),
                'params': parsed.get('params', message),
                'should_speak': bool(parsed.get('should_speak', False)),
            }
    except Exception:
        pass
    return _heuristic_classify(message)


def _build_system_prompt(config, message=''):
    system_prompt = config.get('keli_core_system_prompt', DEFAULT_CONFIG['keli_core_system_prompt']).strip()
    local_context = build_local_context(message)
    if local_context:
        system_prompt = f"{system_prompt}\n\nLocal knowledge:\n{local_context}"
    return system_prompt


def answer_with_keli(message, config, conversation_history=None, extra_context=''):
    provider, api_key, provider_name = _get_text_provider_and_key(config.get('keli_core_text_provider', 'sarvam'))
    system_prompt = _build_system_prompt(config, message)
    if extra_context:
        system_prompt = f"{system_prompt}\n\nTool results:\n{extra_context.strip()}"

    messages = list(conversation_history or [])[-10:]
    messages.append({'role': 'user', 'content': message})

    if provider == 'sarvam' and api_key:
        try:
            return _call_sarvam(system_prompt, messages, api_key), provider_name
        except Exception:
            pass

    if not api_key:
        return offline_fallback(message), 'offline_assistant'

    response_text, api_used = get_ai_response(
        message,
        get_all_config_values(),
        conversation_history,
    )
    if api_used in {'none', 'error', 'auth_error', 'billing_error', 'rate_limited', 'forbidden'}:
        return offline_fallback(message), 'offline_assistant'
    return _sanitize_text_response(response_text), api_used


def _trigger_voice_call(message, config):
    webhook = config.get('keli_core_voice_call_webhook', '').strip()
    if not webhook:
        return False, 'Voice call connector configured nahi hai. Admin panel me webhook add karo.'

    headers = {'Content-Type': 'application/json'}
    secret = config.get('keli_core_voice_call_secret', '').strip()
    if secret:
        headers['X-Keli-Core-Secret'] = secret

    response = requests.post(
        webhook,
        json={'command': 'voice_call', 'message': message},
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except Exception:
        payload = {'message': response.text[:300]}
    return True, payload.get('message') or 'Voice call connector triggered.'


def _voice_payload(text, gender, tone):
    audio_bytes, err = generate_voice(text, gender, tone)
    if not audio_bytes:
        return None, err or 'Voice generation failed'
    audio_mime = 'audio/wav' if audio_bytes[:4] == b'RIFF' else 'audio/mpeg'
    return {
        'audio_base64': base64.b64encode(audio_bytes).decode('ascii'),
        'audio_mime': audio_mime,
        'voice': {'gender': gender, 'tone': tone},
    }, None


def process_keli_request(message, speak_response=False, voice_gender=None, voice_tone=None, conversation_history=None):
    config = get_keli_core_config()
    if not config.get('keli_core_enabled', True):
        return {'success': False, 'error': 'MNI Core is disabled in admin config.'}

    text = str(message or '').strip()
    if not text:
        return {'success': False, 'error': 'Message is required.'}

    gender = (voice_gender or config.get('keli_core_voice_gender') or 'female').strip().lower()
    tone = (voice_tone or config.get('keli_core_voice_tone') or 'soft').strip().lower()
    intent_data = classify_request(text, config)
    intent = intent_data.get('intent', 'chat')
    params = intent_data.get('params') or text

    if config.get('keli_core_prefer_local_tools', True):
        utility_result = handle_utility_request(text)
        if utility_result:
            result = {
                'success': True,
                'intent': 'utility',
                'type': utility_result.get('type', 'text'),
                'response': utility_result.get('response', ''),
                'api_used': utility_result.get('api_used', 'utility'),
            }
            if speak_response and result['type'] == 'text' and config.get('keli_core_tts_enabled', True):
                voice_payload, err = _voice_payload(result['response'], gender, tone)
                if voice_payload:
                    result.update(voice_payload)
                elif err:
                    result['voice_error'] = err
            return result

    if intent == 'voice_call':
        try:
            ok, response = _trigger_voice_call(params, config)
            return {'success': ok, 'intent': intent, 'type': 'text', 'response': response, 'api_used': 'voice_call_connector'}
        except Exception as exc:
            return {'success': False, 'intent': intent, 'type': 'text', 'response': f'Voice call trigger failed: {exc}', 'api_used': 'voice_call_connector'}

    if intent == 'generate_image':
        url, err = generate_image(params)
        if url:
            return {'success': True, 'intent': intent, 'type': 'image', 'response': url, 'api_used': 'image_api'}
        return {'success': False, 'intent': intent, 'type': 'text', 'response': err or 'Image generation failed.', 'api_used': 'image_api'}

    if intent == 'generate_video':
        url, err = generate_video(params)
        if url:
            return {'success': True, 'intent': intent, 'type': 'video', 'response': url, 'api_used': 'video_api'}
        return {'success': False, 'intent': intent, 'type': 'text', 'response': err or 'Video generation failed.', 'api_used': 'video_api'}

    if intent == 'generate_voice':
        voice_payload, err = _voice_payload(params, gender, tone)
        if voice_payload:
            return {
                'success': True,
                'intent': intent,
                'type': 'voice',
                'response': params,
                'api_used': 'voice_api',
                **voice_payload,
            }
        return {'success': False, 'intent': intent, 'type': 'text', 'response': err or 'Voice generation failed.', 'api_used': 'voice_api'}

    if intent == 'web_search':
        search_results = perform_web_search(params)
        answer, api_used = answer_with_keli(
            params,
            config,
            conversation_history=conversation_history,
            extra_context=search_results,
        )
        result = {'success': True, 'intent': intent, 'type': 'text', 'response': answer, 'api_used': api_used}
    else:
        answer, api_used = answer_with_keli(text, config, conversation_history=conversation_history)
        result = {'success': True, 'intent': intent, 'type': 'text', 'response': answer, 'api_used': api_used}

    if (speak_response or intent_data.get('should_speak')) and config.get('keli_core_tts_enabled', True):
        voice_payload, err = _voice_payload(result['response'], gender, tone)
        if voice_payload:
            result.update(voice_payload)
        elif err:
            result['voice_error'] = err

    return result


def transcribe_keli_audio(audio_bytes):
    config = get_keli_core_config()
    if not config.get('keli_core_stt_enabled', True):
        return {'success': False, 'error': 'Speech-to-text is disabled in MNI Core config.'}
    text = transcribe_audio(audio_bytes)
    if not text:
        return {'success': False, 'error': 'STT failed. Whisper dependency ya audio format check karo.'}
    return {'success': True, 'transcript': text.strip()}
