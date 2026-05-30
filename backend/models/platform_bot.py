from datetime import datetime
from backend.database import db
from backend.services.secret_store import mask_secret


class PlatformBot(db.Model):
    __tablename__ = 'platform_bots'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    platform = db.Column(db.String(50), nullable=False)
    bot_token = db.Column(db.Text, default='')
    extra_config_json = db.Column(db.Text, default='{}')
    is_active = db.Column(db.Boolean, default=True)
    status = db.Column(db.String(20), default='active')  # active/wait/inactive
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'platform': self.platform,
            'bot_token': mask_secret(self.bot_token),
            'extra_config_json': self.extra_config_json,
            'is_active': self.is_active,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
