from datetime import datetime
from backend.database import db


class DataAgent(db.Model):
    __tablename__ = 'data_agents'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(120), default='')
    source_url = db.Column(db.Text, default='')
    mode = db.Column(db.String(20), default='wait')  # active/wait/inactive
    last_status = db.Column(db.String(20), default='idle')
    last_snapshot = db.Column(db.Text, default='')
    last_fetched_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'role': self.role,
            'source_url': self.source_url,
            'mode': self.mode,
            'last_status': self.last_status,
            'last_snapshot': self.last_snapshot,
            'last_fetched_at': self.last_fetched_at.isoformat() if self.last_fetched_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
