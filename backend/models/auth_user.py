from datetime import datetime
from backend.database import db


class AuthUser(db.Model):
    __tablename__ = 'auth_users'

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(30), nullable=False, default='google')
    provider_user_id = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(255), nullable=False, index=True)
    name = db.Column(db.String(160), default='')
    profile_pic = db.Column(db.Text, default='')
    role = db.Column(db.String(40), default='admin')
    is_active = db.Column(db.Boolean, default=True)
    first_login_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, default=datetime.utcnow)
    login_count = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint('provider', 'provider_user_id', name='uq_auth_provider_user'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'provider': self.provider,
            'provider_user_id': self.provider_user_id,
            'email': self.email,
            'name': self.name,
            'profile_pic': self.profile_pic,
            'role': self.role,
            'is_active': self.is_active,
            'first_login_at': self.first_login_at.isoformat() if self.first_login_at else '',
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else '',
            'login_count': self.login_count or 0,
        }


class AuthLoginEvent(db.Model):
    __tablename__ = 'auth_login_events'

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(30), nullable=False, default='google')
    provider_user_id = db.Column(db.String(160), default='')
    email = db.Column(db.String(255), default='', index=True)
    name = db.Column(db.String(160), default='')
    success = db.Column(db.Boolean, default=False)
    reason = db.Column(db.String(255), default='')
    ip_address = db.Column(db.String(80), default='')
    user_agent = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'provider': self.provider,
            'provider_user_id': self.provider_user_id,
            'email': self.email,
            'name': self.name,
            'success': self.success,
            'reason': self.reason,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'created_at': self.created_at.isoformat() if self.created_at else '',
        }
