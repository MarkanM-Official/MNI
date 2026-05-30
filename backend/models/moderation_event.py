from datetime import datetime
from backend.database import db


class ModerationEvent(db.Model):
    __tablename__ = 'moderation_events'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(100), default='unknown')
    platform = db.Column(db.String(50), nullable=False)
    chat_id = db.Column(db.String(100), default='')
    action = db.Column(db.String(30), nullable=False, default='warn')
    reason = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'platform': self.platform,
            'chat_id': self.chat_id,
            'action': self.action,
            'reason': self.reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
