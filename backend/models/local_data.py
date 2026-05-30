from datetime import datetime
from backend.database import db


class LocalDataSource(db.Model):
    __tablename__ = 'local_data_sources'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    source_type = db.Column(db.String(50), nullable=False, default='external')
    description = db.Column(db.Text, nullable=False, default='')
    is_enabled = db.Column(db.Boolean, default=False)
    endpoint = db.Column(db.Text, nullable=False, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'key': self.key,
            'name': self.name,
            'source_type': self.source_type,
            'description': self.description,
            'is_enabled': self.is_enabled,
            'endpoint': self.endpoint,
        }
