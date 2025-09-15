from datetime import datetime, timezone
from app.extensions import db
from sqlalchemy.dialects.postgresql import JSONB

class ProjectApplication(db.Model):
    __tablename__ = 'project_applications'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    # Application details
    application_message = db.Column(db.Text)  # Optional message from applicant
    proposed_role = db.Column(db.String(100))  # What role they want to fill

    # Matching and evaluation
    match_score = db.Column(db.Float)  # Computed compatibility score (0.0 - 1.0)
    matching_reasons = db.Column(JSONB, default=list)  # List of reasons for good match

    # Application status
    status = db.Column(db.String(20), default='pending', nullable=False)
    # pending, accepted, rejected, withdrawn

    # Response from project creator
    creator_response = db.Column(db.Text)  # Optional response message
    responded_at = db.Column(db.DateTime(timezone=True))

    # Timestamps
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    user = db.relationship('User', backref=db.backref('project_applications', lazy='dynamic'))
    # project relationship defined in Project model

    # Status constants
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REJECTED = 'rejected'
    STATUS_WITHDRAWN = 'withdrawn'

    # Indexes and constraints
    __table_args__ = (
        db.Index('idx_applications_project_id', 'project_id'),
        db.Index('idx_applications_user_id', 'user_id'),
        db.Index('idx_applications_status', 'status'),
        db.Index('idx_applications_created_at', 'created_at'),
        # Prevent duplicate applications
        db.UniqueConstraint('project_id', 'user_id', name='uq_project_user_application'),
        db.CheckConstraint('match_score >= 0.0 AND match_score <= 1.0', name='check_match_score_range'),
    )

    def accept(self, response_message=None):
        """Accept the application"""
        self.status = self.STATUS_ACCEPTED
        self.creator_response = response_message
        self.responded_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def reject(self, response_message=None):
        """Reject the application"""
        self.status = self.STATUS_REJECTED
        self.creator_response = response_message
        self.responded_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def withdraw(self):
        """Withdraw the application (by applicant)"""
        self.status = self.STATUS_WITHDRAWN
        self.updated_at = datetime.now(timezone.utc)

    def is_pending(self):
        """Check if application is still pending"""
        return self.status == self.STATUS_PENDING

    def is_accepted(self):
        """Check if application was accepted"""
        return self.status == self.STATUS_ACCEPTED

    def can_be_modified_by_user(self, user_id):
        """Check if user can modify this application"""
        # Debug logging
        import logging
        logger = logging.getLogger(__name__)

        logger.debug(f"Checking permission for user {user_id} on application {self.id}")
        logger.debug(f"Application user_id: {self.user_id}, status: {self.status}")
        logger.debug(f"Project exists: {self.project is not None}")
        if self.project:
            logger.debug(f"Project user_id: {self.project.user_id}")

        # Applicant can withdraw pending applications
        if self.user_id == user_id and self.is_pending():
            logger.debug("Permission granted: User is applicant and application is pending")
            return True
        # Project creator can accept/reject pending applications
        if self.project and self.project.user_id == user_id and self.is_pending():
            logger.debug("Permission granted: User is project creator and application is pending")
            return True

        logger.debug("Permission denied: No matching conditions")
        return False

    def set_match_score(self, score, reasons=None):
        """Set the computed match score and reasons"""
        self.match_score = max(0.0, min(1.0, float(score)))  # Clamp to [0.0, 1.0]
        if reasons:
            self.matching_reasons = reasons
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self, include_user=False, include_project=False, current_user_id=None):
        """Convert to dictionary for API responses"""
        data = {
            "id": self.id,
            "project_id": self.project_id,
            "user_id": self.user_id,
            "application_message": self.application_message,
            "proposed_role": self.proposed_role,
            "match_score": self.match_score,
            "matching_reasons": self.matching_reasons or [],
            "status": self.status,
            "creator_response": self.creator_response,
            "responded_at": self.responded_at.isoformat() if self.responded_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

        # Include user information
        if include_user and self.user:
            data["user"] = {
                "id": self.user.id,
                "username": self.user.username,
                "avatar_url": self.user.avatar_url,
                # Include profile if available
                "profile": self.user.profile.to_dict() if hasattr(self.user, 'profile') and self.user.profile else None
            }

        # Include project information
        if include_project and self.project:
            data["project"] = {
                "id": self.project.id,
                "title": self.project.title,
                "description": self.project.description[:200] + "..." if len(self.project.description) > 200 else self.project.description,
                "status": self.project.status,
                "creator": {
                    "id": self.project.creator.id,
                    "username": self.project.creator.username,
                    "avatar_url": self.project.creator.avatar_url
                } if self.project.creator else None
            }

        # Add permissions for current user
        if current_user_id:
            data["can_modify"] = self.can_be_modified_by_user(current_user_id)
            data["is_own_application"] = self.user_id == current_user_id
            data["is_project_creator"] = self.project and self.project.user_id == current_user_id

        return data

    @classmethod
    def get_for_project(cls, project_id, status=None):
        """Get applications for a specific project"""
        query = cls.query.filter_by(project_id=project_id)
        if status:
            query = query.filter_by(status=status)
        return query

    @classmethod
    def get_for_user(cls, user_id, status=None):
        """Get applications by a specific user"""
        query = cls.query.filter_by(user_id=user_id)
        if status:
            query = query.filter_by(status=status)
        return query

    @classmethod
    def get_pending_for_creator(cls, creator_user_id):
        """Get pending applications for projects created by user"""
        from app.models.project import Project
        return cls.query.join(Project).filter(
            Project.user_id == creator_user_id,
            cls.status == cls.STATUS_PENDING
        )