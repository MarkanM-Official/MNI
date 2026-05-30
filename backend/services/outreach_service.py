"""
MNI — Outreach Service
Sends bulk messages to users on different platforms.
"""
import os
from backend.database import db
from backend.models.user import User
from backend.models.outreach import OutreachMessage
from backend.routes.platforms import send_telegram_message, send_discord_message
from backend.services.runtime_config import get_config_value

def get_config(key, default=''):
    return get_config_value(key, default)

def deliver_outreach_message(message_id):
    """
    Fetches users and sends the outreach message based on the target platform.
    """
    msg = OutreachMessage.query.get(message_id)
    if not msg or msg.status != 'draft':
        return {'success': False, 'error': 'Message not found or already sent.'}

    target_platform = msg.target_platform
    content = msg.message_content
    
    query = User.query.filter_by(is_blocked=False)
    if target_platform != 'all':
        query = query.filter_by(platform=target_platform)
    
    users_to_message = query.all()
    sent_count, failed_count = 0, 0

    for user in users_to_message:
        try:
            if user.platform == 'telegram':
                token = get_config('telegram_token') or os.getenv('TELEGRAM_BOT_TOKEN', '')
                send_telegram_message(user.user_id, content, token)
                sent_count += 1
        except Exception as e:
            failed_count += 1

    msg.status = 'sent' if sent_count > 0 else 'failed'
    msg.sent_at = db.func.now()
    db.session.commit()
    return {'success': True, 'message': f"Outreach sent to {sent_count}/{len(users_to_message)} users."}
