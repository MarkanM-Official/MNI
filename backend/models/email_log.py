from datetime import datetime
from backend.database import db


class EmailLog(db.Model):
    __tablename__ = 'email_logs'

    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(255), default='')
    recipients = db.Column(db.Text, default='')
    body = db.Column(db.Text, default='')
    status = db.Column(db.String(20), default='sent')
    detail = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'subject': self.subject,
            'recipients': self.recipients,
            'body': self.body,
            'status': self.status,
            'detail': self.detail,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
