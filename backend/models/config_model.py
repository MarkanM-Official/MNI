from backend.database import db
from datetime import datetime
from backend.services.secret_store import mask_secret

class BotConfig(db.Model):
    __tablename__ = 'bot_config'

    id         = db.Column(db.Integer,     primary_key=True)
    key        = db.Column(db.String(100), unique=True, nullable=False)
    value      = db.Column(db.Text,        nullable=False)
    updated_at = db.Column(db.DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {'key': self.key, 'value': self.value}


class ApiKey(db.Model):
    __tablename__ = 'api_keys'

    id         = db.Column(db.Integer,     primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    category   = db.Column(db.String(50),  nullable=False)  # text/image/video/voice
    provider   = db.Column(db.String(100), nullable=False)
    api_key    = db.Column(db.Text,        nullable=False)
    base_url   = db.Column(db.Text,        default='')
    is_active  = db.Column(db.Boolean,     default=True)
    is_primary = db.Column(db.Boolean,     default=False)
    priority   = db.Column(db.Integer,     default=1)
    fail_count = db.Column(db.Integer,     default=0)
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':         self.id,
            'name':       self.name,
            'category':   self.category,
            'provider':   self.provider,
            'api_key':    mask_secret(self.api_key),
            'base_url':   self.base_url,
            'is_active':  self.is_active,
            'is_primary': self.is_primary,
            'priority':   self.priority,
            'fail_count': self.fail_count,
        }
