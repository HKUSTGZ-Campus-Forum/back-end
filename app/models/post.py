from datetime import datetime, timezone
from app.extensions import db
from sqlalchemy.dialects.postgresql import JSONB

class Post(db.Model):
    __tablename__ = 'posts'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    embedding = db.Column(JSONB)  # Store embedding as JSON in PostgreSQL
    comment_count = db.Column(db.Integer, default=0)
    reaction_count = db.Column(db.Integer, default=0)
    views_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    comments = db.relationship('Comment', backref='post', lazy='dynamic', 
                               cascade='all, delete-orphan')
    reactions = db.relationship('Reaction', backref='post', lazy='dynamic',
                                cascade='all, delete-orphan')
    tags = db.relationship('Tag', secondary='post_tags', backref=db.backref('posts', lazy='dynamic'))
    
    def to_dict(self, include_content=True):
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "comment_count": self.comment_count,
            "reaction_count": self.reaction_count,
            "views_count": self.views_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        
        if include_content:
            data["content"] = self.content
            
        return data
    
    def increment_view(self):
        self.views_count += 1
