import json
import re
from typing import List, Optional, Tuple

import requests

from backend.database import db
from backend.models.message import Message
from backend.models.user import User
from backend.models.config_model import BotConfig
from backend.services.runtime_config import get_all_config_values, get_config_value
from backend.services.secret_store import encrypt_config_value


def _split_config_values(raw):
    return [item.strip() for item in str(raw or '').replace('\n', ',').split(',') if item.strip()]


def _set_config(key, value):
    row = BotConfig.query.filter_by(key=key).first()
    stored_value = encrypt_config_value(key, value)
    if row:
        row.value = stored_value
    else:
        db.session.add(BotConfig(key=key, value=stored_value))
    db.session.commit()


def _get_json_list(key):
    raw = get_config_value(key, '[]')
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _set_json_list(key, values):
    _set_config(key, json.dumps(values))


def _normalize_identity(value):
    return str(value or '').strip().lower().lstrip('@')


def _identity_skeleton(value):
    normalized = _normalize_identity(value)
    if not normalized:
        return ''
    parts = normalized.split(':')
    cleaned = [re.sub(r'[^a-z0-9]+', '', part) for part in parts]
    return ':'.join(part for part in cleaned if part)


def _identity_candidates(user_id='', username='', platform=''):
    platform = str(platform or '').strip().lower()
    username = str(username or '').strip()
    normalized_username = _normalize_identity(username)
    raw_user_id = str(user_id or '').strip()
    items = {
        _normalize_identity(raw_user_id),
        normalized_username,
    }
    if platform and raw_user_id:
        items.add(_normalize_identity(f'{platform}:{raw_user_id}'))
    if platform and normalized_username:
        items.add(_normalize_identity(f'{platform}:{normalized_username}'))
    normalized = {item for item in items if item}
    skeletons = {_identity_skeleton(item) for item in normalized if item}
    return {item for item in normalized | skeletons if item}


def _get_role_entries(key):
    raw = get_config_value(key, '[]')
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _save_role_entries(key, entries):
    _set_config(key, json.dumps(entries))


def get_super_admin_identities():
    return _split_config_values(get_config_value('super_admin_identities', ''))


def get_platform_admin_roles():
    entries = _get_role_entries('platform_admin_roles')
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized.append({
            'identity': str(entry.get('identity', '')).strip(),
            'role': str(entry.get('role', 'platform_admin')).strip() or 'platform_admin',
            'platforms': [str(item).strip().lower() for item in (entry.get('platforms') or ['all']) if str(item).strip()],
            'permissions': [str(item).strip().lower() for item in (entry.get('permissions') or ['all']) if str(item).strip()],
        })
    return normalized


def grant_platform_admin(identity, role='platform_admin', platforms=None, permissions=None):
    normalized_identity = str(identity or '').strip()
    if not normalized_identity:
        return get_platform_admin_roles()
    platforms = [str(item).strip().lower() for item in (platforms or ['all']) if str(item).strip()]
    permissions = [str(item).strip().lower() for item in (permissions or ['all']) if str(item).strip()]
    entries = get_platform_admin_roles()
    lookup = _normalize_identity(normalized_identity)
    updated = False
    for entry in entries:
        if _normalize_identity(entry.get('identity')) == lookup:
            entry['role'] = role
            entry['platforms'] = platforms
            entry['permissions'] = permissions
            updated = True
            break
    if not updated:
        entries.append({
            'identity': normalized_identity,
            'role': role,
            'platforms': platforms,
            'permissions': permissions,
        })
    _save_role_entries('platform_admin_roles', entries)
    return entries


def revoke_platform_admin(identity):
    lookup = _normalize_identity(identity)
    entries = [entry for entry in get_platform_admin_roles() if _normalize_identity(entry.get('identity')) != lookup]
    _save_role_entries('platform_admin_roles', entries)
    return entries


def get_actor_role(user_id='', username='', platform=''):
    candidates = _identity_candidates(user_id=user_id, username=username, platform=platform)
    super_admins = {
        item
        for raw in get_super_admin_identities()
        for item in {_normalize_identity(raw), _identity_skeleton(raw)}
        if item
    }
    if candidates & super_admins:
        return {
            'is_admin': True,
            'is_super_admin': True,
            'role': 'super_admin',
            'permissions': ['all'],
            'platforms': ['all'],
        }

    for entry in get_platform_admin_roles():
        lookup_values = {
            _normalize_identity(entry.get('identity')),
            _identity_skeleton(entry.get('identity')),
        }
        if any(lookup and lookup in candidates for lookup in lookup_values):
            return {
                'is_admin': True,
                'is_super_admin': False,
                'role': entry.get('role', 'platform_admin'),
                'permissions': entry.get('permissions') or ['all'],
                'platforms': entry.get('platforms') or ['all'],
            }

    legacy_admins = {
        item
        for raw in _split_config_values(get_config_value('admin_identities', ''))
        for item in {_normalize_identity(raw), _identity_skeleton(raw)}
        if item
    }
    if candidates & legacy_admins:
        return {
            'is_admin': True,
            'is_super_admin': False,
            'role': 'platform_admin',
            'permissions': ['all'],
            'platforms': ['all'],
        }

    return {
        'is_admin': False,
        'is_super_admin': False,
        'role': 'user',
        'permissions': [],
        'platforms': [],
    }


def actor_has_permission(actor, permission, target_platform=''):
    if actor.get('is_super_admin'):
        return True
    permissions = {str(item).strip().lower() for item in (actor.get('permissions') or [])}
    platforms = {str(item).strip().lower() for item in (actor.get('platforms') or [])}
    target_platform = str(target_platform or '').strip().lower()
    permission = str(permission or '').strip().lower()
    platform_ok = 'all' in platforms or not target_platform or target_platform in platforms
    perm_ok = 'all' in permissions or permission in permissions
    return platform_ok and perm_ok


def _message_count_lookup():
    lookup = {}
    rows = db.session.query(
        Message.user_id,
        Message.platform,
        db.func.count(Message.id),
        db.func.max(Message.timestamp),
    ).group_by(Message.user_id, Message.platform).all()
    for row in rows:
        lookup[(row[0], row[1])] = {
            'message_count': row[2],
            'last_message_at': row[3].isoformat() if row[3] else None,
        }
    return lookup


def list_known_contacts(platform='', query=''):
    platform = str(platform or '').strip().lower()
    query_text = str(query or '').strip().lower()
    rows = User.query
    if platform:
        rows = rows.filter_by(platform=platform)
    if query_text:
        like = f'%{query_text}%'
        rows = rows.filter(db.or_(User.username.ilike(like), User.user_id.ilike(like)))
    users = rows.order_by(User.last_seen.desc()).all()
    message_lookup = _message_count_lookup()
    payload = []
    for user in users:
        meta = message_lookup.get((user.user_id, user.platform), {})
        payload.append({
            **user.to_dict(),
            'message_count': meta.get('message_count', 0),
            'last_message_at': meta.get('last_message_at'),
        })
    return payload


def list_known_scopes(platform='', query=''):
    platform = str(platform or '').strip().lower()
    query_text = str(query or '').strip().lower()
    rows = db.session.query(
        Message.platform,
        Message.chat_id,
        db.func.max(Message.username).label('last_username'),
        db.func.count(Message.id).label('message_count'),
        db.func.max(Message.timestamp).label('last_seen'),
    ).filter(Message.chat_id != '')
    if platform:
        rows = rows.filter(Message.platform == platform)
    rows = rows.group_by(Message.platform, Message.chat_id).order_by(db.desc('last_seen')).all()
    payload = []
    for row in rows:
        label = (
            f"Telegram Chat {row[1]}" if row[0] == 'telegram' else
            f"Discord Scope {row[1]}" if row[0] == 'discord' else
            (row[2] or f"{row[0].title()} Scope {row[1]}")
        )
        if query_text and query_text not in str(label).lower() and query_text not in str(row[1]).lower():
            continue
        payload.append({
            'platform': row[0],
            'chat_id': row[1],
            'chat_title': label,
            'message_count': row[3],
            'last_seen': row[4].isoformat() if row[4] else None,
        })
    return payload


def list_scope_members(platform, chat_id, query=''):
    platform = str(platform or '').strip().lower()
    chat_id = str(chat_id or '').strip()
    query_text = str(query or '').strip().lower()
    if not platform or not chat_id:
        return []
    rows = db.session.query(
        Message.user_id,
        db.func.max(Message.username).label('username'),
        db.func.count(Message.id).label('message_count'),
        db.func.max(Message.timestamp).label('last_seen'),
    ).filter_by(platform=platform, chat_id=chat_id).group_by(Message.user_id).order_by(db.desc('last_seen')).all()
    payload = []
    for row in rows:
        username = row[1] or row[0]
        if query_text and query_text not in str(username).lower() and query_text not in str(row[0]).lower():
            continue
        payload.append({
            'platform': platform,
            'chat_id': chat_id,
            'user_id': row[0],
            'username': username,
            'message_count': row[2],
            'last_seen': row[3].isoformat() if row[3] else None,
        })
    return payload


def get_contact_history(platform, user_id, limit=20):
    rows = Message.query.filter_by(platform=platform, user_id=user_id).order_by(Message.timestamp.desc()).limit(limit).all()
    return [row.to_dict() for row in rows]


def resolve_contact(platform, target):
    platform = str(platform or '').strip().lower()
    target_text = str(target or '').strip()
    if not platform or not target_text:
        return None
    normalized_target = _normalize_identity(target_text)
    users = list_known_contacts(platform=platform)
    for user in users:
        candidates = _identity_candidates(user_id=user.get('user_id'), username=user.get('username'), platform=platform)
        if normalized_target in candidates:
            return user
    return None


def _discord_open_dm(recipient_id, token):
    response = requests.post(
        'https://discord.com/api/v10/users/@me/channels',
        json={'recipient_id': str(recipient_id)},
        headers={'Authorization': f'Bot {token}', 'Content-Type': 'application/json'},
        timeout=20,
    )
    if not response.ok:
        return False, response.text[:300]
    data = response.json()
    channel_id = str(data.get('id', '')).strip()
    if not channel_id:
        return False, 'Discord DM channel could not be created'
    return True, channel_id


def send_platform_message(platform, recipient, text, recipient_kind='user'):
    config = get_all_config_values()
    platform = str(platform or '').strip().lower()
    recipient = str(recipient or '').strip()
    text = str(text or '').strip()
    if not platform or not recipient or not text:
        return False, 'platform, recipient, and text are required'

    if platform == 'telegram':
        token = config.get('telegram_token', '')
        if not token:
            return False, 'Telegram token missing'
        response = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': recipient, 'text': text},
            timeout=20,
        )
        if not response.ok:
            return False, response.text[:300]
        return True, 'sent'

    if platform == 'discord':
        token = config.get('discord_token', '')
        if not token:
            return False, 'Discord token missing'
        channel_id = recipient
        if recipient_kind == 'user':
            ok, result = _discord_open_dm(recipient, token)
            if not ok:
                return False, result
            channel_id = result
        response = requests.post(
            f'https://discord.com/api/v10/channels/{channel_id}/messages',
            json={'content': text},
            headers={'Authorization': f'Bot {token}', 'Content-Type': 'application/json'},
            timeout=20,
        )
        if not response.ok:
            return False, response.text[:300]
        return True, 'sent'

    return False, f'Unsupported platform: {platform}'


def _format_short_list(rows, formatter, empty_message):
    if not rows:
        return empty_message
    return '\n'.join(formatter(index, row) for index, row in enumerate(rows[:15], start=1))


def _normalize_natural_admin_text(text):
    lowered = str(text or '').strip().lower()
    lowered = re.sub(r'\s+', ' ', lowered)
    return lowered


def execute_admin_command(actor_platform, actor_user_id, actor_username, raw_text):
    actor = get_actor_role(user_id=actor_user_id, username=actor_username, platform=actor_platform)
    if not actor.get('is_admin'):
        return None

    text = str(raw_text or '').strip()
    lowered = _normalize_natural_admin_text(text)
    body = ''
    lowered_body = ''

    if lowered in {'shutdown everywhere', 'arise everywhere', 'shutdown on this platform', 'arise on this platform', 'shutdown in dm', 'arise in dm'}:
        if lowered == 'shutdown everywhere':
            _set_config('silent_mode', 'true')
            return 'Sab jagah AI shutdown ho gaya. Ab admin ke `arise everywhere` tak koi reply nahi jayega.'
        if lowered == 'arise everywhere':
            _set_config('silent_mode', 'false')
            return 'Global shutdown hata diya. AI phir se active hai.'
        if lowered == 'shutdown in dm':
            _set_config('dm_only_mode', 'true')
            return 'DM-only mode active ho gaya. Ab DMs ke alawa kahin reply nahi jayega.'
        if lowered == 'arise in dm':
            _set_config('dm_only_mode', 'false')
            return 'DM-only mode hata diya. Non-DM scopes phir se active hain.'
        platform_key = str(actor_platform or '').strip().lower()
        if not platform_key:
            return 'Platform detect nahi ho paaya.'
        silenced_platforms = [str(item).strip().lower() for item in _get_json_list('silent_platforms') if str(item).strip()]
        if lowered == 'shutdown on this platform':
            if platform_key not in silenced_platforms:
                silenced_platforms.append(platform_key)
                _set_json_list('silent_platforms', silenced_platforms)
            return f'{platform_key} platform shutdown ho gaya. Ab is platform par admin ke `arise on this platform` tak koi reply nahi jayega.'
        silenced_platforms = [item for item in silenced_platforms if item != platform_key]
        _set_json_list('silent_platforms', silenced_platforms)
        return f'{platform_key} platform phir se active ho gaya.'

    if lowered.startswith('admin '):
        body = text[6:].strip()
        lowered_body = _normalize_natural_admin_text(body)
    else:
        natural_user_list = [
            'user list',
            'username list',
            'user name list',
            'user nam list',
            'mujhe user list',
            'mujhe username list',
            'mujhe user name list',
            'mujhe user nam list',
            'list dikha',
            'list do',
        ]
        natural_scope_list = [
            'group list',
            'scope list',
            'groups dikha',
            'groups list',
            'discord groups',
            'telegram groups',
        ]
        if any(phrase in lowered for phrase in natural_user_list):
            target_platform = ''
            if 'telegram' in lowered:
                target_platform = 'telegram'
            elif 'discord' in lowered:
                target_platform = 'discord'
            body = f'users {target_platform}'.strip()
            lowered_body = _normalize_natural_admin_text(body)
        elif any(phrase in lowered for phrase in natural_scope_list):
            target_platform = ''
            if 'telegram' in lowered:
                target_platform = 'telegram'
            elif 'discord' in lowered:
                target_platform = 'discord'
            body = f'scopes {target_platform}'.strip()
            lowered_body = _normalize_natural_admin_text(body)
        else:
            return None

    if not body or lowered_body == 'help':
        return (
            "Admin commands:\n"
            "- admin users [platform] [search]\n"
            "- admin scopes [platform]\n"
            "- admin members <platform> <chat_id>\n"
            "- admin history <platform> <user_or_id>\n"
            "- admin send <platform> <user_or_id> :: <message>\n"
            "- admin broadcast <platform|all> :: <message>"
        )

    if lowered_body.startswith('users'):
        parts = body.split(maxsplit=2)
        target_platform = parts[1].lower() if len(parts) > 1 and parts[1].lower() in {'telegram', 'discord', 'whatsapp', 'instagram'} else ''
        search = parts[2] if len(parts) > 2 and target_platform else (parts[1] if len(parts) > 1 and not target_platform else '')
        if not actor_has_permission(actor, 'view_users', target_platform):
            return 'Tumhare paas users dekhne ki permission nahi hai.'
        rows = list_known_contacts(platform=target_platform, query=search)
        return _format_short_list(
            rows,
            lambda i, row: f"{i}. [{row['platform']}] {row['username'] or 'unknown'} | id={row['user_id']} | msgs={row.get('message_count', 0)}",
            'Koi matching user nahi mila.'
        )

    if lowered_body.startswith('scopes'):
        parts = body.split(maxsplit=1)
        target_platform = parts[1].lower() if len(parts) > 1 and parts[1].lower() in {'telegram', 'discord', 'whatsapp', 'instagram'} else ''
        if not actor_has_permission(actor, 'view_scopes', target_platform):
            return 'Tumhare paas scopes dekhne ki permission nahi hai.'
        rows = list_known_scopes(platform=target_platform)
        return _format_short_list(
            rows,
            lambda i, row: f"{i}. [{row['platform']}] {row['chat_title']} | chat_id={row['chat_id']} | msgs={row['message_count']}",
            'Koi scope/group abhi record me nahi hai.'
        )

    if lowered_body.startswith('members '):
        parts = body.split(maxsplit=2)
        if len(parts) < 3:
            return 'Format: admin members <platform> <chat_id>'
        target_platform = parts[1].lower()
        chat_id = parts[2].strip()
        if not actor_has_permission(actor, 'view_scopes', target_platform):
            return 'Tumhare paas members dekhne ki permission nahi hai.'
        rows = list_scope_members(target_platform, chat_id)
        return _format_short_list(
            rows,
            lambda i, row: f"{i}. {row['username']} | id={row['user_id']} | msgs={row['message_count']}",
            'Is scope ke members record me nahi mile.'
        )

    if lowered_body.startswith('history '):
        parts = body.split(maxsplit=2)
        if len(parts) < 3:
            return 'Format: admin history <platform> <user_or_id>'
        target_platform = parts[1].lower()
        target = parts[2].strip()
        if not actor_has_permission(actor, 'view_history', target_platform):
            return 'Tumhare paas history dekhne ki permission nahi hai.'
        contact = resolve_contact(target_platform, target)
        if not contact:
            return f'{target_platform} par `{target}` ka contact nahi mila.'
        rows = get_contact_history(target_platform, contact['user_id'], limit=5)
        if not rows:
            return 'History empty hai.'
        lines = []
        for index, row in enumerate(reversed(rows), start=1):
            lines.append(f"{index}. Q: {row.get('content', '')[:120]} | A: {row.get('response', '')[:120]}")
        return '\n'.join(lines)

    if lowered_body.startswith('send '):
        match = re.match(r'^send\s+(\w+)\s+(.+?)\s*::\s*(.+)$', body, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return 'Format: admin send <platform> <user_or_id> :: <message>'
        target_platform = match.group(1).strip().lower()
        target = match.group(2).strip()
        message = match.group(3).strip()
        if not actor_has_permission(actor, 'send_dm', target_platform):
            return 'Tumhare paas direct message bhejne ki permission nahi hai.'
        contact = resolve_contact(target_platform, target)
        if not contact:
            return f'{target_platform} par `{target}` resolve nahi hua.'
        ok, detail = send_platform_message(target_platform, contact['user_id'], message, recipient_kind='user')
        if ok:
            return f"Message `{contact['username'] or contact['user_id']}` ko {target_platform} par bhej diya."
        return f"Send failed: {detail}"

    if lowered_body.startswith('broadcast '):
        match = re.match(r'^broadcast\s+(\w+)\s*::\s*(.+)$', body, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return 'Format: admin broadcast <platform|all> :: <message>'
        target_platform = match.group(1).strip().lower()
        message = match.group(2).strip()
        if target_platform not in {'all', 'telegram', 'discord'}:
            return 'Broadcast abhi `all`, `telegram`, ya `discord` ke liye support hai.'
        send_platforms = ['telegram', 'discord'] if target_platform == 'all' else [target_platform]
        if not all(actor_has_permission(actor, 'broadcast', platform) for platform in send_platforms):
            return 'Tumhare paas broadcast permission nahi hai.'
        sent = 0
        failed = 0
        for platform in send_platforms:
            for contact in list_known_contacts(platform=platform):
                ok, _detail = send_platform_message(platform, contact['user_id'], message, recipient_kind='user')
                if ok:
                    sent += 1
                else:
                    failed += 1
        return f"Broadcast complete. Sent={sent}, Failed={failed}."

    return "Unknown admin command. `admin help` try karo."
