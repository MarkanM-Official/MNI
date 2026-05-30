"""
MNI Automation Manager - Smart API Routing Engine
Multi-API support, load balancing, failover
"""
import os
from backend.database import db
from backend.models.config_model import ApiKey, BotConfig
from backend.services.runtime_config import get_config_value

_round_robin_counters = {}  # category -> index

_SUPPORTED_PROVIDERS = {
    'text': {'openai', 'anthropic', 'sarvam', 'gemini'},
    'image': {'openai', 'stability', 'gemini'},
    'voice': {'elevenlabs', 'openai', 'sarvam'},
    'video': {'replicate'},
}


def _normalize_provider(provider):
    provider = (provider or '').strip().lower()
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
    if 'stability' in provider:
        return 'stability'
    if 'replicate' in provider:
        return 'replicate'
    return provider


def is_supported_provider(category, provider):
    supported = _SUPPORTED_PROVIDERS.get((category or '').strip().lower())
    if supported is None:
        return True
    return _normalize_provider(provider) in supported


def get_strategy():
    return get_config_value('load_balancing', 'round_robin')


def get_api_pool(category):
    """Get all active APIs for a category, sorted by priority."""
    pool = ApiKey.query.filter_by(
        category=category, is_active=True
    ).order_by(ApiKey.priority.asc()).all()
    return [api for api in pool if is_supported_provider(category, api.provider)]


def pick_api(category):
    """
    Pick the best API for a category using load balancing strategy.
    Returns ApiKey object or None.
    """
    pool = get_api_pool(category)
    if not pool:
        return None

    strategy = get_strategy()

    # Primary first on low load
    primary = next((a for a in pool if a.is_primary), None)
    if primary and strategy == 'priority':
        return primary

    # Round-robin
    if strategy == 'round_robin':
        idx = _round_robin_counters.get(category, 0)
        chosen = pool[idx % len(pool)]
        _round_robin_counters[category] = (idx + 1) % len(pool)
        return chosen

    # Default: first available
    return pool[0]


def mark_failed(api_id):
    """Increment fail count. Disable if fails > 5."""
    api = ApiKey.query.get(api_id)
    if api:
        api.fail_count += 1
        if api.fail_count >= 5:
            api.is_active = False
        db.session.commit()


def get_next_fallback(category, exclude_id):
    """Get next available API excluding a failed one."""
    pool = get_api_pool(category)
    fallbacks = [a for a in pool if a.id != exclude_id]
    return fallbacks[0] if fallbacks else None


def get_env_api_key(category):
    """
    Fallback: read from environment if no DB API keys set up.
    Used during initial setup.
    """
    mapping = {
        'text':  os.getenv('OPENAI_API_KEY', '') or os.getenv('ANTHROPIC_API_KEY', '') or os.getenv('GEMINI_API_KEY', '') or os.getenv('GOOGLE_API_KEY', '') or os.getenv('SARVAM_API_KEY', ''),
        'image': os.getenv('OPENAI_API_KEY', '') or os.getenv('GEMINI_API_KEY', '') or os.getenv('GOOGLE_API_KEY', ''),
        'voice': os.getenv('OPENAI_API_KEY', '') or os.getenv('ELEVENLABS_API_KEY', '') or os.getenv('SARVAM_API_KEY', ''),
        'video': os.getenv('REPLICATE_API_KEY', ''),
    }
    return mapping.get(category, '')
