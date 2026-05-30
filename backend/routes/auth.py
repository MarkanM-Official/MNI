"""
MNI — Auth Routes (Admin Login)
"""
import os
import re
from datetime import datetime
import requests
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required
from werkzeug.security import check_password_hash, generate_password_hash
from backend.database import db
from backend.models.auth_user import AuthUser, AuthLoginEvent
from backend.models.config_model import BotConfig
from backend.services.secret_store import encrypt_config_value
from backend.services.runtime_config import get_config_value

auth_bp = Blueprint('auth', __name__)

BOOTSTRAP_USERNAME = 'admin'
BOOTSTRAP_PASSWORD = 'admin@123@321'
AUTH_METHODS = {
    'password',
    'google_oauth',
    'password_google_oauth',
}


def _config_value(key, default=''):
    return get_config_value(key, default)


def _set_config(key, value):
    row = BotConfig.query.filter_by(key=key).first()
    stored_value = encrypt_config_value(key, value)
    if row:
        row.value = stored_value
    else:
        db.session.add(BotConfig(key=key, value=stored_value))
    db.session.commit()


def _admin_setup_complete():
    return _config_value('admin_setup_complete', 'false').lower() == 'true'


def _admin_username():
    return _config_value('admin_username', BOOTSTRAP_USERNAME) or BOOTSTRAP_USERNAME


def _auth_method():
    method = _config_value('admin_auth_method', 'password')
    return method if method in AUTH_METHODS else 'password'


def _stored_hash():
    return _config_value('admin_password_hash', '')


def _password_ok(username, password):
    username = str(username or '').strip()
    password = str(password or '')
    if username != _admin_username():
        return False
    stored_hash = _stored_hash()
    if stored_hash:
        return check_password_hash(stored_hash, password)
    fallback = os.getenv('ADMIN_PASSWORD', '')
    return bool(fallback and password == fallback)


def _auth_settings_payload():
    method = _auth_method()
    google_client_id = _google_login_client_id()
    allowed_emails = _google_allowed_emails()
    return {
        'username': _admin_username(),
        'auth_method': method,
        'password_enabled': bool(_stored_hash() or os.getenv('ADMIN_PASSWORD', '')),
        'google_login_enabled': bool(google_client_id),
        'google_login_client_id': google_client_id,
        'google_allowed_emails': ', '.join(allowed_emails),
        'google_user_count': AuthUser.query.filter_by(provider='google').count(),
    }


def _public_auth_settings_payload():
    method = _auth_method()
    google_client_id = _google_login_client_id()
    return {
        'username': _admin_username(),
        'auth_method': method,
        'password_enabled': bool(_stored_hash() or os.getenv('ADMIN_PASSWORD', '')),
        'google_login_enabled': bool(google_client_id),
        'google_login_client_id': google_client_id,
    }


def _google_login_client_id():
    return (
        _config_value('admin_google_client_id', '')
        or _config_value('google_client_id', '')
        or os.getenv('GOOGLE_CLIENT_ID', '')
    ).strip()


def _google_allowed_emails():
    raw = _config_value('admin_google_allowed_emails', '') or os.getenv('GOOGLE_ALLOWED_EMAILS', '')
    return [item.strip().lower() for item in re.split(r'[\s,;]+', raw) if item.strip()]


def _password_required_for_method(method):
    return method in {'password', 'password_google_oauth'}


def _google_oauth_allowed_for_method(method):
    return method in {'google_oauth', 'password_google_oauth'}


def _verify_google_credential(credential):
    client_id = _google_login_client_id()
    if not client_id:
        return None, 'Google Client ID is not configured'
    if not credential:
        return None, 'Google credential missing'

    try:
        response = requests.get(
            'https://oauth2.googleapis.com/tokeninfo',
            params={'id_token': credential},
            timeout=8,
        )
        if response.status_code != 200:
            return None, 'Google token verification failed'
        payload = response.json()
    except Exception:
        return None, 'Google verification service is unreachable'

    if payload.get('aud') != client_id:
        return None, 'Google token was issued for a different Client ID'
    if payload.get('iss') not in {'accounts.google.com', 'https://accounts.google.com'}:
        return None, 'Google token issuer is invalid'
    if str(payload.get('email_verified', '')).lower() not in {'true', '1'}:
        return None, 'Google email is not verified'
    if not payload.get('sub') or not payload.get('email'):
        return None, 'Google token does not include required user details'
    return payload, ''


def _record_login_event(payload=None, success=False, reason=''):
    payload = payload or {}
    event = AuthLoginEvent(
        provider='google',
        provider_user_id=str(payload.get('sub') or ''),
        email=str(payload.get('email') or '').lower(),
        name=str(payload.get('name') or ''),
        success=bool(success),
        reason=str(reason or '')[:255],
        ip_address=request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip(),
        user_agent=request.headers.get('User-Agent', '')[:1000],
    )
    db.session.add(event)
    db.session.commit()


def _upsert_google_auth_user(payload):
    now = datetime.utcnow()
    provider_user_id = str(payload.get('sub') or '')
    email = str(payload.get('email') or '').lower()
    user = AuthUser.query.filter_by(provider='google', provider_user_id=provider_user_id).first()
    if not user:
        user = AuthUser(
            provider='google',
            provider_user_id=provider_user_id,
            email=email,
            first_login_at=now,
            login_count=0,
        )
        db.session.add(user)
    user.email = email
    user.name = str(payload.get('name') or payload.get('given_name') or email)
    user.profile_pic = str(payload.get('picture') or '')
    user.last_login_at = now
    user.login_count = (user.login_count or 0) + 1
    db.session.commit()
    return user


def _google_user_allowed(email, provider_user_id):
    email = str(email or '').lower()
    allowed = _google_allowed_emails()
    existing = AuthUser.query.filter_by(provider='google', provider_user_id=provider_user_id, is_active=True).first()
    if existing:
        return True
    if allowed:
        return email in allowed
    return False


def _login_allowed(data):
    method = _auth_method()
    username = data.get('username', '')
    password = data.get('password', '')
    password_ok = _password_ok(username, password)

    if method == 'password':
        return password_ok
    return False


@auth_bp.route('/google-login', methods=['POST'])
def google_login():
    data = request.get_json() or {}
    payload, error = _verify_google_credential(str(data.get('credential', '')).strip())
    method = _auth_method()

    if not _admin_setup_complete():
        _record_login_event(payload, False, 'setup required')
        return jsonify({'success': False, 'message': 'First-time setup required before Google login.'}), 403
    if not _google_oauth_allowed_for_method(method):
        _record_login_event(payload, False, 'google oauth disabled')
        return jsonify({'success': False, 'message': 'Google button login is not enabled in Access & Login.'}), 403
    if error:
        _record_login_event(payload, False, error)
        return jsonify({'success': False, 'message': error}), 401
    if method == 'password_google_oauth' and not _password_ok(data.get('username', ''), data.get('password', '')):
        _record_login_event(payload, False, 'password check failed')
        return jsonify({'success': False, 'message': 'Password is required with Google login.'}), 401

    email = str(payload.get('email') or '').lower()
    provider_user_id = str(payload.get('sub') or '')
    if not _google_user_allowed(email, provider_user_id):
        _record_login_event(payload, False, 'email not allowed')
        return jsonify({'success': False, 'message': 'This Google account is not allowed for MNI admin login.'}), 403

    user = _upsert_google_auth_user(payload)
    _record_login_event(payload, True, 'login ok')
    token = create_access_token(identity=f'google:{user.id}')
    return jsonify({
        'success': True,
        'token': token,
        'user': user.to_dict(),
        'settings': _auth_settings_payload(),
    })


@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}

    if not _admin_setup_complete():
        return jsonify({
            'success': False,
            'message': 'First-time setup required before login.',
        }), 403

    if _login_allowed(data):
        token = create_access_token(identity='admin')
        return jsonify({'success': True, 'token': token, 'settings': _auth_settings_payload()})

    return jsonify({'success': False, 'message': 'Login details did not match the selected access mode.'}), 401


@auth_bp.route('/setup-status', methods=['GET'])
def setup_status():
    return jsonify({
        'setup_complete': _admin_setup_complete(),
        'bootstrap_username': BOOTSTRAP_USERNAME if not _admin_setup_complete() else '',
        'bootstrap_password': BOOTSTRAP_PASSWORD if not _admin_setup_complete() else '',
        'settings': _public_auth_settings_payload() if _admin_setup_complete() else {},
    })


@auth_bp.route('/register', methods=['POST'])
def register_admin():
    if _admin_setup_complete():
        return jsonify({'success': False, 'message': 'Admin already configured'}), 400

    data = request.get_json() or {}
    bootstrap_username = str(data.get('bootstrap_username', '')).strip()
    bootstrap_password = str(data.get('bootstrap_password', '')).strip()
    username = str(data.get('username', BOOTSTRAP_USERNAME)).strip() or BOOTSTRAP_USERNAME
    password = str(data.get('password', '')).strip()
    auth_method = str(data.get('auth_method', 'password')).strip()
    google_client_id = str(data.get('google_client_id', '')).strip()
    google_allowed_emails = str(data.get('google_allowed_emails', '')).strip()

    if bootstrap_username != BOOTSTRAP_USERNAME or bootstrap_password != BOOTSTRAP_PASSWORD:
        return jsonify({'success': False, 'message': 'Default first-time username/password did not match'}), 401
    if auth_method not in AUTH_METHODS:
        return jsonify({'success': False, 'message': 'Invalid login method'}), 400
    needs_password = _password_required_for_method(auth_method)
    if needs_password and len(password) < 8:
        return jsonify({'success': False, 'message': 'Permanent password must be at least 8 characters'}), 400
    if auth_method in {'google_oauth', 'password_google_oauth'} and not (google_client_id or _google_login_client_id()):
        return jsonify({'success': False, 'message': 'Google Client ID required for Google button login'}), 400
    if auth_method in {'google_oauth', 'password_google_oauth'} and not google_allowed_emails:
        return jsonify({'success': False, 'message': 'Allowed Google email is required so unknown accounts cannot login'}), 400

    _set_config('admin_username', username)
    if password:
        _set_config('admin_password_hash', generate_password_hash(password))
    _set_config('admin_auth_method', auth_method)
    if google_client_id:
        _set_config('admin_google_client_id', google_client_id)
    if google_allowed_emails:
        _set_config('admin_google_allowed_emails', google_allowed_emails)
    _set_config('admin_setup_complete', 'true')

    token = create_access_token(identity='admin')
    return jsonify({'success': True, 'token': token, 'message': 'MNI access configured', 'settings': _auth_settings_payload()})


@auth_bp.route('/settings', methods=['GET'])
@jwt_required()
def auth_settings():
    return jsonify(_auth_settings_payload())


@auth_bp.route('/settings', methods=['POST'])
@jwt_required()
def update_auth_settings():
    data = request.get_json() or {}
    username = str(data.get('username', _admin_username())).strip() or _admin_username()
    auth_method = str(data.get('auth_method', _auth_method())).strip()
    password = str(data.get('password', '')).strip()
    google_client_id = str(data.get('google_client_id', '')).strip()
    google_allowed_emails = str(data.get('google_allowed_emails', '')).strip()

    if auth_method not in AUTH_METHODS:
        return jsonify({'success': False, 'message': 'Invalid login method'}), 400
    needs_password = _password_required_for_method(auth_method)
    if needs_password and not password and not _stored_hash():
        return jsonify({'success': False, 'message': 'Set a password for this login method'}), 400
    if password and len(password) < 8:
        return jsonify({'success': False, 'message': 'Password must be at least 8 characters'}), 400
    if auth_method in {'google_oauth', 'password_google_oauth'} and not (google_client_id or _google_login_client_id()):
        return jsonify({'success': False, 'message': 'Set Google Client ID for Google button login'}), 400
    if auth_method in {'google_oauth', 'password_google_oauth'} and not google_allowed_emails:
        return jsonify({'success': False, 'message': 'Add at least one allowed Google email for Google button login'}), 400

    _set_config('admin_username', username)
    _set_config('admin_auth_method', auth_method)
    if password:
        _set_config('admin_password_hash', generate_password_hash(password))
    if google_client_id:
        _set_config('admin_google_client_id', google_client_id)
    _set_config('admin_google_allowed_emails', google_allowed_emails)
    return jsonify({'success': True, 'settings': _auth_settings_payload()})


@auth_bp.route('/google-logins', methods=['GET'])
@jwt_required()
def google_logins():
    users = AuthUser.query.filter_by(provider='google').order_by(AuthUser.last_login_at.desc()).limit(50).all()
    events = AuthLoginEvent.query.filter_by(provider='google').order_by(AuthLoginEvent.created_at.desc()).limit(100).all()
    return jsonify({
        'users': [user.to_dict() for user in users],
        'events': [event.to_dict() for event in events],
    })


@auth_bp.route('/verify', methods=['GET'])
def verify():
    from flask_jwt_extended import verify_jwt_in_request
    try:
        verify_jwt_in_request()
        return jsonify({'valid': True})
    except Exception:
        return jsonify({'valid': False}), 401
