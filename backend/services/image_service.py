"""
MNI Automation Manager - Image Generation Service
"""
import base64
import hashlib
import os
import struct
import textwrap
import zlib
import requests
from backend.services.router import pick_api, mark_failed, get_env_api_key, get_next_fallback
from backend.services.router import _normalize_provider
from backend.services.secret_store import decrypt_secret_text


def _sanitize_image_error(provider, error):
    message = str(error or '')
    print(f"[Image Service Error][{provider}] {message}")
    if 'billing_hard_limit_reached' in message or 'Billing hard limit has been reached' in message:
        return "OpenAI image billing hard limit hit ho gaya hai. Gemini ya dusri funded image key use karo."
    if 'RESOURCE_EXHAUSTED' in message or 'Quota exceeded' in message or '429' in message:
        return "Gemini image quota/rate limit hit ho gaya hai. Thodi der baad try karo ya paid quota enable karo."
    return "Image generate nahi ho payi abhi. Thoda baad try karo."


def _local_fallback_image(prompt):
    digest = hashlib.sha256((prompt or 'mni image').encode('utf-8')).hexdigest()
    width = 768
    height = 768
    palette = [
        tuple(int(digest[i + j:i + j + 2], 16) for j in (0, 2, 4))
        for i in (0, 6, 12, 18, 24)
    ]
    start, end, accent, accent2, accent3 = palette
    prompt_score = sum(ord(ch) for ch in (prompt or 'Generated image'))
    wrapped = textwrap.wrap((prompt or 'Generated image').strip(), width=26)[:4]
    lines = wrapped or ['Generated image']

    rows = []
    for y in range(height):
        mix_y = y / max(height - 1, 1)
        base_r = int(start[0] * (1 - mix_y) + end[0] * mix_y)
        base_g = int(start[1] * (1 - mix_y) + end[1] * mix_y)
        base_b = int(start[2] * (1 - mix_y) + end[2] * mix_y)
        row = bytearray([0])
        for x in range(width):
            mix_x = x / max(width - 1, 1)
            curve = ((x - width / 2) ** 2 + (y - height / 2) ** 2) ** 0.5
            wave = int(18 * ((x + y + prompt_score) % 29) / 28)
            blob1 = max(0.0, 1 - (((x - width * 0.22) ** 2 + (y - height * 0.28) ** 2) ** 0.5) / 260)
            blob2 = max(0.0, 1 - (((x - width * 0.78) ** 2 + (y - height * 0.22) ** 2) ** 0.5) / 240)
            blob3 = max(0.0, 1 - (((x - width * 0.70) ** 2 + (y - height * 0.78) ** 2) ** 0.5) / 310)
            r = min(255, max(0, int(
                base_r * (0.72 + 0.28 * mix_x)
                + accent[0] * 0.14
                + accent2[0] * blob1 * 0.55
                + accent3[0] * blob2 * 0.45
                - curve * 0.013
                + wave
            )))
            g = min(255, max(0, int(
                base_g * (0.85 + 0.15 * (1 - mix_x))
                + accent[1] * 0.10
                + accent2[1] * blob2 * 0.48
                + accent3[1] * blob3 * 0.52
                - curve * 0.009
            )))
            b = min(255, max(0, int(
                base_b
                + accent[2] * 0.18
                + accent2[2] * blob3 * 0.44
                + accent3[2] * blob1 * 0.36
                - curve * 0.006
                + wave // 2
            )))

            for idx, line in enumerate(lines):
                text_top = 420 + idx * 44
                if text_top <= y < text_top + 26:
                    line_seed = sum(ord(ch) for ch in line)
                    left = 72
                    usable = max(width - 144, 1)
                    char_band = max(len(line) * 18, 1)
                    right = min(width - 72, left + char_band)
                    if left <= x <= right:
                        char_slot = max((x - left) // 18, 0)
                        if char_slot < len(line):
                            char_val = ord(line[char_slot])
                            if ((x + y + char_val + line_seed) % 11) < 5:
                                r = min(255, 225 + (char_val % 20))
                                g = min(255, 225 + (line_seed % 20))
                                b = min(255, 225 + ((char_val + line_seed) % 20))

            row.extend((r, g, b))
        rows.append(bytes(row))

    raw = b''.join(rows)
    compressed = zlib.compress(raw, level=9)

    def chunk(tag, data):
        return (
            struct.pack("!I", len(data))
            + tag
            + data
            + struct.pack("!I", zlib.crc32(tag + data) & 0xffffffff)
        )

    png = b''.join([
        b'\x89PNG\r\n\x1a\n',
        chunk(b'IHDR', struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0)),
        chunk(b'IDAT', compressed),
        chunk(b'IEND', b''),
    ])
    encoded = base64.b64encode(png).decode('ascii')
    return f"data:image/png;base64,{encoded}", None


def _call_image_provider(provider, api_key, prompt):
    provider = _normalize_provider(provider)
    if not api_key:
        return None, "Image generation API not configured."

    if provider == 'openai':
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body = {
            "model": os.getenv('OPENAI_IMAGE_MODEL', 'gpt-image-1'),
            "prompt": prompt,
            "size": "1024x1024",
        }
        r = requests.post("https://api.openai.com/v1/images/generations", json=body, headers=headers, timeout=30)
        r.raise_for_status()
        image = r.json()['data'][0]
        if image.get('b64_json'):
            return f"data:image/png;base64,{image['b64_json']}", None
        if image.get('url'):
            return image['url'], None
        raise ValueError('No image payload returned by OpenAI')

    if provider == 'gemini':
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
        }
        model = os.getenv('GEMINI_IMAGE_MODEL', 'gemini-2.5-flash-image')
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            json=body,
            headers=headers,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        for candidate in data.get('candidates', []):
            parts = candidate.get('content', {}).get('parts', [])
            for part in parts:
                inline = part.get('inlineData') or part.get('inline_data') or {}
                encoded = inline.get('data')
                mime = inline.get('mimeType') or inline.get('mime_type') or 'image/png'
                if encoded:
                    return f"data:{mime};base64,{encoded}", None
        raise ValueError('No image payload returned by Gemini')

    if provider == 'stability':
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        body = {"text_prompts": [{"text": prompt}], "cfg_scale": 7, "steps": 30}
        r = requests.post("https://api.stability.ai/v1/generation/stable-diffusion-v1-6/text-to-image", json=body, headers=headers, timeout=60)
        r.raise_for_status()
        b64 = r.json()['artifacts'][0]['base64']
        return f"data:image/png;base64,{b64}", None

    return None, "Image provider abhi supported nahi hai."


def generate_image(prompt):
    api_entry = pick_api('image')
    api_key = decrypt_secret_text(api_entry.api_key) if api_entry else get_env_api_key('image')
    provider = _normalize_provider(api_entry.provider if api_entry else os.getenv('IMAGE_API_PRIMARY', 'openai'))
    allow_local_fallback = os.getenv('IMAGE_LOCAL_FALLBACK', 'false').lower() == 'true'

    try:
        return _call_image_provider(provider, api_key, prompt)
    except Exception as e:
        if api_entry:
            mark_failed(api_entry.id)
            fallback = get_next_fallback('image', api_entry.id)
            if fallback:
                try:
                    return _call_image_provider(fallback.provider, decrypt_secret_text(fallback.api_key), prompt)
                except Exception as fallback_error:
                    if allow_local_fallback:
                        fallback_image, _ = _local_fallback_image(prompt)
                        return fallback_image, _sanitize_image_error(_normalize_provider(fallback.provider), fallback_error)
                    return None, _sanitize_image_error(_normalize_provider(fallback.provider), fallback_error)
        if allow_local_fallback:
            fallback_image, _ = _local_fallback_image(prompt)
            return fallback_image, _sanitize_image_error(provider, e)
        return None, _sanitize_image_error(provider, e)
