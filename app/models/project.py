from datetime import datetime, timezone
from app.extensions import db
from sqlalchemy.dialects.postgresql import JSONB

class Project(db.Model):
    __tablename__ = 'projects'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # Project creator

    # Basic project information
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    goal = db.Column(db.Text)  # What the project aims to achieve

    # Technical requirements
    required_skills = db.Column(JSONB, default=list)  # List of required skill names
    preferred_skills = db.Column(JSONB, default=list)  # Nice-to-have skills
    project_type = db.Column(db.String(50))  # web, mobile, research, hardware, etc.
    difficulty_level = db.Column(db.String(20))  # beginner, intermediate, advanced
    duration_estimate = db.Column(db.String(50))  # "1-2 weeks", "1 month", "semester-long"

    # Team requirements
    team_size_min = db.Column(db.Integer, default=1)
    team_size_max = db.Column(db.Integer, default=5)
    looking_for_roles = db.Column(JSONB, default=list)  # List of roles needed

    # Project status and metadata
    status = db.Column(db.String(20), default='recruiting')  # recruiting, active, completed, cancelled
    view_count = db.Column(db.Integer, default=0, nullable=False)
    interest_count = db.Column(db.Integer, default=0, nullable=False)  # Number of applications/interests

    # Contact and collaboration preferences
    collaboration_method = db.Column(db.String(50))  # remote, in-person, hybrid
    meeting_frequency = db.Column(db.String(50))  # daily, weekly, as-needed
    communication_tools = db.Column(JSONB, default=list)  # slack, discord, email, etc.

    # Semantic search
    embedding = db.Column(JSONB)  # Store embedding vector for similarity search

    # Timestamps
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    creator = db.relationship('User', backref=db.backref('created_projects', lazy='dynamic'))
    applications = db.relationship('ProjectApplication', backref='project', lazy='dynamic', cascade='all, delete-orphan')

    # Constants for status
    STATUS_RECRUITING = 'recruiting'
    STATUS_ACTIVE = 'active'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELLED = 'cancelled'

    # Indexes for performance
    __table_args__ = (
        db.Index('idx_projects_user_id', 'user_id'),
        db.Index('idx_projects_status', 'status'),
        db.Index('idx_projects_created_at', 'created_at'),
        db.Index('idx_projects_type', 'project_type'),
        db.Index('idx_projects_difficulty', 'difficulty_level'),
        db.Index('idx_projects_active', 'is_deleted', 'status'),
        db.CheckConstraint('team_size_min <= team_size_max', name='check_team_size'),
        db.CheckConstraint('view_count >= 0 AND interest_count >= 0', name='check_positive_counts'),
    )

    def get_text_representation(self):
        """Generate text representation for embedding"""
        parts = []

        parts.append(f"Title: {self.title}")
        parts.append(f"Description: {self.description}")

        if self.goal:
            parts.append(f"Goal: {self.goal}")

        if self.required_skills:
            parts.append(f"Required Skills: {', '.join(self.required_skills)}")

        if self.preferred_skills:
            parts.append(f"Preferred Skills: {', '.join(self.preferred_skills)}")

        if self.project_type:
            parts.append(f"Type: {self.project_type}")

        if self.difficulty_level:
            parts.append(f"Difficulty: {self.difficulty_level}")

        if self.looking_for_roles:
            parts.append(f"Looking for: {', '.join(self.looking_for_roles)}")

        return " | ".join(parts)

    def update_embedding(self, embedding_vector):
        """Update the embedding vector"""
        self.embedding = embedding_vector
        self.updated_at = datetime.now(timezone.utc)

    def increment_view(self):
        """Increment view count"""
        self.view_count += 1

    def increment_interest(self):
        """Increment interest count when someone applies"""
        self.interest_count += 1

    def get_current_team_size(self):
        """Get current number of accepted team members"""
        return self.applications.filter_by(status='accepted').count() + 1  # +1 for creator

    def is_recruiting(self):
        """Check if project is still recruiting"""
        return (self.status == self.STATUS_RECRUITING and
                not self.is_deleted and
                self.get_current_team_size() < self.team_size_max)

    def can_user_apply(self, user_id):
        """Check if user can apply to this project"""
        if self.user_id == user_id:  # Creator can't apply to own project
            return False

        if not self.is_recruiting():
            return False

        # Check if user already applied
        existing_application = self.applications.filter_by(user_id=user_id).first()
        return existing_application is None

    def to_dict(self, include_creator=True, include_applications=False, include_embedding=False, current_user_id=None):
        """Convert to dictionary for API responses"""
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "description": self.description,
            "goal": self.goal,
            "required_skills": self.required_skills or [],
            "preferred_skills": self.preferred_skills or [],
            "project_type": self.project_type,
            "difficulty_level": self.difficulty_level,
            "duration_estimate": self.duration_estimate,
            "team_size_min": self.team_size_min,
            "team_size_max": self.team_size_max,
            "current_team_size": self.get_current_team_size(),
            "looking_for_roles": self.looking_for_roles or [],
            "status": self.status,
            "view_count": self.view_count,
            "interest_count": self.interest_count,
            "collaboration_method": self.collaboration_method,
            "meeting_frequency": self.meeting_frequency,
            "communication_tools": self.communication_tools or [],
            "is_recruiting": self.is_recruiting(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

        # Include creator information
        if include_creator and self.creator:
            data["creator"] = {
                "id": self.creator.id,
                "username": self.creator.username,
                "avatar_url": self.creator.avatar_url
            }

        # Check if current user can apply
        if current_user_id:
            data["can_apply"] = self.can_user_apply(current_user_id)
            # Check if current user already applied
            existing_app = self.applications.filter_by(user_id=current_user_id).first()
            data["user_application_status"] = existing_app.status if existing_app else None

        # Include applications if requested
        if include_applications:
            data["applications"] = [app.to_dict(include_user=True) for app in self.applications]

        if include_embedding and self.embedding:
            data["embedding"] = self.embedding

        return data

    @classmethod
    def get_active_projects(cls):
        """Get all active (non-deleted) projects"""
        return cls.query.filter_by(is_deleted=False)

    @classmethod
    def get_recruiting_projects(cls):
        """Get projects that are currently recruiting"""
        return cls.query.filter_by(is_deleted=False, status=cls.STATUS_RECRUITING)