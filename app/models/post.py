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
    comment_count = db.Column(db.Integer, default=0, nullable=False)
    reaction_count = db.Column(db.Integer, default=0, nullable=False)
    view_count = db.Column(db.Integer, default=0, nullable=False)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    comments = db.relationship('Comment', backref='post', lazy='dynamic', 
                               cascade='all, delete-orphan')
    reactions = db.relationship('Reaction', backref='post', lazy='dynamic',
                                cascade='all, delete-orphan')
    tags = db.relationship('Tag', secondary='post_tags', backref=db.backref('posts', lazy='dynamic'))
    
    # Files relationship - get files associated with this post
    @property
    def files(self):
        from app.models.file import File
        return File.query.filter_by(
            entity_type='post',
            entity_id=self.id,
            is_deleted=False
        ).all()
    
    # Add check constraint for counts
    __table_args__ = (
        db.CheckConstraint('comment_count >= 0 AND reaction_count >= 0 AND view_count >= 0', 
                          name='valid_counts'),
        db.Index('idx_posts_user_id', 'user_id', postgresql_where=db.text('NOT is_deleted')),
        db.Index('idx_posts_created_at', 'created_at', postgresql_where=db.text('NOT is_deleted')),
    )
    
    def to_dict(self, include_content=True, include_tags=False, include_files=False, include_author=True):
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "comment_count": self.comment_count,
            "reaction_count": self.reaction_count,
            "view_count": self.view_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "is_deleted": self.is_deleted,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None
        }
        
        # Include author information
        if include_author and self.author:
            data["author"] = self.author.username
            data["author_avatar"] = self.author.avatar_url  # Use fresh avatar URL
        
        if include_content:
            data["content"] = self.content
            
        if include_tags:
            data["tags"] = [{"tag_name": tag.name, 
                            "isImportant": tag.tag_type == "system", 
                            "tagcolor": "#3498db"} for tag in self.tags]
        
        if include_files:
            data["files"] = [file.to_dict() for file in self.files]
            
        return data
    
    def increment_view(self, user_id=None, ip=None):
        """Increment view count with optional abuse prevention"""
        # Could implement logic to prevent same user/IP repeatedly incrementing, stay current version temply
        self.view_count += 1
