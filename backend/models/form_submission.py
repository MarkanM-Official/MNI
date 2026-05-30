from datetime import datetime
from backend.database import db


class FormSubmission(db.Model):
    __tablename__ = 'form_submissions'

    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(50), default='telegram')
    user_id = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(100), default='unknown')
    chat_id = db.Column(db.String(100), default='')
    form_title = db.Column(db.String(200), default='Application Form')
    answers_json = db.Column(db.Text, default='{}')
    status = db.Column(db.String(20), default='pending')  # pending/approved/rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'platform': self.platform,
            'user_id': self.user_id,
            'username': self.username,
            'chat_id': self.chat_id,
            'form_title': self.form_title,
            'answers_json': self.answers_json,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
