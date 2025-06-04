from datetime import datetime, timezone
from app.extensions import db

class TagType(db.Model):
    __tablename__ = 'tag_types'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    
    # Constants for easy reference
    SYSTEM = 'system'
    USER = 'user'
    COURSE = 'course'  # New type for course tags
    
    @classmethod
    def get_system_type(cls):
        return cls.query.filter_by(name=cls.SYSTEM).first()
    
    @classmethod
    def get_user_type(cls):
        return cls.query.filter_by(name=cls.USER).first()
    
    @classmethod
    def get_course_type(cls):
        return cls.query.filter_by(name=cls.COURSE).first()
        

class Tag(db.Model):
    __tablename__ = 'tags'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    tag_type_id = db.Column(db.Integer, db.ForeignKey('tag_types.id'), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relationship
    tag_type = db.relationship('TagType', backref=db.backref('tags', lazy='dynamic'))
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "tag_type": self.tag_type.name,
            "description": self.description,
            "created_at": self.created_at.isoformat()
        }

# Association table for many-to-many relationship between Posts and Tags
post_tags = db.Table('post_tags',
    db.Column('post_id', db.Integer, db.ForeignKey('posts.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id'), primary_key=True)
)