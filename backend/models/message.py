from backend.database import db
from datetime import datetime

class Message(db.Model):
    __tablename__ = 'messages'

    id           = db.Column(db.Integer,     primary_key=True)
    user_id      = db.Column(db.String(100), nullable=False)
    username     = db.Column(db.String(100), default='unknown')
    platform     = db.Column(db.String(50),  nullable=False)
    chat_id      = db.Column(db.String(100), default='')
    message_type = db.Column(db.String(20),  default='text')   # text/image/video/voice
    content      = db.Column(db.Text,        nullable=False)
    response     = db.Column(db.Text,        default='')
    api_used     = db.Column(db.String(100), default='')
    status       = db.Column(db.String(20),  default='ok')     # ok/blocked/error/disabled
    timestamp    = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':           self.id,
            'user_id':      self.user_id,
            'username':     self.username,
            'platform':     self.platform,
            'chat_id':      self.chat_id,
            'message_type': self.message_type,
            'content':      self.content,
            'response':     self.response,
            'api_used':     self.api_used,
            'status':       self.status,
            'timestamp':    self.timestamp.isoformat(),
        }
