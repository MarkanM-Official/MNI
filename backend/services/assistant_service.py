import ast
import operator
import re
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import base64
from io import BytesIO
try:
    import qrcode
except Exception:
    qrcode = None
try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None

from backend.models.blog_post import BlogPost
from backend.services.local_data_service import build_local_context
from backend.services import meeting_service
from backend.services.runtime_config import get_config_value


OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}

CITY_TIMEZONES = {
    'delhi': 'Asia/Kolkata',
    'mumbai': 'Asia/Kolkata',
    'kolkata': 'Asia/Kolkata',
    'london': 'Europe/London',
    'dubai': 'Asia/Dubai',
    'new york': 'America/New_York',
}


def _identity_response(message):
    lowered = (message or '').lower().strip()
    identity_patterns = [
        'what is your name',
        'what\'s your name',
        'who are you',
        'tell me your name',
        'tumhara naam kya hai',
        'apka naam kya hai',
        'tera naam kya hai',
        'your name',
    ]
    if any(pattern in lowered for pattern in identity_patterns):
        return "My name is MNI. I am your automation manager."
    return None


def _capability_response(message):
    lowered = (message or '').lower().strip()
    patterns = [
        'what can you do',
        'what all can you do',
        'okay what thing you can do',
        'what thing you can do',
        'tum kya kar sakte ho',
        'tum kya kar sakta hai',
        'aap kya kar sakte ho',
        'kya kya kar sakte ho',
        'kya kar sakte ho',
    ]
    if any(pattern in lowered for pattern in patterns):
        return (
            "I can help with chat, coding, ideas, translations, web search, images, voice replies, and admin actions when you are authorized. "
            "If you want platform data, ask clearly like: `admin users telegram` or `admin scopes discord`."
        )
    return None


def _safe_eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Num):
        return node.n
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    raise ValueError('unsupported expression')


def calculate_expression(expression):
    parsed = ast.parse(expression, mode='eval')
    result = _safe_eval(parsed.body)
    if isinstance(result, float):
        result = round(result, 6)
    return result


def convert_units(message):
    text = (message or '').lower()
    temp = re.search(r'(-?\d+(?:\.\d+)?)\s*(c|celsius)\s+to\s+(f|fahrenheit)', text)
    if temp:
        c = float(temp.group(1))
        return f"{c}C = {round((c * 9/5) + 32, 2)}F"
    temp = re.search(r'(-?\d+(?:\.\d+)?)\s*(f|fahrenheit)\s+to\s+(c|celsius)', text)
    if temp:
        f = float(temp.group(1))
        return f"{f}F = {round((f - 32) * 5/9, 2)}C"
    km = re.search(r'(\d+(?:\.\d+)?)\s*(km|kilometer|kilometre)s?\s+to\s+(mi|mile)s?', text)
    if km:
        value = float(km.group(1))
        return f"{value} km = {round(value * 0.621371, 3)} miles"
    mi = re.search(r'(\d+(?:\.\d+)?)\s*(mi|mile)s?\s+to\s+(km|kilometer|kilometre)s?', text)
    if mi:
        value = float(mi.group(1))
        return f"{value} miles = {round(value / 0.621371, 3)} km"
    return None


def fetch_weather(city):
    url = f'https://wttr.in/{requests.utils.quote(city)}?format=j1'
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()
    current = data['current_condition'][0]
    return {
        'city': city,
        'temp_c': current.get('temp_C'),
        'feels_like_c': current.get('FeelsLikeC'),
        'condition': current.get('weatherDesc', [{}])[0].get('value', 'Unknown'),
        'humidity': current.get('humidity'),
        'wind_kmph': current.get('windspeedKmph'),
    }


def fetch_wikipedia(topic):
    url = f'https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(topic)}'
    response = requests.get(url, headers={'User-Agent': 'MNI/1.0'}, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data.get('extract')


def fetch_reddit_summary(topic):
    url = f'https://www.reddit.com/search.json?q={requests.utils.quote(topic)}&limit=5&sort=relevance'
    response = requests.get(url, headers={'User-Agent': 'MNI/1.0'}, timeout=10)
    response.raise_for_status()
    posts = response.json().get('data', {}).get('children', [])
    cleaned = []
    for item in posts[:5]:
        post = item.get('data', {})
        title = post.get('title', '').strip()
        score = post.get('score', 0)
        subreddit = post.get('subreddit', '')
        if title:
            cleaned.append(f"- r/{subreddit}: {title} (score {score})")
    if not cleaned:
        return None
    return "Reddit discussion snapshot:\n" + "\n".join(cleaned)


def find_relevant_blog(topic):
    topic = (topic or '').lower().strip()
    if not topic:
        return None
    ranked = []
    for post in BlogPost.query.filter_by(is_enabled=True).all():
        haystack = ' '.join([post.title, post.summary, post.tags, post.content]).lower()
        score = sum(1 for token in topic.split() if token and token in haystack)
        if score:
            ranked.append((score, post))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def _format_weather(payload):
    return (
        f"Weather in {payload['city']}:\n"
        f"- Temperature: {payload['temp_c']}C\n"
        f"- Feels like: {payload['feels_like_c']}C\n"
        f"- Condition: {payload['condition']}\n"
        f"- Humidity: {payload['humidity']}%\n"
        f"- Wind: {payload['wind_kmph']} km/h"
    )


def _detect_city(message):
    text = (message or '').strip()
    match = re.search(r'(?:weather\s+in|temperature\s+in|forecast\s+for)\s+(.+)$', text, re.I)
    return match.group(1).strip(' ?.!') if match else None


def _detect_topic(message, prefixes):
    lowered = (message or '').lower().strip()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return message[len(prefix):].strip(' ?.!')
    return None


def _time_response(message):
    lowered = (message or '').lower()
    city = None
    for known in CITY_TIMEZONES:
        if known in lowered:
            city = known
            break
    tz = ZoneInfo(CITY_TIMEZONES.get(city, 'Asia/Kolkata'))
    now = datetime.now(tz)
    target = city.title() if city else 'server timezone'
    return f"Current time in {target}: {now.strftime('%d %b %Y, %I:%M %p (%Z)')}"


def offline_fallback(message):
    identity = _identity_response(message)
    if identity:
        return identity
    capabilities = _capability_response(message)
    if capabilities:
        return capabilities
    local = build_local_context(message)
    if local:
        return f"I found some local context:\n\n{local}\n\nI’m not fully sure beyond this, but this should help."
    topic = _detect_topic(message, ['what is ', 'who is ', 'tell me about '])
    if topic:
        try:
            wiki = fetch_wikipedia(topic)
            if wiki:
                return f"{topic.title()}:\n{wiki}"
        except requests.exceptions.RequestException as e:
            logging.warning(f"[Offline Wikipedia] Request failed for topic '{topic}': {e}")
        except Exception as e:
            logging.error(f"[Offline Wikipedia] Unexpected error for topic '{topic}': {e}")
    return "I’m not fully sure. Try asking more specifically, or enable a data source/API for a stronger answer."


def perform_web_search(query):
    """Uses DuckDuckGo to perform a free, real-time web search."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        
        if not results:
            return "No search results found on the web."
            
        formatted = "Live Web Search Results:\n"
        for r in results:
            formatted += f"- {r.get('title')}: {r.get('body')} (Source: {r.get('href')})\n"
        return formatted
    except ImportError:
        return "Web search tool is not installed. Admin needs to run `pip install duckduckgo-search`."
    except Exception as e:
        return f"Web search failed: {str(e)}"


def generate_qr_code(data):
    if qrcode is None:
        return None
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    encoded = base64.b64encode(buffered.getvalue()).decode('ascii')
    return f"data:image/png;base64,{encoded}"


def translate_text(text, target_lang='en'):
    if GoogleTranslator is None:
        return "Translation tool is not installed. Admin needs to add `deep-translator`."
    try:
        # Seamlessly handles Hinglish, Hindi, and English
        translated = GoogleTranslator(source='auto', target=target_lang).translate(text)
        return translated
    except Exception as e:
        return f"Translation error: {str(e)}"


def handle_utility_request(message):
    text = (message or '').strip()
    lowered = text.lower()

    if not text:
        return None

    media_keywords = [
        'generate image', 'create image', 'make image', 'image of', 'photo of',
        'generate voice', 'create voice', 'make voice', 'voice note', 'audio message',
        'text to speech', 'tts', 'send voice', 'generate the voice of', 'voice of',
        'generate video', 'create video', 'make video',
    ]
    if any(keyword in lowered for keyword in media_keywords):
        return None

    identity = _identity_response(text)
    if identity:
        return {'response': identity, 'api_used': 'identity', 'type': 'text'}

    if 'google meet' in lowered and any(word in lowered for word in ['create', 'book', 'schedule', 'setup', 'call']):
        try:
            client_id = get_config_value('google_client_id')
            client_secret = get_config_value('google_client_secret')
            refresh_token = get_config_value('google_refresh_token')

            if not all([client_id, client_secret, refresh_token]):
                return {'response': 'Google Meet integration is not configured. Admin panel ya environment variables me credentials set karo.', 'api_used': 'gmeet_error', 'type': 'text'}

            meet_details = meeting_service.create_google_meet_space(client_id, client_secret, refresh_token)
            response = f"✅ Google Meet link created!\n\nJoin here: {meet_details['meeting_uri']}"
            return {'response': response, 'api_used': 'gmeet', 'type': 'text'}
        except requests.exceptions.HTTPError as e:
            error_message = str(e)
            try:
                if e.response is not None:
                    error_details = e.response.json()
                    error_message = error_details.get('error_description') or error_details.get('error', {}).get('message') or str(e)
            except (ValueError, AttributeError):
                pass  # Stick with the original error message
            logging.error(f"[GMeet Error] {error_message}")
            return {'response': f"Google Meet create nahi ho paaya. Error: {error_message}", 'api_used': 'gmeet_error', 'type': 'text'}
        except Exception as e:
            logging.error(f"[GMeet Error] Unexpected error: {str(e)}")
            return {'response': f"Google Meet create nahi ho paaya. Error: {str(e)}", 'api_used': 'gmeet_error', 'type': 'text'}

    if any(word in lowered for word in ['weather in', 'temperature in', 'forecast for']):
        city = _detect_city(text)
        if city:
            try:
                return {'response': _format_weather(fetch_weather(city)), 'api_used': 'weather', 'type': 'text'}
            except requests.exceptions.RequestException as e:
                logging.warning(f"Weather fetch failed for {city}: {e}")
                return {'response': f"Weather fetch nahi ho paaya for {city}. City name check karo.", 'api_used': 'weather_error', 'type': 'text'}

    if lowered.startswith(('calc ', 'calculate ')):
        expr = re.sub(r'^(calc|calculate)\s+', '', text, flags=re.I).strip()
        try:
            return {'response': f"Result: {calculate_expression(expr)}", 'api_used': 'calculator', 'type': 'text'}
        except (ValueError, SyntaxError):
            return {'response': "Calculation samajh nahi aayi. Example: `calc (12*8)/4`", 'api_used': 'calculator_error', 'type': 'text'}

    converted = convert_units(text)
    if converted:
        return {'response': converted, 'api_used': 'converter', 'type': 'text'}

    if any(word in lowered for word in ['time', 'date']) and not any(word in lowered for word in ['lifetime', 'update']):
        return {'response': _time_response(text), 'api_used': 'datetime', 'type': 'text'}

    wiki_topic = _detect_topic(text, ['wiki ', 'wikipedia ', 'what is ', 'who is ', 'tell me about '])
    if wiki_topic:
        try:
            wiki = fetch_wikipedia(wiki_topic)
            if wiki:
                return {'response': f"{wiki_topic.title()}:\n{wiki}", 'api_used': 'wikipedia', 'type': 'text'}
        except requests.exceptions.RequestException as e:
            logging.warning(f"Wikipedia fetch failed for '{wiki_topic}': {e}")
        except Exception as e:
            logging.error(f"Unexpected Wikipedia error for '{wiki_topic}': {e}")

    if 'reddit' in lowered and any(word in lowered for word in ['summary', 'summarize', 'opinions', 'discussion']):
        topic = re.sub(r'.*reddit(?:\s+opinions|\s+discussion|\s+summary)?\s+(?:on|about)?\s*', '', text, flags=re.I).strip(' ?.!')
        topic = topic or text
        try:
            summary = fetch_reddit_summary(topic)
            if summary:
                return {'response': summary, 'api_used': 'reddit', 'type': 'text'}
        except requests.exceptions.RequestException as e:
            logging.warning(f"Reddit fetch failed for '{topic}': {e}")
            return {'response': 'Reddit fetch fail ho gaya. Thoda baad try karo.', 'api_used': 'reddit_error', 'type': 'text'}

    if 'quora' in lowered:
        return {'response': 'Quora ka official public API available nahi hai. Manual source ya custom scraper layer chahiye hogi.', 'api_used': 'quora_unavailable', 'type': 'text'}

    if any(word in lowered for word in ['search the web', 'web search', 'search online', 'latest news', 'live search']):
        query = re.sub(r'^(search the web|web search|search online|latest news|live search)\s*', '', text, flags=re.I).strip(' ?.!')
        query = query or text
        return {'response': perform_web_search(query), 'api_used': 'web_search', 'type': 'text'}

    if any(word in lowered for word in ['qr code', 'create qr', 'generate qr', 'make qr']):
        payload = re.sub(r'^(create|generate|make)?\s*qr(\s*code)?\s*', '', text, flags=re.I).strip() or text
        qr = generate_qr_code(payload)
        if not qr:
            return {'response': 'QR code feature ke liye `qrcode` package missing hai.', 'api_used': 'qr_missing_dep', 'type': 'text'}
        return {'response': qr, 'api_used': 'qr_code', 'type': 'image'}

    if lowered.startswith('translate '):
        parts = text.split('|')
        if len(parts) == 2:
            source_text = parts[0].replace('translate', '', 1).strip()
            target_lang = parts[1].strip() or 'en'
        else:
            source_text = text.replace('translate', '', 1).strip()
            target_lang = 'en'
        return {'response': translate_text(source_text, target_lang), 'api_used': 'translate', 'type': 'text'}

    if 'blog' in lowered:
        topic = re.sub(r'.*blog\s+(?:about|on)?\s*', '', text, flags=re.I).strip(' ?.!')
        post = find_relevant_blog(topic or text)
        if post:
            return {
                'response': f"Local blog match: {post.title}\n\n{post.summary or post.content[:800]}",
                'api_used': 'local_blog',
                'type': 'text'
            }

    return None
