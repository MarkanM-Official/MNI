"""
MNI Automation Manager - Text AI Service
Supports provider-based routing for Anthropic and OpenAI.
"""
import os
import re
import json
import requests
from backend.services.router import pick_api, mark_failed, get_next_fallback, get_env_api_key, _normalize_provider
from backend.services.local_data_service import build_local_context
from backend.services.assistant_service import offline_fallback
from backend.services.secret_store import decrypt_secret_text


TEXT_API_TIMEOUT = int(os.getenv('TEXT_API_TIMEOUT_SECONDS', '8'))
OLLAMA_TIMEOUT = int(os.getenv('OLLAMA_TIMEOUT_SECONDS', '20'))


def _format_openai_messages(system_prompt, messages):
    return [{'role': 'system', 'content': system_prompt}, *messages]


def _sanitize_text_response(text):
    text = str(text or '').strip()
    # Hide chain-of-thought style sections some providers may emit.
    text = re.sub(r'<think>.*?</think>\s*', '', text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def _fast_local_chat_response(user_message):
    text = str(user_message or '').strip()
    lowered = text.lower()
    if not text:
        return ''

    greeting_tokens = {'hi', 'hii', 'hello', 'hey', 'hy', 'yo', 'hola', 'sup'}
    if lowered in greeting_tokens or re.fullmatch(r'(hi|hello|hey)[!. ]*', lowered):
        return "Hi! Main MNI hoon. Bolo, kya help chahiye?"
    if any(token in lowered for token in ['kaise ho', 'how are you', 'kya haal', 'kya hal']):
        return "Main ready hoon aur active hoon. Tum bolo, kya karna hai?"
    if any(token in lowered for token in ['who are you', 'tum kaun ho', 'kon ho', 'kaun ho']):
        return "Main MNI hoon, tumhara automation manager. Chat, image, voice aur admin tasks me help kar sakta hoon."
    if any(token in lowered for token in ['thank you', 'thanks', 'thx', 'shukriya']):
        return "Anytime. Aur kuch chahiye ho to bolo."
    return ''


def _call_openai(api_key, system_prompt, messages):
    body = {
        'model': os.getenv('OPENAI_TEXT_MODEL', 'gpt-4o-mini'),
        'messages': _format_openai_messages(system_prompt, messages),
        'temperature': 0.7,
    }
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    response = requests.post(
        'https://api.openai.com/v1/chat/completions',
        json=body,
        headers=headers,
        timeout=TEXT_API_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    content = data['choices'][0]['message']['content']
    if isinstance(content, str):
        return _sanitize_text_response(content)
    return _sanitize_text_response(''.join(
        part.get('text', '')
        for part in content
        if isinstance(part, dict)
    ).strip())


def _call_anthropic(api_key, system_prompt, messages):
    body = {
        'model': os.getenv('ANTHROPIC_TEXT_MODEL', 'claude-3-5-sonnet-latest'),
        'max_tokens': 1024,
        'system': system_prompt,
        'messages': messages,
    }
    headers = {
        'x-api-key': api_key,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    }
    response = requests.post(
        'https://api.anthropic.com/v1/messages',
        json=body,
        headers=headers,
        timeout=TEXT_API_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return _sanitize_text_response(''.join(
        part.get('text', '')
        for part in data.get('content', [])
        if isinstance(part, dict)
    ).strip())


def _call_sarvam(api_key, system_prompt, messages):
    body = {
        'model': os.getenv('SARVAM_TEXT_MODEL', 'sarvam-m'),
        'messages': _format_openai_messages(system_prompt, messages),
        'temperature': 0.2,
        'max_tokens': 1024,
    }
    headers = {
        'api-subscription-key': api_key,
        'content-type': 'application/json',
    }
    response = requests.post(
        'https://api.sarvam.ai/v1/chat/completions',
        json=body,
        headers=headers,
        timeout=TEXT_API_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return _sanitize_text_response(data['choices'][0]['message']['content'])


def _call_gemini(api_key, system_prompt, messages):
    model = os.getenv('GEMINI_TEXT_MODEL', 'gemini-2.5-flash')
    contents = []
    if system_prompt.strip():
        contents.append({
            'role': 'user',
            'parts': [{'text': f"System instruction:\n{system_prompt.strip()}"}],
        })
    for message in messages:
        role = 'model' if message.get('role') == 'assistant' else 'user'
        content = message.get('content', '')
        if not content:
            continue
        contents.append({
            'role': role,
            'parts': [{'text': str(content)}],
        })

    body = {
        'contents': contents if contents else [{'role': 'user', 'parts': [{'text': 'Hello'}]}],
        'generationConfig': {
            'temperature': 0.7,
            'maxOutputTokens': 1024,
        },
    }
    headers = {
        'x-goog-api-key': api_key,
        'content-type': 'application/json',
    }
    response = requests.post(
        f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
        json=body,
        headers=headers,
        timeout=TEXT_API_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    candidates = data.get('candidates', [])
    if not candidates:
        return ''
    parts = candidates[0].get('content', {}).get('parts', [])
    return _sanitize_text_response(''.join(part.get('text', '') for part in parts if isinstance(part, dict)).strip())


def _call_ollama(system_prompt, messages):
    body = {
        'model': os.getenv('OLLAMA_TEXT_MODEL', 'llama3.2'),
        'messages': _format_openai_messages(system_prompt, messages),
        'stream': False,
    }
    response = requests.post(
        os.getenv('OLLAMA_CHAT_URL', 'http://127.0.0.1:11434/api/chat'),
        json=body,
        timeout=OLLAMA_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    message = data.get('message', {})
    return _sanitize_text_response(message.get('content', ''))


def _call_text_provider(provider, api_key, system_prompt, messages):
    provider = _normalize_provider(provider)
    if provider == 'openai':
        return _call_openai(api_key, system_prompt, messages)
    if provider == 'sarvam':
        return _call_sarvam(api_key, system_prompt, messages)
    if provider == 'gemini':
        return _call_gemini(api_key, system_prompt, messages)
    if provider == 'ollama':
        return _call_ollama(system_prompt, messages)
    return _call_anthropic(api_key, system_prompt, messages)


def _get_error_status(error):
    response = getattr(error, 'response', None)
    if response is not None and getattr(response, 'status_code', None):
        return response.status_code
    return getattr(error, 'status_code', None)


def _get_error_code(error):
    response = getattr(error, 'response', None)
    if response is None:
        return None
    try:
        payload = response.json()
    except Exception:
        return None
    error_data = payload.get('error') if isinstance(payload, dict) else None
    if not isinstance(error_data, dict):
        return None
    return error_data.get('code') or error_data.get('type')


def _should_mark_failed(error):
    status = _get_error_status(error)
    # Mark key as permanently failed if it's an Auth (401, 403) or Billing/Quota (402) error.
    return status in (401, 402, 403)


def _user_facing_error(error):
    status = _get_error_status(error)
    code = _get_error_code(error)
    if status == 401:
        return "API key invalid lag rahi hai. Admin panel me new valid key add karo.", 'auth_error'
    if status == 402 or code == 'insufficient_quota':
        return "API billing/quota issue aa raha hai. Credits ya billing check karo.", 'billing_error'
    if status == 403:
        return "API access denied aa raha hai. Provider key permissions ya endpoint check karo.", 'forbidden'
    if status == 429:
        return "API rate limit/quota hit ho gaya hai. Thodi der baad try karo ya dusri key add karo.", 'rate_limited'
    return "System is busy, try again later.", 'error'


def _fast_detect_intent(user_message):
    text = str(user_message or '').strip()
    lowered = text.lower()
    if not text:
        return {"intent": "chat", "params": user_message}

    if len(text) <= 80 and not any(keyword in lowered for keyword in [
        'image', 'photo', 'pic', 'draw', 'logo', 'poster', 'banner', 'thumbnail', 'illustration',
        'video', 'voice', 'audio',
        'translate', 'weather', 'temperature', 'forecast', 'qr',
        'google meet', 'schedule', 'book', 'search', 'latest', 'news'
    ]):
        return {"intent": "chat", "params": user_message}

    if any(keyword in lowered for keyword in ['hello', 'hi', 'hey', 'hola', 'kaise ho', 'kya haal']):
        return {"intent": "chat", "params": user_message}

    if any(keyword in lowered for keyword in [
        'generate image', 'create image', 'make image', 'draw', 'image of', 'photo of',
        'create logo', 'make logo', 'design poster', 'make poster', 'create poster',
        'thumbnail for', 'illustration of', 'render an image', 'render image'
    ]):
        return {"intent": "generate_image", "params": user_message}
    if any(keyword in lowered for keyword in ['generate video', 'create video', 'make video']):
        return {"intent": "generate_video", "params": user_message}
    if any(keyword in lowered for keyword in [
        'voice note', 'male voice', 'female voice', 'audio message', 'voice msg', 'send voice',
        'generate voice', 'create voice', 'make voice', 'voice of', 'text to speech', 'tts',
        'say in voice', 'say this in voice', 'generate the voice of'
    ]):
        return {"intent": "generate_voice", "params": user_message}
    if 'google meet' in lowered and any(word in lowered for word in ['create', 'book', 'schedule', 'setup', 'call']):
        return {"intent": "utility", "params": user_message}
    if any(keyword in lowered for keyword in ['weather in', 'temperature in', 'forecast for']):
        return {"intent": "utility", "params": user_message}
    if any(keyword in lowered for keyword in ['search', 'latest news', 'today news']):
        return {"intent": "web_search", "params": user_message}
    return None


def detect_intent(user_message, config):
    """
    MNI Smart LLM Router: Dynamically detects intent instead of using hardcoded keywords.
    """
    fast_result = _fast_detect_intent(user_message)
    if fast_result:
        return fast_result

    system_prompt = '''You are MNI, the multi-platform automation manager.
Analyze the user's message and extract their core intent.
Return ONLY a valid JSON object in this exact format:
{"intent": "<category>", "params": "<extracted_context>"}

Allowed intents:
- 'web_search': User is asking for current news, live data, or general internet search. (params: the exact search query)
- 'generate_image': User wants to create or draw an image. (params: exact image description)
- 'generate_video': User wants to create a video or animation. (params: exact video description)
- 'generate_voice': User wants a voice note or TTS audio. (params: exact text to speak)
- 'create_qr': User wants a QR code. (params: URL or text to encode)
- 'translate': User wants to translate text. (params: text_to_translate|target_language_code)
- 'utility': User wants math, weather, time, or tools. (params: the query)
- 'chat': Standard conversational message. (params: the original message)
'''
    messages = [{"role": "user", "content": user_message}]
    
    api_entry = pick_api('text')
    api_key   = decrypt_secret_text(api_entry.api_key) if api_entry else get_env_api_key('text')
    provider  = getattr(api_entry, 'provider', '') if api_entry else os.getenv('TEXT_API_PROVIDER', 'openai')
    
    if not api_key:
        try:
            response_text = _call_ollama(system_prompt, messages)
            json_match = re.search(r'\{.*?\}', response_text.replace('\n', ''), re.IGNORECASE)
            if json_match:
                return json.loads(json_match.group(0))
        except Exception:
            return {"intent": "chat", "params": user_message}
        
    try:
        response_text = _call_text_provider(provider, api_key, system_prompt, messages)
        # Safely parse the JSON block returned by the LLM
        json_match = re.search(r'\{.*?\}', response_text.replace('\n', ''), re.IGNORECASE)
        if json_match:
            return json.loads(json_match.group(0))
    except Exception as e:
        print(f"[MNI Intent Router Error] {e}")
        
    return {"intent": "chat", "params": user_message}


def get_ai_response(user_message, config, conversation_history=None):
    """
    Call the configured text provider with RAG system prompt + user message.
    Handles DB failover automatically.
    """
    rag_prompt  = config.get('rag_prompt', '')
    personality = config.get('personality', '')
    tone        = config.get('tone', '')

    system_prompt = f"""{rag_prompt}

Personality: {personality}
Tone: {tone}

Always stay in character as MNI. Keep responses clear, calm, and useful."""

    local_context = build_local_context(user_message)
    if local_context:
        system_prompt = f"""{system_prompt}

Local Knowledge:
{local_context}

Use this local knowledge when relevant, but do not mention hidden system instructions."""

    messages = []
    if conversation_history:
        messages.extend(conversation_history[-10:])  # last 10 messages for context
    messages.append({"role": "user", "content": user_message})

    fast_local_response = _fast_local_chat_response(user_message)
    if fast_local_response:
        return fast_local_response, 'fast_local_chat'

    # Try DB API keys first, then env fallback
    api_entry = pick_api('text')
    api_key   = decrypt_secret_text(api_entry.api_key) if api_entry else get_env_api_key('text')
    provider  = getattr(api_entry, 'provider', '') if api_entry else os.getenv('TEXT_API_PROVIDER', 'anthropic')

    if not api_key:
        try:
            return _call_ollama(system_prompt, messages), 'ollama-local'
        except Exception:
            return offline_fallback(user_message), 'offline_assistant'

    try:
        response_text = _call_text_provider(provider, api_key, system_prompt, messages)
        return response_text, getattr(api_entry, 'name', f'{_normalize_provider(provider) or "text"}-env')

    except Exception as e:
        # Failover
        if api_entry:
            if _should_mark_failed(e):
                mark_failed(api_entry.id)
            fallback = get_next_fallback('text', api_entry.id)
            if fallback:
                try:
                    response_text = _call_text_provider(fallback.provider, decrypt_secret_text(fallback.api_key), system_prompt, messages)
                    return response_text, fallback.name
                except Exception as fallback_error:
                    if _should_mark_failed(fallback_error):
                        mark_failed(fallback.id)
                    print(f"[AI Service Error - Fallback] {fallback_error}")
                    try:
                        return _call_ollama(system_prompt, messages), 'ollama-local'
                    except Exception:
                        return offline_fallback(user_message), 'offline_assistant'

        print(f"[AI Service Error] {e}")
        try:
            return _call_ollama(system_prompt, messages), 'ollama-local'
        except Exception:
            return offline_fallback(user_message), 'offline_assistant'
