from datetime import datetime
import json

from backend.database import db
from backend.services.secret_store import mask_secret


class DeveloperApiClient(db.Model):
    __tablename__ = 'developer_api_clients'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False)
    api_key = db.Column(db.Text, nullable=False)
    webhook_slug = db.Column(db.String(120), unique=True, nullable=False)
    allowed_features_json = db.Column(db.Text, default='[]')
    allowed_sources_json = db.Column(db.Text, default='[]')
    is_active = db.Column(db.Boolean, default=True)
    last_used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def allowed_features(self):
        try:
            parsed = json.loads(self.allowed_features_json or '[]')
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    def allowed_sources(self):
        try:
            parsed = json.loads(self.allowed_sources_json or '[]')
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'api_key': mask_secret(self.api_key),
            'webhook_slug': self.webhook_slug,
            'allowed_features': self.allowed_features(),
            'allowed_sources': self.allowed_sources(),
            'is_active': self.is_active,
            'last_used_at': self.last_used_at.isoformat() if self.last_used_at else '',
            'created_at': self.created_at.isoformat() if self.created_at else '',
            'updated_at': self.updated_at.isoformat() if self.updated_at else '',
        }
