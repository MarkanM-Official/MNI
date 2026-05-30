from datetime import datetime
from backend.database import db


class AvailabilityRule(db.Model):
    __tablename__ = 'availability_rules'

    id = db.Column(db.Integer, primary_key=True)
    weekday = db.Column(db.Integer, nullable=False)  # 0=Mon ... 6=Sun
    start_time = db.Column(db.String(5), nullable=False)  # HH:MM
    end_time = db.Column(db.String(5), nullable=False)    # HH:MM
    slot_minutes = db.Column(db.Integer, default=30)
    is_enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'weekday': self.weekday,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'slot_minutes': self.slot_minutes,
            'is_enabled': self.is_enabled,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class AppointmentRequest(db.Model):
    __tablename__ = 'appointment_requests'

    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(50), default='telegram')
    user_id = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(100), default='unknown')
    chat_id = db.Column(db.String(100), default='')
    scheduled_for = db.Column(db.DateTime, nullable=False)
    notes = db.Column(db.Text, default='')
    status = db.Column(db.String(20), default='pending')  # pending/approved/rejected/cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'platform': self.platform,
            'user_id': self.user_id,
            'username': self.username,
            'chat_id': self.chat_id,
            'scheduled_for': self.scheduled_for.isoformat() if self.scheduled_for else None,
            'notes': self.notes,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
