from datetime import datetime, timezone
from app.extensions import db
from sqlalchemy.dialects.postgresql import JSONB
import json

class UserProfile(db.Model):
    __tablename__ = 'user_profiles'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)

    # Basic profile information
    bio = db.Column(db.Text)
    skills = db.Column(JSONB, default=list)  # List of skill names
    interests = db.Column(JSONB, default=list)  # List of interest areas
    thrust = db.Column(JSONB, default=list)  # List of research thrust areas

    # Experience and preferences
    experience_level = db.Column(db.String(20))  # beginner, intermediate, advanced, expert
    preferred_roles = db.Column(JSONB, default=list)  # List of preferred project roles
    availability = db.Column(db.String(50))  # full-time, part-time, weekends, flexible

    # Contact information
    contact_preferences = db.Column(JSONB, default=dict)  # How they prefer to be contacted
    contact_methods = db.Column(JSONB, default=list)  # Specific contact methods with values

    # Semantic search
    embedding = db.Column(JSONB)  # Store embedding vector for similarity search

    # Metadata
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    user = db.relationship('User', backref=db.backref('profile', uselist=False, cascade='all, delete-orphan'))

    # Indexes for performance
    __table_args__ = (
        db.Index('idx_user_profiles_user_id', 'user_id'),
        db.Index('idx_user_profiles_active', 'is_active'),
        db.Index('idx_user_profiles_experience', 'experience_level'),
    )

    def get_text_representation(self):
        """Generate text representation for embedding"""
        parts = []

        if self.bio:
            parts.append(f"Bio: {self.bio}")

        if self.skills:
            parts.append(f"Skills: {', '.join(self.skills)}")

        if self.interests:
            parts.append(f"Interests: {', '.join(self.interests)}")

        if self.thrust:
            parts.append(f"Research Thrust: {', '.join(self.thrust)}")

        if self.experience_level:
            parts.append(f"Experience Level: {self.experience_level}")

        if self.preferred_roles:
            parts.append(f"Preferred Roles: {', '.join(self.preferred_roles)}")

        return " | ".join(parts)

    def update_embedding(self, embedding_vector):
        """Update the embedding vector and invalidate related caches"""
        self.embedding = embedding_vector
        self.updated_at = datetime.now(timezone.utc)

        # Invalidate related caches
        try:
            from app.extensions import cache

            # Clear compatibility scores for this profile
            cache_pattern = f"compat:{self.id}:*"
            logger.info(f"Invalidating compatibility cache for profile {self.id}")

            # Clear user's project matches cache
            user_cache_pattern = f"matches:projects:{self.user_id}:*"
            logger.info(f"Invalidating project matches cache for user {self.user_id}")

            # Note: In production, you'd use Redis SCAN to delete pattern-based keys
            # For now, we'll let TTL handle cache expiration

        except Exception as e:
            logger.debug(f"Cache invalidation failed for profile {self.id}: {e}")

    def to_dict(self, include_embedding=False):
        """Convert to dictionary for API responses"""
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "bio": self.bio,
            "skills": self.skills or [],
            "interests": self.interests or [],
            "thrust": self.thrust or [],
            "experience_level": self.experience_level,
            "preferred_roles": self.preferred_roles or [],
            "availability": self.availability,
            "contact_preferences": self.contact_preferences or {},
            "contact_methods": self.contact_methods or [],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

        # Include user information
        if self.user:
            data["user"] = {
                "id": self.user.id,
                "username": self.user.username,
                "avatar_url": self.user.avatar_url,
                "email": self.user.email if hasattr(self, '_include_contact') else None
            }

        if include_embedding and self.embedding:
            data["embedding"] = self.embedding

        return data

    def is_complete(self):
        """Check if profile has minimum required information"""
        return bool(
            self.bio and
            self.skills and
            len(self.skills) > 0 and
            self.experience_level
        )

    @classmethod
    def get_or_create_for_user(cls, user_id):
        """Get existing profile or create empty one for user"""
        profile = cls.query.filter_by(user_id=user_id).first()
        if not profile:
            profile = cls(user_id=user_id)
            db.session.add(profile)
            # Don't commit here - let the caller handle commits
        return profile