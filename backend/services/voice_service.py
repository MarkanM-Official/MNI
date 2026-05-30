"""
MNI Automation Manager - Voice Generation Service (ElevenLabs)
"""
import os
import re
import base64
import shutil
import subprocess
import requests
import logging
import tempfile
from io import BytesIO
try:
    from gtts import gTTS
except Exception:
    gTTS = None
try:
    import whisper
except Exception:
    whisper = None
from backend.services.router import pick_api, mark_failed, get_env_api_key, get_next_fallback, _normalize_provider
from backend.services.secret_store import decrypt_secret_text


def detect_voice_params(text):
    """Detect gender + tone from user message."""
    t = text.lower()
    gender = 'female'
    tone   = 'soft'

    if 'male voice' in t or 'man voice' in t:
        gender = 'male'
    if 'energetic' in t or 'hype' in t:
        tone = 'energetic'
    elif 'formal' in t or 'professional' in t:
        tone = 'formal'
    elif 'robotic' in t or 'robot' in t:
        tone = 'robotic'

    return gender, tone


def extract_spoken_text(text):
    raw = str(text or '').strip()
    if not raw:
        return raw

    quoted = re.findall(r'["“](.+?)["”]', raw)
    if quoted:
        return quoted[0].strip()

    patterns = [
        r'^\s*(?:please\s+)?(?:generate|create|make|send)?\s*(?:me\s+)?(?:a\s+)?(?:voice\s*note|voice\s*message|voice|audio|tts|text\s+to\s+speech)\s*(?:of|for|saying|that\s+says|to\s+say)?\s*[:,-]?\s*(.+)$',
        r'^\s*(?:say|speak|read)\s*[:,-]?\s*(.+)$',
    ]
    lowered = raw.lower()
    for pattern in patterns:
        match = re.match(pattern, raw, flags=re.I)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate

    cleanup_prefixes = [
        'generate the voice of',
        'generate voice of',
        'generate a voice of',
        'generate voice saying',
        'generate a voice saying',
        'generate voice for',
        'create voice of',
        'create a voice of',
        'make voice of',
        'make a voice of',
        'voice note saying',
        'voice note of',
        'send voice saying',
        'send voice of',
        'text to speech',
        'tts',
    ]
    for prefix in cleanup_prefixes:
        if lowered.startswith(prefix):
            candidate = raw[len(prefix):].strip(" :,-")
            if candidate:
                return candidate

    return raw


# ElevenLabs voice IDs (you can change these in admin)
VOICE_IDS = {
    'female_soft':      'EXAVITQu4vr4xnSDxMaL',
    'female_energetic': 'ThT5KcBeYPX3keUQqHPh',
    'male_soft':        'TxGEqnHWrfWFTfGW9XjX',
    'male_formal':      'VR6AewLTigWG4xSOukaG',
    'male_robotic':     'AZnzlk1XvdvUeBnXmlld',
}

OPENAI_VOICES = {
    'female_soft': 'nova',
    'female_energetic': 'shimmer',
    'male_soft': 'ash',
    'male_formal': 'echo',
    'male_robotic': 'onyx',
}

SARVAM_VOICES = {
    'female_soft': 'shreya',
    'female_energetic': 'simran',
    'male_soft': 'shubh',
    'male_formal': 'amit',
    'male_robotic': 'soham',
}


def _guess_language_code(prompt):
    if re.search(r'[\u0900-\u097F]', prompt or ''):
        return 'hi-IN'
    return 'en-IN'


def _guess_local_tts_lang(prompt):
    if re.search(r'[\u0900-\u097F]', prompt or ''):
        return 'hi'
    return 'en'


def _resolve_piper_model(gender, tone):
    voice_key = _voice_key(gender, tone).upper()
    return (
        os.getenv(f'PIPER_MODEL_{voice_key}')
        or os.getenv('PIPER_MODEL_PATH')
        or ''
    ).strip()


def _voice_key(gender, tone):
    return f"{gender}_{tone}"


def _generate_voice_openai(prompt, gender, tone, api_key):
    voice = OPENAI_VOICES.get(_voice_key(gender, tone), OPENAI_VOICES['female_soft'])
    response = requests.post(
        'https://api.openai.com/v1/audio/speech',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json={
            'model': os.getenv('OPENAI_TTS_MODEL', 'gpt-4o-mini-tts'),
            'voice': voice,
            'input': prompt,
            'format': 'mp3',
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.content, None


def _generate_voice_sarvam(prompt, gender, tone, api_key):
    speaker = SARVAM_VOICES.get(_voice_key(gender, tone), SARVAM_VOICES['female_soft'])
    response = requests.post(
        os.getenv('SARVAM_TTS_URL', 'https://api.sarvam.ai/text-to-speech'),
        headers={
            'api-subscription-key': api_key,
            'Content-Type': 'application/json',
        },
        json={
            'text': prompt,
            'target_language_code': _guess_language_code(prompt),
            'speaker': speaker,
            'model': os.getenv('SARVAM_TTS_MODEL', 'bulbul:v3'),
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    audios = data.get('audios') or []
    if not audios:
        raise ValueError('No audio returned by Sarvam')
    return base64.b64decode(audios[0]), None


def _generate_voice_piper(prompt, gender, tone):
    piper_bin = os.getenv('PIPER_BIN', 'piper')
    if not shutil.which(piper_bin):
        return None, 'Piper binary not found'

    model_path = _resolve_piper_model(gender, tone)
    if not model_path or not os.path.exists(model_path):
        return None, 'Piper voice model not configured'

    output_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as out_file:
            output_path = out_file.name

        proc = subprocess.run(
            [piper_bin, '--model', model_path, '--output_file', output_path],
            input=(prompt or '').encode('utf-8'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        with open(output_path, 'rb') as f:
            return f.read(), None
    except Exception as e:
        logging.error(f"Piper TTS failed: {e}")
        return None, f'Piper TTS failed: {e}'
    finally:
        if output_path and os.path.exists(output_path):
            os.remove(output_path)


def clone_voice(prompt, sample_audio_bytes):
    server_url = os.getenv('XTTS_SERVER_URL', '').strip()
    if not server_url:
        return None, 'Voice cloning server not configured. Set XTTS_SERVER_URL.'

    try:
        response = requests.post(
            server_url,
            data={'text': prompt},
            files={'speaker_wav': ('speaker.wav', sample_audio_bytes, 'audio/wav')},
            timeout=120,
        )
        response.raise_for_status()
        return response.content, None
    except Exception as e:
        logging.error(f"Voice clone failed: {e}")
        return None, f'Voice clone failed: {e}'


def generate_voice(prompt, gender='female', tone='soft'):
    api_entry = pick_api('voice')
    api_key   = decrypt_secret_text(api_entry.api_key) if api_entry else get_env_api_key('voice')
    provider  = _normalize_provider(api_entry.provider if api_entry else os.getenv('VOICE_API_PRIMARY', ''))
    voice_key = _voice_key(gender, tone)
    voice_id  = VOICE_IDS.get(voice_key, VOICE_IDS['female_soft'])

    # MNI native fallback: local open-source TTS if no external API is configured.
    if not api_key or provider == 'local':
        return _generate_voice_local(prompt, _guess_local_tts_lang(prompt), gender, tone)

    try:
        if provider == 'openai':
            return _generate_voice_openai(prompt, gender, tone, api_key)

        if provider == 'sarvam':
            return _generate_voice_sarvam(prompt, gender, tone, api_key)

        url     = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
        body    = {"text": prompt, "model_id": "eleven_monolingual_v1",
                   "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
        r = requests.post(url, json=body, headers=headers, timeout=30)
        r.raise_for_status()
        return r.content, None  # raw audio bytes

    except requests.exceptions.RequestException as e:
        logging.warning(f"ElevenLabs API failed: {e}. Falling back to local TTS.")
        if api_entry:
            mark_failed(api_entry.id)
            fallback = get_next_fallback('voice', api_entry.id)
            if fallback:
                fallback_provider = _normalize_provider(fallback.provider)
                try:
                    if fallback_provider == 'openai':
                        return _generate_voice_openai(prompt, gender, tone, decrypt_secret_text(fallback.api_key))
                    if fallback_provider == 'sarvam':
                        return _generate_voice_sarvam(prompt, gender, tone, decrypt_secret_text(fallback.api_key))
                except requests.exceptions.RequestException as fallback_error:
                    logging.warning(f"Voice fallback API failed: {fallback_error}")

        # Smooth Fallback to Local Generation upon API error
        return _generate_voice_local(prompt, _guess_local_tts_lang(prompt), gender, tone)


def _generate_voice_local(prompt, lang='en', gender='female', tone='soft'):
    """Prefer Piper for offline TTS, then fall back to gTTS if available."""
    piper_audio, piper_err = _generate_voice_piper(prompt, gender, tone)
    if piper_audio:
        return piper_audio, None

    if gTTS is None:
        return None, f"Local TTS unavailable. {piper_err or 'Install Piper or gTTS.'}"
    try:
        tts = gTTS(text=prompt, lang=lang, slow=False)
        fp = BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp.getvalue(), None
    except Exception as e:
        logging.error(f"Local voice generation failed: {e}")
        detail = piper_err or 'gTTS failed'
        return None, f"Local Voice generation failed: {str(e)} | {detail}"


def _transcribe_audio_openai(audio_bytes, api_key):
    if not api_key:
        return None
    filename = 'audio.mp3'
    mime = 'audio/mpeg'
    if audio_bytes[:4] == b'OggS':
        filename = 'audio.ogg'
        mime = 'audio/ogg'
    elif audio_bytes[:4] == b'RIFF':
        filename = 'audio.wav'
        mime = 'audio/wav'
    files = {
        'file': (filename, audio_bytes, mime),
    }
    data = {
        'model': os.getenv('OPENAI_STT_MODEL', 'whisper-1'),
        'response_format': 'json',
    }
    response = requests.post(
        'https://api.openai.com/v1/audio/transcriptions',
        headers={'Authorization': f'Bearer {api_key}'},
        data=data,
        files=files,
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    return str(payload.get('text') or '').strip() or None


def transcribe_audio(audio_bytes):
    """Open-Source Local STT (Whisper) for incoming User Voice Notes/Videos."""
    if whisper is None:
        logging.warning("Whisper STT dependency missing. Trying hosted STT fallback.")
        try:
            voice_entry = pick_api('voice')
            if voice_entry and _normalize_provider(voice_entry.provider) == 'openai':
                return _transcribe_audio_openai(audio_bytes, decrypt_secret_text(voice_entry.api_key))
            text_entry = pick_api('text')
            if text_entry and _normalize_provider(text_entry.provider) == 'openai':
                return _transcribe_audio_openai(audio_bytes, decrypt_secret_text(text_entry.api_key))
            env_key = os.getenv('OPENAI_API_KEY', '')
            if env_key:
                return _transcribe_audio_openai(audio_bytes, env_key)
        except Exception as fallback_error:
            logging.error(f"[OpenAI STT Fallback Error] {fallback_error}", exc_info=True)
        return None
    tmp_path = None
    try:
        model = whisper.load_model("base")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        result = model.transcribe(tmp_path)
        return result["text"]
    except Exception as e:
        logging.error(f"[Whisper STT Error] {e}", exc_info=True)
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
