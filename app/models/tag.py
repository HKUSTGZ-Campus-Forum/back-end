from datetime import datetime, timezone
from app.extensions import db
from sqlalchemy.dialects.postgresql import ENUM

class TagType:
    SYSTEM = 'system'
    USER = 'user'

class Tag(db.Model):
    __tablename__ = 'tags'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    tag_type = db.Column(ENUM('system', 'user', name='tag_type_enum', create_if_not_exists=True), default=TagType.USER)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "tag_type": self.tag_type,
            "description": self.description,
            "created_at": self.created_at.isoformat()
        }

# Association table for many-to-many relationship between Posts and Tags
post_tags = db.Table('post_tags',
    db.Column('post_id', db.Integer, db.ForeignKey('posts.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id'), primary_key=True)
) 