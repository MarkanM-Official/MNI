from backend.database import db
from datetime import datetime

class User(db.Model):
    __tablename__ = 'users'

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.String(100), nullable=False)
    username    = db.Column(db.String(100), default='unknown')
    platform    = db.Column(db.String(50),  nullable=False)
    is_blocked  = db.Column(db.Boolean,     default=False)
    block_reason= db.Column(db.String(255), default='')
    created_at  = db.Column(db.DateTime,    default=datetime.utcnow)
    last_seen   = db.Column(db.DateTime,    default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'platform', name='uq_user_platform'),)

    def to_dict(self):
        return {
            'id':           self.id,
            'user_id':      self.user_id,
            'username':     self.username,
            'platform':     self.platform,
            'is_blocked':   self.is_blocked,
            'block_reason': self.block_reason,
            'created_at':   self.created_at.isoformat(),
            'last_seen':    self.last_seen.isoformat(),
        }
