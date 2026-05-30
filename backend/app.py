"""
MNI Automation Manager - Main Flask Application
"""
import os
import sys
import sqlite3
import time
import shutil
from collections import defaultdict, deque
from datetime import timedelta
from sqlalchemy import text
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from dotenv import load_dotenv
from backend.database import db
from backend.services.local_data_service import ensure_local_data_sources, ensure_default_data_agents
from backend.services.crawler_service import start_daily_crawler
from backend.services.telegram_polling_service import start_telegram_polling
from backend.services.discord_gateway_service import start_discord_gateway
from backend.services.deployment_settings import DEFAULT_DB_PATH, build_database_diagnostics, resolve_database_uri
from backend.services.secret_store import (
    decrypt_environment_secrets,
    decrypt_config_value,
    decrypt_secret_text,
    encrypt_config_value,
    encrypt_secret_text,
    is_secret_config_key,
    is_encrypted,
)
from backend.models.platform_bot import PlatformBot
from backend.models.config_model import BotConfig, ApiKey
from backend.models.developer_api_client import DeveloperApiClient
from backend.models.auth_user import AuthUser, AuthLoginEvent

load_dotenv()
decrypt_environment_secrets()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_rate_buckets = defaultdict(deque)


def _rate_limit_enabled():
    return os.getenv('RATE_LIMIT_ENABLED', 'true').lower() == 'true'


def _rate_limit_allows():
    if not _rate_limit_enabled():
        return True
    if not request.path.startswith('/api/'):
        return True
    window = int(os.getenv('RATE_LIMIT_WINDOW_SECONDS', '60'))
    limit = int(os.getenv('RATE_LIMIT_MAX_REQUESTS', '120'))
    key = request.headers.get('X-Forwarded-For', request.remote_addr or 'local').split(',')[0].strip()
    now = time.time()
    bucket = _rate_buckets[key]
    while bucket and bucket[0] <= now - window:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def _copy_runtime_placeholder(source, target):
    try:
        if os.path.exists(target) or not os.path.exists(source):
            return
        shutil.copyfile(source, target)
        print(f"[First Run] Created {os.path.relpath(target, PROJECT_ROOT)}")
    except Exception as exc:
        print(f"[First Run] Could not create {target}: {exc}")


def _ensure_first_run_files():
    os.makedirs(os.path.join(PROJECT_ROOT, 'instance'), exist_ok=True)
    os.makedirs(os.path.join(PROJECT_ROOT, 'config'), exist_ok=True)
    _copy_runtime_placeholder(
        os.path.join(PROJECT_ROOT, '.env.example'),
        os.path.join(PROJECT_ROOT, '.env'),
    )
    _copy_runtime_placeholder(
        os.path.join(PROJECT_ROOT, 'config', 'google_auth.example.json'),
        os.path.join(PROJECT_ROOT, 'config', 'google_auth.json'),
    )


def _ensure_sqlite_columns():
    if not str(db.engine.url).startswith('sqlite'):
        return

    migrations = {
        'api_keys': {
            'base_url': "ALTER TABLE api_keys ADD COLUMN base_url TEXT DEFAULT ''",
        },
        'messages': {
            'chat_id': "ALTER TABLE messages ADD COLUMN chat_id VARCHAR(100) DEFAULT ''",
        },
    }

    conn = db.engine.raw_connection()
    try:
        cursor = conn.cursor()
        for table, columns in migrations.items():
            try:
                cursor.execute(f"PRAGMA table_info({table})")
                existing = {row[1] for row in cursor.fetchall()}
            except Exception:
                existing = set()
            for column, statement in columns.items():
                if column not in existing:
                    cursor.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _table_exists(cursor, table_name):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cursor.fetchone() is not None


def _table_columns(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]


def _copy_table_rows(target_path, source_path, table_name):
    if not os.path.exists(source_path) or os.path.abspath(source_path) == os.path.abspath(target_path):
        return 0

    source_conn = sqlite3.connect(source_path)
    target_conn = sqlite3.connect(target_path)
    copied = 0
    try:
        source_cur = source_conn.cursor()
        target_cur = target_conn.cursor()
        if not _table_exists(source_cur, table_name) or not _table_exists(target_cur, table_name):
            return 0

        source_cols = _table_columns(source_cur, table_name)
        target_cols = _table_columns(target_cur, table_name)
        common_cols = [col for col in source_cols if col in target_cols]
        if not common_cols:
            return 0

        rows = source_cur.execute(
            f"SELECT {', '.join(common_cols)} FROM {table_name}"
        ).fetchall()
        if not rows:
            return 0

        placeholders = ', '.join(['?'] * len(common_cols))
        target_cur.executemany(
            f"INSERT OR IGNORE INTO {table_name} ({', '.join(common_cols)}) VALUES ({placeholders})",
            rows,
        )
        target_conn.commit()
        copied = target_conn.total_changes
    finally:
        source_conn.close()
        target_conn.close()
    return copied


def _sync_legacy_sqlite_data():
    if not str(db.engine.url).startswith('sqlite'):
        return

    target_path = db.engine.url.database
    if not target_path:
        return

    source_paths = [
        os.path.join(PROJECT_ROOT, 'backend', 'instance', 'keli_ai.db'),
        os.path.join(os.path.dirname(PROJECT_ROOT), 'instance', 'keli_ai.db'),
    ]
    tables = ['messages', 'users', 'data_agents', 'platform_bots', 'moderation_events', 'outreach_message']
    for source_path in source_paths:
        for table_name in tables:
            try:
                _copy_table_rows(target_path, source_path, table_name)
            except Exception:
                continue


def _ensure_platform_bots():
    if PlatformBot.query.count() > 0:
        return

    config = {row.key: decrypt_config_value(row.key, row.value) for row in BotConfig.query.all()}
    platform_configs = [
        ('telegram', 'Telegram Primary', config.get('telegram_token', '')),
        ('discord', 'Discord Primary', config.get('discord_token', '')),
        ('whatsapp', 'WhatsApp Primary', config.get('whatsapp_access_token', '')),
        ('instagram', 'Instagram Primary', config.get('instagram_access_token', '')),
    ]
    changed = False
    for platform, name, token in platform_configs:
        if not token:
            continue
        db.session.add(PlatformBot(
            name=name,
            platform=platform,
            bot_token=encrypt_secret_text(token),
            extra_config_json='{}',
            is_active=True,
            status='active',
        ))
        changed = True
    if changed:
        db.session.commit()


def _sync_platform_bot_runtime_tokens():
    platform_keys = {
        'telegram': 'telegram_token',
        'discord': 'discord_token',
        'whatsapp': 'whatsapp_access_token',
        'instagram': 'instagram_access_token',
    }
    for platform, config_key in platform_keys.items():
        active_bot = PlatformBot.query.filter_by(platform=platform, is_active=True) \
            .order_by(PlatformBot.updated_at.desc(), PlatformBot.created_at.desc()) \
            .first()
        row = BotConfig.query.filter_by(key=config_key).first()
        if active_bot and active_bot.bot_token:
            token = decrypt_secret_text(active_bot.bot_token)
            if row:
                row.value = encrypt_config_value(config_key, token)
            else:
                db.session.add(BotConfig(key=config_key, value=encrypt_config_value(config_key, token)))
        elif not row:
            db.session.add(BotConfig(key=config_key, value=''))
    db.session.commit()


def _encrypt_existing_secrets():
    changed = False
    for row in BotConfig.query.all():
        if is_secret_config_key(row.key) and row.value and not is_encrypted(row.value):
            row.value = encrypt_config_value(row.key, row.value)
            changed = True
    for key_row in ApiKey.query.all():
        if key_row.api_key and not is_encrypted(key_row.api_key):
            key_row.api_key = encrypt_secret_text(key_row.api_key)
            changed = True
    for bot in PlatformBot.query.all():
        if bot.bot_token and not is_encrypted(bot.bot_token):
            bot.bot_token = encrypt_secret_text(bot.bot_token)
            changed = True
    if changed:
        db.session.commit()


def _seed_default_config():
    existing = BotConfig.query.filter_by(key='personality').first()
    if existing:
        return

    rag_path = os.path.join(PROJECT_ROOT, 'config', 'default_rag.txt')
    try:
        with open(rag_path, 'r', encoding='utf-8') as handle:
            rag_prompt = handle.read()
    except Exception:
        rag_prompt = ''

    defaults = [
        ('personality', 'confident, attitude-driven, slightly flirty, smart and witty'),
        ('rag_prompt', rag_prompt),
        ('tone', 'sharp, smart, engaging'),
        ('text_enabled', 'true'),
        ('image_enabled', 'true'),
        ('video_enabled', 'true'),
        ('voice_enabled', 'true'),
        ('telegram_enabled', 'true'),
        ('discord_enabled', 'true'),
        ('whatsapp_enabled', 'true'),
        ('instagram_enabled', 'true'),
        ('api_usage', 'true'),
        ('load_balancing', 'round_robin'),
    ]
    for key, value in defaults:
        db.session.add(BotConfig(key=key, value=encrypt_config_value(key, value)))
    db.session.commit()


def _background_workers_enabled():
    return os.getenv('MARKANM_DISABLE_BACKGROUND_WORKERS', 'false').lower() != 'true'

def create_app():
    _ensure_first_run_files()
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), '../admin/templates'),
        static_folder=os.path.join(os.path.dirname(__file__), '../admin/static')
    )

    # Config
    app.config['SECRET_KEY'] = os.getenv('ADMIN_SECRET_KEY', 'keli-dev-secret')
    app.config['JWT_SECRET_KEY'] = os.getenv('ADMIN_SECRET_KEY', 'keli-dev-secret')
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=30)
    app.config['SQLALCHEMY_DATABASE_URI'] = resolve_database_uri()
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }
    db_uri = app.config['SQLALCHEMY_DATABASE_URI']
    db_mode = 'Postgres' if str(db_uri).startswith('postgresql://') else 'SQLite'
    db_meta = build_database_diagnostics(db_uri)
    print(f"[DB] Using {db_mode} via {db_meta['source']}: {db_meta['active_uri_masked']}")

    cors_origins = [
        origin.strip()
        for origin in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:5000,http://127.0.0.1:5000').split(',')
        if origin.strip()
    ]
    if os.getenv('FLASK_DEBUG', 'False').lower() == 'true' and os.getenv('CORS_ALLOWED_ORIGINS', '') == '*':
        cors_origins = '*'
    CORS(app, origins=cors_origins)
    db.init_app(app)
    JWTManager(app)

    @app.before_request
    def _apply_basic_rate_limit():
        if not _rate_limit_allows():
            return jsonify({'success': False, 'error': 'Rate limit exceeded'}), 429

    with app.app_context():
        db.create_all()
        _seed_default_config()
        _ensure_sqlite_columns()
        _sync_legacy_sqlite_data()
        ensure_local_data_sources()
        ensure_default_data_agents()
        _encrypt_existing_secrets()
        _ensure_platform_bots()
        _sync_platform_bot_runtime_tokens()
    if _background_workers_enabled():
        start_daily_crawler(app)
        start_telegram_polling(app)
        start_discord_gateway(app)

    # Register blueprints
    from backend.routes.auth import auth_bp
    from backend.routes.admin import admin_bp, sync_telegram_webhooks
    from backend.routes.chat import chat_bp
    from backend.routes.platforms import platforms_bp

    app.register_blueprint(auth_bp,      url_prefix='/api/auth')
    app.register_blueprint(admin_bp,     url_prefix='/api/admin')
    app.register_blueprint(chat_bp,      url_prefix='/api/chat')
    app.register_blueprint(platforms_bp, url_prefix='/api/platforms')
    with app.app_context():
        sync_telegram_webhooks()

    # Admin UI route
    from flask import render_template, redirect, make_response

    def _no_cache_response(template_name):
        response = make_response(render_template(template_name))
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    @app.route('/')
    def index():
        return redirect('/admin')

    @app.route('/admin')
    def admin_login():
        return _no_cache_response('login.html')

    @app.route('/admin/dashboard')
    def admin_dashboard():
        return _no_cache_response('dashboard.html')

    @app.route('/admin/llm-monitor')
    def admin_llm_monitor():
        return redirect('/admin/dashboard?panel=monitor')

    @app.route('/healthz')
    def healthz():
        try:
            db.session.execute(text('SELECT 1'))
            diagnostics = build_database_diagnostics(app.config.get('SQLALCHEMY_DATABASE_URI', ''))
            return {
                'status': 'ok',
                'database_mode': diagnostics['mode'],
                'database_source': diagnostics['source'],
            }
        except Exception as exc:
            return {
                'status': 'error',
                'error': str(exc),
            }, 500

    return app

# Import and run WSGI application
app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('FLASK_PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    print(f"MNI Automation Manager backend starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)
