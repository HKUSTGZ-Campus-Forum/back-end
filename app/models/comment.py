from datetime import datetime, timezone
from app.extensions import db
from sqlalchemy.dialects.postgresql import JSONB

class Comment(db.Model):
    __tablename__ = 'comments'
    
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    embedding = db.Column(JSONB)  # Store embedding as JSON in PostgreSQL
    parent_comment_id = db.Column(db.Integer, db.ForeignKey('comments.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), 
                           onupdate=lambda: datetime.now(timezone.utc))

    # Self-referential relationship for nested comments
    replies = db.relationship(
        'Comment',
        backref=db.backref('parent', remote_side=[id]),
        lazy='dynamic'
    )
    reactions = db.relationship('Reaction', backref='comment', lazy='dynamic')
    
    def to_dict(self):
        return {
            "id": self.id,
            "post_id": self.post_id,
            "user_id": self.user_id,
            "content": self.content,
            "parent_comment_id": self.parent_comment_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
