from datetime import datetime
from backend.database import db


class BlogPost(db.Model):
    __tablename__ = 'blog_posts'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False)
    summary = db.Column(db.Text, nullable=False, default='')
    content = db.Column(db.Text, nullable=False, default='')
    seo_title = db.Column(db.String(255), nullable=False, default='')
    seo_description = db.Column(db.Text, nullable=False, default='')
    tags = db.Column(db.Text, nullable=False, default='')
    status = db.Column(db.String(30), nullable=False, default='draft')
    is_enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'slug': self.slug,
            'summary': self.summary,
            'content': self.content,
            'seo_title': self.seo_title,
            'seo_description': self.seo_description,
            'tags': self.tags,
            'status': self.status,
            'is_enabled': self.is_enabled,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
