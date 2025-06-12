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
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), 
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Self-referential relationship for nested comments
    replies = db.relationship(
        'Comment',
        foreign_keys=[parent_comment_id],
        backref=db.backref('parent', remote_side=[id]),
        lazy='dynamic'
    )
    reactions = db.relationship('Reaction', backref='comment', lazy='dynamic')
    
    def to_dict(self, include_author=True):
        data = {
            "id": self.id,
            "post_id": self.post_id,
            "user_id": self.user_id,
            "content": self.content,
            "parent_comment_id": self.parent_comment_id,
            "is_deleted": self.is_deleted,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
        
        # Include author information
        if include_author and self.author:
            data["author"] = self.author.username
            data["author_avatar"] = self.author.avatar_url  # Use fresh avatar URL
            
        return data
    
    # Add these constraints to the model
    __table_args__ = (
        db.Index(
            'idx_comments_post_id_active',
            'post_id',
            postgresql_where=db.text("is_deleted IS FALSE")
        ),
        db.CheckConstraint(
            'parent_comment_id IS NULL OR parent_comment_id != id',
            name='ck_comments_valid_parent'
        ),
    )
