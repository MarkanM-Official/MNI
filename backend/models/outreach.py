from backend.database import db
from datetime import datetime

class OutreachMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    target_platform = db.Column(db.String(50), nullable=False) # 'all', 'telegram', 'discord', etc.
    message_content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default='draft', nullable=False) # 'draft', 'sent', 'failed'
    sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'target_platform': self.target_platform,
            'message_content': self.message_content,
            'status': self.status,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }