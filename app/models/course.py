from datetime import datetime, timezone
from app.extensions import db
from app.models.tag import Tag, TagType

class Course(db.Model):
    __tablename__ = 'courses'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False, unique=True)  # e.g., "UCUG1001"
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    instructor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    credits = db.Column(db.Integer, nullable=False)
    capacity = db.Column(db.Integer)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    instructor = db.relationship('User', backref=db.backref('instructed_courses', lazy='dynamic'))

    # Constraints
    __table_args__ = (
        db.CheckConstraint('credits > 0', name='valid_credits'),
        db.CheckConstraint('capacity IS NULL OR capacity > 0', name='valid_capacity'),
        db.Index('idx_courses_code_active', 'code', postgresql_where=db.text('is_active IS TRUE AND is_deleted IS FALSE')),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "instructor_id": self.instructor_id,
            "credits": self.credits,
            "capacity": self.capacity,
            "is_active": self.is_active,
            "is_deleted": self.is_deleted,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

    def create_semester_tag(self, semester):
        """Create a course tag for a specific semester"""
        tag_name = f"{self.code}-{semester}"
        
        # Check if tag already exists
        existing_tag = Tag.query.filter_by(name=tag_name).first()
        if existing_tag:
            return existing_tag
            
        # Get course tag type
        course_type = TagType.get_course_type()
        if not course_type:
            # Create course type if it doesn't exist
            course_type = TagType(name=TagType.COURSE)
            db.session.add(course_type)
            db.session.flush()
        
        # Create new tag
        tag = Tag(
            name=tag_name,
            tag_type_id=course_type.id,
            description=f"Tag for {self.name} ({semester})"
        )
        db.session.add(tag)
        db.session.commit()
        
        return tag

    @classmethod
    def get_course_by_tag(cls, tag_name):
        """Get course information from a course tag name"""
        try:
            code, semester = tag_name.split('-', 1)
            return cls.query.filter_by(code=code, is_deleted=False).first()
        except ValueError:
            return None

    @classmethod
    def get_course_tags(cls, code=None, semester=None):
        """Get all course tags, optionally filtered by code and/or semester"""
        query = Tag.query.join(TagType).filter(TagType.name == TagType.COURSE)
        
        if code:
            query = query.filter(Tag.name.like(f"{code}-%"))
        if semester:
            query = query.filter(Tag.name.like(f"%-{semester}"))
            
        return query.all() 