"""
Discord Gateway worker for DM and mention-based bot replies.
"""
import asyncio
import contextlib
import json
import logging
import threading

import aiohttp

from backend.models.config_model import BotConfig
from backend.models.platform_bot import PlatformBot
from backend.routes.platforms import handle_discord_message_event
from backend.services.runtime_config import get_config_value
from backend.services.secret_store import decrypt_secret_text


GATEWAY_URL = 'wss://gateway.discord.gg/?v=10&encoding=json'
# Use a conservative intent set so DMs work even when privileged message-content
# intent is not approved in the Discord portal yet.
INTENTS = 1 | 512 | 4096  # guilds, guild messages, DMs


def _get_config(key, default=''):
    return get_config_value(key, default)


def _active_discord_bots():
    bots = PlatformBot.query.filter_by(platform='discord', is_active=True).order_by(
        PlatformBot.updated_at.desc(), PlatformBot.created_at.desc()
    ).all()
    payload = []
    seen_tokens = set()
    for bot in bots:
        token = (bot.bot_token or '').strip()
        token = decrypt_secret_text(token)
        if not token or token in seen_tokens:
            continue
        seen_tokens.add(token)
        payload.append({
            'id': bot.id,
            'name': bot.name,
            'token': token,
        })

    fallback = (_get_config('discord_token', '') or '').strip()
    if fallback and fallback not in seen_tokens:
        payload.append({
            'id': 0,
            'name': 'Discord Primary',
            'token': fallback,
        })
    return payload


async def _heartbeat(ws, interval_ms, sequence_ref):
    try:
        while True:
            await asyncio.sleep(interval_ms / 1000.0)
            await ws.send_json({'op': 1, 'd': sequence_ref.get('value')})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logging.error(f"[Discord Gateway] Heartbeat failed: {exc}")


async def _run_bot_session(app, token, bot_name):
    headers = {'Authorization': f'Bot {token}'}
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=None)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(GATEWAY_URL, heartbeat=0) as ws:
            hello = await ws.receive_json()
            heartbeat_interval = (hello.get('d') or {}).get('heartbeat_interval', 45000)
            sequence_ref = {'value': None}
            heartbeat_task = asyncio.create_task(_heartbeat(ws, heartbeat_interval, sequence_ref))
            bot_user_id = ''
            try:
                await ws.send_json({
                    'op': 2,
                    'd': {
                        'token': token,
                        'intents': INTENTS,
                        'properties': {
                            'os': 'linux',
                            'browser': 'mni-automation-manager',
                            'device': 'mni-automation-manager',
                        },
                    },
                })

                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    payload = json.loads(msg.data)
                    op = payload.get('op')
                    t = payload.get('t')
                    d = payload.get('d') or {}
                    sequence_ref['value'] = payload.get('s')

                    if op == 7:
                        raise RuntimeError('Discord requested reconnect')
                    if op == 9:
                        raise RuntimeError('Discord invalid session')
                    if op == 1:
                        await ws.send_json({'op': 1, 'd': sequence_ref.get('value')})
                        continue

                    if t == 'READY':
                        user = d.get('user') or {}
                        bot_user_id = str(user.get('id', '')).strip()
                        logging.info(f"[Discord Gateway] Ready for {bot_name} ({user.get('username', 'bot')})")
                        continue

                    if t == 'MESSAGE_CREATE':
                        with app.app_context():
                            handle_discord_message_event(d, token_override=token, bot_user_id=bot_user_id)
            finally:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await heartbeat_task


async def _gateway_supervisor(app):
    while True:
        try:
            with app.app_context():
                discord_enabled = _get_config('discord_enabled', 'true').lower() == 'true'
                bots = _active_discord_bots()
            if not discord_enabled or not bots:
                await asyncio.sleep(5)
                continue

            tasks = [asyncio.create_task(_run_bot_session(app, bot['token'], bot['name'])) for bot in bots]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc:
                    logging.error(f"[Discord Gateway] Worker stopped: {exc}")
            for task in pending:
                task.cancel()
            await asyncio.sleep(5)
        except Exception as exc:
            logging.error(f"[Discord Gateway] Supervisor failed: {exc}")
            await asyncio.sleep(5)


def start_discord_gateway(app):
    def runner():
        asyncio.run(_gateway_supervisor(app))

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread
